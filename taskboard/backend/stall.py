"""Состояние «задача стоит»: blocked_by / blocks / paused.

У задачи одно производное состояние — **стоит** — и две причины: она ждёт
другую задачу (`blocked_by`) или ждёт обстоятельства (`paused`). Пауза — метка
рядом со статусом, а не этап пайплайна: задача сохраняет свой статус, поэтому
разделы доски, каталог статусов и миграции о ней ничего не знают.

Зависимость хранится **двумя концами**: `blocked_by` у ждущей задачи и `blocks`
у блокера. Это тот же класс проблемы, что и статус в файле и на доске: правка
одного конца руками рано или поздно разъезжается. Поэтому оба конца правит
здешний инструмент, а расхождение ловит валидатор.

Автономный `tasks/set_status.py` повторяет эти операции своей копией — он
работает без сервера, в том числе в проектах без установленного taskboard.
Меняешь правила здесь — правь и там.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.task_parser import find_task_file, parse_frontmatter, set_meta_fields

BLOCKED_BY = "blocked_by"
BLOCKS = "blocks"
PAUSED = "paused"

# «Нет значения» во frontmatter. Поле остаётся на месте: пустая строка выглядит
# как недописанное, а `~` читается как осознанное «ничего»
EMPTY = "~"

_TASK_FILE_RE = re.compile(r"^(TASK-\d+).*\.md$")


def parse_ids(value) -> list[str]:
    """Список задач из значения поля: «~», пусто и None — это «нет».

    Разделитель — запятая, но пробелы тоже разбивают: человек пишет
    `blocked_by: TASK-013 TASK-014` не реже, чем через запятую.
    """
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    out: list[str] = []
    for part in re.split(r"[,\s]+", str(value or "")):
        part = part.strip().upper()
        if not part or part == EMPTY:
            continue
        if part not in out:
            out.append(part)
    return out


def format_ids(ids) -> str:
    """Значение поля из списка задач (пустой список — «~»)."""
    ids = parse_ids(ids)
    return ", ".join(ids) if ids else EMPTY


def _one_line(text) -> str:
    """Причина паузы живёт одной строкой frontmatter — перенос её разорвал бы."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return "" if text == EMPTY else text


def stall_of(meta: dict) -> dict:
    """Производное состояние задачи по её frontmatter."""
    blocked_by = parse_ids(meta.get(BLOCKED_BY))
    paused = _one_line(meta.get(PAUSED))
    return {
        "blocked_by": blocked_by,
        "blocks": parse_ids(meta.get(BLOCKS)),
        "paused": paused,
        "stalled": bool(blocked_by or paused),
    }


def _meta_of(path: Path) -> dict:
    try:
        return parse_frontmatter(path.read_text(encoding="utf-8-sig"))[0]
    except OSError:
        return {}


def task_stall(tasks_dir: Path, task_id: str) -> dict | None:
    """Состояние простоя одной задачи (None — файла нет)."""
    path = find_task_file(Path(tasks_dir), task_id)
    return stall_of(_meta_of(path)) if path else None


def resolve_ids(tasks_dir: Path, ids) -> list[dict]:
    """Развернуть номера задач в `[{id, title, status, found}]`.

    Номера мало: «TASK-013» ничего не говорит о том, далеко ли до разблокировки.
    Ненайденную задачу не выбрасываем — помечаем `found: False`, иначе ссылка
    на несуществующее просто исчезнет из интерфейса.
    """
    tasks_dir = Path(tasks_dir)
    out: list[dict] = []
    for task_id in parse_ids(ids):
        path = find_task_file(tasks_dir, task_id)
        meta = _meta_of(path) if path else {}
        out.append({"id": task_id, "title": meta.get("title", ""),
                    "status": meta.get("status", ""), "found": path is not None})
    return out


def stall_details(tasks_dir: Path, meta: dict) -> dict:
    """Состояние простоя с развёрнутыми ссылками — для открытой карточки."""
    state = stall_of(meta)
    state["blocked_by_tasks"] = resolve_ids(tasks_dir, state["blocked_by"])
    state["blocks_tasks"] = resolve_ids(tasks_dir, state["blocks"])
    return state


def _edit_field(path: Path, field: str, task_id: str, add: bool) -> None:
    """Добавить/убрать задачу в списочном поле файла."""
    ids = parse_ids(_meta_of(path).get(field))
    if add and task_id not in ids:
        ids.append(task_id)
    elif not add and task_id in ids:
        ids.remove(task_id)
    else:
        return
    set_meta_fields(path, {field: format_ids(ids)})


def set_blocked_by(tasks_dir: Path, task_id: str, ids) -> dict:
    """Задать список блокеров задачи, синхронно правя их `blocks`.

    Повторный вызов с тем же списком чинит односторонние записи: у каждого
    блокера проверяется наличие обратной ссылки, а не только у новых.
    """
    tasks_dir = Path(tasks_dir)
    task_id = task_id.strip().upper()
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}

    ids = parse_ids(ids)
    if task_id in ids:
        return {"ok": False, "error": "Задача не может блокировать сама себя"}

    old = parse_ids(_meta_of(path).get(BLOCKED_BY))
    set_meta_fields(path, {BLOCKED_BY: format_ids(ids)})

    missing: list[str] = []
    for blocker in ids:
        blocker_path = find_task_file(tasks_dir, blocker)
        if blocker_path is None:
            # Блокер может лежать в другом проекте или быть опечаткой: поле
            # пишем как просили, но молчать об этом нельзя
            missing.append(blocker)
            continue
        _edit_field(blocker_path, BLOCKS, task_id, add=True)

    for blocker in old:
        if blocker in ids:
            continue
        blocker_path = find_task_file(tasks_dir, blocker)
        if blocker_path is not None:
            _edit_field(blocker_path, BLOCKS, task_id, add=False)

    return {"ok": True, "task": task_id, "blocked_by": ids, "missing": missing}


