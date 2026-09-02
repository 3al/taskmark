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
import re
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


# Обмен с сервером при обновлении по кнопке. Пути — магические константы,
# дублируются в backend/updater.py и должны оставаться синхронными: лаунчер
# не может импортировать backend, потому что git-операция идёт ДО импорта
UPDATE_DIR = Path.home() / ".taskboard"
UPDATE_REQUEST = UPDATE_DIR / "update_apply.json"
UPDATE_RESULT = UPDATE_DIR / "update_result.json"

# Имя релизного тега и ничего кроме (зеркало updater.TAG_RE): тег приходит
# по сети и попадает в командную строку git
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def log(msg: str) -> None:
    print(f"[taskboard] {msg}")


def fail(msg: str) -> None:
    print(f"[taskboard] ОШИБКА: {msg}")
    sys.exit(1)


def local_version(root: Path | None = None) -> str:
    """Версия копии инструмента (файл taskboard/VERSION).

    Читается напрямую, без импорта backend: лаунчер работает до создания venv,
    когда зависимостей ещё нет. Логика та же, что в backend/version.py.
    `root` — корень копии; по умолчанию своя (нужен обновлению, которое
    проверяет версию в только что обновлённом дереве).
    """
    base = (root or ROOT) / "taskboard"
    try:
        text = (base / "VERSION").read_text(encoding="utf-8").strip()
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


# --- Применение обновления --------------------------------------------------
# Живой сервер раздаёт frontend/dist из той самой папки, которую перезаписывает
# git, и держит импортированный backend в памяти. Поэтому обновление применяет
# отдельный процесс при уже остановленном сервере и ДО импорта backend.


