"""Возраст задачи в статусе вместо агента на превью (TASK-080).

Нижняя строка превью занята хвостом строки доски — «кто и когда менял статус».
Имя модели там почти бесполезно, а дата сама по себе не отвечает на главный
вопрос при взгляде на доску: **что залежалось**. Возраст приходится считать
в уме.

Прежде чем показывать возраст, дата обязана быть правдой: `set_status.py`
обновляет хвост при каждой смене статуса, а перетаскивание мышью переносило
строку как есть — после DnD дата показывала предыдущий переход.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.board_parser import annotate_age, parse_board, retail_entry  # noqa: E402
from backend.config import CARD_LIMITS, DEFAULTS, PROJECT_KEYS  # noqa: E402
from backend.queue_ops import move_task  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
CARD = SRC / "components" / "TaskCard.jsx"
SETTINGS = SRC / "components" / "SettingsModal.jsx"

TASK_FILE = """---
id: {id}
title: {title}
status: {status}
created: 2026-01-01 10:00
---

## Описание

Текст.

## Комментарии

## История коммитов
"""


def _tasks_of(board: dict, status: str) -> list[dict]:
    for column in board["columns"]:
        if column["status"] == status:
            return [t for g in column["groups"] for t in g["tasks"]]
    return []


class RetailEntryTest(unittest.TestCase):
    """Хвост строки доски переписывается, прежний исполнитель сохраняется."""

    def test_date_is_replaced_and_agent_kept(self) -> None:
        entry = "- TASK-001 · [Заголовок](TASK-001-x.md) · Claude Opus 5 · 2026-01-05"

        out = retail_entry(entry, "2026-03-07")

        self.assertEqual(
            "- TASK-001 · [Заголовок](TASK-001-x.md) · Claude Opus 5 · 2026-03-07", out)

    def test_brackets_in_title_survive(self) -> None:
        """Заголовок пишет человек, и скобки в нём — норма (TASK-057)."""
        entry = "- TASK-002 · [[BE] [VIEWER] Счетчик](TASK-002-x.md) · Агент · 2026-01-05"

        out = retail_entry(entry, "2026-03-07")

        self.assertEqual(
            "- TASK-002 · [[BE] [VIEWER] Счетчик](TASK-002-x.md) · Агент · 2026-03-07", out)

    def test_entry_without_tail_stays_bare(self) -> None:
        """Исполнителя не было — выдумывать его перенос не должен."""
        entry = "- TASK-003 · [Заголовок](TASK-003-x.md)"

        self.assertEqual(entry, retail_entry(entry, "2026-03-07"))

    def test_entry_with_date_only_gets_new_date(self) -> None:
        entry = "- TASK-004 · [Заголовок](TASK-004-x.md) · 2026-01-05"

        self.assertEqual("- TASK-004 · [Заголовок](TASK-004-x.md) · 2026-03-07",
                         retail_entry(entry, "2026-03-07"))

    def test_struck_entry_is_not_broken(self) -> None:
        entry = "- ~~TASK-005 · [Заголовок](TASK-005-x.md) · Агент · 2026-01-05~~"

        self.assertIn("2026-03-07", retail_entry(entry, "2026-03-07"))
        self.assertIn("TASK-005", retail_entry(entry, "2026-03-07"))

    def test_unparsable_line_is_left_alone(self) -> None:
        self.assertEqual("- мусор", retail_entry("- мусор", "2026-03-07"))


class ParsedTailTest(unittest.TestCase):
    """Хвост разбирается на исполнителя и дату — подсказке нужны они порознь."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.tmp.name)
        self.pipeline = load_pipeline({})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _board(self, body: str) -> dict:
        path = self.tasks_dir / "board.md"
        path.write_text("# Tasks Board\n\n" + body, encoding="utf-8")
        return parse_board(path, self.pipeline)

    def test_agent_and_date_are_separate_fields(self) -> None:
        board = self._board(
            "## Development\n\n"
            "- TASK-001 · [Заголовок](TASK-001-x.md) · Claude Opus 5 · 2026-01-05\n")

        task = _tasks_of(board, "development")[0]
        self.assertEqual("Claude Opus 5", task["agent"])
        self.assertEqual("2026-01-05", task["moved"])
        # Прежнее поле остаётся: его читают старые сборки фронта
        self.assertEqual("Claude Opus 5 · 2026-01-05", task["meta"])

    def test_date_only_tail_has_no_agent(self) -> None:
        board = self._board(
            "## Development\n\n"
            "- TASK-002 · [Заголовок](TASK-002-x.md) · 2026-01-05\n")

        task = _tasks_of(board, "development")[0]
        self.assertEqual("", task["agent"])
        self.assertEqual("2026-01-05", task["moved"])

    def test_agent_only_tail_has_no_date(self) -> None:
        board = self._board(
            "## Development\n\n"
            "- TASK-003 · [Заголовок](TASK-003-x.md) · Claude Opus 5\n")

        task = _tasks_of(board, "development")[0]
        self.assertEqual("Claude Opus 5", task["agent"])
        self.assertEqual("", task["moved"])

    def test_no_tail_at_all(self) -> None:
        board = self._board(
            "## Development\n\n"
            "- TASK-004 · [Заголовок](TASK-004-x.md)\n")

        task = _tasks_of(board, "development")[0]
        self.assertEqual("", task["agent"])
        self.assertEqual("", task["moved"])


