"""Подпроцессы не мигают консольными окнами.

Windows выделяет консольному приложению собственное окно, если у родителя
консоли нет. У Taskmark это обычное состояние: автозапуск идёт через `pythonw`,
перезапуск и обновление из UI — через отсоединённый процесс. Каждый вызов
`git.exe` при этом мелькал окном поверх всех остальных, а проверка обновлений
работает по таймеру всё время (TASK-234).

`capture_output=True` от этого не спасает: перехватываются потоки, а окно
создаётся всё равно.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.proc import no_window_flags  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
LAUNCHER = ROOT.parent / "taskboard.py"

# Где консоль есть у самого родителя, и прятать нечего. Ключ — имя функции,
# значение — почему исключение законно. Список исключений именно такой:
# запись здесь требует осознанной правки, а не молчаливого пропуска флага
ALLOWED_WITHOUT_FLAGS = {
    "dev_supervisor": ("dev-режим запускает человек из консоли: и супервизор, "
                       "и сервер-ребёнок наследуют её, окну взяться неоткуда"),
}


class FlagsTest(unittest.TestCase):
    """Сам флаг: на Windows он есть, на других платформах его не существует."""

    def test_на_windows_флаг_настоящий(self) -> None:
        if sys.platform != "win32":
            self.skipTest("флаг существует только на Windows")
        self.assertEqual(no_window_flags(), subprocess.CREATE_NO_WINDOW)

    def test_на_остальных_платформах_пусто(self) -> None:
        """`creationflags=0` — то же, что не передавать его вовсе."""
        if sys.platform == "win32":
            self.skipTest("проверка для платформ без флага")
        self.assertEqual(no_window_flags(), 0)

    def test_чужие_флаги_сохраняются(self) -> None:
        """Вызов может нести свои флаги — их нельзя затирать."""
        own = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        self.assertEqual(no_window_flags(own) & own, own)


def _subprocess_calls(path: Path):
    """Вызовы subprocess.run/Popen в файле: (функция, строка, есть ли флаги)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    holder: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                holder.setdefault(id(child), node.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in ("run", "Popen"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
            continue
        flags = any(kw.arg == "creationflags" for kw in node.keywords)
        yield holder.get(id(node), "<модуль>"), node.lineno, flags


class EveryCallHiddenTest(unittest.TestCase):
    """Шестой вызов заведут без флага — если за этим никто не следит.

    Проверяется не число мест (оно меняется), а правило: каждый запуск
    подпроцесса либо прячет окно, либо назван в исключениях с причиной.
    """

    def sources(self) -> list[Path]:
        return sorted(BACKEND.glob("*.py")) + [LAUNCHER]

    def test_каждый_запуск_прячет_окно(self) -> None:
        forgotten = []
        for path in self.sources():
            for holder, line, flags in _subprocess_calls(path):
                if flags or holder in ALLOWED_WITHOUT_FLAGS:
                    continue
                forgotten.append(f"{path.name}:{line} (в {holder})")
        self.assertEqual(forgotten, [],
                         "запуск подпроцесса без creationflags — окно мелькнёт: "
                         + ", ".join(forgotten))

    def test_проверка_действительно_что_то_видит(self) -> None:
        """Страховка от молчаливого «всё зелено»: вызовы должны находиться."""
        found = sum(1 for path in self.sources() for _ in _subprocess_calls(path))
        self.assertGreater(found, 5, "разбор перестал находить вызовы subprocess")

    def test_исключения_объяснены(self) -> None:
        for name, reason in ALLOWED_WITHOUT_FLAGS.items():
            self.assertTrue(reason.strip(), f"исключение {name} без причины")


if __name__ == "__main__":
    unittest.main()
