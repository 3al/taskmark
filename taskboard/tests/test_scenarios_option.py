"""Настройка проекта «сценарии до кода».

Галочка решает, описывает ли агент результат задачи сценариями КОГДА/ТОГДА до
кода. Выключена — скилла `scenarios` в проекте нет, а шаги, которые его зовут,
вырезаны из `start-task`, `fix-task`, `handoff-task` и правил целиком. Как и
остальные возможности, она меняет **эталон** поставки, а не переписывает
развёрнутые файлы за спиной пользователя.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import (DEFAULTS, PROJECT_KEYS, load_project_config,  # noqa: E402
                            save_project_config, stored_project_config)
from backend.scaffold import OPTIONAL_BLOCKS, scaffold_project  # noqa: E402
from backend.validator import validate_project  # noqa: E402

KEY = "scenarios"
MARKER = "<!-- scenarios -->"
HARNESSES = {"claude": True, "opencode": True}
# Скиллы, которые зовут scenarios: в каждом свой шаг
CALLERS = ("start-task", "fix-task", "handoff-task")


class SettingTest(unittest.TestCase):
    """Ключ настройки: дефолт, слой хранения, реестр блоков."""

    def test_off_by_default(self) -> None:
        """Согласование до кода меняет порядок работы — включает его человек."""
        self.assertIs(DEFAULTS[KEY], False)

    def test_belongs_to_project_layer(self) -> None:
        self.assertIn(KEY, PROJECT_KEYS)

    def test_registered_as_optional_block(self) -> None:
        spec = next((s for s in OPTIONAL_BLOCKS if s["key"] == KEY), None)
        assert spec is not None, "настройки нет в реестре опциональных блоков"
        self.assertEqual(spec["marker"], KEY)
        self.assertEqual(spec["skills"], ("scenarios",))


class DeployTest(unittest.TestCase):
    """Что доезжает до проекта при включённой и выключенной настройке."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks_dir = self.root / "tasks"
        self.cfg = {**DEFAULTS, "harnesses": HARNESSES}

    def _deploy(self, enabled: bool) -> None:
        self.cfg[KEY] = enabled
        scaffold_project(self.tasks_dir, self.cfg,
                         {"harnesses": HARNESSES, "skills": True, KEY: enabled})

    def _skill(self, name: str) -> str:
        return (self.root / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def _rules(self) -> str:
        return (self.root / "CLAUDE.md").read_text(encoding="utf-8")

    def test_enabled_deploys_skill_and_command(self) -> None:
        self._deploy(True)
        self.assertIn("КОГДА", self._skill("scenarios"))
        self.assertTrue((self.root / ".opencode" / "commands" / "scenarios.md").is_file())

    def test_enabled_callers_and_rules_call_the_skill(self) -> None:
        self._deploy(True)
        for name in CALLERS:
            with self.subTest(skill=name):
                self.assertIn("`scenarios TASK-NNN", self._skill(name))
        self.assertIn("## Сценарии до кода", self._rules())

    def test_disabled_leaves_no_skill_and_no_calls(self) -> None:
        self._deploy(False)
        self.assertFalse((self.root / ".claude" / "skills" / "scenarios").exists())
        self.assertFalse((self.root / ".opencode" / "commands" / "scenarios.md").exists())
        for name in CALLERS:
            with self.subTest(skill=name):
                text = self._skill(name)
                self.assertNotIn(MARKER, text)
                self.assertNotIn("scenarios", text)
        self.assertNotIn("Сценарии до кода", self._rules())

    def test_steps_are_numbered_without_holes(self) -> None:
        """Полушаг сценариев не сдвигает целые шаги ни в одном положении галочки."""
        for enabled in (False, True):
            self._deploy(enabled)
            for name in CALLERS:
                with self.subTest(enabled=enabled, skill=name):
                    whole = [int(m.group(1)) for m in
                             re.finditer(r"^## Шаг (\d+)\.\s", self._skill(name), re.MULTILINE)]
                    self.assertEqual(whole, sorted(set(whole)))
                    self.assertEqual(whole, list(range(whole[0], whole[0] + len(whole))))

    def test_saved_setting_survives_reload(self) -> None:
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": HARNESSES})
        save_project_config(self.tasks_dir, {KEY: True})
        self.assertIs(stored_project_config(self.tasks_dir)[KEY], True)
        self.assertIs(load_project_config(self.tasks_dir)[KEY], True)


class FreshnessTest(unittest.TestCase):
    """Включение галочки показывает недостающее, а кнопка баннера его доставляет."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks_dir = self.root / "tasks"
        self.cfg = {**DEFAULTS, "harnesses": HARNESSES}
        scaffold_project(self.tasks_dir, self.cfg,
                         {"harnesses": HARNESSES, "skills": True})
        self.start = self.root / ".claude" / "skills" / "start-task" / "SKILL.md"

    def _codes(self) -> list[str]:
        report = validate_project(self.tasks_dir, self.cfg)
        return [d["code"] for d in report["degraded"] + report["warnings"]]

    def test_switch_on_reports_skill_and_outdated_texts(self) -> None:
        self.assertNotIn("outdated_skills", self._codes())
        self.cfg[KEY] = True
        codes = self._codes()
        self.assertIn("outdated_skills", codes)
        self.assertIn("no_skills", codes)
        self.assertIn("no_commands", codes)
        self.assertNotIn(MARKER, self.start.read_text(encoding="utf-8"),
                         "развёрнутый скилл переписан без ведома пользователя")

    def test_banner_buttons_bring_skill_and_blocks(self) -> None:
        self.cfg[KEY] = True
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["skills"]})
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["commands"]})
        self.assertIn(MARKER, self.start.read_text(encoding="utf-8"))
        self.assertTrue((self.root / ".claude" / "skills" / "scenarios" / "SKILL.md").is_file())
        codes = self._codes()
        self.assertNotIn("outdated_skills", codes)
        self.assertNotIn("no_skills", codes)
        self.assertNotIn("no_commands", codes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
