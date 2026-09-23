"""Показать файл в файловом менеджере системы: открыть папку и выделить файл.

У каждой системы свой способ, и выделение умеют не все:

- Windows — `explorer /select,` выделяет файл в Проводнике; новое окно затем
  поднимается поверх остальных (см. `_raise_window`);
- macOS — `open -R` выделяет файл в Finder;
- Linux — единого способа нет. Выделение умеют менеджеры, реализующие
  `org.freedesktop.FileManager1` (Nautilus, Dolphin, Nemo, Thunar и другие);
  не ответил никто — открываем папку через `xdg-open`, без выделения.

Сервер локальный и работает от имени пользователя, поэтому окно менеджера
появляется на его рабочем столе.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from .proc import no_window_flags

# Сколько ждать программу, по коду возврата которой понятно, сработало ли
TIMEOUT = 5

# Чьему коду возврата можно верить. `explorer` возвращает 1 и при успехе, а
# `xdg-open` может не вернуться, пока открыт менеджер, — их только запускаем
_WAIT_FOR = {"dbus-send", "open"}


class RevealError(RuntimeError):
    """Файловый менеджер открыть не удалось."""


def _start(argv: list[str]) -> bool:
    """Запустить программу. False — не нашлась, упала или ответила отказом."""
    try:
        if argv[0] in _WAIT_FOR:
            done = subprocess.run(argv, capture_output=True, timeout=TIMEOUT,
                                  creationflags=no_window_flags())
            return done.returncode == 0
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=no_window_flags())
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# Сколько ждать появления окна Проводника и как часто смотреть
RAISE_WAIT = 3.0
RAISE_STEP = 0.1


def _explorer_windows() -> set[int]:
    """Видимые окна Проводника (класс `CabinetWClass`). Только Windows.

    Только видимые: новое окно Проводник создаёт скрытым и показывает позже,
    уже за окном браузера. Поднятое до показа, оно снова уходит вниз.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    found: set[int] = set()
    name = ctypes.create_unicode_buffer(64)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _):
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == "CabinetWClass" and user32.IsWindowVisible(hwnd):
            found.add(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    return found


def _raise_window(hwnd: int) -> None:
    """Поднять окно поверх остальных, не забирая фокус. Только Windows.

    Фокус Windows фоновому процессу не отдаёт: кнопку нажали в браузере, а
    Проводник запускает сервер. Окно тогда открывается за браузером (или
    свёрнутым) и лишь мигает на панели задач. Положение окна среди других
    защитой фокуса не ограничено: свёрнутое разворачиваем без активации, а
    поверх остальных ставим парой «поверх всех» → «обычное» — окно остаётся
    сверху, но не закрепляется там.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    # Типы обязательны: `HWND_TOPMOST` — это -1 размером с указатель, а без
    # объявления ctypes передаёт 32-битное целое, и старшие биты не те
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE|NOMOVE|NOACTIVATE|SHOWWINDOW
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, flags)  # HWND_TOPMOST
    user32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, flags)  # HWND_NOTOPMOST


def wait_new_window(before: set, windows, timeout: float = RAISE_WAIT,
                    step: float = RAISE_STEP, sleep=time.sleep):
    """Дождаться окна, которого не было в `before`. Не дождались — None."""
    waited = 0.0
    while waited < timeout:
        fresh = windows() - before
        if fresh:
            return next(iter(fresh))
        sleep(step)
        waited += step
    return None


def _raise_later(before: set) -> None:
    """Фоном: дождаться нового окна Проводника и поднять его.

    Фоном — чтобы ответ кнопке не ждал окна. Провал молчаливый: окно уже
    открыто и файл выделен, не вышло только поднять его наверх.
    """
    def run() -> None:
        try:
            hwnd = wait_new_window(before, _explorer_windows)
            if hwnd:
                _raise_window(hwnd)
        except (AttributeError, OSError):
            pass

    threading.Thread(target=run, name="reveal-raise", daemon=True).start()


def _attempts(path: Path, platform: str) -> list[list[str]]:
    """Команды по порядку: первая сработавшая — и хватит."""
    if platform.startswith("win"):
        return [["explorer", "/select,", str(path)]]
    if platform == "darwin":
        return [["open", "-R", str(path)]]
    return [
        ["dbus-send", "--session", "--print-reply",
         "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
         "org.freedesktop.FileManager1.ShowItems",
         f"array:string:{path.as_uri()}", "string:"],
        ["xdg-open", str(path.parent)],
    ]


def reveal(path: Path, platform: str | None = None, run=None) -> None:
    """Открыть папку файла в менеджере системы и, где можно, выделить файл.

    `platform` и `run` подменяются в тестах. Не сработал ни один способ —
    `RevealError`.
    """
    path = Path(path).resolve()
    platform = platform or sys.platform
    # Поднимать окно имеет смысл только у настоящего запуска на Windows
    lift = run is None and platform.startswith("win")
    run = _start if run is None else run
    before: set = set()
    if lift:
        try:
            before = _explorer_windows()
        except (AttributeError, OSError):
            lift = False
    for argv in _attempts(path, platform):
        if run(argv):
            if lift:
                _raise_later(before)
            return
    raise RevealError(f"Не удалось открыть файловый менеджер для {path.parent}")
