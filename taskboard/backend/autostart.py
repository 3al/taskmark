"""Запуск Taskmark при входе в систему.

Инструмент локальный: пока он не запущен, не работает ничего фонового — ни
проверка обновлений, ни задачи из чата. У бота невычитанные сообщения живут
около суток, и без автозапуска интеграция начинает молча терять задачи.

**Кнопка, а не инструкция.** Инструкция упирается в готовность человека её
выполнить, а отказ при этом тихий: он просто не заметит, что чего-то не
получил. Кнопкой закрыт Windows; на macOS и Linux автозагрузка регистрируется
по-разному, и вместо неработающей кнопки там показывается, что сделать руками.

Запись автозагрузки — обычный `.cmd`: файл, а не ярлык, чтобы обойтись без COM
и PowerShell. Запуск идёт через `pythonw`, поэтому консоль сервера не висит на
экране; мелькание окна самого `.cmd` при входе в систему — цена простоты.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Имя записи в автозагрузке. Константа: по нему же запись ищется и удаляется
ENTRY_NAME = "taskboard.cmd"

LINUX_HINT = ("Автозапуск на Linux заводится юнитом systemd: положите в "
              "~/.config/systemd/user/taskboard.service запуск "
              "«python3 <путь>/taskboard.py --no-browser» и включите его "
              "командой systemctl --user enable --now taskboard")
MACOS_HINT = ("Автозапуск на macOS заводится агентом launchd: положите в "
              "~/Library/LaunchAgents/taskboard.plist запуск "
              "«python3 <путь>/taskboard.py --no-browser» и включите его "
              "командой launchctl load ~/Library/LaunchAgents/taskboard.plist")


def supported() -> bool:
    """Есть ли на этой платформе кнопка. Вынесено, чтобы тесты не зависели от ОС."""
    return sys.platform == "win32"


def hint() -> str:
    """Что делать там, где кнопки нет."""
    if sys.platform == "darwin":
        return MACOS_HINT
    if sys.platform.startswith("linux"):
        return LINUX_HINT
    return ""


def startup_dir() -> Path:
    """Папка автозагрузки текущего пользователя (Windows)."""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup")


def entry_path() -> Path:
    return startup_dir() / ENTRY_NAME


def _python(root: Path) -> Path:
    """Чем запускать. `pythonw` из venv — чтобы не висело окно консоли.

    Venv может не существовать (снесли, ещё не создавали) — тогда зовём
    интерпретатор из PATH: лаунчер сам создаст окружение и перезапустится.
    """
    venv = root / "taskboard" / ".venv" / "Scripts" / "pythonw.exe"
    return venv if venv.is_file() else Path("pythonw")


def _script(root: Path) -> str:
    text = (
        "@echo off\r\n"
        "rem Запуск taskboard при входе в систему. Управляется из настроек\r\n"
        f'start "" "{_python(root)}" "{root / "taskboard.py"}" --no-browser\r\n'
    )
    return text


def _entry_points_here(root: Path) -> bool:
    """Ведёт ли существующая запись в эту копию Taskmark."""
    try:
        return str(root / "taskboard.py") in entry_path().read_text(encoding="utf-8")
    except OSError:
        return False


def status(root: Path) -> dict:
    """Состояние автозапуска: есть ли кнопка, включён ли он и где запись.

    `stale` — запись есть, но ведёт в другую папку: репозиторий переехали или
    скопировали. Само по себе это тихая поломка — при входе в систему
    запускалось бы не то или ничего, и человек об этом не узнал бы.
    """
    if not supported():
        return {"supported": False, "enabled": False, "path": "", "stale": False,
                "hint": hint()}
    enabled = entry_path().is_file()
    return {"supported": True, "enabled": enabled, "path": str(entry_path()),
            "stale": enabled and not _entry_points_here(root), "hint": ""}


def enable(root: Path) -> dict:
    """Прописать запуск при входе в систему. Повторный вызов запись перезапишет."""
    if not supported():
        return {"ok": False, "error": hint() or "Автозапуск кнопкой на этой системе не заводится"}
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
        entry_path().write_text(_script(root), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"Не удалось записать автозапуск: {exc}"}
    return {"ok": True, **status(root)}


def disable() -> dict:
    """Убрать запись автозагрузки. Её отсутствие — не ошибка."""
    try:
        entry_path().unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"Не удалось убрать автозапуск: {exc}"}
    return {"ok": True, "supported": supported(), "enabled": False,
            "path": str(entry_path()) if supported() else "", "stale": False,
            "hint": ""}
