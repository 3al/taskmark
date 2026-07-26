"""Парсинг файлов задач: frontmatter + markdown-тело."""

from __future__ import annotations

import re
from pathlib import Path

_TASK_FILE_RE = re.compile(r"^TASK-\d+.*\.md$")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Разобрать YAML-lite frontmatter (key: value между --- строками).

    Возвращает (meta, body). При отсутствии frontmatter — ({}, весь текст).
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end < 0:
        return {}, content

    meta: dict[str, str] = {}
    for line in content[3:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    body = content[end + 4:].lstrip("\n")
    return meta, body


def find_task_file(tasks_dir: Path, task_id: str) -> Path | None:
    """Найти файл задачи по id (TASK-NNN)."""
    for f in tasks_dir.glob(f"{task_id}*.md"):
        if _TASK_FILE_RE.match(f.name):
            return f
    return None


def parse_task(tasks_dir: Path, task_id: str) -> dict | None:
    """Прочитать задачу: meta + markdown-тело."""
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return None
    content = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)
    return {"id": task_id, "file": path.name, "meta": meta, "body": body}


def set_task_status(tasks_dir: Path, task_id: str, status: str) -> bool:
    """Обновить status в frontmatter задачи. Возвращает успех."""
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return False
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end < 0:
        return False

    header = content[:end]
    if re.search(r"^status:.*$", header, flags=re.MULTILINE):
        header = re.sub(r"^status:.*$", f"status: {status}", header, flags=re.MULTILINE)
    else:
        header += f"\nstatus: {status}"

    path.write_text(header + content[end:], encoding="utf-8")
    return True
