"""Зеркало движка требований: бэкенд и скрипт судят одинаково (TASK-110).

Скрипт автономен — он работает без сервера и импортировать бэкенд не может,
поэтому правила живут в двух местах. Расхождение зеркал даёт худшее из
возможного: доска показывает один долг, а агент упирается в другой.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_set_status_script import load_script  # noqa: E402

PIPELINE = ["backlog", "todo", "development", "testing",
            "ready_for_release", "release_notes", "done", "cancelled"]

REQUIRES = {
    "testing": [{"id": "verified", "check": "confirm",
                 "ask": "проверку подтвердил человек"}],
    "ready_for_release": [{"id": "checklist", "check": "checklist_done"},
                          {"id": "commits", "check": "section_filled",
                           "name": "История коммитов"}],
    "release_notes": [{"id": "release_text", "check": "section_present",
                       "name": "Изменение для пользователя"},
                      {"id": "epic", "check": "field", "name": "epic"}],
}

TASK = """---
id: {task_id}
title: Задача
epic: {epic}
status: {status}
created: 2026-08-03 10:00
confirmed: {confirmed}
waived: {waived}
---

## Описание

Текст.

## Чеклист

- [{box}] Пункт

## Заметки агента

## История коммитов
{commits}
"""


class MirrorTest(unittest.TestCase):
    """Один и тот же набор задач — один и тот же вердикт у обеих реализаций."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.cfg = {"pipeline": PIPELINE, "requires": REQUIRES,
                    "actions": {"create": "backlog", "start": "development",
                                "release_draft": "release_notes"}}
        (self.tasks / ".taskboard.json").write_text(
            json.dumps(self.cfg, ensure_ascii=False), encoding="utf-8")
        self.script = load_script()

    def _task(self, task_id: str, **kw) -> Path:
        path = self.tasks / f"{task_id}-t.md"
        path.write_text(TASK.format(
            task_id=task_id,
            status=kw.get("status", "testing"),
            epic=kw.get("epic", "~"),
            confirmed=kw.get("confirmed", "~"),
            waived=kw.get("waived", "~"),
            box=kw.get("box", " "),
            commits=kw.get("commits", ""),
        ), encoding="utf-8")
        return path

    def _cases(self) -> list[dict]:
        """Набор, покрывающий каждый предикат и каждый ограничитель."""
        return [
            {"task_id": "TASK-001", "status": "testing"},
            {"task_id": "TASK-002", "status": "ready_for_release"},
            {"task_id": "TASK-003", "status": "ready_for_release",
             "confirmed": "verified"},
            {"task_id": "TASK-004", "status": "release_notes", "box": "x",
             "commits": "\n- `abc1234` коммит", "confirmed": "verified"},
            {"task_id": "TASK-005", "status": "release_notes", "box": "x",
             "commits": "\n- `abc1234` коммит", "waived": "verified, checklist"},
            {"task_id": "TASK-006", "status": "done", "comment": "терминальный"},
            {"task_id": "TASK-007", "status": "cancelled", "comment": "съезд"},
            {"task_id": "TASK-008", "status": "backlog", "comment": "ничего не прошёл"},
            {"task_id": "TASK-009", "status": "release_notes", "epic": "E003-LIFECYCLE",
             "box": "x", "commits": "\n- `abc1234` коммит", "confirmed": "verified"},
        ]

    def _script_debt(self, task_id: str) -> list[str]:
        return [r["id"] for r in
                self.script.task_debt(self.tasks, task_id, self.cfg)["debt"]]

    def _backend_debt(self, task_id: str) -> list[str]:
        from backend.requirements import task_debt

        return [r["id"] for r in task_debt(self.tasks, task_id, self.cfg)["debt"]]

    def test_debt_verdicts_match(self) -> None:
        for case in self._cases():
            case = {k: v for k, v in case.items() if k != "comment"}
            self._task(**case)
        for case in self._cases():
            tid = case["task_id"]
            with self.subTest(task=tid, status=case["status"]):
                self.assertEqual(self._backend_debt(tid), self._script_debt(tid))

    def test_debt_is_not_always_empty(self) -> None:
        """Страховка от «оба зеркала одинаково ничего не считают»."""
        self._task("TASK-001", status="ready_for_release")

        self.assertTrue(self._backend_debt("TASK-001"))
        self.assertEqual(self._backend_debt("TASK-001"), self._script_debt("TASK-001"))

    def test_move_verdicts_match(self) -> None:
        """Что потребуется при переносе — тоже одно и то же."""
        from backend.requirements import move_requirements, unmet

        path = self._task("TASK-001", status="testing")
        pipeline = self.script.pipeline_of(self.cfg)
        for target in ("ready_for_release", "release_notes", "done", "cancelled",
                       "development"):
            with self.subTest(target=target):
                mine = [r["id"] for r in unmet(
                    move_requirements(self.cfg, pipeline, "testing", target), path)]
                theirs = [r["id"] for r in self.script.unmet(
                    self.script.move_requirements(self.cfg, pipeline, "testing", target),
                    path)]
                self.assertEqual(mine, theirs)

    def test_type_scope_matches_in_both_mirrors(self) -> None:
        """Требование, исключённое по типу задачи, обе реализации считают
        неприменимым — иначе карточка покажет долг, которого агент не видит.

        Ключ `except_types` появился ради задач-обсуждений: «История коммитов»
        у них пуста не по недосмотру, коммитов там не будет никогда.
        """
        from backend.requirements import unmet as backend_unmet

        req = {"id": "commits", "check": "section_filled",
               "name": "История коммитов", "except_types": ["discussion"]}
        cases = [("TASK-020", "discussion", []), ("TASK-021", "feature", ["commits"])]

        for task_id, task_type, expected in cases:
            path = self._task(task_id, status="ready_for_release")
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("epic: ~", f"epic: ~\ntype: {task_type}"),
                            encoding="utf-8")
            with self.subTest(type=task_type):
                mine = [r["id"] for r in backend_unmet([dict(req)], path)]
                theirs = [r["id"] for r in self.script.unmet([dict(req)], path)]
                self.assertEqual(mine, theirs)
                self.assertEqual(mine, expected)

    def test_own_id_silences_recommendation_in_both_mirrors(self) -> None:
        """Вытеснение рекомендации считается по смыслу предиката, а не по `id`,
        и обе реализации обязаны считать его одинаково (TASK-135).

        Проект объявил своё требование на этапе, у которого каталог рекомендует
        `confirm`: рекомендация замолчала, но каталожный `id` остался бы в долге,
        разойдись зеркала.
        """
        from backend.requirements import stage_requirements as backend_reqs

        cfg = dict(self.cfg, requires={"testing": [
            {"id": "qa_ok", "check": "confirm", "ask": "проверено на контуре"}]})
        pipeline = self.script.pipeline_of(cfg)

        mine = [r["id"] for r in backend_reqs(cfg, pipeline, "testing")]
        theirs = [r["id"] for r in self.script.stage_requirements(cfg, pipeline,
                                                                 "testing")]

        self.assertEqual(mine, theirs)
        self.assertEqual(mine, ["qa_ok"])

    def test_section_name_case_does_not_split_mirrors(self) -> None:
        """Имя секции пишет человек — регистр не должен менять вердикт.

        Скрипт сравнивал заголовок без учёта регистра, бэкенд искал точное
        совпадение: «история коммитов» в конфиге давала агенту «выполнено», а
        доске — долг.
        """
        from backend.requirements import requirement_met as backend_met

        path = self._task("TASK-010", status="ready_for_release",
                          commits="\n- `abc1234` коммит")
        for name in ("История коммитов", "история коммитов", "  ИСТОРИЯ КОММИТОВ "):
            req = {"id": "commits", "check": "section_filled", "name": name}
            with self.subTest(name=name):
                self.assertEqual(backend_met(req, path),
                                 self.script.requirement_met(req, path))

    def test_checklist_section_is_found_by_both(self) -> None:
        """Чеклист ищется тем же способом — иначе разъедется и он."""
        from backend.requirements import requirement_met as backend_met

        path = self._task("TASK-011", status="ready_for_release", box=" ")
        req = {"id": "checklist", "check": "checklist_done"}

        self.assertEqual(backend_met(req, path), self.script.requirement_met(req, path))
        self.assertFalse(backend_met(req, path), "незакрытый пункт не увиден")

    def test_terminal_and_offramp_have_no_debt_in_both(self) -> None:
        for task_id, status in (("TASK-006", "done"), ("TASK-007", "cancelled")):
            self._task(task_id, status=status)
            with self.subTest(status=status):
                self.assertEqual(self._backend_debt(task_id), [])
                self.assertEqual(self._script_debt(task_id), [])


if __name__ == "__main__":
    unittest.main()
