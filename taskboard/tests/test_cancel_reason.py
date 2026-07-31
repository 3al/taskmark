"""Причина отмены: без неё задачу не отменить (TASK-042).

Отмена — съезд с маршрута: из неё не возвращаются, и «почему» остаётся в файле
навсегда. Поэтому причина обязательна и спрашивается один раз — при переводе.

Правило про съезд, а не про имя статуса: пайплайн настраивается, съездов может
быть несколько. Решение принимает бэкенд, скрипт зеркалит — как и с простоем.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.queue_ops import move_task  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402
from backend.task_parser import parse_task  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "templates" / "tasks" / "set_status.py"
SKILLS = ROOT / "templates" / "agentic" / ".claude" / "skills"
RULES = ROOT / "templates" / "agentic" / "rules_section.md"
SRC = ROOT / "frontend" / "src"
DOCS = ROOT.parent / "docs" / "help"

CFG = {**DEFAULTS,
       "pipeline": ["backlog", "todo", "development", "testing", "done", "cancelled"],
       "actions": {"create": "backlog", "pick": "todo",
                   "start": "development", "return": "development"},
       "harnesses": {"claude": True, "opencode": False}}

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: todo
created: 2026-07-31 10:00
---

## Описание

Тестовая задача.
"""


class Project(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        scaffold_project(self.tasks, CFG, {"harnesses": CFG["harnesses"]})
        (self.tasks / ".taskboard.json").write_text(
            json.dumps({"pipeline": CFG["pipeline"], "actions": CFG["actions"]},
                       ensure_ascii=False), encoding="utf-8")
        self.make("TASK-013", "Первая")

    def make(self, task_id: str, title: str) -> None:
        name = f"{task_id}-{title.lower()}.md"
        (self.tasks / name).write_text(
            TASK_FILE.format(task_id=task_id, title=title), encoding="utf-8")
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower() == "## to do":
                lines.insert(i + 1, f"\n- {task_id} · [{title}]({name})")
                break
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def meta(self, task_id: str) -> dict:
        return parse_task(self.tasks, task_id)["meta"]


class PipelineOfframpTest(unittest.TestCase):
    """Съезд — свойство статуса, а не его имя."""

    def test_pipeline_tells_offramp(self) -> None:
        pipeline = load_pipeline(CFG)
        self.assertTrue(pipeline.is_offramp("cancelled"))
        self.assertFalse(pipeline.is_offramp("done"))
        self.assertFalse(pipeline.is_offramp("нет такого"))


class MoveRequiresReasonTest(Project):
    def test_move_to_offramp_without_reason_refused(self) -> None:
        result = move_task(self.tasks, CFG, "TASK-013", "Cancelled")

        self.assertFalse(result.get("ok"), "задача отменена без причины")
        self.assertEqual("cancel_reason", result.get("code"),
                         "фронту не по чему отличить этот отказ от прочих")
        self.assertEqual("todo", self.meta("TASK-013")["status"],
                         "статус изменился, хотя перенос не состоялся")

    def test_reason_is_saved(self) -> None:
        result = move_task(self.tasks, CFG, "TASK-013", "Cancelled",
                           reason="дублирует TASK-010")

        self.assertTrue(result.get("ok"), result)
        self.assertEqual("дублирует TASK-010", self.meta("TASK-013")["cancel_reason"])
        self.assertEqual("cancelled", self.meta("TASK-013")["status"])

    def test_reason_is_one_line(self) -> None:
        move_task(self.tasks, CFG, "TASK-013", "Cancelled", reason="дублирует\nTASK-010")

        self.assertEqual("дублирует TASK-010", self.meta("TASK-013")["cancel_reason"])

    def test_ordinary_move_needs_no_reason(self) -> None:
        self.assertTrue(move_task(self.tasks, CFG, "TASK-013", "Development")["ok"])

    def test_return_from_offramp_keeps_reason(self) -> None:
        """Из отмены не возвращаются, но если вернули — «почему» остаётся историей."""
        move_task(self.tasks, CFG, "TASK-013", "Cancelled", reason="передумали")
        move_task(self.tasks, CFG, "TASK-013", "To Do")

        self.assertEqual("передумали", self.meta("TASK-013")["cancel_reason"])


class ScriptRequiresReasonTest(Project):
    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks / "set_status.py"),
             "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_cancel_without_reason_refused(self) -> None:
        done = self.run_script("TASK-013", "cancelled")

        self.assertNotEqual(0, done.returncode, "скрипт отменил задачу без причины")
        self.assertIn("--reason", done.stderr + done.stdout,
                      "отказ не подсказывает, чего не хватает")
        self.assertEqual("todo", self.meta("TASK-013")["status"])

    def test_cancel_with_reason(self) -> None:
        done = self.run_script("TASK-013", "cancelled", "--reason", "дублирует TASK-010")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("дублирует TASK-010", self.meta("TASK-013")["cancel_reason"])

    def test_reason_not_required_elsewhere(self) -> None:
        self.assertEqual(0, self.run_script("TASK-013", "development").returncode)


class ApiAndUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app_py = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
        self.app = (SRC / "App.jsx").read_text(encoding="utf-8")
        self.modal = (SRC / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.api = (SRC / "api.js").read_text(encoding="utf-8")

    def test_api_passes_reason(self) -> None:
        self.assertIn("reason", self.app_py)
        self.assertIn("reason", self.api, "клиент не передаёт причину при переносе")

    def test_board_asks_reason(self) -> None:
        self.assertIn("cancel_reason", self.app,
                      "доска не реагирует на отказ «нужна причина»")
        self.assertIn("ReasonPrompt", self.app,
                      "ввод причины сделан на месте, а не общим компонентом")

    def test_modal_shows_reason(self) -> None:
        self.assertIn("cancel_reason", self.modal,
                      "в открытой карточке не видно, почему задачу отменили")


class DeliveryTest(unittest.TestCase):
    def test_rules_mention_reason(self) -> None:
        rules = RULES.read_text(encoding="utf-8")
        self.assertIn("--reason", rules, "правила не говорят, как отменять задачу")

    def test_finalize_skill_asks_reason(self) -> None:
        text = (SKILLS / "finalize-task" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--reason", text,
                      "скилл финализации отменяет задачу без причины")

    def test_help_describes_it(self) -> None:
        text = (DOCS / "02-board.md").read_text(encoding="utf-8").lower()
        self.assertIn("причин", text)
        self.assertIn("отмен", text)


if __name__ == "__main__":
    unittest.main()
