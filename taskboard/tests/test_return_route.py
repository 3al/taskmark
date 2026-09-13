"""Возврат в работу не проходит мимо `fix-task` (TASK-215).

Гейт перехода знал возврат только по одному признаку — задача входит в рабочий
статус с более позднего этапа. Два пути это обходили:

- `--via` принимался любым, и возврат с проверки проходил под `start-task`;
- возврат через очередь: шаг назад в очередь своего скилла не имеет, а следующий
  вход в работу слева выглядел обычным стартом.

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


class CliProject(Project):
    CFG = PLAIN_CFG

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks / "set_status.py"),
             "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def with_history(self, path: Path, *lines: str) -> None:
        """Дописать в «Комментарии» строки прошлых переводов."""
        text = path.read_text(encoding="utf-8")
        text = text.replace("## Комментарии\n",
                            "## Комментарии\n\n" + "\n".join(lines) + "\n", 1)
        path.write_text(text, encoding="utf-8")


class ForeignViaTest(CliProject):
    """`--via` называет не тот скилл маршрута — это не источник, а ошибка."""

    def test_start_task_on_return_is_refused(self) -> None:
        path = self.make("TASK-001", status="testing", section="## Testing")

        done = self.cli("TASK-001", "development", "--agent", "Тест",
                        "--via", "start-task")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("fix-task", done.stderr)
        self.assertIn("status: testing", path.read_text(encoding="utf-8"))

    def test_owner_via_passes(self) -> None:
        self.make("TASK-002", status="testing", section="## Testing")

        done = self.cli("TASK-002", "development", "--agent", "Тест",
                        "--via", "fix-task")

        self.assertEqual(0, done.returncode, done.stderr)

    def test_skill_outside_the_route_map_passes(self) -> None:
        """Скилл, которого в карте нет (ревью), подменой момента не считается."""
        self.make("TASK-003", status="todo", section="## To Do")

        done = self.cli("TASK-003", "development", "--agent", "Тест",
                        "--via", "review-task")

        self.assertEqual(0, done.returncode, done.stderr)

    def test_manual_still_passes(self) -> None:
        self.make("TASK-004", status="testing", section="## Testing")

        done = self.cli("TASK-004", "development", "--agent", "Тест",
                        "--manual", "доработка без скилла")

        self.assertEqual(0, done.returncode, done.stderr)


class ReturnThroughQueueTest(CliProject):
    """Задача, уже побывавшая за рабочим статусом, возвращается, а не стартует."""

    def test_return_via_queue_is_named_fix_task(self) -> None:
        self.make("TASK-001", status="testing", section="## Testing")
        back = self.cli("TASK-001", "todo", "--agent", "Тест")
        self.assertEqual(0, back.returncode, back.stderr)

        done = self.cli("TASK-001", "development", "--agent", "Тест",
                        "--via", "start-task")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("fix-task", done.stderr)

    def test_fix_task_takes_it_from_the_queue(self) -> None:
        self.make("TASK-002", status="testing", section="## Testing")
        self.cli("TASK-002", "backlog", "--agent", "Тест")

        done = self.cli("TASK-002", "development", "--agent", "Тест",
                        "--via", "fix-task")

        self.assertEqual(0, done.returncode, done.stderr)

    def test_board_move_counts_as_history(self) -> None:
        """Перенос мышью пишет ту же строку перевода — возврат виден и по ней."""
        path = self.make("TASK-003", status="todo", section="## To Do")
        self.with_history(path,
                          "- **2026-09-01 10:00** · скрипт (Тест) · To Do → Development",
                          "- **2026-09-01 11:00** · скрипт (Тест) · Development → Testing",
                          "- **2026-09-02 09:00** · доска · Testing → To Do")

        result = self.mod.set_status(self.tasks, "TASK-003", "development",
                                     agent="Тест", via="fix-task")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual("fix-task", result.get("moment_skill"))

    def test_first_start_stays_start_task(self) -> None:
        """История только до работы — это обычный старт."""
        path = self.make("TASK-004", status="todo", section="## To Do")
        self.with_history(path, "- **2026-09-01 10:00** · доска · Backlog → To Do")

        done = self.cli("TASK-004", "development", "--agent", "Тест",
                        "--via", "start-task")

        self.assertEqual(0, done.returncode, done.stderr)

    def test_earlier_start_and_back_is_still_a_start(self) -> None:
        """Побывала в работе, но дальше не ушла — возвращать нечего: работы не сдавали."""
        path = self.make("TASK-005", status="todo", section="## To Do")
        self.with_history(path,
                          "- **2026-09-01 10:00** · скрипт (Тест) · To Do → Development",
                          "- **2026-09-01 11:00** · скрипт (Тест) · Development → To Do")

        done = self.cli("TASK-005", "development", "--agent", "Тест",
                        "--via", "start-task")

        self.assertEqual(0, done.returncode, done.stderr)

    def test_offramp_in_history_is_not_past_work(self) -> None:
        """Съезд стоит в конце списка, но дальше работы по маршруту не уводит."""
        path = self.make("TASK-006", status="todo", section="## To Do")
        self.with_history(path, "- **2026-09-01 10:00** · доска · Backlog → Cancelled")

        done = self.cli("TASK-006", "development", "--agent", "Тест",
                        "--via", "start-task")

        self.assertEqual(0, done.returncode, done.stderr)


class ReturnCustomPipelineTest(CliProject):
    """Имена статусов не зашиты: «дальше работы» считается от `actions.start`."""

    CFG = {**PLAIN_CFG,
           "pipeline": ["idea", "queue", "coding", "check", "shipped", "dropped"],
           "actions": {"create": "idea", "pick": "queue", "start": "coding",
                       "return": "coding"}}

    def test_return_via_queue_on_custom_names(self) -> None:
        self.make("TASK-001", status="check", section="## Check")
        self.cli("TASK-001", "queue", "--agent", "Тест")

        done = self.cli("TASK-001", "coding", "--agent", "Тест", "--via", "start-task")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("fix-task", done.stderr)



if __name__ == "__main__":
    unittest.main()
