"""Рекомендация одного смысла звучит в маршруте один раз (TASK-193).

Каталог вешает «проверку подтвердил человек» на `testing` — и это верно в
простых маршрутах, где `testing` и есть проверка автором. Но пресет «Полный»
разводит две разные проверки: `local_testing` (локальная, автором) и `testing`
(на стенде). Совет оставался на стенде, где человек подтвердил проверку этапом
раньше, и говорил не про то событие.

Роль «первая проверка человеком после работы» принадлежит не имени статуса, а
месту в маршруте, поэтому рекомендация достаётся самому раннему этапу, который
её несёт.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.requirements import stage_requirements  # noqa: E402
from backend.statuses import CATALOG, load_pipeline  # noqa: E402
from tests.test_set_status_script import load_script  # noqa: E402

FULL = ["backlog", "todo", "development", "local_testing", "review",
        "to_testing", "testing", "done", "cancelled"]
SIMPLE = ["backlog", "todo", "development", "testing", "done", "cancelled"]

VERIFIED = ("confirm", "")


def kinds(reqs: list[dict]) -> set[str]:
    return {str(r.get("check", "")) for r in reqs}


class CatalogTest(unittest.TestCase):
    """Локальная проверка несёт ту же рекомендацию, что и простое тестирование."""

    def test_local_testing_recommends_confirmation(self) -> None:
        recommends = CATALOG["local_testing"].get("recommends") or []
        self.assertIn("confirm", kinds(recommends))

    def test_testing_still_recommends_it(self) -> None:
        """Каталог остаётся прежним: в простом маршруте совет нужен на `testing`."""
        recommends = CATALOG["testing"].get("recommends") or []
        self.assertIn("confirm", kinds(recommends))


class BackendPlaceTest(unittest.TestCase):
    """Живая рекомендация достаётся раннему этапу, поздний о ней молчит."""

    def stage(self, pipeline_keys: list[str], status: str) -> list[dict]:
        cfg = {"pipeline": pipeline_keys}
        pipeline = load_pipeline(cfg)
        return stage_requirements(cfg, pipeline, status)

    def test_full_route_keeps_it_on_local_testing(self) -> None:
        self.assertIn("confirm", kinds(self.stage(FULL, "local_testing")))

    def test_full_route_drops_it_on_testing(self) -> None:
        self.assertNotIn("confirm", kinds(self.stage(FULL, "testing")))

    def test_simple_route_keeps_it_on_testing(self) -> None:
        """Локальной проверки в маршруте нет — совет остаётся на своём месте."""
        self.assertIn("confirm", kinds(self.stage(SIMPLE, "testing")))

    def test_declared_requirement_is_not_touched(self) -> None:
        """Правило про рекомендации: объявленное проектом остаётся где объявлено."""
        cfg = {"pipeline": FULL,
               "requires": {"testing": [{"id": "stand_ok", "check": "confirm",
                                         "ask": "стенд проверен"}]}}
        pipeline = load_pipeline(cfg)

        reqs = stage_requirements(cfg, pipeline, "testing")

        self.assertEqual(["stand_ok"], [r["id"] for r in reqs])
        self.assertTrue(reqs[0]["mandatory"])


class DifferentMeaningsTest(unittest.TestCase):
    """Повтор — та же рекомендация, а не тот же предикат."""

    def test_release_confirmation_survives(self) -> None:
        """`confirm` носят и подтверждение проверки, и утверждение текстов релиза."""
        keys = ["backlog", "development", "local_testing", "testing",
                "ready_for_release", "release_notes", "to_release", "done"]
        pipeline = load_pipeline({"pipeline": keys})

        notes = next(s for s in pipeline.statuses() if s["key"] == "release_notes")

        self.assertIn("release_ok", [r["id"] for r in notes.get("recommends") or []])


class SettingsViewTest(unittest.TestCase):
    """Экран настроек читает рекомендации из пайплайна — там их тоже быть не должно."""

    def recommends(self, pipeline_keys: list[str], status: str) -> list[dict]:
        pipeline = load_pipeline({"pipeline": pipeline_keys})
        meta = next(s for s in pipeline.statuses() if s["key"] == status)
        return meta.get("recommends") or []

    def test_late_stage_has_no_recommendation_to_show(self) -> None:
        self.assertEqual([], self.recommends(FULL, "testing"))

    def test_early_stage_shows_it(self) -> None:
        self.assertIn("confirm", kinds(self.recommends(FULL, "local_testing")))

    def test_simple_route_unchanged(self) -> None:
        self.assertIn("confirm", kinds(self.recommends(SIMPLE, "testing")))


class ScriptMirrorTest(unittest.TestCase):
    """Автономный скрипт считает так же — иначе доска и агент разойдутся."""

    def setUp(self) -> None:
        self.mod = load_script()

    def stage(self, pipeline_keys: list[str], status: str) -> list[dict]:
        cfg = {"pipeline": pipeline_keys}
        pipeline = self.mod.pipeline_of(cfg)
        return self.mod.stage_requirements(cfg, pipeline, status)

    def test_full_route_keeps_it_on_local_testing(self) -> None:
        self.assertIn("confirm", kinds(self.stage(FULL, "local_testing")))

    def test_full_route_drops_it_on_testing(self) -> None:
        self.assertNotIn("confirm", kinds(self.stage(FULL, "testing")))

    def test_simple_route_keeps_it_on_testing(self) -> None:
        self.assertIn("confirm", kinds(self.stage(SIMPLE, "testing")))


if __name__ == "__main__":
    unittest.main()
