"""Момент передачи назван на входе в работу, а не только на выходе (TASK-185).

Напоминание о передаче печаталось на уходе из рабочего статуса — то есть после
события, о котором предупреждает: коммит, push и MR успевают пройти раньше
первого вызова скрипта. Сказанное на входе успевает всегда.

Вторая половина — про волт: строка о передаче звучала только при включённом
волте, и в проекте без него момент не комментировал никто.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_finish_reminders import PLAIN_CFG, Project  # noqa: E402

SKILL = (Path(__file__).resolve().parent.parent / "templates" / "agentic"
         / ".claude" / "skills" / "handoff-task" / "SKILL.md")


class ExitIsNamedOnEntryTest(Project):
    """Вход в работу говорит, чем работа кончается и что считается выходом."""

    CFG = PLAIN_CFG

    def entry(self, task_id: str, status: str) -> str:
        result = self.move(task_id, status)
        self.assertTrue(result.get("ok"), result.get("error"))
        return " ".join(result.get("entry_reminders", []))

    def test_entry_names_the_events_that_count_as_exit(self) -> None:
        self.make("TASK-001", status="todo", section="## To Do")
        said = self.entry("TASK-001", "development")
        self.assertIn("handoff-task", said)
        for event in ("коммит", "push"):
            self.assertIn(event, said)

    def test_entry_line_avoids_optional_vocabulary(self) -> None:
        """Строку читают все проекты — словаря опциональной возможности в ней нет."""
        self.make("TASK-003", status="todo", section="## To Do")
        self.assertNotIn("MR", self.entry("TASK-003", "development"))

    def test_other_transitions_do_not_repeat_it(self) -> None:
        """Правило выхода звучит на входе, а не на каждом переходе."""
        self.make("TASK-002", status="development", section="## Development")
        self.assertNotIn("handoff-task", self.entry("TASK-002", "testing"))


class HandoffReminderTest(Project):
    """Напоминание о передаче звучит независимо от волта."""

    CFG = PLAIN_CFG

    def handoff(self, task_id: str, status: str) -> list[str]:
        result = self.move(task_id, status)
        self.assertTrue(result.get("ok"), result.get("error"))
        return result.get("handoff_reminders", [])

    def test_speaks_without_vault(self) -> None:
        self.write_config(vault=False)
        self.make("TASK-001", status="development", section="## Development")
        said = " ".join(self.handoff("TASK-001", "testing"))
        self.assertTrue(said, "без волта момент передачи не прокомментирован")
        self.assertIn("что именно проверять", said)
        self.assertNotIn("волт", said.lower())

    def test_vault_adds_its_own_line(self) -> None:
        self.write_config(vault=True)
        self.make("TASK-002", status="development", section="## Development")
        said = self.handoff("TASK-002", "testing")
        self.assertIn("волт", " ".join(said).lower())
        self.assertGreater(len(said), 1, "волт добавляет строку, а не заменяет")

    def test_silent_on_other_transitions(self) -> None:
        self.write_config(vault=False)
        self.make("TASK-003", status="testing", section="## Testing")
        self.assertEqual([], self.handoff("TASK-003", "completed"))


class HandoffSkillTextTest(unittest.TestCase):
    """Признак момента в скилле перечисляет события, а не один вызов скрипта."""

    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")

    def test_events_are_listed(self) -> None:
        section = self.text[self.text.index("## Когда вызывать"):
                            self.text.index("## Аргументы")]
        for event in ("коммит", "push"):
            self.assertIn(event, section, f"признак не называет: {event}")

    def test_mr_stays_inside_the_optional_block(self) -> None:
        """MR — словарь опциональной возможности, и вне её блока его быть не должно."""
        section = self.text[self.text.index("## Когда вызывать"):
                            self.text.index("## Аргументы")]
        marked = section[section.index("<!-- review_sources -->"):
                         section.index("<!-- /review_sources -->")]
        self.assertIn("MR", marked)
        self.assertEqual(1, section.count("MR"), "MR упомянут и вне блока")

    def test_moment_is_not_reduced_to_the_script_call(self) -> None:
        """Прежний единственный признак («собираешься позвать») больше не один."""
        section = self.text[self.text.index("## Когда вызывать"):
                            self.text.index("## Аргументы")]
        self.assertIn("даже если", section.lower())


if __name__ == "__main__":
    unittest.main()
