"""Галка «DnD по всей доске»: живёт только в настройках и включена по умолчанию
(TASK-051).

Дубль тумблера в шапке доски соревновался с настройками за одно и то же поле
конфига, а выключенный дефолт заставлял каждого нового пользователя сначала
идти и включать очевидное поведение.

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
        dnd = text.split("## Перетаскивание задач")[-1]
        self.assertNotIn("в шапке", dnd,
                         "помощь всё ещё ищет тумблер в шапке")
        self.assertIn("настройк", dnd.lower(),
                      "помощь не говорит, где теперь живёт тумблер")

    def test_lifecycle_section_mentions_settings(self) -> None:
        text = (DOCS / "04-lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("настройк", text.lower(),
                      "раздел жизненного цикла не указывает, где галка DnD")


if __name__ == "__main__":
    unittest.main()
