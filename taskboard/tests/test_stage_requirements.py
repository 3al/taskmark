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
type: {task_type}
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
              box: str = "x", task_type: str = "feature") -> Path:
        path = self.tasks / f"{task_id}-test.md"
        path.write_text(TASK_FILE.format(task_id=task_id, status=status, box=box,
                                         task_type=task_type),
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

    def test_refused_move_writes_no_note(self) -> None:
        """Отказ не оставляет заметку о переводе, которого не было.

        Заметку пишут тем же вызовом, что двигают задачу («переведена в …»), и
        при отказе она оставалась в файле: история задачи начинала врать о
        событии, которое не случилось.
        """
        path = self._task(status="testing")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест",
                           "--note", "переведена в «Готово к выпуску»")

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("переведена", path.read_text(encoding="utf-8"))

    def test_successful_move_writes_the_note(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест",
                           "--note", "переведена в «Готово к выпуску»")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("переведена", path.read_text(encoding="utf-8"))

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

    def test_refusal_hint_names_the_task(self) -> None:
        """Подсказку из отказа копируют как есть — значит в ней обязан быть TASK-NNN.

        Трение обкатки (TASK-112): без идентификатора буквальный запуск даёт
        «нужен TASK-NNN для --confirm», то есть отказ учит команде, которая не
        работает. Имя интерпретатора человек только что набрал сам, а вот номер
        задачи подставить за него может только скрипт — он его знает.
        """
        self._task(status="testing")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertIn("TASK-001 --confirm verified", result.stderr)
        self.assertIn("TASK-001 --waive", result.stderr)

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

    def test_repeated_confirm_writes_no_second_note(self) -> None:
        """Событие было одно — вторая одинаковая строка засоряет хронологию."""
        path = self._task(status="testing")
        self._run("TASK-001", "--confirm", "verified", "проверил", "--agent", "Тест")

        result = self._run("TASK-001", "--confirm", "verified", "проверил", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("уже отмечено", result.stdout)
        self.assertEqual(len([n for n in self._notes(path) if "проверил" in n]), 1)

    def test_repeated_waive_writes_no_second_note(self) -> None:
        path = self._task(status="testing")
        for _ in range(2):
            self._run("TASK-001", "--waive", "verified", "--reason", "проверял заказчик",
                      "--agent", "Тест")

        self.assertEqual(len([n for n in self._notes(path) if "списано" in n]), 1)

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

    def test_return_leaves_a_note(self) -> None:
        """Снятие — событие хронологии: иначе одно и то же подтверждали дважды
        без видимой причины между двумя строками."""
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})

        self._run("TASK-001", "development", "--agent", "Тест")

        trace = [n for n in self._notes(path) if "снято подтверждение" in n]
        self.assertTrue(trace, "возврат не оставил следа в заметках агента")
        self.assertIn("проверку подтвердил человек", trace[0],
                      "в строке должна стоять формулировка, а не служебный id")
        self.assertNotIn("verified", trace[0])
        self.assertIn("Тест", trace[0])

    def test_return_note_is_signed_without_agent(self) -> None:
        """Без --agent событие подписывает сам скрипт: выдумывать модель нельзя."""
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})

        self._run("TASK-001", "development")

        trace = [n for n in self._notes(path) if "снято подтверждение" in n]
        self.assertTrue(trace)
        self.assertIn("set_status.py", trace[0])

    def test_return_without_confirmations_is_silent(self) -> None:
        """Снимать нечего — и писать не о чем: возврат сам по себе виден на доске."""
        path = self._task(status="testing")

        self._run("TASK-001", "development", "--agent", "Тест")

        self.assertEqual([n for n in self._notes(path) if "снято подтверждение" in n], [])

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


