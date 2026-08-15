"""Галка «DnD по всей доске»: живёт только в настройках и включена по умолчанию
(TASK-051), сортировка внутри колонки не блокируется (TASK-068).

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent / "backend"
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"
DOCS = Path(__file__).resolve().parent.parent.parent / "docs" / "help"


class DefaultEnabledTest(unittest.TestCase):
    """Полная доска — норма, ограничение «приём ↔ очередь» — опция."""

    def test_config_default_is_enabled(self) -> None:
        self.assertIs(DEFAULTS["dnd_full_board"], True,
                      "DnD по всей доске по умолчанию выключен")

    def test_health_fallback_is_enabled(self) -> None:
        """Конфиг без ключа (старые глобальные config.json) читается как включённый."""
        src = (BACKEND / "app.py").read_text(encoding="utf-8")
        self.assertIn('cfg.get("dnd_full_board", True)', src,
                      "fallback конфига всё ещё выключает полный DnD")


class TogglePlacementTest(unittest.TestCase):
    """Один тумблер — в настройках; шапка доски его больше не дублирует."""

    def test_header_has_no_dnd_checkbox(self) -> None:
        src = (FRONTEND / "components" / "Header.jsx").read_text(encoding="utf-8")
        self.assertNotIn("DnD по всей доске", src,
                         "галка DnD всё ещё дублируется в шапке")
        self.assertNotIn("onToggleDnd", src,
                         "в шапку всё ещё протянут обработчик тумблера")

    def test_settings_keep_the_toggle(self) -> None:
        src = (FRONTEND / "components" / "SettingsModal.jsx").read_text(encoding="utf-8")
        self.assertIn("dnd_full_board", src,
                      "из настроек пропала галка DnD — теперь её негде включить")


class HelpSyncTest(unittest.TestCase):
    """Руководство не отправляет пользователя искать тумблер в шапке."""

    def test_board_section_points_to_settings(self) -> None:
        text = (DOCS / "02-board.md").read_text(encoding="utf-8")
        # Только свой раздел, а не хвост файла: соседние разделы о шапке говорят
        # по своему поводу, и проверка тумблера про них ничего не знает
        dnd = text.split("## Перетаскивание задач")[-1].split("\n## ")[0]
        self.assertNotIn("в шапке", dnd,
                         "помощь всё ещё ищет тумблер в шапке")
        self.assertIn("настройк", dnd.lower(),
                      "помощь не говорит, где теперь живёт тумблер")

    def test_lifecycle_section_mentions_settings(self) -> None:
        text = (DOCS / "04-lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("настройк", text.lower(),
                      "раздел жизненного цикла не указывает, где галка DnD")


class SameColumnReorderTest(unittest.TestCase):
    """Сортировка внутри одной колонки безопасна всегда (TASK-068).

    dndFullBoard регулирует только кросс-колоночные перемещения.
    Перетаскивание карточки внутри той же колонки — это reorder, а не move,
    и не должно блокироваться при выключенном DnD.
    """

    def _src(self) -> str:
        return (FRONTEND / "statuses.js").read_text(encoding="utf-8")

    def test_same_column_returns_true(self) -> None:
        """from === to должен вернуть true — без оглядки на dndFullBoard."""
        src = self._src()
        fn_start = src.index("export function isDropAllowed")
        fn_end = src.index("\n}", fn_start) + 2
        fn_body = src[fn_start:fn_end]
        same_col_line = [l for l in fn_body.splitlines() if "from === to" in l][0]
        self.assertIn("return true", same_col_line,
                      "same-column reorder всё ещё зависит от dndFullBoard")

    def test_comment_says_same_column_always_allowed(self) -> None:
        """Комментарий должен отражать, что сортировка внутри колонки разрешена."""
        src = self._src()
        fn_start = src.index("export function isDropAllowed")
        # Берём 300 символов перед функцией — это комментарий
        dnd_comment = src[max(0, fn_start - 300):fn_start]
        self.assertIn("внутри колонки", dnd_comment,
                      "комментарий не уточняет, что сортировка внутри колонки разрешена")


if __name__ == "__main__":
    unittest.main()
