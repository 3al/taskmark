"""Тексты поставки ведут по схеме «в выпуск — только коммиты отобранных задач».

Проверенное коммитится в интеграционную ветку проекта, а выпуск переносит в
выпускаемую ветку коммиты одних лишь задач состава. Схему держат тексты: правила
и `finalize-task` говорят, куда коммитить, скилл выпуска — что передать скрипту
и что делать, когда коммит не ложится без невыбранной задачи.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_release_skill_scope import ReleaseSkillText  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AGENTIC = ROOT / "templates" / "agentic"
FINALIZE = AGENTIC / ".claude" / "skills" / "finalize-task" / "SKILL.md"
RULES = AGENTIC / "rules_section.md"
HELP = ROOT.parent / "docs" / "help" / "08-release.md"

_RELEASE_BLOCK = re.compile(r"<!-- release -->(.*?)<!-- /release -->", re.S)


def release_blocks(path: Path) -> str:
    return "\n".join(_RELEASE_BLOCK.findall(path.read_text(encoding="utf-8")))


class ReleaseSkillTest(ReleaseSkillText):
    def test_route_names_the_integration_branch(self) -> None:
        self.assertIn("integration_branch", self.step_with("Узнать маршрут"))

    def test_script_receives_the_commits(self) -> None:
        apply = self.step_with("--apply")
        self.assertIn("--commits", apply, "скрипт не получает коммиты состава")
        self.assertIn("commits", self.step_with("--changelog"))

    def test_task_without_commits_is_named(self) -> None:
        self.assertRegex(self.text, r"нет\s+(ни\s+одного\s+)?коммит",
                         "задача состава без коммитов проходит молча")

    def test_conflict_is_the_humans_choice(self) -> None:
        apply = self.step_with("--apply")
        self.assertIn("conflict", apply, "отказ из-за зависимости не разобран")
        self.assertRegex(apply, r"[Рр]ешает человек")

    def test_manual_release_lists_commits(self) -> None:
        manual = self.step_with("release_script` пуст")
        self.assertIn("integration_branch", manual,
                      "без скрипта не сказано, какие коммиты переносить руками")


class CommitBranchTest(unittest.TestCase):
    """Правила и финализация говорят, куда коммитить при включённой схеме."""

    def test_rules_name_the_branch(self) -> None:
        block = release_blocks(RULES)
        self.assertIn("integration_branch", block)
        self.assertRegex(block, r"Истори\w+ коммитов",
                         "не сказано, что выпуск берёт коммиты из истории задачи")

    def test_finalize_commits_into_the_branch(self) -> None:
        block = release_blocks(FINALIZE)
        self.assertIn("integration_branch", block)
        self.assertRegex(block, r"[Вв]етки нет", "не сказано, что делать без ветки")


class WorkBranchTest(unittest.TestCase):
    """Ветку проверяют до первой правки, а не только перед коммитом."""

    def test_work_starts_in_the_branch(self) -> None:
        for skill in ("start-task", "fix-task"):
            with self.subTest(skill=skill):
                block = release_blocks(AGENTIC / ".claude" / "skills" / skill / "SKILL.md")
                self.assertIn("integration_branch", block)
                self.assertIn("до первой правки", block)
                self.assertRegex(block, r"[Вв]етки нет")


class HelpContractTest(unittest.TestCase):
    def test_contract_documents_commits_and_conflict(self) -> None:
        text = HELP.read_text(encoding="utf-8")
        self.assertIn("--commits", text)
        self.assertIn('"conflict"', text)


if __name__ == "__main__":
    unittest.main()
