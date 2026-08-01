"""Разбор CHANGELOG.md на версии — чтобы показать пропущенное.

Обновление может перепрыгнуть несколько выпусков, и человеку нужно увидеть
их все, а не только последний: манифест несёт заметки одной версии, а полная
история приезжает вместе с обновлением — в локальном файле.

Режем здесь, а не во фронте: разбор markdown по заголовкам и сравнение версий —
это работа бэкенда, у которого для второго уже есть `version.compare`.
"""

from __future__ import annotations

import re

from . import version

# Заголовок секции Keep a Changelog: `## [1.2.0] — 2026-08-02` (тире любое).
# Версия обязана разбираться — иначе это не версия, а чей-то свободный текст
SECTION_RE = re.compile(r"^##\s+\[(?P<version>[^\]]+)\]\s*[—-]?\s*(?P<date>.*)$")


def sections(text: str) -> list[dict]:
    """Версии из changelog в порядке файла: [{version, date, body}].

    Заголовки, из которых не выходит разбираемая версия, пропускаются целиком
    вместе с телом: вступление файла и чьи-то заметки версией не станут.
    """
    out: list[dict] = []
    current: dict | None = None
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            current["body"] = "\n".join(body).strip()
            out.append(current)

    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            flush()
            raw = m.group("version").strip()
            current = ({"version": raw, "date": m.group("date").strip(), "body": ""}
                       if version.is_valid(raw) else None)
            body = []
            continue
        if current is not None:
            body.append(line)
    flush()
    return out


def since(text: str, previous: str, limit: int = 0) -> list[dict]:
    """Секции строго новее `previous` — то, чего пользователь ещё не видел.

    Прежняя версия исключается: на ней он и сидел. Пустая или неразбираемая
    точка отсчёта — отдаём всё: показать лишнее лучше, чем промолчать.

    `limit` (>0) оставляет только самые свежие: у того, кто пропустил два
    десятка выпусков, полная история — не подарок, а стена текста.
    """
    found = sections(text)
    if previous and version.is_valid(previous):
        found = [s for s in found if version.compare(s["version"], previous) > 0]
    return found[:limit] if limit > 0 else found
