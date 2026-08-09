"""Сворачивание колонок и скрытие пустых (TASK-028).

Доска с настраиваемым пайплайном стала шире, и часть колонок нужна редко.
Свёрнутая колонка превращается в узкую полосу, но **остаётся целью дропа**:
иначе задачу в неё некуда положить, и сворачивание становится односторонним.

Главное требование — не косметическое: сворачивают обычно терминальные статусы,
где со временем копятся сотни задач, поэтому свёрнутая колонка **не монтирует
свои карточки**. `display: none` этого не даёт: узлы, обработчики и регистрации
dnd-kit остаются, а они и есть стоимость страницы.

Фронтенд здесь проверяется по исходникам — как в соседних тестах формы настроек:
своего рантайма у JS в проекте нет, а сторожить регресс всё равно нужно.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.config import DEFAULTS, PROJECT_KEYS  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


def source(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


class SettingScopeTest(unittest.TestCase):
    """«Скрывать пустые колонки» — свойство глаз, а не репозитория."""

    def test_default_is_off(self) -> None:
        """По умолчанию доска показывает пайплайн целиком: пропажу колонки
        человек должен включить сам, иначе он ищет её и не находит."""
        self.assertIs(DEFAULTS["hide_empty_columns"], False)

    def test_setting_is_global(self) -> None:
        """Удобство восприятия не зависит от репозитория — значит не проектный ключ."""
        self.assertNotIn("hide_empty_columns", PROJECT_KEYS)

    def test_saved_to_global_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config.GLOBAL_CONFIG_FILE = root / "config.json"
            config.GLOBAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            tasks_dir = root / "tasks"
            tasks_dir.mkdir()

            config.save_global_config({"hide_empty_columns": True})

            stored = json.loads(config.GLOBAL_CONFIG_FILE.read_text(encoding="utf-8"))
            self.assertIs(stored["hide_empty_columns"], True)
            self.assertIs(config.load_project_config(tasks_dir)["hide_empty_columns"], True)


class SettingsFormTest(unittest.TestCase):
    """Настройка живёт в форме, во вкладке вида доски."""

    def test_control_present(self) -> None:
        self.assertIn("hide_empty_columns", source("components/SettingsModal.jsx"))

    def test_control_lives_in_board_tab(self) -> None:
        src = source("components/SettingsModal.jsx")
        board = src[src.index("tab === 'board'"):src.index("tab === 'agentic'")]
        self.assertIn("hide_empty_columns", board,
                      "настройка вида доски должна стоять во вкладке «Вид доски»")


class CollapsedColumnTest(unittest.TestCase):
    """Свёрнутая колонка: полоса вместо списка, но дроп работает."""

    def test_collapsed_column_component_exists(self) -> None:
        self.assertIn("CollapsedColumn", source("components/Column.jsx"))

    def test_collapsed_column_has_no_task_cards(self) -> None:
        """Ради этого всё и делается: карточек свёрнутой колонки нет в DOM."""
        src = source("components/Column.jsx")
        start = src.index("function CollapsedColumn")
        body = src[start:src.index("export default function Column")]
        self.assertNotIn("<TaskCard", body,
                         "свёрнутая колонка не должна монтировать карточки")

    def test_collapsed_column_is_a_drop_target(self) -> None:
        """Иначе сворачивание одностороннее: задачу в колонку не положить."""
        src = source("components/Column.jsx")
        start = src.index("function CollapsedColumn")
        body = src[start:src.index("export default function Column")]
        self.assertIn("useDroppable", body)
        self.assertIn("col:", body, "дроп-зона должна быть той же, что у развёрнутой")

    def test_collapsed_column_can_be_reordered(self) -> None:
        """Сворачивание — это вид, а не потеря возможностей: полосу тоже двигают."""
        src = source("components/Column.jsx")
        start = src.index("function CollapsedColumn")
        body = src[start:src.index("export default function Column")]
        self.assertIn("useDraggable", body)
        self.assertIn("col-drag:", body,
                      "полоса должна быть той же ручкой перестановки, что и шапка")

    def test_collapsed_column_shows_count(self) -> None:
        src = source("components/Column.jsx")
        start = src.index("function CollapsedColumn")
        body = src[start:src.index("export default function Column")]
        self.assertIn("count", body, "на полосе нужен счётчик задач")


class BoardStateTest(unittest.TestCase):
    """Состояние сворачивания: per-project, рядом с порядком колонок."""

    def test_state_key_is_per_project(self) -> None:
        src = source("App.jsx")
        self.assertIn("taskboard:collapsedColumns:", src,
                      "состояние сворачивания хранится по проекту, как и порядок колонок")

    def test_filter_expands_matching_columns(self) -> None:
        """Найденное не должно молча оставаться в свёрнутой колонке."""
        src = source("App.jsx")
        self.assertIn("filtered", src)
        start = src.index("const collapsedSet")
        collapsed = src[start:src.index("const findColumn", start)]
        self.assertIn("filtered", collapsed,
                      "под фильтром колонка с совпадениями должна разворачиваться")


if __name__ == "__main__":
    unittest.main()
