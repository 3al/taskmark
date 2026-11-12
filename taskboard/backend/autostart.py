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

try:
    import winreg
except ImportError:  # вне Windows реестра нет
    winreg = None

# Имя записи в автозагрузке. Константа: по нему же запись ищется и удаляется
ENTRY_NAME = "taskboard.cmd"
APPROVAL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
WINDOWS_DISABLED_HINT = (
    "Автозапуск отключён средствами Windows. Установите галочку и нажмите «Сохранить» или "
    "разрешите taskboard в Диспетчере задач → Автозагрузка приложений.")

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
    """Текст записи. Эталон: по нему же устаревшая запись узнаётся и чинится.

    **Рабочую папку задаём сами.** Элементы автозагрузки Проводник запускает из
    `C:\\Windows\\System32`, а лаунчер выводит проект из рабочей папки — и там
    существует `Tasks`, хранилище планировщика заданий. Без `cd` инструмент
    заводил себе проект в системной папке Windows, делал его активным и падал
    на следующем запуске (TASK-233).

    `--yes` обязателен: при входе в систему отвечать на вопросы установки
    некому, а спросить не через что — консоли у процесса нет.
    """
    text = (
        "@echo off\r\n"
        "rem Запуск taskboard при входе в систему. Управляется из настроек\r\n"
        f'cd /d "{root}"\r\n'
        f'start "" "{_python(root)}" "{root / "taskboard.py"}" --no-browser --yes\r\n'
    )
    return text


def _entry_text() -> str:
    """Текст записи; нечитаемая запись — пустая строка.

    Кодировку файла в папке автозагрузки мы не контролируем: его мог положить
    кто угодно. `UnicodeDecodeError` — не `OSError`, и без этой ветки он ушёл бы
    наружу, в обработчик старта сервера.

    Переносы читаются как есть (`newline=""`): иначе `\\r\\n` файла превращается
    в `\\n`, и запись никогда не совпадёт с эталоном — самопочинка переписывала
    бы её при каждом старте. Читаем через `open()`, а не `read_text(newline="")`:
    этот параметр у `read_text` появился только в Python 3.13, а минимум
    проекта — 3.10.
    """
    try:
        with entry_path().open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, ValueError):
        return ""


def _write_entry(text: str) -> None:
    """Записать `.cmd` ровно тем текстом, что дали.

    `newline=""` обязателен: переносы в скрипте уже `\\r\\n`, и без этого
    `write_text` добавляет к ним свой — файл получает `\\r\\r\\n` и перестаёт
    совпадать с эталоном, по которому узнаётся устаревшая запись.
    """
    entry_path().write_text(text, encoding="utf-8", newline="")


def _entry_points_here(root: Path) -> bool:
    """Ведёт ли существующая запись в эту копию Taskmark."""
    return str(root / "taskboard.py") in _entry_text()


def _windows_disabled() -> bool:
    """Отключил ли пользователь запись через настройки Windows.

    StartupApproved хранит состояние отдельно от файла в Startup. DWORD
    2/6 означает разрешение, 3/7 — отключение; следующие 8 байт — время,
    по которому нельзя судить о текущем разрешении. Формат не является
    публичным API Windows: неизвестное состояние показываем как неизвестное,
    а не обещаем успешный автозапуск.
    """
    if winreg is None:
        raise OSError("реестр Windows недоступен")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVAL_KEY,
                            0, winreg.KEY_QUERY_VALUE) as key:
            data, kind = winreg.QueryValueEx(key, ENTRY_NAME)
    except FileNotFoundError:
        return False  # Windows ещё не записывала отдельного решения
    if kind != winreg.REG_BINARY or not isinstance(data, bytes) or len(data) != 12:
        raise OSError("неизвестный формат состояния автозапуска Windows")
    flag = int.from_bytes(data[:4], "little")
    if flag not in (2, 3, 6, 7):
        raise OSError(f"неизвестное состояние автозапуска Windows: {flag}")
    return flag in (3, 7)


def _allow_windows_startup() -> None:
    """Снять только своё отключение и только при явном включении из UI.

    Обновление файла автозагрузки этого не делает: иначе ручной запуск
    инструмента отменял бы выбор пользователя в Диспетчере задач.
    """
    if not _windows_disabled():
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVAL_KEY,
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, ENTRY_NAME)
    except FileNotFoundError:
        pass  # решение уже снято другим процессом


def status(root: Path) -> dict:
    """Состояние автозапуска: есть ли кнопка, включён ли он и где запись.

    `stale` — запись есть, но ведёт в другую папку: репозиторий переехали или
    скопировали. Само по себе это тихая поломка — при входе в систему
    запускалось бы не то или ничего, и человек об этом не узнал бы.
    """
    if not supported():
        return {"supported": False, "enabled": False, "path": "", "stale": False,
                "hint": hint(), "registered": False, "disabled_by_windows": False,
                "warning": ""}
    registered = entry_path().is_file()
    disabled, warning = False, ""
    if registered:
        try:
            disabled = _windows_disabled()
            if disabled:
                warning = WINDOWS_DISABLED_HINT
        except OSError as exc:
            warning = f"Не удалось проверить разрешение автозапуска Windows: {exc}"
    return {"supported": True, "enabled": registered and not warning,
            "registered": registered, "disabled_by_windows": disabled,
            "warning": warning, "path": str(entry_path()),
            "stale": registered and not _entry_points_here(root), "hint": ""}


def enable(root: Path) -> dict:
    """Прописать запуск при входе в систему. Повторный вызов запись перезапишет."""
    if not supported():
        return {"ok": False, "error": hint() or "Автозапуск кнопкой на этой системе не заводится"}
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
        _write_entry(_script(root))
        _allow_windows_startup()
    except OSError as exc:
        return {"ok": False, "error": f"Не удалось включить автозапуск: {exc}"}
    state = status(root)
    if not state["enabled"]:
        return {"ok": False, "error": state["warning"] or "Не удалось включить автозапуск"}
    return {"ok": True, **state}


def refresh_if_outdated(root: Path) -> bool:
    """Привести запись этой копии к эталону. Вызывается при старте сервера.

    Прежние версии писали запись без рабочей папки, и она ведёт в правильную
    копию — то есть `stale` считает её нормальной, а при каждом входе в систему
    она заново заводит проект в системной папке Windows. Такая запись чинится
    сама: своё же поручение, приведённое к своей же форме, — не то решение,
    ради которого будят человека.

    **Запись, ведущая в другую копию, не трогается**: там перезапись меняет
    смысл, а не форму, и об этом спрашивают предупреждением `stale`. Отсутствие
    записи — тоже не случай для починки: автозапуск включает человек.

    Возвращает True, если запись переписана.
    """
    if not supported() or not entry_path().is_file() or not _entry_points_here(root):
        return False
    fresh = _script(root)
    if _entry_text() == fresh:
        return False
    try:
        _write_entry(fresh)
    except OSError:
        return False
    return True


def disable() -> dict:
    """Убрать запись автозагрузки. Её отсутствие — не ошибка."""
    try:
        entry_path().unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"Не удалось убрать автозапуск: {exc}"}
    return {"ok": True, "supported": supported(), "enabled": False,
            "path": str(entry_path()) if supported() else "", "stale": False,
            "hint": "", "registered": False, "disabled_by_windows": False, "warning": ""}
