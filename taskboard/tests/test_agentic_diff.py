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

from backend import baseline  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import (  # noqa: E402
    agentic_diff,
    agentic_stale_details,
    refresh_agentic,
    scaffold_project,
)


class BaselineMixin:
    """Правка развёрнутого файла сама по себе устаревания не даёт (TASK-014).

    Свежесть считается по слепку: пока шаблон не двигался, расхождение — это
    кастомизация, о которой не сообщают. Чтобы получить устаревание, слепок
    должен говорить, что разворачивали именно эту, прежнюю версию.
    """

    root: Path
    cfg: dict

    def _deployed_from(self, part: str, name: str, text: str) -> None:
        baseline.write(self.root, part, name, text, self.cfg)


class AgenticDetailsTest(BaselineMixin, unittest.TestCase):
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

    def test_outdated_skill_has_state_outdated(self) -> None:
        skill = self._skill("start-task")
        old = skill.read_text(encoding="utf-8") + "\nхвост\n"
        skill.write_text(old, encoding="utf-8")
        self._deployed_from("skills", "start-task", old)

        items = agentic_stale_details(self.root, self.cfg)

        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["part"], "skills")
        self.assertEqual(items[0]["name"], "start-task")
        self.assertEqual(items[0]["state"], "outdated")

    def test_missing_skill_has_state_missing(self) -> None:
        self._skill("fix-task").unlink()
        items = agentic_stale_details(self.root)
        self.assertEqual([i["state"] for i in items], ["missing"])
        self.assertEqual(items[0]["name"], "fix-task")

    def test_details_cover_commands_too(self) -> None:
        cmd = self.root / ".opencode" / "commands" / "new-task.md"
        cmd.write_text("сломано", encoding="utf-8")
        self._deployed_from("commands", "new-task", "сломано")
        items = agentic_stale_details(self.root)
        self.assertEqual([(i["part"], i["name"]) for i in items], [("commands", "new-task")])

    # --- Diff ---

    def test_diff_shows_added_and_removed_lines(self) -> None:
        skill = self._skill("start-task")
        original = skill.read_text(encoding="utf-8").splitlines()
        broken = "\n".join(["ЛИШНЯЯ СТРОКА"] + original[1:]) + "\n"
        skill.write_text(broken, encoding="utf-8")
        self._deployed_from("skills", "start-task", broken)

        result = agentic_diff(self.root, "skills", "start-task", self.cfg)

        self.assertTrue(result["ok"], result)
        self.assertIn("@@", result["diff"])
        # Направление: развёрнутое -> шаблон, «+» = появится после обновления
        self.assertIn("-ЛИШНЯЯ СТРОКА", result["diff"])
        self.assertIn("+" + original[0], result["diff"])
        self.assertEqual(result["state"], "outdated")

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

        created, replaced, skipped, _diverged = refresh_agentic(
            self.root, "skills", names=["start-task"])

        self.assertEqual([Path(p).parts[-2] for p in replaced], ["start-task"])
        self.assertEqual(second.read_text(encoding="utf-8"), "тоже устарел",
                         "точечное обновление задело соседний скилл")
        self.assertNotIn("fix-task", " ".join(created + skipped))

    def test_diff_uses_project_vault_mode_for_commands(self) -> None:
        """Эталон команды считается по конфигу проекта, как и в списке расхождений.

        Иначе окно противоречит баннеру: список говорит «отличается», а diff
        рядом — «файлы совпадают».
        """
        cmd = self.root / ".opencode" / "commands" / "start-task.md"
        old = cmd.read_text(encoding="utf-8") + "\nхвост\n"
        cmd.write_text(old, encoding="utf-8")
        self._deployed_from("commands", "start-task", old)
        listed = [i for i in agentic_stale_details(self.root, self.cfg)
                  if i["part"] == "commands"]
        result = agentic_diff(self.root, "commands", "start-task", self.cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual(bool(listed), result["state"] != "same",
                         "список расхождений и diff разошлись в оценке")

    def test_scaffold_passes_names_through(self) -> None:
        first, second = self._skill("start-task"), self._skill("fix-task")
        first.write_text("устарел", encoding="utf-8")
        second.write_text("тоже устарел", encoding="utf-8")

        result = scaffold_project(self.tasks, self.cfg,
                                  {"parts": ["skills"], "names": ["start-task"]})

        self.assertTrue(any("start-task" in r for r in result["replaced"]))
        self.assertEqual(second.read_text(encoding="utf-8"), "тоже устарел")


class VaultDiffTest(BaselineMixin, unittest.TestCase):
    """Волт — такая же часть поставки: у его файлов тоже должен открываться diff (TASK-048)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks = self.root / "tasks"
        self.cfg = dict(DEFAULTS, vault=True)
        scaffold_project(self.tasks, self.cfg, {
            "skills": True, "commands": True,
            "rules_agents": False, "rules_claude": False, "vault": True,
        })
        self.structure = self.root / "vault" / "SYS" / "structure.md"

    def test_stale_details_name_opens_in_diff(self) -> None:
        """Имя из списка расхождений — рабочий ключ для diff, а не «неизвестный элемент»."""
        old = self.structure.read_text(encoding="utf-8") + "хвост\n"
        self.structure.write_text(old, encoding="utf-8")
        self._deployed_from("vault", "SYS/structure.md", old)
        items = [i for i in agentic_stale_details(self.root, self.cfg) if i["part"] == "vault"]
        self.assertEqual([i["name"] for i in items], ["SYS/structure.md"], items)

        result = agentic_diff(self.root, "vault", items[0]["name"], self.cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "outdated")
        self.assertEqual((result["added"], result["removed"]), (0, 1), result["diff"])

    def test_diff_for_missing_vault_file_is_all_additions(self) -> None:
        self.structure.unlink()
        result = agentic_diff(self.root, "vault", "SYS/structure.md", self.cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "missing")
        self.assertGreater(result["added"], 0)

    def test_fresh_vault_file_has_empty_diff(self) -> None:
        result = agentic_diff(self.root, "vault", "SYS/templates/code-note.md", self.cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["diff"], "")

    def test_unknown_vault_name_is_still_an_error(self) -> None:
        result = agentic_diff(self.root, "vault", "SYS/нет-такого.md", self.cfg)
        self.assertFalse(result["ok"])

    def test_user_files_are_not_diffable(self) -> None:
        """Таксономию и README наполняет пользователь — их с эталоном не сверяют."""
        result = agentic_diff(self.root, "vault", "SYS/taxonomy.md", self.cfg)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