class AgeCaseMixin:
    """Доска из одной задачи и возраст на ней — общая обвязка двух наборов."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.tmp.name)
        self.pipeline = load_pipeline({})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _annotated(self, moved: str, cfg: dict | None = None,
                   today: date | None = None, section: str = "Development",
                   pipeline=None, touched: date | None = None) -> dict:
        """Разобрать доску с одной задачей и проставить возраст.

        `touched` — когда последний раз правили файл задачи; None означает
        «файла нет», и возраст тогда стоит на одной дате перехода.
        """
        pipeline = pipeline or self.pipeline
        path = self.tasks_dir / "board.md"
        tail = f" · Агент · {moved}" if moved else " · Агент"
        path.write_text(
            f"# Tasks Board\n\n## {section}\n\n"
            f"- TASK-001 · [Заголовок](TASK-001-x.md){tail}\n", encoding="utf-8")
        if touched is not None:
            task_file = self.tasks_dir / "TASK-001-x.md"
            task_file.write_text(
                TASK_FILE.format(id="TASK-001", title="Заголовок",
                                 status="development"), encoding="utf-8")
            stamp = datetime.combine(touched, time(12, 0)).timestamp()
            os.utime(task_file, (stamp, stamp))
        board = parse_board(path, pipeline)
        annotate_age(self.tasks_dir, board, cfg or {}, pipeline,
                     today=today or date(2026, 3, 20))
        tasks = [t for c in board["columns"] for g in c["groups"] for t in g["tasks"]]
        return tasks[0]


class AnnotateAgeTest(AgeCaseMixin, unittest.TestCase):
    """Возраст показывается только у залежавшихся — остальным строка не нужна."""

    def test_old_task_gets_age(self) -> None:
        task = self._annotated("2026-03-01", {"card_stale_days": 7})

        self.assertEqual(19, task["stale_days"])

    def test_young_task_has_no_age(self) -> None:
        """Моложе порога — нижней строки нет вовсе, карточка остаётся короткой."""
        task = self._annotated("2026-03-18", {"card_stale_days": 7})

        self.assertNotIn("stale_days", task)

    def test_threshold_is_inclusive(self) -> None:
        """«Залежалась на неделю» — это уже неделя, а не восемь дней."""
        task = self._annotated("2026-03-13", {"card_stale_days": 7})

        self.assertEqual(7, task["stale_days"])

    def test_threshold_comes_from_project_config(self) -> None:
        task = self._annotated("2026-03-01", {"card_stale_days": 30})

        self.assertNotIn("stale_days", task)

    def test_default_threshold_applies_without_config(self) -> None:
        task = self._annotated("2026-01-01", {})

        self.assertEqual(DEFAULTS["card_stale_days"], 7)
        self.assertEqual(78, task["stale_days"])

    def test_task_without_date_is_silent(self) -> None:
        """Задачу не двигали ни разу — возраст в статусе неизвестен."""
        task = self._annotated("")

        self.assertNotIn("stale_days", task)

    def test_broken_date_is_silent(self) -> None:
        task = self._annotated("вчера")

        self.assertNotIn("stale_days", task)

    def test_future_date_is_silent(self) -> None:
        """Дата из будущего — не залежалость, а испорченная строка."""
        task = self._annotated("2026-04-01")

        self.assertNotIn("stale_days", task)

    def test_terminal_status_is_silent(self) -> None:
        """В конце маршрута стоять и положено — работа окончена."""
        task = self._annotated("2026-01-01", section="Completed")

        self.assertNotIn("stale_days", task)

    def test_offramp_is_silent(self) -> None:
        """Отменённая задача не залёживается: из съезда не возвращаются."""
        pipeline = load_pipeline(
            {"pipeline": ["backlog", "development", "done", "cancelled"]})
        task = self._annotated("2026-01-01", section="Cancelled", pipeline=pipeline)

        self.assertNotIn("stale_days", task)

    def test_unknown_status_still_ages(self) -> None:
        """Пайплайн поменяли, задача осталась — молчать о ней не за что."""
        task = self._annotated("2026-01-01", section="Hotfix Wait")

        self.assertEqual(78, task["stale_days"])


class EditFreshnessTest(AgeCaseMixin, unittest.TestCase):
    """Задачу правят, не двигая статус, — залежавшейся она не считается.

    Возраст стоит на дате перехода, и у задачи, которую дорабатывают неделю в
    одном статусе, превью говорило «7 дней здесь» — читается как «застряла»
    (TASK-178). Правку видно по времени правки файла задачи.
    """

    def test_recently_edited_task_is_silent(self) -> None:
        task = self._annotated("2026-03-01", {"card_stale_days": 7},
                               touched=date(2026, 3, 20))

        self.assertNotIn("stale_days", task)

    def test_untouched_task_still_ages(self) -> None:
        """Файл лежит нетронутым — залежалость настоящая, возраст от перехода."""
        task = self._annotated("2026-03-01", {"card_stale_days": 7},
                               touched=date(2026, 3, 2))

        self.assertEqual(19, task["stale_days"])

    def test_edit_freshness_uses_the_same_threshold(self) -> None:
        """Порог один: «залежалось» меряется одной неделей с обоих концов."""
        task = self._annotated("2026-03-01", {"card_stale_days": 7},
                               touched=date(2026, 3, 13))

        self.assertEqual(19, task["stale_days"])

    def test_edit_a_day_short_of_the_threshold_silences(self) -> None:
        task = self._annotated("2026-03-01", {"card_stale_days": 7},
                               touched=date(2026, 3, 14))

        self.assertNotIn("stale_days", task)

    def test_future_mtime_is_silent(self) -> None:
        """Часы съехали — молчим: ложная метка «залежалась» хуже её отсутствия."""
        task = self._annotated("2026-03-01", {"card_stale_days": 7},
                               touched=date(2026, 4, 1))

        self.assertNotIn("stale_days", task)

    def test_missing_file_keeps_the_age(self) -> None:
        """Файла нет (строка осталась от удалённой задачи) — молчать не за что."""
        task = self._annotated("2026-03-01", {"card_stale_days": 7})

        self.assertEqual(19, task["stale_days"])


class MoveTouchesDateTest(unittest.TestCase):
    """Перенос мышью обновляет дату: иначе возраст врал бы о предыдущем переходе."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.tmp.name)
        (self.tasks_dir / "TASK-001-x.md").write_text(
            TASK_FILE.format(id="TASK-001", title="Заголовок", status="backlog"),
            encoding="utf-8")
        self.board = self.tasks_dir / "board.md"
        self.board.write_text(
            "# Tasks Board\n\n"
            "## Backlog\n\n"
            "- TASK-001 · [Заголовок](TASK-001-x.md) · Claude Opus 5 · 2026-01-05\n\n"
            "## Development\n\n"
            "_(нет)_\n", encoding="utf-8")
        self.cfg = {"pipeline": ["backlog", "development", "done"],
                    "actions": {"create": "backlog", "start": "development"}}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _entry(self) -> str:
        for line in self.board.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("- TASK-001"):
                return line
        return ""

    def test_move_writes_today_and_keeps_agent(self) -> None:
        move_task(self.tasks_dir, self.cfg, "TASK-001", "Development")

        entry = self._entry()
        self.assertIn(date.today().isoformat(), entry)
        self.assertIn("Claude Opus 5", entry)
        self.assertNotIn("2026-01-05", entry)

    def test_repair_move_keeps_the_old_date(self) -> None:
        """Починка доски двигает строку под файл задачи — это не перевод."""
        move_task(self.tasks_dir, self.cfg, "TASK-001", "Development",
                  touch_status=False)

        self.assertIn("2026-01-05", self._entry())


class ConfigTest(unittest.TestCase):
    def test_threshold_has_default_and_limits(self) -> None:
        self.assertIn("card_stale_days", DEFAULTS)
        low, high = CARD_LIMITS["card_stale_days"]
        self.assertTrue(low <= DEFAULTS["card_stale_days"] <= high)

    def test_threshold_lives_in_the_project_layer(self) -> None:
        """Темп работы — свойство репозитория, а не глаз пользователя."""
        self.assertIn("card_stale_days", PROJECT_KEYS)


class FrontendTest(unittest.TestCase):
    """Карточка показывает возраст, а исполнителя с датой уводит в подсказку."""

    def test_card_reads_stale_days(self) -> None:
        source = CARD.read_text(encoding="utf-8")

        self.assertIn("stale_days", source)

    def test_card_keeps_agent_and_date_in_the_title(self) -> None:
        source = CARD.read_text(encoding="utf-8")

        self.assertIn("task.agent", source)
        self.assertIn("task.moved", source)

    def test_settings_expose_the_threshold(self) -> None:
        source = SETTINGS.read_text(encoding="utf-8")

        self.assertIn("card_stale_days", source)


if __name__ == "__main__":
    unittest.main()
