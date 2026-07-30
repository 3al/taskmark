"""Переименование задачи из UI (TASK-072): frontmatter, файл, строка доски.

Название задачи живёт в трёх местах: `title:` во frontmatter, имя файла и
строка на доске. Правка из интерфейса обязана свести все три — иначе задача
теряется: ссылка на доске ведёт в никуда, а поиск по заголовку врёт.

Отдельно проверяем, что название не ломает форматы, в которых оно хранится:
frontmatter — это одна строка `ключ: значение`, а строка доски — markdown-ссылка
`[Заголовок](файл)`.

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
from backend.queue_ops import relink_entry, retitle_entry  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402
from backend.task_parser import (find_task_file, normalize_title,  # noqa: E402
                                 parse_task, set_task_title, slugify)

CFG = {"pipeline": ["backlog", "done"],
       "actions": {"create": "backlog", "start": "backlog", "pick": "backlog"}}

BOARD = """# Tasks Board

## Backlog

- TASK-001 · [{title}]({file}) · Агент · 2026-07-30

## Done

_(нет)_
"""


class TaskRenameTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)

    def _create(self, task_id: str, title: str) -> None:
        slug = title.lower().strip()
        slug = slug.replace(" ", "-")
        (self.tasks / f"{task_id}-{slug}.md").write_text(
            f"---\nid: {task_id}\ntitle: {title}\nstatus: backlog\n---\n\nТело.\n",
            encoding="utf-8")

    def test_rename_updates_frontmatter(self) -> None:
        self._create("TASK-001", "Старое название")
        result = set_task_title(self.tasks, "TASK-001", "Новое название")
        self.assertTrue(result["ok"])
        task = parse_task(self.tasks, "TASK-001")
        self.assertEqual("Новое название", task["meta"]["title"])

    def test_rename_renames_file(self) -> None:
        self._create("TASK-001", "Старое название")
        result = set_task_title(self.tasks, "TASK-001", "Новое название")
        self.assertTrue(result["ok"])
        self.assertEqual("TASK-001-новое-название.md", result["file"])
        old = find_task_file(self.tasks, "TASK-001")
        self.assertIsNotNone(old)
        self.assertEqual("TASK-001-новое-название.md", old.name)

    def test_rename_unknown_task(self) -> None:
        result = set_task_title(self.tasks, "TASK-999", "Любое")
        self.assertFalse(result["ok"])

    def test_rename_empty_title(self) -> None:
        self._create("TASK-001", "Название")
        result = set_task_title(self.tasks, "TASK-001", "")
        self.assertFalse(result["ok"])

    def test_rename_same_slug_keeps_file(self) -> None:
        """Если slug не изменился, файл не переименовывается."""
        self._create("TASK-001", "Название")
        old_path = find_task_file(self.tasks, "TASK-001")
        result = set_task_title(self.tasks, "TASK-001", "Название")
        self.assertTrue(result["ok"])
        new_path = find_task_file(self.tasks, "TASK-001")
        self.assertEqual(old_path, new_path)

    def test_rename_preserves_body(self) -> None:
        self._create("TASK-001", "Старое")
        result = set_task_title(self.tasks, "TASK-001", "Новое")
        self.assertTrue(result["ok"])
        task = parse_task(self.tasks, "TASK-001")
        self.assertIn("Тело.", task["body"])

    def test_slugify_special_chars(self) -> None:
        self.assertEqual(slugify("Hello C++"), "hello-c")
        self.assertEqual(slugify("Простой текст"), "простой-текст")
        self.assertEqual(slugify("  A  B  C  "), "a-b-c")


class TitleStaysOneLineTest(unittest.TestCase):
    """Название не должно разрушать форматы, в которых хранится."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "TASK-001-старое.md").write_text(
            "---\nid: TASK-001\ntitle: Старое\nstatus: backlog\n---\n\nТело.\n",
            encoding="utf-8")

    def test_newline_collapses_into_space(self) -> None:
        """Перенос внутри названия разорвал бы frontmatter пополам."""
        result = set_task_title(self.tasks, "TASK-001", "Две\nстроки")
        self.assertTrue(result["ok"])
        self.assertEqual("Две строки", result["title"])
        task = parse_task(self.tasks, "TASK-001")
        self.assertEqual("Две строки", task["meta"]["title"])
        self.assertIn("Тело.", task["body"])

    def test_repeated_spaces_collapse(self) -> None:
        result = set_task_title(self.tasks, "TASK-001", "  Много   пробелов  ")
        self.assertEqual("Много пробелов", result["title"])

    def test_whitespace_only_title_rejected(self) -> None:
        self.assertFalse(set_task_title(self.tasks, "TASK-001", " \n ")["ok"])

    def test_link_break_rejected(self) -> None:
        """`](` внутри названия обрывает ссылку в строке доски."""
        result = set_task_title(self.tasks, "TASK-001", "Ссылка](взлом.md) хвост")
        self.assertFalse(result["ok"])
        self.assertEqual("Старое", parse_task(self.tasks, "TASK-001")["meta"]["title"])

    def test_single_brackets_allowed(self) -> None:
        """`[BE] Счётчик` — обычное название, парсер доски его переживает."""
        result = set_task_title(self.tasks, "TASK-001", "[BE] Счётчик")
        self.assertTrue(result["ok"])
        self.assertEqual("[BE] Счётчик", parse_task(self.tasks, "TASK-001")["meta"]["title"])

    def test_colon_in_title_survives_frontmatter(self) -> None:
        set_task_title(self.tasks, "TASK-001", "Баг: не грузится")
        self.assertEqual("Баг: не грузится",
                         parse_task(self.tasks, "TASK-001")["meta"]["title"])

    def test_backslash_in_title_is_literal(self) -> None:
        """Название — данные, а не шаблон подстановки re."""
        set_task_title(self.tasks, "TASK-001", r"Путь C:\1 и \g<0>")
        self.assertEqual(r"Путь C:\1 и \g<0>",
                         parse_task(self.tasks, "TASK-001")["meta"]["title"])

    def test_punctuation_only_title_keeps_valid_filename(self) -> None:
        """Slug пустой — остаётся голый номер, а не файл `TASK-001-.md`."""
        result = set_task_title(self.tasks, "TASK-001", "???")
        self.assertTrue(result["ok"])
        self.assertEqual("TASK-001.md", result["file"])
        self.assertIsNotNone(find_task_file(self.tasks, "TASK-001"))

    def test_normalize_title_is_idempotent(self) -> None:
        self.assertEqual("а б", normalize_title(normalize_title(" а \n б ")))


