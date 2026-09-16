"""Секция правил в агентском файле: границы маркерами и посторонние секции.

TASK-282: баннер «правила не развёрнуты» и кнопка «Развернуть» по-разному
решали, есть ли секция в файле. Файл с `## Task Management` или просто словами
«task management» в тексте баннер считал пустым, а кнопка — уже размеченным,
и ничего не дописывала. Секция теперь опознаётся маркерами, а файлы,
развёрнутые до них, продолжают работать по прежнему правилу.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import baseline  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import (RULES_CLOSE, _renumber_rules,  # noqa: E402
                              agentic_diff, agentic_stale_details, remove_element,
                              render_rules, resolve_element, scaffold_project,
                              sync_rules)
from backend.validator import validate_project  # noqa: E402

RULES_OPEN_PREFIX = "<!-- task_management:rules"


class RulesMarkersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)
        # Только Claude Code: файл правил один — CLAUDE.md
        self.cfg["harnesses"] = {"claude": True, "opencode": False, "codex": False}
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False,
            "rules_agents": False, "rules_claude": False})
        self.path = self.root / "CLAUDE.md"

    def _write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def _read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def _codes(self) -> list[str]:
        return [d["code"] for d in validate_project(self.tasks_dir, self.cfg)["degraded"]]

    def _degraded(self, code: str) -> dict:
        report = validate_project(self.tasks_dir, self.cfg)
        found = next((d for d in report["degraded"] if d["code"] == code), None)
        if found is None:
            self.fail(f"деградация {code} не обнаружена: {report['degraded']}")
        return found

    def _press_banner_button(self) -> dict:
        """Кнопка «Развернуть» на баннере no_rules."""
        return scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False, "rules": False, "parts": ["rules"]})

    def _section(self, before: str = "") -> str:
        return _renumber_rules(render_rules(self.cfg), before)

    def _legacy_deploy(self, before: str) -> str:
        """Файл, развёрнутый версией без маркеров: секция дописана в конец, слепок — она сама."""
        section = self._section(before)
        self._write(before.rstrip("\n") + "\n\n" + section)
        baseline.write(self.root, "rules", "CLAUDE.md", section, self.cfg)
        return section

    def _extra_rules(self) -> list[dict]:
        return [i for i in agentic_stale_details(self.root, self.cfg)
                if i["part"] == "rules" and i["state"] == "extra"]


class BannerButtonAgreeTest(RulesMarkersTestCase):
    """Кнопка чинит ровно то, о чём говорит баннер."""

    def test_subheading_task_management_gets_section(self) -> None:
        self._write("# Проект\n\n## Task Management\n\nсвои правила\n")
        self.assertIn("no_rules", self._codes())

        self._press_banner_button()

        content = self._read()
        self.assertIn(RULES_OPEN_PREFIX, content)
        self.assertIn("свои правила", content, "чужой текст не трогаем")
        self.assertNotIn("no_rules", self._codes())

    def test_mention_in_text_gets_section(self) -> None:
        self._write("# Проект\n\nСм. раздел task management в вики.\n")

        self._press_banner_button()

        self.assertIn(RULES_OPEN_PREFIX, self._read())
        self.assertNotIn("no_rules", self._codes())

    def test_new_section_is_wrapped_in_markers(self) -> None:
        self._write("# Проект\n\nтекст\n")

        self._press_banner_button()

        content = self._read()
        open_at = content.index(RULES_OPEN_PREFIX)
        close_at = content.index(RULES_CLOSE)
        self.assertLess(open_at, content.index("TASK MANAGEMENT"))
        self.assertLess(content.index("## Структура файла задачи"), close_at)
        self.assertNotIn("outdated_rules", self._codes())

    def test_second_press_does_not_duplicate(self) -> None:
        self._write("# Проект\n\nтекст\n")
        self._press_banner_button()
        once = self._read()

        self._press_banner_button()

        self.assertEqual(self._read(), once)


class MarkedSectionTest(RulesMarkersTestCase):
    """Размеченная секция: своё — между маркерами, остальное принадлежит пользователю."""

    def test_text_after_close_marker_is_not_customization(self) -> None:
        self._write("# Проект\n")
        self._press_banner_button()
        self._write(self._read() + "\n# Моё\n\nприписка без заголовка\n")

        self.assertEqual([i for i in agentic_stale_details(self.root, self.cfg)
                          if i["part"] == "rules"], [])
        self.assertNotIn("outdated_rules", self._codes())

    def test_template_resolve_keeps_text_outside_markers(self) -> None:
        self._write("# Проект\n")
        self._press_banner_button()
        content = self._read().replace("## Эпики", "## Мои эпики", 1)
        self._write(content + "моя строка после секции\n")
        base = baseline.read(self.root, "rules", "CLAUDE.md", self.cfg) or ""
        baseline.write(self.root, "rules", "CLAUDE.md",
                       base.replace("## Структура", "## Прежняя структура", 1), self.cfg)

        result = resolve_element(self.root, "rules", "CLAUDE.md", "template", self.cfg)

        content = self._read()
        self.assertTrue(result["ok"])
        self.assertNotIn("## Мои эпики", content)
        self.assertIn("моя строка после секции", content)
        self.assertEqual(content.count(RULES_OPEN_PREFIX), 1)
        self.assertEqual(content.count(RULES_CLOSE), 1)

    def test_merge_keeps_text_outside_markers(self) -> None:
        if not baseline.git_available():
            self.skipTest("git не найден")
        self._write("# Проект\n")
        self._press_banner_button()
        content = self._read().replace("## Эпики", "## Мои эпики", 1)
        self._write(content + "моя строка после секции\n")
        base = baseline.read(self.root, "rules", "CLAUDE.md", self.cfg) or ""
        baseline.write(self.root, "rules", "CLAUDE.md",
                       base.replace("## Структура", "## Прежняя структура", 1), self.cfg)

        result = resolve_element(self.root, "rules", "CLAUDE.md", "merge", self.cfg)

        content = self._read()
        self.assertTrue(result["ok"], result)
        self.assertIn("## Мои эпики", content)
        self.assertIn("моя строка после секции", content)
        self.assertEqual(content.count(RULES_CLOSE), 1)

    def test_diff_does_not_show_markers(self) -> None:
        self._write("# Проект\n")
        self._press_banner_button()
        base = baseline.read(self.root, "rules", "CLAUDE.md", self.cfg) or ""
        baseline.write(self.root, "rules", "CLAUDE.md", base + "старый хвост\n", self.cfg)
        self._write(self._read().replace(RULES_CLOSE, "старый хвост\n" + RULES_CLOSE))

        diff = agentic_diff(self.root, "rules", "CLAUDE.md", self.cfg)

        self.assertTrue(diff["ok"])
        self.assertNotIn("task_management:rules", diff["diff"])

    def test_lone_open_marker_does_not_eat_file(self) -> None:
        """Закрывающий маркер снесли: берём прежнее правило, лишний маркер не множим."""
        self._write("# Проект\n")
        self._press_banner_button()
        self._write(self._read().replace(RULES_CLOSE + "\n", "") + "\n# Моё\n\nтекст\n")

        sync_rules(self.root, {**self.cfg, "pipeline": ["backlog", "queued", "development", "completed"]})

        content = self._read()
        self.assertEqual(content.count(RULES_OPEN_PREFIX), 1)
        self.assertEqual(content.count(RULES_CLOSE), 1)
        self.assertIn("# Моё\n\nтекст", content)
        self.assertIn("backlog → queued → development → completed", content)


class LegacyCompatibilityTest(RulesMarkersTestCase):
    """Файлы, развёрнутые до маркеров, после обновления живут как жили."""

    def test_legacy_same_stays_silent_and_untouched(self) -> None:
        self._legacy_deploy("# Проект\n\nтекст\n")
        before = self._read()

        self.assertEqual([i for i in agentic_stale_details(self.root, self.cfg)
                          if i["part"] == "rules"], [])
        self.assertNotIn("no_rules", self._codes())
        self.assertNotIn("outdated_rules", self._codes())
        self.assertNotIn("extra_rules", self._codes())
        # Полное развёртывание и валидация маркеры молча не расставляют
        scaffold_project(self.tasks_dir, self.cfg, {
            "skills": False, "commands": False, "rules_claude": True})
        self._press_banner_button()
        self.assertEqual(self._read(), before)

    def test_legacy_customized_stays_customized(self) -> None:
        self._legacy_deploy("# Проект\n")
        self._write(self._read().replace("## Эпики", "## Мои эпики", 1))

        self.assertNotIn("outdated_rules", self._codes())
        self.assertEqual([i for i in agentic_stale_details(self.root, self.cfg)
                          if i["part"] == "rules"], [])

    def test_legacy_outdated_stays_outdated_and_update_adds_markers(self) -> None:
        old = self._legacy_deploy("# Проект\n").replace("## Эпики", "## Старые эпики", 1)
        self._write(self._read().replace("## Эпики", "## Старые эпики", 1))
        baseline.write(self.root, "rules", "CLAUDE.md", old, self.cfg)
        self.assertEqual(agentic_stale_details(self.root, self.cfg)[0]["state"],
                         baseline.OUTDATED)

        resolve_element(self.root, "rules", "CLAUDE.md", "template", self.cfg)

        content = self._read()
        self.assertIn(RULES_OPEN_PREFIX, content)
        self.assertIn("# Проект", content)
        self.assertNotIn("outdated_rules", self._codes())
        self.assertEqual(self._extra_rules(), [])

    def test_state_unchanged_after_markers_written(self) -> None:
        """Слепок, записанный без маркеров, совпадает с размеченной секцией."""
        self._legacy_deploy("# 1. Проект\n\nтекст\n\n# 2. Другое\n\nещё\n")
        self._write(self._read() + "\n")

        sync_rules(self.root, {**self.cfg, "pipeline": ["backlog", "queued", "development", "completed"]})
        sync_rules(self.root, self.cfg)

        content = self._read()
        self.assertIn(RULES_OPEN_PREFIX, content)
        self.assertIn("# 3. TASK MANAGEMENT", content)
        self.assertEqual([i for i in agentic_stale_details(self.root, self.cfg)
                          if i["part"] == "rules"], [])

    def test_legacy_section_with_user_heading_after(self) -> None:
        """Секция в середине файла: закрывающий маркер встаёт до следующего заголовка."""
        section = self._section("# 1. Проект\n")
        self._write("# 1. Проект\n\n" + section + "\n# 3. Моё\n\nтекст\n")
        baseline.write(self.root, "rules", "CLAUDE.md", section, self.cfg)

        sync_rules(self.root, {**self.cfg, "pipeline": ["backlog", "queued", "development", "completed"]})

        content = self._read()
        self.assertLess(content.index(RULES_CLOSE), content.index("# 3. Моё"))
        self.assertIn("# 3. Моё\n\nтекст\n", content)


class ForeignSectionTest(RulesMarkersTestCase):
    """Старая секция Task Management рядом с актуальной: предупредить и дать убрать."""

    def test_foreign_section_reported_after_deploy(self) -> None:
        self._write("# Проект\n\n## Task Management\n\nстарые правила\n\n## Сборка\n\nnpm\n")
        self._press_banner_button()

        message = self._degraded("extra_rules")["message"]
        self.assertIn("CLAUDE.md", message)
        items = self._extra_rules()
        self.assertEqual(len(items), 1)
        self.assertIn("Task Management", items[0]["label"])

    def test_mention_is_not_a_section(self) -> None:
        self._write("# Проект\n\nСм. task management в вики.\n")
        self._press_banner_button()

        self.assertNotIn("extra_rules", self._codes())

    def test_mention_in_heading_is_not_a_section(self) -> None:
        self._write("# Проект\n\n# 5. KNOWLEDGE VAULT\n\nволт\n\n"
                    "## В начале каждой сессии (дополнительно к task management)\n\n"
                    "читай волт\n\n## Правила работы с волтом\n\nпиши заметки\n")
        self._press_banner_button()

        self.assertNotIn("extra_rules", self._codes())
        self.assertEqual(self._extra_rules(), [])

    def test_named_sections_found(self) -> None:
        for heading in ("## Task Management", "## 1. TASK MANAGEMENT",
                        "## 6) Task management — правила"):
            with self.subTest(heading=heading):
                self._write(f"# Проект\n\n{heading}\n\nстарые правила\n")
                self._press_banner_button()

                items = self._extra_rules()
                self.assertEqual(len(items), 1)
                self.assertIn(heading.lstrip("# "), items[0]["label"])

    def test_indented_heading_is_a_section(self) -> None:
        self._write(" ## TASK MANAGEMENT\n старое\n\n# Проект\n\nтекст\n")
        self._press_banner_button()

        items = self._extra_rules()
        self.assertEqual(len(items), 1)
        self.assertIn("## TASK MANAGEMENT", items[0]["label"])

    def test_four_space_indent_is_code_not_section(self) -> None:
        self._write("# Проект\n\n    ## TASK MANAGEMENT\n\nтекст\n")
        self._press_banner_button()

        self.assertNotIn("extra_rules", self._codes())

    def test_mention_does_not_hide_real_section(self) -> None:
        self._write("# Проект\n\n## Сессия (дополнительно к task management)\n\nтекст\n\n"
                    "## Task Management\n\nстарые правила\n")
        self._press_banner_button()

        items = self._extra_rules()
        self.assertEqual(len(items), 1)
        self.assertIn("## Task Management", items[0]["label"])
        self.assertNotIn("Сессия", items[0]["label"])

    def test_heading_in_code_fence_is_not_a_section(self) -> None:
        self._write("# Проект\n\n```sh\n# task management helper\nrun\n```\n")
        self._press_banner_button()

        self.assertNotIn("extra_rules", self._codes())

    def test_legacy_own_section_is_not_foreign(self) -> None:
        self._legacy_deploy("# Проект\n")

        self.assertNotIn("extra_rules", self._codes())

    def test_second_unmarked_heading_is_foreign(self) -> None:
        self._legacy_deploy("# Проект\n\n# 1. TASK MANAGEMENT\n\nсовсем старое\n")

        self.assertIn("extra_rules", self._codes())

    def test_diff_shows_foreign_section_text(self) -> None:
        self._write("# Проект\n\n## Task Management\n\nстарые правила\n\n## Сборка\n\nnpm\n")
        self._press_banner_button()
        item = self._extra_rules()[0]

        diff = agentic_diff(self.root, "rules", item["name"], self.cfg)

        self.assertTrue(diff["ok"])
        self.assertIn("-старые правила", diff["diff"])
        self.assertNotIn("npm", diff["diff"], "соседний раздел в секцию не входит")
        self.assertNotIn("Жизненный цикл", diff["diff"])

    def test_remove_cuts_only_foreign_section_with_backup(self) -> None:
        self._write("# Проект\n\n## Task Management\n\nстарые правила\n\n### Подробности\n\n"
                    "ещё старое\n\n## Сборка\n\nnpm\n")
        self._press_banner_button()
        item = self._extra_rules()[0]

        result = remove_element(self.root, "rules", item["name"], self.cfg)

        content = self._read()
        self.assertTrue(result["ok"], result)
        self.assertNotIn("старые правила", content)
        self.assertNotIn("ещё старое", content, "подраздел уходит вместе с секцией")
        self.assertIn("## Сборка\n\nnpm", content)
        self.assertIn("# Проект", content)
        self.assertIn("Жизненный цикл статуса", content)
        backup = (self.root / result["backup"]).read_text(encoding="utf-8")
        self.assertIn("старые правила", backup)
        self.assertNotIn("extra_rules", self._codes())
        self.assertNotIn("no_rules", self._codes())

    def test_remove_refuses_our_section(self) -> None:
        self._write("# Проект\n")
        self._press_banner_button()

        result = remove_element(self.root, "rules", "CLAUDE.md", self.cfg)

        self.assertFalse(result["ok"])
        self.assertIn("TASK MANAGEMENT", self._read())


if __name__ == "__main__":
    unittest.main()
