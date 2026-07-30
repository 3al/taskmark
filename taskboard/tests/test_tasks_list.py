"""Тест: список всех задач проекта для подсказок blocked_by.

Бэкенд отдаёт задачи проекта — UI подсказывает их при вводе blocked_by.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.task_parser import list_all_tasks


class TasksListTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)

    def _task(self, task_id: str, title: str) -> None:
        (self.tasks / f"{task_id}-{title.lower().replace(' ', '-')}.md").write_text(
            f"---\nid: {task_id}\ntitle: {title}\nstatus: backlog\n---\n\nТело.\n",
            encoding="utf-8")

    def test_empty_dir_returns_empty_list(self) -> None:
        self.assertEqual([], list_all_tasks(self.tasks))

    def test_missing_dir_returns_empty_list(self) -> None:
        self.assertEqual([], list_all_tasks(Path("/nonexistent")))

    def test_lists_all_tasks(self) -> None:
        self._task("TASK-001", "Первая задача")
        self._task("TASK-002", "Вторая задача")

        expected = [
            {"id": "TASK-001", "title": "Первая задача"},
            {"id": "TASK-002", "title": "Вторая задача"},
        ]
        self.assertEqual(expected, list_all_tasks(self.tasks))

    def test_returns_sorted_by_id(self) -> None:
        self._task("TASK-005", "Пятая")
        self._task("TASK-001", "Первая")
        self._task("TASK-010", "Десятая")

        ids = [t["id"] for t in list_all_tasks(self.tasks)]
        self.assertEqual(["TASK-001", "TASK-005", "TASK-010"], ids)

    def test_ignores_non_task_files(self) -> None:
        self._task("TASK-001", "Задача")
        (self.tasks / "notes.md").write_text("# Заметки\n", encoding="utf-8")
        (self.tasks / "TASK-template.md").write_text("---\nid: TASK-999\n---\n", encoding="utf-8")

        self.assertEqual(1, len(list_all_tasks(self.tasks)))


if __name__ == "__main__":
    unittest.main()
