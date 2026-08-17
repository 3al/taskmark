"""Обзор файловой системы для выбора корня проекта (TASK-006).

Путь к проекту раньше вводился строкой, потому что абсолютного пути браузер не
отдаёт: ни выбор папки, ни перетаскивание его не дают. Значит читает файловую
систему бэкенд, а UI показывает свой обозреватель — эти тесты про чтение.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.fs_browse import browse_dir  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
COMPONENTS = SRC / "components"


class BrowseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "проект" / "tasks").mkdir(parents=True)
        (self.root / "проект" / "tasks" / "board.md").write_text("# Доска",
                                                                encoding="utf-8")
        (self.root / "второй").mkdir()
        (self.root / ".скрытая").mkdir()
        (self.root / "файл.txt").write_text("не папка", encoding="utf-8")

    def test_lists_only_directories(self) -> None:
        result = browse_dir(str(self.root))

        names = [e["name"] for e in result["entries"]]
        self.assertIn("проект", names)
        self.assertIn("второй", names)
        self.assertNotIn("файл.txt", names, "в списке папок оказался файл")

    def test_hidden_directories_are_skipped(self) -> None:
        """Точечные папки — служебные, и корнем проекта не бывают."""
        self.assertNotIn(".скрытая",
                         [e["name"] for e in browse_dir(str(self.root))["entries"]])

    @unittest.skipUnless(sys.platform == "win32", "атрибут скрытой папки — про Windows")
    def test_system_folders_are_skipped_on_windows(self) -> None:
        """В корне диска иначе первыми идут `$RECYCLE.BIN` и подобные."""
        import subprocess

        hidden = self.root / "служебная"
        hidden.mkdir()
        subprocess.run(["attrib", "+h", str(hidden)], check=True,
                       capture_output=True, shell=True)

        self.assertNotIn("служебная",
                         [e["name"] for e in browse_dir(str(self.root))["entries"]])

    def test_entries_carry_full_path(self) -> None:
        """Клик по строке должен вести внутрь без склейки пути на фронте."""
        entry = next(e for e in browse_dir(str(self.root))["entries"]
                     if e["name"] == "проект")

        self.assertEqual(str(self.root / "проект"), entry["path"])

    def test_project_folders_are_marked(self) -> None:
        """Проект — доска внутри папки задач: её кладёт сюда сам инструмент."""
        entries = {e["name"]: e for e in browse_dir(str(self.root))["entries"]}

        self.assertTrue(entries["проект"]["project"])
        self.assertFalse(entries["второй"]["project"])

    def test_config_alone_is_enough(self) -> None:
        """Доску могли снести, а настройки проекта остались — это всё ещё проект."""
        (self.root / "настроенный" / "tasks").mkdir(parents=True)
        (self.root / "настроенный" / "tasks" / ".taskboard.json").write_text(
            "{}", encoding="utf-8")

        entries = {e["name"]: e for e in browse_dir(str(self.root))["entries"]}

        self.assertTrue(entries["настроенный"]["project"])

    def test_bare_tasks_folder_is_not_a_project(self) -> None:
        """Папка `tasks` встречается где угодно — в волте, в исходниках, в чужом
        репозитории. Метка на ней обещает проект там, где его нет."""
        (self.root / "волт" / "tasks").mkdir(parents=True)
        (self.root / "волт" / "tasks" / "заметка.md").write_text("текст",
                                                                encoding="utf-8")

        entries = {e["name"]: e for e in browse_dir(str(self.root))["entries"]}

        self.assertFalse(entries["волт"]["project"],
                         "проектом объявлена любая папка с подпапкой tasks")

    def test_sorted_case_insensitively(self) -> None:
        for name in ("Яблоко", "арбуз", "Банан"):
            (self.root / name).mkdir()

        names = [e["name"] for e in browse_dir(str(self.root))["entries"]]

        self.assertEqual(sorted(names, key=str.lower), names,
                         "порядок папок зависит от регистра — читается как случайный")

    def test_parent_is_returned(self) -> None:
        result = browse_dir(str(self.root / "проект"))

        self.assertEqual(str(self.root), result["parent"])
        self.assertEqual(str(self.root / "проект"), result["path"])

    def test_root_has_no_parent(self) -> None:
        """У корня диска родителя нет — кнопке «вверх» некуда вести."""
        root = Path(os.path.abspath(os.sep)).anchor

        self.assertIsNone(browse_dir(root)["parent"])

    def test_empty_path_starts_at_home(self) -> None:
        """Начинать с домашней папки: проект человека почти всегда рядом с ней."""
        self.assertEqual(str(Path.home()), browse_dir("")["path"])

    def test_missing_path_is_an_error_not_a_crash(self) -> None:
        result = browse_dir(str(self.root / "нет-такой"))

        self.assertFalse(result["ok"])
        self.assertTrue(result["error"], "отказ без объяснения неотличим от пустой папки")

    def test_file_instead_of_directory_is_an_error(self) -> None:
        self.assertFalse(browse_dir(str(self.root / "файл.txt"))["ok"])

    def test_unreadable_directory_is_an_error(self) -> None:
        """Отказ в доступе — обычное дело в системных папках, и это не пятисотка."""
        original = os.scandir

        def deny(path):
            raise PermissionError(13, "Отказано в доступе")

        os.scandir = deny
        self.addCleanup(lambda: setattr(os, "scandir", original))

        result = browse_dir(str(self.root))

        self.assertFalse(result["ok"])
        self.assertIn("доступ", result["error"].lower())

    def test_drives_listed_on_windows(self) -> None:
        """На Windows «вверх» упирается в корень диска — остальные диски иначе не видны."""
        result = browse_dir(str(self.root))

        if sys.platform == "win32":
            self.assertTrue(result["drives"], "список дисков пуст на Windows")
            self.assertIn(str(self.root.anchor), result["drives"])
        else:
            self.assertEqual([], result["drives"])


class ApiWiringTest(unittest.TestCase):
    def test_endpoint_exists(self) -> None:
        src = (Path(__file__).resolve().parent.parent / "backend" / "app.py").read_text(
            encoding="utf-8")

        self.assertIn("/api/fs/dirs", src, "обзор папок не выведен в API")
        self.assertIn("browse_dir", src)


class UiTest(unittest.TestCase):
    """Тест-раннера фронтенда нет: связка «поле API ↔ элемент UI» рвётся молча."""

    def test_components_exist(self) -> None:
        for name in ("DirBrowser.jsx", "AddProjectModal.jsx"):
            self.assertTrue((COMPONENTS / name).is_file(), f"нет компонента {name}")

    def test_header_only_opens_the_window(self) -> None:
        """Форма добавления живёт в окне, а не в строке шапки.

        Шапка не сжимается, а переносится целыми группами: поле пути с двумя
        кнопками — это ~430px, из-за которых строка ломается. В окне им есть
        где стоять, а строка шапки становится короче, чем была.
        """
        src = (COMPONENTS / "Header.jsx").read_text(encoding="utf-8")

        self.assertIn("AddProjectModal", src, "из шапки проект не добавить")
        self.assertNotIn("registerProject", src,
                         "регистрация всё ещё делается прямо из шапки")
        self.assertNotIn("D:\\\\мой-проект", src,
                         "поле пути вернулось в строку шапки")

    def test_window_has_field_and_browser(self) -> None:
        src = (COMPONENTS / "AddProjectModal.jsx").read_text(encoding="utf-8")

        self.assertIn("DirBrowser", src, "в окне нет обзора папок")
        self.assertIn("registerProject", src, "окно не добавляет проект")
        self.assertIn("setPath", src, "выбранная папка не попадает в поле пути")

    def test_browser_walks_the_tree(self) -> None:
        src = (COMPONENTS / "DirBrowser.jsx").read_text(encoding="utf-8")

        self.assertIn("parent", src, "нельзя подняться на уровень выше")
        self.assertIn("drives", src, "диски Windows не показаны")
        self.assertIn("project", src, "папка с проектом ничем не выделена")

    def test_api_has_browse_call(self) -> None:
        src = (SRC / "api.js").read_text(encoding="utf-8")

        self.assertIn("/api/fs/dirs", src)


if __name__ == "__main__":
    unittest.main()
