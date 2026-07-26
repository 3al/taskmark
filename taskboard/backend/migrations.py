"""Миграции данных проекта при изменении конфигурации.

Принцип: любое переименование в настройках не должно оставлять проект
в сломанном состоянии — артефакты проекта мигрируют вслед за конфигом.
"""

from __future__ import annotations

import re
from pathlib import Path


def apply_config_migrations(tasks_dir: Path, old: dict, new: dict) -> list[str]:
    """
    Применить миграции к активному проекту после смены конфига.

    old/new — эффективные конфиги (дефолты + сохранённые) до и после.
    Возвращает список выполненных действий (для показа пользователю).
    """
    actions: list[str] = []
    if not tasks_dir.is_dir():
        return actions

    # Переименования файлов/папок внутри tasks/
    _rename_artifact(tasks_dir, old, new, "board_file", actions)
    _rename_artifact(tasks_dir, old, new, "create_script", actions)
    _rename_artifact(tasks_dir, old, new, "logs_dir", actions)

    # Переименование раздела очереди — заголовок ## в файле доски
    _migrate_queue_section(tasks_dir, new.get("board_file", "board.md"), old, new, actions)

    # Переименование статуса очереди — frontmatter всех файлов задач
    _migrate_queued_status(tasks_dir, old, new, actions)

    return actions


def _rename_artifact(tasks_dir: Path, old: dict, new: dict, key: str, actions: list[str]) -> None:
    """Переименовать файл/папку в tasks/ вслед за настройкой."""
    old_v, new_v = old.get(key), new.get(key)
    if not old_v or not new_v or old_v == new_v:
        return
    src = tasks_dir / old_v
    dst = tasks_dir / new_v
    if src.exists() and not dst.exists():
        src.rename(dst)
        actions.append(f"Переименовано: {old_v} → {new_v}")


def _migrate_queue_section(tasks_dir: Path, board_file: str, old: dict, new: dict, actions: list[str]) -> None:
    """Переименовать заголовок ## <queue_section> в файле доски."""
    old_v, new_v = old.get("queue_section"), new.get("queue_section")
    if not old_v or not new_v or old_v == new_v:
        return
    board = tasks_dir / board_file
    if not board.is_file():
        return
    content = board.read_text(encoding="utf-8")
    old_heading = re.compile(rf"^##\s+{re.escape(old_v)}\s*$", flags=re.MULTILINE)
    new_heading = re.compile(rf"^##\s+{re.escape(new_v)}\s*$", flags=re.MULTILINE)
    if old_heading.search(content) and not new_heading.search(content):
        board.write_text(old_heading.sub(f"## {new_v}", content), encoding="utf-8")
        actions.append(f"Раздел очереди в {board_file}: {old_v} → {new_v}")


def _migrate_queued_status(tasks_dir: Path, old: dict, new: dict, actions: list[str]) -> None:
    """Обновить status в frontmatter всех задач со старым статусом очереди."""
    old_v, new_v = old.get("queued_status"), new.get("queued_status")
    if not old_v or not new_v or old_v == new_v:
        return
    pattern = re.compile(rf"^status:\s*{re.escape(old_v)}\s*$", flags=re.MULTILINE)
    count = 0
    for f in tasks_dir.glob("TASK-*.md"):
        content = f.read_text(encoding="utf-8")
        updated = pattern.sub(f"status: {new_v}", content)
        if updated != content:
            f.write_text(updated, encoding="utf-8")
            count += 1
    if count:
        actions.append(f"Статус {old_v} → {new_v} обновлён в {count} файлах задач")
