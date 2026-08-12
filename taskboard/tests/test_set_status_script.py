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


def render_board(cfg: dict | None = None) -> str:
    """Доска из шаблона с разделами по пайплайну — как её создаёт scaffold."""
    from backend.scaffold import render_sections
    from backend.statuses import load_pipeline

    return BOARD_TEMPLATE.read_text(encoding="utf-8").format(
        sections=render_sections(load_pipeline(cfg or {})))


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
            render_board(), encoding="utf-8",
        )
        self.mod = load_script()
        self.today = datetime.now().strftime("%Y-%m-%d")

    def _add_task(self, task_id: str = "TASK-001", title: str = "Тестовая",
                  status: str = "backlog", section: str = "### Новый функционал",
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
        cfg = {"queue_section": "Очередь", "queued_status": "в очереди"}
        self.board.write_text(render_board(cfg), encoding="utf-8")
        cfg_dir = self.root / "taskboard"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        self._add_task()
        result = self.mod.set_status(self.tasks, "TASK-001", "в очереди",
                                     agent="Claude Opus 5")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._section_of(), "Очередь")
        self.assertEqual(self._status(), "в очереди")

    # --- Пайплайн проекта ---

    def test_reads_project_config_from_tasks_dir(self) -> None:
        """Скрипт обязан видеть тот же конфиг, что и сервер.

        Конфиг проекта лежит в tasks/.taskboard.json (внутри tasks/, которая
        игнорируется git). Пока скрипт читал только прежний путь, он работал
        по старому пайплайну и отказывался переводить задачи в новые статусы.
        """
        cfg = {"pipeline": ["backlog", "todo", "development", "testing", "done"]}
        self.board.write_text(render_board(cfg), encoding="utf-8")
        (self.tasks / ".taskboard.json").write_text(json.dumps(cfg), encoding="utf-8")

        self._add_task()
        result = self.mod.set_status(self.tasks, "TASK-001", "done")
        self.assertTrue(result["ok"], result)
        self.assertEqual("Done", self._section_of())
        self.assertEqual("done", self._status())

    def test_custom_pipeline_defines_sections(self) -> None:
        """Проект без review: задача едет из development сразу в тестирование."""
        cfg = {"pipeline": ["backlog", "queued", "development", "local_testing", "completed"]}
        self.board.write_text(render_board(cfg), encoding="utf-8")
        cfg_dir = self.root / "taskboard"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

        self._add_task()
        result = self.mod.set_status(self.tasks, "TASK-001", "local_testing")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._section_of(), "Local Testing")
        self.assertEqual(self._status(), "local_testing")

        rejected = self.mod.set_status(self.tasks, "TASK-001", "review")
        self.assertFalse(rejected["ok"], "статус вне пайплайна проекта принят")

    def test_forward_jump_reports_skipped_statuses(self) -> None:
        """Прыжок вперёд законен, но пропущенное видно в результате."""
        self._add_task()
        self.mod.set_status(self.tasks, "TASK-001", "development")
        result = self.mod.set_status(self.tasks, "TASK-001", "completed")
        self.assertTrue(result["ok"], result)
        self.assertEqual(["review", "testing"], result["skipped"])

    def test_describe_targets_for_task(self) -> None:
        """Скиллы спрашивают у скрипта, куда можно двинуть задачу."""
        self._add_task()
        self.mod.set_status(self.tasks, "TASK-001", "development")
        info = self.mod.describe(self.tasks, "TASK-001")
        self.assertEqual("development", info["current"])
        self.assertEqual("review", info["next"], "ожидаемый шаг — ближайший вперёд")
        self.assertIn("completed", info["forward"], "прыжки вперёд доступны")
        self.assertIn("backlog", info["backward"])
        self.assertEqual("development", info["actions"]["start"])
        self.assertEqual("queued", info["actions"]["pick"])

    def test_describe_pipeline_without_task(self) -> None:
        info = self.mod.describe(self.tasks)
        self.assertEqual(["backlog", "queued", "development", "review", "testing", "completed"],
                         [s["key"] for s in info["pipeline"]])
        self.assertNotIn("current", info)

    def test_cancelled_is_reachable_but_not_expected(self) -> None:
        """Съезд с маршрута: отменить можно всегда, но это не ожидаемый шаг."""
        cfg = {"pipeline": ["backlog", "development", "completed", "cancelled"]}
        self.board.write_text(render_board(cfg), encoding="utf-8")
        cfg_dir = self.root / "taskboard"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

        self._add_task()
        info = self.mod.describe(self.tasks, "TASK-001")
        self.assertIn("cancelled", info["forward"])
        self.assertEqual("development", info["next"])

        # Съезд требует причины (TASK-042): из отмены не возвращаются
        refused = self.mod.set_status(self.tasks, "TASK-001", "cancelled")
        self.assertFalse(refused["ok"], "задача отменена без причины")

        result = self.mod.set_status(self.tasks, "TASK-001", "cancelled",
                                     reason="дублирует TASK-002")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._section_of(), "Cancelled")

    # --- Очередь: агент спрашивает её у скрипта, а не помнит (TASK-062) ---

    def test_queue_returns_pick_section_in_order(self) -> None:
        """Очередь — раздел `actions.pick` в порядке следования сверху вниз."""
        self._add_task("TASK-001", "Первая", section="### Новый функционал")
        self._add_task("TASK-002", "Вторая", section="### Новый функционал")
        self.mod.set_status(self.tasks, "TASK-001", "queued")
        self.mod.set_status(self.tasks, "TASK-002", "queued", position="end")

        queue = self.mod.queue(self.tasks)

        self.assertEqual("queued", queue["status"])
        self.assertEqual("Queue", queue["section"])
        self.assertEqual(["TASK-001", "TASK-002"], [t["id"] for t in queue["tasks"]])
        self.assertEqual("Первая", queue["tasks"][0]["title"])

    def test_queue_reflects_board_right_now(self) -> None:
        """Ради этого всё и затевалось: доска поменялась — ответ другой."""
        self._add_task("TASK-001", "Первая", section="### Новый функционал")
        self._add_task("TASK-002", "Вторая", section="### Новый функционал")
        self.mod.set_status(self.tasks, "TASK-001", "queued")
        self.assertEqual(["TASK-001"], [t["id"] for t in self.mod.queue(self.tasks)["tasks"]])

        self.mod.set_status(self.tasks, "TASK-002", "queued")  # встаёт первой
        self.assertEqual(["TASK-002", "TASK-001"],
                         [t["id"] for t in self.mod.queue(self.tasks)["tasks"]])

    def test_empty_queue_is_not_an_error(self) -> None:
        """Пустая очередь — законный ответ: скиллы по нему спрашивают пользователя."""
        queue = self.mod.queue(self.tasks)
        self.assertEqual([], queue["tasks"])
        self.assertEqual("Queue", queue["section"])

    def test_queue_limit(self) -> None:
        """Агенту нужна верхушка, а не весь бэклог — по умолчанию отдаём срез."""
        for i in range(1, 8):
            self._add_task(f"TASK-00{i}", f"Задача {i}", section="### Новый функционал")
            self.mod.set_status(self.tasks, f"TASK-00{i}", "queued", position="end")
        self.assertEqual(3, len(self.mod.queue(self.tasks, limit=3)["tasks"]))
        self.assertEqual(7, len(self.mod.queue(self.tasks, limit=0)["tasks"]),
                         "limit=0 — вся очередь")

    def test_queue_survives_brackets_in_title(self) -> None:
        """Заголовки со скобками разбираются так же, как на доске (TASK-057)."""
        self._add_task("TASK-009", "[BE] Счетчик", section="### Новый функционал")
        self.mod.set_status(self.tasks, "TASK-009", "queued")
        queue = self.mod.queue(self.tasks)
        self.assertEqual(["TASK-009"], [t["id"] for t in queue["tasks"]])
        self.assertEqual("[BE] Счетчик", queue["tasks"][0]["title"])

    def test_cli_queue_prints_json(self) -> None:
        self._add_task("TASK-001", "Первая", section="### Новый функционал")
        self.mod.set_status(self.tasks, "TASK-001", "queued")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--queue", "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(["TASK-001"], [t["id"] for t in data["tasks"]])

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
        from backend import baseline
        from backend.scaffold import scaffold_project

        original = self.script.read_text(encoding="utf-8")
        self.script.write_text("прежняя версия", encoding="utf-8")
        # Развернули именно её — значит копия отстала от шаблона. Правка без
        # подмены слепка была бы кастомизацией, а о ней не сообщают (TASK-014)
        baseline.write(self.tasks.parent, "status_script", "set_status.py",
                       "прежняя версия", self.cfg)
        self.assertIn("outdated_status_script", self._codes())

        result = scaffold_project(self.tasks, self.cfg, {"parts": ["status_script"]})

        self.assertEqual(self.script.read_text(encoding="utf-8"), original)
        self.assertIn("set_status.py", result["replaced"])
        self.assertNotIn("outdated_status_script", self._codes())

    def test_trailing_newline_is_not_outdated(self) -> None:
        """Съеденный редактором финальный перевод строки — не устаревание.

        Скрипт сравнивался побайтово, а остальная поставка — построчно: копия
        расходилась с шаблоном невидимо для diff, баннер объяснить было нечем,
        а «Обновить» затирал правки пользователя.
        """
        text = self.script.read_text(encoding="utf-8")
        self.script.write_text(text.rstrip("\n"), encoding="utf-8")
        self.assertNotIn("outdated_status_script", self._codes())


