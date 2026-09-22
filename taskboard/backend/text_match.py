"""Сопоставление запроса с текстом — для поиска по справке и логам.

Запрос ищется в два хода (`select`):

- **фраза целиком** — скопированный кусок текста должен находить ровно своё
  место, а не всё, где встречаются его слова. Пробелы между словами любые,
  регистр и «ё/е» не важны;
- **по словам**, если фразы нет нигде: человек помнит смысл, а не формулировку,
  и «задержка простоя» должна найти «Задержка перед подсказкой о простое».
  Слова идут в любом порядке и форме (у русских отсекается окончание), каждое
  совпадает с начала слова текста, а слово в 1–2 буквы — только целиком: иначе
  предлог «к» совпадал бы внутри любого слова.

Слово — литерал, не регулярка (`api()`, `C++`). Свёртка не меняет длину строки:
фронтенд подсвечивает совпадения по тем же правилам, сворачивая текст так же, и
позиции у них сходятся.
"""

from __future__ import annotations

import re

_SPACE_RE = re.compile(r"\s+")
_CYRILLIC_RE = re.compile(r"[а-я]+")
_MIN_STEM = 3
# Окончания существительных, прилагательных и глаголов, от длинных к коротким:
# срезается первое подошедшее. «ё» уже свёрнута в «е»
_ENDINGS = sorted({
    "иями", "ями", "ами", "ией", "ого", "его", "ому", "ему", "ыми", "ими",
    "иях", "ах", "ях", "ам", "ям", "ов", "ев", "ом", "ем", "ой", "ей", "ый", "ий",
    "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ть", "ся", "ет", "ит", "ут",
    "ют", "ат", "ят",
    "а", "е", "и", "о", "у", "ы", "ю", "я", "й", "ь",
}, key=len, reverse=True)


def fold(text: str) -> str:
    """Текст в виде для сравнения: нижний регистр, «ё» как «е»."""
    return text.lower().replace("ё", "е")


def stem(word: str) -> str:
    """Кириллическое слово без окончания: «простоя» и «простой» → «просто».

    Грубо, без словаря: срезается самое длинное окончание из списка, если
    остаётся хотя бы `_MIN_STEM` букв. Короткие и некириллические слова
    (`api()`, `C++`, `TASK-166`) остаются как есть — в них окончаний нет.
    """
    if not _CYRILLIC_RE.fullmatch(word):
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_STEM:
            return word[: -len(ending)]
    return word


def terms(query: str) -> list[str]:
    """Основы слов запроса в свёрнутом виде; пустой запрос — пустой список."""
    return [stem(word) for word in fold(query).split()]


# Граница слова — буква или цифра по соседству. Подчёркивание и точка границей
# считаются: `status` находит `set_status`, `.py` — `set_status.py`
_NOT_AFTER_ALNUM = r"(?<![^\W_])"
_NOT_BEFORE_ALNUM = r"(?![^\W_])"
_SHORT_WORD = 2


def _bounded(pattern: str, first: str, last: str) -> re.Pattern:
    """Шаблон начинается с начала слова текста; короткий хвост — целым словом."""
    if first[0].isalnum():
        pattern = _NOT_AFTER_ALNUM + pattern
    if len(last) <= _SHORT_WORD and last[-1].isalnum():
        pattern += _NOT_BEFORE_ALNUM
    return re.compile(pattern)


def _word_pattern(word: str) -> re.Pattern:
    """Слово совпадает с начала слова текста, короткое — только целиком."""
    return _bounded(re.escape(word), word, word)


def matches(text: str, words: list[str]) -> bool:
    """Все слова есть в тексте — в любом порядке и не обязательно рядом."""
    if not words:
        return False
    folded = fold(text)
    return all(_word_pattern(word).search(folded) for word in words)


def _excerpt_at(text: str, first: int, width: int) -> str:
    flat = _SPACE_RE.sub(" ", text).strip()
    if len(flat) <= width:
        return flat
    start = max(0, min(first - width // 3, len(flat) - width))
    end = start + width
    piece = flat[start:end].strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(flat) else "")


def excerpt(text: str, words: list[str], width: int = 160) -> str:
    """Фрагмент текста вокруг первого найденного слова, пробелы схлопнуты.

    Обрезанный край помечается «…», чтобы фрагмент не выдавал себя за целую
    фразу.
    """
    folded = fold(_SPACE_RE.sub(" ", text).strip())
    hits = [m.start() for m in (_word_pattern(word).search(folded) for word in words) if m]
    return _excerpt_at(text, min(hits) if hits else 0, width)


class Query:
    """Запрос в обоих видах: фраза целиком и основы слов.

    Фраза — от двух слов: одно слово ищется по словам, с формами и границей
    слова, иначе «к» снова совпадал бы внутри любого слова.
    """

    def __init__(self, query: str) -> None:
        self.words = fold(query).split()
        self.terms = [stem(word) for word in self.words]
        self._phrase = (_bounded(r"\s+".join(map(re.escape, self.words)),
                                 self.words[0], self.words[-1])
                        if len(self.words) > 1 else None)

    def has_phrase(self, text: str) -> bool:
        return bool(self._phrase and self._phrase.search(fold(text)))

    def has_words(self, text: str) -> bool:
        return matches(text, self.terms)

    def excerpt(self, text: str, phrase: bool, width: int = 160) -> str:
        if not phrase:
            return excerpt(text, self.terms, width)
        flat = fold(_SPACE_RE.sub(" ", text).strip())
        found = self._phrase.search(flat) if self._phrase else None
        return _excerpt_at(text, found.start() if found else 0, width)


def select(query: str, places: list, text_of) -> tuple[dict, list]:
    """Места с фразой целиком, а если её нет нигде — места со всеми словами.

    `text_of(place)` — текст места. Возвращает `(highlight, места)`, где
    `highlight = {terms, phrase}` — чем подсвечивать: слова фразы подряд или
    основы по отдельности. Вторым элементом каждого места идёт готовый фрагмент:
    `[(place, excerpt), …]`.
    """
    q = Query(query)
    if not q.words:
        return {"terms": [], "phrase": False}, []
    for phrase in (True, False):
        hit = q.has_phrase if phrase else q.has_words
        found = [(place, q.excerpt(text_of(place), phrase)) for place in places
                 if hit(text_of(place))]
        if found or not phrase:
            return {"terms": q.words if phrase else q.terms, "phrase": phrase}, found
    return {"terms": q.terms, "phrase": False}, []


# --- Markdown по подразделам ---
#
# Место в Markdown-файле — блок под заголовком: слова запроса должны стоять под
# одним подзаголовком, а номер строки заголовка ведёт окно к найденному.

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


def markdown_blocks(text: str, title: str) -> list[dict]:
    """Файл, разрезанный по заголовкам: [{heading, line, text}].

    `#` внутри блока кода — не заголовок: там живут примеры команд, строк
    доски и вывод консоли.
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
