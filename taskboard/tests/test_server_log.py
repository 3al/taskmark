"""Файл лога сервера при любом запуске и след ошибок в нём (TASK-231).

У пользователя что-то не работает — мейнтейнеру нужен файл, который можно
прислать. Он был только у запуска без консоли: из терминала вывод жил до
закрытия окна, а сервер, перезапущенный из доски, писал в `DEVNULL` — поток
есть, файл не заводился, и вывод пропадал целиком. Ошибки, которые сервер
обработал сам, доходили только до красной строки на доске.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.exceptions import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from backend.app import app  # noqa: E402
from tests.test_project_from_cwd import load_launcher  # noqa: E402


class LogFileTest(unittest.TestCase):
    """Куда пишет лаунчер: консоль остаётся, файл добавляется."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.launcher = load_launcher()
        patch = mock.patch.object(self.launcher, "UPDATE_DIR", Path(tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def logged(self) -> str:
        return self.launcher.log_file(8765).read_text(encoding="utf-8")

    def start(self, stdout, stderr) -> None:
        """Завести поток при заданных потоках процесса и записать в оба."""
        with mock.patch.multiple(self.launcher.sys, stdout=stdout, stderr=stderr):
            self.launcher.ensure_log_stream(8765)
            self.launcher.log("сообщение сервера")
            print("Traceback: падение запроса", file=self.launcher.sys.stderr)
            self.launcher.close_log_stream()

    def test_terminal_run_writes_console_and_file(self) -> None:
        out, err = io.StringIO(), io.StringIO()

        self.start(out, err)

        self.assertIn("сообщение сервера", out.getvalue(), "консоль больше не видит вывода")
        self.assertIn("падение запроса", err.getvalue())
        self.assertIn("сообщение сервера", self.logged())

    def test_headless_run_writes_file(self) -> None:
        self.start(None, None)

        self.assertIn("сообщение сервера", self.logged())

    def test_restart_from_board_writes_file(self) -> None:
        """Перезапуск из доски отдаёт процессу `DEVNULL`: поток есть, но пустой."""
        with open(os.devnull, "w", encoding="utf-8") as null:
            self.start(null, null)

        self.assertIn("сообщение сервера", self.logged())

    def test_unhandled_failure_reaches_file(self) -> None:
        """Трассировку падения uvicorn пишет в stderr — она должна попасть в файл."""
        self.start(io.StringIO(), io.StringIO())

        self.assertIn("Traceback: падение запроса", self.logged())


def handle(method: str, path: str, exc: Exception):
    """Прогнать исключение через обработчик приложения, как это сделал бы сервер."""
    request = Request({"type": "http", "method": method, "path": path,
                       "headers": [], "query_string": b""})
    handler = app.exception_handlers[type(exc)]
    return asyncio.run(handler(request, exc))


class ErrorTraceTest(unittest.TestCase):
    """Ошибка, которую доска показала красной строкой, остаётся в логе."""

    def capture(self, method: str, path: str, exc: Exception) -> tuple[str, object]:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            response = handle(method, path, exc)
        return out.getvalue(), response

    def test_api_error_is_logged(self) -> None:
        text, response = self.capture("POST", "/api/scaffold",
                                      HTTPException(500, "файл правил не записан"))

        self.assertEqual(response.status_code, 500, "ответ доске не должен меняться")
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
        self.assertIn("POST /api/scaffold", text)
        self.assertIn("500", text)
        self.assertIn("файл правил не записан", text)

    def test_static_miss_is_not_logged(self) -> None:
        """Промах мимо API — не ошибка действия на доске, а шум."""
        text, response = self.capture("GET", "/favicon.ico", HTTPException(404, "Not Found"))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
