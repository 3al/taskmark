"""Смена сред переносит место скиллов — баннер говорит о переезде (TASK-311).

Claude Code и opencode вместе держат скиллы одной копией в `.claude/skills`.
Сняли Claude Code — действующим становится `.opencode/skills`, и там скиллов
правда нет. Но «не хватает скиллов» человек, только что переключивший среду,
читает как «скиллы пропали», хотя файлы на месте — в прежней папке. Переезд от
нехватки отличает то, что скиллы есть в неактивном расположении.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.validator import validate_project  # noqa: E402

CLAUDE_OPENCODE = {"claude": True, "opencode": True, "codex": False}
OPENCODE_ONLY = {"claude": False, "opencode": True, "codex": False}


class SkillsMoveTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks = self.root / "tasks"

    def cfg(self, harnesses: dict) -> dict:
        return {**DEFAULTS, "harnesses": harnesses}

    def deploy(self, harnesses: dict, parts: list[str] | None = None) -> None:
        options = {"harnesses": harnesses}
        if parts is not None:
            options = {"skills": False, "commands": False, "rules": False, "parts": parts}
        scaffold_project(self.tasks, self.cfg(harnesses), options)

    def skills_banner(self, harnesses: dict) -> list[dict]:
        report = validate_project(self.tasks, self.cfg(harnesses))
        return [d for d in report["degraded"] if d["code"] == "no_skills"]

    def test_switch_says_skills_moved(self) -> None:
        self.deploy(CLAUDE_OPENCODE)

        banner = self.skills_banner(OPENCODE_ONLY)

        self.assertEqual(len(banner), 1, banner)
        message = banner[0]["message"]
        self.assertIn(".opencode/skills", message)
        self.assertIn(".claude/skills", message)
        self.assertNotIn("Не хватает", message)
        self.assertNotIn("не развёрнуты", message)

    def test_empty_project_says_not_deployed(self) -> None:
        # Доска и остальное окружение на месте, а скиллов нет ни в одной папке
        self.deploy(OPENCODE_ONLY)
        shutil.rmtree(self.root / ".opencode" / "skills")

        banner = self.skills_banner(OPENCODE_ONLY)

        self.assertEqual(len(banner), 1, banner)
        self.assertIn("Скиллы не развёрнуты", banner[0]["message"])

    def test_button_deploys_in_new_place(self) -> None:
        self.deploy(CLAUDE_OPENCODE)

        self.deploy(OPENCODE_ONLY, parts=["skills"])

        self.assertTrue((self.root / ".opencode" / "skills" / "start-task" / "SKILL.md").is_file())
        self.assertTrue((self.root / ".claude" / "skills" / "start-task" / "SKILL.md").is_file(),
                        "прежняя копия должна остаться на диске")
        self.assertEqual(self.skills_banner(OPENCODE_ONLY), [])

    def test_partial_move_names_the_rest(self) -> None:
        self.deploy(CLAUDE_OPENCODE)
        old = self.root / ".claude" / "skills"
        new = self.root / ".opencode" / "skills"
        new.mkdir(parents=True)
        shutil.copytree(old / "start-task", new / "start-task")

        banner = self.skills_banner(OPENCODE_ONLY)

        self.assertEqual(len(banner), 1, banner)
        message = banner[0]["message"]
        self.assertIn(".opencode/skills", message)
        self.assertIn("new-task", message)
        self.assertNotIn("start-task", message)
        self.assertNotIn("start-task", banner[0]["names"])


if __name__ == "__main__":
    unittest.main()
