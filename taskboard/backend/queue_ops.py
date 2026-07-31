"""Операции перемещения задач между разделами board.md."""

from __future__ import annotations

import re
from pathlib import Path

from backend.stall import clear_stall, is_terminal
from backend.statuses import Pipeline, load_pipeline
from backend.task_parser import set_task_status


def _status_for_section(cfg: dict, to_section: str) -> str | None:
    """Статус frontmatter для раздела доски по пайплайну проекта."""
    return load_pipeline(cfg).status_for_section(to_section)

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


def ensure_section(board_path: Path, pipeline: Pipeline, status: str) -> bool:
    """Создать раздел статуса на его месте по порядку пайплайна, если его нет.

    Место выбирается по соседям: раздел встаёт перед первым существующим
    разделом статуса, идущего дальше по пайплайну. Так включение статуса не
    сваливает колонку в конец доски.
    """
    title = pipeline.section_of(status)
    if not title:
        return False

    content = board_path.read_text(encoding="utf-8")
    if re.search(rf"^##\s+{re.escape(title)}\s*$", content, flags=re.MULTILINE | re.IGNORECASE):
        return True

    marker = None
    for later in pipeline.forward(status):
        later_title = pipeline.section_of(later)
        if not later_title:
            continue
        marker = re.search(rf"^##\s+{re.escape(later_title)}\s*$", content,
                           flags=re.MULTILINE | re.IGNORECASE)
        if marker:
            break

    block = f"## {title}\n\n_(нет)_\n\n"
    if marker:
        content = content[:marker.start()] + block + content[marker.start():]
    else:
        content = content.rstrip() + "\n\n" + block.rstrip() + "\n"
    board_path.write_text(content, encoding="utf-8")
    return True


def ensure_plain_section(board_path: Path, title: str) -> None:
    """Создать раздел ## title в конце доски, если его нет.

    Для разделов вне пайплайна (технических): места по соседям у них нет,
    порядок статусов про них ничего не знает.
    """
    content = board_path.read_text(encoding="utf-8")
    if re.search(rf"^##\s+{re.escape(title)}\s*$", content, flags=re.MULTILINE | re.IGNORECASE):
        return
    board_path.write_text(content.rstrip() + f"\n\n## {title}\n\n", encoding="utf-8")


def add_entry(board_path: Path, pipeline: Pipeline, to_section: str, entry: str) -> dict:
    """Дописать готовую строку задачи в конец раздела доски.

    В отличие от move_task, задачи на доске ещё нет — переносить нечего;
    frontmatter не трогаем, его правит вызывающая сторона.
    """
    lines = board_path.read_text(encoding="utf-8").splitlines()
    bounds = _section_bounds(lines, to_section)
    if bounds is None:
        status = pipeline.status_for_section(to_section)
        if status and ensure_section(board_path, pipeline, status):
            lines = board_path.read_text(encoding="utf-8").splitlines()
            bounds = _section_bounds(lines, to_section)
        if bounds is None:
            return {"ok": False, "error": f"Раздел {to_section} не найден"}

    start, end = bounds
    task_lines = [i for i in range(start + 1, end)
                  if re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", lines[i])]
    insert_at = (task_lines[-1] + 1) if task_lines else start + 1
    _drop_empty_placeholder(lines, start, end)
    lines.insert(insert_at, entry)
    board_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "section": to_section}


def relink_entry(board_path: Path, task_id: str, new_file: str) -> dict:
    """Переписать ссылку на файл в строке задачи, не трогая её место на доске.

    Файл переименовали, а строка доски осталась со старым именем: задача
    живая, но по ссылке её не найти. Правим только ссылку — заголовок,
    хвост строки и позиция в разделе остаются как были.
    """
    lines = board_path.read_text(encoding="utf-8").splitlines()
    idx = _find_entry_line(lines, task_id)
    if idx is None:
        return {"ok": False, "error": f"{task_id} не найден на доске"}
    lines[idx] = re.sub(r"\]\([^)]+\)", f"]({new_file})", lines[idx], count=1)
    board_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "task": task_id, "file": new_file}


def retitle_entry(board_path: Path, task_id: str, new_title: str) -> dict:
    """Переписать заголовок в строке задачи, не трогая ссылку и место на доске.

    Ссылка у строки точная, а заголовок чужой или устаревший (задачу
    переименовали в файле). Файл — источник правды: строка повторяет
    заголовок из его frontmatter.
    """
    lines = board_path.read_text(encoding="utf-8").splitlines()
    idx = _find_entry_line(lines, task_id)
    if idx is None:
        return {"ok": False, "error": f"{task_id} не найден на доске"}
    line = lines[idx]
    start = line.find("[")
    end = line.find("](", start)
    if start == -1 or end == -1:
        return {"ok": False, "error": f"у {task_id} не найдена ссылка в строке"}
    lines[idx] = line[:start + 1] + new_title + line[end:]
    board_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "task": task_id, "title": new_title}


def move_task(
    tasks_dir: Path,
    cfg: dict,
    task_id: str,
    to_section: str,
    position: int | None = None,
    after_task_id: str | None = None,
    group: str | None = None,
    touch_status: bool = True,
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
    touch_status — править ли status: во frontmatter под новый раздел.
               DnD и скрипты правят оба конца связки, а починка доски
               (board_repair) переносит строку ПОД файл — его не трогает.
    """
    board_path = tasks_dir / cfg.get("board_file", "board.md")
    pipeline = load_pipeline(cfg)
    lines = board_path.read_text(encoding="utf-8").splitlines()

    src_idx = _find_entry_line(lines, task_id)
    if src_idx is None:
        return {"ok": False, "error": f"{task_id} не найден на доске"}

    bounds = _section_bounds(lines, to_section)
    if bounds is None:
        # Раздел статуса из пайплайна создаём на лету: доска могла быть
        # заведена до того, как статус включили в конфиге
        status = pipeline.status_for_section(to_section)
        if status and ensure_section(board_path, pipeline, status):
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
        # Позиция не задана: раздел создания задач — в начало (возврат из
        # очереди), остальные разделы — в конец
        if pipeline.status_for_section(to_section) == pipeline.action("create"):
            insert_at = start + 1
        else:
            insert_at = (task_lines[-1] + 1) if task_lines else start + 1
        _drop_empty_placeholder(lines, start, end)

    lines.insert(insert_at, entry_line)

    board_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Обновить frontmatter статус
    status = _status_for_section(cfg, to_section)
    result = {"ok": True, "task": task_id, "section": to_section, "status": status}
    if touch_status and status:
        set_task_status(tasks_dir, task_id, status)
        # Задача доехала до конца маршрута — «ждёт» про неё больше не правда.
        # Снимаем простой вместе с обратными ссылками у блокеров: иначе в
        # файлах осталось бы ровно то, что API отказался бы создать
        if is_terminal(load_pipeline(cfg), status):
            cleared = clear_stall(tasks_dir, task_id)
            if cleared["cleared"]:
                result["stall_cleared"] = cleared

    return result


def _drop_empty_placeholder(lines: list[str], start: int, end: int) -> None:
    """Удалить строку-заглушку _(нет)_ внутри раздела (in-place)."""
    for i in range(start + 1, min(end, len(lines))):
        if lines[i].strip() == "_(нет)_":
            lines.pop(i)
            return
