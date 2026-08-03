"""Требования этапа: движок, долг и гейт в скрипте смены статуса (TASK-108).

Механизм не гарантирует, что этап выполнен, — он гарантирует, что этап нельзя
пройти **молча**. Отсюда две реакции на одно требование: рекомендация статуса
печатает напоминание, объявленное в `requires` проекта даёт отказ.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_set_status_script import SCRIPT, load_script, render_board  # noqa: E402

PIPELINE = ["backlog", "todo", "development", "testing",
            "ready_for_release", "release_notes", "done", "cancelled"]

TASK_FILE = """---
id: {task_id}
title: Тестовая задача
epic: ~
type: feature
status: {status}
created: 2026-08-03 10:00
---

## Описание

Тестовая задача.

## Чеклист

- [{box}] Сделать дело

## Заметки агента

## История коммитов

- `abc1234` тестовый коммит
"""


class RequirementsTestCase(unittest.TestCase):
    """Проект с пайплайном до релизных заметок и одной задачей на доске."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.mod = load_script()
        self.cfg = {"pipeline": PIPELINE,
                    "actions": {"create": "backlog", "start": "development",
                                "release_draft": "release_notes"}}
        self._write_cfg()
        (self.tasks / "board.md").write_text(render_board(self.cfg), encoding="utf-8")

    def _write_cfg(self) -> None:
        (self.tasks / ".taskboard.json").write_text(
            json.dumps(self.cfg, ensure_ascii=False), encoding="utf-8")

    def _requires(self, requires: dict) -> None:
        self.cfg["requires"] = requires
        self._write_cfg()

    def _task(self, task_id: str = "TASK-001", status: str = "testing",
              box: str = "x") -> Path:
        path = self.tasks / f"{task_id}-test.md"
        path.write_text(TASK_FILE.format(task_id=task_id, status=status, box=box),
                        encoding="utf-8")
        section = next(s["section"] for s in self.mod.pipeline_of(self.cfg)
                       if s["key"] == status)
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        idx = lines.index(f"## {section}")
        entry = f"- {task_id} · [Тестовая задача]({path.name})"
        if idx + 2 < len(lines) and lines[idx + 2].strip() == "_(нет)_":
            lines[idx + 2] = entry
        else:
            lines.insert(idx + 2, entry)
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")

    def _meta(self, path: Path) -> dict:
        return self.mod._read_meta(path)

    def _notes(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        return [ln for ln in text.splitlines() if ln.startswith("- **")]


class PredicateTest(RequirementsTestCase):
    """Предикаты читают только файл задачи: ни git, ни сети, ни обхода ФС."""

    def _met(self, req: dict, path: Path) -> bool:
        return self.mod.requirement_met(req, path)

    def test_checklist_done(self) -> None:
        req = {"id": "checklist", "check": "checklist_done"}
        self.assertTrue(self._met(req, self._task(box="x")))
        self.assertFalse(self._met(req, self._task("TASK-002", box=" ")))

    def test_section_present_vs_filled(self) -> None:
        """Пустая секция — принятое решение «сказать нечего», а не пробел."""
        path = self._task()
        path.write_text(path.read_text(encoding="utf-8")
                        + "\n## Изменение для пользователя\n\n", encoding="utf-8")
        present = {"id": "notes", "check": "section_present",
                   "name": "Изменение для пользователя"}
        filled = {"id": "notes", "check": "section_filled",
                  "name": "Изменение для пользователя"}
        self.assertTrue(self._met(present, path))
        self.assertFalse(self._met(filled, path))

    def test_section_absent_fails_both(self) -> None:
        path = self._task()
        for check in ("section_present", "section_filled"):
            self.assertFalse(self._met({"id": "x", "check": check,
                                        "name": "Изменение для пользователя"}, path))

    def test_field(self) -> None:
        path = self._task()
        req = {"id": "epic", "check": "field", "name": "epic"}
        self.assertFalse(self._met(req, path), "поле ~ означает «нет»")
        self.mod._set_fields(path, {"epic": "E003-LIFECYCLE"})
        self.assertTrue(self._met(req, path))

    def test_confirm(self) -> None:
        path = self._task()
        req = {"id": "verified", "check": "confirm"}
        self.assertFalse(self._met(req, path))
        self.mod._set_fields(path, {"confirmed": "verified"})
        self.assertTrue(self._met(req, path))

    def test_unknown_check_fails_open(self) -> None:
        """Непонятная декларация не останавливает работу: о ней ругается валидатор."""
        self.assertTrue(self._met({"id": "x", "check": "нет_такого"}, self._task()))


class DebtTest(RequirementsTestCase):
    """Долг вычисляется из позиции задачи и не хранится."""

    def test_debt_counts_only_crossed_stages(self) -> None:
        self._requires({"testing": [{"id": "verified", "check": "confirm"}],
                        "release_notes": [{"id": "text", "check": "section_filled",
                                           "name": "Изменение для пользователя"}]})
        self._task(status="ready_for_release")

        debt = self.mod.task_debt(self.tasks, "TASK-001")

        self.assertEqual([r["id"] for r in debt["debt"]], ["verified"],
                         "долг — только пересечённые этапы, будущие не считаются")

    def test_no_debt_before_the_stage(self) -> None:
        self._requires({"testing": [{"id": "verified", "check": "confirm"}]})
        self._task(status="development")

        self.assertEqual(self.mod.task_debt(self.tasks, "TASK-001")["debt"], [])

    def test_terminal_and_offramp_have_no_debt(self) -> None:
        """Иначе механизм ретроактивно навешивает долг на всю историю проекта."""
        self._requires({"testing": [{"id": "verified", "check": "confirm"}]})
        for status in ("done", "cancelled"):
            self._task(status=status)
            self.assertEqual(self.mod.task_debt(self.tasks, "TASK-001")["debt"], [],
                             f"в статусе {status} долга быть не должно")
            (self.tasks / "TASK-001-test.md").unlink()

    def test_waived_leaves_no_debt(self) -> None:
        self._requires({"testing": [{"id": "verified", "check": "confirm"}]})
        path = self._task(status="ready_for_release")
        self.mod._set_fields(path, {"waived": "verified"})

        self.assertEqual(self.mod.task_debt(self.tasks, "TASK-001")["debt"], [])

    def test_status_outside_pipeline_fails_open(self) -> None:
        self._requires({"нет_такого_статуса": [{"id": "x", "check": "confirm"}]})
        self._task(status="ready_for_release")

        self.assertEqual(self.mod.task_debt(self.tasks, "TASK-001")["debt"], [])


class GateTest(RequirementsTestCase):
    """Гейт стоит только на пути агента и только вперёд."""

    def setUp(self) -> None:
        super().setUp()
        self._requires({"testing": [{"id": "verified", "check": "confirm",
                                     "ask": "проверку подтвердил человек"}]})

    def test_forward_with_unmet_requirement_refused(self) -> None:
        self._task(status="testing")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)
        self.assertIn("проверку подтвердил человек", result.stderr)
        self.assertIn("verified", result.stderr)
        self.assertEqual(self.mod.current_status(self.tasks, "TASK-001"), "testing",
                         "отказ обязан оставить задачу на месте")

    def test_forward_with_met_requirement_passes(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mod.current_status(self.tasks, "TASK-001"),
                         "ready_for_release")

    def test_backward_is_never_checked(self) -> None:
        self._task(status="ready_for_release")

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_offramp_is_never_checked(self) -> None:
        self._task(status="testing")

        result = self._run("TASK-001", "cancelled", "--agent", "Тест",
                           "--reason", "дублирует TASK-002")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_jump_over_stage_still_checked(self) -> None:
        """Прыжок через этап законен, но требования пересечённого он не отменяет."""
        self._task(status="testing")

        result = self._run("TASK-001", "done", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)
        self.assertIn("verified", result.stderr)

    def test_stage_passed_by_hand_is_still_owed(self) -> None:
        """Перенос мышью гейт не проходит — но и не отменяет требование навсегда.

        Задача уже стоит правее `testing` (её перетащили на доске), требование
        того этапа не выполнено. Следующее движение вперёд обязано его увидеть:
        иначе один перенос рукой снимает проверку насовсем и молча.
        """
        self._task(status="ready_for_release")

        result = self._run("TASK-001", "release_notes", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)
        self.assertIn("verified", result.stderr)
        self.assertEqual(self.mod.current_status(self.tasks, "TASK-001"),
                         "ready_for_release")

    def test_debt_and_gate_agree(self) -> None:
        """Показанный долг и то, на чём останавливает гейт, — одно и то же.

        Разойдясь, они дают худшее из двух: бейдж говорит о долге, которого гейт
        не видит, или наоборот — отказ по тому, чего на карточке нет.
        """
        self._task(status="ready_for_release")

        debt = [r["id"] for r in self.mod.task_debt(self.tasks, "TASK-001")["debt"]]
        gate = [r["id"] for r in self.mod.unmet(
            self.mod.move_requirements(self.mod.load_config(self.tasks),
                                       self.mod.pipeline_of(self.mod.load_config(self.tasks)),
                                       "ready_for_release", "release_notes"),
            self.tasks / "TASK-001-test.md")]

        self.assertEqual(debt, gate)

    def test_terminal_target_is_checked_too(self) -> None:
        """Закрытие задачи — тоже движение вперёд, и долг при нём не исчезает."""
        self._task(status="ready_for_release")

        result = self._run("TASK-001", "done", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)
        self.assertIn("verified", result.stderr)

    def test_waive_lets_move_through_and_leaves_trace(self) -> None:
        path = self._task(status="testing")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест",
                           "--waive", "verified", "--reason", "проверял сам заказчик")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mod.current_status(self.tasks, "TASK-001"),
                         "ready_for_release")
        self.assertEqual(self._meta(path).get("waived"), "verified",
                         "идентификатор пишется так же, как в конфиге")
        trace = [n for n in self._notes(path) if "проверял сам заказчик" in n]
        self.assertTrue(trace, "списание обязано оставить строку в заметках агента")
        self.assertIn("Тест", trace[0])

    def test_waive_requires_reason(self) -> None:
        self._task(status="testing")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест",
                           "--waive", "verified")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.mod.current_status(self.tasks, "TASK-001"), "testing")

    def test_confirm_marks_and_notes(self) -> None:
        path = self._task(status="testing")

        result = self._run("TASK-001", "--confirm", "verified", "показал экран, принято",
                           "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._meta(path).get("confirmed"), "verified")
        self.assertTrue([n for n in self._notes(path) if "показал экран" in n])

    def test_confirm_requires_agent(self) -> None:
        """Заметка без модели врёт о том, кто что делал."""
        self._task(status="testing")

        self.assertEqual(self._run("TASK-001", "--confirm", "verified", "принято")
                         .returncode, 1)


class ReturnResetsConfirmationTest(RequirementsTestCase):
    """Возврат назад — признание, что этап не закрыт: его проходят заново."""

    def setUp(self) -> None:
        super().setUp()
        self._requires({"testing": [{"id": "verified", "check": "confirm",
                                     "ask": "проверку подтвердил человек"}],
                        "development": [{"id": "built", "check": "confirm",
                                         "ask": "собрано"}]})

    def test_return_drops_confirmation_of_stage_passed_again(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._meta(path).get("confirmed"), "~",
                         "подтверждение прошлой итерации осталось")
        self.assertIn("снято подтверждение", result.stdout)

    def test_return_keeps_confirmation_of_earlier_stage(self) -> None:
        """Этапы левее цели задача заново не проходит — их подтверждения при ней."""
        path = self._task(status="ready_for_release")
        self.mod._set_fields(path, {"confirmed": "built, verified"})

        self._run("TASK-001", "testing", "--agent", "Тест")

        self.assertEqual(self.mod.parse_req_ids(self._meta(path).get("confirmed")),
                         ["built"])

    def test_return_keeps_waivers(self) -> None:
        """Списание — решение о самом требовании, а не о степени готовности."""
        path = self._task(status="testing")
        self.mod._set_fields(path, {"waived": "verified"})

        self._run("TASK-001", "development", "--agent", "Тест")

        self.assertEqual(self._meta(path).get("waived"), "verified")

    def test_forward_move_resets_nothing(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})

        self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(self._meta(path).get("confirmed"), "verified")

    def test_stage_is_asked_again_after_return(self) -> None:
        """Смысл сброса: второй круг спрашивает заново, а не проезжает молча."""
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})
        self._run("TASK-001", "development", "--agent", "Тест")
        self._run("TASK-001", "--confirm", "built", "собрал", "--agent", "Тест")
        self._run("TASK-001", "testing", "--agent", "Тест")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)
        self.assertIn("verified", result.stderr)


