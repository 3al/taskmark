"""Клавиатура в выпадающих списках (TASK-078).

Списками в интерфейсе управляли только мышью: стрелками по ним не пройти,
Enter не выбирает подсвеченное. Умел это один `TaskPicker`, и его поведение
было четвёртой копией обработчиков в очереди на расхождение.

Теперь правило одно и живёт в общем хуке: `↑`/`↓` двигают подсветку, `Enter`
выбирает подсвеченное, `Esc` закрывает список, не закрывая окно, а подсветка
под мышью и под клавиатурой — одна.

Автотестов JS в проекте нет, поэтому проверяются исходники: важно не «как
написано», а что обработчик **один на все списки** и что копий не завелось.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
LIST_KEYS = SRC / "listKeys.js"

# Все выпадающие списки интерфейса: поле задачи, поле эпика, выбор проекта
# в шапке и пресеты критериев в форме создания
CONSUMERS = {
    "поле задачи": SRC / "components" / "TaskPicker.jsx",
    "поле эпика": SRC / "components" / "EpicField.jsx",
    "выбор проекта": SRC / "components" / "Header.jsx",
    "пресеты критериев": SRC / "components" / "NewTaskModal.jsx",
    # Выбор типа и размера в окне задачи: списки появились позже остальных,
    # и правило распространяется на них так же
    "тип и размер задачи": SRC / "components" / "TaskModal.jsx",
}


class SharedHookTest(unittest.TestCase):
    """Поведение задаётся общим хуком, а не копией в каждом списке."""

    def setUp(self) -> None:
        self.assertTrue(LIST_KEYS.is_file(),
                        f"нет общего модуля клавиатуры списков: {LIST_KEYS}")
        self.src = LIST_KEYS.read_text(encoding="utf-8")

    def test_hook_is_exported(self) -> None:
        self.assertIn("export function useListKeys", self.src)

    def test_hook_knows_all_keys(self) -> None:
        for key in ("ArrowDown", "ArrowUp", "Enter", "Escape"):
            with self.subTest(key=key):
                self.assertIn(f"'{key}'", self.src, f"хук не обрабатывает {key}")

    def test_escape_does_not_reach_the_window(self) -> None:
        """Esc закрывает список, а не окно, в котором он открыт.

        Слушатель окна закрыл бы модалку целиком, поэтому событие гасится там,
        где список его и обработал.
        """
        self.assertIn("stopPropagation", self.src)

    def test_arrows_do_not_scroll_the_page(self) -> None:
        self.assertIn("preventDefault", self.src)

    def test_wrap_around(self) -> None:
        """Подсветка ходит по кругу, а не упирается в край списка."""
        self.assertIn("%", self.src, "нет перехода через край списка")


class EveryListUsesItTest(unittest.TestCase):
    """Все четыре списка — на общем хуке, своих обработчиков ни у кого."""

    def test_lists_use_the_hook(self) -> None:
        for name, path in CONSUMERS.items():
            with self.subTest(list=name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("useListKeys", text,
                              f"{name}: список не на общем хуке")

    def test_no_local_key_handling(self) -> None:
        """Своя обработка стрелок — начало расхождения: её быть не должно."""
        for name, path in CONSUMERS.items():
            with self.subTest(list=name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("'ArrowDown'", text,
                                 f"{name}: своя копия обработки стрелок")

    def test_mouse_and_keyboard_share_the_highlight(self) -> None:
        """Подсветка одна: наведение мышью двигает ту же подсветку.

        Две подсветки на одном списке — прямой способ выбрать не то, что
        видишь: мышь подсвечивает одну строку, Enter выбирает другую.
        """
        for name, path in CONSUMERS.items():
            with self.subTest(list=name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("onMouseEnter", text,
                              f"{name}: наведение мышью не двигает подсветку")
                self.assertIn("setActive", text,
                              f"{name}: подсветка мышью и клавиатурой разная")


if __name__ == "__main__":
    unittest.main()
