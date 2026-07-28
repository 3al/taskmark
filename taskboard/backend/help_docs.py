"""Раздел помощи: пользовательская документация из docs/help.

Инструкция нужна там, где возникает вопрос, — в браузере у пользователя,
на руках у которого чужой проект, а не наш репозиторий. Поэтому UI показывает
не копию текста, а те же самые файлы `docs/help/*.md`, на которые ссылается
README: источник правды один, расходиться нечему.

Имя файла задаёт порядок и идентификатор раздела: `04-lifecycle.md` → `lifecycle`
(по нему UI открывает помощь сразу на нужном месте), заголовок берётся из первой
строки `# ...`.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS_DIR = Path(__file__).parent.parent.parent / "docs" / "help"

_NAME_RE = re.compile(r"^(?:\d+-)?(?P<id>[a-z0-9-]+)$")
_TITLE_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.M)


def _section_id(path: Path) -> str | None:
    """Идентификатор раздела из имени файла (без числового префикса)."""
    m = _NAME_RE.match(path.stem)
    return m.group("id") if m else None


def _title(text: str, fallback: str) -> str:
    m = _TITLE_RE.search(text)
    return m.group("title") if m else fallback


def _files() -> list[Path]:
    if not DOCS_DIR.is_dir():
        return []
    return sorted(p for p in DOCS_DIR.glob("*.md") if p.is_file())


def list_sections() -> list[dict]:
    """Разделы помощи по порядку: [{id, title}]."""
    sections = []
    for path in _files():
        key = _section_id(path)
        if not key:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        sections.append({"id": key, "title": _title(text, key)})
    return sections


def get_section(section_id: str) -> dict | None:
    """Раздел с содержимым или None, если такого нет.

    Идентификатор сверяется с разобранными именами файлов, а не подставляется
    в путь: `../../README` остаётся просто неизвестным разделом.
    """
    for path in _files():
        if _section_id(path) != section_id:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        return {"id": section_id, "title": _title(text, section_id), "content": text}
    return None
