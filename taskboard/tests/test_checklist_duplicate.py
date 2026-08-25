"""Чеклист не дублирует требование этапа, и план закрывается в свой момент (TASK-186).

Факт «проверку подтвердил человек» жил в двух местах: требование этапа, которое
закрывает скрипт, и пункт чеклиста, который агент закрывает руками. Копии
расходились — подтверждение приходило через скрипт, пункт оставался открытым.

Требование видно на карточке долгом и, в отличие от галочки, перехода не
пропускает. Значит лишняя копия — пункт, и тексты поставки его больше не
узаконивают. Раз таких пунктов нет, у плана остаётся один момент закрытия —
уход из работы, и незакрытое называется там.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import render_rules  # noqa: E402
from tests.test_finish_reminders import PLAIN_CFG, Project  # noqa: E402

SKILLS = (Path(__file__).resolve().parent.parent / "templates" / "agentic"
          / ".claude" / "skills")


class NoDuplicateRuleTest(unittest.TestCase):
    """Правила и скилл передачи запрещают дублировать требование пунктом."""

    def test_rules_forbid_duplicating_a_requirement(self) -> None:
        rules = render_rules({**DEFAULTS})
        section = rules[rules.index("## Чеклист задачи"):rules.index("## Тип задачи")]
        self.assertIn("требован", section.lower())

    def test_handoff_no_longer_legitimizes_such_items(self) -> None:
        """Прежний абзац «пункты, которые закрывает проверка, не трогай» убран."""
        text = (SKILLS / "handoff-task" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("которые закрывает проверка, не трогай", text)

    def test_handoff_says_what_to_do_with_one(self) -> None:
        """Увидел такой пункт — удали: его место в требовании этапа."""
        text = (SKILLS / "handoff-task" / "SKILL.md").read_text(encoding="utf-8")
        section = text[text.index("## Шаг 3"):text.index("## Шаг 4")]
        self.assertIn("требован", section.lower())


class ChecklistMomentTest(Project):
    """Незакрытые пункты называются при уходе из работы, а не в её конце."""

    CFG = PLAIN_CFG

    def handoff(self, task_id: str, status: str) -> str:
        result = self.move(task_id, status)
        self.assertTrue(result.get("ok"), result.get("error"))
        return " ".join(result.get("handoff_reminders", []))

    def test_open_items_named_on_leaving_work(self) -> None:
        self.make("TASK-001", status="development", section="## Development",
                  checklist="- [x] Сделано\n- [ ] Вынести парсер")
        said = self.handoff("TASK-001", "testing")
        self.assertIn("Вынести парсер", said)

    def test_closed_plan_is_silent_about_items(self) -> None:
        self.make("TASK-002", status="development", section="## Development",
                  checklist="- [x] Сделано")
        self.assertNotIn("пункт", self.handoff("TASK-002", "testing").lower())

    def test_no_checklist_no_question(self) -> None:
        """Плана не вели — спрашивать его незачем."""
        self.make("TASK-003", status="development", section="## Development",
                  checklist="")
        self.assertNotIn("пункт", self.handoff("TASK-003", "testing").lower())

    def test_work_done_no_longer_repeats_it(self) -> None:
        """Конец работы про пункты молчит: момент у плана один."""
        self.make("TASK-004", status="testing", section="## Testing",
                  checklist="- [ ] Вынести парсер")
        said = " ".join(self.reminders("TASK-004", "completed"))
        self.assertNotIn("Вынести парсер", said)


if __name__ == "__main__":
    unittest.main()