class ScriptContractTest(unittest.TestCase):
    """Маркер контракта: инструмент видит, что умеет развёрнутая копия скрипта.

    Копия обновляется кнопкой, отдельно от самого taskboard, — без маркера
    возможен худший исход: требования объявлены, гейта нет, человек уверен,
    что он есть.
    """

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

    def _degraded(self) -> list[dict]:
        from backend.validator import validate_project
        return validate_project(self.tasks, self.cfg)["degraded"]

    def _requires_issue(self) -> list[dict]:
        """Сообщения о расхождении «конфиг объявил / скрипт не умеет».

        Это деградация, а не мягкое расхождение данных: объявленная проверка не
        работает, и чинится она кнопкой — а кнопку UI берёт по коду.
        """
        return [d for d in self._degraded() if d["code"] == "requires_unsupported"]

    def _deploy_capabilities(self, names: set[str] | None) -> None:
        """Подменить развёрнутую копию скриптом с заданным набором возможностей.

        Состав маркера в шаблоне растёт (TASK-108 добавит "requires"), поэтому
        тесты задают его сами, а не опираются на текущее содержимое шаблона.
        """
        marker = "" if names is None else \
            "SCRIPT_CAPABILITIES = {%s}\n" % ", ".join(f'"{n}"' for n in sorted(names))
        self.script.write_text(f'"""копия скрипта"""\n{marker}', encoding="utf-8")

    def test_template_declares_capabilities(self) -> None:
        from backend.scaffold import script_capabilities

        self.assertTrue(script_capabilities(self.tasks, self.cfg),
                        "шаблон скрипта не объявляет набор возможностей")

    def test_old_copy_without_marker_has_no_capabilities(self) -> None:
        from backend.scaffold import script_capabilities

        self._deploy_capabilities(None)
        self.assertEqual(script_capabilities(self.tasks, self.cfg), set())

    def test_missing_script_has_no_capabilities(self) -> None:
        from backend.scaffold import script_capabilities

        self.script.unlink()
        self.assertEqual(script_capabilities(self.tasks, self.cfg), set())

    def test_silent_until_requirements_declared(self) -> None:
        self._deploy_capabilities(set())
        self.assertEqual(self._requires_issue(), [])

    def test_declared_requirements_without_support_reported(self) -> None:
        self.cfg["requires"] = {"testing": [{"id": "verified", "check": "confirm"}]}
        self._deploy_capabilities({"stall"})

        found = self._requires_issue()
        self.assertTrue(found, "расхождение конфига и скрипта осталось незамеченным")
        self.assertIn("set_status.py", found[0]["message"])
        self.assertIn("requires", found[0]["message"],
                      "человеку надо знать, что именно объявлено и где")

    def test_absent_script_says_it_once(self) -> None:
        """Файла нет — про это уже сказано своей строкой с кнопкой «Создать».

        Вторая строка советовала бы «обновить» то, чего не существует.
        """
        self.cfg["requires"] = {"testing": [{"id": "verified", "check": "confirm"}]}
        self.script.unlink()

        self.assertEqual(self._requires_issue(), [])

    def test_supported_requirements_are_silent(self) -> None:
        self.cfg["requires"] = {"testing": [{"id": "verified", "check": "confirm"}]}
        self._deploy_capabilities({"requires"})

        self.assertEqual(self._requires_issue(), [])

    def test_ui_knows_the_button_for_this_code(self) -> None:
        """Деградация без кнопки — сообщение, из которого не выйти.

        Код и его кнопка живут в разных файлах (backend/validator.py и
        DEGRADED_FIX в App.jsx) и расходятся молча — реестры такого рода
        правятся только вместе.
        """
        app = (Path(__file__).resolve().parent.parent
               / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("requires_unsupported:", app,
                      "код деградации не заведён в DEGRADED_FIX — строка будет без кнопки")

    def test_empty_requirements_are_silent(self) -> None:
        self.cfg["requires"] = {}
        self._deploy_capabilities({"stall"})

        self.assertEqual(self._requires_issue(), [])


class TypeAwareNextStepTest(unittest.TestCase):
    """Ожидаемый шаг учитывает тип задачи (TASK-151).

    Пропуск объявлен у типа в каталоге поставки и меняет **только**
    рекомендацию: `forward` остаётся полным, потому что прыжок вперёд законен
    и без него, а запрет тут был бы забором вместо маршрута.
    """

    PIPELINE = ["backlog", "todo", "development", "testing", "ready_for_release",
                "release_notes", "to_release", "done", "cancelled"]

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir()
        self.mod = load_script()
        self._use_pipeline(self.PIPELINE)

    def _use_pipeline(self, keys: list[str]) -> None:
        cfg = {"pipeline": keys,
               "actions": {"create": "backlog", "pick": "todo",
                           "start": "development", "return": "development"}}
        (self.tasks / "board.md").write_text(render_board(cfg), encoding="utf-8")
        (self.tasks / ".taskboard.json").write_text(json.dumps(cfg), encoding="utf-8")

    def _task(self, task_type: str, status: str) -> None:
        """Файл задачи с типом: на доске запись не нужна, статус берут из него."""
        text = TASK_FILE.format(task_id="TASK-001", title="Тестовая", status=status)
        text = text.replace("status:", f"type: {task_type}\nstatus:", 1)
        (self.tasks / "TASK-001-test.md").write_text(text, encoding="utf-8")

    def _describe(self) -> dict:
        return self.mod.describe(self.tasks, "TASK-001")

    def test_next_skips_release_tail_of_discussion(self) -> None:
        """У обсуждения релизного хвоста нет: после проверки — сразу закрытие."""
        self._task("discussion", "testing")
        self.assertEqual("done", self._describe()["next"])

    def test_skipped_statuses_stay_reachable(self) -> None:
        """Пропуск — рекомендация, а не запрет: двинуть туда рукой можно."""
        self._task("discussion", "testing")
        forward = self._describe()["forward"]
        for key in ("ready_for_release", "release_notes", "to_release"):
            self.assertIn(key, forward, f"{key} стал недостижим")

    def test_check_stage_is_not_skipped(self) -> None:
        """Проверять у обсуждения есть что: решение утверждает человек."""
        self._task("discussion", "development")
        self.assertEqual("testing", self._describe()["next"])

    def test_other_types_go_the_whole_route(self) -> None:
        self._task("feature", "testing")
        self.assertEqual("ready_for_release", self._describe()["next"])

    def test_task_without_type_goes_the_whole_route(self) -> None:
        """Задача, заведённая до появления поля, рекомендаций не теряет."""
        text = TASK_FILE.format(task_id="TASK-001", title="Тестовая", status="testing")
        (self.tasks / "TASK-001-test.md").write_text(text, encoding="utf-8")
        self.assertEqual("ready_for_release", self._describe()["next"])

    def test_skip_never_leaves_the_task_without_a_next_step(self) -> None:
        """Впереди одни пропускаемые — рекомендация остаётся прежней.

        Иначе пропуск означал бы «дальше некуда», то есть подделывал конец
        маршрута: `next: None` читают как терминальность (`is_terminal`).
        """
        self._use_pipeline(["backlog", "development", "release_notes"])
        self._task("discussion", "development")
        self.assertEqual("release_notes", self._describe()["next"])

    def test_terminal_check_ignores_type(self) -> None:
        """Конец маршрута — свойство пайплайна, а не вида работы."""
        pipeline = self.mod.pipeline_of(self.mod.load_config(self.tasks))
        self.assertFalse(self.mod.is_terminal(pipeline, "testing"))
        self.assertTrue(self.mod.is_terminal(pipeline, "done"))


if __name__ == "__main__":
    unittest.main()
