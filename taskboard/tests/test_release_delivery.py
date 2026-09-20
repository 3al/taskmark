"""Релизная часть поставки едет туда, где выпуск версий есть (TASK-184).

Правила собираются под маршрут проекта, а скиллы до сих пор ехали одинаковые:
в проект без релизных этапов уезжал скилл выпуска целиком и релизные абзацы
в соседних скиллах — включая описание, которое среда показывает агенту ещё до
запуска. Возможность вычисляется из маршрута, а не спрашивается галочкой:
вторая галочка разошлась бы с пайплайном.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS, PROJECT_KEYS  # noqa: E402
from backend.scaffold import (OPTIONAL_BLOCKS, agentic_stale_details,  # noqa: E402
                              remove_element, scaffold_project)
from backend.statuses import PRESETS  # noqa: E402
from backend.validator import validate_project  # noqa: E402
from tests.test_optional_leaks import FEATURE_WORDS  # noqa: E402

KEY = "release"
MARKER = "<!-- release -->"
SKILL = "release"
HARNESSES = {"claude": True, "opencode": True}


def preset(name: str) -> dict:
    """Готовый маршрут по имени — чтобы тест не держал вторую копию пресета."""
    spec = next(p for p in PRESETS if p["name"] == name)
    return {"pipeline": list(spec["pipeline"]), "actions": dict(spec["actions"])}


# Маршрут, где подготовка текстов релиза есть, а этапа утверждённого состава нет:
# одной этой роли достаточно, чтобы проект выпускал версии
DRAFT_ONLY = {"pipeline": ["backlog", "todo", "development", "testing",
                           "release_notes", "done", "cancelled"],
              "actions": {"create": "backlog", "pick": "todo",
                          "start": "development", "return": "development",
                          "release_draft": "release_notes"}}


class Project(unittest.TestCase):
    """Развёрнутый проект с выбранным маршрутом."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks_dir = self.root / "tasks"

    def deploy(self, route: dict | None = None) -> dict:
        """Развернуть окружение с этим маршрутом; вернуть конфиг проекта."""
        cfg = {**DEFAULTS, **(route or {}), "harnesses": HARNESSES}
        scaffold_project(self.tasks_dir, cfg,
                         {"harnesses": HARNESSES, "skills": True, "commands": True})
        return cfg

    def skills_dir(self) -> Path:
        return self.root / ".claude" / "skills"

    def skill_text(self, name: str) -> str:
        return (self.skills_dir() / name / "SKILL.md").read_text(encoding="utf-8")

    def rules_text(self) -> str:
        return (self.root / "CLAUDE.md").read_text(encoding="utf-8")

    def release_mentions(self, text: str) -> list[str]:
        return [line.strip() for line in text.splitlines()
                if FEATURE_WORDS[KEY].search(line)]


class WithoutReleaseTest(Project):
    """Маршрут без выпуска: инструмента выпуска в проекте нет."""

    ROUTE = preset("Простой")

    def test_release_skill_is_not_deployed(self) -> None:
        self.deploy(self.ROUTE)

        self.assertFalse((self.skills_dir() / SKILL).exists(),
                         "скилл выпуска уехал в проект, который версий не выпускает")

    def test_release_command_is_not_deployed(self) -> None:
        self.deploy(self.ROUTE)

        self.assertFalse((self.root / ".opencode" / "commands" / f"{SKILL}.md").exists(),
                         "обёртка opencode ведёт к скиллу, которого нет")

    def test_skills_say_nothing_about_release(self) -> None:
        self.deploy(self.ROUTE)

        for name in ("finalize-task", "handoff-task", "fix-task",
                     "read-handoff", "write-handoff"):
            with self.subTest(skill=name):
                self.assertEqual(self.release_mentions(self.skill_text(name)), [])

    def test_skill_description_says_nothing_about_release(self) -> None:
        """Описание среда показывает в списке скиллов — до всякого запуска."""
        self.deploy(self.ROUTE)
        text = self.skill_text("finalize-task")
        header = text.split("---")[1] if text.startswith("---") else text

        self.assertEqual(self.release_mentions(header), [])

    def test_rules_say_nothing_about_release(self) -> None:
        self.deploy(self.ROUTE)

        self.assertEqual(self.release_mentions(self.rules_text()), [])

    def test_no_markers_left_in_texts(self) -> None:
        self.deploy(self.ROUTE)

        self.assertNotIn(MARKER, self.skill_text("finalize-task"))


class DefaultProjectTest(WithoutReleaseTest):
    """Новый проект, где жизненный цикл не трогали, — тоже без выпуска."""

    ROUTE = None


