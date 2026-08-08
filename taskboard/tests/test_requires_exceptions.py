"""Исключения требований, появившиеся в поставке после снимка настроек (TASK-158).

`requires` материализуются в `tasks/.taskboard.json` в момент, когда человек
добавляет статус, — и дальше живут своей жизнью. Поставка тем временем уходит
вперёд: у требования появляется исключение по типу задачи, проект о нём не
узнаёт, и первая же задача нового типа упирается в отказ, которому нечего
предъявить — «сделать и повторить», хотя делать нечего.

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

from backend.requirements import (apply_preset_exceptions,  # noqa: E402
                                  move_debt, preset_exception_gaps,
                                  unreviewed_task_types)
from backend.statuses import load_pipeline  # noqa: E402
from backend.validator import validate_project  # noqa: E402

PIPELINE = ["backlog", "todo", "development", "testing",
            "ready_for_release", "release_notes", "to_release", "done", "cancelled"]

# Снимок настроек, сделанный до появления типа `review`: исключение перечисляет
# только обсуждение — ровно так выглядит конфиг живого проекта
STALE_REQUIRES = {
    "release_notes": [
        {"id": "release_text", "check": "section_present",
         "name": "Изменение для пользователя", "ask": "тексты релиза написаны",
         "except_types": ["discussion"]},
        {"id": "release_ok", "check": "confirm",
         "ask": "тексты релиза утверждены человеком",
         "except_types": ["discussion"]},
    ],
}

TASK_FILE = """---
id: {task_id}
title: Задача
epic: ~
type: {task_type}
status: {status}
created: 2026-08-08 10:00
---

## Описание

Текст.

## Комментарии