class RecommendationTest(RequirementsTestCase):
    """Проект, ничего не объявивший, ничего не теряет — но и не молчит."""

    def test_project_without_requires_moves_but_reminds(self) -> None:
        self._task(status="testing")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mod.current_status(self.tasks, "TASK-001"),
                         "ready_for_release")
        self.assertIn("[!]", result.stdout)
        self.assertIn("подтвер", result.stdout.lower(),
                      "рекомендация этапа проверки должна прозвучать")

    def test_reminder_and_refusal_use_the_same_words(self) -> None:
        """Разница только в том, состоялся переход или нет."""
        self._task(status="testing")
        reminded = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self._requires({"testing": [dict(self.mod.CATALOG["testing"]["recommends"][0])]})
        self._task("TASK-002", status="testing")
        refused = self._run("TASK-002", "ready_for_release", "--agent", "Тест")

        wording = self.mod.requirement_wording(
            self.mod.CATALOG["testing"]["recommends"][0])
        self.assertIn(wording, reminded.stdout)
        self.assertIn(wording, refused.stderr)

    def test_met_recommendation_is_silent(self) -> None:
        """Шум равен тому, что не сделано."""
        path = self._task(status="testing")
        self.mod._set_fields(
            path, {"confirmed": self.mod.CATALOG["testing"]["recommends"][0]["id"]})

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertNotIn("[!]", result.stdout)

    def test_declared_requirement_does_not_double_with_recommendation(self) -> None:
        """Один и тот же id не должен звучать дважды — отказом и напоминанием."""
        rec = dict(self.mod.CATALOG["testing"]["recommends"][0])
        self._requires({"testing": [rec]})
        path = self._task(status="testing")
        self.mod._set_fields(path, {"waived": rec["id"]})

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(rec["id"], result.stdout)


