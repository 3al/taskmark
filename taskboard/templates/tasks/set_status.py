#!/usr/bin/env python3
"""
Скрипт смены статуса задачи: единая точка правки frontmatter и board.md.

Статус задачи хранится в двух местах — поле `status` в файле задачи и раздел
доски. Правка их по отдельности рано или поздно приводит к рассинхрону
(в файле одно, на доске другое). Этот скрипт меняет оба места за один вызов.

Команда запуска: `py` (Windows), `python` (окружения без лаунчера py — например
Python из Microsoft Store), `python3` (macOS/Linux).

Использование:
  py tasks/set_status.py TASK-004 development
  python tasks/set_status.py TASK-004 development   # то же без лаунчера py
  py tasks/set_status.py TASK-004 testing --agent "Claude Opus 5"
  py tasks/set_status.py TASK-004 completed --position end
  py tasks/set_status.py TASK-004 cancelled --reason "дублирует TASK-002"
  py tasks/set_status.py --list             # пайплайн статусов проекта (JSON)
  py tasks/set_status.py --targets TASK-004 # куда можно двинуть задачу (JSON)

Заметку агента тоже пишет скрипт: время он берёт из системы (выставить его
задним числом «на глаз» нельзя), строку ставит в конец секции, а снесённый
заголовок «Заметки агента» возвращает на его место в шаблоне:

  py tasks/set_status.py TASK-004 --note "корень бага в _apply_role_ui()" --agent "Claude Opus 5"
  py tasks/set_status.py TASK-004 testing --note "готово к проверке" --agent "Claude Opus 5"

Почему задача стоит (блокировки и пауза) — те же два конца, что у статуса,
поэтому правятся тоже одним вызовом:

  py tasks/set_status.py TASK-014 --block TASK-013     # TASK-014 ждёт TASK-013
  py tasks/set_status.py TASK-014 --unblock TASK-013   # снять одну блокировку
  py tasks/set_status.py TASK-014 --unblock            # снять все
  py tasks/set_status.py TASK-014 --pause "ждём ответ контрагента"
  py tasks/set_status.py TASK-014 --resume
  py tasks/set_status.py --stalled                     # что стоит и почему (JSON)

Параметры:
  task_id                 Идентификатор задачи (TASK-NNN)
  status                  Новый статус из пайплайна проекта (см. --list)
  --agent TEXT            Кто меняет статус: попадёт в хвост строки доски.
                          Без него сохраняется прежний исполнитель, дата обновляется.
                          При --note — своя модель, обязательна
  --note ТЕКСТ            Заметка агента: время системное, строка — в конец секции
  --position start|end    Куда вставить в целевом разделе (по умолчанию start)
  --block TASK-NNN        Задача ждёт другую (правит blocked_by и blocks у обеих)
  --unblock [TASK-NNN]    Снять блокировку; без значения — все
  --pause ПРИЧИНА         Пауза с причиной; статус задачи при этом не меняется
  --resume                Снять паузу
  --reason ПРИЧИНА        Причина съезда с маршрута (отмены) — без неё не переведёт
  --stalled               Срез простоя: что стоит и почему (JSON)
  --tasks-dir PATH        Папка задач (по умолчанию — папка этого скрипта)
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PLACEHOLDER = "_(нет)_"


def _utf8_console() -> None:
    """UTF-8 в консоли Windows. Только для запуска как скрипт: при импорте
    подмена потоков ломает stdio вызывающего процесса (например, тестов)."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# Дефолты дублируют backend/config.py: скрипт автономен и работает
# без запущенного сервера, в том числе в проектах без установленного taskboard
DEFAULTS = {
    "board_file": "board.md",
    "queue_section": "Queue",
    "queued_status": "queued",
    "pipeline": ["backlog", "queued", "development", "review", "testing", "completed"],
    "actions": {"create": "backlog", "start": "development"},
}

# Каталог статусов — дубль backend/statuses.py (см. выше про автономность).
# Библиотека дефолтов, а не ограничение: свой ключ описывают в конфиге проекта
CATALOG = {
    "backlog": {"label": "Backlog", "section": "Backlog"},
    "todo": {"label": "To Do", "section": "To Do"},
    "queued": {"label": "Очередь", "section": "Queue"},
    # reentry: сюда возвращают с проверки, но новую работу берут не отсюда
    "to_fix": {"label": "На исправление", "section": "To Fix", "reentry": True},
    "development": {"label": "Development", "section": "Development"},
    "local_testing": {"label": "Локальная проверка", "section": "Local Testing"},
    "review": {"label": "Review", "section": "Review"},
    "to_testing": {"label": "К тестированию", "section": "To Testing"},
    "testing": {"label": "Testing", "section": "Testing"},
    "ready_for_release": {"label": "Готово к выпуску", "section": "Ready for Release"},
    "release_notes": {"label": "Заметки о релизе", "section": "Release Notes"},
    "to_release": {"label": "В ближайший релиз", "section": "To Release"},
    "ready_to_deploy": {"label": "К деплою", "section": "Ready to Deploy"},
    "completed": {"label": "Completed", "section": "Completed"},
    "done": {"label": "Done", "section": "Done"},
    "cancelled": {"label": "Отменена", "section": "Cancelled", "offramp": True},
}


