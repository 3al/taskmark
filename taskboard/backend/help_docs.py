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

from backend import text_match

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


# --- Поиск ---
#
# Справка длинная, и человек помнит слова, а не раздел. Поиск отвечает местом:
# подзаголовком, под которым слова стоят, и номером его строки — по нему окно
# прокручивает раздел к найденному, без якорей в самих текстах.

_HEADING_RE = re.compile(r"^(#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_TABLE_RULE_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _plain(line: str) -> str:
    """Строка markdown без разметки: ищется то, что человек видит в окне.

    Адрес ссылки не виден — и не ищется, иначе `lifecycle` находил бы каждую
    ссылку на раздел жизненного цикла.
    """
    if _TABLE_RULE_RE.match(line) and "-" in line:
        return ""
    line = _LINK_RE.sub(r"\1", line)
    line = _LIST_RE.sub("", line)
    line = line.lstrip("> ")
    return re.sub(r"[*`|~]", " ", line)


def _blocks(text: str, title: str) -> list[dict]:
    """Файл, разрезанный по заголовкам: [{heading, line, text}].

    `#` внутри блока кода — не заголовок: в справке там живут примеры команд
    и строк доски.
    """
    blocks = [{"heading": title, "line": 1, "parts": []}]
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        heading = None if fenced else _HEADING_RE.match(line)
        if heading:
            name = _plain(heading.group("text")).strip()
            blocks.append({"heading": name, "line": number, "parts": [name]})
        else:
            blocks[-1]["parts"].append(line if fenced else _plain(line))
    return [{"heading": b["heading"], "line": b["line"], "text": "\n".join(b["parts"])}
            for b in blocks if "".join(b["parts"]).strip()]


def search(query: str) -> list[dict]:
    """Места справки, где встречаются все слова запроса.

    Ответ — разделы по порядку, в каждом найденные подразделы:
    [{id, title, hits: [{heading, line, excerpt}]}]. Слова должны стоять под
    одним подзаголовком: совпадения, рассыпанные по разным частям раздела,
    места не указывают.
    """
    words = text_match.terms(query)
    if not words:
        return []
    found = []
    for path in _files():
        key = _section_id(path)
        if not key:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title = _title(text, key)
        hits = [{"heading": block["heading"], "line": block["line"],
                 "excerpt": text_match.excerpt(block["text"], words)}
                for block in _blocks(text, title)
                if text_match.matches(block["text"], words)]
        if hits:
            found.append({"id": key, "title": title, "hits": hits})
    return found
