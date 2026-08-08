"""Настройка проекта «внешние источники ревью» (TASK-155).

Галочка решает, знает ли агент про внешние форжи. Выключена — блок внешних
источников вырезан из развёрнутого скилла целиком: агент их не предлагает, а не
«знает, но отказывает». Как и волт, она меняет **эталон** скиллов, а не
переписывает развёрнутые файлы за спиной пользователя.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import (DEFAULTS, PROJECT_KEYS, load_project_config,  # noqa: E402
                            save_project_config, stored_project_config)
from backend.scaffold import OPTIONAL_BLOCKS, scaffold_project  # noqa: E402
from backend.validator import validate_project  # noqa: E402

KEY = "review_sources"
MARKER = "<!-- review_sources -->"
HARNESSES = {"claude": True, "opencode": False}


class SettingTest(unittest.TestCase):
    """Ключ настройки: дефолт, слой хранения, реестр блоков."""

    def test_off_by_default(self) -> None:
        """Хождение в чужой форж включает человек, а не поставка."""
        self.assertIs(DEFAULTS[KEY], False)

    def test_belongs_to_project_layer(self) -> None:
        """Настройка про репозиторий, а не про инструмент: живёт в проекте."""
        self.assertIn(KEY, PROJECT_KEYS)

    def test_registered_as_optional_block(self) -> None:
        spec = next((s for s in OPTIONAL_BLOCKS if s["key"] == KEY), None)
        self.assertIsNotNone(spec, "настройки нет в реестре опциональных блоков")
        self.assertEqual(spec["marker"], KEY)
        self.assertEqual(spec["skills"], (), "своих скиллов у настройки нет")


class DeployTest(unittest.TestCase):
    """Что доезжает до проекта при включённой и выключенной настройке."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks_dir = self.root / "tasks"
        self.cfg = {**DEFAULTS, "harnesses": HARNESSES}

    def _deploy(self, enabled: bool) -> str:
        self.cfg[KEY] = enabled
        scaffold_project(self.tasks_dir, self.cfg,
                         {"harnesses": HARNESSES, "skills": True, KEY: enabled})
        skill = self.root / ".claude" / "skills" / "review-task" / "SKILL.md"
        self.assertTrue(skill.is_file(), "скилл ревью не развернулся")
        return skill.read_text(encoding="utf-8")

    def test_disabled_removes_external_sources(self) -> None:
        """Выключено — шагов про внешний источник в скилле нет вовсе.

        Упоминание интерфейса форжа там, где замечания переносит **человек**,
        остаётся: он делает это руками независимо от настройки.
        """
        text = self._deploy(False)
        self.assertNotIn(MARKER, text)
        self.assertNotIn("MCP", text)
        self.assertNotIn("Предмет из внешнего форжа", text)

    def test_enabled_keeps_external_sources(self) -> None:
        text = self._deploy(True)
        self.assertIn(MARKER, text)
        self.assertIn("MCP", text)

    def test_local_git_survives_either_way(self) -> None:
        """Локальный git — база: он остаётся при любом состоянии галочки."""
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.assertIn("git diff", self._deploy(enabled))

    def test_saved_setting_survives_reload(self) -> None:
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": HARNESSES})
        save_project_config(self.tasks_dir, {KEY: True})
        self.assertIs(stored_project_config(self.tasks_dir)[KEY], True)
        self.assertIs(load_project_config(self.tasks_dir)[KEY], True)


class FreshnessTest(unittest.TestCase):
    """Смена настройки меняет эталон, а не переписывает файлы за спиной."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks_dir = self.root / "tasks"
        self.cfg = {**DEFAULTS, "harnesses": HARNESSES}
        scaffold_project(self.tasks_dir, self.cfg,
                         {"harnesses": HARNESSES, "skills": True})
        self.skill = self.root / ".claude" / "skills" / "review-task" / "SKILL.md"

    def _codes(self) -> list[str]:
        return [d["code"] for d in validate_project(self.tasks_dir, self.cfg)["degraded"]]

    def test_switch_on_reports_outdated_skills(self) -> None:
        self.assertNotIn("outdated_skills", self._codes())
        self.cfg[KEY] = True
        self.assertIn("outdated_skills", self._codes())
        self.assertNotIn(MARKER, self.skill.read_text(encoding="utf-8"),
                         "развёрнутый скилл переписан без ведома пользователя")

    def test_update_button_brings_the_block(self) -> None:
        self.cfg[KEY] = True
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["skills"]})
        self.assertIn(MARKER, self.skill.read_text(encoding="utf-8"))
        self.assertNotIn("outdated_skills", self._codes())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
