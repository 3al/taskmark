"""Починка односторонних блокировок кнопкой (TASK-077).

Зависимость хранится двумя концами: `blocked_by` у ждущей задачи и `blocks` у
блокера. Инструмент правит оба, но в проекте, где поля заполняли руками, связи
односторонние — и валидатор об этом говорит, а починить их можно было только
прогоном скрипта по каждой паре.

`blocked_by` — авторское поле, `blocks` — производное: при расхождении прав
первое, поэтому починка пересобирает `blocks` из него.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.board_repair import apply_repair, plan_repair  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.stall import parse_ids, stall_issues  # noqa: E402
from backend.task_parser import parse_task  # noqa: E402
from backend.validator import validate_project  # noqa: E402

CFG = {**DEFAULTS,
       "pipeline": ["backlog", "todo", "development", "testing", "done", "cancelled"],
       "actions": {"create": "backlog", "pick": "todo",
                   "start": "development", "return": "development"},
       "harnesses": {"claude": True, "opencode": False}}

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: backlog
created: 2026-07-31 10:00
blocked_by: {blocked_by}
blocks: {blocks}
---

## Описание

Тестовая задача.
"""


class BlocksRepairTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        scaffold_project(self.tasks, CFG, {"harnesses": CFG["harnesses"]})

    def make(self, task_id: str, title: str, blocked_by: str = "~",
             blocks: str = "~") -> None:
        name = f"{task_id}-{title.lower()}.md"
        (self.tasks / name).write_text(
            TASK_FILE.format(task_id=task_id, title=title,
                             blocked_by=blocked_by, blocks=blocks),
            encoding="utf-8")
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower() == "## backlog":
                lines.insert(i + 1, f"\n- {task_id} · [{title}]({name})")
                break
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def field(self, task_id: str, name: str) -> list[str]:
        return parse_ids(parse_task(self.tasks, task_id)["meta"].get(name))

    def plan(self) -> dict:
        return plan_repair(self.tasks, CFG)

    def test_missing_back_link_is_planned(self) -> None:
        self.make("TASK-013", "Первая")
        self.make("TASK-014", "Вторая", blocked_by="TASK-013")

        plan = self.plan()

        self.assertEqual([{"id": "TASK-013", "task": "TASK-014", "action": "add"}],
                         plan["blocks"])

    def test_apply_restores_both_ends(self) -> None:
        self.make("TASK-013", "Первая")
        self.make("TASK-014", "Вторая", blocked_by="TASK-013")

        result = apply_repair(self.tasks, CFG)

        self.assertTrue(result["ok"], result)
        self.assertEqual(["TASK-014"], self.field("TASK-013", "blocks"))
        self.assertEqual(["TASK-013"], self.field("TASK-014", "blocked_by"),
                         "авторское поле переписали — чинили не в ту сторону")
        self.assertEqual(1, result["blocks"])
        self.assertEqual([], stall_issues(self.tasks), stall_issues(self.tasks))

    def test_orphan_back_link_is_dropped(self) -> None:
        """`blocks` производное: ссылка, которой не соответствует `blocked_by`, уходит."""
        self.make("TASK-013", "Первая", blocks="TASK-014")
        self.make("TASK-014", "Вторая")

        plan = self.plan()
        self.assertEqual([{"id": "TASK-013", "task": "TASK-014", "action": "drop"}],
                         plan["blocks"])

        apply_repair(self.tasks, CFG)
        self.assertEqual([], self.field("TASK-013", "blocks"))
        self.assertEqual([], self.field("TASK-014", "blocked_by"))

    def test_broken_reference_is_left_alone(self) -> None:
        """Битую ссылку кнопка не чинит: правильного действия там нет."""
        self.make("TASK-014", "Вторая", blocked_by="TASK-404")

        self.assertEqual([], self.plan()["blocks"])

        apply_repair(self.tasks, CFG)
        self.assertEqual(["TASK-404"], self.field("TASK-014", "blocked_by"),
                         "починка молча выбросила ссылку на несуществующую задачу")

    def test_cycle_is_left_alone(self) -> None:
        """Цикл кнопкой не разрывается — обе стороны законны, выбирать не нам."""
        self.make("TASK-013", "Первая", blocked_by="TASK-014", blocks="TASK-014")
        self.make("TASK-014", "Вторая", blocked_by="TASK-013", blocks="TASK-013")

        self.assertEqual([], self.plan()["blocks"])
        self.assertTrue(any("Цикл" in w for w in stall_issues(self.tasks)))

    def test_consistent_project_has_nothing_to_do(self) -> None:
        self.make("TASK-013", "Первая", blocks="TASK-014")
        self.make("TASK-014", "Вторая", blocked_by="TASK-013")

        self.assertEqual([], self.plan()["blocks"])

    def test_repairable_counts_it(self) -> None:
        """Без счётчика кнопка на баннере не появится."""
        self.make("TASK-013", "Первая")
        self.make("TASK-014", "Вторая", blocked_by="TASK-013")

        report = validate_project(self.tasks, CFG)

        self.assertGreater(report["repairable"], 0)


class RepairUiTest(unittest.TestCase):
    """Окно починки стало про данные, а не только про доску."""

    def setUp(self) -> None:
        src = Path(__file__).resolve().parent.parent / "frontend" / "src"
        self.modal = (src / "components" / "BoardRepairModal.jsx").read_text(encoding="utf-8")
        self.app = (src / "App.jsx").read_text(encoding="utf-8")

    def test_group_for_blocks(self) -> None:
        self.assertIn("plan?.blocks", self.modal,
                      "односторонние блокировки не показаны отдельной группой")

    def test_title_matches_content(self) -> None:
        """Заголовок «Починка доски» врал бы: правится ещё и frontmatter."""
        self.assertNotIn("Починка доски", self.modal)
        self.assertNotIn("Починить доску", self.app)

    def test_counts_blocks_in_total(self) -> None:
        self.assertIn("plan.blocks", self.modal,
                      "правки блокировок не попадают в счётчик кнопки")


if __name__ == "__main__":
    unittest.main()
