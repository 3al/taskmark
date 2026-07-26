"""Операции перемещения задач между разделами board.md."""

from __future__ import annotations

import re
from pathlib import Path

from backend.board_parser import BASE_SECTION_STATUS
from backend.task_parser import set_task_status


def _status_for_section(cfg: dict, to_section: str) -> str | None:
    """Статус frontmatter для раздела доски с учётом конфига очереди."""
    key = to_section.strip().lower()
    if key == cfg.get("queue_section", "Queue").strip().lower():
        return cfg.get("queued_status", "queued")
    return BASE_SECTION_STATUS.get(key)

# Заголовок раздела уровня ## по имени (case-insensitive)
def _section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """
    Найти границы раздела ## name: (индекс заголовка, индекс следующего ## или конец).
    """
    start = None
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if start is None and m.group(1).strip().lower() == name.lower():
                start = i
            elif start is not None:
                return start, i
    if start is None:
        return None
    return start, len(lines)


def _find_entry_line(lines: list[str], task_id: str, start: int = 0, end: int | None = None) -> int | None:
    """Найти индекс строки записи задачи (опционально в границах раздела)."""
    pattern = re.compile(rf"^\s*-\s*(?:~~)?\s*{re.escape(task_id)}\s*·")
    for i, line in enumerate(lines):
        if i < start or (end is not None and i >= end):
            continue
        if pattern.match(line):
            return i
    return None


def ensure_queue_section(board_path: Path, queue_name: str) -> bool:
    """Создать раздел ## Queue перед ## Development, если его нет."""
    content = board_path.read_text(encoding="utf-8")
    if re.search(rf"^##\s+{re.escape(queue_name)}\s*$", content, flags=re.MULTILINE | re.IGNORECASE):
        return True

    marker = re.search(r"^##\s+Development\s*$", content, flags=re.MULTILINE | re.IGNORECASE)
    if marker:
        content = content[:marker.start()] + f"## {queue_name}\n\n_(нет)_\n\n" + content[marker.start():]
    else:
        content = content.rstrip() + f"\n\n## {queue_name}\n\n_(нет)_\n"
    board_path.write_text(content, encoding="utf-8")
    return True


def move_task(
    tasks_dir: Path,
    cfg: dict,
    task_id: str,
    to_section: str,
    position: int | None = None,
    after_task_id: str | None = None,
    group: str | None = None,
) -> dict:
    """
    Переместить задачу в другой раздел доски (и обновить frontmatter).

    to_section — имя раздела уровня ## (Backlog, Queue, Development, ...).
    position — индекс внутри целевого раздела (None = по умолчанию:
               Backlog в начало, остальные в конец).
    after_task_id — вставить сразу после указанной задачи (точная семантика
               «хвост подраздела»: не перескакивает через заголовки ###).
    group — имя подраздела ### для вставки в ПУСТОЙ подраздел
               (встаёт сразу после его заголовка).
    """
    board_path = tasks_dir / cfg.get("board_file", "board.md")
    lines = board_path.read_text(encoding="utf-8").splitlines()

    src_idx = _find_entry_line(lines, task_id)
    if src_idx is None:
        return {"ok": False, "error": f"{task_id} не найден на доске"}

    bounds = _section_bounds(lines, to_section)
    if bounds is None:
        if to_section.lower() == cfg.get("queue_section", "Queue").lower():
            ensure_queue_section(board_path, cfg.get("queue_section", "Queue"))
            lines = board_path.read_text(encoding="utf-8").splitlines()
            bounds = _section_bounds(lines, to_section)
        if bounds is None:
            return {"ok": False, "error": f"Раздел {to_section} не найден"}

    entry_line = lines.pop(src_idx)
    start, end = _section_bounds(lines, to_section) or (0, 0)

    # Собрать индексы строк задач целевого раздела
    task_lines = [
        i for i in range(start + 1, end)
        if re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", lines[i])
    ]

    if after_task_id:
        # Вставка сразу после указанной задачи (конец подраздела)
        after_idx = _find_entry_line(lines, after_task_id, start, end)
        if after_idx is not None:
            insert_at = after_idx + 1
        else:
            insert_at = (task_lines[-1] + 1) if task_lines else start + 1
        _drop_empty_placeholder(lines, start, end)
    elif group:
        # Вставка в пустой подраздел ### — сразу после его заголовка
        insert_at = None
        for i in range(start + 1, end):
            m = re.match(r"^###\s+(.*)$", lines[i])
            if m and m.group(1).strip().lower() == group.strip().lower():
                insert_at = i + 1
                break
        if insert_at is None:
            insert_at = (task_lines[-1] + 1) if task_lines else start + 1
        _drop_empty_placeholder(lines, start, end)
    elif position is not None and position < len(task_lines):
        # Явная позиция — вставить перед задачей с этим индексом
        insert_at = task_lines[position]
    elif position is not None:
        # Явный конец раздела
        insert_at = (task_lines[-1] + 1) if task_lines else start + 1
        _drop_empty_placeholder(lines, start, end)
    else:
        # Позиция не задана: Backlog — в начало (возврат из очереди),
        # остальные разделы — в конец
        if to_section.strip().lower() == "backlog":
            insert_at = start + 1
        else:
            insert_at = (task_lines[-1] + 1) if task_lines else start + 1
        _drop_empty_placeholder(lines, start, end)

    lines.insert(insert_at, entry_line)

    board_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Обновить frontmatter статус
    status = _status_for_section(cfg, to_section)
    if status:
        set_task_status(tasks_dir, task_id, status)

    return {"ok": True, "task": task_id, "section": to_section, "status": status}


def _drop_empty_placeholder(lines: list[str], start: int, end: int) -> None:
    """Удалить строку-заглушку _(нет)_ внутри раздела (in-place)."""
    for i in range(start + 1, min(end, len(lines))):
        if lines[i].strip() == "_(нет)_":
            lines.pop(i)
            return