## История коммитов
"""


def _cfg(requires: dict | None = None) -> dict:
    cfg = {"pipeline": PIPELINE,
           "actions": {"create": "backlog", "pick": "todo", "start": "development",
                       "release_draft": "release_notes", "release_lock": "to_release"}}
    if requires is not None:
        cfg["requires"] = requires
    return cfg


class GapsTest(unittest.TestCase):
    """Что именно разошлось: требование, этап и недостающие типы."""

    def _gaps(self, requires: dict | None):
        cfg = _cfg(requires)
        return cfg, preset_exception_gaps(cfg, load_pipeline(cfg).statuses())

    def test_stale_snapshot_names_the_missing_type(self) -> None:
        _cfg_, gaps = self._gaps(STALE_REQUIRES)

        by_id = {g["id"]: g for g in gaps}
        self.assertEqual({"release_text", "release_ok"}, set(by_id))
        self.assertEqual(["review"], by_id["release_text"]["missing"])
        self.assertEqual("release_notes", by_id["release_text"]["status"])

    def test_up_to_date_snapshot_is_silent(self) -> None:
        fresh = {"release_notes": [dict(r, except_types=["discussion", "review"])
                                   for r in STALE_REQUIRES["release_notes"]]}

        self.assertEqual([], self._gaps(fresh)[1])

    def test_no_requires_no_gaps(self) -> None:
        """Проект без объявленных требований живёт на рекомендациях каталога —
        те приходят из поставки и устареть не могут.
        """
        self.assertEqual([], self._gaps(None)[1])

    def test_own_requirement_is_not_compared(self) -> None:
        """Требование, которого в поставке нет, — целиком авторство человека.

        Сверять его не с чем, и молчать про него обязательно: иначе баннер
        предложит «обновить» то, что пользователь придумал сам.
        """
        own = {"testing": [{"id": "мой-гейт", "check": "confirm", "ask": "я сказал"}]}

        self.assertEqual([], self._gaps(own)[1])

    def test_wider_exceptions_than_delivery_are_kept(self) -> None:
        """Человек мог снять требование и с других типов — это его решение."""
        wider = {"release_notes": [dict(r, except_types=["discussion", "review", "bug"])
                                   for r in STALE_REQUIRES["release_notes"]]}

        self.assertEqual([], self._gaps(wider)[1])


class ApplyTest(unittest.TestCase):
    """Кнопка дописывает недостающее и не трогает ничего больше."""

    def test_appends_only_missing_types(self) -> None:
        cfg = _cfg(STALE_REQUIRES)
        updated, applied = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())

        for req in updated["requires"]["release_notes"]:
            with self.subTest(req=req["id"]):
                self.assertEqual(["discussion", "review"], req["except_types"])
        self.assertEqual(2, len(applied))

    def test_keeps_the_rest_of_the_requirement(self) -> None:
        """Формулировку и идентификатор человек правит под себя — приводить их
        к поставке значит затирать его работу.
        """
        mine = {"release_notes": [{"id": "мой-текст-релиза", "check": "section_present",
                                   "name": "Изменение для пользователя",
                                   "ask": "мой текст", "except_types": ["discussion"]}]}
        cfg = _cfg(mine)
        updated, _applied = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())
        req = updated["requires"]["release_notes"][0]

        self.assertEqual("мой-текст-релиза", req["id"])
        self.assertEqual("мой текст", req["ask"])
        self.assertEqual(["discussion", "review"], req["except_types"])

    def test_renamed_section_is_another_requirement(self) -> None:
        """Смысл требования задают предикат и его параметр: сменив имя секции,
        человек объявил **другое** требование — дописывать в него нечего.
        """
        renamed = {"release_notes": [dict(STALE_REQUIRES["release_notes"][0],
                                          name="Мой раздел")]}
        cfg = _cfg(renamed)
        _updated, applied = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())

        self.assertEqual([], applied)

    def test_idempotent(self) -> None:
        cfg = _cfg(STALE_REQUIRES)
        rows = load_pipeline(cfg).statuses()
        once, _ = apply_preset_exceptions(cfg, rows)
        twice, applied = apply_preset_exceptions(once, load_pipeline(once).statuses())

        self.assertEqual(once, twice)
        self.assertEqual([], applied)


class MoveTest(unittest.TestCase):
    """Ради чего всё: задача-ревью перестаёт упираться в чужое требование.

    Требование этапа — условие **выхода** из него, поэтому спрашиваем не долг
    стоящей задачи, а цену следующего шага.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "TASK-001-t.md").write_text(
            TASK_FILE.format(task_id="TASK-001", task_type="review",
                             status="release_notes"), encoding="utf-8")

    def _blocking(self, cfg: dict) -> list[str]:
        rows = load_pipeline(cfg).statuses()
        return [r["id"] for r in
                move_debt(self.tasks, "TASK-001", cfg, "to_release", rows)]

    def test_stale_snapshot_holds_the_review_task(self) -> None:
        self.assertIn("release_text", self._blocking(_cfg(STALE_REQUIRES)))

    def test_applied_exceptions_release_it(self) -> None:
        cfg = _cfg(STALE_REQUIRES)
        updated, _ = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())

        self.assertEqual([], self._blocking(updated))

    def test_other_types_still_held(self) -> None:
        """Исключение снимает требование с ревью, а не со всех подряд."""
        cfg = _cfg(STALE_REQUIRES)
        updated, _ = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())
        (self.tasks / "TASK-001-t.md").write_text(
            TASK_FILE.format(task_id="TASK-001", task_type="feature",
                             status="release_notes"), encoding="utf-8")

        self.assertIn("release_text", self._blocking(updated))


