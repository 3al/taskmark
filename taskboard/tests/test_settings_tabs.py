"""Окно настроек разложено по вкладкам, а не одним свитком (TASK-069).

Настройки копились одним вертикальным списком: порт, превью карточки, среды
агентов, жизненный цикл, скрипт выпуска, кнопки сервера. Читать это сверху
вниз приходилось целиком, а с ростом числа настроек (требования этапа из
E003-LIFECYCLE) свиток становится длиннее.

Проверяем структуру: вкладки объявлены реестром, каждая настройка лежит ровно
в одной, и у каждой вкладки указан уровень влияния — свойство инструмента
(глобально) или свойство проекта.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SETTINGS = (Path(__file__).resolve().parent.parent
            / "frontend" / "src" / "components" / "SettingsModal.jsx")


def source() -> str:
    return SETTINGS.read_text(encoding="utf-8")


class TabsRegistryTest(unittest.TestCase):
    """Вкладки — данные, а не разметка: список правится в одном месте."""

    def test_tabs_are_declared(self) -> None:
        self.assertIn("const TABS", source(),
                      "вкладки должны объявляться реестром, а не россыпью JSX")

    def test_every_group_has_a_tab(self) -> None:
        src = source()
        for key in ("lifecycle", "agentic", "board", "release", "tool"):
            self.assertIn(f"'{key}'", src, f"нет вкладки {key}")

    def test_each_tab_declares_its_scope(self) -> None:
        """Пользователь должен видеть, что он меняет — проект или инструмент."""
        src = source()
        tabs = src[src.index("const TABS"):src.index("export default")]
        self.assertEqual(tabs.count("scope:"), tabs.count("key:"),
                         "у каждой вкладки должен быть указан уровень влияния")
        for scope in ("'project'", "'global'"):
            self.assertIn(scope, tabs, f"уровень {scope} нигде не используется")


class SettingsSurviveTest(unittest.TestCase):
    """Перекладывание по вкладкам ничего не теряет."""

    def test_all_controls_are_present(self) -> None:
        src = source()
        for key in ("dnd_full_board", "port", "card_title_size", "card_title_lines",
                    "card_meta_size", "harnesses", "vault", "review_sources",
                    "release_script"):
            self.assertIn(key, src, f"настройка {key} пропала из формы")

    def test_pipeline_editor_is_still_mounted(self) -> None:
        self.assertIn("<PipelineEditor", source())

    def test_server_actions_are_present(self) -> None:
        src = source()
        self.assertIn("restartServer", src)
        self.assertIn("stopServer", src)

    def test_saved_payload_keeps_every_key(self) -> None:
        """Вкладки не должны отправлять только «свою» часть конфига."""
        src = source()
        payload = src[src.index("const updates = ()"):src.index("const check")]
        for key in ("port", "dnd_full_board", "release_script", "harnesses",
                    "vault", "review_sources", "pipeline", "actions"):
            self.assertIn(key, payload, f"{key} не уходит в сохранение")


class LayoutTest(unittest.TestCase):
    """Окно горизонтальное: список вкладок сбоку, содержимое рядом."""

    def test_modal_is_wide(self) -> None:
        src = source()
        self.assertFalse(re.search(r"max-w-xl\b", src),
                         "окно осталось узким вертикальным свитком")
        self.assertTrue(re.search(r"max-w-(3|4|5)xl", src),
                        "нет горизонтальной раскладки")

    def test_only_active_tab_is_rendered(self) -> None:
        self.assertIn("tab ===", source(),
                      "содержимое должно переключаться выбранной вкладкой")

    def test_height_does_not_follow_the_tab(self) -> None:
        """Высота фиксирована: иначе окно прыгает при переключении."""
        self.assertIn("h-[min(90vh,640px)]", source(),
                      "форма снова тянется по содержимому вкладки")


class RememberedTabTest(unittest.TestCase):
    """Выбранная вкладка переживает закрытие окна."""

    def test_tab_is_stored(self) -> None:
        src = source()
        self.assertIn("localStorage.setItem(TAB_KEY", src,
                      "выбор вкладки нигде не сохраняется")
        self.assertIn("localStorage.getItem(TAB_KEY", src,
                      "сохранённая вкладка не читается при открытии")

    def test_unknown_saved_tab_falls_back(self) -> None:
        """Вкладку могли переименовать — старый ключ не должен давать пустоту."""
        src = source()
        self.assertIn("TABS.some((t) => t.key === saved)", src,
                      "сохранённый ключ принимается без проверки")


HELP = Path(__file__).resolve().parent.parent.parent / "docs" / "help"

# «Настройки (⚙) → Вкладка → …»: берём то, что идёт сразу за первой стрелкой.
# Второй уровень — поля внутри вкладки, их реестр не знает. Имя набирается из
# кириллических слов, поэтому кавычки, звёздочки и знаки препинания обрывают
# его сами; ищем по всему тексту, а не построчно — ссылка бывает с переносом
TAB_REFERENCE = re.compile(
    r"Настройк\w*\s*(?:\([^)]*\)|⚙)?\s*→\s*(?:\*\*)?«?\s*"
    r"([А-ЯЁ][а-яё]*(?:\s+[А-Яа-яЁё]+)*)")


def tab_titles() -> set[str]:
    """Названия вкладок из реестра — то, что человек читает в окне."""
    src = source()
    tabs = src[src.index("const TABS"):src.index("export default")]
    return set(re.findall(r"title:\s*'([^']+)'", tabs))


class HelpNamesRealTabsTest(unittest.TestCase):
    """Справка зовёт вкладки их названиями, а не ключами реестра.

    Справку раздаёт сам инструмент: неверное название — это ложь прямо в
    интерфейсе, и человек ищет несуществующий пункт вместо перезапуска сервера.
    """

    def test_help_points_to_existing_tabs(self) -> None:
        titles = tab_titles()
        self.assertTrue(titles, "не удалось прочитать названия вкладок")

        for path in sorted(HELP.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for match in TAB_REFERENCE.finditer(text):
                # Справка переносит строки по ширине — имя вкладки бывает
                # разорвано переносом, а окно показывает его одной строкой
                name = re.sub(r"\s+", " ", match.group(1)).strip()
                line_no = text.count("\n", 0, match.start()) + 1
                with self.subTest(file=path.name, line=line_no):
                    self.assertIn(name, titles,
                                  f"{path.name}:{line_no} — вкладки «{name}» нет "
                                  f"в окне настроек")


if __name__ == "__main__":
    unittest.main()
