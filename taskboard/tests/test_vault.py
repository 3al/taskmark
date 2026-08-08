"""Тесты Knowledge Vault в поставке (TASK-038).

Галочка волта раньше лишь оставляла волт-блоки в скиллах: они ссылались на
skill `write-vault`, которого мы не поставляли, и на папку `vault/`, которую
никто не создавал. Здесь проверяется, что галочка разворачивает связный набор —
скилл, структуру и правила, — а проект без волта не получает ни файла, ни
упоминания.

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

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import (SKILLS_TEMPLATES, environment_issues,  # noqa: E402
                              feature_skills, render_rules, scaffold_project)
from backend.validator import validate_project  # noqa: E402

BOTH = {"claude": True, "opencode": True}


class VaultTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "мой-проект"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)

    # --- Помощники ---

    def _deploy(self, vault: bool) -> dict:
        self.cfg["harnesses"] = BOTH
        self.cfg["vault"] = vault
        return scaffold_project(self.tasks_dir, self.cfg,
                                {"harnesses": BOTH, "vault": vault})

    def _codes(self) -> list[str]:
        return [d["code"] for d in validate_project(self.tasks_dir, self.cfg)["degraded"]]

    def _skills_dir(self) -> Path:
        return self.root / ".claude" / "skills"

    # --- Скилл write-vault ---

    def test_write_vault_is_shipped(self) -> None:
        """Скилл есть в шаблонах: без него волт-блоки ссылаются в пустоту."""
        self.assertTrue((SKILLS_TEMPLATES / "write-vault" / "SKILL.md").is_file())
        self.assertIn("write-vault", feature_skills("vault"))

    def test_vault_skill_deployed_with_vault(self) -> None:
        self._deploy(vault=True)
        self.assertTrue((self._skills_dir() / "write-vault" / "SKILL.md").is_file())

    def test_vault_skill_absent_without_vault(self) -> None:
        """Проект без волта не получает лишних файлов и упоминаний."""
        self._deploy(vault=False)
        self.assertFalse((self._skills_dir() / "write-vault").exists())
        finalize = (self._skills_dir() / "finalize-task" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("write-vault", finalize)

    def test_vault_skill_not_reported_missing_without_vault(self) -> None:
        """Невыбранный волт — решение пользователя, а не пробел поставки."""
        self._deploy(vault=False)
        self.assertEqual(self._codes(), [])

    # --- Структура vault/SYS ---

    def test_vault_structure_deployed(self) -> None:
        self._deploy(vault=True)
        vault = self.root / "vault"
        for rel in ("SYS/structure.md", "SYS/taxonomy.md", "SYS/README.md",
                    "SYS/templates/business-note.md", "SYS/templates/code-note.md"):
            self.assertTrue((vault / rel).is_file(), f"не развёрнут {rel}")

    def test_vault_ignored_by_git(self) -> None:
        """Знания — локальная память разработчика, чужой репозиторий не засоряют."""
        self._deploy(vault=True)
        self.assertIn("*", (self.root / "vault" / ".gitignore").read_text(encoding="utf-8"))

    def test_vault_readme_names_project(self) -> None:
        self._deploy(vault=True)
        readme = (self.root / "vault" / "SYS" / "README.md").read_text(encoding="utf-8")
        self.assertIn("мой-проект", readme)
        self.assertNotIn("{", readme, "в README волта осталась незаполненная подстановка")

    def test_taxonomy_is_empty_frame(self) -> None:
        """Таксономия — каркас: домены чужого сервиса в поставку не едут."""
        self._deploy(vault=True)
        taxonomy = (self.root / "vault" / "SYS" / "taxonomy.md").read_text(encoding="utf-8")
        self.assertNotIn("inventory", taxonomy.lower())

    def test_no_vault_dir_without_vault(self) -> None:
        self._deploy(vault=False)
        self.assertFalse((self.root / "vault").exists())

    # --- Проверка полноты и устаревания ---

    def test_missing_vault_reported_and_fixed(self) -> None:
        self._deploy(vault=True)
        for path in sorted((self.root / "vault").rglob("*.md")):
            path.unlink()
        issues = environment_issues(self.tasks_dir, self.cfg)
        self.assertIn("vault", {i["part"] for i in issues})
        self.assertIn("no_vault", self._codes())

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["vault"]})
        self.assertEqual(self._codes(), [])

    def test_outdated_vault_rules_reported(self) -> None:
        """structure.md и шаблоны заметок — поставка: отставание видно."""
        self._deploy(vault=True)
        structure = self.root / "vault" / "SYS" / "structure.md"
        structure.write_text("# мои правила\n", encoding="utf-8")
        self.assertIn("outdated_vault", self._codes())

    def test_user_data_of_vault_not_checked(self) -> None:
        """README и таксономию заполняет пользователь — эталона у них нет."""
        self._deploy(vault=True)
        for name in ("README.md", "taxonomy.md"):
            path = self.root / "vault" / "SYS" / name
            path.write_text(path.read_text(encoding="utf-8") + "\nмоя правка\n", encoding="utf-8")
        self.assertEqual(self._codes(), [])

    def test_refresh_only_named_vault_file(self) -> None:
        """Кнопка рядом с diff обновляет один файл волта, а не всю папку (TASK-048)."""
        self._deploy(vault=True)
        structure = self.root / "vault" / "SYS" / "structure.md"
        note = self.root / "vault" / "SYS" / "templates" / "code-note.md"
        structure.write_text("устарело\n", encoding="utf-8")
        note.write_text("тоже устарело\n", encoding="utf-8")

        scaffold_project(self.tasks_dir, self.cfg,
                         {"parts": ["vault"], "names": ["SYS/structure.md"]})

        self.assertNotEqual(structure.read_text(encoding="utf-8"), "устарело\n")
        self.assertEqual(note.read_text(encoding="utf-8"), "тоже устарело\n",
                         "точечное обновление задело соседний файл волта")

    def test_vault_notes_untouched(self) -> None:
        """Заметки пользователя — не наша поставка, их не трогаем и не считаем."""
        self._deploy(vault=True)
        note = self.root / "vault" / "warehouse" / "my-note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# моя заметка\n", encoding="utf-8")

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["vault"]})
        self.assertEqual(note.read_text(encoding="utf-8"), "# моя заметка\n")
        self.assertEqual(self._codes(), [])

    def test_missing_skill_is_not_called_outdated(self) -> None:
        """Не развёрнутый write-vault — «не хватает», а не «устарел».

        Иначе баннер отправляет пользователя искать, что же поменялось в файле,
        которого нет вовсе (поймано при проверке TASK-038).
        """
        self._deploy(vault=False)
        self.cfg["vault"] = True
        issues = {(i["part"], i["state"]): i["names"]
                  for i in environment_issues(self.tasks_dir, self.cfg)}
        self.assertEqual(issues.get(("skills", "partial")), ["write-vault"])
        self.assertNotIn("write-vault", issues.get(("skills", "outdated"), []))

        messages = " | ".join(d["message"]
                              for d in validate_project(self.tasks_dir, self.cfg)["degraded"])
        self.assertIn("Не хватает скиллов: write-vault", messages)

    def test_vault_button_deploys_its_skill(self) -> None:
        """Одна кнопка — рабочее хранилище: волт приезжает вместе со своим скиллом."""
        self._deploy(vault=False)
        self.cfg["vault"] = True
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["vault"]})

        self.assertTrue((self._skills_dir() / "write-vault" / "SKILL.md").is_file())
        self.assertTrue((self.root / ".opencode" / "commands" / "write-vault.md").is_file())
        # Соседние скиллы кнопка волта не переписывает: их расхождение — отдельный
        # разговор с diff, а не побочный эффект развёртывания хранилища
        start = (self._skills_dir() / "start-task" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("Собрать знания из Knowledge Vault", start)

    def test_vault_enabled_later_is_reported(self) -> None:
        """Волт включили в настройках позже — пробел виден и чинится кнопкой."""
        self._deploy(vault=False)
        self.cfg["vault"] = True
        codes = self._codes()
        self.assertIn("no_vault", codes)
        # Волт меняет эталон текстов: скиллы и правила без его блоков отстали
        self.assertIn("outdated_rules", codes)

        scaffold_project(self.tasks_dir, self.cfg,
                         {"parts": ["vault", "skills", "commands", "rules"]})
        self.assertEqual(self._codes(), [])
        self.assertTrue((self._skills_dir() / "write-vault" / "SKILL.md").is_file(),
                        "скилл волта не доехал вместе с хранилищем")

    # --- Правила для агентов ---

    def test_rules_describe_vault_only_with_vault(self) -> None:
        with_vault = render_rules({**DEFAULTS, "vault": True})
        without = render_rules({**DEFAULTS, "vault": False})
        self.assertIn("vault/", with_vault)
        self.assertIn("write-vault", with_vault)
        self.assertNotIn("vault/", without)

    def test_search_instructions_account_for_gitignore(self) -> None:
        """Поиск по волту описан работающим способом, а не голым grep.

        Волт в .gitignore: glob/grep пропускают игнорируемые файлы и возвращают
        пустоту. Инструкция «сделай grep по vault/» отправляла агента в тупик и
        противоречила предупреждению в правилах (поймано при проверке TASK-038).
        """
        texts = {path.parent.name: path.read_text(encoding="utf-8")
                 for path in SKILLS_TEMPLATES.glob("*/SKILL.md")}
        texts["rules"] = (SKILLS_TEMPLATES.parent.parent
                          / "rules_section.md").read_text(encoding="utf-8")
        # Проверяем абзацами: оговорка про ignore-фильтр может стоять
        # следующей строкой того же абзаца, и это нормально
        for name, text in texts.items():
            for chunk in re.split(r"\n\s*\n", text):
                low = chunk.lower()
                if "vault/" in low and ("grep" in low or "glob" in low):
                    self.assertIn("no-ignore", low,
                                  f"{name}: поиск по волту описан способом, который не сработает")

    def test_rules_warn_about_gitignore(self) -> None:
        """Волт игнорируется git — агент должен знать, что glob/grep его не видят."""
        self.assertIn("gitignore", render_rules({**DEFAULTS, "vault": True}).lower())

    def test_deployed_rules_follow_vault_choice(self) -> None:
        self._deploy(vault=True)
        self.assertIn("vault/", (self.root / "CLAUDE.md").read_text(encoding="utf-8"))
        self.setUp()
        self._deploy(vault=False)
        self.assertNotIn("vault/", (self.root / "CLAUDE.md").read_text(encoding="utf-8"))

    # --- Проект, приведённый кнопками в порядок ---

    def test_full_deploy_with_vault_leaves_no_banners(self) -> None:
        self._deploy(vault=True)
        report = validate_project(self.tasks_dir, self.cfg)
        self.assertTrue(report["ok"])
        self.assertEqual(report["degraded"], [])
        self.assertEqual(environment_issues(self.tasks_dir, self.cfg), [])


if __name__ == "__main__":
    unittest.main()