def _titleize(key: str) -> str:
    return " ".join(part.capitalize() for part in key.split("_") if part)


def pipeline_of(cfg: dict) -> list[dict]:
    """Статусы проекта по порядку: [{key, label, section, offramp}].

    Порядок задаёт список из конфига. Поддержаны конфиги, написанные до
    пайплайнов: старый ключ statuses (список) и queue_section/queued_status.
    """
    keys = cfg.get("pipeline")
    if not keys:
        legacy = cfg.get("statuses")
        keys = legacy if isinstance(legacy, list) else None
    keys = list(keys) if keys else list(DEFAULTS["pipeline"])

    queued = cfg.get("queued_status")
    if queued and queued not in keys:
        keys = [queued if k == "queued" else k for k in keys]

    raw = cfg.get("statuses")
    overrides = dict(raw) if isinstance(raw, dict) else {}
    if cfg.get("queue_section"):
        meta = dict(overrides.get(queued or "queued") or {})
        meta.setdefault("section", cfg["queue_section"])
        overrides[queued or "queued"] = meta

    out = []
    for key in dict.fromkeys(k for k in keys if k):
        meta = dict(CATALOG.get(key) or {})
        meta.update(overrides.get(key) or {})
        meta.setdefault("label", _titleize(key))
        meta.setdefault("section", _titleize(key))
        meta["key"] = key
        out.append(meta)
    return out


def actions_of(cfg: dict, pipeline: list[dict]) -> dict:
    """Цели действий скиллов: создать, взять из очереди, начать, вернуть."""
    keys = [s["key"] for s in pipeline]
    actions = dict(DEFAULTS["actions"])
    actions.update({k: v for k, v in (cfg.get("actions") or {}).items() if v})

    if actions.get("create") not in keys:
        actions["create"] = keys[0] if keys else None
    if actions.get("start") not in keys:
        actions["start"] = next((k for k in keys if k != actions.get("create")), None)
    if actions.get("return") not in keys:
        actions["return"] = actions.get("start")
    if actions.get("pick") not in keys:
        # Очередь — это то, откуда берут работу: ближайший статус слева от начала
        # работы. Возвратные (to_fix) пропускаем — новую работу берут не из них
        i = keys.index(actions["start"]) if actions.get("start") in keys else 0
        actions["pick"] = next(
            (s["key"] for s in reversed(pipeline[:i])
             if not s.get("reentry") and not s.get("offramp")),
            actions.get("create"))
    return actions


def directions(pipeline: list[dict], status: str) -> dict:
    """Куда можно двинуть задачу: вперёд, назад и ожидаемый следующий шаг.

    Запретов нет — пайплайн описывает маршрут, а не забор: прыжок вперёд
    (простая задача, ночной хотфикс) законен. Ожидаемым считается ближайший
    следующий статус; съезды (cancelled) доступны всегда, но не ожидаемы.
    """
    keys = [s["key"] for s in pipeline]
    if status not in keys:
        return {"forward": [], "backward": [], "next": None}
    idx = keys.index(status)

    if pipeline[idx].get("offramp"):
        # Из съезда возвращаются в любой статус маршрута
        return {"forward": [], "backward": [k for k in keys if k != status], "next": None}

    later = keys[idx + 1:]
    offramps = [s["key"] for s in pipeline
                if s.get("offramp") and s["key"] != status and s["key"] not in later]
    nxt = next((s["key"] for s in pipeline[idx + 1:] if not s.get("offramp")), None)
    return {"forward": later + offramps, "backward": keys[:idx], "next": nxt}


