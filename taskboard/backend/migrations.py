"""Миграции данных проекта при изменении конфигурации.

Принцип: любое переименование в настройках не должно оставлять проект
в сломанном состоянии — артефакты проекта мигрируют вслед за конфигом.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.board_parser import parse_board
from backend.queue_ops import ensure_section
from backend.scaffold import sync_rules
from backend.statuses import CATALOG, load_pipeline


def apply_config_migrations(tasks_dir: Path, old: dict, new: dict,
                            moves: dict | None = None) -> list[str]:
    """
    Применить миграции к активному проекту после смены конфига.

    old/new — эффективные конфиги (дефолты + сохранённые) до и после.
    moves — куда переносить задачи выключенных статусов: {статус: новый статус}.
    Возвращает список выполненных действий (для показа пользователю).
    """
    actions: list[str] = []
    if not tasks_dir.is_dir():
        return actions

    # Переименования файлов/папок внутри tasks/
    _rename_artifact(tasks_dir, old, new, "board_file", actions)
    _rename_artifact(tasks_dir, old, new, "create_script", actions)
    _rename_artifact(tasks_dir, old, new, "status_script", actions)
    _rename_artifact(tasks_dir, old, new, "logs_dir", actions)

    # Переименование раздела очереди — заголовок ## в файле доски
    _migrate_queue_section(tasks_dir, new.get("board_file", "board.md"), old, new, actions)

    # Переименование статуса очереди — frontmatter всех файлов задач
    _migrate_queued_status(tasks_dir, old, new, actions)

    # Изменение пайплайна: разделы доски следуют за составом статусов
    _migrate_pipeline(tasks_dir, old, new, moves or {}, actions)

    return actions


def pipeline_removals(tasks_dir: Path, old: dict, new: dict) -> list[dict]:
    """Разделы доски с задачами, которым не находится места в новом пайплайне.

    Это и выключаемые статусы, и разделы, оставшиеся от прежних настроек или
    правки board.md руками: задачи в них осиротели одинаково. Молча решать их
    судьбу нельзя — UI спрашивает, куда перенести, и предлагает предыдущий по
    прежнему порядку (чтобы задача не проскочила проверку).

    Ключ элемента — заголовок раздела: у осиротевшего раздела статуса может
    не быть вовсе.
    """
    old_pipe, new_pipe = load_pipeline(old), load_pipeline(new)
    board_path = tasks_dir / new.get("board_file", "board.md")
    if not board_path.is_file():
        return []

    old_keys = old_pipe.keys()
    fallback = next((s["key"] for s in new_pipe.statuses() if not s.get("offramp")), None)

    out: list[dict] = []
    for column in parse_board(board_path, old_pipe)["columns"]:
        count = sum(len(g["tasks"]) for g in column["groups"])
        status = column["status"]
        if not count or new_pipe.has(status):
            continue
        if new_pipe.status_for_section(column["title"]) is not None:
            continue  # раздел достался новому статусу (переименование)

        # Предложение по умолчанию — ближайший слева из оставшихся. Порядок
        # берём из прежнего пайплайна, а для осиротевшего раздела (его статуса
        # там уже нет) — из канонического порядка каталога. Статус такого
        # раздела выведен из заголовка («queue»), поэтому ищем родню по нему
        canonical = status
        if status not in old_keys and status not in CATALOG:
            canonical = next((k for k, m in CATALOG.items()
                              if m["section"].strip().lower() == column["title"].strip().lower()),
                             status)
        order = old_keys if canonical in old_keys else list(CATALOG)
        suggested = None
        if canonical in order:
            i = order.index(canonical)
            suggested = next((k for k in reversed(order[:i]) if new_pipe.has(k)), None)
            if suggested is None:
                suggested = next((k for k in order[i + 1:] if new_pipe.has(k)), None)
        out.append({"section": column["title"], "status": status,
                    "label": old_pipe.get(status)["label"] if old_pipe.has(status)
                             else column["title"],
                    "count": count, "suggested": suggested or fallback})
    return out


def _migrate_pipeline(tasks_dir: Path, old: dict, new: dict,
                      moves: dict, actions: list[str]) -> None:
    """Привести доску в соответствие с новым составом статусов.

    Раннего выхода «пайплайн не менялся» здесь нет намеренно: разделы могли
    осиротеть и раньше — от прежней настройки или правки board.md руками, —
    и повторное сохранение должно их подобрать.
    """
    old_pipe, new_pipe = load_pipeline(old), load_pipeline(new)
    board_path = tasks_dir / new.get("board_file", "board.md")
    if not board_path.is_file():
        return

    # Список считаем до переноса: после него разделы опустеют и «потеряются»
    removals = pipeline_removals(tasks_dir, old, new)

    # Разделы новых статусов создаём ПЕРВЫМИ: иначе переносить задачи некуда —
    # целевого раздела на доске ещё нет, и перенос молча не срабатывает
    for meta in new_pipe.statuses():
        if _section_bounds(board_path.read_text(encoding="utf-8").splitlines(),
                           meta["section"]) is not None:
            continue
        if ensure_section(board_path, new_pipe, meta["key"]):
            actions.append(f"Раздел «{meta['section']}» добавлен на доску")

    # Разделы, которым не нашлось места: задачи переезжают, раздел удаляется
    for removal in removals:
        section = removal["section"]
        target = moves.get(section) or moves.get(removal["status"]) or removal["suggested"]
        if target and new_pipe.has(target):
            moved = _move_section_tasks(tasks_dir, board_path, section,
                                        new_pipe.section_of(target) or "", target)
            if moved:
                actions.append(
                    f"{removal['label']}: {moved} задач → {new_pipe.get(target)['label']}")

    # Убираем опустевшие разделы выключенных статусов. Чужие разделы, заведённые
    # руками (какие-нибудь «Идеи»), не трогаем — они не наши, даже если пусты
    ours = {meta["section"] for meta in old_pipe.statuses() if not new_pipe.has(meta["key"])}
    ours |= {r["section"] for r in removals}
    for title in ours:
        if new_pipe.status_for_section(title) is not None:
            continue
        if _drop_empty_section(board_path, title):
            actions.append(f"Раздел «{title}» удалён из доски")

    # Правила для агентов описывают жизненный цикл — они тоже должны поехать
    for name in sync_rules(tasks_dir.parent, new):
        actions.append(f"Секция правил обновлена в {name}")


def _move_section_tasks(tasks_dir: Path, board_path: Path, from_section: str,
                        to_section: str, to_status: str) -> int:
    """Перенести записи задач между разделами доски и обновить их frontmatter."""
    lines = board_path.read_text(encoding="utf-8").splitlines()
    src = _section_bounds(lines, from_section)
    if not src or not to_section:
        return 0

    start, end = src
    entries = [lines[i] for i in range(start + 1, end)
               if re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", lines[i])]
    if not entries:
        return 0
    for line in entries:
        lines.remove(line)

    dst = _section_bounds(lines, to_section)
    if not dst:
        return 0
    at = dst[0] + 1
    for i in range(dst[0] + 1, dst[1]):
        if re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", lines[i]):
            at = i + 1
    for i in range(dst[0] + 1, dst[1]):
        if lines[i].strip() == "_(нет)_":
            lines.pop(i)
            at = min(at, i + 1)
            break
    lines[at:at] = entries
    # Записи переезжают между разделами, оставляя за собой то слипшийся с
    # заголовком список, то лишние пустые строки — доска должна остаться читаемой
    _tidy_section(lines, to_section)
    _tidy_section(lines, from_section)
    board_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Статус в файлах задач — второй конец правды, он должен поехать следом
    for line in entries:
        m = re.search(r"(TASK-\d+)", line)
        if not m:
            continue
        for path in tasks_dir.glob(f"{m.group(1)}*.md"):
            content = path.read_text(encoding="utf-8")
            updated = re.sub(r"^status:.*$", f"status: {to_status}", content,
                             count=1, flags=re.MULTILINE)
            if updated != content:
                path.write_text(updated, encoding="utf-8")
    return len(entries)


def _tidy_section(lines: list[str], title: str) -> None:
    """Привести раздел к виду «заголовок, пустая строка, записи, отбивка»."""
    bounds = _section_bounds(lines, title)
    if not bounds:
        return
    start, end = bounds

    body = [ln for ln in lines[start + 1:end]]
    tidy: list[str] = []
    for line in body:
        if not line.strip() and tidy and not tidy[-1].strip():
            continue  # схлопнуть подряд идущие пустые
        tidy.append(line)
    while tidy and not tidy[-1].strip():
        tidy.pop()
    if tidy and tidy[0].strip():
        tidy.insert(0, "")
    if end < len(lines):
        tidy.append("")  # отбивка перед следующим разделом
    lines[start + 1:end] = tidy


def _drop_empty_section(board_path: Path, section: str) -> bool:
    """Удалить раздел доски, если в нём не осталось задач."""
    lines = board_path.read_text(encoding="utf-8").splitlines()
    bounds = _section_bounds(lines, section)
    if not bounds:
        return False
    start, end = bounds
    if any(re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", ln) for ln in lines[start + 1:end]):
        return False
    del lines[start:end]
    board_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def _section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """Границы раздела ## name: (индекс заголовка, индекс следующего ## или конец)."""
    start = None
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.*)$", line)
        if not m:
            continue
        if start is None and m.group(1).strip().lower() == name.strip().lower():
            start = i
        elif start is not None:
            return start, i
    return (start, len(lines)) if start is not None else None


