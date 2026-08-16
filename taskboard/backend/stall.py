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

from backend.notes import (BLOCK_TEXT, BLOCKS_TEXT, BOARD_AUTHOR, PAUSE_TEXT,
                           RESUME_TEXT, UNBLOCK_TEXT, UNBLOCKS_TEXT, append_note)
# Реэкспорт: правило конца маршрута живёт рядом с пайплайном, но зовут его
# отсюда — по простою и терминальности вопросы приходят в один модуль
from backend.statuses import is_terminal  # noqa: F401
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


def can_stall(pipeline, status: str) -> dict:
    """Можно ли поставить простой на задачу в этом статусе: `{ok, reason}`.

    Стоять может задача, у которой есть следующий шаг маршрута. Для
    завершённой или отменённой «ждёт» — не правда пользователя, а мусор:
    работа окончена, ждать нечего.

    Незнакомый статус не запрещаем: пайплайн могли поменять, а задача с
    прежним статусом осталась — мешать работать с ней не за что.
    """
    if not is_terminal(pipeline, status):
        return {"ok": True, "reason": ""}
    meta = pipeline.get(status) or {}
    label = meta.get("label") or status
    what = "снята с маршрута" if meta.get("offramp") else "завершена"
    return {"ok": False,
            "reason": f"Задача {what} (статус «{label}») — простой не имеет смысла"}


def stall_reason(task_id: str, state: dict) -> str:
    """Человеческое «чего ждёт задача» — для вопросов и отказов."""
    parts = []
    if state.get("blocked_by"):
        parts.append(f"ждёт {', '.join(state['blocked_by'])}")
    if state.get("paused"):
        parts.append(f"на паузе: {state['paused']}")
    return f"{task_id} " + " и ".join(parts) if parts else ""


def move_confirmation(tasks_dir: Path, pipeline, task_id: str, target: str) -> dict:
    """Нужно ли подтверждение переноса стоящей задачи: `{confirm, reason}`.

    Аномален ровно один переход — **взять в работу**: блокировка это и значит,
    «не начинай, пока та не готова». Ждать внутри ревью, тестирования или
    релиза законно (две задачи проверяются только вместе), назад по маршруту —
    тем более, а в терминальном простой снимается сам.
    """
    state = task_stall(tasks_dir, task_id)
    if not state or not state["stalled"]:
        return {"confirm": False, "reason": ""}
    actions = pipeline.actions() if pipeline else {}
    if target not in (actions.get("start"), actions.get("return")):
        return {"confirm": False, "reason": ""}
    return {"confirm": True, "reason": stall_reason(task_id.strip().upper(), state)}


def resolve_ids(tasks_dir: Path, ids, pipeline=None) -> list[dict]:
    """Развернуть номера задач в `[{id, title, status, label, found, resolved}]`.

    Номера мало: «TASK-013» ничего не говорит о том, далеко ли до разблокировки.
    Ненайденную задачу не выбрасываем — помечаем `found: False`, иначе ссылка
    на несуществующее просто исчезнет из интерфейса.

    `resolved` — блокер сам дошёл до конца маршрута и больше не держит. Снимать
    пометку автоматически не станем: она стоит в чужом файле, и правка по
    касательной («двинули A — молча изменили B») удивляет сильнее, чем помощь.

    `cancelled` — тот же конец маршрута, но другого смысла: блокер не сделали, а
    похоронили, и вместе с ним мог отпасть предмет самой ждущей задачи. Держать
    её из-за этого нечему, а сказать об этом надо другими словами.
    """
    tasks_dir = Path(tasks_dir)
    out: list[dict] = []
    for task_id in parse_ids(ids):
        path = find_task_file(tasks_dir, task_id)
        meta = _meta_of(path) if path else {}
        status = meta.get("status", "")
        label = (pipeline.get(status) or {}).get("label", "") if pipeline and status else ""
        resolved = bool(path) and is_terminal(pipeline, status)
        offramp = bool((pipeline.get(status) or {}).get("offramp")) if pipeline else False
        out.append({"id": task_id, "title": meta.get("title", ""),
                    "status": status, "label": label or status,
                    "found": path is not None,
                    "resolved": resolved,
                    "cancelled": resolved and offramp})
    return out


