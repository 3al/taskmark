"""Тесты починки рассинхрона доски и файлов задач (TASK-056).

Доска и frontmatter — два конца одной связки, и агент нередко правит один,
забывая второй. Починка чинит связку по одному правилу: **файлы задач —
источник правды**. Доска может врать (её копируют между проектами, правят
руками), а файл задачи — единственное, что реально существует.

Отсюда следы починки:

- файл без строки возвращается на доску в раздел своего `status:`;
- строка в чужом разделе переезжает в раздел статуса из файла (файл не
  трогаем — он правда);
- строка, которой не соответствует никакой файл, уезжает в технический
  раздел — включая **чужие** строки со скопированной доски: id совпал,
  но ни имя файла, ни заголовок — нет, значит это не наша задача;
- ссылка на переименованный файл переписывается (только если заголовок
  подтверждает, что задача та же).

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
    """Строка лежит не в том разделе — прав файл, строка переезжает."""

    def setUp(self) -> None:
        super().setUp()
        self.task_file("TASK-010", "Задача", "backlog", name="TASK-010-задача.md")
        self.add_entry("Testing", "- TASK-010 · [Задача](TASK-010-задача.md) · k3 · 2026-07-29")

    def test_plan_reports_move(self):
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(len(plan["move"]), 1)
        item = plan["move"][0]
        self.assertEqual(item["id"], "TASK-010")
        self.assertEqual(item["from"], "Testing")
        self.assertEqual(item["to"], "Backlog")

    def test_apply_moves_row_not_file(self):
        apply_repair(self.tasks_dir, CFG)
        self.assertEqual(self.sections_of()["Backlog"], ["TASK-010"])
        self.assertNotIn("TASK-010", self.sections_of().get("Testing", []))
        self.assertEqual(parse_task(self.tasks_dir, "TASK-010")["meta"]["status"], "backlog",
                         "файл — источник правды: его status трогать нельзя")


class ForeignRowTest(RepairCase):
    """Доска скопирована с другого проекта: id совпадают, задачи чужие.

    Главный сценарий второго возврата TASK-056: чужая строка TASK-001 не
    должна ни привязаться к нашему файлу, ни изменить его — ей место
    в техническом разделе, а нашему файлу — своя строка на доске.
    """

    def setUp(self) -> None:
        super().setUp()
        self.task_file("TASK-001", "Наша задача", "development",
                       name="TASK-001-наша-задача.md")
        self.add_entry("Done", "- TASK-001 · [Чужая задача](TASK-001-чужая.md) · кто-то · 2026-01-01")

    def test_plan_sends_foreign_row_to_lost(self):
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual([i["id"] for i in plan["lost"]], ["TASK-001"])
        self.assertEqual(plan["relink"], [],
                         "чужой заголовок — это не переименование")
        self.assertEqual(len(plan["add"]), 1,
                         "наш файл остался без строки — её надо добавить")

    def test_apply_keeps_our_file_untouched(self):
        apply_repair(self.tasks_dir, CFG)
        meta = parse_task(self.tasks_dir, "TASK-001")["meta"]
        self.assertEqual(meta["status"], "development",
                         "статус нашего файла не должен подстраиваться под чужую строку")
        sections = self.sections_of()
        self.assertNotIn("TASK-001", sections.get("Done", []))
        self.assertIn("TASK-001", sections.get("Потерянные", []))
        self.assertIn("TASK-001", sections.get("Development", []),
                      "наш файл получает свою строку в разделе своего статуса")

    def test_repair_converges_with_foreign_rows(self):
        apply_repair(self.tasks_dir, CFG)
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["move"], plan["lost"], plan["relink"]),
                         ([], [], [], []))
        self.assertEqual(validate_project(self.tasks_dir, CFG)["warnings"], [])

    def test_same_title_means_rename_not_foreign(self):
        """Заголовок совпал — это наша задача с битой ссылкой, а не чужая."""
        content = self.board.read_text(encoding="utf-8")
        content = content.replace("[Чужая задача]", "[Наша задача]")
        self.board.write_text(content, encoding="utf-8")
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(plan["lost"], [])
        self.assertEqual(len(plan["relink"]), 1)

    def test_foreign_row_in_lost_section_stays_there(self):
        """Чужая строка в свалке не воскресает restore'ом под наш файл."""
        apply_repair(self.tasks_dir, CFG)  # чужая строка уже в свалке
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(plan["add"], [], "наша строка уже на доске, добавлять нечего")
        self.assertEqual(plan["lost"], [])


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
        self.assertEqual((result["added"], result["moved"], result["lost"]), (1, 1, 1))

        report = validate_project(self.tasks_dir, CFG)
        self.assertEqual(report["warnings"], [])

        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["move"], plan["lost"], plan["relink"]),
                         ([], [], [], []))

    def test_clean_project_yields_empty_plan(self):
        self.task_file("TASK-005", "Чистая", "todo", name="TASK-005-чистая.md")
        self.add_entry("To Do", "- TASK-005 · [Чистая](TASK-005-чистая.md) · k3 · 2026-07-29")
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["move"], plan["lost"], plan["relink"]),
                         ([], [], [], []))


