"""Тесты подробностей по устаревшему агентскому окружению: список, diff, точечное обновление.

TASK-010: баннер ведёт в модалку, где видно что именно разошлось и что
изменится после обновления.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import (  # noqa: E402
    agentic_diff,
    agentic_stale_details,
    refresh_agentic,
    scaffold_project,
)


class AgenticDetailsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks = self.root / "tasks"
        self.cfg = dict(DEFAULTS)
        scaffold_project(self.tasks, self.cfg, {
            "skills": True, "commands": True,
            "rules_agents": False, "rules_claude": False, "vault": False,
        })

    def _skill(self, name: str) -> Path:
        return self.root / ".claude" / "skills" / name / "SKILL.md"

    # --- Детальный список ---

    def test_fresh_environment_has_no_details(self) -> None:
        self.assertEqual(agentic_stale_details(self.root), [])

    def test_modified_skill_has_state_modified(self) -> None:
        skill = self._skill("start-task")
        skill.write_text(skill.read_text(encoding="utf-8") + "\nхвост\n", encoding="utf-8")
        items = agentic_stale_details(self.root)
        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["part"], "skills")
        self.assertEqual(items[0]["name"], "start-task")
        self.assertEqual(items[0]["state"], "modified")

    def test_missing_skill_has_state_missing(self) -> None:
        self._skill("fix-task").unlink()
        items = agentic_stale_details(self.root)
        self.assertEqual([i["state"] for i in items], ["missing"])
        self.assertEqual(items[0]["name"], "fix-task")

    def test_details_cover_commands_too(self) -> None:
        cmd = self.root / ".opencode" / "commands" / "new-task.md"
        cmd.write_text("сломано", encoding="utf-8")
        items = agentic_stale_details(self.root)
        self.assertEqual([(i["part"], i["name"]) for i in items], [("commands", "new-task")])

    # --- Diff ---

    def test_diff_shows_added_and_removed_lines(self) -> None:
        skill = self._skill("start-task")
        original = skill.read_text(encoding="utf-8").splitlines()
        broken = ["ЛИШНЯЯ СТРОКА"] + original[1:]
        skill.write_text("\n".join(broken) + "\n", encoding="utf-8")

        result = agentic_diff(self.root, "skills", "start-task")

        self.assertTrue(result["ok"], result)
        self.assertIn("@@", result["diff"])
        # Направление: развёрнутое -> шаблон, «+» = появится после обновления
        self.assertIn("-ЛИШНЯЯ СТРОКА", result["diff"])
        self.assertIn("+" + original[0], result["diff"])
        self.assertEqual(result["state"], "modified")

    def test_diff_counts_changes(self) -> None:
        skill = self._skill("start-task")
        # Ровно одна лишняя строка: файл шаблона уже оканчивается переводом строки
        skill.write_text(skill.read_text(encoding="utf-8") + "хвост\n", encoding="utf-8")
        result = agentic_diff(self.root, "skills", "start-task")
        self.assertEqual(result["removed"], 1, result["diff"])
        self.assertEqual(result["added"], 0, result["diff"])

    def test_diff_for_missing_file_is_all_additions(self) -> None:
        self._skill("fix-task").unlink()
        result = agentic_diff(self.root, "skills", "fix-task")
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "missing")
        self.assertEqual(result["removed"], 0)
        self.assertGreater(result["added"], 0)

    def test_diff_ignores_vault_blocks_for_project_without_vault(self) -> None:
        """Проект без волта: вырезанные блоки — не изменения, diff должен быть пуст."""
        result = agentic_diff(self.root, "skills", "start-task")
        self.assertTrue(result["ok"])
        self.assertEqual(result["diff"], "")
        self.assertEqual((result["added"], result["removed"]), (0, 0))

    def test_diff_unknown_name_is_error(self) -> None:
        result = agentic_diff(self.root, "skills", "нет-такого-скилла")
        self.assertFalse(result["ok"])

    # --- Точечное обновление ---

    def test_refresh_only_named_skill(self) -> None:
        first, second = self._skill("start-task"), self._skill("fix-task")
        first.write_text("устарел", encoding="utf-8")
        second.write_text("тоже устарел", encoding="utf-8")

        created, replaced, skipped = refresh_agentic(self.root, "skills", names=["start-task"])

        self.assertEqual([Path(p).parts[-2] for p in replaced], ["start-task"])
        self.assertEqual(second.read_text(encoding="utf-8"), "тоже устарел",
                         "точечное обновление задело соседний скилл")
        self.assertNotIn("fix-task", " ".join(created + skipped))

    def test_scaffold_passes_names_through(self) -> None:
        first, second = self._skill("start-task"), self._skill("fix-task")
        first.write_text("устарел", encoding="utf-8")
        second.write_text("тоже устарел", encoding="utf-8")

        result = scaffold_project(self.tasks, self.cfg,
                                  {"parts": ["skills"], "names": ["start-task"]})

        self.assertTrue(any("start-task" in r for r in result["replaced"]))
        self.assertEqual(second.read_text(encoding="utf-8"), "тоже устарел")


if __name__ == "__main__":
    unittest.main()
