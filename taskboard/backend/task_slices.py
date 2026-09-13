"""Срез задач по значению поля: кто принёс, кто занимается, в каком эпике.

Все такие срезы отвечают на один вопрос — «где едут задачи с этим значением», —
поэтому механизм один, а поле приходит параметром. Порядок — **пайплайна
проекта**, а не номера задачи: список читают как маршрут.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.task_parser import parse_frontmatter

# Имя поля frontmatter. Всё прочее — не поле, а мусор в запросе: с двоеточием
# или переносом оно сопоставилось бы с чужой строкой шапки
_FIELD_RE = re.compile(r"^[A-Za-z_][\w-]*$")

EMPTY = "~"


def field_value(meta: dict, field: str) -> str:
    """Значение поля из шапки; `~` и пустота — «значения нет»."""
    value = str(meta.get(field, "") or "").strip()
    return "" if value == EMPTY else value


def field_tasks(tasks_dir: Path, field: str, value: str, pipeline) -> list[dict]:
    """Задачи с `field: value` в порядке маршрута: [{id, title, file, status, label, color}].

    Съезды (отмена) идут за терминальным статусом, статус вне пайплайна —
    следом: молча прятать задачу, у которой статус выключили из настроек,
    нельзя, но и места в маршруте у неё уже нет.

    Пустое значение задач без значения не собирает: `author: ~` — это «автора
    нет», а не автор с пустым именем. Неизвестное значение — пустой список.
    """
    field = (field or "").strip()
    value = (value or "").strip()
    if (not _FIELD_RE.match(field) or not value or value == EMPTY
            or not Path(tasks_dir).is_dir()):
        return []

    order = {status: i for i, status in enumerate(pipeline.keys())}
    offramp = len(order) + 1        # съезды — за терминальным статусом
    unknown = len(order) + 2        # статус вне пайплайна — следом за ними

    out: list[dict] = []
    for path in sorted(Path(tasks_dir).glob("TASK-*.md")):
        match_id = re.match(r"^(TASK-\d+)", path.name)
        if not match_id:
            continue
        try:
            meta, _body = parse_frontmatter(path.read_text(encoding="utf-8-sig"))
        except OSError:
            continue
        if field_value(meta, field) != value:
            continue
        status = (meta.get("status") or "").strip()
        info = pipeline.get(status) or {}
        rank = (order.get(status, unknown) if info and not info.get("offramp")
                else offramp if info else unknown)
        out.append({"id": match_id.group(1), "title": meta.get("title", ""),
                    "file": path.name, "status": status,
                    "label": pipeline.label_of(status),
                    "color": info.get("color", ""), "_rank": rank})

    out.sort(key=lambda t: (t["_rank"], t["id"]))
    for task in out:
        task.pop("_rank")
    return out
