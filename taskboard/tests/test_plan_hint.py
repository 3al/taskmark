"""Оценка объёма зовёт завести план (TASK-199).

Чеклист — план под конкретную работу, и правило «заводи там, где работа
многошаговая» требует суждения. Суждение это уже вынесено: агент назвал размер,
а каталог размеров сам говорит про `L` — «несколько сессий, лучше с планом».
Значит подсказку можно дать в момент оценки, ничего не требуя.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_finish_reminders import PLAIN_CFG, Project  # noqa: E402


class PlanHintTest(Project):
    """Подсказка следует из данных: размер назван, секции нет."""

    CFG = PLAIN_CFG

    def size(self, task_id: str, value: str) -> str:
        result = self.mod.set_size(self.tasks, task_id, value, agent="Тест")
        self.assertTrue(result.get("ok"), result.get("error"))
        return result.get("hint", "")

    def test_large_without_plan_is_invited(self) -> None:
        self.make("TASK-001", status="development", section="## Development",
                  checklist="")

        hint = self.size("TASK-001", "L")

        self.assertIn("Чеклист", hint)

    def test_extra_large_too(self) -> None:
        self.make("TASK-002", status="development", section="## Development",
                  checklist="")

        self.assertIn("Чеклист", self.size("TASK-002", "XL"))

    def test_existing_plan_is_silent(self) -> None:
        """Проверяемое проверяем: секция есть — говорить не о чем."""
        self.make("TASK-003", status="development", section="## Development",
                  checklist="- [ ] Вынести парсер")

        self.assertEqual("", self.size("TASK-003", "L"))

    def test_empty_section_is_not_a_plan(self) -> None:
        """Заголовок без пунктов достался от прежнего шаблона — это не план."""
        self.make("TASK-009", status="development", section="## Development",
                  checklist="")

        self.assertIn("Чеклист", self.size("TASK-009", "L"))

    def test_closed_plan_is_still_a_plan(self) -> None:
        self.make("TASK-010", status="development", section="## Development",
                  checklist="- [x] Уже сделано")

        self.assertEqual("", self.size("TASK-010", "L"))

    def test_small_sizes_are_silent(self) -> None:
        for task_id, value in (("TASK-004", "S"), ("TASK-005", "M")):
            with self.subTest(value):
                self.make(task_id, status="development", section="## Development",
                          checklist="")
                self.assertEqual("", self.size(task_id, value))

    def test_clearing_size_is_silent(self) -> None:
        """Оценку сняли — звать к плану не с чего."""
        self.make("TASK-006", status="development", section="## Development",
                  checklist="")
        self.size("TASK-006", "L")

        self.assertEqual("", self.size("TASK-006", ""))

    def test_cli_prints_it_and_exits_zero(self) -> None:
        self.make("TASK-007", status="development", section="## Development",
                  checklist="")

        done = subprocess.run(
            [sys.executable, str(self.tasks / "set_status.py"),
             "--tasks-dir", str(self.tasks), "TASK-007", "--size", "L",
             "--agent", "Тест"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("Чеклист", done.stdout)

    def test_hint_does_not_block_the_size(self) -> None:
        """Подсказка ничего не требует: размер проставлен в любом случае."""
        path = self.make("TASK-008", status="development", section="## Development",
                         checklist="")

        self.size("TASK-008", "L")

        self.assertIn("size: L", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
