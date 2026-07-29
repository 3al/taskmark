"""Тесты починки рассинхрона доски и файлов задач (TASK-056).

Доска и frontmatter — два конца одной связки, и агент нередко правит один,
забывая второй. Починка чинит связку по одному правилу: **доска — источник
правды**. Файл, которого нет на доске, попадает на неё; строка, у которой нет
файла, уезжает в технический раздел; расхождение статуса выправляется в файле.

Правки идут по чужим данным, поэтому план (`plan_repair`) отделён от применения
(`apply_repair`): пользователь сначала видит список, потом соглашается.

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
from backend.board_repair import apply_repair, plan_repair, visible_columns  # noqa: E402
from backend.statuses import load_pipeline  # noqa: E402
from backend.task_parser import parse_task  # noqa: E402
from backend.validator import validate_project  # noqa: E402

CFG = {"pipeline": ["backlog", "todo", "development", "testing", "done"],
       "actions": {"create": "backlog", "start": "development", "pick": "todo"}}

BOARD = """# Tasks Board

## Backlog

_(нет)_

## To Do

_(нет)_

## Development

_(нет)_

## Testing

_(нет)_

## Done

_(нет)_
"""

TASK = """---
id: {id}
title: {title}
status: {status}
---

## Описание

текст
"""


class RepairCase(unittest.TestCase):
    """Общая обвязка: временная папка tasks/ с доской и файлами задач."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.tmp.name)
        self.board = self.tasks_dir / "board.md"
        self.board.write_text(BOARD, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def task_file(self, task_id: str, title: str, status: str, name: str | None = None) -> Path:
        path = self.tasks_dir / (name or f"{task_id}-{title.lower().replace(' ', '-')}.md")
        path.write_text(TASK.format(id=task_id, title=title, status=status), encoding="utf-8")
        return path

    def add_entry(self, section: str, line: str) -> None:
        """Дописать строку в раздел доски (готовим рассинхрон руками)."""
        lines = self.board.read_text(encoding="utf-8").splitlines()
        idx = lines.index(f"## {section}")
        # заглушку _(нет)_ убираем — иначе она останется висеть в разделе
        for i in range(idx, len(lines)):
            if lines[i].strip() == "_(нет)_":
                lines.pop(i)
                break
        lines.insert(idx + 2, line)
        self.board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def sections_of(self) -> dict[str, list[str]]:
        """Раздел доски → номера задач в нём."""
        board = parse_board(self.board, load_pipeline(CFG))
        return {c["title"]: [t["id"] for g in c["groups"] for t in g["tasks"]]
                for c in board["columns"]}


class MissingOnBoardTest(RepairCase):
    """Файл задачи есть, строки на доске нет."""

    def test_plan_puts_file_into_section_of_its_status(self):
        self.task_file("TASK-001", "Первая", "development")
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(len(plan["add"]), 1)
        item = plan["add"][0]
        self.assertEqual(item["id"], "TASK-001")
        self.assertEqual(item["section"], "Development")
        self.assertEqual(item["title"], "Первая")

    def test_plan_does_not_touch_files(self):
        """План — только отчёт: доска после него не меняется."""
        self.task_file("TASK-001", "Первая", "development")
        before = self.board.read_text(encoding="utf-8")
        plan_repair(self.tasks_dir, CFG)
        self.assertEqual(before, self.board.read_text(encoding="utf-8"))

    def test_apply_adds_entry_to_board(self):
        self.task_file("TASK-001", "Первая", "development")
        result = apply_repair(self.tasks_dir, CFG)
        self.assertEqual(result["added"], 1)
        self.assertEqual(self.sections_of()["Development"], ["TASK-001"])

    def test_unknown_status_goes_to_create_section(self):
        """Статус, которого нет в пайплайне, — не повод потерять задачу."""
        self.task_file("TASK-002", "Вторая", "какой-то-свой")
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(plan["add"][0]["section"], "Backlog")

    def test_bracketed_title_survives_round_trip(self):
        """Скобки в заголовке доживают до доски и читаются обратно (TASK-057)."""
        self.task_file("TASK-003", "[BE] Счетчик", "todo", name="TASK-003-счетчик.md")
        apply_repair(self.tasks_dir, CFG)
        self.assertEqual(self.sections_of()["To Do"], ["TASK-003"])
        self.assertEqual(plan_repair(self.tasks_dir, CFG)["add"], [])


class StatusMismatchTest(RepairCase):
    """Строка на доске и status: в файле разошлись — прав раздел доски."""

    def setUp(self) -> None:
        super().setUp()
        self.task_file("TASK-010", "Задача", "backlog", name="TASK-010-задача.md")
        self.add_entry("Testing", "- TASK-010 · [Задача](TASK-010-задача.md) · k3 · 2026-07-29")

    def test_plan_reports_mismatch(self):
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(len(plan["status"]), 1)
        item = plan["status"][0]
        self.assertEqual(item["id"], "TASK-010")
        self.assertEqual(item["from"], "backlog")
        self.assertEqual(item["to"], "testing")

    def test_apply_fixes_frontmatter_not_board(self):
        apply_repair(self.tasks_dir, CFG)
        self.assertEqual(parse_task(self.tasks_dir, "TASK-010")["meta"]["status"], "testing")
        self.assertEqual(self.sections_of()["Testing"], ["TASK-010"],
                         "строку на доске трогать не должны — она источник правды")


class LostEntryTest(RepairCase):
    """Строка ссылается на файл, которого нет."""

    def setUp(self) -> None:
        super().setUp()
        self.add_entry("Development",
                       "- TASK-020 · [Пропавшая](TASK-020-пропавшая.md) · k3 · 2026-07-29")

    def test_plan_reports_lost_entry(self):
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(len(plan["lost"]), 1)
        self.assertEqual(plan["lost"][0]["id"], "TASK-020")

    def test_apply_moves_entry_to_technical_section(self):
        apply_repair(self.tasks_dir, CFG)
        sections = self.sections_of()
        self.assertNotIn("TASK-020", sections.get("Development", []))
        self.assertIn("TASK-020", sections.get("Потерянные", []),
                      "запись должна остаться в файле, а не исчезнуть")

    def test_lost_section_is_hidden_from_ui(self):
        """Свалка — технический раздел: колонкой на доске он не показывается."""
        apply_repair(self.tasks_dir, CFG)
        board = parse_board(self.board, load_pipeline(CFG))
        titles = [c["title"] for c in visible_columns(board, CFG)]
        self.assertNotIn("Потерянные", titles)

    def test_lost_section_is_not_reported_as_stray(self):
        """И не считается «разделом вне пайплайна» — иначе починка родит новый баннер."""
        apply_repair(self.tasks_dir, CFG)
        report = validate_project(self.tasks_dir, CFG)
        stray = [w for w in report["warnings"] if "вне пайплайна" in w]
        self.assertEqual(stray, [])

    def test_entries_already_in_lost_section_are_left_alone(self):
        """Повторная починка не гоняет сирот по кругу."""
        apply_repair(self.tasks_dir, CFG)
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(plan["lost"], [])


class RepairIsIdempotentTest(RepairCase):
    """После починки жаловаться не на что — и повторный вызов ничего не делает."""

    def test_all_warnings_gone(self):
        self.task_file("TASK-001", "Первая", "development")
        self.task_file("TASK-002", "Вторая", "backlog", name="TASK-002-вторая.md")
        self.add_entry("Done", "- TASK-002 · [Вторая](TASK-002-вторая.md) · k3 · 2026-07-29")
        self.add_entry("Development",
                       "- TASK-020 · [Пропавшая](TASK-020-пропавшая.md) · k3 · 2026-07-29")

        result = apply_repair(self.tasks_dir, CFG)
        self.assertEqual((result["added"], result["restatused"], result["lost"]), (1, 1, 1))

        report = validate_project(self.tasks_dir, CFG)
        self.assertEqual(report["warnings"], [])

        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["status"], plan["lost"]), ([], [], []))

    def test_clean_project_yields_empty_plan(self):
        self.task_file("TASK-005", "Чистая", "todo", name="TASK-005-чистая.md")
        self.add_entry("To Do", "- TASK-005 · [Чистая](TASK-005-чистая.md) · k3 · 2026-07-29")
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["status"], plan["lost"]), ([], [], []))


class ValidatorSeesMismatchTest(RepairCase):
    """Расхождение статуса раньше было невидимым — теперь про него говорят."""

    def test_status_mismatch_is_a_warning(self):
        self.task_file("TASK-010", "Задача", "backlog", name="TASK-010-задача.md")
        self.add_entry("Testing", "- TASK-010 · [Задача](TASK-010-задача.md) · k3 · 2026-07-29")
        report = validate_project(self.tasks_dir, CFG)
        self.assertTrue(any("TASK-010" in w and "статус" in w for w in report["warnings"]),
                        f"нет предупреждения о расхождении статуса: {report['warnings']}")


if __name__ == "__main__":
    unittest.main()
