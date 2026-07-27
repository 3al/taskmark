"""Тесты скрипта смены статуса задачи (шаблон tasks/set_status.py).

TASK-011: статус живёт в двух местах (frontmatter и раздел board.md).
Скрипт — единственная точка правки, чтобы они не расходились.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SCRIPT = Path(__file__).resolve().parent.parent / "templates" / "tasks" / "set_status.py"
BOARD_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "tasks" / "board.md"


def load_script():
    """Загрузить шаблон скрипта как модуль (он живёт вне пакета)."""
    spec = importlib.util.spec_from_file_location("set_status", SCRIPT)
    assert spec and spec.loader, f"не удалось загрузить {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-07-27
patch: ~
---

## Описание

Тестовая задача.
"""


class SetStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.board = self.tasks / "board.md"
        self.board.write_text(
            BOARD_TEMPLATE.read_text(encoding="utf-8").format(queue_section="Queue"),
            encoding="utf-8",
        )
        self.mod = load_script()
        self.today = datetime.now().strftime("%Y-%m-%d")

    def _add_task(self, task_id: str = "TASK-001", title: str = "Тестовая",
                  status: str = "backlog", section: str = "### Новый функционал и баги",
                  meta: str = "") -> Path:
        """Создать файл задачи и запись на доске в указанном разделе."""
        filename = f"{task_id}-test.md"
        path = self.tasks / filename
        path.write_text(TASK_FILE.format(task_id=task_id, title=title, status=status),
                        encoding="utf-8")
        entry = f"- {task_id} · [{title}]({filename})" + (f" · {meta}" if meta else "")
        lines = self.board.read_text(encoding="utf-8").splitlines()
        idx = lines.index(section)
        # Заменить заглушку раздела, если она есть
        if idx + 2 < len(lines) and lines[idx + 2].strip() == "_(нет)_":
            lines[idx + 2] = entry
        else:
            lines.insert(idx + 2, entry)
        self.board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _status(self, task_id: str = "TASK-001") -> str:
        text = next(self.tasks.glob(f"{task_id}*.md")).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("status:"):
                return line.partition(":")[2].strip()
        return ""

    def _section_of(self, task_id: str = "TASK-001") -> str | None:
        """Заголовок ## раздела, в котором сейчас лежит запись задачи."""
        current = None
        for line in self.board.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
            elif line.strip().startswith(f"- {task_id} "):
                return current
        return None

    def _entry(self, task_id: str = "TASK-001") -> str:
        for line in self.board.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"- {task_id} "):
                return line
        return ""

    # --- Основной сценарий ---

    def test_moves_entry_and_updates_frontmatter(self) -> None:
        self._add_task()
        result = self.mod.set_status(self.tasks, "TASK-001", "development",
                                     agent="Claude Opus 5")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._status(), "development")
        self.assertEqual(self._section_of(), "Development")

    def test_writes_agent_and_date_tail(self) -> None:
        self._add_task()
        self.mod.set_status(self.tasks, "TASK-001", "development", agent="Claude Opus 5")
        entry = self._entry()
        self.assertIn("· Claude Opus 5 ·", entry)
        self.assertTrue(entry.rstrip().endswith(self.today), entry)

    def test_keeps_previous_agent_when_not_given(self) -> None:
        """Без --agent прежний исполнитель сохраняется, дата обновляется."""
        self._add_task(meta="k3 · 2026-07-01")
        self.mod.set_status(self.tasks, "TASK-001", "review")
        entry = self._entry()
        self.assertIn("· k3 ·", entry)
        self.assertTrue(entry.rstrip().endswith(self.today), entry)

    def test_leaves_placeholder_in_emptied_section(self) -> None:
        self._add_task(section="## Development", status="development")
        self.mod.set_status(self.tasks, "TASK-001", "testing", agent="Claude Opus 5")
        board = self.board.read_text(encoding="utf-8")
        dev = board.split("## Development")[1].split("##")[0]
        self.assertIn("_(нет)_", dev, "опустевший раздел остался без заглушки")

    def test_drops_placeholder_in_target_section(self) -> None:
        self._add_task()
        self.mod.set_status(self.tasks, "TASK-001", "development", agent="Claude Opus 5")
        board = self.board.read_text(encoding="utf-8")
        dev = board.split("## Development")[1].split("##")[0]
        self.assertNotIn("_(нет)_", dev, "заглушка осталась рядом с задачей")

    def test_inserts_first_in_target_section(self) -> None:
        self._add_task("TASK-001")
        self._add_task("TASK-002", section="## Development", status="development")
        self.mod.set_status(self.tasks, "TASK-001", "development", agent="Claude Opus 5")
        board = self.board.read_text(encoding="utf-8")
        dev = board.split("## Development")[1].split("\n## ")[0]
        entries = [ln for ln in dev.splitlines() if ln.strip().startswith("- TASK-")]
        self.assertEqual(entries[0].strip().split()[1], "TASK-001", dev)

    def test_keeps_board_formatting_readable(self) -> None:
        """Запись не должна слипаться со следующим заголовком или тонуть в пустых строках."""
        self._add_task()
        self.mod.set_status(self.tasks, "TASK-001", "testing", agent="Claude Opus 5")
        lines = self.board.read_text(encoding="utf-8").splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.strip().startswith("- TASK-001 "))
        self.assertEqual(lines[i - 1].strip(), "", "нет отбивки перед записью")
        self.assertEqual(lines[i - 2].strip(), "## Testing", "лишние пустые строки после заголовка")
        self.assertEqual(lines[i + 1].strip(), "", "запись слиплась со следующим разделом")

    # --- Конфигурация ---

    def test_respects_renamed_queue_section(self) -> None:
        """Раздел очереди и её статус переименуемы — хардкод недопустим."""
        self.board.write_text(
            BOARD_TEMPLATE.read_text(encoding="utf-8").format(queue_section="Очередь"),
            encoding="utf-8",
        )
        cfg_dir = self.root / "taskboard"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(
            json.dumps({"queue_section": "Очередь", "queued_status": "в очереди"}),
            encoding="utf-8",
        )
        self._add_task()
        result = self.mod.set_status(self.tasks, "TASK-001", "в очереди",
                                     agent="Claude Opus 5")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._section_of(), "Очередь")
        self.assertEqual(self._status(), "в очереди")

    # --- Идемпотентность и ошибки ---

    def test_idempotent(self) -> None:
        self._add_task()
        self.mod.set_status(self.tasks, "TASK-001", "development", agent="Claude Opus 5")
        first = self.board.read_text(encoding="utf-8")
        self.mod.set_status(self.tasks, "TASK-001", "development", agent="Claude Opus 5")
        second = self.board.read_text(encoding="utf-8")
        self.assertEqual(first, second, "повторный вызов изменил доску")
        self.assertEqual(second.count("- TASK-001 "), 1, "запись задублировалась")

    def test_unknown_status_is_error(self) -> None:
        self._add_task()
        result = self.mod.set_status(self.tasks, "TASK-001", "нет-такого-статуса")
        self.assertFalse(result["ok"])
        self.assertIn("статус", result["error"].lower())

    def test_task_missing_on_board_is_error(self) -> None:
        path = self.tasks / "TASK-042-test.md"
        path.write_text(TASK_FILE.format(task_id="TASK-042", title="Вне доски",
                                         status="backlog"), encoding="utf-8")
        result = self.mod.set_status(self.tasks, "TASK-042", "development")
        self.assertFalse(result["ok"])
        self.assertIn("TASK-042", result["error"])

    def test_missing_task_file_is_error(self) -> None:
        result = self.mod.set_status(self.tasks, "TASK-999", "development")
        self.assertFalse(result["ok"])

    # --- CLI ---

    def test_cli_success_and_failure_exit_codes(self) -> None:
        self._add_task()
        ok = subprocess.run(
            [sys.executable, str(SCRIPT), "TASK-001", "development",
             "--agent", "Claude Opus 5", "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr or ok.stdout)
        self.assertEqual(self._status(), "development")

        bad = subprocess.run(
            [sys.executable, str(SCRIPT), "TASK-999", "development",
             "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(bad.returncode, 1)


class StatusScriptScaffoldTest(unittest.TestCase):
    """Скрипт разворачивается вместе со структурой и проверяется на актуальность."""

    def setUp(self) -> None:
        from backend.config import DEFAULTS
        from backend.scaffold import scaffold_project

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        self.cfg = dict(DEFAULTS)
        scaffold_project(self.tasks, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": False, "rules_claude": False,
        })
        self.script = self.tasks / "set_status.py"

    def _codes(self) -> list[str]:
        from backend.validator import validate_project
        return [d["code"] for d in validate_project(self.tasks, self.cfg)["degraded"]]

    def test_scaffold_deploys_script(self) -> None:
        self.assertTrue(self.script.is_file(), "set_status.py не развёрнут")
        self.assertNotIn("no_status_script", self._codes())
        self.assertNotIn("outdated_status_script", self._codes())

    def test_missing_script_reported(self) -> None:
        self.script.unlink()
        self.assertIn("no_status_script", self._codes())

    def test_outdated_script_reported_and_fixable(self) -> None:
        from backend.scaffold import scaffold_project

        original = self.script.read_text(encoding="utf-8")
        self.script.write_text("устаревшая версия", encoding="utf-8")
        self.assertIn("outdated_status_script", self._codes())

        result = scaffold_project(self.tasks, self.cfg, {"parts": ["status_script"]})

        self.assertEqual(self.script.read_text(encoding="utf-8"), original)
        self.assertIn("set_status.py", result["replaced"])
        self.assertNotIn("outdated_status_script", self._codes())


if __name__ == "__main__":
    unittest.main()
