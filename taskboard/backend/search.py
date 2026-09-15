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


# Токен отбора по полю: `поле:значение[,значение]`. Поле — ключ frontmatter,
# который текстовый поиск не видит; по-русски пишут так же часто, как по-английски.
# Незнакомое поле остаётся текстом: `C:\temp` и `http://host` — не фильтры
_FIELD_ALIASES = {"epic": "epic", "эпик": "epic"}
_FIELD_TOKEN_RE = re.compile(r"(?<!\S)([^\s:]+):(\S*)")


def parse_query(query: str) -> dict:
    """Разобрать запрос: {text, filters}.

    filters — {поле: [значения в нижнем регистре]}: значения одного поля
    складываются по «или» (через запятую или повтором токена), разные поля и
    текст — по «и». text — остаток запроса как литерал, без схлопывания пробелов.
    Поле без значения (`epic:` в процессе набора) отбрасывается целиком.
    """
    filters: dict[str, list[str]] = {}

    def take(m: re.Match) -> str:
        field = _FIELD_ALIASES.get(m.group(1).lower())
        if field is None:
            return m.group(0)
        values = [v.strip().lower() for v in m.group(2).split(",") if v.strip()]
        if values:
            bucket = filters.setdefault(field, [])
            bucket.extend(v for v in values if v not in bucket)
        return ""

    text = _FIELD_TOKEN_RE.sub(take, query or "").strip()
    return {"text": text, "filters": filters}


def _field_value(meta: dict, field: str) -> str:
    value = str(meta.get(field, "") or "").strip()
    return "" if value == "~" else value.lower()


def search_tasks(tasks_dir: Path, query: str) -> list[dict]:
    """Задачи, подходящие под query: [{id, title, in_title, hits, excerpt}].

    Пустой запрос — выключенный фильтр, а не «всё подряд»: доска в этом случае
    показывается целиком, и выдача не нужна. Отбор по полю без текста отдаёт
    все подходящие задачи без попаданий.
    Сортировка: сначала совпавшие заголовком, дальше по номеру задачи.
    """
    parsed = parse_query(query)
    needle = parsed["text"].lower()
    filters = parsed["filters"]
    if (not needle and not filters) or not tasks_dir.is_dir():
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

        # Поле без значения (`epic: ~`) под отбор не попадает никогда
        if any(_field_value(meta, field) not in values for field, values in filters.items()):
            continue

        # Frontmatter целиком не ищем: `status: todo` есть в каждой задаче,
        # и запрос «todo» выдал бы всю доску
        haystacks = ((task_id, "id"), (title, "title"), (body, "body"))
        hits = 0
        excerpt = ""
        in_title = False
        for text, kind in (haystacks if needle else ()):
            low = text.lower()
            at = low.find(needle)
            if at < 0:
                continue
            hits += low.count(needle)
            if kind in ("id", "title"):
                in_title = True
            if not excerpt:
                excerpt = _excerpt(text, at, len(needle))

        if hits or not needle:
            results.append({"id": task_id, "title": title, "in_title": in_title,
                            "hits": hits, "excerpt": excerpt})

    results.sort(key=lambda item: (not item["in_title"], item["id"]))
    return results