def _git(root: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def _pip_install() -> bool:
    """Доустановить зависимости новой версии: requirements.txt мог смениться."""
    out = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.returncode == 0


def apply_update(root: Path, request: dict, install_deps=_pip_install,
                 result_file: Path | None = None) -> dict:
    """Выполнить обновление: fetch → fast-forward на тег → зависимости → проверка.

    `request` — что записал сервер перед выходом: `tag`, `version`, `head`.
    Возвращает итог и кладёт его в файл, чтобы поднявшийся сервер показал
    пользователю, чем всё кончилось.

    Провал на любом шаге возвращает код к записанному HEAD. Зависимости при
    этом откату не подлежат: requirements.txt не припинен, и переустановка
    старого файла поставит те же новые версии — обещать транзакцию нечестно.
    """
    tag = str(request.get("tag") or "")
    target = str(request.get("version") or "")
    head = str(request.get("head") or "")

    def done(ok: bool, error: str = "") -> dict:
        result = {"ok": ok, "version": local_version(root) if ok else target,
                  "target": target, "tag": tag, "error": error,
                  # С какой версии ушли: по ней окно покажет всё пропущенное
                  "from": str(request.get("from") or ""),
                  "at": time.time()}
        path = result_file if result_file is not None else UPDATE_RESULT
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError:
            pass
        return result

    def rollback(reason: str) -> dict:
        if head:
            _git(root, "reset", "--hard", head)
        return done(False, f"{reason}. Код возвращён к прежней версии, "
                           f"но установленные зависимости откату не подлежат")

    # Тег пришёл по сети: проверяем как данные, до любого вызова git
    if not TAG_RE.match(tag):
        return done(False, f"Тег не похож на релизный: {tag!r}")
    if not head:
        return done(False, "Не записан HEAD — откатывать было бы некуда")

    # Remote берётся из локального репозитория, а не из манифеста: обновляемся
    # только оттуда, откуда клонировались
    try:
        remotes = _git(root, "remote").stdout.split()
    except (OSError, subprocess.SubprocessError) as exc:
        return done(False, f"git недоступен: {exc}")
    if not remotes:
        return done(False, "У репозитория нет remote — неоткуда получать обновление")
    remote = "origin" if "origin" in remotes else remotes[0]

    log(f"Обновление до {target}: получаю {tag} из {remote} ...")
    fetched = _git(root, "fetch", remote, "main", "--tags")
    if fetched.returncode != 0:
        return done(False, f"Не удалось получить обновление: {fetched.stderr.strip()}")

    ref = f"refs/tags/{tag}"
    if _git(root, "rev-parse", "-q", "--verify", ref).returncode != 0:
        return done(False, f"Тега {tag} нет в {remote} — обновляться не на что")

    # Потомок HEAD? merge --ff-only это и обеспечит, но отказ должен быть внятным
    if _git(root, "merge-base", "--is-ancestor", "HEAD", ref).returncode != 0:
        return done(False, f"{tag} не является продолжением вашей истории: "
                           f"есть локальные коммиты или другая ветка")

    merged = _git(root, "merge", "--ff-only", ref)
    if merged.returncode != 0:
        return done(False, f"Обновление не применилось: {merged.stderr.strip()}")

    if not install_deps():
        return rollback("Не удалось установить зависимости новой версии")

    # Верификация: стартовать половину обновления хуже, чем не обновиться
    if local_version(root) != target:
        return rollback(f"После обновления версия {local_version(root)}, "
                        f"а ожидалась {target}")
    if not (root / "taskboard" / "frontend" / "dist" / "index.html").is_file():
        return rollback("В новой версии нет собранного интерфейса")

    log(f"Обновление применено: {target}")
    return done(True)


def read_update_request() -> dict:
    """Что просил применить сервер перед выходом (и сразу забыть просьбу)."""
    try:
        data = json.loads(UPDATE_REQUEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        UPDATE_REQUEST.unlink()
    except OSError:
        pass
    return data if isinstance(data, dict) else {}


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


def log_file(port: int) -> Path:
    return UPDATE_DIR / f"server_{port}.log"


def ensure_log_stream(port: int) -> None:
    """Дать процессу без консоли, куда писать.

    Автозапуск идёт через `pythonw`, у которого нет ни stdout, ни stderr:
    `sys.stdout` равен `None`. Свои сообщения лаунчер при этом просто теряет,
    но хуже другое — `uvicorn.run` на старте падает, его конфиг логирования
    ссылается на `ext://sys.stdout`, и `dictConfig` на `None` бросает
    `ValueError`. Без файла сервер при входе в систему не поднимался вовсе, и
    узнать об этом было неоткуда: сообщать не через что (TASK-233).
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        path = log_file(port)
        # Лог пишется всю жизнь машины — подрезаем, чтобы не рос без края
        if path.exists() and path.stat().st_size > 1_000_000:
            path.unlink()
        stream = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return  # писать некуда — молча, иначе некуда и жаловаться
    sys.stdout = sys.stderr = stream
    log(f"--- запуск без консоли ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")


def resolve_tasks_dir(explicit: str | None, cwd: Path) -> tuple[Path, str]:
    """Папка задач и причина, по которой её нельзя брать активным проектом.

    Причина пустая — можно. `missing` — папки нет. `not_project` — папка есть,
    но нашего в ней ничего: ни доски, ни конфига проекта.

    **Рабочая папка — догадка, а не поручение.** Папка с именем `tasks`
    встречается где угодно, и одна из них — `C:\\Windows\\System32\\Tasks`,
    хранилище планировщика заданий: запись автозагрузки не задавала рабочую
    папку, Проводник запускал лаунчер из `System32`, и инструмент заводил себе
    активный проект в системной папке Windows (TASK-233).

    **Явный `--tasks-dir` не проверяется**: путь назвал человек, и развернуть
    структуру в пустую папку он вправе.
    """
    path = (Path(explicit) if explicit else cwd / "tasks").resolve()
    if not path.is_dir():
        return path, "missing"
    if explicit:
        return path, ""

    sys.path.insert(0, str(TOOL_DIR))
    from backend.fs_browse import looks_like_project

    return path, "" if looks_like_project(path) else "not_project"


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
    parser.add_argument("--apply-update", action="store_true",
                        help="Служебный (обновление из UI): применить обновление и стартовать")
    args = parser.parse_args()

    # Первым делом: без этого запуск без консоли (автозапуск через pythonw)
    # не сможет ни сказать о себе, ни поднять uvicorn
    ensure_log_stream(args.port)

    ensure_python()
    ensure_deps(args.yes)

    # Перезапуск из UI: старый процесс ещё умирает — ждём освобождения порта
    if args.respawn or args.apply_update:
        for _ in range(60):
            if server_alive(args.port) is None:
                break
            time.sleep(0.25)

    # Обновление применяется здесь: сервер уже вышел, backend ещё не импортирован
    if args.apply_update:
        request = read_update_request()
        if request:
            result = apply_update(ROOT, request)
            if not result["ok"]:
                log(f"Обновление не применено: {result['error']}")
        else:
            log("Обновление отменено: запрос не найден")

    ensure_frontend(args.yes)

    tasks_dir, refusal = resolve_tasks_dir(args.tasks_dir, Path.cwd())
    if refusal == "missing":
        log(f"ВНИМАНИЕ: папка задач не найдена: {tasks_dir}")
    elif refusal == "not_project":
        log(f"ВНИМАНИЕ: это не проект Taskmark — в папке задач нет доски: {tasks_dir}")
    if refusal:
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
        if not refusal and register_in_running(args.port, tasks_dir):
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

    if not refusal:
        proj = registry.register_project(tasks_dir, activate=True)
        log(f"Активный проект: {proj['name']} ({tasks_dir})")

    url = f"http://127.0.0.1:{args.port}"
    log(f"Запуск сервера: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