class StaleLinkTest(RepairCase):
    """Строка ссылается на переименованный файл, но файл с тем же id есть.

    Именно этот расклад качал починку по кругу: строку с битой ссылкой
    признавали сиротой и убирали в свалку, а оттуда её тут же возвращал
    restore — реальный файл с тем же id существует. Ссылку при переносе
    никто не правил, и цикл повторялся (возврат TASK-056 с ревью).
    """

    def setUp(self) -> None:
        super().setUp()
        self.task_file("TASK-030", "Переименованная", "testing",
                       name="TASK-030-новое-имя.md")
        self.add_entry("Testing",
                       "- TASK-030 · [Переименованная](TASK-030-старое-имя.md) · k3 · 2026-07-29")

    def test_plan_reports_relink_not_lost(self):
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(plan["lost"], [], "файл по id есть — это не сирота")
        self.assertEqual(plan["add"], [])
        self.assertEqual(len(plan["relink"]), 1)
        item = plan["relink"][0]
        self.assertEqual(item["id"], "TASK-030")
        self.assertEqual(item["from"], "TASK-030-старое-имя.md")
        self.assertEqual(item["to"], "TASK-030-новое-имя.md")

    def test_apply_rewrites_link_in_place(self):
        result = apply_repair(self.tasks_dir, CFG)
        self.assertEqual(result["relinked"], 1)
        content = self.board.read_text(encoding="utf-8")
        self.assertIn("](TASK-030-новое-имя.md)", content)
        self.assertNotIn("TASK-030-старое-имя.md", content)
        self.assertEqual(self.sections_of()["Testing"], ["TASK-030"],
                         "строка остаётся на своём месте — меняется только ссылка")

    def test_repair_converges_after_one_pass(self):
        """Одного применения хватает: ни качелей lost↔restore, ни остатка."""
        apply_repair(self.tasks_dir, CFG)
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["move"], plan["lost"], plan["relink"]),
                         ([], [], [], []))
        self.assertEqual(validate_project(self.tasks_dir, CFG)["warnings"], [])

    def test_restore_from_lost_section_fixes_link_too(self):
        """Возврат из свалки с битой ссылкой — сразу с исправленной."""
        apply_repair(self.tasks_dir, CFG)  # sanity: теперь это relink, не свалка
        # руками имитируем старое состояние: строка уже лежит в свалке с битой ссылкой
        content = self.board.read_text(encoding="utf-8")
        content = content.replace("](TASK-030-новое-имя.md)", "](TASK-030-старое-имя.md)")
        content = content.replace("## Testing", "## Потерянные", 1)
        self.board.write_text(content, encoding="utf-8")

        result = apply_repair(self.tasks_dir, CFG)
        self.assertEqual(result["added"], 1)
        content = self.board.read_text(encoding="utf-8")
        self.assertIn("](TASK-030-новое-имя.md)", content)
        self.assertNotIn("TASK-030-старое-имя.md", content)
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["lost"], plan["relink"]), ([], [], []))

    def test_validator_points_at_stale_link(self):
        report = validate_project(self.tasks_dir, CFG)
        self.assertTrue(any("TASK-030" in w and "TASK-030-новое-имя.md" in w
                            for w in report["warnings"]),
                        f"нет предупреждения об устаревшей ссылке: {report['warnings']}")
        self.assertGreater(report["repairable"], 0)


