"""Парсинг board.md в структуру колонок и задач."""

from __future__ import annotations

import re
from pathlib import Path

from backend.statuses import Pipeline

# Строка задачи: - TASK-NNN · [Заголовок](файл.md) · агент · дата
# Допускается зачёркивание ~~...~~ вокруг всей записи
_ENTRY_RE = re.compile(
    r"^\s*-\s*(?P<struck>~~)?\s*"
    r"(?P<id>TASK-\d+)\s*·\s*"
    r"\[(?P<title>[^\]]+)\]\((?P<file>[^)]+)\)"
    r"(?P<tail>.*?)(?P=struck)?\s*$"
)

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$")


def _parse_entry(line: str) -> dict | None:
    """Распарсить строку задачи, вернуть dict или None."""
    m = _ENTRY_RE.match(line)
    if not m:
        return None
    tail = m.group("tail").strip()
    # tail: " · Агент · дата" — убираем ведущий разделитель
    tail = re.sub(r"^·\s*", "", tail).strip()
    return {
        "id": m.group("id"),
        "title": m.group("title"),
        "file": m.group("file"),
        "meta": tail,
        "struck": bool(m.group("struck")),
    }


def parse_board(board_path: Path, pipeline: Pipeline) -> dict:
    """
    Распарсить board.md.

    Возвращает:
      columns: [{status, title, groups: [{title | None, tasks: [...]}]}]
      known_sections: [заголовки ##]
    Подразделы ### внутри раздела становятся группами; записи вне
    подразделов попадают в группу с title=None.

    Соответствие «раздел ↔ статус» и порядок колонок задаёт пайплайн проекта;
    разделы вне пайплайна остаются колонками (со статусом по заголовку) и
    уезжают в конец — молча терять чужие данные нельзя.
    """
    content = board_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    sections: list[dict] = []
    current_section: dict | None = None
    current_group: dict | None = None

    for line in lines:
        hm = _HEADING_RE.match(line)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            if level == 2:
                current_section = {"title": title, "groups": []}
                sections.append(current_section)
                current_group = None
                # Группа по умолчанию для записей вне подразделов
                current_group = {"title": None, "tasks": []}
                current_section["groups"].append(current_group)
            elif level == 3 and current_section is not None:
                current_group = {"title": title, "tasks": []}
                current_section["groups"].append(current_group)
            continue

        if current_section is None or current_group is None:
            continue

        entry = _parse_entry(line)
        if entry:
            current_group["tasks"].append(entry)

    # Собрать колонки по известным статусам + прочие разделы в конец
    status_map = pipeline.section_map()
    order_list = pipeline.keys()
    columns: list[dict] = []
    for section in sections:
        key = section["title"].strip().lower()
        status = status_map.get(key)
        # Убрать пустые группы по умолчанию, если есть именованные
        groups = [g for g in section["groups"] if g["tasks"] or g["title"]]
        columns.append({"status": status or key, "title": section["title"], "groups": groups})

    order = {s: i for i, s in enumerate(order_list)}
    columns.sort(key=lambda c: order.get(c["status"], len(order_list)))

    return {
        "columns": columns,
        "known_sections": [s["title"] for s in sections],
    }
