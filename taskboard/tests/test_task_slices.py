"""Срез задач по значению поля: автор, исполнитель, эпик (TASK-221).

Состав эпика уже собирался в порядке маршрута. Срез по автору и по исполнителю
отвечает на тот же вопрос — «где едут задачи с этим значением», — поэтому
механизм один: поле приходит параметром, а не зашито в функцию.

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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.statuses import load_pipeline  # noqa: E402
from backend.task_slices import field_tasks  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "templates" / "tasks" / "set_status.py"

PIPELINE = ["backlog", "todo", "development", "testing", "done", "cancelled"]

TASK = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-09-13 10:00
author: {author}
assignee: {assignee}
---

## Описание

Текст.
"""


class SliceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / ".taskboard.json").write_text(
            json.dumps({"pipeline": PIPELINE}), encoding="utf-8")
        self.pipeline = load_pipeline({"pipeline": PIPELINE})

    def task(self, task_id: str, status: str = "todo", author: str = "Иван Петров",
             assignee: str = "~", title: str = "Задача") -> None:
        (self.tasks / f"{task_id}-test.md").write_text(
            TASK.format(task_id=task_id, title=title, status=status,
                        author=author, assignee=assignee), encoding="utf-8")

    def ids(self, field: str = "author", value: str = "Иван Петров") -> list[str]:
        return [t["id"] for t in field_tasks(self.tasks, field, value, self.pipeline)]


class FieldTasksTest(SliceCase):
    def test_sorted_by_pipeline(self) -> None:
        self.task("TASK-001", "done")
        self.task("TASK-002", "backlog")
        self.task("TASK-003", "development")

        self.assertEqual(["TASK-002", "TASK-003", "TASK-001"], self.ids())

    def test_offramp_goes_last(self) -> None:
        self.task("TASK-001", "cancelled")
        self.task("TASK-002", "done")

        self.assertEqual(["TASK-002", "TASK-001"], self.ids())

    def test_other_values_excluded(self) -> None:
        self.task("TASK-001")
        self.task("TASK-002", author="@nick")

        self.assertEqual(["TASK-001"], self.ids())

    def test_empty_field_never_matches(self) -> None:
        """Пустое поле — «значения нет», а не значение с пустым именем."""
        self.task("TASK-001", author="~")
        self.task("TASK-002", author="")

        self.assertEqual([], self.ids(value=""))
        self.assertEqual([], self.ids(value="~"))

    def test_unknown_value_is_empty_not_error(self) -> None:
        self.task("TASK-001")

        self.assertEqual([], self.ids(value="Никто"))

    def test_any_field_works(self) -> None:
        """Механизм не знает про автора: исполнитель отбирается им же."""
        self.task("TASK-001", assignee="Сидоров")
        self.task("TASK-002", assignee="~")

        self.assertEqual(["TASK-001"], self.ids("assignee", "Сидоров"))

    def test_value_with_spaces_and_cyrillic_is_exact(self) -> None:
        self.task("TASK-001", author="Иван Петров")
        self.task("TASK-002", author="Иван Петров-Водкин")

        self.assertEqual(["TASK-001"], self.ids(value="  Иван Петров "))

    def test_entry_shape(self) -> None:
        self.task("TASK-001", "development", title="Срез")

        entry = field_tasks(self.tasks, "author", "Иван Петров", self.pipeline)[0]

        self.assertEqual({"id": "TASK-001", "title": "Срез", "file": "TASK-001-test.md",
                          "status": "development", "label": "Development",
                          "color": "sky"}, entry)

    def test_invalid_field_name_matches_nothing(self) -> None:
        self.task("TASK-001")

        self.assertEqual([], self.ids("", "Иван Петров"))
        self.assertEqual([], self.ids("author: x", "Иван Петров"))


class EndpointTest(SliceCase):
    def test_slice_endpoint(self) -> None:
        from backend import app as app_module

        self.task("TASK-001", "done")
        self.task("TASK-002", "todo")
        cfg = {"pipeline": PIPELINE}
        with mock.patch.object(app_module, "_ctx", return_value=(self.tasks, cfg)):
            result = app_module.api_task_slice(field="author", value="Иван Петров")

        self.assertEqual("author", result["field"])
        self.assertEqual("Иван Петров", result["value"])
        self.assertEqual(["TASK-002", "TASK-001"], [t["id"] for t in result["tasks"]])

    def test_assignee_endpoint(self) -> None:
        from backend import app as app_module

        self.task("TASK-001", "testing", assignee="Иванов")
        self.task("TASK-002", "todo", assignee="Петров")
        with mock.patch.object(app_module, "_ctx",
                               return_value=(self.tasks, {"pipeline": PIPELINE})):
            result = app_module.api_task_slice(field="assignee", value="Иванов")

        self.assertEqual(["TASK-001"], [t["id"] for t in result["tasks"]])

    def test_unknown_name_is_empty(self) -> None:
        from backend import app as app_module

        with mock.patch.object(app_module, "_ctx",
                               return_value=(self.tasks, {"pipeline": PIPELINE})):
            result = app_module.api_task_slice(field="author", value="Никто")

        self.assertEqual([], result["tasks"])

    def test_capability_declared(self) -> None:
        from backend.app import CAPABILITIES

        self.assertTrue(CAPABILITIES.get("task_slice"))


class ScriptTest(SliceCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_by_flag_matches_backend(self) -> None:
        self.task("TASK-001", "done")
        self.task("TASK-002", "cancelled")
        self.task("TASK-003", "backlog")
        self.task("TASK-004", author="~")

        done = self.run_script("--by", "author", "Иван Петров")

        self.assertEqual(0, done.returncode, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual("author", report["field"])
        self.assertEqual("Иван Петров", report["value"])
        self.assertEqual(3, report["total"])
        self.assertEqual(self.ids(), [t["id"] for t in report["tasks"]])
        self.assertEqual("Done", report["tasks"][1]["label"])

    def test_by_assignee(self) -> None:
        """Срез по исполнителю — тот же флаг, без отдельной ветки."""
        self.task("TASK-001", "testing", assignee="Иванов")
        self.task("TASK-002", "todo", assignee="Иванов")
        self.task("TASK-003", "todo", assignee="~")

        done = self.run_script("--by", "assignee", "Иванов")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(["TASK-002", "TASK-001"],
                         [t["id"] for t in json.loads(done.stdout)["tasks"]])

    def test_unknown_name_is_empty_not_error(self) -> None:
        self.task("TASK-001")

        done = self.run_script("--by", "author", "Никто")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(0, json.loads(done.stdout)["total"])


if __name__ == "__main__":
    unittest.main()
