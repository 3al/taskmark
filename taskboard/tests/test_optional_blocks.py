"""Тесты реестра опциональных блоков шаблонов (TASK-154).

Механизм «часть текста разворачивается только при включённой возможности»
раньше знал ровно один случай — волт — и держал его зашитой парой констант.
Здесь проверяется, что вырезание идёт по реестру: любая выключенная
возможность снимает свой блок и свои скиллы, а перенумерация шагов остаётся
одна на проход, сколько бы блоков ни сняли.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import scaffold  # noqa: E402
from backend.scaffold import (OPTIONAL_BLOCKS, feature_skills,  # noqa: E402
                              strip_optional_blocks)

# Два вымышленных блока: реестр обязан работать на любом наборе, а не на волте
DEMO_BLOCKS = (
    {"key": "vault", "marker": "vault", "skills": ("write-vault",)},
    {"key": "forge", "marker": "forge", "skills": ("send-review",)},
)

TEXT = """\
## Шаг 1. Собрать предмет

Текст первого шага.

<!-- forge -->
## Шаг 2. Спросить форж

Текст форжа.
<!-- /forge -->

<!-- vault -->
## Шаг 3. Прочитать волт

Текст волта.
<!-- /vault -->

## Шаг 4. Записать замечания

Дальше — как в шаге 5-6.

## Шаг 5. Отдать работу

## Шаг 6. Конец
"""


HALF_TEXT = """\
## Шаг 1. Начать

<!-- vault -->
## Шаг 2. Записать волт
<!-- /vault -->

## Шаг 2.5. Спросить исполнителя

Имя передаётся дальше (Шаг 2.5), см. шаги 2.5-3.

## Шаг 3. Перевести статус

## Шаг 4. Конец
"""

SKILLS_DIR = (Path(__file__).resolve().parent.parent
              / "templates" / "agentic" / ".claude" / "skills")


def _steps(text: str) -> list[float]:
    return [float(ln.split()[2].rstrip(".")) for ln in text.splitlines()
            if ln.startswith("## Шаг ")]


class StripOptionalBlocksTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(scaffold, "OPTIONAL_BLOCKS", DEMO_BLOCKS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_disabled_feature_disappears_with_its_markers(self) -> None:
        """Выключенная возможность уходит целиком — вместе с маркерами."""
        text = strip_optional_blocks(TEXT, {"vault"})
        self.assertNotIn("Текст форжа", text)
        self.assertNotIn("forge", text)

    def test_enabled_feature_survives_untouched(self) -> None:
        """Включённая возможность остаётся с маркерами: по ним режим и опознают."""
        text = strip_optional_blocks(TEXT, {"vault", "forge"})
        self.assertEqual(text, TEXT, "текст со всеми возможностями не переписывается")

    def test_each_feature_cuts_only_its_own_block(self) -> None:
        """Реестр разбирает блоки по ключам, а не снимает всё разом."""
        text = strip_optional_blocks(TEXT, {"forge"})
        self.assertIn("Текст форжа", text)
        self.assertNotIn("Текст волта", text)

    def test_step_numbering_survives_two_cuts(self) -> None:
        """Два вырезанных блока сдвигают шаги на два, а не на один."""
        text = strip_optional_blocks(TEXT, set())
        self.assertEqual(_steps(text), [1, 2, 3, 4])
        self.assertIn("как в шаге 3-4", text)

    def test_single_cut_keeps_old_behaviour(self) -> None:
        """Один блок — прежний результат: сдвиг на единицу."""
        text = strip_optional_blocks(TEXT, {"forge"})
        self.assertEqual(_steps(text), [1, 2, 3, 4, 5])
        self.assertIn("как в шаге 4-5", text)

    def test_half_step_after_cut_step_follows_previous(self) -> None:
        """Полушаг вырезанного шага встаёт за предыдущим, ссылки идут следом (TASK-277)."""
        text = strip_optional_blocks(HALF_TEXT, set())
        self.assertEqual(_steps(text), [1, 1.5, 2, 3])
        self.assertIn("(Шаг 1.5)", text)
        self.assertIn("шаги 1.5-2", text)

    def test_cut_half_step_shifts_nothing(self) -> None:
        """Вырезанный полушаг места в целой нумерации не занимал."""
        text = strip_optional_blocks(
            "## Шаг 1. А\n<!-- vault -->\n## Шаг 1.5. Волт\n<!-- /vault -->\n"
            "## Шаг 2. Б\nсм. шаг 2\n", set())
        self.assertEqual(_steps(text), [1, 2])
        self.assertIn("см. шаг 2", text)

    def test_skills_of_feature_come_from_registry(self) -> None:
        self.assertEqual(feature_skills("forge"), ("send-review",))
        self.assertEqual(feature_skills("нет-такой"), ())


class VaultInRegistryTest(unittest.TestCase):
    """Волт — обычная запись реестра, а не зашитый случай."""

    def test_vault_is_registered(self) -> None:
        keys = {spec["key"] for spec in OPTIONAL_BLOCKS}
        self.assertIn("vault", keys)

    def test_vault_skill_is_registered(self) -> None:
        self.assertIn("write-vault", feature_skills("vault"))

    def test_vault_block_cut_without_vault(self) -> None:
        text = strip_optional_blocks("до\n<!-- vault -->\nволт\n<!-- /vault -->\nпосле\n",
                                     set())
        self.assertNotIn("волт", text)
        self.assertIn("до", text)
        self.assertIn("после", text)


class RealSkillsStepOrderTest(unittest.TestCase):
    """Скиллы поставки без волта: заголовки шагов идут по возрастанию (TASK-277)."""

    def _stripped(self, skill: str) -> str:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        return strip_optional_blocks(text, set())

    def assertAscending(self, skill: str) -> list[float]:
        """Целые шаги подряд, полушаг — под номером целого шага, за которым стоит.

        Порядок полушагов между собой не проверяется: в start-task «1.2» стоит
        перед «1.1» в самом шаблоне, и перенумерация за это не отвечает.
        """
        steps = _steps(self._stripped(skill))
        whole = [s for s in steps if s == int(s)]
        self.assertEqual(whole, sorted(whole), f"{skill}: шаги не по порядку — {steps}")
        self.assertEqual(len(steps), len(set(steps)), f"{skill}: номер шага повторяется")
        current = None
        for s in steps:
            if s == int(s):
                current = int(s)
            else:
                self.assertEqual(int(s), current, f"{skill}: полушаг {s} не у своего шага — {steps}")
        self.assertEqual(sorted({int(s) for s in steps}), list(range(int(max(steps)) + 1)),
                         f"{skill}: в целой нумерации дыра — {steps}")
        return steps

    def test_handoff_task(self) -> None:
        text = self._stripped("handoff-task")
        steps = self.assertAscending("handoff-task")
        self.assertIn(3.5, steps)
        self.assertNotIn(4.5, steps)
        self.assertIn("(Шаг 3.5)", text)

    def test_neighbour_skills(self) -> None:
        # start-task — полушаги в разделах до и после вырезанного шага;
        # finalize-task — вырезанный шаг перед полушагом чужого шага
        for skill in ("start-task", "finalize-task", "brainstorm", "brainstorm-team"):
            with self.subTest(skill=skill):
                self.assertAscending(skill)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