def is_stale(blockers: list[dict], paused: str = "") -> bool:
    """Держать нечему: пометка есть, а все блокеры дошли до конца маршрута.

    Пауза этого не касается — она ждёт обстоятельства, а не задачу: пока она
    стоит, задача стоит вместе с ней. Ненайденный блокер тоже держит: битая
    ссылка — предупреждение валидатора, а не разрешение работать.
    """
    return bool(blockers) and all(b["resolved"] for b in blockers) and not paused


def stale_reason(blockers: list[dict], unblock_hint: str = "--unblock") -> str:
    """Чем протухла пометка и что с ней делать — одной строкой для агента.

    Выполненный блокер и отменённый разводятся текстом: первый снимает вопрос,
    второй его задаёт — предмет ждущей задачи мог отпасть вместе с ним.
    """
    done = [b["id"] for b in blockers if b["resolved"] and not b["cancelled"]]
    cancelled = [b["id"] for b in blockers if b["cancelled"]]
    if not done and not cancelled:
        return ""
    parts = []
    if done:
        parts.append(f"{', '.join(done)} "
                     f"{'завершены' if len(done) > 1 else 'завершена'}")
    if cancelled:
        parts.append(f"{', '.join(cancelled)} "
                     f"{'отменены' if len(cancelled) > 1 else 'отменена'}, "
                     f"проверьте, не отпал ли вместе с ней предмет задачи")
    ids = " ".join(b["id"] for b in blockers)
    return f"{' и '.join(parts)} — снять пометку: {unblock_hint} {ids}"


def stall_details(tasks_dir: Path, meta: dict, pipeline=None) -> dict:
    """Состояние простоя с развёрнутыми ссылками — для открытой карточки."""
    state = stall_of(meta)
    state["blocked_by_tasks"] = resolve_ids(tasks_dir, state["blocked_by"], pipeline)
    state["blocks_tasks"] = resolve_ids(tasks_dir, state["blocks"], pipeline)
    # Пометка стоит, а держать её нечему — интерфейс приглушает маркер и
    # предлагает снять. Две причины: все блокеры дошли до конца маршрута или
    # сама задача завершена (у закрытой работы «ждёт» смысла не имеет)
    blocked = state["blocked_by_tasks"]
    all_resolved = is_stale(blocked, state["paused"])
    state["stale"] = bool(state["stalled"]) and (
        all_resolved or is_terminal(pipeline, meta.get("status", "")))
    state["stale_reason"] = stale_reason(blocked) if all_resolved else ""
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


