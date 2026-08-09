"""Задачи эпика для окна эпика (TASK-030).

Эпик — единственная связь задачи с другими задачами, но увидеть эту связь было
негде: в файле лежит ключ, имя — в реестре, а «что ещё входит в эпик» приходилось
собирать грепом. Окно эпика показывает состав целиком, и порядок в нём —
**пайплайна проекта**, а не алфавита: список читают как маршрут, по которому эпик
едет, поэтому съезды (отмена) стоят в конце, за терминальным статусом.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.epics import epic_tasks  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402

TASK = """---
id: {task_id}
title: {title}
epic: {epic}
status: {status}
created: 2026-08-09 10:00
---

## Описание

Текст.
"""


class EpicTasksCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        # Пайплайн задаём явно: у каждого проекта он свой, и порядок в окне
        # эпика проверяется именно против него, а не против дефолтов поставки
        self.pipeline = load_pipeline({"pipeline": ["backlog", "todo", "development",
                                                    "testing", "done", "cancelled"]})

    def task(self, task_id: str, status: str, epic: str = "E056-18500",
             title: str = "Задача") -> None:
        (self.tasks / f"{task_id}-test.md").write_text(
            TASK.format(task_id=task_id, title=title, epic=epic, status=status),
            encoding="utf-8")

    def ids(self, key: str = "E056-18500") -> list[str]:
        return [t["id"] for t in epic_tasks(self.tasks, key, self.pipeline)]


class OrderTest(EpicTasksCase):
    """Порядок — маршрут пайплайна, а не номер задачи и не алфавит."""

    def test_sorted_by_pipeline(self) -> None:
        self.task("TASK-003", "backlog")
        self.task("TASK-001", "done")
        self.task("TASK-002", "development")

        self.assertEqual(self.ids(), ["TASK-003", "TASK-002", "TASK-001"])

    def test_offramp_goes_last(self) -> None:
        """Отменённая задача — съезд с маршрута, её место за терминальным статусом."""
        self.task("TASK-001", "cancelled")
        self.task("TASK-002", "done")
        self.task("TASK-003", "todo")

        self.assertEqual(self.ids(), ["TASK-003", "TASK-002", "TASK-001"])

    def test_unknown_status_after_known(self) -> None:
        """Статус вне пайплайна не теряется: он уходит в конец, но остаётся видимым."""
        self.task("TASK-001", "выдуманный")
        self.task("TASK-002", "development")

        self.assertEqual(self.ids(), ["TASK-002", "TASK-001"])

    def test_same_status_keeps_task_order(self) -> None:
        self.task("TASK-002", "development")
        self.task("TASK-001", "development")

        self.assertEqual(self.ids(), ["TASK-001", "TASK-002"])


class SelectionTest(EpicTasksCase):
    """Что попадает в эпик, а что нет."""

    def test_foreign_and_empty_epics_excluded(self) -> None:
        self.task("TASK-001", "todo")
        self.task("TASK-002", "todo", epic="E999-OTHER")
        self.task("TASK-003", "todo", epic="~")

        self.assertEqual(self.ids(), ["TASK-001"])

    def test_unknown_key_is_empty_not_error(self) -> None:
        self.task("TASK-001", "todo")

        self.assertEqual(epic_tasks(self.tasks, "E000-NOPE", self.pipeline), [])

    def test_blank_key_matches_nothing(self) -> None:
        """Пустой ключ не должен собирать задачи без эпика в «эпик без имени»."""
        self.task("TASK-001", "todo", epic="~")

        self.assertEqual(epic_tasks(self.tasks, "", self.pipeline), [])

    def test_key_match_is_exact(self) -> None:
        self.task("TASK-001", "todo", epic="E056-18500")
        self.task("TASK-002", "todo", epic="E056-185")

        self.assertEqual(self.ids("E056-185"), ["TASK-002"])


class ShapeTest(EpicTasksCase):
    """Что отдаётся строке списка: подпись и цвет — из пайплайна, а не из фронта."""

    def test_entry_carries_status_label_and_color(self) -> None:
        self.task("TASK-001", "development", title="Окно эпика")

        entry = epic_tasks(self.tasks, "E056-18500", self.pipeline)[0]

        self.assertEqual(entry["id"], "TASK-001")
        self.assertEqual(entry["title"], "Окно эпика")
        self.assertEqual(entry["file"], "TASK-001-test.md")
        self.assertEqual(entry["status"], "development")
        self.assertEqual(entry["label"], "Development")
        self.assertEqual(entry["color"], "sky")

    def test_unknown_status_keeps_itself_as_label(self) -> None:
        """Выключенный из пайплайна статус остаётся собой — врать цветом нечем."""
        self.task("TASK-001", "выдуманный")

        entry = epic_tasks(self.tasks, "E056-18500", self.pipeline)[0]

        self.assertEqual(entry["label"], "выдуманный")
        self.assertEqual(entry["color"], "")


if __name__ == "__main__":
    unittest.main()
