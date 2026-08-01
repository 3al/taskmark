#!/usr/bin/env python3
"""
Taskboard — визуальный фронтенд для менеджера задач (папка tasks/).

Запуск из корня любого проекта:
  py taskboard.py            (Windows)
  python3 taskboard.py       (macOS)

Bootstrap: проверяет Python, ставит зависимости в taskboard/.venv,
проверяет собранный фронтенд, стартует сервер (или регистрирует проект
в уже запущенном) и открывает браузер.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
TOOL_DIR = ROOT / "taskboard"
VENV_DIR = TOOL_DIR / ".venv"
DIST_DIR = TOOL_DIR / "frontend" / "dist"
REQUIREMENTS = TOOL_DIR / "requirements.txt"

MIN_PYTHON = (3, 10)
DEFAULT_PORT = 8765

# Код выхода «остановлен из UI» — должен совпадать с backend/lifecycle.py.
# Супервизор по нему понимает, что сервер перезапускать не нужно.
EXIT_STOP = 43


def log(msg: str) -> None:
    print(f"[taskboard] {msg}")


def fail(msg: str) -> None:
    print(f"[taskboard] ОШИБКА: {msg}")
    sys.exit(1)


def local_version() -> str:
    """Версия этой копии инструмента (файл taskboard/VERSION).

    Читается напрямую, без импорта backend: лаунчер работает до создания venv,
    когда зависимостей ещё нет. Логика та же, что в backend/version.py.
    """
    try:
        text = (TOOL_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text or "0.0.0"


def _version_tuple(value: str) -> tuple[int, ...] | None:
    """Строка версии → кортеж чисел; непонятная строка → None."""
    text = (value or "").strip().lstrip("vV")
    parts = text.split(".") if text else []
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _version_lt(running: str, local: str) -> bool:
    """Версия запущенного сервера строго старее локальной?

    Любая неразбираемая сторона — False: пугать пользователя из-за того, что
    мы не поняли строку, хуже, чем промолчать.
    """
    a, b = _version_tuple(running), _version_tuple(local)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))


def venv_python() -> Path:
    """Путь к python внутри venv (win/posix)."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def deps_installed() -> bool:
    try:
        import fastapi, uvicorn, watchdog  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_python() -> None:
    if sys.version_info < MIN_PYTHON:
        fail(f"Требуется Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}, "
             f"у вас {sys.version_info.major}.{sys.version_info.minor}")


