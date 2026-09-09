"""Безопасные HTML-блоки единого визуального языка Telegram-канала.

Telegram не даёт управлять гарнитурой и размером шрифта. Иерархию поэтому
строим одинаково во всех ответах: эмодзи состояния, жирный заголовок, задача с
моноширинным номером, подписанные поля и отдельная строка адресатов.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

PARSE_MODE = "HTML"
# У Telegram предел 4096 символов. Запас оставляет место на различие способа
# подсчёта emoji и будущие небольшие элементы оформления.
MESSAGE_LIMIT = 4000


def safe(value, limit: int) -> str:
    """Экранировать динамический текст и обрезать, не разрывая HTML-сущность."""
    raw = str(value or "")
    pieces: list[str] = []
    used = 0
    clipped = False
    room = max(0, limit - 1)
    for char in raw:
        piece = html.escape(char, quote=False)
        if used + len(piece) > room:
            clipped = True
            break
        pieces.append(piece)
        used += len(piece)
    if clipped:
        pieces.append("…")
    return "".join(pieces)


def one_line(value) -> str:
    """Свернуть пользовательское значение для компактной строки карточки."""
    return " ".join(str(value or "").split())


def heading(icon: str, title, continuation: bool = False) -> str:
    suffix = " · продолжение" if continuation else ""
    return f"{safe(icon, 8)} <b>{safe(one_line(title), 180)}</b>{suffix}"


def task_line(task_id, title) -> str:
    return (f"<code>{safe(one_line(task_id), 60)}</code> · "
            f"{safe(one_line(title), 600)}")


def field(label, value) -> str:
    return f"<b>{safe(one_line(label), 80)}:</b> {safe(one_line(value), 320)}"


def mentions_line(mentions: Iterable[str]) -> str:
    return safe(" ".join(one_line(item) for item in mentions if one_line(item)), 400)


def card(icon: str, title, *, body="", task_id="", task_title="",
         fields: Iterable[tuple[str, object]] = (),
         mentions: Iterable[str] = ()) -> str:
    """Собрать одну компактную карточку; все аргументы считаются динамическими."""
    lines = [heading(icon, title)]
    if task_id or task_title:
        lines.extend(("", task_line(task_id, task_title)))
    if body:
        lines.extend(("", safe(str(body).strip(), 1000)))
    for label, value in list(fields)[:3]:
        lines.append(field(label, value))
    addressed = mentions_line(mentions)
    if addressed:
        lines.extend(("", addressed))
    text = "\n".join(lines)
    # Бюджеты выше удерживают штатные карточки ниже границы. Этот рубеж нужен
    # на случай большого числа полей при будущем переиспользовании.
    return text if len(text) <= MESSAGE_LIMIT else safe(text, MESSAGE_LIMIT)
