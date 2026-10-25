"""Значения шапки задачи как скаляры YAML.

Шапку задачи читают и чужие парсеры YAML: GitHub, Obsidian, PyYAML. Значение
без кавычек для них — «простой скаляр», и `: `, ` #` или служебный символ в
начале делают шапку невалидной. Такое значение пишется в двойных кавычках,
остальные — как есть: метки вида `telegram:123:45` и обычные заголовки не
меняются, а шапки других задач при записи не трогаются.

Та же пара функций живёт в скрипте проекта (`templates/tasks/set_status.py`):
он автономен и импортировать отсюда не может, поэтому поведение копий сверяет
тест `test_yaml_scalar`.
"""

from __future__ import annotations

EMPTY = "~"
_LEAD = set("-?:,[]{}#&*!|>'\"%@`")
_ESCAPES = {"\\": "\\", '"': '"', "/": "/", "t": "\t", "n": "\n"}


def needs_quotes(value: str) -> bool:
    """Ломает ли значение YAML, если записать его без кавычек."""
    value = str(value)
    if not value or value == EMPTY:
        return False
    return (value != value.strip() or value[0] in _LEAD
            or ": " in value or value.endswith(":")
            or " #" in value or "\t" in value)


def to_yaml(value) -> str:
    """Значение поля для записи в шапку: в кавычках, только если без них нельзя."""
    value = str(value)
    if not needs_quotes(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def from_yaml(raw: str) -> str:
    """Значение поля как текст: обрамляющие кавычки сняты, экранирование раскручено.

    Значение без кавычек возвращается как есть — так читаются файлы, записанные
    до правила. Кавычки снимаются, только если строка — целый скаляр в кавычках:
    `"Foo" bar "baz"` из старого файла остаётся текстом.
    """
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        inner = raw[1:-1]
        return inner.replace("''", "'") if "'" not in inner.replace("''", "") else raw
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        out, i, inner = [], 0, raw[1:-1]
        while i < len(inner):
            ch = inner[i]
            if ch == '"':
                return raw
            if ch == "\\":
                if i + 1 == len(inner):
                    return raw
                nxt = inner[i + 1]
                out.append(_ESCAPES.get(nxt, "\\" + nxt))
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)
    return raw


def is_quoted(raw: str) -> bool:
    """Значение уже записано скаляром в кавычках."""
    raw = raw.strip()
    return len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'" and from_yaml(raw) != raw


def header_issues(header: str) -> list[str]:
    """Поля шапки, которые чужой парсер YAML не разберёт: `[ключ, …]`."""
    bad = []
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key != key.strip():
            continue
        value = value.strip()
        if value and not is_quoted(value) and needs_quotes(value):
            bad.append(key)
    return bad


def requote(content: str) -> str | None:
    """Текст файла, где сломанные для YAML значения шапки взяты в кавычки.

    Значение берётся тем, чем его видела доска до починки, — сырым текстом,
    поэтому заголовки и причины после неё не меняются. Шапка в порядке или её
    нет — None.
    """
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end < 0:
        return None
    lines = content[3:end].split("\n")
    changed = False
    for i, line in enumerate(lines):
        tail = "\r" if line.endswith("\r") else ""
        body = line[:-1] if tail else line
        if header_issues(body):
            key, _, value = body.partition(":")
            lines[i] = f"{key}: {to_yaml(value.strip())}{tail}"
            changed = True
    return content[:3] + "\n".join(lines) + content[end:] if changed else None
