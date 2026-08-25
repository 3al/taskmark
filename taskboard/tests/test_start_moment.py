"""Взятие задачи в работу: правило поставки и напоминание об оценке (TASK-187).

Пункт правил «При начале работы» описывал механику — «переведи статус скриптом»
— и читался как разрешение брать задачу напрямую: скилл в нём не упоминался.
Обход же теряет не только оценку объёма, но и все прочие шаги старта.

Страховка стоит там, где агент на коротком пути всё-таки окажется: вход в
рабочий статус. Оценка проверяемая — о пустом поле говорится конкретно.

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

TASK_WITH_SIZE = """---
id: {task_id}
title: {title}
epic: ~
type: feature
size: {size}
status: {status}
created: 2026-08-01 10:00
---

## Описание

Тестовая задача.

## Комментарии

## История коммитов
"""


class StartRuleTest(unittest.TestCase):
    """Правило старта называет скилл, а не только перевод статуса."""

    def rules(self, cfg: dict | None = None) -> str:
        return render_rules(cfg or {**DEFAULTS})

    def test_start_rule_names_the_skill(self) -> None:
        """Скилл старта назван в правилах поставки."""
        self.assertIn("start-task", self.rules())

    def test_start_rule_no_longer_reads_as_permission(self) -> None:
        """Прямой перевод статуса не подаётся как способ начать работу."""
        rules = self.rules()
        start = rules.index("## Правила работы с задачей")
        bullet = rules[start:rules.index("\n- Во время работы", start)]
        self.assertIn("start-task", bullet)

    def test_start_status_still_substituted(self) -> None:
        """Имя рабочего статуса по-прежнему подставляется из конфига."""
        cfg = {**DEFAULTS, "pipeline": ["idea", "coding", "shipped"],
               "actions": {"create": "idea", "start": "coding"}}
        self.assertIn("coding", self.rules(cfg))


class SizeReminderTest(Project):
    """Вход в рабочий статус говорит о неоценённом размере — и только тогда."""

    CFG = PLAIN_CFG

    def make_sized(self, task_id: str, size: str, status: str, section: str) -> Path:
        name = f"{task_id}-proba.md"
        path = self.tasks / name
        path.write_text(TASK_WITH_SIZE.format(task_id=task_id, title="Проба",
                                              size=size, status=status),
                        encoding="utf-8")
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        lines.insert(lines.index(section) + 1, f"\n- {task_id} · [Проба]({name})")
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def entry(self, task_id: str, status: str) -> list[str]:
        result = self.move(task_id, status)
        self.assertTrue(result.get("ok"), result.get("error"))
        return result.get("entry_reminders", [])

    def test_empty_size_is_named_on_entry(self) -> None:
        self.make_sized("TASK-001", "~", "todo", "## To Do")
        said = " ".join(self.entry("TASK-001", "development"))
        self.assertIn("размер", said.lower())
        self.assertIn("--sizes", said)

    def test_filled_size_is_silent(self) -> None:
        self.make_sized("TASK-002", "M", "todo", "## To Do")
        self.assertEqual([], self.entry("TASK-002", "development"))

    def test_other_status_is_silent(self) -> None:
        """Оценку спрашивают на входе в работу, а не на каждом переходе."""
        self.make_sized("TASK-003", "~", "development", "## Development")
        self.assertEqual([], self.entry("TASK-003", "testing"))

    def test_reminder_does_not_block(self) -> None:
        self.make_sized("TASK-004", "~", "todo", "## To Do")
        result = self.move("TASK-004", "development")
        self.assertTrue(result.get("ok"))
        self.assertEqual("development", result.get("status"))

    def test_return_to_work_also_asks(self) -> None:
        """Вернулись в работу без оценки — спрашивают снова: поле всё ещё пусто."""
        self.make_sized("TASK-005", "~", "testing", "## Testing")
        self.assertTrue(self.entry("TASK-005", "development"))


if __name__ == "__main__":
    unittest.main()
