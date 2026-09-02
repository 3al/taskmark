"""Обзор папок для выбора корня проекта.

Путь к проекту вводился строкой не по лени интерфейса: абсолютного пути браузер
не отдаёт — ни при выборе папки, ни при перетаскивании. Значит показать
файловую систему может только тот, кто на ней стоит, — сервер. Отсюда и объём
знания здесь: **только имена папок**, без файлов и без содержимого.
"""

from __future__ import annotations

import os
import string
import sys
from pathlib import Path

from backend.config import DEFAULTS

# Признаки проекта внутри папки задач. Имена — константы поставки (DEFAULTS),
# одинаковые во всех проектах
TASKS_DIR_NAME = DEFAULTS["tasks_dir"]
BOARD_FILE = DEFAULTS["board_file"]
PROJECT_CONFIG = ".taskboard.json"


def _drives() -> list[str]:
    """Корни дисков — только на Windows: «вверх» упирается в корень диска.

    На остальных системах дерево одно, и список был бы пересказом `/`.
    """
    if sys.platform != "win32":
        return []
    return [f"{letter}:\\" for letter in string.ascii_uppercase
            if Path(f"{letter}:\\").is_dir()]


# Атрибуты Windows: скрытая и системная папка. В корне диска иначе первыми
# идут `$RECYCLE.BIN` и `System Volume Information` — шум там, где человек
# ищет свой проект
_HIDDEN = 0x2
_SYSTEM = 0x4


def _is_hidden(item) -> bool:
    """Скрытая папка: точка в начале имени или атрибут файловой системы."""
    if item.name.startswith("."):
        return True
    try:
        attrs = item.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False  # не Windows либо запись исчезла — судить не по чему
    return bool(attrs & (_HIDDEN | _SYSTEM))


def readable(path: Path) -> bool:
    """Пускают ли нас внутрь папки.

    `is_dir()` на закрытой папке говорит «да», и дальше всё разваливается по
    очереди: доски не видно, наблюдатель получает отказ. Спросить прямо — один
    вызов, и случай перестаёт притворяться пустым проектом.
    """
    try:
        with os.scandir(path):
            return True
    except OSError:
        return False


def looks_like_project(tasks_dir: Path) -> bool:
    """Проект — это доска внутри папки задач, а не сама папка `tasks`.

    Папка с таким именем встречается где угодно: домен волта, каталог исходников,
    чужой репозиторий, хранилище планировщика заданий Windows. Метка на них —
    ложное обещание. Доска или конфиг проекта врут гораздо реже: их кладёт сюда
    сам инструмент.
    """
    try:
        return ((tasks_dir / BOARD_FILE).is_file()
                or (tasks_dir / PROJECT_CONFIG).is_file())
    except OSError:
        return False


def _is_project(path: Path) -> bool:
    """То же про корень проекта: обзор папок показывает метку именно на нём."""
    return looks_like_project(path / TASKS_DIR_NAME)


def browse_dir(path: str | None) -> dict:
    """Подпапки каталога: `{ok, path, parent, entries, drives}`.

    Пустой путь ведёт в домашнюю папку — проект человека почти всегда рядом с
    ней, а от корня диска до него кликов больше.

    Отказ не пятисотка, а ответ: несуществующий путь, файл вместо папки и
    закрытый доступ — обычные исходы обхода чужой файловой системы, и человеку
    о них говорят строкой в том же окне.
    """
    target = Path(path).expanduser() if str(path or "").strip() else Path.home()
    try:
        target = target.resolve()
    except OSError:
        pass

    if not target.exists():
        return _error(target, f"Папки нет: {target}")
    if not target.is_dir():
        return _error(target, f"Это файл, а не папка: {target}")

    entries: list[dict] = []
    try:
        with os.scandir(target) as it:
            for item in it:
                # Служебные папки корнем проекта не бывают и только засоряют
                # список
                if _is_hidden(item):
                    continue
                try:
                    if not item.is_dir():
                        continue
                except OSError:
                    # Битая ссылка или исчезнувшая за время обхода запись
                    continue
                child = Path(item.path)
                entries.append({"name": item.name, "path": str(child),
                                "project": _is_project(child)})
    except PermissionError:
        return _error(target, f"Нет доступа к папке: {target}")
    except OSError as e:
        return _error(target, f"Не удалось прочитать папку: {e}")

    entries.sort(key=lambda e: e["name"].lower())
    parent = target.parent
    return {"ok": True, "path": str(target),
            # У корня родителя нет: `Path('C:/').parent` — тот же корень, и
            # кнопка «вверх» топталась бы на месте
            "parent": None if parent == target else str(parent),
            "entries": entries, "drives": _drives()}


def _error(target: Path, message: str) -> dict:
    """Отказ той же формы, что и удачный ответ: окну нечего ветвить."""
    return {"ok": False, "error": message, "path": str(target),
            "parent": None if target.parent == target else str(target.parent),
            "entries": [], "drives": _drives()}
