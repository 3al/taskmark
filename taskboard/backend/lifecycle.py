"""Управление жизненным циклом сервера: остановка и перезапуск из UI."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

# Коды выхода: dev-супервизор в taskboard.py различает по ним,
# перезапускать ли сервер-подпроцесс или завершаться вместе с ним
EXIT_RESTART = 42
EXIT_STOP = 43

LAUNCHER = Path(__file__).resolve().parent.parent.parent / "taskboard.py"


def _delayed_exit(code: int, delay: float = 0.7) -> None:
    """Завершить процесс с задержкой — HTTP-ответ должен уйти клиенту.

    Страховка: если os._exit почему-то не сработал — SIGKILL через 3 секунды
    (был случай «остановленного из UI» сервера, продолжившего обслуживать).
    """
    threading.Timer(delay, lambda: os._exit(code)).start()
    threading.Timer(delay + 3, _hard_kill).start()


def _hard_kill() -> None:
    import signal
    os.kill(os.getpid(), signal.SIGKILL)


def stop_marker(port: int) -> Path:
    """Файл-маркер остановки: виден всем уровням вложенных супервизоров."""
    return Path.home() / ".taskboard" / f"stop_{port}.flag"


def stop_server(port: int) -> None:
    """Остановить сервер (в dev-режиме супервизор завершится следом).

    Маркер пишется до выхода: если супервизоров несколько (вложенность),
    каждый увидит маркер и завершится, а не перезапустит сервер.
    """
    try:
        marker = stop_marker(port)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("stop", encoding="utf-8")
    except Exception:
        pass
    _delayed_exit(EXIT_STOP)


def restart_server(port: int, tasks_dir: str | None) -> None:
    """Перезапустить сервер.

    Под dev-супервизором достаточно умереть — он перезапустит подпроцесс.
    Без супервизора перед выходом спавним detached-копию лаунчера.
    """
    if not os.environ.get("TASKBOARD_SUPERVISED"):
        _spawn_detached(port, tasks_dir)
    _delayed_exit(EXIT_RESTART)


def _spawn_detached(port: int, tasks_dir: str | None) -> None:
    """Запустить отсоединённый процесс лаунчера, переживающий текущий."""
    cmd = [
        sys.executable, str(LAUNCHER),
        "--port", str(port), "--no-browser", "--respawn",
    ]
    if tasks_dir:
        cmd += ["--tasks-dir", tasks_dir]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(LAUNCHER.parent),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