def _read_json(path: Path) -> dict:
    """Прочитать json, при любой ошибке — пустой словарь."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_config(tasks_dir: Path) -> dict:
    """Дефолты → глобальный конфиг → per-project переопределения.

    Конфиг проекта живёт в tasks/.taskboard.json; прежнее расположение
    (<корень>/taskboard/config.json) читаем ради совместимости.
    """
    cfg = dict(DEFAULTS)
    cfg.update(_read_json(Path.home() / ".taskboard" / "config.json"))
    cfg.update(_read_json(tasks_dir.parent / "taskboard" / "config.json"))
    cfg.update(_read_json(tasks_dir / ".taskboard.json"))
    return cfg


def section_for_status(cfg: dict, status: str) -> str | None:
    """Заголовок раздела доски для статуса (или None, если он вне пайплайна)."""
    for meta in pipeline_of(cfg):
        if meta["key"] == status:
            return meta["section"]
    return None


def find_task_file(tasks_dir: Path, task_id: str) -> Path | None:
    for path in sorted(tasks_dir.glob(f"{task_id}*.md")):
        if re.match(rf"^{re.escape(task_id)}\b.*\.md$", path.name):
            return path
    return None


def _set_frontmatter_status(path: Path, status: str) -> bool:
    """Обновить поле status во frontmatter задачи."""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end < 0:
        return False

    header = content[:end]
    if re.search(r"^status:.*$", header, flags=re.MULTILINE):
        header = re.sub(r"^status:.*$", f"status: {status}", header, flags=re.MULTILINE)
    else:
        header += f"\nstatus: {status}"
    path.write_text(header + content[end:], encoding="utf-8")
    return True


def _entry_index(lines: list[str], task_id: str) -> int | None:
    pattern = re.compile(rf"^\s*-\s*(?:~~)?\s*{re.escape(task_id)}\s*·")
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return None


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


# Строка задачи на доске: - TASK-NNN · [Заголовок](файл.md) · агент · дата.
# Заголовок ленивый: человек ставит в него скобки («[BE] Счетчик»), и конец
# ссылки — первое `](`, а не первая `]`
_ENTRY_RE = re.compile(
    r"^\s*-\s*(?:~~)?\s*(?P<id>TASK-\d+)\s*·\s*"
    r"\[(?P<title>.+?)\]\((?P<file>[^)]+)\)(?P<tail>.*)$")


def _retail(entry: str, agent: str | None, date: str) -> str:
    """Заменить хвост записи на «· агент · дата», сохранив прежнего агента."""
    m = re.match(r"^(\s*-\s*(?:~~)?\s*TASK-\d+\s*·\s*\[.+?\]\([^)]*\))(.*)$", entry)
    if not m:
        return entry
    head, tail = m.group(1), m.group(2).strip()

    if agent is None:
        # Прежний исполнитель: первый сегмент хвоста, если он не выглядит датой
        parts = [p.strip() for p in tail.split("·") if p.strip()]
        agent = next((p for p in parts if not re.match(r"^\d{4}-\d{2}-\d{2}$", p)), None)
    if not agent:
        return head
    return f"{head} · {agent} · {date}"


def _drop_placeholder(lines: list[str], start: int, end: int) -> None:
    """Убрать заглушку пустого раздела (in-place)."""
    for i in range(start + 1, min(end, len(lines))):
        if lines[i].strip() == PLACEHOLDER:
            lines.pop(i)
            return


def _add_placeholder_if_empty(lines: list[str], name: str) -> None:
    """Вернуть заглушку в раздел, если в нём не осталось задач."""
    bounds = _section_bounds(lines, name)
    if not bounds:
        return
    start, end = bounds
    body = lines[start + 1:end]
    if any(re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", ln) for ln in body):
        return
    if any(ln.strip() == PLACEHOLDER for ln in body):
        return
    # Подразделы ### получают заглушку сами по себе — здесь только пустой ##
    if any(ln.startswith("### ") for ln in body):
        return
    lines.insert(start + 1, "")
    lines.insert(start + 2, PLACEHOLDER)


def _tidy_section(lines: list[str], name: str) -> None:
    """Причесать раздел: без двойных пустых строк, с отбивкой перед следующим ##.

    Записи переезжают между разделами, оставляя за собой то лишние пустые
    строки, то слипшийся с записью заголовок — доска должна оставаться читаемой.
    """
    bounds = _section_bounds(lines, name)
    if not bounds:
        return
    start, end = bounds
    body = lines[start + 1:end]

    tidy: list[str] = []
    for line in body:
        if not line.strip() and tidy and not tidy[-1].strip():
            continue  # схлопнуть подряд идущие пустые
        tidy.append(line)
    while tidy and not tidy[-1].strip():
        tidy.pop()
    if tidy and tidy[0].strip():
        tidy.insert(0, "")
    # Отбивка перед следующим разделом (в конце файла не нужна)
    if end < len(lines):
        tidy.append("")

    lines[start + 1:end] = tidy


def set_status(tasks_dir: Path, task_id: str, status: str,
               agent: str | None = None, position: str = "start",
               force: bool = False, reason: str | None = None) -> dict:
    """
    Перевести задачу в новый статус: frontmatter + раздел board.md.

    Возвращает {"ok": True, "section": ...} либо {"ok": False, "error": ...}.
    Операция идемпотентна: повторный вызов с тем же статусом ничего не ломает.

    force — взять в работу стоящую задачу: блокировка ровно от этого и
    защищает, поэтому без явного признака такой перевод не выполняется.

    reason — причина съезда с маршрута (отмены). Обязательна: из отмены не
    возвращаются, и «почему» должно остаться в файле.
    """
    tasks_dir = Path(tasks_dir)
    cfg = load_config(tasks_dir)

    pipeline = pipeline_of(cfg)
    known = [s["key"] for s in pipeline]
    if status not in known:
        return {"ok": False,
                "error": f"Статус {status} не входит в пайплайн проекта. "
                         f"Допустимо: {', '.join(known)}"}

    meta_status = next((s for s in pipeline if s["key"] == status), {})
    reason = _one_line(reason)
    if meta_status.get("offramp") and not reason:
        return {"ok": False,
                "error": f"Нужна причина: перевод в «{meta_status.get('label', status)}» "
                         f"без неё не выполняется — добавьте --reason \"…\""}

    # Стоящую задачу берут в работу — единственный аномальный переход: ждать
    # внутри ревью или тестирования законно, назад по маршруту тем более, а в
    # терминальном простой снимется сам. Зеркало backend/stall.py
    task_path = find_task_file(tasks_dir, task_id)
    actions = actions_of(cfg, pipeline)
    if task_path is not None and not force and status in (actions.get("start"),
                                                          actions.get("return")):
        state = stall_of(_read_meta(task_path))
        if state["stalled"]:
            parts = []
            if state["blocked_by"]:
                parts.append(f"ждёт {', '.join(state['blocked_by'])}")
            if state["paused"]:
                parts.append(f"на паузе: {state['paused']}")
            return {"ok": False,
                    "error": f"{task_id} {' и '.join(parts)}. "
                             f"Всё равно взять в работу — повторите с --force"}

    section = section_for_status(cfg, status)
    if not section:
        return {"ok": False, "error": f"Не удалось определить раздел для статуса {status}"}

    task_file = find_task_file(tasks_dir, task_id)
    if task_file is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}

    board_path = tasks_dir / cfg.get("board_file", "board.md")
    if not board_path.is_file():
        return {"ok": False, "error": f"Файл доски не найден: {board_path}"}

    lines = board_path.read_text(encoding="utf-8").splitlines()
    src_idx = _entry_index(lines, task_id)
    if src_idx is None:
        return {"ok": False, "error": f"{task_id} не найден на доске — добавьте запись вручную"}

    if _section_bounds(lines, section) is None:
        return {"ok": False, "error": f"Раздел «{section}» отсутствует в {board_path.name}"}

    # Из какого раздела забираем — чтобы вернуть в него заглушку
    from_section = None
    for i in range(src_idx, -1, -1):
        m = re.match(r"^##\s+(.*)$", lines[i])
        if m:
            from_section = m.group(1).strip()
            break

    date = datetime.now().strftime("%Y-%m-%d")
    entry = _retail(lines.pop(src_idx), agent, date)

    start, end = _section_bounds(lines, section) or (0, 0)
    _drop_placeholder(lines, start, end)
    start, end = _section_bounds(lines, section) or (0, 0)

    task_lines = [
        i for i in range(start + 1, end)
        if re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", lines[i])
    ]
    if position == "end" and task_lines:
        insert_at = task_lines[-1] + 1
    elif task_lines:
        insert_at = task_lines[0]
    else:
        # Пустой раздел: сразу после заголовка, отделив одной пустой строкой
        insert_at = start + 1
        if insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        else:
            lines.insert(insert_at, "")
            insert_at += 1
    lines.insert(insert_at, entry)

    if from_section and from_section.lower() != section.lower():
        _add_placeholder_if_empty(lines, from_section)

    _tidy_section(lines, section)
    if from_section:
        _tidy_section(lines, from_section)

    board_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not _set_frontmatter_status(task_file, status):
        return {"ok": False,
                "error": f"Доска обновлена, но во {task_file.name} нет frontmatter — "
                         f"проставьте status: {status} вручную"}

    # Прыжок вперёд законен, но пропущенные шаги стоит видеть в логе
    keys = [s["key"] for s in pipeline]
    prev = next((s["key"] for s in pipeline
                 if from_section and s["section"].lower() == from_section.lower()), None)
    skipped: list[str] = []
    if prev in keys and status in keys and keys.index(status) > keys.index(prev) + 1:
        # Съезды и возвратные статусы не «пропускаются»: маршрут через них не идёт
        between = pipeline[keys.index(prev) + 1:keys.index(status)]
        skipped = [s["key"] for s in between
                   if not s.get("offramp") and not s.get("reentry")]

    if reason:
        _set_fields(task_file, {"cancel_reason": reason})

    # Доехали до конца маршрута — «ждёт» про закрытую задачу больше не правда
    cleared = clear_stall(tasks_dir, task_id) if is_terminal(pipeline, status) else None

    return {"ok": True, "task": task_id, "status": status, "section": section,
            "file": task_file.name, "from": prev, "skipped": skipped,
            "stall_cleared": bool(cleared and cleared.get("cleared")),
            # Смена статуса — единственный момент, когда файл задачи заведомо
            # открывают: заодно показываем, что в нём разъехалось
            "warnings": check_task_file(task_file)}


# --- Заметки агента и структура файла задачи --------------------------------
# Заметку пишет скрипт, а не агент руками: время он берёт из системы, поэтому
# выставить его «на глаз» задним числом нельзя, а строка всегда встаёт в конец
# секции — так лог остаётся хронологией, а не набором строк в случайном порядке.

NOTES_SECTION = "Заметки агента"
COMMITS_SECTION = "История коммитов"

# Порядок секций файла задачи — эталон tasks/_TEMPLATE.md. «История доработок»
# появляется только после возврата с ревью, поэтому необязательна
TASK_SECTIONS = ("Описание", "Чеклист", "История доработок",
                 NOTES_SECTION, COMMITS_SECTION)

NOTE_RE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\*\* · [^·]+ · .+$")


def _headings(lines: list[str]) -> list[str]:
    """Заголовки второго уровня файла в порядке следования."""
    out = []
    for line in lines:
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def check_task_file(path: Path) -> list[str]:
    """Что разъехалось в файле задачи: секции, их порядок, хронология заметок.

    Не запрет, а видимость: файл правят руками, и нарушения копятся молча —
    снесённый заголовок секции, «История коммитов» посреди файла, заметки
    вразнобой. Проверка идёт при каждой смене статуса, поэтому агент видит
    список сразу, а не через пять задач.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []

    warnings: list[str] = []
    heads = _headings(lines)
    lower = [h.lower() for h in heads]

    # «История коммитов» появляется при первом коммите — её отсутствие законно,
    # а вот секция заметок есть в шаблоне всегда: нет её — заголовок снесли
    if NOTES_SECTION.lower() not in lower:
        warnings.append(f"нет секции «{NOTES_SECTION}»: заголовки задачи — её структура, "
                        f"их не сносят (эталон — tasks/_TEMPLATE.md)")

    if lower and COMMITS_SECTION.lower() in lower and lower[-1] != COMMITS_SECTION.lower():
        warnings.append(f"«{COMMITS_SECTION}» не последняя секция файла")

    for name in TASK_SECTIONS:
        if lower.count(name.lower()) > 1:
            warnings.append(f"секция «{name}» встречается {lower.count(name.lower())} раза: "
                            f"дописывать нужно в существующую, а не заводить вторую")

    known = [h for h in heads if h.lower() in [s.lower() for s in TASK_SECTIONS]]
    order = [s.lower() for s in TASK_SECTIONS]
    positions = [order.index(h.lower()) for h in known]
    if positions != sorted(positions):
        warnings.append("секции идут не в порядке шаблона: "
                        + " → ".join(s for s in TASK_SECTIONS if s.lower() in lower))

    bounds = _section_bounds(lines, NOTES_SECTION)
    if bounds:
        start, end = bounds
        stamps: list[str] = []
        malformed = 0
        in_comment = False
        for i in range(start + 1, end):
            line = lines[i].rstrip()
            # Комментарии агенту читателю не видны; отступ — продолжение списка
            if in_comment:
                in_comment = "-->" not in line
                continue
            if line.lstrip().startswith("<!--"):
                in_comment = "-->" not in line
                continue
            if not line.strip() or line.startswith((" ", "\t")):
                continue
            m = NOTE_RE.match(line)
            if m:
                stamps.append(m.group(1))
            elif line.startswith("- "):
                # Ругаемся только на попытку записать заметку: свободный текст
                # старых задач (формат до перехода на строки списка) не наше дело
                malformed += 1
        if malformed:
            warnings.append(f"строк не в формате заметки: {malformed} "
                            f"(нужно `- **ГГГГ-ММ-ДД ЧЧ:ММ** · Модель · суть`)")
        for prev, cur in zip(stamps, stamps[1:]):
            if cur < prev:
                warnings.append(f"заметки не в хронологии: {cur} после {prev} — "
                                f"новая заметка пишется в конец секции (--note)")
                break

    return warnings