class UndoFactsTest(RequirementsTestCase):
    """Факты снимаются командой, а не правкой frontmatter руками (TASK-132).

    Механизм умел ставить `confirmed` и `waived`, но не снимать: ошибочное
    списание приходилось убирать вручную — ровно тем способом, от которого он и
    уводит, потому что причина остаётся в комментариях, а поле уезжает отдельно.
    """

    def setUp(self) -> None:
        super().setUp()
        self._requires({"testing": [{"id": "verified", "check": "confirm",
                                     "ask": "проверку подтвердил человек"},
                                    {"id": "commits", "check": "section_filled",
                                     "name": "История коммитов"}]})

    def test_unwaive_removes_and_leaves_trace(self) -> None:
        path = self._task(status="testing")
        self._run("TASK-001", "--waive", "commits", "--reason", "коммитов не будет",
                  "--agent", "Тест")

        result = self._run("TASK-001", "--unwaive", "commits", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._meta(path).get("waived"), "~")
        trace = [n for n in self._notes(path) if "снято списание" in n]
        self.assertTrue(trace, "снятие списания не оставило следа")
        self.assertIn("История коммитов", trace[0],
                      "в строке должна быть формулировка, а не служебный id")

    def test_unwaive_brings_the_debt_back(self) -> None:
        """Списание снято — требование снова в долге, если оно не выполнено."""
        path = self._task(status="ready_for_release")
        # Требование должно быть именно невыполненным: с заполненной секцией
        # оно истинно и без списания, и тест ничего бы не проверял
        path.write_text(path.read_text(encoding="utf-8")
                        .replace("- `abc1234` тестовый коммит", ""), encoding="utf-8")
        self._run("TASK-001", "--waive", "commits", "--reason", "проверка", "--agent", "Тест")
        self._run("TASK-001", "--unwaive", "commits", "--agent", "Тест")

        debt = [r["id"] for r in self.mod.task_debt(self.tasks, "TASK-001")["debt"]]

        self.assertIn("commits", debt)

    def test_unconfirm_removes_confirmation(self) -> None:
        path = self._task(status="testing")
        self._run("TASK-001", "--confirm", "verified", "проверил", "--agent", "Тест")

        self._run("TASK-001", "--unconfirm", "verified", "--agent", "Тест")

        self.assertEqual(self._meta(path).get("confirmed"), "~")
        self.assertTrue([n for n in self._notes(path) if "снято подтверждение" in n])

    def test_without_id_removes_all(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"waived": "commits, verified"})

        self._run("TASK-001", "--unwaive", "--agent", "Тест")

        self.assertEqual(self._meta(path).get("waived"), "~")

    def test_nothing_to_remove_is_not_an_error(self) -> None:
        """Снимать нечего — это не ошибка и не повод писать в хронологию."""
        path = self._task(status="testing")

        result = self._run("TASK-001", "--unwaive", "commits", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([n for n in self._notes(path) if "снято" in n], [])

    def test_keeps_other_ids(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"waived": "commits, verified"})

        self._run("TASK-001", "--unwaive", "commits", "--agent", "Тест")

        self.assertEqual(self.mod.parse_req_ids(self._meta(path).get("waived")),
                         ["verified"])

    def test_requires_agent(self) -> None:
        """Строку в комментариях подписывает тот, кто снял: без модели она врёт."""
        path = self._task(status="testing")
        self.mod._set_fields(path, {"waived": "commits"})

        self.assertEqual(self._run("TASK-001", "--unwaive", "commits").returncode, 1)


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

    def test_reminder_hint_names_the_task_too(self) -> None:
        """Напоминание — та же подсказка, значит и его команда должна работать."""
        self._task(status="testing")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertIn("TASK-001 --confirm verified", result.stdout)

    def test_reminder_names_the_stage_not_the_current_move(self) -> None:
        """Незакрытым остаётся этап, а не тот, с которого задачу двигают.

        Трение обкатки (TASK-112): «уходя из „X“» врёт, когда долг пришёл с
        этапа, пройденного раньше. Задача уже стоит в «Готово к выпуску», не
        подтвердив проверку, — уходит она из «Готово к выпуску», а незакрытым
        остался «Testing».
        """
        self._task(status="ready_for_release")

        result = self._run("TASK-001", "release_notes", "--agent", "Тест")

        self.assertIn("«Testing»", result.stdout)
        self.assertIn("остался незакрытым", result.stdout)
        self.assertNotIn("уходя из", result.stdout)

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

    def test_own_id_with_the_same_predicate_also_silences(self) -> None:
        """Идентификатор человек придумывает сам — угадывать чужой он не обязан.

        Трение обкатки (TASK-112, Т9): вытеснение шло по совпадению `id`, поэтому
        своё требование с тем же смыслом получало вдогонку рекомендацию каталога —
        два ритуала там, где всё настроено верно. Совпадать должен предикат.
        """
        self._requires({"testing": [{"id": "qa_ok", "check": "confirm",
                                     "ask": "проверено на контуре"}]})
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "qa_ok"})

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("[!]", result.stdout,
                         "рекомендация того же смысла звучать не должна")

    def test_recommendation_of_another_predicate_still_speaks(self) -> None:
        """Взяв под контроль одно требование этапа, человек не теряет подсказку
        о другом: у релизных заметок их две — секция и утверждение текстов."""
        rec = self.mod.CATALOG["release_notes"]["recommends"]
        confirm_rec = next(r for r in rec if r["check"] == "confirm")
        section_rec = next(r for r in rec if r["check"] == "section_present")
        self._requires({"release_notes": [{"id": "texts_ok", "check": "confirm",
                                           "ask": "тексты утвердил главред"}]})
        path = self._task(status="release_notes")
        self.mod._set_fields(path, {"confirmed": "texts_ok"})

        result = self._run("TASK-001", "done", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(section_rec["id"], result.stdout,
                      "рекомендация другого предиката обязана прозвучать")
        self.assertNotIn(confirm_rec["id"], result.stdout)

    def test_section_parameter_matches_regardless_of_case(self) -> None:
        """Имя секции пишет человек, и на регистре зеркала уже расходились."""
        stage = {"key": "x", "label": "X",
                 "recommends": [{"id": "commits", "check": "section_filled",
                                 "name": "История коммитов"}]}
        cfg = {"requires": {"x": [{"id": "mine", "check": "section_filled",
                                   "name": "история КОММИТОВ"}]}}

        reqs = self.mod.stage_requirements(cfg, [stage], "x")

        self.assertEqual([r["id"] for r in reqs], ["mine"])


class TypeScopeTest(RequirementsTestCase):
    """Требование может не относиться к типу задачи (TASK-134).

    Живой случай: «История коммитов» бессмысленна для задачи-обсуждения — там
    коммитов не будет никогда. Это не долг, а норма типа, а требование,
    невыполнимое в принципе, учит списывать и всё остальное.

    Хранится **исключение**, а не белый список: при появлении нового типа в
    поставке белый список молча перестал бы его покрывать (тихая потеря), а
    исключение молча включит — это заметно.
    """

    COMMITS = {"id": "commits", "check": "section_filled",
               "name": "История коммитов", "except_types": ["discussion"]}

    def setUp(self) -> None:
        super().setUp()
        self._requires({"testing": [dict(self.COMMITS)]})

    def _no_commits(self, task_id: str = "TASK-001", **kw) -> Path:
        """Задача без истории коммитов: требование по ней ложно."""
        path = self._task(task_id, **kw)
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("- `abc1234` тестовый коммит", ""),
                        encoding="utf-8")
        return path

    def test_excluded_type_is_not_stopped(self) -> None:
        self._no_commits(status="testing", task_type="discussion")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_other_types_are_stopped(self) -> None:
        self._no_commits(status="testing", task_type="feature")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)
        self.assertIn("commits", result.stderr)

    def test_requirement_without_key_applies_to_every_type(self) -> None:
        self._requires({"testing": [{k: v for k, v in self.COMMITS.items()
                                     if k != "except_types"}]})
        self._no_commits(status="testing", task_type="discussion")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 1, "без ключа требование ко всем типам")

    def test_task_without_type_keeps_requirements(self) -> None:
        """Задачи, заведённые до появления поля, требований не теряют."""
        path = self._no_commits(status="testing")
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("type: feature\n", ""), encoding="utf-8")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)

    def test_type_case_does_not_matter(self) -> None:
        """Значение пишет человек — регистр не должен менять вердикт."""
        self._requires({"testing": [dict(self.COMMITS, except_types=["Discussion"])]})
        self._no_commits(status="testing", task_type="discussion")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_type_in_list_changes_nothing(self) -> None:
        """Опечатка в исключении не гасит требование молча."""
        self._requires({"testing": [dict(self.COMMITS, except_types=["нет-такого"])]})
        self._no_commits(status="testing", task_type="discussion")

        result = self._run("TASK-001", "ready_for_release", "--agent", "Тест")

        self.assertEqual(result.returncode, 1)

    def test_debt_hides_requirement_of_another_type(self) -> None:
        self._no_commits(status="ready_for_release", task_type="discussion")

        out = json.loads(self._run("TASK-001", "--debt").stdout)

        self.assertEqual([], out["debt"])

    def test_announcement_skips_requirement_of_another_type(self) -> None:
        """Анонс на входе в этап не должен обещать того, чего не спросят."""
        self._no_commits(status="development", task_type="discussion")

        result = self._run("TASK-001", "testing", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("commits", result.stdout)


class UnknownIdTest(RequirementsTestCase):
    """Запись, которую движок не понимает, не должна молчать (TASK-133).

    `confirmed` и `waived` — плоские списки идентификаторов, и всё незнакомое
    движок игнорирует: требование остаётся невыполненным, хотя поле выглядит
    заполненным. Такая запись переживает и возврат назад — снимается только
    распознанное, — поэтому сказать о ней обязан скрипт.
    """

    def setUp(self) -> None:
        super().setUp()
        self._requires({"testing": [{"id": "verified", "check": "confirm",
                                     "ask": "проверку подтвердил человек"}]})

    def test_unknown_confirmed_id_is_reported(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "testing/verified"})

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("testing/verified", result.stdout)

    def test_unknown_waived_id_is_reported(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"waived": "нет-такого"})

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertIn("нет-такого", result.stdout)

    def test_known_id_is_silent(self) -> None:
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "verified"})

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertNotIn("не опознан", result.stdout.lower())

    def test_empty_fields_are_silent(self) -> None:
        self._task(status="testing")

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertNotIn("не опознан", result.stdout.lower())

    def test_id_of_another_stage_is_known(self) -> None:
        """Требование другого этапа — не «неопознанное»: задача могла закрыть его
        раньше, и жаловаться на честную запись нельзя."""
        self._requires({"testing": [{"id": "verified", "check": "confirm"}],
                        "ready_for_release": [{"id": "commits",
                                               "check": "section_filled",
                                               "name": "История коммитов"}]})
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "commits"})

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertNotIn("не опознан", result.stdout.lower())

    def test_recommended_id_is_known(self) -> None:
        """Рекомендация каталога тоже настоящее требование: её подтверждают, и
        запись об этом — не мусор."""
        rec = self.mod.CATALOG["release_notes"]["recommends"][0]
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": rec["id"]})

        result = self._run("TASK-001", "development", "--agent", "Тест")

        self.assertNotIn("не опознан", result.stdout.lower())

    def test_nothing_is_deleted(self) -> None:
        """Механизм только показывает: запись может быть опечаткой в конфиге, и
        тогда стирается факт, а не мусор."""
        path = self._task(status="testing")
        self.mod._set_fields(path, {"confirmed": "testing/verified"})

        self._run("TASK-001", "development", "--agent", "Тест")

        self.assertIn("testing/verified", path.read_text(encoding="utf-8"))


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

    def test_debt_accepts_the_task_positionally(self) -> None:
        """Долг спрашивают той же формой, что и всё остальное над задачей.

        Трение обкатки (TASK-112): остальные операции берут `TASK-NNN`
        позиционно, а долг требовал его значением флага — и `TASK-001 --debt`
        отвечал простынёй `usage`, из которой не видно, что номер надо
        переставить. Старая форма при этом остаётся рабочей.
        """
        self._requires({"testing": [{"id": "verified", "check": "confirm"}]})
        self._task(status="ready_for_release")

        out = json.loads(self._run("TASK-001", "--debt").stdout)

        self.assertEqual([r["id"] for r in out["debt"]], ["verified"])

    def test_targets_accept_the_task_positionally(self) -> None:
        """Второй флаг той же группы обязан понимать обе формы — иначе рука
        снова ошибётся, только уже на соседней команде."""
        self._task(status="testing")

        out = json.loads(self._run("TASK-001", "--targets").stdout)

        self.assertEqual(out["task"], "TASK-001")

    def test_debt_without_any_task_is_an_error(self) -> None:
        result = self._run("--debt")

        self.assertEqual(result.returncode, 2)
        self.assertIn("TASK-NNN", result.stderr)

    def test_entering_stage_announces_its_requirements(self) -> None:
        self._requires({"testing": [{"id": "verified", "check": "confirm",
                                     "ask": "проверку подтвердил человек"}]})
        self._task(status="development")

        result = self._run("TASK-001", "testing", "--agent", "Тест")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("проверку подтвердил человек", result.stdout)


if __name__ == "__main__":
    unittest.main()
