"""Обновившийся узнаёт про требования этапа из плашки «что нового» (TASK-113).

Требования материализуются **действием человека** — иначе обновление включало бы
гейты в чужих проектах молча. Обратная сторона: тот, кто обновился, может вообще
не узнать, что механизм появился, а возможность, о которой надо догадаться, для
большинства не существует.

Новых баннеров и состояний для этого не заводится: всё нужное уже есть — плашка
показывается один раз после обновления и сама помнит, что её показали. Работа
ровно одна: кнопка из плашки в настройки жизненного цикла.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
UPDATE_MODAL = SRC / "components" / "UpdateModal.jsx"
SETTINGS_MODAL = SRC / "components" / "SettingsModal.jsx"
APP = SRC / "App.jsx"


class WhatsNewButtonTest(unittest.TestCase):
    """Кнопка живёт в плашке результата обновления и ведёт в жизненный цикл."""

    def setUp(self) -> None:
        self.update = UPDATE_MODAL.read_text(encoding="utf-8")
        self.settings = SETTINGS_MODAL.read_text(encoding="utf-8")
        self.app = APP.read_text(encoding="utf-8")

    def test_update_modal_offers_the_settings(self) -> None:
        self.assertIn("Настроить требования", self.update,
                      "из плашки «что нового» некуда пойти настраивать")

    def test_button_asks_the_app_to_open_settings(self) -> None:
        """Окно обновлений само настройки не открывает: оно закрывается, а
        открытие — дело владельца обоих окон."""
        self.assertIn("onOpenSettings", self.update)

    def test_app_wires_the_callback(self) -> None:
        self.assertIn("onOpenSettings", self.app,
                      "App не связывает окно обновлений с настройками")

    def test_settings_accept_the_initial_tab(self) -> None:
        """Ведём не «в настройки вообще», а на вкладку жизненного цикла: иначе
        человек попадает в общие свойства и ищет требования сам."""
        self.assertIn("initialTab", self.settings)
        self.assertIn("initialTab", self.app)

    def test_lifecycle_tab_exists_under_that_key(self) -> None:
        """Ключ вкладки — не выдумка вызывающего: он объявлен в реестре вкладок."""
        self.assertIn("key: 'lifecycle'", self.settings)


class NoNewStateTest(unittest.TestCase):
    """Ноль новых мест, где что-то может застрять включённым.

    Отдельный баннер на доске потребовал бы своего флажка скрытия, а вечное
    напоминание тому, кто осознанно не хочет гейтов, — ровно тот шум, против
    которого механизм и заводился.
    """

    def test_no_separate_board_banner(self) -> None:
        app = APP.read_text(encoding="utf-8")

        self.assertNotIn("requiresBanner", app)
        self.assertNotIn("requires_banner", app)

    def test_dismissal_still_belongs_to_the_update_notice(self) -> None:
        """Плашка гасится тем же способом, что и раньше: своего состояния у
        кнопки нет — иначе она пережила бы «Понятно»."""
        update = UPDATE_MODAL.read_text(encoding="utf-8")

        self.assertIn("api.updateSeen()", update)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
