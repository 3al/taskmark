"""Маршрут вида работы, у которого выпуска не бывает, короче объявленного (TASK-292).

У обсуждения и код-ревью нет ни ревью, ни выпуска: из локального тестирования
такая задача идёт сразу в терминальный статус, сколько бы этапов ни стояло
между ними. Ожидаемый следующий шаг это уже учитывал, а карта моментов — нет:
переход в конец маршрута она отдавала скиллу выпуска, и закрыть обсуждение
штатной финализацией было нельзя.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_finish_reminders import PLAIN_CFG, RELEASE_CFG  # noqa: E402
from tests.test_return_route import CliProject  # noqa: E402

# Маршрут с ревью после тестирования: у обсуждения нет и этого этапа, а имя
# статуса ревью проект объявляет ролью — по ключу его опознавать нельзя
REVIEW_CFG = {**RELEASE_CFG,
              "pipeline": ["backlog", "todo", "development", "testing", "review",
                           "ready_for_release", "release_notes", "to_release",
                           "done", "cancelled"],
              "actions": {**RELEASE_CFG["actions"], "review": "review"}}


class LongRouteProject(CliProject):
    """Проект с полным маршрутом: ревью позади, пул готового и хвост впереди."""

    CFG = RELEASE_CFG

    def targets(self, task_id: str) -> dict:
        done = self.cli("--targets", task_id)
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)


class ShortRouteTest(LongRouteProject):
    """Из тестирования такая задача идёт сразу в конец маршрута."""

    def test_next_step_from_testing_is_terminal(self) -> None:
        self.make("TASK-001", status="testing", section="## Testing",
                  task_type="discussion")

        self.assertEqual("done", self.targets("TASK-001")["next"])

    def test_finalize_closes_discussion(self) -> None:
        path = self.make("TASK-002", status="testing", section="## Testing",
                         task_type="discussion")

        done = self.cli("TASK-002", "done", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(0, done.returncode, done.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: done", text)
        self.assertNotIn("вручную", text)

    def test_finalize_closes_code_review(self) -> None:
        path = self.make("TASK-003", status="testing", section="## Testing",
                         task_type="review")

        done = self.cli("TASK-003", "done", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("status: done", path.read_text(encoding="utf-8"))

    def test_release_stages_are_not_offered(self) -> None:
        """Релизные этапы в рекомендацию не попадают: маршрут туда не ведёт."""
        self.make("TASK-004", status="testing", section="## Testing",
                  task_type="discussion")

        answer = self.targets("TASK-004")

        self.assertNotIn(answer["next"],
                         ("ready_for_release", "release_notes", "to_release"))


class ShortRouteWithReviewStageTest(ShortRouteTest):
    """Тот же короткий маршрут, когда в проекте есть ещё и этап ревью."""

    CFG = REVIEW_CFG


class ShortRouteWithoutReleaseTailTest(CliProject):
    """Маршрут без релизного хвоста ведёт такие задачи так же."""

    CFG = PLAIN_CFG

    def test_discussion_closes_in_terminal(self) -> None:
        path = self.make("TASK-001", status="testing", section="## Testing",
                         task_type="discussion")

        done = self.cli("TASK-001", "completed", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("status: completed", path.read_text(encoding="utf-8"))

    def test_ordinary_task_closes_the_same_way(self) -> None:
        """Хвоста нет — выпуска нет ни у кого, и конец маршрута ведёт финализация."""
        self.make("TASK-002", status="testing", section="## Testing",
                  task_type="feature")

        done = self.cli("TASK-002", "completed", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(0, done.returncode, done.stderr)


class RefusalNamesFinalizeTest(LongRouteProject):
    """Отказ называет того, кто ведёт момент, а чужой источник отклоняет."""

    def test_move_without_source_names_finalize(self) -> None:
        path = self.make("TASK-001", status="testing", section="## Testing",
                         task_type="discussion")

        done = self.cli("TASK-001", "done", "--agent", "Тест")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("finalize-task", done.stderr)
        self.assertNotIn("скилл release", done.stderr)
        self.assertIn("status: testing", path.read_text(encoding="utf-8"))

    def test_release_as_source_is_refused(self) -> None:
        """Назвать источником выпуск — подмена: выпускать по обсуждению нечего."""
        self.make("TASK-002", status="testing", section="## Testing",
                  task_type="discussion")

        done = self.cli("TASK-002", "done", "--agent", "Тест", "--via", "release")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("finalize-task", done.stderr)


class StuckInForeignStageTest(LongRouteProject):
    """Задача, перенесённая мышью в чужой статус, не застревает."""

    def test_discussion_in_release_stage_still_closes(self) -> None:
        path = self.make("TASK-001", status="ready_for_release",
                         section="## Ready for Release", task_type="discussion")

        done = self.cli("TASK-001", "done", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("status: done", path.read_text(encoding="utf-8"))


class OrdinaryWorkUntouchedTest(LongRouteProject):
    """У работы, которую выпускают, всё остаётся как было."""

    def test_feature_to_terminal_still_names_release(self) -> None:
        self.make("TASK-001", status="testing", section="## Testing",
                  task_type="feature")

        done = self.cli("TASK-001", "done", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("release", done.stderr)

    def test_task_without_type_still_names_release(self) -> None:
        """Тип не проставлен — считаем, что выпуск бывает: молчать по недостатку
        данных нельзя."""
        self.make("TASK-002", status="testing", section="## Testing")

        done = self.cli("TASK-002", "done", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("release", done.stderr)

    def test_release_stage_still_named_release(self) -> None:
        self.make("TASK-003", status="ready_for_release",
                  section="## Ready for Release", task_type="feature")

        done = self.cli("TASK-003", "release_notes", "--agent", "Тест",
                        "--via", "finalize-task")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("release", done.stderr)


class FinishRemindersAtRouteEndTest(LongRouteProject):
    """Хвосты работы спрашиваются там, где работа этой задачи кончается."""

    def test_discussion_hears_them_in_terminal(self) -> None:
        self.make("TASK-001", status="testing", section="## Testing",
                  task_type="discussion")
        self.make("TASK-002", title="Вторая", status="todo", section="## To Do",
                  blocked_by="TASK-001")

        result = self.mod.set_status(self.tasks, "TASK-001", "done", agent="Тест")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertTrue(any("TASK-002" in line for line in result["reminders"]),
                        result["reminders"])

    def test_ordinary_task_still_hears_them_at_the_pool(self) -> None:
        self.make("TASK-003", status="testing", section="## Testing",
                  task_type="feature")
        self.make("TASK-004", title="Четвёртая", status="todo", section="## To Do",
                  blocked_by="TASK-003")

        result = self.mod.set_status(self.tasks, "TASK-003", "ready_for_release",
                                     agent="Тест")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertTrue(any("TASK-004" in line for line in result["reminders"]),
                        result["reminders"])


class CatalogDrivenRouteTest(LongRouteProject):
    """Короткий маршрут задаётся каталогом видов работы, а не перечнем имён."""

    def test_new_type_from_catalog_gets_the_short_route(self) -> None:
        self.mod.TASK_TYPES["report"] = {
            "label": "Отчёт", "section": "Отчёты", "letter": "Т", "color": "stone",
            "commits": False, "skip_roles": self.mod.SKIP_ROLES}
        self.addCleanup(self.mod.TASK_TYPES.pop, "report", None)
        self.make("TASK-001", status="testing", section="## Testing",
                  task_type="report")

        result = self.mod.set_status(self.tasks, "TASK-001", "done", agent="Тест")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual("finalize-task", result.get("moment_skill"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