class RetitleTest(RepairCase):
    """Ссылка у строки точная, а заголовок чужой или устаревший.

    Третий возврат TASK-056: relink прошлой версии переписал ссылки чужих
    строк на локальные файлы, и критерий «ссылка совпала → строка своя»
    их легализовал — доска врала заголовками, а валидатор молчал.
    """

    def setUp(self) -> None:
        super().setUp()
        self.task_file("TASK-040", "Настоящая задача", "done",
                       name="TASK-040-настоящая.md")
        self.add_entry("Done",
                       "- TASK-040 · [Чужое имя](TASK-040-настоящая.md) · k3 · 2026-07-29")

    def test_plan_reports_retitle_not_lost(self):
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual(plan["lost"], [], "ссылка точная — строка своя, не сирота")
        self.assertEqual(plan["relink"], [])
        self.assertEqual(len(plan["retitle"]), 1)
        item = plan["retitle"][0]
        self.assertEqual(item["id"], "TASK-040")
        self.assertEqual(item["from"], "Чужое имя")
        self.assertEqual(item["to"], "Настоящая задача")

    def test_apply_rewrites_title_only(self):
        result = apply_repair(self.tasks_dir, CFG)
        self.assertEqual(result["retitled"], 1)
        content = self.board.read_text(encoding="utf-8")
        self.assertIn("[Настоящая задача](TASK-040-настоящая.md)", content)
        self.assertNotIn("Чужое имя", content)
        self.assertEqual(self.sections_of()["Done"], ["TASK-040"],
                         "место строки не меняется — правится только заголовок")
        self.assertEqual(parse_task(self.tasks_dir, "TASK-040")["meta"]["title"],
                         "Настоящая задача")

    def test_repair_converges(self):
        apply_repair(self.tasks_dir, CFG)
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual((plan["add"], plan["move"], plan["lost"],
                          plan["relink"], plan["retitle"]), ([], [], [], [], []))
        self.assertEqual(validate_project(self.tasks_dir, CFG)["warnings"], [])

    def test_validator_warns_about_title_mismatch(self):
        report = validate_project(self.tasks_dir, CFG)
        self.assertTrue(any("TASK-040" in w and "заголовок" in w
                            for w in report["warnings"]),
                        f"нет предупреждения о чужом заголовке: {report['warnings']}")
        self.assertGreater(report["repairable"], 0)

    def test_empty_frontmatter_title_skips_retitle(self):
        """Нет заголовка в файле — нечем доказать расхождение, оставляем как есть."""
        (self.tasks_dir / "TASK-041-без-заголовка.md").write_text(
            "---\nid: TASK-041\nstatus: done\n---\n", encoding="utf-8")
        self.add_entry("Done",
                       "- TASK-041 · [Какое-то имя](TASK-041-без-заголовка.md) · k3 · 2026-07-29")
        plan = plan_repair(self.tasks_dir, CFG)
        self.assertEqual([i["id"] for i in plan["retitle"]], ["TASK-040"])


class ValidatorSeesMismatchTest(RepairCase):
    """Строка не в том разделе — про это говорят заранее, а не молча."""

    def test_row_in_wrong_section_is_a_warning(self):
        self.task_file("TASK-010", "Задача", "backlog", name="TASK-010-задача.md")
        self.add_entry("Testing", "- TASK-010 · [Задача](TASK-010-задача.md) · k3 · 2026-07-29")
        report = validate_project(self.tasks_dir, CFG)
        self.assertTrue(any("TASK-010" in w and "статус" in w for w in report["warnings"]),
                        f"нет предупреждения о расхождении раздела и статуса: {report['warnings']}")

    def test_foreign_row_is_a_warning(self):
        self.task_file("TASK-001", "Наша задача", "development",
                       name="TASK-001-наша-задача.md")
        self.add_entry("Done", "- TASK-001 · [Чужая задача](TASK-001-чужая.md) · кто-то · 2026-01-01")
        report = validate_project(self.tasks_dir, CFG)
        self.assertTrue(any("TASK-001" in w for w in report["warnings"]),
                        f"нет предупреждения о чужой записи: {report['warnings']}")
        self.assertGreater(report["repairable"], 0)


if __name__ == "__main__":
    unittest.main()
