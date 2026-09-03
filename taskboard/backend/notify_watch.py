"""Уведомления о движении задачи по статусам.

**Смену статуса ловим сравнением снимков, а не перехватом вызова.** Статус
меняют тремя путями — доской, автономным `set_status.py` и рукой в файле, — и
два из них бэкенд не видит вовсе. Снимок делает все три неразличимыми: важно не
то, кто подвинул задачу, а то, что она подвинулась.

Снимок строится по `board.md`: один файл на проект вместо сотен файлов задач.
Файл задачи открывается только для той, что переехала, — за происхождением,
заголовком и автором.

Проход живёт в цикле опроса чата (`telegram_source.start_polling`), а не на
watchdog: тот следит только за **активным** проектом, а задачи из чата приходят
в любой привязанный.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import notify_targets, registry, telegram_notify, telegram_source
from .board_parser import parse_board
from .config import load_project_config
from .statuses import load_pipeline
from .task_parser import parse_task


def _enabled(cfg: dict) -> bool:
    """Есть ли кому и куда писать вообще.

    Без включённой интеграции, без токена и без единого привязанного чата
    уведомлений не существует: настройка не доведена до конца, и слать некуда.
    """
    return bool(telegram_source.enabled(cfg) and (cfg.get("telegram_chats") or {}))


def _snapshot(tasks_dir: Path, pipeline) -> dict:
    """`{TASK-NNN: раздел доски}` — состояние проекта одним чтением."""
    board = tasks_dir / "board.md"
    if not board.is_file():
        return {}
    try:
        parsed = parse_board(board, pipeline)
    except Exception:  # noqa: BLE001 — доску правят руками; битую переживаем молча
        return {}
    found: dict[str, str] = {}
    for column in parsed.get("columns", []):
        for group in column.get("groups", []):
            for task in group.get("tasks", []):
                task_id = str(task.get("id") or "").strip()
                if task_id:
                    found[task_id] = str(column.get("title") or "")
    return found


def _message(task_id: str, title: str, was: str, now: str,
             mentions: list[str]) -> str:
    """Что человек прочитает в чате.

    Заголовок повторяется намеренно: номер задачи ничего не говорит тому, кто
    её принёс месяц назад. Теги — в конце, чтобы сообщение читалось как фраза,
    а не начиналось с обращения.
    """
    line = f"{task_id} · {title} · {was} → {now}"
    return f"{line}\n{' '.join(mentions)}" if mentions else line


def check_project(tasks_dir: Path, cfg: dict, project_cfg: dict, state: dict,
                  send: Callable | None = None) -> int:
    """Один проход по проекту. Возвращает число отправленных уведомлений.

    Снимок сдвигается **всегда** — даже когда отправка не удалась и когда
    возможность выключена. Иначе выключенная интеграция копила бы «долг»
    движений, а упавшая отправка повторяла бы одно сообщение на каждом проходе.
    """
    tasks_dir = Path(tasks_dir)
    pipeline = load_pipeline(project_cfg)
    current = _snapshot(tasks_dir, pipeline)
    if not current:
        return 0
    key = str(tasks_dir)
    before = state.get(key)
    state[key] = current
    if before is None:
        # Первый проход после запуска: снимок берётся молча, иначе старт
        # сервера рассылал бы в чат всю доску
        return 0
    if not _enabled(cfg):
        return 0

    notified = {s["key"]: s for s in pipeline.statuses() if s.get("notify")}
    sections = {str(s.get("section") or s.get("label") or s["key"]): s["key"]
                for s in pipeline.statuses()}
    reply = send or telegram_source.send_message
    sent = 0
    for task_id, now in current.items():
        was = before.get(task_id)
        if was is None or was == now:
            continue
        if sections.get(now) not in notified:
            continue
        meta = (parse_task(tasks_dir, task_id) or {}).get("meta") or {}
        targets = telegram_notify.targets(meta, cfg)
        if not targets:
            continue
        text = _message(task_id, str(meta.get("title") or task_id), was, now,
                        targets["mentions"])
        try:
            if send is not None:
                reply(targets["chat_id"], text)
            else:
                reply(telegram_source.token(cfg), targets["chat_id"], text,
                      proxy=telegram_source.proxy(cfg),
                      api_root=telegram_source.api_root(cfg))
            sent += 1
        except Exception:  # noqa: BLE001 — сеть, отказ API: снимок уже сдвинут
            pass
    return sent


def check_all(cfg: dict, state: dict, projects: list[dict] | None = None) -> int:
    """Проход по всем проектам реестра. Зовётся из цикла опроса чата."""
    if projects is None:
        projects = registry.list_projects().get("projects", [])
    total = 0
    for project in projects:
        tasks_dir = Path(str(project.get("tasks_dir") or ""))
        if not tasks_dir.is_dir():
            continue
        try:
            total += check_project(tasks_dir, cfg, load_project_config(tasks_dir),
                                   state)
        except Exception:  # noqa: BLE001 — один битый проект не должен
            continue      # останавливать остальные
    return total