def _insert_notes_section(lines: list[str]) -> list[str]:
    """Вернуть снесённую секцию заметок на её место — перед историей коммитов."""
    block = [f"## {NOTES_SECTION}", ""]
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.*)$", line)
        if m and m.group(1).strip().lower() == COMMITS_SECTION.lower():
            return lines[:i] + block + lines[i:]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines + [""] + block


def add_note(tasks_dir: Path, task_id: str, text: str,
             agent: str | None = None) -> dict:
    """Дописать заметку агента в конец секции «Заметки агента».

    agent обязателен: модель — единственное, чего скрипт про агента не знает,
    а копирование её из соседней строки как раз и делает историю ложной.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}

    text = _one_line(text)
    if not text:
        return {"ok": False, "error": "Пустая заметка: нужен текст — --note \"суть\""}

    agent = _one_line(agent)
    if not agent:
        return {"ok": False,
                "error": "Не указана модель: добавьте --agent \"Модель\". Имя берётся "
                         "из текущей сессии, а не из соседней строки заметок"}

    note = f"- **{datetime.now().strftime('%Y-%m-%d %H:%M')}** · {agent} · {text}"

    lines = path.read_text(encoding="utf-8").splitlines()
    if _section_bounds(lines, NOTES_SECTION) is None:
        lines = _insert_notes_section(lines)
    start, end = _section_bounds(lines, NOTES_SECTION) or (0, 0)

    body = lines[start + 1:end]
    while body and not body[-1].strip():
        body.pop()
    if body and body[0].strip():
        body.insert(0, "")
    elif not body:
        body = [""]
    body.append(note)
    if end < len(lines):
        body.append("")
    lines[start + 1:end] = body

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "task": task_id, "file": path.name, "note": note,
            "warnings": check_task_file(path)}


def current_status(tasks_dir: Path, task_id: str) -> str | None:
    """Статус задачи из frontmatter."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return None
    m = re.search(r"^status:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return m.group(1) if m else None


def queue(tasks_dir: Path, limit: int = 5) -> dict:
    """Живая очередь: раздел `actions.pick` доски прямо сейчас.

    Нужна, чтобы агент не называл очередь по памяти: доску правят через UI и
    другие агенты, и снимок, прочитанный в начале сессии, устаревает молча.
    Чтение всей доски ради этого — лишние тысячи токенов, поэтому здесь
    компактный срез: верхушка очереди в порядке следования.

    limit — сколько задач вернуть (0 — всю очередь).
    """
    tasks_dir = Path(tasks_dir)
    cfg = load_config(tasks_dir)
    pipeline = pipeline_of(cfg)
    status = actions_of(cfg, pipeline).get("pick")
    section = section_for_status(cfg, status) if status else None
    out: dict = {"status": status, "section": section, "total": 0, "tasks": []}
    if not section:
        return out

    board = tasks_dir / cfg.get("board_file", "board.md")
    if not board.is_file():
        return out
    lines = board.read_text(encoding="utf-8").splitlines()
    bounds = _section_bounds(lines, section)
    if not bounds:
        return out

    start, end = bounds
    tasks: list[dict] = []
    for i in range(start + 1, end):
        m = _ENTRY_RE.match(lines[i])
        if not m:
            continue
        # Признак простоя идёт вместе с очередью: без него агент не отличит
        # стоящую задачу от свободной и возьмёт первую сверху
        path = find_task_file(tasks_dir, m.group("id"))
        state = stall_of(_read_meta(path)) if path else stall_of({})
        tasks.append({"position": len(tasks) + 1, "id": m.group("id"),
                      "title": m.group("title"), "file": m.group("file"),
                      "meta": re.sub(r"^·\s*", "", m.group("tail").strip()).strip(),
                      "stalled": state["stalled"],
                      "blocked_by": state["blocked_by"], "paused": state["paused"]})
    out["total"] = len(tasks)
    out["tasks"] = tasks if limit <= 0 else tasks[:limit]
    return out


# --- Простой задачи: блокировки и пауза -------------------------------------
# Зеркало backend/stall.py: скрипт автономен и работает без сервера, поэтому
# правила живут в двух местах и должны меняться синхронно.

EMPTY = "~"


def parse_ids(value) -> list[str]:
    """Список задач из значения поля: «~», пусто и None — это «нет»."""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    out: list[str] = []
    for part in re.split(r"[,\s]+", str(value or "")):
        part = part.strip().upper()
        if not part or part == EMPTY or part in out:
            continue
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


def _read_meta(path: Path) -> dict:
    """Frontmatter файла задачи (key: value до закрывающего ---)."""
    content = path.read_text(encoding="utf-8-sig")
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end < 0:
        return {}
    meta = {}
    for line in content[3:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


def _set_fields(path: Path, updates: dict) -> bool:
    """Записать поля frontmatter; отсутствующие дописать в конец шапки."""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end < 0:
        return False

    header = content[:end]
    for key, value in updates.items():
        line = f"{key}: {value}"
        pattern = rf"^{re.escape(key)}:.*$"
        if re.search(pattern, header, flags=re.MULTILINE):
            header = re.sub(pattern, lambda _m, ln=line: ln, header, flags=re.MULTILINE)
        else:
            header = header.rstrip("\n") + f"\n{line}"
    path.write_text(header + content[end:], encoding="utf-8")
    return True


def is_terminal(pipeline: list[dict], status: str) -> bool:
    """Конец маршрута: терминальный статус или съезд.

    Имена не подставляем: пайплайн настраивается, и терминал может называться
    как угодно. Признак структурный — нет ожидаемого следующего шага.
    """
    if not status:
        return False
    keys = [s["key"] for s in pipeline]
    if status not in keys:
        return False
    return directions(pipeline, status)["next"] is None


def can_stall(pipeline: list[dict], status: str) -> dict:
    """Можно ли ставить простой на задачу в этом статусе: `{ok, reason}`.

    Стоять может задача, у которой есть следующий шаг маршрута: для
    завершённой или отменённой «ждёт» — не правда, а мусор в данных.
    Зеркало backend/stall.py: правила должны совпадать.
    """
    if not is_terminal(pipeline, status):
        return {"ok": True, "reason": ""}
    meta = next((s for s in pipeline if s["key"] == status), {})
    label = meta.get("label", status)
    what = "снята с маршрута" if meta.get("offramp") else "завершена"
    return {"ok": False,
            "reason": f"Задача {what} (статус «{label}») — простой не имеет смысла"}


def stall_of(meta: dict) -> dict:
    """Производное состояние «стоит» по frontmatter задачи."""
    blocked_by = parse_ids(meta.get("blocked_by"))
    paused = _one_line(meta.get("paused"))
    return {"blocked_by": blocked_by, "blocks": parse_ids(meta.get("blocks")),
            "paused": paused, "stalled": bool(blocked_by or paused)}


def _edit_list_field(path: Path, field: str, task_id: str, add: bool) -> None:
    ids = parse_ids(_read_meta(path).get(field))
    if add and task_id not in ids:
        ids.append(task_id)
    elif not add and task_id in ids:
        ids.remove(task_id)
    else:
        return
    _set_fields(path, {field: format_ids(ids)})


def set_blocked_by(tasks_dir: Path, task_id: str, ids) -> dict:
    """Задать блокеров задачи, синхронно правя `blocks` у них.

    Два конца зависимости — та же ловушка, что статус в файле и на доске:
    правка одного руками разъезжается молча. Поэтому правим оба сразу.
    """
    tasks_dir = Path(tasks_dir)
    task_id = task_id.strip().upper()
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}

    ids = parse_ids(ids)
    if task_id in ids:
        return {"ok": False, "error": "Задача не может блокировать сама себя"}

    old = parse_ids(_read_meta(path).get("blocked_by"))
    _set_fields(path, {"blocked_by": format_ids(ids)})

    missing = []
    for blocker in ids:
        blocker_path = find_task_file(tasks_dir, blocker)
        if blocker_path is None:
            missing.append(blocker)
            continue
        _edit_list_field(blocker_path, "blocks", task_id, add=True)

    for blocker in old:
        if blocker in ids:
            continue
        blocker_path = find_task_file(tasks_dir, blocker)
        if blocker_path is not None:
            _edit_list_field(blocker_path, "blocks", task_id, add=False)

    return {"ok": True, "task": task_id, "blocked_by": ids, "missing": missing}


def _stall_allowed(tasks_dir: Path, path: Path) -> dict:
    """Разрешено ли ставить простой на задачу в её текущем статусе."""
    cfg = load_config(Path(tasks_dir))
    return can_stall(pipeline_of(cfg), _read_meta(path).get("status", ""))


def block(tasks_dir: Path, task_id: str, blocker: str) -> dict:
    """Добавить блокера к задаче."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    verdict = _stall_allowed(tasks_dir, path)
    if not verdict["ok"]:
        return {"ok": False, "error": verdict["reason"]}
    current = parse_ids(_read_meta(path).get("blocked_by"))
    return set_blocked_by(tasks_dir, task_id, current + parse_ids(blocker))


def unblock(tasks_dir: Path, task_id: str, blocker: str = "") -> dict:
    """Снять блокера (без аргумента — всех)."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    drop = parse_ids(blocker)
    current = parse_ids(_read_meta(path).get("blocked_by"))
    keep = [i for i in current if i not in drop] if drop else []
    return set_blocked_by(tasks_dir, task_id, keep)


def set_paused(tasks_dir: Path, task_id: str, reason: str) -> dict:
    """Поставить задачу на паузу с причиной (пустая причина — снять).

    Пауза — метка, а не статус: `status` и раздел доски остаются как были.
    """
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    reason = _one_line(reason)
    # Снятие разрешено всегда: убрать мусор нужно и там, где поставить нельзя
    if reason:
        verdict = _stall_allowed(tasks_dir, path)
        if not verdict["ok"]:
            return {"ok": False, "error": verdict["reason"]}
    _set_fields(path, {"paused": reason or EMPTY})
    return {"ok": True, "task": task_id.strip().upper(), "paused": reason}


def clear_stall(tasks_dir: Path, task_id: str) -> dict:
    """Снять с задачи и блокировки, и паузу (при переезде в конец маршрута)."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "cleared": False}
    state = stall_of(_read_meta(path))
    if not state["stalled"]:
        return {"ok": True, "cleared": False}
    if state["blocked_by"]:
        set_blocked_by(tasks_dir, task_id, [])
    if state["paused"]:
        set_paused(tasks_dir, task_id, "")
    return {"ok": True, "cleared": True,
            "blocked_by": state["blocked_by"], "paused": state["paused"]}


def stalled(tasks_dir: Path) -> dict:
    """Срез «что сейчас стоит и почему» — для агента, как `--queue`.

    Причина простоя лежит во frontmatter, поэтому доску читать незачем.
    """
    tasks_dir = Path(tasks_dir)
    tasks = []
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        m = re.match(r"^(TASK-\d+).*\.md$", path.name)
        if not m:
            continue
        try:
            meta = _read_meta(path)
        except OSError:
            continue
        state = stall_of(meta)
        if not state["stalled"]:
            continue
        tasks.append({"id": m.group(1).upper(), "title": meta.get("title", ""),
                      "status": meta.get("status", ""), "file": path.name,
                      "blocked_by": state["blocked_by"], "paused": state["paused"]})
    return {"total": len(tasks), "tasks": tasks}


def describe(tasks_dir: Path, task_id: str | None = None) -> dict:
    """Пайплайн проекта и — для задачи — законные цели перехода.

    Это единственный источник знаний о жизненном цикле для скиллов: они
    выражают намерение (продвинуть, вернуть, взять в работу), а конкретные
    статусы берут отсюда, поэтому не содержат жёстких имён.
    """
    cfg = load_config(Path(tasks_dir))
    pipeline = pipeline_of(cfg)
    out: dict = {
        "pipeline": [{"key": s["key"], "label": s["label"], "section": s["section"],
                      "offramp": bool(s.get("offramp"))} for s in pipeline],
        "actions": actions_of(cfg, pipeline),
    }
    if task_id:
        status = current_status(Path(tasks_dir), task_id)
        out["task"] = task_id
        out["current"] = status
        out.update(directions(pipeline, status or ""))
    return out


def main() -> None:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Сменить статус задачи (файл + доска)")
    parser.add_argument("task_id", nargs="?", help="Идентификатор задачи (TASK-NNN)")
    parser.add_argument("status", nargs="?", help="Новый статус")
    parser.add_argument("--list", action="store_true",
                        help="Показать пайплайн статусов проекта (JSON)")
    parser.add_argument("--targets", metavar="TASK-NNN", default=None,
                        help="Законные цели перехода для задачи (JSON)")
    parser.add_argument("--queue", action="store_true",
                        help="Живая очередь доски прямо сейчас (JSON)")
    parser.add_argument("--limit", type=int, default=5,
                        help="Сколько задач очереди показать, 0 — все (default: 5)")
    parser.add_argument("--block", metavar="TASK-NNN", default=None,
                        help="Задача ждёт другую: правит blocked_by и blocks у обеих")
    parser.add_argument("--unblock", metavar="TASK-NNN", nargs="?", const="", default=None,
                        help="Снять блокировку (без значения — все блокировки)")
    parser.add_argument("--pause", metavar="ПРИЧИНА", default=None,
                        help="Пауза с причиной: статус задачи не меняется")
    parser.add_argument("--resume", action="store_true",
                        help="Снять паузу")
    parser.add_argument("--stalled", action="store_true",
                        help="Что сейчас стоит и почему (JSON)")
    parser.add_argument("--note", metavar="ТЕКСТ", default=None,
                        help="Дописать заметку агента (время — системное, строка — в конец)")
    parser.add_argument("--agent", default=None,
                        help="Кто меняет статус (попадёт в строку доски); при --note — модель")
    parser.add_argument("--position", choices=["start", "end"], default="start",
                        help="Позиция в целевом разделе (default: start)")
    parser.add_argument("--force", action="store_true",
                        help="Взять в работу стоящую задачу (блокировка/пауза)")
    parser.add_argument("--reason", default=None, metavar="ПРИЧИНА",
                        help="Причина съезда с маршрута (отмены) — обязательна")
    parser.add_argument("--tasks-dir", default=None,
                        help="Папка задач (default: папка этого скрипта)")
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else Path(__file__).parent

    if args.queue:
        print(json.dumps(queue(tasks_dir, args.limit), ensure_ascii=False, indent=2))
        return

    if args.stalled:
        print(json.dumps(stalled(tasks_dir), ensure_ascii=False, indent=2))
        return

    if args.list or args.targets:
        print(json.dumps(describe(tasks_dir, args.targets), ensure_ascii=False, indent=2))
        return

    # Блокировки и пауза правят только frontmatter: статус задачи и её раздел
    # доски остаются на месте, поэтому эти флаги идут без аргумента `status`
    stall_flags = (args.block is not None or args.unblock is not None
                   or args.pause is not None or args.resume)
    if stall_flags:
        if not args.task_id:
            parser.error("нужен TASK-NNN для --block / --unblock / --pause / --resume")

        def apply(result: dict, message) -> None:
            if not result.get("ok"):
                print(f"[ERROR] {result.get('error')}", file=sys.stderr)
                sys.exit(1)
            print(message(result))
            if result.get("missing"):
                print(f"[i] задачи не найдены в проекте: {', '.join(result['missing'])}")

        if args.block:
            apply(block(tasks_dir, args.task_id, args.block),
                  lambda r: f"[OK] {r['task']} ждёт {format_ids(r['blocked_by'])}")
        if args.unblock is not None:
            apply(unblock(tasks_dir, args.task_id, args.unblock),
                  lambda r: f"[OK] {r['task']}: блокировки — {format_ids(r['blocked_by'])}")
        if args.pause:
            apply(set_paused(tasks_dir, args.task_id, args.pause),
                  lambda r: f"[OK] {r['task']} на паузе: {r['paused']}")
        if args.resume:
            apply(set_paused(tasks_dir, args.task_id, ""),
                  lambda r: f"[OK] {r['task']}: пауза снята")

        if not args.status and args.note is None:
            return

    # Заметка не трогает ни статус, ни доску — идёт и сама по себе, и вместе
    # со сменой статуса (финализация пишет заметку и двигает задачу за раз)
    if args.note is not None:
        if not args.task_id:
            parser.error("нужен TASK-NNN для --note")
        result = add_note(tasks_dir, args.task_id, args.note, agent=args.agent)
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"[OK] {result['task']}: заметка записана в «{NOTES_SECTION}»")
        print(result["note"])
        for warning in result.get("warnings", []):
            print(f"[!] {warning}")
        if not args.status:
            return

    if not args.task_id or not args.status:
        parser.error("нужны TASK-NNN и статус "
                     "(либо --list / --targets / --queue / --stalled / --block / --pause)")

    result = set_status(tasks_dir, args.task_id, args.status,
                        agent=args.agent, position=args.position, force=args.force,
                        reason=args.reason)

    if not result.get("ok"):
        print(f"[ERROR] {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] {result['task']} → {result['status']} (раздел «{result['section']}»)")
    if result.get("stall_cleared"):
        print("[i] простой снят: задача дошла до конца маршрута")
    if result.get("skipped"):
        # Не запрет, а видимость: пайплайн описывает ожидаемый маршрут
        print(f"[i] минуя {', '.join(result['skipped'])}")
    for warning in result.get("warnings", []):
        print(f"[!] {warning}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CANCEL] Отменено пользователем", file=sys.stderr)
        sys.exit(1)
