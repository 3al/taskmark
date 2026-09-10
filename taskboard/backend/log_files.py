"""Чтение текстовых логов для встроенного просмотрщика."""

from __future__ import annotations

import codecs
import re
from pathlib import Path


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
