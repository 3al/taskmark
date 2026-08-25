"""Скилл, отвечающий за момент маршрута, называется в момент перехода (TASK-189).

Обход скилла прямым вызовом скрипта найден трижды подряд: `start-task` (размер
не оценён), `handoff-task` (хвосты и знание потеряны), `finalize-task` (сверка
коммитов, разблокированные и очередь пропущены). Каждый раз предлагалась своя
заплата — значит не хватало общего правила: у каждого момента есть свой скилл,
а механизм его не называл.

Вызов скрипта — то единственное, что агент на коротком пути всё-таки делает,
поэтому карта печатается там же, где остальные подсказки момента.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_finish_reminders import PLAIN_CFG, RELEASE_CFG, Project  # noqa: E402


class MomentSkillTest(Project):
    """Карта «момент → скилл» выводится из `actions`, а не из имён статусов."""

    def skill(self, task_id: str, status: str) -> str:
        result = self.move(task_id, status)
        self.assertTrue(result.get("ok"), result.get("error"))
        return result.get("moment_skill", "")

    def test_entering_work_names_start_task(self) -> None:
        """Взятие в работу: вход в `actions.start` с более раннего этапа."""
        self.make("TASK-001", status="todo", section="## To Do")
        self.assertIn("start-task", self.skill("TASK-001", "development"))

    def test_leaving_work_forward_names_handoff(self) -> None:
        """Передача на проверку: уход вперёд из рабочего статуса."""
        self.make("TASK-002", status="development", section="## Development")
        self.assertIn("handoff-task", self.skill("TASK-002", "testing"))

    def test_returning_to_work_names_fix_task(self) -> None:
        """Возврат в работу с более позднего этапа ведёт другой скилл."""
        self.make("TASK-003", status="testing", section="## Testing")
        self.assertIn("fix-task", self.skill("TASK-003", "development"))

    def test_other_forward_move_names_finalize(self) -> None:
        """Продвижение по маршруту вне рабочего статуса — финализация."""
        self.make("TASK-004", status="testing", section="## Testing")
        self.assertIn("finalize-task", self.skill("TASK-004", "ready_for_release"))

    def test_offramp_names_finalize(self) -> None:
        """Съезд с маршрута тоже ведёт финализация: она спрашивает причину."""
        self.make("TASK-005", status="testing", section="## Testing")
        result = self.mod.set_status(self.tasks, "TASK-005", "cancelled",
                                     agent="Тест", reason="дублирует TASK-001")
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertIn("finalize-task", result.get("moment_skill", ""))

    def test_release_tail_names_release_skill(self) -> None:
        """Релизный хвост ведёт выпуск, а не финализация: работа там кончилась."""
        self.make("TASK-008", status="ready_for_release",
                  section="## Ready for Release")
        self.assertIn("release", self.skill("TASK-008", "release_notes"))

    def test_work_done_status_is_still_finalize(self) -> None:
        """Статус конца работы — ещё финализация: хвост начинается за ним."""
        self.make("TASK-009", status="testing", section="## Testing")
        self.assertIn("finalize-task", self.skill("TASK-009", "ready_for_release"))

    def test_line_is_single_and_short(self) -> None:
        """Строка одна и не пересказывает шаги скилла."""
        self.make("TASK-006", status="todo", section="## To Do")
        line = self.skill("TASK-006", "development")
        self.assertNotIn("\n", line)
        self.assertLessEqual(len(line), 120, line)

    def test_note_without_transition_is_silent(self) -> None:
        """Комментарий без перевода момента не образует — карта молчит."""
        self.make("TASK-007", status="development", section="## Development")
        result = self.mod.add_note(self.tasks, "TASK-007", "просто мысль", agent="Тест")
        self.assertFalse(result.get("moment_skill"))


class MomentSkillPlainTest(MomentSkillTest):
    """Проект без релизного хвоста: карта считается так же, из `actions`."""

    CFG = PLAIN_CFG

    def test_other_forward_move_names_finalize(self) -> None:
        self.make("TASK-004", status="testing", section="## Testing")
        self.assertIn("finalize-task", self.skill("TASK-004", "completed"))

    def test_release_tail_names_release_skill(self) -> None:
        """Хвоста нет — выпуск задачу не ведёт, и скилла выпуска в карте нет."""
        self.make("TASK-008", status="testing", section="## Testing")
        self.assertIn("finalize-task", self.skill("TASK-008", "completed"))

    def test_work_done_status_is_still_finalize(self) -> None:
        self.make("TASK-009", status="testing", section="## Testing")
        self.assertIn("finalize-task", self.skill("TASK-009", "completed"))


class MomentSkillNamesTest(Project):
    """Имена статусов в карте не зашиты: пайплайн с другими ключами."""

    CFG = {**RELEASE_CFG,
           "pipeline": ["idea", "queue", "coding", "check", "shipped", "dropped"],
           "actions": {"create": "idea", "pick": "queue", "start": "coding",
                       "return": "coding"}}

    def test_custom_pipeline_still_maps(self) -> None:
        self.make("TASK-001", status="queue", section="## Queue")
        result = self.move("TASK-001", "coding")
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertIn("start-task", result.get("moment_skill", ""))


if __name__ == "__main__":
    unittest.main()
