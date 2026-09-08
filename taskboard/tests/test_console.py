"""Сообщения инструмента в консоли: метка времени и единое место формата.

Лог живёт дольше сеанса: у запуска без консоли он копится в файле
`~/.taskboard/server_<порт>.log` неделями, и присланный пользователем кусок
разбирают через несколько дней. Без даты и времени по нему не сказать ни когда
случилась ошибка, ни сколько раз она повторилась.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import ast
import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import console  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# `[taskboard 2026-09-08 16:45:12] сообщение`
LINE = re.compile(r"^\[taskboard \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] (?P<text>.*)$")


def printed(msg: str) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        console.log(msg)
    return buffer.getvalue().rstrip("\n")


class FormatTest(unittest.TestCase):
    """Строка называет время события и остаётся узнаваемой."""

    def test_строка_начинается_с_даты_и_времени(self) -> None:
        match = LINE.match(printed("сервер запущен"))

        self.assertIsNotNone(match, "в строке нет метки времени")
        self.assertEqual("сервер запущен", match.group("text"))

    def test_дата_нужна_наравне_со_временем(self) -> None:
        """Лог переживает не одни сутки: одно время не отвечает «когда»."""
        stamp = console.stamp(0)

        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_сообщение_не_теряется_целиком(self) -> None:
        self.assertIn("watcher: путь без наблюдения (D:\\x)",
                      printed("watcher: путь без наблюдения (D:\\x)"))


class ConsoleEncodingTest(unittest.TestCase):
    """У консоли Windows своя кодировка, и падать на ней сообщение не должно."""

    def test_символ_вне_кодировки_консоли_не_роняет_печать(self) -> None:
        class Cp866Console(io.StringIO):
            encoding = "cp866"  # символа «→» в ней нет

        buffer = Cp866Console()
        with redirect_stdout(buffer):
            console.log("задача из чата: backlog → development")

        self.assertIn("[taskboard ", buffer.getvalue())
        self.assertNotIn("→", buffer.getvalue())


class SinglePlaceTest(unittest.TestCase):
    """Формат задаётся в одном месте — иначе следующая строка снова без метки."""

    def sources(self):
        for path in sorted((ROOT / "backend").glob("*.py")):
            yield path, path.read_text(encoding="utf-8")
        launcher = ROOT.parent / "taskboard.py"
        yield launcher, launcher.read_text(encoding="utf-8")

    def test_лаунчер_не_импортирует_backend_ради_формата(self) -> None:
        """Модуль, загруженный до накатки обновления, остался бы старым.

        Тот же процесс после git-операции стартует сервер: попав в
        `sys.modules` обычным импортом, лаунчерский экземпляр достался бы и
        новому коду — сервер работал бы на смеси версий.
        """
        text = (ROOT.parent / "taskboard.py").read_text(encoding="utf-8")
        # Только импорты уровня модуля: внутри функций backend импортируется
        # законно — там это уже после накатки обновления
        tree = ast.parse(text)
        imported = [
            node.module or "" for node in tree.body
            if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name for node in tree.body
            if isinstance(node, ast.Import) for alias in node.names
        ]

        self.assertEqual([], [name for name in imported
                              if name == "backend" or name.startswith("backend.")])
        self.assertIn('spec_from_file_location("taskboard_console"', text)

    def test_никто_не_печатает_префикс_сам(self) -> None:
        offenders = [path.name for path, text in self.sources()
                     if path.name != "console.py" and "[taskboard]" in text]

        self.assertEqual([], offenders,
                         "печать мимо console.log — строка останется без времени")