class BoardFollowsRenameTest(unittest.TestCase):
    """Строка доски после переименования: и ссылка, и заголовок."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "TASK-001-старое.md").write_text(
            "---\nid: TASK-001\ntitle: Старое\nstatus: backlog\n---\n\nТело.\n",
            encoding="utf-8")
        self.board = self.tasks / "board.md"
        self.board.write_text(
            BOARD.format(title="Старое", file="TASK-001-старое.md"), encoding="utf-8")

    def _entry(self) -> dict:
        board = parse_board(self.board, load_pipeline(CFG))
        entries = [e for col in board["columns"] for g in col["groups"] for e in g["tasks"]]
        self.assertEqual(1, len(entries))
        return entries[0]

    def test_entry_points_to_renamed_file(self) -> None:
        result = set_task_title(self.tasks, "TASK-001", "Новое название")
        relink_entry(self.board, "TASK-001", result["file"])
        retitle_entry(self.board, "TASK-001", result["title"])
        entry = self._entry()
        self.assertEqual("Новое название", entry["title"])
        self.assertEqual("TASK-001-новое-название.md", entry["file"])
        self.assertTrue((self.tasks / entry["file"]).exists())

    def test_entry_tail_survives(self) -> None:
        """Исполнитель и дата в хвосте строки — не наше дело, их не трогаем."""
        result = set_task_title(self.tasks, "TASK-001", "Новое")
        relink_entry(self.board, "TASK-001", result["file"])
        retitle_entry(self.board, "TASK-001", result["title"])
        line = [ln for ln in self.board.read_text(encoding="utf-8").splitlines()
                if "TASK-001" in ln][0]
        self.assertIn("Агент · 2026-07-30", line)

    def test_brackets_in_title_survive_board_roundtrip(self) -> None:
        result = set_task_title(self.tasks, "TASK-001", "[BE] Счётчик")
        relink_entry(self.board, "TASK-001", result["file"])
        retitle_entry(self.board, "TASK-001", result["title"])
        self.assertEqual("[BE] Счётчик", self._entry()["title"])


if __name__ == "__main__":
    unittest.main()
