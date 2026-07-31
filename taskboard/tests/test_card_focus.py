"""Кольцо фокуса карточки задачи (TASK-060, довесок по отзыву).

Карточка фокусируема не по нашей воле: `useDraggable` из dnd-kit кладёт в неё
`tabIndex` ради перетаскивания с клавиатуры. Пока взаимодействие мышиное, кольца
не видно, но стоит закрыть окно задачи по Esc — браузер считает работу
клавиатурной и рисует **свой** дефолтный outline: толстый и белый на тёмной доске.

Убрать фокус нельзя (сломается клавиатурный DnD), поэтому кольцо своё — тонкое и
в цвет статуса.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
CARD = SRC / "components" / "TaskCard.jsx"
STATUSES = SRC / "statuses.js"


class CardFocusRingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.card = CARD.read_text(encoding="utf-8")
        self.statuses = STATUSES.read_text(encoding="utf-8")

    def test_card_replaces_browser_outline(self) -> None:
        self.assertIn("outline-none", self.card, "дефолтный outline браузера не отключён")
        self.assertIn("style.cardFocus", self.card, "карточка не берёт кольцо из палитры статуса")

    def test_focus_ring_defined_for_every_palette(self) -> None:
        """Tailwind собирает классы статически — цвет описывается в каждой палитре."""
        palettes = re.findall(r"^  (\w+): \{", self.statuses, flags=re.M)
        rings = re.findall(r"cardFocus: 'focus-visible:ring-1 focus-visible:ring-(\w+)-", self.statuses)
        self.assertEqual(sorted(palettes), sorted(rings),
                         "у части статусов нет своего кольца фокуса")

    def test_ring_is_focus_visible_only(self) -> None:
        """Клик мышью кольцо не рисует — иначе оно висит на каждой открытой задаче."""
        self.assertNotIn("focus:ring", self.statuses,
                         "кольцо повешено на :focus, а не на :focus-visible")


if __name__ == "__main__":
    unittest.main()
