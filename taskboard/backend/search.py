"""Поиск по задачам проекта: живой фильтр доски.

Ищем по файлам задач, а не по тому, что видно на карточке: пользователь помнит
формулировку из описания или критериев, а не заголовок. Запрос — литерал, а не
регулярка: человек вводит `api()` и `C++`, и это не должно ломать поиск.

Индекса нет намеренно: файлы задач мелкие, их десятки, а любой кэш пришлось бы
инвалидировать по событиям watcher'а — сложность, которой этот объём не стоит.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.task_parser import _TASK_FILE_RE, parse_frontmatter

# Сколько символов контекста показывать вокруг найденного
_EXCERPT_PAD = 60


def _excerpt(text: str, at: int, length: int) -> str:
    """Фрагмент вокруг найденного места — чтобы было видно, почему задача в выдаче."""
    start = max(0, at - _EXCERPT_PAD)
    end = min(len(text), at + length + _EXCERPT_PAD)
    piece = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


def search_tasks(tasks_dir: Path, query: str) -> list[dict]:
    """Задачи, в которых встречается query: [{id, title, in_title, hits, excerpt}].

    Пустой запрос — выключенный фильтр, а не «всё подряд»: доска в этом случае
    показывается целиком, и выдача не нужна.
    Сортировка: сначала совпавшие заголовком, дальше по номеру задачи.
    """
    needle = query.strip().lower()
    if not needle or not tasks_dir.is_dir():
        return []

    results: list[dict] = []
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        if not _TASK_FILE_RE.match(path.name):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue  # файл исчез между glob и чтением — не повод падать

        meta, body = parse_frontmatter(content)
        # Идентичность задачи задаёт имя файла: доска ссылается на файл, а
        # `id:` во frontmatter может от него отстать (в живом проекте нашёлся
        # TASK-000 с `id: TASK-120`). Взяли бы оттуда — выдача указывала бы на
        # задачу, которой на доске нет
        match_id = re.match(r"^(TASK-\d+)", path.name)
        if not match_id:
            continue
        task_id = match_id.group(1)
        title = meta.get("title", "")

        # Frontmatter целиком не ищем: `status: todo` есть в каждой задаче,
        # и запрос «todo» выдал бы всю доску
        haystacks = ((task_id, "id"), (title, "title"), (body, "body"))
        hits = 0
        excerpt = ""
        in_title = False
        for text, kind in haystacks:
            low = text.lower()
            at = low.find(needle)
            if at < 0:
                continue
            hits += low.count(needle)
            if kind in ("id", "title"):
                in_title = True
            if not excerpt:
                excerpt = _excerpt(text, at, len(needle))

        if hits:
            results.append({"id": task_id, "title": title, "in_title": in_title,
                            "hits": hits, "excerpt": excerpt})

    results.sort(key=lambda item: (not item["in_title"], item["id"]))
    return results
