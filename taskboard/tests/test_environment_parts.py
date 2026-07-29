"""Тесты полноты поставки агентского окружения (TASK-037).

Проверка раньше работала по принципу «проверяем только развёрнутое»: части,
которой в проекте нет вовсе, отчёт не касался — молчание принималось за выбор
пользователя. Теперь состав поставки задаёт реестр частей, а выбор сред
(харнессов) хранится в конфиге проекта и спрашивается один раз.

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
from backend.scaffold import environment_issues, scaffold_project  # noqa: E402
from backend.validator import validate_project  # noqa: E402

BOTH = {"claude": True, "opencode": True}
CLAUDE_ONLY = {"claude": True, "opencode": False}
OPENCODE_ONLY = {"claude": False, "opencode": True}


class EnvironmentPartsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)

    # --- Помощники ---

    def _use(self, harnesses: dict) -> None:
        """Зафиксировать выбор сред, как это делает диалог через конфиг проекта."""
        self.cfg["harnesses"] = harnesses

    def _deploy(self, harnesses: dict, **options) -> dict:
        self._use(harnesses)
        return scaffold_project(self.tasks_dir, self.cfg,
                                {"harnesses": harnesses, **options})

    def _codes(self) -> list[str]:
        return [d["code"] for d in validate_project(self.tasks_dir, self.cfg)["degraded"]]

    def _bare_structure(self) -> None:
        """Только tasks/ и доска: агентского окружения нет вовсе."""
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": False, "rules_claude": False})

    # --- Выбор сред спрашивается, а не угадывается ---

    def test_without_choice_only_asks_for_it(self) -> None:
        """Пока среды не выбраны, про их части молчим — но говорим о самом выборе.

        Иначе проект, который просто не открывали ни в одной среде, получил бы
        баннеры про части, которых пользователь, возможно, и не хочет.
        """
        self._bare_structure()
        codes = self._codes()
        self.assertIn("no_harness_choice", codes)
        self.assertNotIn("no_skills", codes)
        self.assertNotIn("no_rules", codes)

    def test_choice_is_reported_with_prefill(self) -> None:
        """Отчёт несёт сохранённый выбор и предзаполнение для диалога."""
        self._bare_structure()
        report = validate_project(self.tasks_dir, self.cfg)
        self.assertIsNone(report["harnesses"]["choice"])
        self.assertEqual(set(report["harnesses"]["detected"]), {"claude", "opencode"})

    # --- Отсутствие части видно так же, как устаревание ---

    def test_missing_skills_reported(self) -> None:
        """Скиллов нет вовсе — без них агент не знает процесса, молчать нельзя."""
        self._use(BOTH)
        self._bare_structure()
        self.assertIn("no_skills", self._codes())

    def test_missing_commands_reported_for_opencode(self) -> None:
        self._use(BOTH)
        self._bare_structure()
        self.assertIn("no_commands", self._codes())

    def test_commands_not_required_without_opencode(self) -> None:
        """Отказ от opencode — решение пользователя, а не пробел поставки."""
        self._use(CLAUDE_ONLY)
        self._bare_structure()
        self.assertNotIn("no_commands", self._codes())

    def test_missing_epics_reported_and_fixed(self) -> None:
        """epics.md разворачивается, но раньше валидатор о нём не знал вовсе."""
        self._deploy(BOTH)
        (self.tasks_dir / "epics.md").unlink()
        self.assertIn("no_epics", self._codes())

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["epics"]})
        self.assertNotIn("no_epics", self._codes())

    def test_missing_skills_fixed_by_button(self) -> None:
        self._use(BOTH)
        self._bare_structure()
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["skills"]})
        self.assertNotIn("no_skills", self._codes())
        self.assertTrue((self.root / ".claude" / ".gitignore").is_file(),
                        "развёрнутое кнопкой окружение не должно утекать в git")

    def test_missing_commands_fixed_by_button(self) -> None:
        self._use(BOTH)
        self._bare_structure()
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["commands"]})
        self.assertNotIn("no_commands", self._codes())

    # --- .opencode/.gitignore обязан покрывать commands (TASK-049) ---

    def test_foreign_opencode_gitignore_gets_commands_entry(self) -> None:
        """Чужой .opencode/.gitignore без записи про commands — дописываем, а не молчим.

        Раньше файл создавался только при отсутствии: существующий
        (пользовательский) пропускался, и развёрнутые команды утекали в git.
        """
        self._use(OPENCODE_ONLY)
        self._bare_structure()
        gitignore = self.root / ".opencode" / ".gitignore"
        gitignore.parent.mkdir(parents=True)
        gitignore.write_text("# своё\nopencode.local.json\n", encoding="utf-8")

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["commands"]})

        text = gitignore.read_text(encoding="utf-8")
        self.assertIn("opencode.local.json", text, "чужие записи не трогаем")
        self.assertRegex(text, r"(?m)^commands/$")

    def test_full_deploy_appends_commands_to_foreign_gitignore(self) -> None:
        """То же при полном развёртывании, а не только по кнопке части."""
        self._use(OPENCODE_ONLY)
        self._bare_structure()
        gitignore = self.root / ".opencode" / ".gitignore"
        gitignore.parent.mkdir(parents=True)
        gitignore.write_text("# своё\n", encoding="utf-8")

        self._deploy(OPENCODE_ONLY)

        self.assertRegex(gitignore.read_text(encoding="utf-8"), r"(?m)^commands/$")

    def test_opencode_gitignore_untouched_when_commands_covered(self) -> None:
        """Папка уже покрыта (наш `*` или своя запись) — файл не меняется."""
        for content in ("# шаблон\n*\n", "# своё\ncommands/\n"):
            with self.subTest(content=content):
                self.setUp()
                self._use(OPENCODE_ONLY)
                self._bare_structure()
                gitignore = self.root / ".opencode" / ".gitignore"
                gitignore.parent.mkdir(parents=True)
                gitignore.write_text(content, encoding="utf-8")

                scaffold_project(self.tasks_dir, self.cfg, {"parts": ["commands"]})

                self.assertEqual(gitignore.read_text(encoding="utf-8"), content)

    def test_deployed_but_modified_is_outdated_not_missing(self) -> None:
        """Правленные целиком скиллы — устаревание, а не отсутствие части."""
        self._deploy(BOTH)
        for skill in (self.root / ".claude" / "skills").glob("*/SKILL.md"):
            skill.write_text("# правки пользователя\n", encoding="utf-8")
        codes = self._codes()
        self.assertIn("outdated_skills", codes)
        self.assertNotIn("no_skills", codes)

    # --- Рубрики доски по умолчанию (TASK-058) ---

    def test_board_template_has_default_subsections(self) -> None:
        """Раздел приёма задач стартует с дефолтным набором рубрик."""
        self._deploy(CLAUDE_ONLY)
        board = (self.tasks_dir / "board.md").read_text(encoding="utf-8")
        for title in ("### Новый функционал", "### Рефакторинг", "### Баги",
                      "### Уборка", "### Дизайн"):
            # \n в конце: «### Рефакторинг» не должно сматчиться на более длинный заголовок
            self.assertIn(f"{title}\n", board)

    # --- Раскладка opencode-проекта без Claude Code ---

    def test_opencode_only_deploys_skills_to_opencode(self) -> None:
        """Без Claude Code скиллам негде лежать, кроме .opencode/skills."""
        self._deploy(OPENCODE_ONLY)
        self.assertTrue((self.root / ".opencode" / "skills" / "start-task" / "SKILL.md").is_file())
        self.assertFalse((self.root / ".claude").exists(), "лишняя папка Claude Code")
        self.assertEqual(self._codes(), [])

    def test_opencode_only_checks_skills_in_opencode(self) -> None:
        """Устаревание скилла видно и в opencode-раскладке — раньше туда не смотрели."""
        self._deploy(OPENCODE_ONLY)
        skill = self.root / ".opencode" / "skills" / "start-task" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nхвост\n", encoding="utf-8")
        self.assertIn("outdated_skills", self._codes())

    def test_skills_not_duplicated_when_both_harnesses(self) -> None:
        """opencode читает и .claude/skills — вторая копия не нужна."""
        self._deploy(BOTH)
        self.assertTrue((self.root / ".claude" / "skills" / "start-task" / "SKILL.md").is_file())
        self.assertFalse((self.root / ".opencode" / "skills").exists())

    # --- Правила: состав файлов задаёт выбор сред ---

    def test_rules_files_follow_harness_choice(self) -> None:
        """Каждая среда читает свой файл: claude — CLAUDE.md, opencode — AGENTS.md."""
        self._deploy(CLAUDE_ONLY)
        self.assertTrue((self.root / "CLAUDE.md").is_file())
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertEqual(self._codes(), [])

    def test_missing_rules_of_chosen_harness_reported(self) -> None:
        self._deploy(BOTH)
        (self.root / "AGENTS.md").unlink()
        self.assertIn("no_rules", self._codes())

    # --- Чужие файлы рядом с нашими ---

    def test_foreign_skill_untouched(self) -> None:
        """Собственные скиллы пользователя лежат рядом и нас не касаются.

        Отличить свой развёрнутый файл от чужого с тем же именем мы пока не
        умеем (см. TASK-014), поэтому граница простая — состав шаблонов.
        """
        self._deploy(BOTH)
        foreign = self.root / ".claude" / "skills" / "init-vault" / "SKILL.md"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("# мой скилл\n", encoding="utf-8")

        self.assertEqual(self._codes(), [], "чужой скилл не должен попадать в отчёт")
        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["skills"]})
        self.assertEqual(foreign.read_text(encoding="utf-8"), "# мой скилл\n")

    def test_deploy_does_not_clobber_customized_tools(self) -> None:
        """Кнопка развёртывания не должна уносить локальные правки инструментов.

        Окно предлагается при первом открытии любого проекта, в том числе
        давно настроенного вручную, — одно нажатие не может стирать работу.
        """
        self._deploy(BOTH)
        script = self.tasks_dir / "create_task.py"
        script.write_text("# мой скрипт\n", encoding="utf-8")
        rules = self.root / "CLAUDE.md"
        rules.write_text(rules.read_text(encoding="utf-8") + "\nмоя приписка\n",
                         encoding="utf-8")

        result = self._deploy(BOTH)

        self.assertEqual(script.read_text(encoding="utf-8"), "# мой скрипт\n")
        self.assertIn("моя приписка", rules.read_text(encoding="utf-8"))
        self.assertIn("create_task.py", result["diverged"])
        self.assertIn("CLAUDE.md", result["diverged"])

    # --- Проект, приведённый кнопками в порядок ---

    def test_full_deploy_leaves_no_banners(self) -> None:
        for harnesses in (BOTH, CLAUDE_ONLY, OPENCODE_ONLY):
            with self.subTest(harnesses=harnesses):
                self.setUp()
                self._deploy(harnesses)
                report = validate_project(self.tasks_dir, self.cfg)
                self.assertTrue(report["ok"])
                self.assertEqual(report["degraded"], [])
                self.assertEqual(environment_issues(self.tasks_dir, self.cfg), [])

    def test_every_registry_part_is_fixable(self) -> None:
        """Каждая проблема реестра чинится точечным развёртыванием своей части."""
        self._deploy(BOTH)
        for path in ((self.tasks_dir / "epics.md"), (self.tasks_dir / "create_task.py"),
                     (self.tasks_dir / "set_status.py"), (self.root / "AGENTS.md")):
            path.unlink()
        (self.tasks_dir / "logs").rmdir()
        for skill in (self.root / ".claude" / "skills").glob("*/SKILL.md"):
            skill.unlink()
        for command in (self.root / ".opencode" / "commands").glob("*.md"):
            command.unlink()

        issues = environment_issues(self.tasks_dir, self.cfg)
        self.assertEqual({i["part"] for i in issues},
                         {"create_script", "status_script", "epics", "logs",
                          "skills", "commands", "rules"})

        scaffold_project(self.tasks_dir, self.cfg,
                         {"parts": sorted({i["part"] for i in issues})})
        self.assertEqual(self._codes(), [])


if __name__ == "__main__":
    unittest.main()