def block(tasks_dir: Path, task_id: str, blocker: str) -> dict:
    """Добавить блокера к задаче."""
    current = task_stall(tasks_dir, task_id)
    if current is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    return set_blocked_by(tasks_dir, task_id, current["blocked_by"] + parse_ids(blocker))


def unblock(tasks_dir: Path, task_id: str, blocker: str = "") -> dict:
    """Снять блокера (без аргумента — всех)."""
    current = task_stall(tasks_dir, task_id)
    if current is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    drop = parse_ids(blocker)
    keep = [i for i in current["blocked_by"] if i not in drop] if drop else []
    return set_blocked_by(tasks_dir, task_id, keep)


def set_paused(tasks_dir: Path, task_id: str, reason: str) -> dict:
    """Поставить задачу на паузу с причиной (пустая причина — снять)."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    reason = _one_line(reason)
    set_meta_fields(path, {PAUSED: reason or EMPTY})
    return {"ok": True, "task": task_id.upper(), "paused": reason}


def _all_tasks(tasks_dir: Path) -> dict[str, dict]:
    """Все задачи проекта: id → {path, meta, stall}."""
    out: dict[str, dict] = {}
    tasks_dir = Path(tasks_dir)
    if not tasks_dir.is_dir():
        return out
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        m = _TASK_FILE_RE.match(path.name)
        if not m:
            continue
        meta = _meta_of(path)
        out[m.group(1).upper()] = {"path": path, "meta": meta, "stall": stall_of(meta)}
    return out


def stalled_tasks(tasks_dir: Path) -> dict:
    """Срез «что сейчас стоит и почему» — для скиллов, как `--queue`.

    Читать ради этого всю доску незачем: причина простоя лежит во frontmatter.
    """
    tasks: list[dict] = []
    for task_id, info in _all_tasks(tasks_dir).items():
        state = info["stall"]
        if not state["stalled"]:
            continue
        tasks.append({
            "id": task_id,
            "title": info["meta"].get("title", ""),
            "status": info["meta"].get("status", ""),
            "file": info["path"].name,
            "blocked_by": state["blocked_by"],
            "paused": state["paused"],
        })
    return {"total": len(tasks), "tasks": tasks}


def _cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Циклы в графе зависимостей: каждый — один раз, по составу участников."""
    found: list[list[str]] = []
    seen: set[frozenset] = set()

    def walk(node: str, path: list[str], visiting: set[str]) -> None:
        for nxt in graph.get(node, []):
            if nxt in visiting:
                cycle = path[path.index(nxt):]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    found.append(cycle + [nxt])
                continue
            if nxt not in graph:
                continue
            walk(nxt, path + [nxt], visiting | {nxt})

    for start in graph:
        walk(start, [start], {start})
    return found


def stall_issues(tasks_dir: Path, cfg: dict | None = None) -> list[str]:
    """Предупреждения о простое для отчёта валидатора.

    Ловит ровно то, что нельзя увидеть глазами в одном файле: ссылку на
    несуществующую задачу, разъехавшиеся концы зависимости и цикл, в котором
    все ждут друг друга и не сдвинется никто.

    Односторонние связи собираются в одну строку: в проекте, где `blocked_by`
    проставляли руками, их сразу десяток — россыпь одинаковых предупреждений
    только спрячет остальные проблемы.
    """
    tasks = _all_tasks(tasks_dir)
    issues: list[str] = []
    one_sided: list[str] = []
    orphan_blocks: list[str] = []

    for task_id, info in tasks.items():
        state = info["stall"]
        for blocker in state["blocked_by"]:
            other = tasks.get(blocker)
            if other is None:
                issues.append(f"{task_id}: blocked_by ссылается на несуществующую задачу {blocker}")
            elif task_id not in other["stall"]["blocks"]:
                one_sided.append(f"{task_id} ждёт {blocker}")
        for dependent in state["blocks"]:
            other = tasks.get(dependent)
            if other is None:
                issues.append(f"{task_id}: blocks ссылается на несуществующую задачу {dependent}")
            elif task_id not in other["stall"]["blocked_by"]:
                orphan_blocks.append(f"{task_id} блокирует {dependent}")

    script = (cfg or {}).get("status_script", "set_status.py")
    if one_sided:
        issues.append(
            "Односторонние блокировки — у блокера нет обратной ссылки blocks: "
            + ", ".join(one_sided)
            + f" (проставит {script} --block)")
    if orphan_blocks:
        issues.append(
            "Односторонние блокировки — задача не считает себя заблокированной: "
            + ", ".join(orphan_blocks)
            + f" (проставит {script} --block)")

    graph = {tid: info["stall"]["blocked_by"] for tid, info in tasks.items()}
    for cycle in _cycles(graph):
        issues.append("Цикл зависимостей: " + " → ".join(cycle))

    return issues


def annotate_stall(tasks_dir: Path, board: dict) -> dict:
    """Проставить карточкам доски состояние простоя.

    В строке board.md его нет — как и эпика, берём из frontmatter файлов задач.
    Свободные задачи полей не получают: на превью маркер нужен только тем,
    кто действительно стоит.
    """
    tasks_dir = Path(tasks_dir)
    for column in board.get("columns", []):
        for group in column.get("groups", []):
            for task in group.get("tasks", []):
                path = tasks_dir / task.get("file", "")
                if not path.is_file():
                    continue
                state = stall_of(_meta_of(path))
                if not state["stalled"]:
                    continue
                task["stalled"] = True
                if state["blocked_by"]:
                    task["blocked_by"] = state["blocked_by"]
                if state["paused"]:
                    task["paused"] = state["paused"]
    return board
