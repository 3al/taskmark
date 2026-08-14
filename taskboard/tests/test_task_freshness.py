"""Подсветка свежести: какие задачи трогают прямо сейчас (TASK-179).

Доска обновляется живьём, но карточка, которую агент правит в эту минуту, ничем
не отличается от лежащей неделю. Свежесть считается по времени правки файла
задачи — той же величине, на которой стоит порог залежалости, но с точностью
до минут.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.board_parser import annotate_fresh, parse_board  # noqa: E402
from backend.config import (CARD_LIMITS, DEFAULTS, PROJECT_KEYS,  # noqa: E402
                            card_style, validate_card_style)
from backend.statuses import load_pipeline  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
CARD = SRC / "components" / "TaskCard.jsx"
APP = SRC / "App.jsx"
SETTINGS = SRC / "components" / "SettingsModal.jsx"

TASK_FILE = """---
id: TASK-001
title: Заголовок
status: development
created: 2026-01-01 10:00
---

## Описание

Текст.

## Комментарии

## История коммитов
"""

NOW = datetime(2026, 3, 20, 12, 0)


class FreshnessTest(unittest.TestCase):
    """Поле приходит только у свежих карточек — остальным подсвечивать нечего."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.tmp.name)
        self.pipeline = load_pipeline({})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _annotated(self, minutes_ago: float | None, cfg: dict | None = None,
                   section: str = "Development") -> dict:
        """Разобрать доску с одной задачей, правленной `minutes_ago` минут назад.

        None — файла задачи нет вовсе (строка осталась от удалённой).
        """
        path = self.tasks_dir / "board.md"
        path.write_text(
            f"# Tasks Board\n\n## {section}\n\n"
            "- TASK-001 · [Заголовок](TASK-001-x.md) · Агент · 2026-03-01\n",
            encoding="utf-8")
        if minutes_ago is not None:
            task_file = self.tasks_dir / "TASK-001-x.md"
            task_file.write_text(TASK_FILE, encoding="utf-8")
            stamp = (NOW - timedelta(minutes=minutes_ago)).timestamp()
            os.utime(task_file, (stamp, stamp))
        board = parse_board(path, self.pipeline)
        annotate_fresh(self.tasks_dir, board, cfg or {}, self.pipeline, now=NOW)
        tasks = [t for c in board["columns"] for g in c["groups"] for t in g["tasks"]]
        return tasks[0]

    def test_just_edited_task_is_fresh(self) -> None:
        task = self._annotated(2, {"card_fresh_minutes": 15})

        self.assertEqual(2, task["fresh_minutes"])

    def test_edited_right_now_is_zero_minutes(self) -> None:
        """Правка секунду назад — «сейчас», а не отсутствие свежести."""
        task = self._annotated(0, {"card_fresh_minutes": 15})

        self.assertEqual(0, task["fresh_minutes"])

    def test_old_edit_has_no_field(self) -> None:
        """За порогом поля нет вовсе: подсвечивать нечего, фронт не решает сам."""
        task = self._annotated(40, {"card_fresh_minutes": 15})

        self.assertNotIn("fresh_minutes", task)

    def test_threshold_is_inclusive(self) -> None:
        task = self._annotated(15, {"card_fresh_minutes": 15})

        self.assertEqual(15, task["fresh_minutes"])

    def test_zero_threshold_turns_the_highlight_off(self) -> None:
        """Ноль — выключатель: отдельного флага показа у подсветки нет."""
        task = self._annotated(1, {"card_fresh_minutes": 0})

        self.assertNotIn("fresh_minutes", task)

    def test_default_threshold_applies_without_config(self) -> None:
        task = self._annotated(1, {})

        self.assertEqual(DEFAULTS["card_fresh_minutes"], 15)
        self.assertEqual(1, task["fresh_minutes"])

    def test_broken_threshold_falls_back_to_default(self) -> None:
        task = self._annotated(1, {"card_fresh_minutes": "скоро"})

        self.assertEqual(1, task["fresh_minutes"])

    def test_missing_file_is_silent(self) -> None:
        """Файла нет — правки не было, и подсвечивать нечего."""
        task = self._annotated(None, {"card_fresh_minutes": 15})

        self.assertNotIn("fresh_minutes", task)

    def test_future_mtime_is_silent(self) -> None:
        """Часы съехали — не подсвечиваем: подсветка означает работу, а не сбой."""
        task = self._annotated(-30, {"card_fresh_minutes": 15})

        self.assertNotIn("fresh_minutes", task)

    def test_terminal_status_is_silent(self) -> None:
        """В конце маршрута работа окончена, а выпуск правит задачи пачками."""
        task = self._annotated(1, {"card_fresh_minutes": 15}, section="Completed")

        self.assertNotIn("fresh_minutes", task)


class ConfigTest(unittest.TestCase):
    def test_threshold_has_default_and_limits(self) -> None:
        low, high = CARD_LIMITS["card_fresh_minutes"]
        self.assertEqual(0, low)
        self.assertTrue(low <= DEFAULTS["card_fresh_minutes"] <= high)

    def test_zero_passes_validation(self) -> None:
        """Ноль — рабочее значение (выключено), а не выход за границы."""
        _out, errors = validate_card_style({"card_fresh_minutes": "0"})

        self.assertEqual([], errors)

    def test_threshold_is_global_not_project(self) -> None:
        """«Прямо сейчас» — про темп работы человека, а не про репозиторий."""
        self.assertNotIn("card_fresh_minutes", PROJECT_KEYS)

    def test_card_style_carries_the_threshold(self) -> None:
        self.assertIn("card_fresh_minutes", card_style({}))


class FrontendTest(unittest.TestCase):
    """Кольцо на карточке, поле в настройках и таймер, который его гасит."""

    def test_card_reads_fresh_minutes(self) -> None:
        source = CARD.read_text(encoding="utf-8")

        self.assertIn("fresh_minutes", source)

    def test_settings_expose_the_threshold(self) -> None:
        source = SETTINGS.read_text(encoding="utf-8")

        self.assertIn("card_fresh_minutes", source)

    def test_board_refreshes_while_something_is_fresh(self) -> None:
        """Конец свежести события не порождает — доска перечитывается по таймеру."""
        source = APP.read_text(encoding="utf-8")

        self.assertIn("fresh_minutes", source)


if __name__ == "__main__":
    unittest.main()
