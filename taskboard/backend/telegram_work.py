"""Список незавершённой работы по запросу из Telegram-чата.

Доски участников локальны: каждый персональный бот отвечает только за своего
хозяина и читает только проекты, привязанные к текущему чату. Поэтому запрос с
несколькими никами даёт по одному ответу от каждого тегнутого бота, а не
пытается собрать чужие файлы на одной машине.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import telegram_messages, telegram_notify
from .board_parser import parse_board
from .config import load_project_config
from .statuses import is_terminal, load_pipeline
from .task_parser import due_left, parse_task

WORK_TAG = "работа"
MESSAGE_LIMIT = telegram_messages.MESSAGE_LIMIT

_WORK_TAG = re.compile(r"(?<!\w)#работа(?![\w-])", re.IGNORECASE)
# Для маршрутизации важен сам факт адресации, а не валидность Telegram-ника.
# Иначе `@ник` на кириллице или короткое `@я` исчезают из разбора, и запрос
# опасно превращается в другую команду — `#работа` без адресата.
_MENTION = re.compile(r"@(\w+)")


def parse(text: str) -> dict | None:
    """Распознать `#работа` и вернуть ники без повторов."""
    if not text or not _WORK_TAG.search(text):
        return None
    mentions: list[str] = []
    for name in _MENTION.findall(text):
        name = name.lower()
        if name not in mentions:
            mentions.append(name)
    return {"mentions": mentions}


def is_for_me(parsed: dict | None, cfg: dict) -> bool:
    """Должен ли этот персональный бот отвечать на запрос.

    С никами отвечают только названные участники. Без ников отвечают все боты:
    каждый возвращает свою локальную часть задач, поставленных автором запроса.
    """
    if parsed is None:
        return False
    me = str(cfg.get("telegram_username") or "").strip().lstrip("@").lower()
    if not me:
        return False
    mentions = parsed.get("mentions") or []
    return not mentions or me in mentions


def collect(projects: list[dict], chat_id, author: str = "",
            today: date | None = None) -> list[dict]:
    """Собрать незавершённые задачи текущего чата в порядке досок.

    `author` задан для формы без ников: тогда остаются задачи, которые поставил
    автор запроса. `origin`, а не рубрика или имя автора, ограничивает выборку
    текущим чатом.
    """
    found: list[dict] = []
    today = today or date.today()
    for project in projects:
        tasks_dir = Path(str(project.get("tasks_dir") or ""))
        board_path = tasks_dir / "board.md"
        if not board_path.is_file():
            continue
        project_cfg = load_project_config(tasks_dir)
        pipeline = load_pipeline(project_cfg)
        board = parse_board(board_path, pipeline)
        for column in board.get("columns", []):
            status = str(column.get("status") or "")
            if is_terminal(pipeline, status):
                continue
            for group in column.get("groups", []):
                for task in group.get("tasks", []):
                    task_id = str(task.get("id") or "").strip()
                    parsed_task = parse_task(tasks_dir, task_id) if task_id else None
                    meta = (parsed_task or {}).get("meta") or {}
                    if telegram_notify.chat_of(meta) != _chat_id(chat_id):
                        continue
                    if author and not _same_person(meta.get("author"), author):
                        continue
                    due = str(meta.get("due") or "").strip()
                    found.append({
                        "id": task_id,
                        "title": str(meta.get("title") or task.get("title") or task_id),
                        "status": status,
                        "status_label": pipeline.label_of(status),
                        "project": str(project.get("name") or tasks_dir.parent.name),
                        "due": due,
                        "due_left": due_left(due, today),
                        "assigned": str(meta.get("created") or "").strip(),
                    })
    return found


def _chat_id(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _same_person(left, right) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def format_messages(tasks: list[dict], owner: str, by_author: bool = False,
                    limit: int = MESSAGE_LIMIT) -> list[str]:
    """Сформировать безопасный HTML и разбить длинный список по задачам."""
    owner = "@" + str(owner or "").strip().lstrip("@")
    subject = f"Назначено вами → {owner}" if by_author else f"Работа {owner}"
    heading = telegram_messages.heading("📋", subject)
    if not tasks:
        return [f"{heading}\n\nНезавершённых задач нет."]

    blocks = [_task_block(task) for task in tasks]
    messages: list[str] = []
    current = f"{heading}\nНезавершённых задач: {len(tasks)}"
    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue
        messages.append(current)
        current = f"{telegram_messages.heading('📋', subject, continuation=True)}\n\n{block}"
    messages.append(current)
    return messages


def _task_block(task: dict) -> str:
    task_line = telegram_messages.task_line(
        _clip(task.get("id"), 30), _clip(task.get("title"), 300))
    status = telegram_messages.field(
        "Статус", _clip(task.get("status_label") or task.get("status"), 100))
    due = telegram_messages.field(
        "Срок", _due_text(task.get("due"), task.get("due_left")))
    assigned = telegram_messages.field(
        "Назначена", _date_text(task.get("assigned")))
    project = telegram_messages.field("Проект", f"«{_clip(task.get('project'), 120)}»")
    return f"▫️ {task_line}\n{status}\n{due}\n{assigned}\n{project}"


def _clip(value, size: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def _date_text(value) -> str:
    raw = str(value or "").strip()[:10]
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return "дата неизвестна"
    return parsed.strftime("%d.%m.%Y")


def _due_text(value, left) -> str:
    raw = str(value or "").strip()
    shown = _date_text(raw)
    if left is None:
        return "не задан" if not raw or raw == "~" else f"не разобран ({raw})"
    if left == 0:
        return f"сегодня · {shown}"
    days = _days(abs(int(left)))
    if left < 0:
        return f"просрочено на {abs(int(left))} {days} · {shown}"
    return f"осталось {int(left)} {days} · {shown}"


def _days(value: int) -> str:
    last, teen = value % 10, 11 <= value % 100 <= 14
    if not teen and last == 1:
        return "день"
    if not teen and 2 <= last <= 4:
        return "дня"
    return "дней"
