"""Версия поставки: чтение файла VERSION и сравнение версий.

Модуль намеренно пустой на зависимости: ни git, ни сети, ни конфига. Его читают
и бэкенд, и лаунчер (до создания venv), поэтому импортироваться он должен всегда.

Версия одна на всю поставку — и на бэкенд, и на собранный фронтенд, и на шаблоны
агентского окружения. Разводить их по отдельным номерам смысла нет: пользователь
получает дерево целиком одним обновлением.
"""

from __future__ import annotations

import re
from pathlib import Path

# VERSION лежит рядом с пакетом backend: taskboard/VERSION
VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

UNKNOWN = "0.0.0"

_SEGMENT = re.compile(r"^\d+$")


def parse(value: str) -> tuple[int, ...]:
    """Строка версии → кортеж чисел для сравнения.

    Ведущая «v» отбрасывается: в теге она есть (`v1.4.0`), в файле VERSION её нет,
    а сравнивать приходится одно с другим. Мусор — ValueError: молча считать
    непонятную строку нулевой версией опаснее, чем не ответить вовсе.
    """
    text = (value or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    if not text:
        raise ValueError("пустая строка версии")
    parts = text.split(".")
    if not all(_SEGMENT.match(p) for p in parts):
        raise ValueError(f"не похоже на версию: {value!r}")
    return tuple(int(p) for p in parts)


def is_valid(value: str) -> bool:
    """Разбирается ли строка как версия."""
    try:
        parse(value)
    except (ValueError, TypeError):
        return False
    return True


def compare(a: str, b: str) -> int:
    """-1 если a < b, 0 если равны, 1 если a > b.

    Разная длина не мешает: недостающие сегменты считаются нулями, поэтому
    «1.2» и «1.2.0» — одна и та же версия.
    """
    left, right = parse(a), parse(b)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return (left > right) - (left < right)


def current() -> str:
    """Версия этой копии инструмента.

    Файла нет или он испорчен — возвращаем UNKNOWN, а не падаем: отсутствие
    версии не повод не запускать доску. Для вызывающего это выглядит как
    «очень старая версия», что и требуется.
    """
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return UNKNOWN
    return text if is_valid(text) else UNKNOWN
