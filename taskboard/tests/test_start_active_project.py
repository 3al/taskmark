"""Старт сервера открывает последний активный проект, а не проект рабочей папки.

Автозагрузка запускает лаунчер из папки инструмента; если там свой проект, он
становился активным при каждом входе в систему, и выбор в UI терялся.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import registry  # noqa: E402
from tests.test_project_from_cwd import load_launcher  # noqa: E402


class StartActiveProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        orig = registry.PROJECTS_FILE, registry.GLOBAL_DIR
        registry.PROJECTS_FILE = self.root / "projects.json"
        registry.GLOBAL_DIR = self.root

        def restore() -> None:
            registry.PROJECTS_FILE, registry.GLOBAL_DIR = orig
        self.addCleanup(restore)
        self.launcher = load_launcher()

        self.a = self.project("a")
        self.b = self.project("b")
        registry.register_project(self.a, activate=True)
        registry.register_project(self.b, activate=True)

    def project(self, name: str) -> Path:
        tasks = self.root / name / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "board.md").write_text("# Доска\n", encoding="utf-8")
        return tasks

    def start(self, tasks_dir: Path, explicit: bool = False) -> str:
        """Регистрация проекта при старте сервера — как её делает лаунчер."""
        registry.register_project(
            tasks_dir, activate=self.launcher.activate_on_start(tasks_dir, explicit))
        return registry.get_active()["name"]

    def test_папка_известного_проекта_не_переключает_активный(self) -> None:
        self.assertEqual("b", self.start(self.a))

    def test_новый_проект_становится_активным(self) -> None:
        c = self.project("c")
        self.assertEqual("c", self.start(c))
        self.assertIn("c", [p["name"] for p in registry.list_projects()["projects"]])

    def test_явный_tasks_dir_переключает(self) -> None:
        self.assertEqual("a", self.start(self.a, explicit=True))

    def test_работающему_серверу_не_велят_переключаться(self) -> None:
        """Лаунчер при живом сервере передаёт ему то же решение."""
        activate = self.launcher.activate_on_start(self.a, explicit=False)
        with mock.patch.object(self.launcher.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            self.assertTrue(self.launcher.register_in_running(8765, self.a, activate))
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertIs(False, payload["activate"])


class BrowserWhenReadyTest(unittest.TestCase):
    """Вкладка открывается, когда сервер уже отвечает, а не в момент запуска."""

    def setUp(self) -> None:
        self.launcher = load_launcher()

    def test_браузер_ждёт_ответа_сервера(self) -> None:
        answers = iter([None, None, {"ok": True}])
        calls: list[str] = []

        def alive(port: int):
            calls.append("health")
            return next(answers)

        with mock.patch.object(self.launcher, "server_alive", alive), \
                mock.patch.object(self.launcher.webbrowser, "open",
                                  lambda url: calls.append(url)):
            self.launcher.open_browser_when_ready(8765, interval=0).join(5)
        self.assertEqual(["health"] * 3 + ["http://127.0.0.1:8765"], calls)

    def test_не_дождавшись_всё_равно_открывает(self) -> None:
        opened: list[str] = []
        with mock.patch.object(self.launcher, "server_alive", lambda port: None), \
                mock.patch.object(self.launcher.webbrowser, "open", opened.append):
            self.launcher.open_browser_when_ready(8765, timeout=0.05, interval=0.01).join(5)
        self.assertEqual(["http://127.0.0.1:8765"], opened)


if __name__ == "__main__":
    unittest.main()
