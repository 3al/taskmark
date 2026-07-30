"""Тест ширины окна задачи и широкого содержимого (TASK-047).

Окно задачи было жёстко 48rem (`max-w-3xl`), и широкая таблица уезжала за край:
горизонтально ехало **всё тело задачи**, вместе с текстом и заголовками.

Проверяем два свойства вёрстки:

1. окно растёт под содержимое до предела по вьюпорту, а не сидит на одной ширине;
2. таблица прокручивается внутри своей обёртки — значит, когда расти уже некуда,
   едет она одна, а не текст задачи.

Тесты читают исходники: тест-раннера фронтенда в проекте нет, а связка
«класс в JSX ↔ правило в CSS» рвётся молча.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
MODAL = SRC / "components" / "TaskModal.jsx"
CSS = SRC / "index.css"

# Класс-связка: обёртка широкого блока в разметке и её правило в стилях
SCROLL_CLASS = "md-scroll-x"


class TaskModalWidthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.src = MODAL.read_text(encoding="utf-8")
        # Контейнер окна — div сразу после бэкдропа, с фоном и рамкой
        m = re.search(r'className="bg-zinc-900 border border-zinc-700[^"]*"', self.src)
        self.assertIsNotNone(m, "контейнер окна задачи не найден")
        self.window_classes = m.group(0)

    def test_window_is_not_locked_to_one_width(self) -> None:
        self.assertNotIn("max-w-3xl", self.window_classes,
                         "ширина окна снова прибита гвоздём — широкая таблица не поместится")

    def test_window_grows_with_content(self) -> None:
        self.assertIn("w-fit", self.window_classes,
                      "окно не растёт под содержимое")

    def test_window_is_bounded_by_viewport(self) -> None:
        self.assertRegex(self.window_classes, r"max-w-\[[^\]]*vw\]",
                         "у окна нет предела по ширине экрана — вылезет за край")

    def test_window_keeps_readable_minimum(self) -> None:
        """Узкая задача не должна схлопываться в колонку по ширине заголовка."""
        self.assertRegex(self.window_classes, r"min-w-\[[^\]]*rem[^\]]*\]",
                         "у окна нет минимальной ширины")


class WideContentScrollsInsideTest(unittest.TestCase):
    def test_table_is_wrapped_into_scroller(self) -> None:
        md = (SRC / "markdown.jsx").read_text(encoding="utf-8")
        self.assertRegex(md, r"table:\s*\(", "нет переопределения рендера table")
        self.assertIn(SCROLL_CLASS, md, f"обёртка не помечена классом {SCROLL_CLASS}")

    def test_task_and_help_share_the_same_wrapper(self) -> None:
        """Справка рендерит те же таблицы — расходиться этим двум окнам незачем."""
        for name in ("TaskModal.jsx", "HelpModal.jsx"):
            src = (SRC / "components" / name).read_text(encoding="utf-8")
            self.assertIn("mdComponents", src, f"{name} рендерит таблицы без общей обёртки")

    def test_scroller_class_has_a_rule(self) -> None:
        css = CSS.read_text(encoding="utf-8")
        rules = re.findall(rf"\.{SCROLL_CLASS}[^{{]*{{([^}}]*)}}", css)
        self.assertTrue(rules, f"класс {SCROLL_CLASS} используется, но правила для него нет")
        declared = " ".join(rules)
        self.assertIn("overflow-x-auto", declared, "обёртка не прокручивается по горизонтали")
        self.assertIn("max-content", declared,
                      "обёртка не претендует на ширину содержимого — окно не вырастет")

    def test_long_words_do_not_stretch_the_window(self) -> None:
        """Длинный URL или путь переносится, а не растягивает окно на весь экран."""
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.md-body[^{]*\{[^}]*(break-words|overflow-wrap)",
                         "длинные слова в тексте задачи ничем не ограничены")

    def test_text_column_is_capped(self) -> None:
        """Текст не должен растягивать окно.

        Ширина окна считается по содержимому, а «естественная» ширина абзаца —
        это вся строка без переносов: без предела на текстовые блоки любая
        длинная заметка агента раздвигает окно на весь экран (поймано при
        проверке первой версии).
        """
        css = CSS.read_text(encoding="utf-8")
        capped = re.search(r"\.md-body\s*>\s*\*\s*\{([^}]*)\}", css)
        self.assertIsNotNone(capped, "у блоков тела задачи нет предела ширины")
        self.assertRegex(capped.group(1), r"max-width:\s*\d+(\.\d+)?rem",
                         "предел ширины текста не задан в rem")

    def test_wide_blocks_are_exempt_from_the_cap(self) -> None:
        """Ради широких блоков окно и растягивается — на них предел не действует."""
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, rf"\.md-body\s*>\s*\.{SCROLL_CLASS}\s*\{{[^}}]*max-width:\s*100%",
                         "обёртка широкого блока унаследовала предел текстовой колонки")

    def test_title_is_capped_too(self) -> None:
        """Длинное название задачи в шапке — тоже не повод растягивать окно."""
        src = MODAL.read_text(encoding="utf-8")
        self.assertRegex(src, r"flex-1 max-w-\[\d+(\.\d+)?rem\]",
                         "название в шапке ничем не ограничено по ширине")


if __name__ == "__main__":
    unittest.main()