def ensure_deps(assume_yes: bool) -> None:
    """Проверить зависимости; при отсутствии — venv + pip install + re-exec."""
    if deps_installed():
        return

    py = venv_python()
    if not py.is_file():
        log("Зависимости не найдены. Создаю окружение taskboard/.venv ...")
        if not assume_yes and not _confirm("Установить зависимости (fastapi, uvicorn, watchdog)?"):
            fail("Установка отменена пользователем")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        log("Устанавливаю зависимости ...")
        subprocess.run(
            [str(py), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
            check=True,
        )

    # Перезапуск под python из venv
    log("Перезапуск под виртуальным окружением ...")
    os.execv(str(py), [str(py), str(Path(__file__).resolve())] + sys.argv[1:])


def ensure_frontend(assume_yes: bool) -> None:
    """Проверить собранный фронтенд; предложить сборку при наличии node."""
    if DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file():
        return

    log("Фронтенд не собран (нет taskboard/frontend/dist).")
    npm = _find_exe("npm")
    if not npm:
        log("Node.js не найден в PATH.")
        log("Варианты: 1) установить Node.js и перезапустить; "
            "2) взять релиз с собранным dist. Сервер API стартует без UI.")
        return

    if not assume_yes and not _confirm("Собрать фронтенд сейчас (npm install && npm run build)?"):
        log("Пропускаю сборку. Сервер API стартует без UI.")
        return

    frontend = TOOL_DIR / "frontend"
    shell = sys.platform == "win32"
    log("npm install ...")
    subprocess.run(["npm", "install"], cwd=str(frontend), check=True, shell=shell)
    log("npm run build ...")
    subprocess.run(["npm", "run", "build"], cwd=str(frontend), check=True, shell=shell)


def _confirm(question: str) -> bool:
    try:
        return input(f"[taskboard] {question} [y/n]: ").strip().lower() in ("y", "yes", "д", "да", "")
    except EOFError:
        return False


def _find_exe(name: str) -> str | None:
    from shutil import which
    return which(name)


def server_alive(port: int) -> dict | None:
    """Вернуть health запущенного сервера или None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def register_in_running(port: int, tasks_dir: Path) -> bool:
    """Зарегистрировать проект в уже запущенном сервере."""
    payload = json.dumps({"tasks_dir": str(tasks_dir), "activate": True}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/projects",
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception as exc:
        log(f"Не удалось зарегистрировать проект в запущенном сервере: {exc}")
        return False


def stop_marker(port: int) -> Path:
    """Маркер остановки из UI (пишет backend/lifecycle.py)."""
    return Path.home() / ".taskboard" / f"stop_{port}.flag"


def dev_supervisor(args, tasks_dir: Path) -> None:
    """
    Dev-режим: свой супервизор вместо uvicorn --reload.

    Следит за taskboard/backend/ и перезапускает сервер-подпроцесс при
    изменениях. uvicorn --reload на Windows в связке с watchdog/SSE
    зависает при перезапуске, поэтому reload реализован снаружи.
    """
    import threading
    import time

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    restart = threading.Event()

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event) -> None:
            if event.is_directory or "__pycache__" in event.src_path:
                return
            if event.src_path.endswith(".py"):
                restart.set()

    child_args = [
        sys.executable, str(Path(__file__).resolve()),
        "--port", str(args.port), "--tasks-dir", str(tasks_dir), "--no-browser",
    ]
    # Маркер для backend/lifecycle.py: сервер под супервизором,
    # перезапуск из UI = просто умереть, спавнить замену не нужно
    child_env = {**os.environ, "TASKBOARD_SUPERVISED": "1"}

    log(f"Dev-режим: слежу за {TOOL_DIR / 'backend'}, перезапуск при изменениях")
    # Сбросить маркер остановки от прошлых сессий: свежий маркер
    # появится только при остановке из UI в этой сессии
    stop_marker(args.port).unlink(missing_ok=True)
    proc = subprocess.Popen(child_args, env=child_env)
    webbrowser.open(f"http://127.0.0.1:{args.port}")

    observer = Observer()
    observer.schedule(_Handler(), str(TOOL_DIR / "backend"), recursive=False)
    observer.daemon = True
    observer.start()

    try:
        while True:
            if restart.wait(timeout=0.5):
                restart.clear()
                time.sleep(0.3)  # дебаунс серии записей
                log("Изменения в backend/ — перезапуск сервера ...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
                proc = subprocess.Popen(child_args, env=child_env)
            if proc.poll() is not None:
                # Маркер — страховка на случай вложенных супервизоров:
                # код выхода видит только прямой родитель, маркер — все
                if proc.returncode == EXIT_STOP or stop_marker(args.port).exists():
                    # Маркер не удаляем: его должны увидеть все уровни
                    # вложенных супервизоров (очистка — при старте супервизора)
                    log("Сервер остановлен из UI — завершаю dev-режим")
                    break
                log("Сервер завершился — перезапуск ...")
                time.sleep(1)
                proc = subprocess.Popen(child_args, env=child_env)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        proc.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Taskboard — фронтенд для tasks/")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tasks-dir", default=None,
                        help="Путь к папке tasks/ (по умолчанию ./tasks)")
    parser.add_argument("--yes", action="store_true",
                        help="Не спрашивать подтверждения при установке")
    parser.add_argument("--dev", action="store_true",
                        help="Режим разработки: авто-перезагрузка при правках backend")
    parser.add_argument("--no-browser", action="store_true",
                        help="Не открывать браузер (служебный, для дочерних процессов)")
    parser.add_argument("--respawn", action="store_true",
                        help="Служебный (перезапуск из UI): ждать освобождения порта перед стартом")
    args = parser.parse_args()

    ensure_python()
    ensure_deps(args.yes)
    ensure_frontend(args.yes)

    # Перезапуск из UI: старый процесс ещё умирает — ждём освобождения порта
    if args.respawn:
        for _ in range(60):
            if server_alive(args.port) is None:
                break
            time.sleep(0.25)

    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else Path.cwd() / "tasks"
    tasks_dir = tasks_dir.resolve()
    if not tasks_dir.is_dir():
        log(f"ВНИМАНИЕ: папка задач не найдена: {tasks_dir}")
        log("Сервер стартует без активного проекта — зарегистрируйте проект в UI.")

    # Если сервер уже жив — регистрируем проект в нём и выходим
    health = server_alive(args.port)
    if health is not None:
        log(f"Сервер уже запущен на порту {args.port}.")
        # Версия запущенного сервера против версии этого кода. Отсутствие ключа
        # `version` — само по себе признак старого сервера: он появился вместе
        # с проверкой обновлений и раньше его не было
        running = health.get("version")
        if running is None or _version_lt(running, local_version()):
            shown = running or "без версии"
            log(f"ВНИМАНИЕ: запущенный сервер старее текущего кода ({shown} "
                f"против {local_version()}).")
            log("Перезапустите сервер: UI → Настройки → «Сервер».")
        # Сервер из другого расположения (напр. старая/удалённая копия)
        other_dir = health.get("tool_dir")
        if other_dir and Path(other_dir).resolve() != ROOT:
            log(f"ВНИМАНИЕ: запущенный сервер работает из ДРУГОЙ папки:")
            log(f"  {other_dir}")
            log(f"  текущая копия: {ROOT}")
            log("Запросы пойдут СТАРОМУ коду! Остановите его: UI → Настройки → "
                "«Остановить», или: lsof -ti:8765 | xargs kill")
        if tasks_dir.is_dir() and register_in_running(args.port, tasks_dir):
            log(f"Проект активирован: {tasks_dir}")
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return

    # Стартуем сервер
    if args.dev:
        dev_supervisor(args, tasks_dir)
        return

    sys.path.insert(0, str(TOOL_DIR))
    import uvicorn
    from backend import registry
    from backend.app import app

    # Порт нужен backend/lifecycle.py для перезапуска из UI
    os.environ["TASKBOARD_PORT"] = str(args.port)

    if tasks_dir.is_dir():
        proj = registry.register_project(tasks_dir, activate=True)
        log(f"Активный проект: {proj['name']} ({tasks_dir})")

    url = f"http://127.0.0.1:{args.port}"
    log(f"Запуск сервера: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
