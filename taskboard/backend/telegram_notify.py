"""Чат как канал уведомлений: кого тегать и в какой чат писать.

Канал отвечает на три вопроса, которые общая часть (`notify_targets`) не задаёт
и знать не должна: **его ли это задача**, **кого из причастных он умеет
достать** и **куда писать**.

Охват — задачи, заведённые через чат: уведомления это обратная связь по ним, а
заведённые в окне доски и агентом человек и так видит там, где работает.

**Адрес берётся из самой задачи**, из отметки происхождения, а не обратным
поиском по привязке «чат → проекты». Привязка отвечает на другой вопрос — куда
класть новую задачу, — и обратный ход по ней разослал бы уведомление во все
чаты проекта, включая те, где эту задачу никто не заводил. Цена решения:
задачи, заведённые до появления отметки, уведомлений не получают — молчание
здесь честнее письма не по адресу.
"""

from __future__ import annotations

from . import notify_targets, telegram_source

# Метка канала в поле `origin` задачи. Значение непрозрачно для общей части:
# записывает и разбирает его один и тот же канал
KIND = "telegram"


def origin_of(chat_id) -> str:
    """Отметка происхождения для задачи, заведённой из этого чата."""
    return f"{KIND}:{chat_id}"


def chat_of(meta: dict) -> int | None:
    """Чат, из которого пришла задача. `None` — задача не наша.

    Битую отметку (пустой или нечисловой id) считаем чужой: писать наугад
    некуда, а падать на файле задачи, который правят руками, незачем.
    """
    raw = notify_targets.origin(meta)
    kind, _, value = raw.partition(":")
    if kind != KIND or not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _mention(value: str) -> str:
    """Ник — единственное, чем можно тегнуть в чате.

    Автор задачи бывает и не ником: у заведённой в окне доски там «доска», у
    агентской — имя модели, у пришедшей из чата без ника — отображаемое имя.
    Тегать их нечем, и попытка превратить имя в тег дала бы обращение к
    случайному человеку.
    """
    name = value.strip()
    return name.lower() if name.startswith("@") else ""


def targets(meta: dict, cfg: dict) -> dict | None:
    """Кому и куда писать по задаче. `None` — каналу тут делать нечего.

    Хозяин доски идёт первым: это его доска и его инструмент. Совпадение
    хозяина с автором даёт **один** тег — два подряд выглядят ошибкой бота.
    """
    if not telegram_source.enabled(cfg):
        return None
    chat_id = chat_of(meta)
    if chat_id is None:
        return None
    owner = str(cfg.get("telegram_username") or "").strip().lstrip("@")
    mentions: list[str] = []
    for person in notify_targets.people(meta):
        if person["role"] == notify_targets.OWNER:
            tag = f"@{owner}".lower() if owner else ""
        else:
            tag = _mention(person.get("value", ""))
        if tag and tag not in mentions:
            mentions.append(tag)
    return {"chat_id": chat_id, "mentions": mentions}
