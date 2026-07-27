"""Тесты актуальности агентского окружения (скиллы и команды).

TASK-004: проверка устаревания расширена со скрипта создания задач на
скиллы `.claude/skills/` и команды `.opencode/commands/`.

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
from backend.scaffold import agentic_diff, agentic_stale_details, scaffold_project  # noqa: E402
from backend.validator import validate_project  # noqa: E402


class AgenticFreshnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)

    def _scaffold(self, vault: bool = False) -> dict:
        return scaffold_project(self.tasks_dir, self.cfg, {
            "skills": True, "commands": True,
            "rules_agents": False, "rules_claude": False, "vault": vault,
        })

    def _codes(self) -> list[str]:
        report = validate_project(self.tasks_dir, self.cfg)
        return [d["code"] for d in report["degraded"]]

    def _require_degraded(self, code: str) -> dict:
        """Найти деградацию по коду или провалить тест."""
        report = validate_project(self.tasks_dir, self.cfg)
        found = next((d for d in report["degraded"] if d["code"] == code), None)
        if found is None:
            self.fail(f"деградация {code} не обнаружена: {report['degraded']}")
        return found

    def _skill(self, name: str) -> Path:
        return self.root / ".claude" / "skills" / name / "SKILL.md"

    # --- Свежеразвёрнутое окружение считается актуальным ---

    def test_freshly_scaffolded_skills_not_reported(self) -> None:
        """Без волта скиллы копируются с вырезанными блоками — это не устаревание."""
        self._scaffold(vault=False)
        self.assertNotIn("outdated_skills", self._codes())
        self.assertNotIn("outdated_commands", self._codes())

    def test_freshly_scaffolded_with_vault_not_reported(self) -> None:
        """С волтом скиллы копируются как есть — тоже актуальны."""
        self._scaffold(vault=True)
        self.assertNotIn("outdated_skills", self._codes())

    def test_no_agentic_env_is_silent(self) -> None:
        """Окружение не разворачивали — молчим, а не требуем обновления."""
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": False, "rules_claude": False,
        })
        codes = self._codes()
        self.assertNotIn("outdated_skills", codes)
        self.assertNotIn("outdated_commands", codes)

    def test_missing_final_newline_not_reported(self) -> None:
        """Редактор съел хвостовой перевод строки при пересохранении скилла.

        Строки при этом не меняются: diff пуст, показать пользователю нечего
        и обновлять нечего — но строгое сравнение считало файл устаревшим,
        и баннер висел вечно, не убираясь правкой текста.
        """
        self._scaffold(vault=False)
        skill = self._skill("finalize-task")
        skill.write_bytes(skill.read_text(encoding="utf-8").rstrip("\n").encode("utf-8"))
        self.assertNotIn("outdated_skills", self._codes())

    def test_stale_only_when_diff_is_not_empty(self) -> None:
        """Инвариант: помечен устаревшим ⇔ непустой diff (иначе баннер необъясним)."""
        self._scaffold(vault=False)
        skill = self._skill("finalize-task")
        skill.write_bytes(skill.read_text(encoding="utf-8").rstrip("\n").encode("utf-8"))
        stale = agentic_stale_details(self.root)
        for item in stale:
            diff = agentic_diff(self.root, item["part"], item["name"])
            self.assertTrue(diff["added"] or diff["removed"],
                            f"{item['part']}/{item['name']} помечен устаревшим с пустым diff")

    # --- Устаревание обнаруживается ---

    def test_modified_skill_reported_outdated(self) -> None:
        self._scaffold(vault=False)
        skill = self._skill("start-task")
        skill.write_text(skill.read_text(encoding="utf-8") + "\nстарый хвост\n",
                         encoding="utf-8")
        self.assertIn("start-task", self._require_degraded("outdated_skills")["message"])

    def test_missing_skill_reported_outdated(self) -> None:
        """Скилл, появившийся в шаблонах позже развёртывания, тоже требует обновления."""
        self._scaffold(vault=False)
        self._skill("fix-task").unlink()
        self.assertIn("fix-task", self._require_degraded("outdated_skills")["message"])

    def test_modified_command_reported_outdated(self) -> None:
        self._scaffold(vault=False)
        cmd = self.root / ".opencode" / "commands" / "new-task.md"
        cmd.write_text("сломано", encoding="utf-8")
        self.assertIn("new-task", self._require_degraded("outdated_commands")["message"])

    # --- Точечное восстановление из UI ---

    def test_parts_skills_updates_stale_file(self) -> None:
        self._scaffold(vault=False)
        skill = self._skill("start-task")
        original = skill.read_text(encoding="utf-8")
        skill.write_text("устаревшее содержимое", encoding="utf-8")

        result = scaffold_project(self.tasks_dir, self.cfg, {"parts": ["skills"]})

        self.assertEqual(skill.read_text(encoding="utf-8"), original)
        self.assertTrue(any("start-task" in r for r in result["replaced"]))
        self.assertNotIn("outdated_skills", self._codes())

    def test_parts_skills_preserves_vault_mode(self) -> None:
        """Проект с волтом обновляется волт-версией, а не урезанной."""
        self._scaffold(vault=True)
        skill = self._skill("start-task")
        original = skill.read_text(encoding="utf-8")
        skill.write_text("устаревшее содержимое", encoding="utf-8")

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["skills"]})

        self.assertEqual(skill.read_text(encoding="utf-8"), original)
        self.assertIn("<!-- vault -->", skill.read_text(encoding="utf-8"))

    def test_full_scaffold_updates_stale_skill(self) -> None:
        """Повторное развёртывание тоже подтягивает скиллы до шаблонной версии."""
        self._scaffold(vault=False)
        skill = self._skill("start-task")
        original = skill.read_text(encoding="utf-8")
        skill.write_text("устаревшее содержимое", encoding="utf-8")

        result = self._scaffold(vault=False)

        self.assertEqual(skill.read_text(encoding="utf-8"), original)
        self.assertTrue(any("start-task" in r for r in result["replaced"]))

    def test_full_scaffold_respects_vault_checkbox(self) -> None:
        """Выбор пользователя важнее сложившегося в проекте режима."""
        self._scaffold(vault=False)
        self._scaffold(vault=True)
        self.assertIn("<!-- vault -->",
                      self._skill("start-task").read_text(encoding="utf-8"))

    def test_parts_commands_updates_stale_file(self) -> None:
        self._scaffold(vault=False)
        cmd = self.root / ".opencode" / "commands" / "new-task.md"
        original = cmd.read_text(encoding="utf-8")
        cmd.write_text("сломано", encoding="utf-8")

        result = scaffold_project(self.tasks_dir, self.cfg, {"parts": ["commands"]})

        self.assertEqual(cmd.read_text(encoding="utf-8"), original)
        self.assertTrue(any("new-task" in r for r in result["replaced"]))


if __name__ == "__main__":
    unittest.main()
