"""Сопоставление запроса с текстом по словам — для поиска по справке и логам.

Человек помнит слова, а не фразу целиком и не их порядок: «задержка простоя»
должна найти «Задержка перед подсказкой о простое». Поэтому запрос режется на
слова, у русских слов отсекается окончание, и место подходит, если в нём есть
каждая основа. Слово — литерал, не регулярка (`api()`, `C++`), регистр и «ё/е»
не различаются.

Свёртка не меняет длину строки: фронтенд подсвечивает совпадения по тем же
словам, сворачивая текст так же, и позиции у них сходятся.
"""

from __future__ import annotations

import re

_SPACE_RE = re.compile(r"\s+")
_CYRILLIC_RE = re.compile(r"[а-я]+")
_MIN_STEM = 4
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


def matches(text: str, words: list[str]) -> bool:
    """Все слова есть в тексте — в любом порядке и не обязательно рядом."""
    if not words:
        return False
    folded = fold(text)
    return all(word in folded for word in words)


def excerpt(text: str, words: list[str], width: int = 160) -> str:
    """Фрагмент текста вокруг первого найденного слова, пробелы схлопнуты.

    Обрезанный край помечается «…», чтобы фрагмент не выдавал себя за целую
    фразу.
    """
    flat = _SPACE_RE.sub(" ", text).strip()
    if len(flat) <= width:
        return flat
    folded = fold(flat)
    hits = [pos for pos in (folded.find(word) for word in words) if pos >= 0]
    first = min(hits) if hits else 0
    start = max(0, min(first - width // 3, len(flat) - width))
    end = start + width
    piece = flat[start:end].strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(flat) else "")
