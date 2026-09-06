"""Срок задачи: хранение, снятие, срез и вытеснение возраста на превью (TASK-225).

Срок — поле `due:` во frontmatter, только дата. Времени у него нет намеренно:
оно нужно редко, а два вида значения расползлись бы по сравнениям, текстам
и форматированию — и заодно потянули бы за собой вопрос часового пояса.

**Задача без срока — норма**, поэтому пустое значение его снимает, а мусор
в поле не превращается в пустую метку на доске: непрочитанный срок для превью
это то же, что его отсутствие.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.task_parser import (annotate_marks, due_left,  # noqa: E402
                                 parse_frontmatter, set_task_due)

TASK = """---
id: TASK-001
title: Задача со сроком
epic: ~
type: feature
size: ~
status: development
created: 2026-09-01 10:00
due: ~
---

## Описание

Текст.

## Комментарии

## История коммитов
"""


class _Tasks(unittest.TestCase):
    """Проект с одной задачей во временной папке."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.file = self.tasks / "TASK-001-задача.md"
        self.file.write_text(TASK, encoding="utf-8")

    def meta(self) -> dict:
        return parse_frontmatter(self.file.read_text(encoding="utf-8"))[0]

    def body(self) -> str:
        return self.file.read_text(encoding="utf-8")


class DueLeftTest(unittest.TestCase):
    """Сколько осталось — по календарным дням, без времени."""

    def test_today_is_zero(self) -> None:
        self.assertEqual(0, due_left("2026-09-07", date(2026, 9, 7)))

    def test_future_is_positive(self) -> None:
        self.assertEqual(5, due_left("2026-09-12", date(2026, 9, 7)))

    def test_overdue_is_negative(self) -> None:
        self.assertEqual(-6, due_left("2026-09-01", date(2026, 9, 7)))

    def test_garbage_is_none(self) -> None:
        """Мусор — не «сегодня» и не ноль: это отсутствие ответа."""
        for value in ("завтра", "12.09.2026", "", "~", "2026-13-40"):
            self.assertIsNone(due_left(value), value)


class SetDueTest(_Tasks):
    """Правка срока — то же, что делает скрипт."""

    def test_due_is_stored(self) -> None:
        result = set_task_due(self.tasks, "TASK-001", "2026-09-12")
        self.assertTrue(result["ok"])
        self.assertEqual("2026-09-12", self.meta()["due"])

    def test_change_goes_to_history(self) -> None:
        """Перенос срока объясняет ход работы — как смена размера."""
        set_task_due(self.tasks, "TASK-001", "2026-09-12")
        set_task_due(self.tasks, "TASK-001", "2026-09-20")
        text = self.body()
        self.assertIn("срок: 2026-09-12 (было не указан)", text)
        self.assertIn("срок: 2026-09-20 (было 2026-09-12)", text)

    def test_same_value_is_not_an_event(self) -> None:
        set_task_due(self.tasks, "TASK-001", "2026-09-12")
        set_task_due(self.tasks, "TASK-001", "2026-09-12")
        self.assertEqual(1, self.body().count("срок: 2026-09-12"))

    def test_empty_removes_due(self) -> None:
        """Задача без срока — норма, и передумать должно быть чем."""
        set_task_due(self.tasks, "TASK-001", "2026-09-12")
        result = set_task_due(self.tasks, "TASK-001", "")
        self.assertTrue(result["ok"])
        self.assertEqual("~", self.meta()["due"])
        self.assertIn("срок: не указан (было 2026-09-12)", self.body())

    def test_garbage_is_refused_and_file_untouched(self) -> None:
        before = self.body()
        result = set_task_due(self.tasks, "TASK-001", "завтра")
        self.assertFalse(result["ok"])
        self.assertIn("ГГГГ-ММ-ДД", result["error"])
        self.assertEqual(before, self.body())

    def test_missing_task_is_reported(self) -> None:
        result = set_task_due(self.tasks, "TASK-404", "2026-09-12")
        self.assertFalse(result["ok"])


class BoardMarksTest(_Tasks):
    """Что доска отдаёт превью."""

    def board(self) -> dict:
        return {"columns": [{"status": "development", "groups": [
            {"tasks": [{"id": "TASK-001", "file": self.file.name}]}]}]}

    def card(self) -> dict:
        marked = annotate_marks(self.tasks, self.board(), DEFAULTS)
        return marked["columns"][0]["groups"][0]["tasks"][0]

    def test_no_due_no_fields(self) -> None:
        """Задача без срока полей не получает — пустая метка хуже отсутствия."""
        card = self.card()
        self.assertNotIn("due", card)
        self.assertNotIn("due_left", card)

    def test_due_and_days_left_are_delivered(self) -> None:
        """Считает бэкенд: «сколько осталось» зависит от сегодняшнего дня."""
        soon = (date.today() + timedelta(days=3)).isoformat()
        set_task_due(self.tasks, "TASK-001", soon)
        card = self.card()
        self.assertEqual(soon, card["due"])
        self.assertEqual(3, card["due_left"])

    def test_overdue_is_negative_on_card(self) -> None:
        past = (date.today() - timedelta(days=2)).isoformat()
        set_task_due(self.tasks, "TASK-001", past)
        self.assertEqual(-2, self.card()["due_left"])

    def test_garbage_due_is_not_shown(self) -> None:
        """Файл правят руками: непрочитанный срок — то же, что его отсутствие."""
        self.file.write_text(TASK.replace("due: ~", "due: когда-нибудь"),
                             encoding="utf-8")
        self.assertNotIn("due_left", self.card())


if __name__ == "__main__":
    unittest.main()
