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


def _steps(text: str) -> list[int]:
    return [int(ln.split()[2].rstrip(".")) for ln in text.splitlines()
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