class PersistTest(unittest.TestCase):
    """Дописанное должно пережить сохранение — эндпоинт кладёт только requires."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / ".taskboard.json").write_text(
            json.dumps(_cfg(STALE_REQUIRES), ensure_ascii=False), encoding="utf-8")

    def test_saved_requires_close_the_gap(self) -> None:
        from backend.config import load_project_config, save_project_config

        cfg = load_project_config(self.tasks)
        updated, applied = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())
        self.assertTrue(applied)
        save_project_config(self.tasks, {"requires": updated["requires"]})

        stored = load_project_config(self.tasks)
        self.assertEqual([], preset_exception_gaps(stored,
                                                   load_pipeline(stored).statuses()))

    def test_other_settings_survive(self) -> None:
        """Кнопка трогает исключения, а не конфиг целиком."""
        from backend.config import load_project_config, save_project_config

        save_project_config(self.tasks, {"vault": True})
        cfg = load_project_config(self.tasks)
        updated, _ = apply_preset_exceptions(cfg, load_pipeline(cfg).statuses())
        save_project_config(self.tasks, {"requires": updated["requires"]})

        stored = json.loads((self.tasks / ".taskboard.json").read_text(encoding="utf-8"))
        self.assertTrue(stored["vault"])
        self.assertEqual(PIPELINE, stored["pipeline"])


class UnreviewedTypesTest(unittest.TestCase):
    """Требование, придуманное человеком, поставка дописать не может.

    Держало задачу-ревью именно такое: «История коммитов» на этапе проверки
    объявлена проектом, в каталоге поставки её нет вовсе. Сказать, что появился
    тип, для которого требования стоит пересмотреть, инструмент обязан —
    настраивает человек сам.
    """

    # Требование целиком авторства человека: в каталоге на `testing` только
    # `verified`, и «Историю коммитов» туда никто из поставки не клал
    OWN = {"testing": [{"id": "commits", "check": "section_filled",
                        "name": "История коммитов",
                        "except_types": ["discussion"]}]}

    def _types(self, cfg: dict) -> list[str]:
        return unreviewed_task_types(cfg, load_pipeline(cfg).statuses())

    def test_type_excluded_by_delivery_elsewhere_is_offered(self) -> None:
        """`review` поставка где-то исключает — значит он из тех, ради которых
        требование снимают, и молчать про него нельзя.
        """
        self.assertEqual(["review"], self._types(_cfg(self.OWN)))

    def test_silent_when_already_listed(self) -> None:
        listed = {"testing": [dict(self.OWN["testing"][0],
                                   except_types=["discussion", "review"])]}

        self.assertEqual([], self._types(_cfg(listed)))

    def test_silent_after_dismissal(self) -> None:
        """Человек мог посмотреть и решить, что требование к типу относится.

        Второй раз не спрашиваем: вечная строка в баннере обесценивает соседние.
        """
        seen = dict(_cfg(self.OWN), known_task_types=["review"])

        self.assertEqual([], self._types(seen))

    def test_silent_without_declared_requirements(self) -> None:
        """Проект без своих требований настраивать нечего."""
        self.assertEqual([], self._types(_cfg(None)))

    def test_preset_requirements_are_not_counted(self) -> None:
        """У требований поставки своя кнопка — она знает, что дописать."""
        self.assertEqual([], self._types(_cfg(STALE_REQUIRES)))

    def test_type_named_elsewhere_does_not_silence_the_question(self) -> None:
        """Живой случай, из-за которого всё и затевалось.

        Тип назван в требованиях релизного хвоста и забыт в требовании проверки.
        Считай мы «человек про тип знает» по проекту целиком — вопрос про то
        требование, где его забыли, не прозвучал бы никогда. А упирается задача
        именно в него.
        """
        mixed = {**self.OWN,
                 "release_notes": [dict(r, except_types=["discussion", "review"])
                                   for r in STALE_REQUIRES["release_notes"]]}

        self.assertEqual(["review"], self._types(_cfg(mixed)))

    def test_report_names_the_type(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tasks = Path(tmp.name) / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "board.md").write_text("# Board\n\n## Backlog\n\n_(нет)_\n",
                                        encoding="utf-8")
        cfg = _cfg(self.OWN)
        (tasks / ".taskboard.json").write_text(json.dumps(cfg, ensure_ascii=False),
                                               encoding="utf-8")
        report = validate_project(tasks, cfg)
        entry = next((d for d in report["degraded"]
                      if d["code"] == "requires_types_unreviewed"), None)

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertIn("Код-ревью", entry["message"])


class ReportTest(unittest.TestCase):
    """Расхождение видно в отчёте — молча его оставлять нельзя."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "board.md").write_text("# Board\n\n## Backlog\n\n_(нет)_\n",
                                             encoding="utf-8")
        (self.tasks / ".taskboard.json").write_text(
            json.dumps(_cfg(STALE_REQUIRES), ensure_ascii=False), encoding="utf-8")

    def _codes(self, cfg: dict) -> list[str]:
        report = validate_project(self.tasks, cfg)
        return [d["code"] for d in report["degraded"]]

    def test_stale_exceptions_are_reported(self) -> None:
        self.assertIn("requires_exceptions_stale", self._codes(_cfg(STALE_REQUIRES)))

    def test_message_names_types_and_requirement(self) -> None:
        """Баннер без конкретики нечем закрыть: человек не знает, что нажимает."""
        report = validate_project(self.tasks, _cfg(STALE_REQUIRES))
        message = next(d["message"] for d in report["degraded"]
                       if d["code"] == "requires_exceptions_stale")

        self.assertIn("Код-ревью", message)
        self.assertIn("тексты релиза написаны", message)

    def test_up_to_date_project_is_silent(self) -> None:
        fresh = _cfg({"release_notes": [dict(r, except_types=["discussion", "review"])
                                        for r in STALE_REQUIRES["release_notes"]]})

        self.assertNotIn("requires_exceptions_stale", self._codes(fresh))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