class ReportingTest(RequirementsTestCase):
    """Долг узнаётся тем же вызовом, которым скилл и так спрашивает маршрут."""

    def test_targets_carry_debt_per_goal(self) -> None:
        self._requires({"testing": [{"id": "verified", "check": "confirm",
                                     "ask": "проверку подтвердил человек"}]})
        self._task(status="testing")

        out = json.loads(self._run("--targets", "TASK-001").stdout)

        self.assertIn("blocked", out)
        self.assertIn("verified", json.dumps(out["blocked"], ensure_ascii=False))
        self.assertNotIn("development", out["blocked"],
                         "назад требования не проверяются")

    def test_debt_flag_reports_task_debt(self) -> None:
        self._requires({"testing": [{"id": "verified", "check": "confirm"}]})
        self._task(status="ready_for_release")

        out = json.loads(self._run("--debt", "TASK-001").stdout)

        self.assertEqual([r["id"] for r in out["debt"]], ["verified"])

    def test_entering_stage_announces_its_requirements(self) -> None:
        self._requires({"testing": [{"id": "verified", "check": "confirm",
                                     "ask": "проверку подтвердил человек"}]})
        self._task(status="development")

        result = self._run("TASK-001", "testing", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("проверку подтвердил человек", result.stdout)


if __name__ == "__main__":
    unittest.main()
