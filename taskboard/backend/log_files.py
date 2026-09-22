"""Чтение текстовых логов для встроенного просмотрщика."""

from __future__ import annotations

import codecs
import re
from pathlib import Path

from backend import text_match


# Цвета и прочие управляющие команды терминала полезны в консоли, но в обычном
# тексте превращаются в видимые `^[31m`. Покрываем CSI (цвет, курсор) и OSC
# (например, заголовок окна и гиперссылки); прочие управляющие символы не трогаем.
_ANSI_ESCAPE = re.compile(
    r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])"
)


def decode_log_bytes(raw: bytes) -> str:
    """Декодировать распространённые текстовые логи и убрать команды терминала.

    Windows PowerShell 5.1 пишет результат перенаправления в UTF-16 LE с BOM,
    тогда как современные оболочки обычно используют UTF-8. Без проверки BOM
    UTF-16 выглядел как чередование NUL и символов замены.
    """
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    return _ANSI_ESCAPE.sub("", text)


def read_log_text(path: Path) -> str:
    return decode_log_bytes(path.read_bytes())


def log_kind(name: str) -> str:
    """Markdown оформляется, остальные файлы сохраняют вид консольного текста."""
    return "markdown" if Path(name).suffix.lower() in {".md", ".markdown"} else "text"


# Мест на файл в ответе: `FAIL` в выводе тестов встречается тысячи раз, и
# список из тысяч строк не помогает найти, а только грузит окно
SEARCH_LIMIT = 20
# Строки режутся только по переводу строки — так же, как их нумерует окно.
# `splitlines` резал бы и по \x0c, \x1c, \u2028, и номера в консольном выводе
# с такими символами разъехались бы с тем, что видно на экране
_LINE_BREAK = re.compile(r"\r?\n")


def _log_places(text: str, kind: str, name: str) -> list[dict]:
    """Места файла: подраздел Markdown или строка текста — {heading, line, text}."""
    if kind == "markdown":
        return text_match.markdown_blocks(text, name)
    return [{"heading": "", "line": number, "text": line}
            for number, line in enumerate(_LINE_BREAK.split(text), start=1)]


def search_log_places(logs_dir: Path, query: str,
                      limit: int = SEARCH_LIMIT) -> tuple[dict, list[dict]]:
    """Места логов по запросу — фраза целиком, иначе все слова (`text_match.select`).

    Ответ — `(highlight, файлы)`: `highlight = {terms, phrase}` для подсветки,
    файлы свежие первыми: [{name, mtime, kind, hits: [{heading, line, excerpt}],
    more}]. В Markdown место — блок под подзаголовком, в остальных файлах —
    строка: консольный вывод заголовков не знает, а одна строка в нём — одно
    событие. `more` — сколько мест файла не вошло в `limit`.
    """
    files = []
    places = []
    if logs_dir.is_dir():
        for path in logs_dir.iterdir():
            if not path.is_file():
                continue
            try:
                text = read_log_text(path)
                mtime = path.stat().st_mtime
            except OSError:
                continue
            kind = log_kind(path.name)
            files.append({"name": path.name, "mtime": mtime, "kind": kind})
            places += [(path.name, place) for place in _log_places(text, kind, path.name)]
    highlight, found = text_match.select(query, places, lambda place: place[1]["text"])
    result = []
    for info in files:
        hits = [{"heading": place["heading"], "line": place["line"], "excerpt": piece}
                for (owner, place), piece in found if owner == info["name"]]
        if hits:
            result.append({**info, "hits": hits[:limit], "more": max(0, len(hits) - limit)})
    return highlight, sorted(result, key=lambda f: f["mtime"], reverse=True)


def search_logs(logs_dir: Path, query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Только файлы с найденными местами — без сведений о подсветке."""
    return search_log_places(logs_dir, query, limit)[1]
