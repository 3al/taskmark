"""Файл задачи в файловом менеджере системы: открыть папку и выделить файл.

Дочерние процессы подменяются: в тестах ни Проводник, ни Finder не запускаются.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from backend import app as app_module
from backend import reveal


class _Runner:
    """Запоминает команды; `fail` — какие программы «не запустились»."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, argv: list[str]) -> bool:
        self.calls.append(list(argv))
        return argv[0] not in self.fail


class TestRevealCommand(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "TASK-001-задача с пробелом.md"
        self.file.write_text("x", encoding="utf-8")
        self.file = self.file.resolve()

    def test_windows_selects_file_in_explorer(self) -> None:
        run = _Runner()
        reveal.reveal(self.file, platform="win32", run=run)
        self.assertEqual(run.calls, [["explorer", "/select,", str(self.file)]])

    def test_macos_selects_file_in_finder(self) -> None:
        run = _Runner()
        reveal.reveal(self.file, platform="darwin", run=run)
        self.assertEqual(run.calls, [["open", "-R", str(self.file)]])

    def test_linux_asks_file_manager_to_select(self) -> None:
        run = _Runner()
        reveal.reveal(self.file, platform="linux", run=run)
        [call] = run.calls
        self.assertEqual(call[0], "dbus-send")
        self.assertIn("org.freedesktop.FileManager1.ShowItems", call)
        self.assertIn(f"array:string:{self.file.as_uri()}", call)

    def test_linux_without_selection_opens_folder(self) -> None:
        run = _Runner(fail=("dbus-send",))
        reveal.reveal(self.file, platform="linux", run=run)
        self.assertEqual(run.calls[-1], ["xdg-open", str(self.file.parent)])

    def test_nothing_started_is_error(self) -> None:
        run = _Runner(fail=("dbus-send", "xdg-open"))
        with self.assertRaises(reveal.RevealError):
            reveal.reveal(self.file, platform="linux", run=run)
        with self.assertRaises(reveal.RevealError):
            reveal.reveal(self.file, platform="darwin", run=_Runner(fail=("open",)))


class TestWaitNewWindow(unittest.TestCase):
    def test_returns_window_that_appeared(self) -> None:
        shots = iter([{1, 2}, {1, 2}, {1, 2, 7}])
        found = reveal.wait_new_window({1, 2}, lambda: next(shots), sleep=lambda _: None)
        self.assertEqual(found, 7)

    def test_no_new_window_gives_up(self) -> None:
        slept = []
        found = reveal.wait_new_window({1}, lambda: {1}, timeout=0.5, step=0.1,
                                       sleep=slept.append)
        self.assertIsNone(found)
        self.assertEqual(len(slept), 5)


class TestRevealEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name)
        self.file = self.tasks / "TASK-007-кнопка.md"
        self.file.write_text("---\nid: TASK-007\n---\n", encoding="utf-8")
        patch = mock.patch.object(app_module, "_ctx", return_value=(self.tasks, {}))
        patch.start()
        self.addCleanup(patch.stop)

    def test_reveals_task_file(self) -> None:
        with mock.patch.object(app_module.reveal, "reveal") as opened:
            self.assertEqual(app_module.api_task_reveal("TASK-007"), {"ok": True})
        opened.assert_called_once_with(self.file)

    def test_missing_task_is_404_and_opens_nothing(self) -> None:
        with mock.patch.object(app_module.reveal, "reveal") as opened:
            with self.assertRaises(HTTPException) as caught:
                app_module.api_task_reveal("TASK-404")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("не найдена", caught.exception.detail)
        opened.assert_not_called()

    def test_failure_is_reported(self) -> None:
        with mock.patch.object(app_module.reveal, "_start", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                app_module.api_task_reveal("TASK-007")
        self.assertEqual(caught.exception.status_code, 500)
        self.assertTrue(caught.exception.detail)

    def test_route_and_capability(self) -> None:
        route = next(r for r in app_module.app.routes
                     if getattr(r, "path", "") == "/api/tasks/{task_id}/reveal")
        self.assertIn("POST", route.methods)
        self.assertTrue(app_module.CAPABILITIES.get("task_reveal"))


if __name__ == "__main__":
    unittest.main()
