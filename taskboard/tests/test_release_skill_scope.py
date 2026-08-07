"""Охват скилла выпуска: он видит все релизные статусы (TASK-092).

Задача попадает в выпуск двумя путями: через скилл и мышью на доске. Второй
путь законен — доска источник правды, — но скилл смотрел только в пул готового,
и перенесённая мышью задача оставалась без текста, мимо changelog и незакрытой.

Тексты инструкций проверяются как поставка: они и есть реализация скилла.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "templates" / "agentic" / ".claude" / "skills" / "release" / "SKILL.md"


def steps(text: str) -> dict[str, str]:
    """Тело каждого шага скилла по его заголовку `## Шаг N. …`."""
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(Шаг\s+[\d.]+)\.\s*(.*)$", line)
        if m:
            current = f"{m.group(1)}. {m.group(2)}".strip()
            out[current] = ""
        elif current is not None:
            out[current] += line + "\n"
    return out


class ReleaseSkillText(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")
        self.steps = steps(self.text)

    def step_with(self, needle: str) -> str:
        """Тело шага, в заголовке или теле которого встречается needle."""
        for title, body in self.steps.items():
            if needle in title or needle in body:
                return body
        return ""


class ScopeTest(ReleaseSkillText):
    """Осмотр идёт по всем релизным статусам, а не по одному пулу."""

    def test_survey_covers_both_release_statuses(self) -> None:
        survey = self.step_with("Осмотреть")

        self.assertTrue(survey, "нет шага, который осматривает релизные статусы")
        self.assertIn("actions.release_draft", survey,
                      "статус подготовки не осматривается")
        self.assertIn("actions.release_lock", survey,
                      "статус утверждённого не осматривается")

    def test_manual_move_named_legal(self) -> None:
        """Перенос мышью — не нарушение порядка, а второй законный путь."""
        self.assertIn("мышью", self.text,
                      "скилл не говорит, откуда берутся задачи помимо него самого")

    def test_pool_alone_is_not_the_source(self) -> None:
        """Прежняя формулировка «смотри пул» не должна остаться единственной."""
        survey = self.step_with("Осмотреть")

        self.assertNotIn("Пул готового — статус **перед**", survey,
                         "шаг по-прежнему описывает только пул")


class DraftRuleTest(ReleaseSkillText):
    """Черновик пишется по отсутствию секции, а не по её пустоте."""

    def test_missing_section_is_the_trigger(self) -> None:
        drafts = self.step_with("Перенести отобранное")

        self.assertIn("отсутствие секции", drafts,
                      "признак черновика не назван — агент будет судить по пустоте")

    def test_empty_section_is_a_decision(self) -> None:
        drafts = self.step_with("Перенести отобранное")

        self.assertTrue(re.search(r"пуст\w+ секци\w+", drafts, re.I),
                        "про пустую секцию в шаге черновиков не сказано")
        self.assertTrue(re.search(r"не перезаписыва\w+", drafts),
                        "не сказано, что пустую секцию трогать нельзя")


class ApprovalIsMandatoryTest(ReleaseSkillText):
    """Утверждение текстов — гейт, а не вежливость (TASK-100).

    Решение проверяется **с двух концов**: правило в скилле и рекомендация
    этапа, работающая даже у того, кто требований не объявлял. Объяснения из
    скилла переселены в механизм, само правило осталось строкой — она стоит
    копейки, а разбирательство «почему агент этого не сделал» дорого.
    """

    def test_step_says_it_cannot_be_skipped(self) -> None:
        drafts = self.step_with("Показать черновики")

        self.assertTrue(re.search(r"пропуск\w*|не пропуска\w+|обязательн\w+", drafts),
                        "шаг не назван обязательным — его снова пропустят")

    def test_wording_approval_is_a_stage_requirement(self) -> None:
        """То же требование объявлено рекомендацией этапа: у невключившего оно
        звучит напоминанием, у включившего — отказом. Пропадёт из каталога —
        скилл останется единственной страховкой, а он держится на внимании."""
        from backend.statuses import CATALOG

        recommends = CATALOG["release_notes"].get("recommends", [])
        confirms = [r for r in recommends if r["check"] == "confirm"]

        self.assertTrue(confirms, "утверждение текстов не объявлено рекомендацией")
        self.assertIn("утвержден", confirms[0]["ask"].lower())

    def test_draft_presence_is_a_stage_requirement_too(self) -> None:
        """Задача без текста не должна уехать в changelog никак."""
        from backend.statuses import CATALOG

        checks = {r["check"] for r in CATALOG["release_notes"].get("recommends", [])}

        self.assertIn("section_present", checks)

    def test_lock_requires_the_answer(self) -> None:
        approved = self.step_with("Утвердить состав")

        self.assertTrue(re.search(r"только после", approved, re.I),
                        "перевод в утверждённое не привязан к ответу человека")

    def test_rule_repeated_among_the_important_ones(self) -> None:
        """Рядом с «ничего не выпускай без подтверждения» — там оно работает."""
        tail = self.text.split("## Важно", 1)[-1]

        self.assertIn("текст", tail.lower())


class ApprovedCompositionTest(ReleaseSkillText):
    """Состав выпуска — то, что лежит в статусе утверждённого."""

    def test_textless_task_in_lock_is_reported(self) -> None:
        approved = self.step_with("Утвердить состав")

        self.assertIn("actions.release_lock", approved)
        self.assertTrue(re.search(r"без текста|без секции", approved),
                        "задача без текста в утверждённом проходит молча")

    def test_changelog_is_built_from_lock(self) -> None:
        changelog = self.step_with("Собрать changelog")

        self.assertIn("actions.release_lock", changelog,
                      "changelog собирается не по составу утверждённого")

    def test_closing_follows_the_same_composition(self) -> None:
        closing = self.step_with("Закрыть задачи выпуска")

        self.assertIn("actions.release_lock", closing,
                      "закрываются не те задачи, что выпущены")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
