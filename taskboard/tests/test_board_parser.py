"""Тесты разбора строк доски (TASK-057).

Заголовок задачи пишет человек, и он свободно ставит в него скобки:
`[BE] [VIEWER] Счетчик посылок`. Такая запись — валидная markdown-ссылка,
но нераспознанная строка молча выпадает из доски: карточки нет, а валидатор
сообщает «файла нет на доске», хотя и файл, и строка на месте.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.board_parser import parse_board  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402
from backend.validator import validate_project  # noqa: E402


def _write_board(tasks_dir: Path, body: str) -> Path:
    board = tasks_dir / "board.md"
    board.write_text("# Tasks Board\n\n" + body, encoding="utf-8")
    return board


def _tasks_of(board: dict, status: str) -> list[dict]:
    for column in board["columns"]:
        if column["status"] == status:
            return [t for g in column["groups"] for t in g["tasks"]]
    return []


class BoardEntryParsingTest(unittest.TestCase):
    """Строка задачи разбирается независимо от того, что человек написал в заголовке."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.tmp.name)
        self.pipeline = load_pipeline({})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _parse(self, body: str) -> dict:
        return parse_board(_write_board(self.tasks_dir, body), self.pipeline)

    def test_brackets_in_title(self):
        """Скобки внутри заголовка не обрывают ссылку на файл."""
        board = self._parse(
            "## Backlog\n\n"
            "- TASK-001 · [E056-18698 [BE] [VIEWER] Счетчик посылок]"
            "(TASK-001-e056-18698-be-viewer-счетчик-посылок.md)\n"
        )
        tasks = _tasks_of(board, "backlog")
        self.assertEqual(len(tasks), 1, "строка со скобками в заголовке потеряна")
        self.assertEqual(tasks[0]["id"], "TASK-001")
        self.assertEqual(tasks[0]["title"], "E056-18698 [BE] [VIEWER] Счетчик посылок")
        self.assertEqual(tasks[0]["file"],
                         "TASK-001-e056-18698-be-viewer-счетчик-посылок.md")

    def test_brackets_in_title_with_tail(self):
        """Хвост «агент · дата» читается и у строки со скобками."""
        board = self._parse(
            "## Development\n\n"
            "- TASK-003 · [E056-18706 [BE] Счетчик](TASK-003-счетчик.md)"
            " · minimax-m2.5-free · 2026-03-11\n"
        )
        tasks = _tasks_of(board, "development")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["meta"], "minimax-m2.5-free · 2026-03-11")

    def test_plain_entry_still_parsed(self):
        """Обычная строка разбирается как раньше."""
        board = self._parse(
            "## Backlog\n\n"
            "- TASK-010 · [Обычный заголовок](TASK-010-обычный.md) · k3 · 2026-07-23\n"
        )
        tasks = _tasks_of(board, "backlog")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["title"], "Обычный заголовок")
        self.assertEqual(tasks[0]["file"], "TASK-010-обычный.md")
        self.assertEqual(tasks[0]["meta"], "k3 · 2026-07-23")
        self.assertFalse(tasks[0]["struck"])

    def test_struck_entry_still_parsed(self):
        """Зачёркнутая запись остаётся зачёркнутой."""
        board = self._parse(
            "## Cancelled\n\n"
            "- ~~TASK-011 · [Отменённая [BE] задача](TASK-011-отменённая.md)~~\n"
        )
        tasks = _tasks_of(board, "cancelled")
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0]["struck"])
        self.assertEqual(tasks[0]["title"], "Отменённая [BE] задача")

    def test_parentheses_in_tail(self):
        """Скобки в имени модели не подменяют ссылку на файл."""
        board = self._parse(
            "## Development\n\n"
            "- TASK-012 · [Заголовок](TASK-012-заголовок.md) · Claude (Opus 5) · 2026-07-29\n"
        )
        tasks = _tasks_of(board, "development")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["file"], "TASK-012-заголовок.md")
        self.assertEqual(tasks[0]["meta"], "Claude (Opus 5) · 2026-07-29")


class BoardSyncWarningTest(unittest.TestCase):
    """Симптом бага: валидатор объявляет ненайденными задачи, которые на доске есть."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_orphan_warning_for_bracketed_title(self):
        (self.tasks_dir / "TASK-001-счетчик.md").write_text("# t", encoding="utf-8")
        _write_board(
            self.tasks_dir,
            "## Backlog\n\n"
            "- TASK-001 · [E056-18698 [BE] [VIEWER] Счетчик](TASK-001-счетчик.md)\n"
            "\n## To Do\n\n_(нет)_\n",
        )
        report = validate_project(self.tasks_dir, {})
        orphan = [w for w in report["warnings"] if "нет на доске" in w]
        self.assertEqual(orphan, [], "задача есть на доске, но объявлена отсутствующей")


if __name__ == "__main__":
    unittest.main()
