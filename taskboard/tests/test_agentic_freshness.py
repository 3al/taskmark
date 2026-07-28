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
        # Выбор сред — часть конфига проекта: без него части, зависящие от
        # среды, не проверяются (UI сначала спрашивает выбор)
        self.cfg["harnesses"] = {"claude": True, "opencode": True}

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

    def test_missing_skill_reported_as_gap_not_outdated(self) -> None:
        """Скилла нет — это нехватка, а не устаревание.

        Скилл, появившийся в шаблонах позже развёртывания, тоже сюда попадает:
        чинится он кнопкой «Развернуть» (код no_skills), а не походом в diff, —
        и называть несуществующий файл устаревшим значит врать пользователю.
        """
        self._scaffold(vault=False)
        self._skill("fix-task").unlink()
        message = self._require_degraded("no_skills")["message"]
        self.assertIn("Не хватает скиллов", message)
        self.assertIn("fix-task", message)

    def test_modified_command_reported_outdated(self) -> None:
        self._scaffold(vault=False)
        cmd = self.root / ".opencode" / "commands" / "new-task.md"
        cmd.write_text("сломано", encoding="utf-8")
        self.assertIn("new-task", self._require_degraded("outdated_commands")["message"])

    # --- Точечное восстановление из UI ---

    # --- Секция правил в агентских файлах ---

    def _rules_file(self, body: str) -> Path:
        path = self.root / "AGENTS.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def test_stale_rules_section_reported(self) -> None:
        """Правила описывают жизненный цикл — их устаревание надо замечать.

        Иначе после правки шаблона (или смены пайплайна) агент продолжает
        работать по инструкциям, которых уже нет в эталоне, и сказать некому.
        """
        self._scaffold(vault=False)
        self._rules_file("# 1. Проект\n\nТекст.\n\n"
                         "# 2. TASK MANAGEMENT\n\nстарые правила\n")
        self.assertIn("outdated_rules", self._codes())

    def test_fresh_rules_section_silent(self) -> None:
        self._scaffold(vault=False)
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": True, "rules_claude": False})
        self.assertNotIn("outdated_rules", self._codes())

    def test_missing_rules_section_reported(self) -> None:
        """Без секции правил проект полурабочий — это чинится кнопкой, а не молчанием."""
        self._scaffold(vault=False)
        self._rules_file("# Проект\n\nСвои правила, без нашей секции.\n")
        self.assertIn("no_rules", self._codes())

    def test_missing_in_one_file_reported(self) -> None:
        """Секция есть в CLAUDE.md, но в AGENTS.md её снесли.

        Молчать нельзя: opencode читает именно AGENTS.md и процесса не увидит.
        """
        self._scaffold(vault=False)
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": True, "rules_claude": True})
        self._rules_file("# Проект\n\nСекцию правил отсюда удалили.\n")

        report = validate_project(self.tasks_dir, self.cfg)
        found = next((d for d in report["degraded"] if d["code"] == "no_rules"), None)
        self.assertIsNotNone(found, f"молчим о файле без правил: {report['degraded']}")
        self.assertIn("AGENTS.md", found["message"])
        self.assertNotIn("CLAUDE.md", found["message"], "в CLAUDE.md секция на месте")

    def test_missing_in_one_file_fixed_by_button(self) -> None:
        self._scaffold(vault=False)
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": True, "rules_claude": True})
        path = self._rules_file("# Проект\n\nСекцию правил отсюда удалили.\n")

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["rules"]})

        self.assertIn("TASK MANAGEMENT", path.read_text(encoding="utf-8"))
        self.assertNotIn("no_rules", self._codes())

    def test_missing_rules_section_deployed_pointwise(self) -> None:
        """Кнопка на баннере дописывает секцию в существующий агентский файл."""
        self._scaffold(vault=False)
        path = self._rules_file("# Проект\n\nСвои правила.\n")

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["rules"]})

        content = path.read_text(encoding="utf-8")
        self.assertIn("Свои правила.", content, "чужой текст файла не должен пострадать")
        self.assertIn("TASK MANAGEMENT", content)
        self.assertNotIn("no_rules", self._codes())

    def test_rules_deployed_from_scratch(self) -> None:
        """Агентских файлов нет вовсе — заводим оба, проект должен стать рабочим."""
        self._scaffold(vault=False)
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = self.root / name
            if path.exists():
                path.unlink()

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["rules"]})

        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertTrue((self.root / name).is_file(), f"{name} не создан")
        self.assertNotIn("no_rules", self._codes())

    def test_rules_section_updated_pointwise(self) -> None:
        """Кнопка обновления приводит секцию к эталону, не трогая остальной файл."""
        self._scaffold(vault=False)
        path = self._rules_file("# 1. Проект\n\nМой текст.\n\n"
                                "# 2. TASK MANAGEMENT\n\nстарые правила\n")

        result = scaffold_project(self.tasks_dir, self.cfg,
                                  {"parts": ["rules"], "names": ["AGENTS.md"]})

        content = path.read_text(encoding="utf-8")
        self.assertIn("# 1. Проект", content)
        self.assertIn("Мой текст.", content)
        self.assertIn("Жизненный цикл статуса", content)
        self.assertNotIn("старые правила", content)
        self.assertIn("AGENTS.md", result["replaced"])
        self.assertNotIn("outdated_rules", self._codes())

    def test_rules_follow_project_pipeline(self) -> None:
        """Эталон правил считается под пайплайн проекта, а не под дефолтный."""
        self.cfg["pipeline"] = ["backlog", "queued", "development", "completed"]
        self._scaffold(vault=False)
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": True, "rules_claude": False})

        content = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("backlog → queued → development → completed", content)
        self.assertNotIn("outdated_rules", self._codes())

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

    def test_full_scaffold_keeps_customized_skill(self) -> None:
        """Развёртывание не стирает правленный скилл — там инструкции пользователя.

        Раньше повторное «Развернуть» молча приводило файл к шаблону: одна
        кнопка уносила всю локальную настройку. Теперь расхождение только
        показывается, а переписывается по кнопке рядом с diff.
        """
        self._scaffold(vault=False)
        skill = self._skill("start-task")
        skill.write_text("мои инструкции", encoding="utf-8")

        result = self._scaffold(vault=False)

        self.assertEqual(skill.read_text(encoding="utf-8"), "мои инструкции")
        self.assertTrue(any("start-task" in d for d in result["diverged"]))
        self.assertFalse(any("start-task" in r for r in result["replaced"]))

    def test_pointwise_update_still_overwrites(self) -> None:
        """Осознанное обновление (кнопка у diff) по-прежнему приводит к шаблону."""
        self._scaffold(vault=False)
        skill = self._skill("start-task")
        original = skill.read_text(encoding="utf-8")
        skill.write_text("мои инструкции", encoding="utf-8")

        result = scaffold_project(self.tasks_dir, self.cfg,
                                  {"parts": ["skills"], "names": ["start-task"]})

        self.assertEqual(skill.read_text(encoding="utf-8"), original)
        self.assertTrue(any("start-task" in r for r in result["replaced"]))

    def test_vault_checkbox_changes_expected_version(self) -> None:
        """Галка волта меняет эталон, а не переписывает файлы за спиной.

        Иначе выбор пользователя либо ничего не делает, либо молча правит
        скиллы — оба варианта плохи; расхождение показывается в баннере.
        """
        self._scaffold(vault=False)
        self.cfg["vault"] = True

        self.assertIn("start-task", self._require_degraded("outdated_skills")["message"])
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["skills"]})
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
