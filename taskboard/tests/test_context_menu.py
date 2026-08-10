"""Контекстное меню превью задачи (TASK-070).

Правый клик по карточке открывает меню действий: перенос в колонку без
перетаскивания через всю доску, копирование номера и содержимого. Механизм
общий — действия описываются списком, чтобы следующие пункты добавлялись
записью, а не новым меню.

Проверяем исходники: логика живёт во фронтенде, автотестов JS в проекте нет.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
MENU = SRC / "components" / "ContextMenu.jsx"
CARD = SRC / "components" / "TaskCard.jsx"
COLUMN = SRC / "components" / "Column.jsx"
APP = SRC / "App.jsx"
TASK_TEXT = SRC / "taskText.js"
TASK_MODAL = SRC / "components" / "TaskModal.jsx"


class MenuComponentTest(unittest.TestCase):
    """Само меню: общее, закрывается и не уезжает за край экрана."""

    def setUp(self) -> None:
        self.assertTrue(MENU.is_file(), "нет компонента ContextMenu")
        self.menu = MENU.read_text(encoding="utf-8")

    def test_items_are_data_not_hardcoded_actions(self) -> None:
        """Действия приходят списком — следующий пункт добавляется записью."""
        self.assertIn("items", self.menu)
        self.assertNotIn("Перенести в", self.menu,
                         "пункт зашит в меню — механизм не общий")

    def test_closes_on_escape_and_outside_click(self) -> None:
        self.assertIn("Escape", self.menu, "меню не закрывается по Esc")
        for event in ("pointerdown", "scroll", "resize"):
            with self.subTest(event=event):
                self.assertIn(event, self.menu, f"меню не закрывается на {event}")

    def test_keeps_menu_inside_viewport(self) -> None:
        """У правого края доски меню должно открываться левее курсора."""
        self.assertIn("innerWidth", self.menu)
        self.assertIn("innerHeight", self.menu)


class CardTriggerTest(unittest.TestCase):
    """Карточка только зовёт меню: что в нём — решает доска."""

    def setUp(self) -> None:
        self.card = CARD.read_text(encoding="utf-8")
        self.column = COLUMN.read_text(encoding="utf-8")

    def test_card_reports_context_menu(self) -> None:
        self.assertIn("onContextMenu", self.card)
        self.assertIn("preventDefault", self.card,
                      "меню браузера не подавлено — откроется чужое")

    def test_column_passes_handler_down(self) -> None:
        self.assertIn("onContextMenu", self.column,
                      "колонка не прокидывает обработчик до карточки")


class BoardActionsTest(unittest.TestCase):
    """Доска: состав меню и путь переноса."""

    def setUp(self) -> None:
        self.app = APP.read_text(encoding="utf-8")

    def test_menu_state_and_component(self) -> None:
        self.assertIn("ContextMenu", self.app, "меню не подключено к доске")
        self.assertIn("menuFor", self.app, "нет состояния открытого меню")

    def test_move_targets_respect_drag_rules(self) -> None:
        """Правило одно на все способы переноса — `isAllowed`, как у мыши."""
        start = self.app.index("const menuItems")
        block = self.app[start:self.app.index("\n  const ", start + 10)]
        self.assertIn("isAllowed", block,
                      "меню предлагает колонки в обход правил перетаскивания")

    def test_move_goes_through_the_same_dialogs_as_dnd(self) -> None:
        """Долг этапа, простой и причина отмены спрашиваются и здесь.

        Путь один — `startMove`: два пути с разными вопросами разъехались бы
        молча, и правым кликом обходилось бы то, что мышью не обойти.
        """
        start = self.app.index("const menuItems")
        block = self.app[start:self.app.index("\n  const ", start + 10)]
        self.assertIn("startMove", block, "меню переносит задачу в обход общего пути")

        move = self.app[self.app.index("const startMove"):]
        move = move[:move.index("\n  const ", 10)]
        for step in ("setPendingMove", "askDebtThenMove", "applyMove"):
            with self.subTest(step=step):
                self.assertIn(step, move, f"общий путь переноса потерял {step}")
        self.assertIn("startMove(taskId, from, to", self.app,
                      "перетаскивание больше не идёт общим путём")

    def test_copy_items_present(self) -> None:
        self.assertIn("copyTaskText", self.app, "нет пункта копирования содержимого")
        self.assertIn("clipboard", self.app, "нет копирования в буфер")


class CopyTextSharedTest(unittest.TestCase):
    """Формула «скопировать содержимое» одна: меню и окно задачи копируют одно."""

    def test_helper_exists_and_is_used_by_modal(self) -> None:
        self.assertTrue(TASK_TEXT.is_file(), "нет общего модуля текста задачи")
        helper = TASK_TEXT.read_text(encoding="utf-8")
        self.assertIn("export function taskCopyText", helper)
        self.assertIn("taskCopyText", TASK_MODAL.read_text(encoding="utf-8"),
                      "окно задачи копирует по своей формуле — тексты разъедутся")


if __name__ == "__main__":
    unittest.main()