class DeployStageOnlyTest(WithoutReleaseTest):
    """Выкатка на стенд выпуском версии не является."""

    ROUTE = preset("Полный")


class WithReleaseTest(Project):
    """Маршрут с подготовкой текстов релиза: поставка прежняя."""

    ROUTE = preset("С релизами")

    def test_release_skill_is_deployed(self) -> None:
        self.deploy(self.ROUTE)

        self.assertTrue((self.skills_dir() / SKILL / "SKILL.md").is_file())

    def test_release_command_is_deployed(self) -> None:
        self.deploy(self.ROUTE)

        self.assertTrue((self.root / ".opencode" / "commands" / f"{SKILL}.md").is_file())

    def test_release_paragraphs_stay(self) -> None:
        self.deploy(self.ROUTE)

        self.assertNotEqual(self.release_mentions(self.skill_text("finalize-task")), [])
        self.assertNotEqual(self.release_mentions(self.skill_text("handoff-task")), [])


class DraftOnlyTest(WithReleaseTest):
    """Подготовка текстов есть, этапа утверждённого состава нет — всё равно выпуск."""

    ROUTE = DRAFT_ONLY


class RouteChangeTest(Project):
    """Смена маршрута меняет эталон, а файлы трогает только кнопка."""

    def codes(self, cfg: dict) -> list[str]:
        return [d["code"] for d in validate_project(self.tasks_dir, cfg)["degraded"]]

    def extra(self, cfg: dict) -> set[tuple[str, str]]:
        return {(i["part"], i["name"])
                for i in agentic_stale_details(self.root, cfg) if i["state"] == "extra"}

    def test_adding_release_stages_marks_environment_outdated(self) -> None:
        self.deploy(preset("Простой"))
        cfg = {**DEFAULTS, **preset("С релизами"), "harnesses": HARNESSES}

        self.assertIn("outdated_skills", self.codes(cfg))
        self.assertNotIn(MARKER, self.skill_text("finalize-task"),
                         "развёрнутый скилл переписан без ведома пользователя")

    def test_update_button_brings_release_back(self) -> None:
        self.deploy(preset("Простой"))
        cfg = {**DEFAULTS, **preset("С релизами"), "harnesses": HARNESSES}

        scaffold_project(self.tasks_dir, cfg, {"parts": ["skills", "commands"]})

        self.assertIn(MARKER, self.skill_text("finalize-task"))
        self.assertTrue((self.skills_dir() / SKILL / "SKILL.md").is_file())

    def test_removing_release_stages_marks_skill_extra(self) -> None:
        self.deploy(preset("С релизами"))
        cfg = {**DEFAULTS, **preset("Простой"), "harnesses": HARNESSES}

        self.assertIn("extra_skills", self.codes(cfg))
        self.assertIn(("skills", SKILL), self.extra(cfg))
        self.assertIn(("commands", SKILL), self.extra(cfg))

    def test_removing_release_stages_deletes_nothing_by_itself(self) -> None:
        self.deploy(preset("С релизами"))
        cfg = {**DEFAULTS, **preset("Простой"), "harnesses": HARNESSES}

        scaffold_project(self.tasks_dir, cfg, {"parts": ["skills", "commands", "rules"]})

        self.assertTrue((self.skills_dir() / SKILL / "SKILL.md").is_file(),
                        "файл снесли без ведома пользователя")

    def test_extra_release_skill_is_removed_by_the_button(self) -> None:
        self.deploy(preset("С релизами"))
        cfg = {**DEFAULTS, **preset("Простой"), "harnesses": HARNESSES}

        for part in ("skills", "commands"):
            result = remove_element(self.root, part, SKILL, cfg)
            self.assertTrue(result["ok"], result.get("error"))

        self.assertFalse((self.skills_dir() / SKILL).exists())
        self.assertEqual(self.extra(cfg), set())


class RegistryTest(unittest.TestCase):
    """Возможность вычисляется из маршрута, а не спрашивается галочкой."""

    def test_registered_as_optional_block(self) -> None:
        specs = [s for s in OPTIONAL_BLOCKS if s["key"] == KEY]
        self.assertTrue(specs, "релизной возможности нет в реестре блоков")
        self.assertEqual(specs[0]["marker"], KEY)
        self.assertIn(SKILL, specs[0]["skills"])

    def test_has_no_config_switch(self) -> None:
        """Вторая галочка разошлась бы с маршрутом, который уже всё говорит."""
        self.assertNotIn(KEY, PROJECT_KEYS)
        self.assertNotIn(KEY, DEFAULTS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