def set_blocked_by(tasks_dir: Path, task_id: str, ids, author: str = BOARD_AUTHOR) -> dict:
    """Задать список блокеров задачи, синхронно правя их `blocks`.

    Повторный вызов с тем же списком чинит односторонние записи: у каждого
    блокера проверяется наличие обратной ссылки, а не только у новых.

    В хронологию идёт **разница**, а не поданный список: починка односторонней
    ссылки — не событие жизненного цикла, и строка о ней сказала бы неправду.
    Пишется обоим концам: файл блокера иначе молчит о том, что на него встали.
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

    _note_blockers(tasks_dir, path, task_id,
                   added=[i for i in ids if i not in old],
                   removed=[i for i in old if i not in ids], author=author)
    return {"ok": True, "task": task_id, "blocked_by": ids, "missing": missing}


def _note_blockers(tasks_dir: Path, path: Path, task_id: str,
                   added: list[str], removed: list[str], author: str) -> None:
    """Записать появление и снятие блокировок обоим концам зависимости."""
    if added:
        append_note(path, BLOCK_TEXT.format(ids=", ".join(added)), author)
    if removed:
        append_note(path, UNBLOCK_TEXT.format(ids=", ".join(removed)), author)
    for blocker, text in ([(b, BLOCKS_TEXT) for b in added]
                          + [(b, UNBLOCKS_TEXT) for b in removed]):
        blocker_path = find_task_file(tasks_dir, blocker)
        if blocker_path is not None:
            append_note(blocker_path, text.format(id=task_id), author)


def block(tasks_dir: Path, task_id: str, blocker: str, author: str = BOARD_AUTHOR) -> dict:
    """Добавить блокера к задаче."""
    current = task_stall(tasks_dir, task_id)
    if current is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    return set_blocked_by(tasks_dir, task_id,
                          current["blocked_by"] + parse_ids(blocker), author)


def unblock(tasks_dir: Path, task_id: str, blocker: str = "",
            author: str = BOARD_AUTHOR) -> dict:
    """Снять блокера (без аргумента — всех)."""
    current = task_stall(tasks_dir, task_id)
    if current is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    drop = parse_ids(blocker)
    keep = [i for i in current["blocked_by"] if i not in drop] if drop else []
    return set_blocked_by(tasks_dir, task_id, keep, author)


def set_paused(tasks_dir: Path, task_id: str, reason: str,
               author: str = BOARD_AUTHOR) -> dict:
    """Поставить задачу на паузу с причиной (пустая причина — снять).

    Строка в хронологию идёт только при смене состояния: «снял паузу», когда её
    не было, — не событие, а повторный вызов инструмента.
    """
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    reason = _one_line(reason)
    was = _one_line(_meta_of(path).get(PAUSED, "")).strip(EMPTY)
    set_meta_fields(path, {PAUSED: reason or EMPTY})
    if reason and reason != was:
        append_note(path, PAUSE_TEXT.format(reason=reason), author)
    elif not reason and was:
        append_note(path, RESUME_TEXT, author)
    return {"ok": True, "task": task_id.upper(), "paused": reason}


def clear_stall(tasks_dir: Path, task_id: str, author: str = BOARD_AUTHOR) -> dict:
    """Снять с задачи и блокировки, и паузу. Возвращает, что было снято.

    Зовётся при переезде в терминальный статус: задача закрыта, и «ждёт» про
    неё — уже не правда, а ровно те данные, которые API отказался бы создать.
    """
    state = task_stall(tasks_dir, task_id)
    if state is None or not state["stalled"]:
        return {"ok": True, "cleared": False, "blocked_by": [], "paused": ""}
    if state["blocked_by"]:
        set_blocked_by(tasks_dir, task_id, [], author)
    if state["paused"]:
        set_paused(tasks_dir, task_id, "", author)
    return {"ok": True, "cleared": True,
            "blocked_by": state["blocked_by"], "paused": state["paused"]}


def blocker_candidates(tasks_dir: Path, cfg: dict, task_id: str) -> list[dict]:
    """Кем можно заблокировать задачу: `[{id, title, status, label}]`.

    Половина вариантов «всех задач проекта» — заведомо мусор, и показывать их
    незачем: завершённые и отменённые блокеры мертвы в момент создания, а
    задача, которая (пусть и через цепочку) ждёт текущую, замкнула бы круг —
    обе стоят и не двинется никто.

    Считает бэкенд: фронт не знает ни графа зависимостей, ни статусов задач.
    """
    from backend.statuses import load_pipeline

    pipeline = load_pipeline(cfg or {})
    tasks = _all_tasks(tasks_dir)
    task_id = (task_id or "").strip().upper()
    current = tasks.get(task_id)
    if current is None:
        return []

    # Кто (транзитивно) ждёт текущую задачу. Граф строим по `blocked_by` — это
    # авторское поле, оно есть всегда; обратные ссылки `blocks` в проекте, где
    # блокировки проставляли руками, могут быть не заполнены, и обход по ним
    # пропустил бы цикл
    waiting_for: dict[str, list[str]] = {}
    for other, info in tasks.items():
        for blocker in info["stall"]["blocked_by"]:
            waiting_for.setdefault(blocker, []).append(other)

    dependents: set[str] = set()
    queue = [task_id]
    while queue:
        for dep in waiting_for.get(queue.pop(), []):
            if dep not in dependents:
                dependents.add(dep)
                queue.append(dep)

    exclude = {task_id} | set(current["stall"]["blocked_by"]) | dependents

    out: list[dict] = []
    for candidate, info in tasks.items():
        if candidate in exclude:
            continue
        status = info["meta"].get("status", "")
        if is_terminal(pipeline, status):
            continue
        out.append({"id": candidate, "title": info["meta"].get("title", ""),
                    "status": status,
                    "label": (pipeline.get(status) or {}).get("label", status)})
    return out


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


def stalled_tasks(tasks_dir: Path, pipeline=None) -> dict:
    """Срез «что сейчас стоит и почему» — для скиллов, как `--queue`.

    Читать ради этого всю доску незачем: причина простоя лежит во frontmatter.

    Задача с протухшей пометкой из среза не исчезает — пометка-то стоит, и
    снять её кому-то придётся, — но помечена `stale` с объяснением: иначе
    читающий идёт смотреть блокер и обнаруживает, что тот давно закрыт.
    """
    tasks: list[dict] = []
    for task_id, info in _all_tasks(tasks_dir).items():
        state = info["stall"]
        if not state["stalled"]:
            continue
        blockers = resolve_ids(tasks_dir, state["blocked_by"], pipeline)
        stale = is_stale(blockers, state["paused"])
        tasks.append({
            "id": task_id,
            "title": info["meta"].get("title", ""),
            "status": info["meta"].get("status", ""),
            "file": info["path"].name,
            "blocked_by": state["blocked_by"],
            "blocked_by_tasks": blockers,
            "paused": state["paused"],
            "stale": stale,
            "stale_reason": stale_reason(blockers) if stale else "",
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


def plan_blocks_repair(tasks_dir: Path) -> list[dict]:
    """Что нужно поправить в обратных ссылках: `[{id, task, action}]`.

    `blocked_by` — авторское поле, `blocks` — производное, поэтому починка
    пересобирает второе из первого: где обратной ссылки нет — добавить
    (`add`), где она указывает на задачу, которая себя заблокированной не
    считает — убрать (`drop`).

    Битые ссылки и циклы не трогаем: однозначного правильного действия там
    нет, они остаются предупреждениями валидатора.
    """
    tasks = _all_tasks(tasks_dir)
    plan: list[dict] = []

    for task_id, info in tasks.items():
        for blocker in info["stall"]["blocked_by"]:
            other = tasks.get(blocker)
            if other is not None and task_id not in other["stall"]["blocks"]:
                plan.append({"id": blocker, "task": task_id, "action": "add"})
        for dependent in info["stall"]["blocks"]:
            other = tasks.get(dependent)
            if other is not None and task_id not in other["stall"]["blocked_by"]:
                plan.append({"id": task_id, "task": dependent, "action": "drop"})

    return plan


def apply_blocks_repair(tasks_dir: Path) -> dict:
    """Выполнить починку обратных ссылок. Возвращает счётчик и осечки."""
    tasks_dir = Path(tasks_dir)
    failed: list[str] = []
    plan = plan_blocks_repair(tasks_dir)

    for item in plan:
        path = find_task_file(tasks_dir, item["id"])
        if path is None:
            failed.append(f"{item['id']}: файл не найден")
            continue
        _edit_field(path, BLOCKS, item["task"], add=item["action"] == "add")

    return {"ok": not failed, "fixed": len(plan) - len(failed), "failed": failed}


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


def annotate_stall(tasks_dir: Path, board: dict, pipeline=None) -> dict:
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
                meta = _meta_of(path)
                state = stall_of(meta)
                if not state["stalled"]:
                    continue
                task["stalled"] = True
                # Держать пометку нечему: задача сама завершена или все её
                # блокеры дошли до конца маршрута. Маркер приглушается, но
                # остаётся — снимает человек
                if pipeline is not None:
                    stale = is_terminal(pipeline, meta.get("status", ""))
                    if not stale and state["blocked_by"] and not state["paused"]:
                        stale = all(is_terminal(pipeline, _status_of(tasks_dir, b))
                                    for b in state["blocked_by"])
                    if stale:
                        task["stall_stale"] = True
                if state["blocked_by"]:
                    task["blocked_by"] = state["blocked_by"]
                if state["paused"]:
                    task["paused"] = state["paused"]
    return board


def _status_of(tasks_dir: Path, task_id: str) -> str:
    path = find_task_file(Path(tasks_dir), task_id)
    return _meta_of(path).get("status", "") if path else ""