def _rename_artifact(tasks_dir: Path, old: dict, new: dict, key: str, actions: list[str]) -> None:
    """Переименовать файл/папку в tasks/ вслед за настройкой."""
    old_v, new_v = old.get(key), new.get(key)
    if not old_v or not new_v or old_v == new_v:
        return
    src = tasks_dir / old_v
    dst = tasks_dir / new_v
    if src.exists() and not dst.exists():
        src.rename(dst)
        actions.append(f"Переименовано: {old_v} → {new_v}")


def _migrate_queue_section(tasks_dir: Path, board_file: str, old: dict, new: dict, actions: list[str]) -> None:
    """Переименовать заголовок ## <queue_section> в файле доски."""
    old_v, new_v = old.get("queue_section"), new.get("queue_section")
    if not old_v or not new_v or old_v == new_v:
        return
    board = tasks_dir / board_file
    if not board.is_file():
        return
    content = board.read_text(encoding="utf-8")
    old_heading = re.compile(rf"^##\s+{re.escape(old_v)}\s*$", flags=re.MULTILINE)
    new_heading = re.compile(rf"^##\s+{re.escape(new_v)}\s*$", flags=re.MULTILINE)
    if old_heading.search(content) and not new_heading.search(content):
        board.write_text(old_heading.sub(f"## {new_v}", content), encoding="utf-8")
        actions.append(f"Раздел очереди в {board_file}: {old_v} → {new_v}")


def _migrate_queued_status(tasks_dir: Path, old: dict, new: dict, actions: list[str]) -> None:
    """Обновить status в frontmatter всех задач со старым статусом очереди."""
    old_v, new_v = old.get("queued_status"), new.get("queued_status")
    if not old_v or not new_v or old_v == new_v:
        return
    pattern = re.compile(rf"^status:\s*{re.escape(old_v)}\s*$", flags=re.MULTILINE)
    count = 0
    for f in tasks_dir.glob("TASK-*.md"):
        content = f.read_text(encoding="utf-8")
        updated = pattern.sub(f"status: {new_v}", content)
        if updated != content:
            f.write_text(updated, encoding="utf-8")
            count += 1
    if count:
        actions.append(f"Статус {old_v} → {new_v} обновлён в {count} файлах задач")
