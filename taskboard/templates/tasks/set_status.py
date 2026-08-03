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

# Контракт с инструментом: что умеет эта копия скрипта. Развёрнутая копия
# живёт в проекте пользователя и обновляется отдельно, кнопкой, — значит
# конфиг может опережать её. Без маркера расхождение молчаливо: настройка
# объявлена, скрипт про неё не знает, а человек уверен, что она работает.
# Имена, а не номер версии, — как CAPABILITIES в backend/app.py: набор
# расширяется, не заводя таблицы соответствия версий возможностям.
SCRIPT_CAPABILITIES = {"stall", "task_types", "requires"}

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
    # recommends — что этап просит на выходе, пока проект не объявил этого
    # требованием: печатается напоминанием, ничего не запрещая. В рекомендации
    # попадает только то, что уже стреляло на практике (TASK-101), а не всё,
    # что легко проверить
    "testing": {"label": "Testing", "section": "Testing",
                "recommends": [{"id": "verified", "check": "confirm",
                                "ask": "проверку подтвердил человек"}]},
    "ready_for_release": {"label": "Готово к выпуску", "section": "Ready for Release"},
    "release_notes": {"label": "Заметки о релизе", "section": "Release Notes",
                      # section_present, а не filled: пустая секция — принятое
                      # решение «пользователю сказать нечего», и требовать текст
                      # значило бы ломать это решение
                      "recommends": [{"id": "release_text", "check": "section_present",
                                      "name": "Изменение для пользователя",
                                      "ask": "тексты релиза написаны"},
                                     {"id": "release_ok", "check": "confirm",
                                      "ask": "тексты релиза утверждены человеком"}]},
    "to_release": {"label": "В ближайший релиз", "section": "To Release"},
    "ready_to_deploy": {"label": "К деплою", "section": "Ready to Deploy"},
    "completed": {"label": "Completed", "section": "Completed"},
    "done": {"label": "Done", "section": "Done"},
    "cancelled": {"label": "Отменена", "section": "Cancelled", "offramp": True},
}


# Типы задач — дубль backend/config.py (см. выше про автономность). В отличие
# от статусов это **не** библиотека дефолтов, а закрытый список: тип отвечает
# на вопрос «что это за работа», и ответ не зависит от жизненного цикла проекта.
# letter — буква кружка на превью доски, буквы не повторяются
# section — заголовок рубрики бэклога, куда create_task.py кладёт новую задачу
TASK_TYPES = {
    "feature":    {"label": "Новый функционал", "section": "Новый функционал",
                   "letter": "Н", "color": "sky"},
    "bug":        {"label": "Баг",              "section": "Баги",
                   "letter": "Б", "color": "rose"},
    "refactor":   {"label": "Рефакторинг",      "section": "Рефакторинг",
                   "letter": "Р", "color": "violet"},
    "cleanup":    {"label": "Уборка",           "section": "Уборка",
                   "letter": "У", "color": "emerald"},
    "discussion": {"label": "Обсуждение",       "section": "Обсуждения",
                   "letter": "О", "color": "amber"},
    "design":     {"label": "Дизайн",           "section": "Дизайн",
                   "letter": "Д", "color": "fuchsia"},
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

    # Гейт требований этапа. Стоит только здесь, на агентском пути: доска
    # пропускает всегда, и это осознанно — у человека есть контекст, которого
    # у агента нет. Пересечение считаем от статуса из файла: раздел доски мог
    # разъехаться с ним, а источник правды — файл
    from_status = current_status(tasks_dir, task_id) or ""
    pending = unmet(move_requirements(cfg, pipeline, from_status, status), task_file)
    blocked = [r for r in pending if r.get("mandatory")]
    if blocked:
        return {"ok": False,
                "error": gate_message(task_id, pipeline, from_status, status, blocked)}

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

    # Возврат назад: этапы правее цели задача пройдёт заново, и подтверждения
    # прошлой итерации к новой не относятся
    keys_all = [s["key"] for s in pipeline]
    unconfirmed: list[str] = []
    if (from_status in keys_all and status in keys_all
            and keys_all.index(status) < keys_all.index(from_status)):
        unconfirmed = reset_confirmations(task_file, cfg, pipeline, status)
        if unconfirmed:
            # Строкой в заметки, а не только в консоль: снятие объясняет, почему
            # то же самое подтверждали дважды. Без него история показывает два
            # подтверждения подряд без причины между ними.
            # Имя — своё, если передано; иначе событие подписывает сам скрипт:
            # сброс сделал он, а выдумывать за агента модель нельзя
            # Формулировки, а не идентификаторы: правило для всех строк,
            # которые читает человек, — одно
            add_note(tasks_dir, task_id,
                     f"возврат в «{_label_of(pipeline, status)}» — снято "
                     f"подтверждение: "
                     f"{'; '.join(requirement_names(cfg, pipeline, unconfirmed))}",
                     agent=agent or "set_status.py")

    # Работа кончилась — самое время сказать про волт и хвосты в файле задачи:
    # позже, при выпуске, автор деталей уже не помнит
    reminders = (finish_reminders(tasks_dir, task_id, task_file, cfg)
                 if status == work_done_status(cfg, pipeline) else [])

    return {"ok": True, "task": task_id, "status": status, "section": section,
            "file": task_file.name, "from": prev, "skipped": skipped,
            "stall_cleared": bool(cleared and cleared.get("cleared")),
            "unconfirmed": unconfirmed,
            "reminders": reminders,
            # Отдельным ключом от reminders: это разные механизмы — конец работы
            # напоминает о хвостах задачи, этап говорит о невыполненном на выходе
            "stage_reminders": stage_reminders(pipeline, from_status, pending),
            # Что потребует новый этап — сказанное на входе, а не в момент отказа
            "announce": stage_announcement(cfg, pipeline, status),
            # Смена статуса — единственный момент, когда файл задачи заведомо
            # открывают: заодно показываем, что в нём разъехалось
            "warnings": check_task_file(task_file)}


# --- Заметки агента и структура файла задачи --------------------------------
# Заметку пишет скрипт, а не агент руками: время он берёт из системы, поэтому
# выставить его «на глаз» задним числом нельзя, а строка всегда встаёт в конец
# секции — так лог остаётся хронологией, а не набором строк в случайном порядке.

NOTES_SECTION = "Заметки агента"
COMMITS_SECTION = "История коммитов"
CHECKLIST_SECTION = "Чеклист"

# Порядок секций файла задачи — эталон tasks/_TEMPLATE.md. «История доработок»
# появляется только после возврата с ревью, поэтому необязательна
RELEASE_SECTION = "Изменение для пользователя"

# Порядок секций файла задачи. «История доработок» появляется после возврата
# с ревью, «Изменение для пользователя» — при отборе в выпуск: обе создаются
# скиллами и в шаблоне новой задачи не нужны
TASK_SECTIONS = ("Описание", RELEASE_SECTION, CHECKLIST_SECTION, "История доработок",
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


def set_type(tasks_dir: Path, task_id: str, value: str) -> dict:
    """Сменить тип задачи. Тип — метка работы, статус и доску он не трогает.

    Правится скриптом, а не руками: значение закрытое, и опечатка в нём
    означает молча пропавшую метку на доске.
    """
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    value = (value or "").strip().lower()
    if value not in TASK_TYPES:
        return {"ok": False,
                "error": f"Неизвестный тип задачи: {value or '(пусто)'} "
                         f"(допустимо: {', '.join(TASK_TYPES)})"}
    _set_fields(path, {"type": value})
    return {"ok": True, "task": task_id.strip().upper(), "type": value,
            "label": TASK_TYPES[value]["label"]}


def types() -> dict:
    """Каталог типов задач: список спрашивают у скрипта, а не помнят."""
    return {"types": [{"key": key, **meta} for key, meta in TASK_TYPES.items()]}


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


# --- НАЧАЛО БЛОКА: требования этапа -----------------------------------------
# Блок обособлен маркерами намеренно: те же правила нужны бэкенду, чтобы рисовать
# долг на карточке, и переносить их туда следует копированием, а не восстановлением
# логики по следам.
#
# Чего механизм НЕ делает: он не гарантирует, что этап выполнен. Доска пропускает
# всегда, HTTP-API открыт, `--waive` существует. Он гарантирует, что этап нельзя
# пройти **молча**: пропуск перестаёт быть режимом умолчания и становится
# поступком — названным и оставившим след.
#
# Две реакции на одно требование: рекомендация статуса (объявлена в CATALOG, в
# конфиге проекта её нет) печатает напоминание, требование из `requires` проекта
# даёт отказ. Слова у них одни и те же — разница в том, состоялся переход или нет.

CONFIRMED_FIELD = "confirmed"
WAIVED_FIELD = "waived"


def _req_list(value) -> list[dict]:
    """Объявления требований из конфига: без `id` требование не существует.

    Идентификатор обязателен, потому что именно он попадает во frontmatter:
    пользовательский текст с запятой внутри рвал бы разбор плоского списка.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(r) for r in value if isinstance(r, dict) and _one_line(r.get("id"))]


def parse_req_ids(value) -> list[str]:
    """Идентификаторы требований из поля frontmatter — как их написал человек.

    Отдельно от `parse_ids`: тот приводит к верхнему регистру, потому что разбирает
    `TASK-NNN`. Идентификатор требования человек пишет в конфиге строчными, и в
    файле задачи он обязан выглядеть так же — иначе конфиг и данные читаются как
    разные вещи. Сравнение при этом регистронезависимое.
    """
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    out: list[str] = []
    for part in re.split(r"[,\s]+", str(value or "")):
        part = part.strip()
        if not part or part == EMPTY or part.lower() in [o.lower() for o in out]:
            continue
        out.append(part)
    return out


def format_req_ids(ids) -> str:
    """Значение поля из списка идентификаторов (пустой список — «~»)."""
    ids = parse_req_ids(ids)
    return ", ".join(ids) if ids else EMPTY


def requirement_text(req: dict) -> str:
    """Голая формулировка требования, как её написал человек в конфиге.

    По смыслу это утверждение о выполненном («проверку подтвердил человек»),
    поэтому в заметках она стоит сама по себе, без служебного префикса.
    """
    return _one_line(req.get("ask") or req.get("name") or req.get("id"))


def requirement_wording(req: dict) -> str:
    """Как требование называется в отказе и напоминании: текст плюс идентификатор.

    Идентификатор здесь обязателен — им гасят требование (`--confirm <id>`), и
    отказ, не назвавший его, заставляет лезть в конфиг.
    """
    return f"«{requirement_text(req)}» ({_one_line(req.get('id'))})"


def requirement_names(cfg: dict, pipeline: list[dict], ids: list[str]) -> list[str]:
    """Формулировки требований по идентификаторам — для строк, которые читает человек.

    Нет объявления — остаётся идентификатор: врать нечем.
    """
    out = []
    for req_id in ids:
        req = requirement_by_id(cfg, pipeline, req_id)
        out.append(requirement_text(req) if req else req_id)
    return out


def requirement_by_id(cfg: dict, pipeline: list[dict], req_id: str) -> dict | None:
    """Найти объявление требования по идентификатору — где бы оно ни стояло.

    Нужно там, где показать надо формулировку, а на руках только `id`:
    идентификатор — служебное имя, и в тексте, который читает человек, ему не
    место.
    """
    needle = _one_line(req_id).lower()
    for status in [s["key"] for s in pipeline]:
        for req in stage_requirements(cfg, pipeline, status):
            if _one_line(req.get("id")).lower() == needle:
                return req
    return None


def stage_requirements(cfg: dict, pipeline: list[dict], status: str) -> list[dict]:
    """Что этап просит на выходе: объявленное проектом и рекомендованное каталогом.

    `mandatory` — объявлено в `requires` и потому даёт отказ; иначе рекомендация,
    дающая напоминание. Одноимённое объявление вытесняет рекомендацию: один и тот
    же id не должен звучать дважды.
    """
    meta = next((s for s in pipeline if s["key"] == status), {})
    # `stage` — чьё это требование. Долг копится с разных этапов, и назвать
    # чужой (тот, откуда задачу двигают) значит соврать в тексте отказа
    stage = {"stage": status, "stage_label": meta.get("label", status)}
    declared = [dict(r, mandatory=True, **stage)
                for r in _req_list((cfg.get("requires") or {}).get(status))]
    seen = {_one_line(r.get("id")).upper() for r in declared}
    recommended = [dict(r, mandatory=False, **stage)
                   for r in _req_list(meta.get("recommends"))
                   if _one_line(r.get("id")).upper() not in seen]
    return declared + recommended


def _section_filled(lines: list[str], name: str) -> bool:
    """Есть ли в секции хоть что-то. Не то же, что «секция есть»: пустая секция
    «Изменение для пользователя» — это принятое решение «сказать нечего»."""
    bounds = _section_bounds(lines, name)
    if bounds is None:
        return False
    start, end = bounds
    return any(lines[i].strip() for i in range(start + 1, end))


def requirement_met(req: dict, task_path) -> bool:
    """Выполнено ли требование — по одному лишь файлу задачи.

    Граница жёсткая: ни git, ни сети, ни обхода файловой системы. Долг считается
    для каждой карточки доски, и предикат, лезущий наружу, превращает открытие
    доски в ожидание.

    Неизвестный предикат истинен (fail-open): непонятная декларация не должна
    останавливать работу — про неё скажет валидатор, а не отказ посреди дела.
    """
    path = Path(task_path)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return True

    check = _one_line(req.get("check"))
    name = _one_line(req.get("name"))
    if check == "checklist_done":
        return not _unchecked_boxes(lines)
    if check == "section_present":
        return bool(name) and _section_bounds(lines, name) is not None
    if check == "section_filled":
        return bool(name) and _section_filled(lines, name)
    if check == "field":
        return bool(name) and bool(_one_line(_read_meta(path).get(name)))
    if check == "confirm":
        done = [i.lower() for i in parse_req_ids(_read_meta(path).get(CONFIRMED_FIELD))]
        return _one_line(req.get("id")).lower() in done
    return True


def unmet(reqs: list[dict], task_path) -> list[dict]:
    """Требования, ложные сейчас и не списанные."""
    waived = [i.lower()
              for i in parse_req_ids(_read_meta(Path(task_path)).get(WAIVED_FIELD))]
    return [r for r in reqs
            if _one_line(r.get("id")).lower() not in waived
            and not requirement_met(r, task_path)]


def crossed(pipeline: list[dict], status: str) -> list[str]:
    """Этапы, которые задача уже прошла: строго левее текущего, без съездов.

    В съезде и в терминальном статусе пересечения нет вовсе. Без этих двух
    ограничителей механизм навесил бы долг на всю историю проекта задним числом.
    """
    keys = [s["key"] for s in pipeline]
    if status not in keys:
        return []
    if pipeline[keys.index(status)].get("offramp") or is_terminal(pipeline, status):
        return []
    return [s["key"] for s in pipeline[:keys.index(status)] if not s.get("offramp")]


def move_requirements(cfg: dict, pipeline: list[dict],
                      current: str, target: str) -> list[dict]:
    """Что должно быть закрыто, чтобы задача оказалась в целевом статусе.

    Правило одно: **переход вперёд разрешён, если в целевой позиции долг пуст**.
    Поэтому смотрим не на пересекаемый отрезок, а на **все пройденные этапы** —
    требования всего, что окажется левее цели.

    Так задумано с самого начала: агент, уходя с этапа, видит все незакрытые
    долги предыдущих и гасит их. Считать только отрезок `[исходный … цель)`
    значило бы, что этап, пройденный мимо гейта — рукой на доске, — не проверится
    уже никогда: один перенос мышью снимал бы проверку насовсем и молча, в отличие
    от `--waive`, который хотя бы оставляет строку.

    Не проверяется никогда: движение назад и в съезд. Возврат идёт по маршруту
    правильно, а у задачи в съезде долга нет вовсе.
    """
    keys = [s["key"] for s in pipeline]
    if current not in keys or target not in keys:
        return []
    if pipeline[keys.index(target)].get("offramp"):
        return []
    if keys.index(target) <= keys.index(current):
        return []
    passed = [s["key"] for s in pipeline[:keys.index(target)] if not s.get("offramp")]
    return [r for key in passed for r in stage_requirements(cfg, pipeline, key)]


def reset_confirmations(task_path, cfg: dict, pipeline: list[dict],
                        target: str) -> list[str]:
    """Снять подтверждения этапов, которые задача пройдёт заново. Вернуть снятые.

    Возврат назад — это признание, что этап не закрыт: задача пойдёт по нему
    ещё раз, и подтверждение прошлой итерации к новой не относится. Оставленное,
    оно гасит требование там, где вторая итерация принесла новое — другую
    проверку человеком, другие коммиты, другое знание.

    Подтверждения этапов **левее** цели остаются: их задача заново не проходит.

    `waived` не трогаем. Списание — решение о самом требовании («оно к этой
    задаче не относится»), а не о степени готовности; сбрасывая его, мы заставили
    бы списывать одно и то же на каждом круге, а рутина списаний — главный
    признак того, что механизм неверен.
    """
    path = Path(task_path)
    keys = [s["key"] for s in pipeline]
    if target not in keys:
        return []
    again = [s["key"] for s in pipeline[keys.index(target):] if not s.get("offramp")]
    ids = {_one_line(r.get("id")).lower()
           for key in again for r in stage_requirements(cfg, pipeline, key)}
    if not ids:
        return []

    confirmed = parse_req_ids(_read_meta(path).get(CONFIRMED_FIELD))
    dropped = [i for i in confirmed if i.lower() in ids]
    if dropped:
        kept = [i for i in confirmed if i.lower() not in ids]
        _set_fields(path, {CONFIRMED_FIELD: format_req_ids(kept)})
    return dropped


def _label_of(pipeline: list[dict], status: str) -> str:
    return next((s.get("label", status) for s in pipeline if s["key"] == status), status)


def _requirement_line(req: dict) -> str:
    """Строка о невыполненном требовании: чем оно гасится, сказано тут же."""
    rid = _one_line(req.get("id"))
    how = (f"отметить: --confirm {rid} \"как подтвердили\""
           if _one_line(req.get("check")) == "confirm"
           else "сделать и повторить")
    return f"{requirement_wording(req)} — {how}"


def gate_message(task_id: str, pipeline: list[dict], current: str,
                 target: str, blocked: list[dict]) -> str:
    """Текст отказа: что не выполнено, чем гасится и как обойти намеренно.

    Этап называется у каждого требования: долг копится с разных этапов, в том
    числе с пройденных мимо гейта — рукой на доске.
    """
    lines = [f"{task_id} → {target}: не закрыты требования пройденных этапов:"]
    lines += [f"  - {_requirement_line(r)} [{r.get('stage_label') or r.get('stage')}]"
              for r in blocked]
    lines.append("Пропустить намеренно: --waive <id> --reason \"почему\" "
                 "(останется строкой в заметках агента)")
    return "\n".join(lines)


def stage_reminders(pipeline: list[dict], current: str, pending: list[dict]) -> list[str]:
    """Напоминания о невыполненных рекомендациях — теми же словами, что и отказ.

    Печатается только невыполненное и только на движении вперёд: шум равен тому,
    что не сделано. Это то, чем раньше был обвес скиллов, — но работает у всех,
    включая тех, кто ничего не настраивал.
    """
    return [f"уходя из «{r.get('stage_label') or _label_of(pipeline, current)}»: "
            f"{_requirement_line(r)}"
            for r in pending if not r.get("mandatory")]


def stage_announcement(cfg: dict, pipeline: list[dict], status: str) -> str:
    """Что этап потребует на выходе — сказанное при входе в него.

    Требование, о котором узнают в момент отказа, выглядит придиркой; то же
    самое, сказанное на входе, — условием работы.
    """
    reqs = [r for r in stage_requirements(cfg, pipeline, status) if r.get("mandatory")]
    if not reqs:
        return ""
    return (f"этап «{_label_of(pipeline, status)}» требует на выходе: "
            + "; ".join(requirement_wording(r) for r in reqs))


def task_debt(tasks_dir, task_id: str, cfg: dict | None = None) -> dict:
    """Долг задачи в её нынешнем положении.

    Долг **вычисляется, а не хранится**: хранимое производное поле разъезжается
    молча и переживает честный возврат через ревью, где списывать нечего.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    cfg = load_config(tasks_dir) if cfg is None else cfg
    pipeline = pipeline_of(cfg)
    status = current_status(tasks_dir, task_id) or ""
    reqs = [r for key in crossed(pipeline, status)
            for r in stage_requirements(cfg, pipeline, key)]
    pending = unmet(reqs, path)
    return {"ok": True, "task": task_id, "status": status,
            "debt": [r for r in pending if r.get("mandatory")],
            "recommended": [r for r in pending if not r.get("mandatory")]}


def _mark_requirement(tasks_dir, task_id: str, field: str, req_id: str,
                      text: str, agent: str | None, what: str | None) -> dict:
    """Записать факт (подтверждение или списание) и оставить строку в заметках.

    Во frontmatter идёт только идентификатор — плоским списком, как `blocked_by`.
    Человеческая причина живёт в «Заметках агента» со временем из системы: она
    нужна тому, кто придёт к задаче позже, а не механизму.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    req_id = _one_line(req_id)
    if not req_id:
        return {"ok": False, "error": "Нужен идентификатор требования"}
    text = _one_line(text)
    if not text:
        return {"ok": False,
                "error": f"«{req_id}»: без объяснения не выполняется — причина "
                         f"остаётся в файле для того, кто придёт позже"}

    # Повтор ничего не меняет — и писать о нём нечего: событие было одно, а
    # вторая одинаковая строка засоряет хронологию, ради которой заметки и ведут
    ids = parse_req_ids(_read_meta(path).get(field))
    if req_id.lower() in [i.lower() for i in ids]:
        return {"ok": True, "task": task_id, "id": req_id, "already": True, "note": ""}

    # В строке — формулировка требования, а не его идентификатор: заметки
    # читает человек, и служебное имя ему ничего не говорит. Для подтверждения
    # префикса нет вовсе — формулировка сама им является («проверку подтвердил
    # человек»), и «подтверждено: проверку подтвердил человек» было тавтологией
    req = requirement_by_id(load_config(tasks_dir), pipeline_of(load_config(tasks_dir)),
                            req_id)
    named = requirement_text(req) if req else req_id
    line = f"{named} — {text}" if what is None else f"{what}: {named} — {text}"
    note = add_note(tasks_dir, task_id, line, agent=agent)
    if not note.get("ok"):
        return note

    ids.append(req_id)
    _set_fields(path, {field: format_req_ids(ids)})
    return {"ok": True, "task": task_id, "id": req_id, "already": False,
            "note": note["note"]}


def _unmark_requirement(tasks_dir, task_id: str, field: str, req_id: str,
                        agent: str | None, what: str) -> dict:
    """Снять факт (подтверждение или списание) и оставить строку в комментариях.

    Пустой `req_id` снимает все. Причина здесь не спрашивается: списание требовало
    её как обход, а снятие — возврат к честному состоянию, объяснять нечего.

    Снятие такое же решение, как сам факт, поэтому оно тоже громкое: молчаливое
    неотличимо от того, что списания и не было.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}

    current = parse_req_ids(_read_meta(path).get(field))
    req_id = _one_line(req_id)
    if req_id:
        removed = [i for i in current if i.lower() == req_id.lower()]
    else:
        removed = list(current)
    if not removed:
        return {"ok": True, "task": task_id, "removed": []}

    cfg = load_config(tasks_dir)
    named = requirement_names(cfg, pipeline_of(cfg), removed)
    note = add_note(tasks_dir, task_id, f"{what}: {'; '.join(named)}", agent=agent)
    if not note.get("ok"):
        return note

    kept = [i for i in current if i not in removed]
    _set_fields(path, {field: format_req_ids(kept)})
    return {"ok": True, "task": task_id, "removed": removed, "note": note["note"]}


def unwaive_requirement(tasks_dir, task_id: str, req_id: str = "",
                        agent: str | None = None) -> dict:
    """Снять списание: требование снова считается — и снова попадёт в долг."""
    return _unmark_requirement(tasks_dir, task_id, WAIVED_FIELD, req_id, agent,
                               "снято списание")


def unconfirm_requirement(tasks_dir, task_id: str, req_id: str = "",
                          agent: str | None = None) -> dict:
    """Снять подтверждение, не двигая задачу.

    Возврат назад по маршруту снимает подтверждения сам, но это грубый
    инструмент: он меняет статус. Подтвердили преждевременно — снимается точечно.
    """
    return _unmark_requirement(tasks_dir, task_id, CONFIRMED_FIELD, req_id, agent,
                               "снято подтверждение")


def confirm_requirement(tasks_dir, task_id: str, req_id: str, text: str,
                        agent: str | None = None) -> dict:
    """Отметить требование выполненным: подтверждение — тоже факт, не суждение."""
    return _mark_requirement(tasks_dir, task_id, CONFIRMED_FIELD, req_id, text,
                             agent, None)


def waive_requirement(tasks_dir, task_id: str, req_id: str, reason: str,
                      agent: str | None = None) -> dict:
    """Списать требование — единственный легальный обход, громкий и со следом."""
    return _mark_requirement(tasks_dir, task_id, WAIVED_FIELD, req_id, reason,
                             agent, "списано")


# --- КОНЕЦ БЛОКА: требования этапа ------------------------------------------


# --- Конец работы над задачей -----------------------------------------------
# Правило «финализируй скиллом» записано в правилах проекта и не срабатывает:
# напоминание, лежащее вдали от места действия, проигрывает контексту, который
# «вроде бы уже есть». Работает то, что сказано в момент операции, — поэтому
# говорит сам скрипт.


def work_done_status(cfg: dict, pipeline: list[dict]) -> str | None:
    """Статус, в котором кончается работа автора над задачей.

    Это не обязательно терминальный статус. С релизным хвостом задача уходит
    в пул готового, а конец маршрута наступает при выпуске — когда её закрывает
    релизный скилл, а автор давно забыл детали. Но у части проектов хвоста нет
    вовсе, и работа кончается именно в терминальном статусе.

    Имена не подставляем, правило выводится из конфига: задана цель подготовки
    текстов (`actions.release_draft`) — работа кончается перед ней; не задана —
    в последнем статусе маршрута.
    """
    keys = [s["key"] for s in pipeline if not s.get("offramp")]
    if not keys:
        return None
    draft = actions_of(cfg, pipeline).get("release_draft")
    if draft in keys:
        i = keys.index(draft)
        if i > 0:
            return keys[i - 1]
    return keys[-1]


def _unchecked_boxes(lines: list[str]) -> list[str]:
    """Незакрытые пункты чеклиста задачи."""
    bounds = _section_bounds(lines, CHECKLIST_SECTION)
    if not bounds:
        return []
    start, end = bounds
    out = []
    for i in range(start + 1, end):
        m = re.match(r"^\s*-\s*\[\s\]\s*(.+?)\s*$", lines[i])
        if m:
            out.append(m.group(1))
    return out


def _has_entries(lines: list[str], name: str) -> bool:
    """Есть ли в секции хоть одна строка списка."""
    bounds = _section_bounds(lines, name)
    if not bounds:
        return False
    start, end = bounds
    return any(lines[i].lstrip().startswith("- ") for i in range(start + 1, end))


def waiting_on(tasks_dir: Path, task_id: str) -> list[str]:
    """Кто помечен ждущим эту задачу — по их собственным `blocked_by`."""
    task_id = task_id.strip().upper()
    return [t["id"] for t in stalled(tasks_dir)["tasks"]
            if task_id in t["blocked_by"] and t["id"] != task_id]


def finish_reminders(tasks_dir: Path, task_id: str, task_path: Path,
                     cfg: dict) -> list[str]:
    """Что осталось сделать, раз работа над задачей кончилась.

    Проверяемое — проверяем: незакрытые чекбоксы, пустая история коммитов и
    чужие пометки видны в данных, поэтому о них говорится конкретно. Про волт
    можно только напомнить: «стоит ли сохранять знание» — суждение, а не
    проверка. Список ничего не запрещает: задача может честно не давать знаний.
    """
    out: list[str] = []
    if cfg.get("vault"):
        out.append("работа над задачей кончилась — что выяснилось про проект, "
                   "сохраните в волт (скилл write-vault): иначе следующая задача "
                   "на ту же тему вычитывает тот же код заново")

    try:
        lines = Path(task_path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []

    boxes = _unchecked_boxes(lines)
    if boxes:
        shown = ", ".join(f"«{b}»" for b in boxes[:3])
        tail = f" и ещё {len(boxes) - 3}" if len(boxes) > 3 else ""
        out.append(f"незакрытых пунктов чеклиста: {len(boxes)} — {shown}{tail}")

    if not _has_entries(lines, COMMITS_SECTION):
        out.append(f"секция «{COMMITS_SECTION}» пуста: по строке на коммит "
                   f"задачи — `<short-hash>` и сообщение")

    waiting = waiting_on(tasks_dir, task_id)
    if waiting:
        out.append(f"задачу ждут: {', '.join(waiting)} — свой простой она снимет "
                   f"сама, а их пометки снимают у них: --unblock {task_id.upper()}")
    return out


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
        # Чем проект выпускает версии. Пусто — своего механизма нет, скилл
        # выпуска доводит подготовку и останавливается, а не гадает
        "release_script": (cfg.get("release_script") or "").strip(),
    }
    if task_id:
        status = current_status(Path(tasks_dir), task_id)
        out["task"] = task_id
        out["current"] = status
        out.update(directions(pipeline, status or ""))
        # Долг по каждой цели — тем же вызовом, которым скилл и так спрашивает
        # маршрут: второй команде «а можно ли туда» взяться неоткуда
        path = find_task_file(Path(tasks_dir), task_id)
        blocked: dict[str, list[dict]] = {}
        if path is not None:
            for target in out.get("forward", []):
                rest = unmet(move_requirements(cfg, pipeline, status or "", target), path)
                stopping = [r for r in rest if r.get("mandatory")]
                if stopping:
                    blocked[target] = stopping
        out["blocked"] = blocked
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
    parser.add_argument("--type", dest="task_type", metavar="ТИП", default=None,
                        help="сменить тип задачи (см. --types)")
    parser.add_argument("--types", action="store_true",
                        help="каталог типов задач (JSON)")
    parser.add_argument("--stalled", action="store_true",
                        help="Что сейчас стоит и почему (JSON)")
    parser.add_argument("--debt", metavar="TASK-NNN", default=None,
                        help="Долг задачи: требования пройденных этапов (JSON)")
    parser.add_argument("--confirm", nargs=2, metavar=("ID", "ЧТО СКАЗАЛ ЧЕЛОВЕК"),
                        default=None,
                        help="Отметить требование выполненным (нужен --agent)")
    parser.add_argument("--waive", metavar="ID", default=None,
                        help="Списать требование: нужен --reason, след — в заметках")
    parser.add_argument("--unwaive", metavar="ID", nargs="?", const="", default=None,
                        help="Снять списание (без значения — все)")
    parser.add_argument("--unconfirm", metavar="ID", nargs="?", const="", default=None,
                        help="Снять подтверждение, не двигая задачу (без значения — все)")
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

    if args.types:
        print(json.dumps(types(), ensure_ascii=False, indent=2))
        return

    if args.stalled:
        print(json.dumps(stalled(tasks_dir), ensure_ascii=False, indent=2))
        return

    if args.debt:
        result = task_debt(tasks_dir, args.debt)
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.list or args.targets:
        print(json.dumps(describe(tasks_dir, args.targets), ensure_ascii=False, indent=2))
        return

    # Подтверждение и списание — факты о задаче, а не этап: идут и сами по себе,
    # и вместе со сменой статуса (погасить долг и двинуть задачу за один вызов)
    for flag, action, done in (
        (args.confirm, lambda: confirm_requirement(
            tasks_dir, args.task_id, args.confirm[0], args.confirm[1], agent=args.agent),
         "подтверждено"),
        (args.waive, lambda: waive_requirement(
            tasks_dir, args.task_id, args.waive, args.reason, agent=args.agent),
         "списано"),
        # Снятие идёт тем же путём: это тоже факт о задаче, а не этап
        (args.unwaive is not None, lambda: unwaive_requirement(
            tasks_dir, args.task_id, args.unwaive, agent=args.agent), "снято списание"),
        (args.unconfirm is not None, lambda: unconfirm_requirement(
            tasks_dir, args.task_id, args.unconfirm, agent=args.agent),
         "снято подтверждение"),
    ):
        if not flag:
            continue
        if not args.task_id:
            parser.error("нужен TASK-NNN для --confirm / --waive / --unwaive / --unconfirm")
        result = action()
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        if "removed" in result:
            if not result["removed"]:
                print(f"[i] {result['task']}: снимать нечего")
            else:
                print(f"[OK] {result['task']}: {done} — {', '.join(result['removed'])}")
                print(result["note"])
        elif result.get("already"):
            print(f"[i] {result['task']}: «{result['id']}» уже отмечено раньше — "
                  f"повтор ничего не изменил")
        else:
            print(f"[OK] {result['task']}: {done} «{result['id']}»")
            print(result["note"])

    if ((args.confirm or args.waive or args.unwaive is not None
         or args.unconfirm is not None) and not args.status and args.note is None):
        return

    # Тип — метка работы, а не этап: по маршруту он задачу не двигает, поэтому
    # флаг работает и сам по себе, и вместе со сменой статуса
    if args.task_type is not None:
        if not args.task_id:
            parser.error("нужен TASK-NNN для --type")
        result = set_type(tasks_dir, args.task_id, args.task_type)
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"[OK] {result['task']}: тип — {result['label']} ({result['type']})")
        if not args.status and args.note is None:
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
    if result.get("unconfirmed"):
        print(f"[i] снято подтверждение: {', '.join(result['unconfirmed'])} — "
              f"эти этапы задача проходит заново")
    if result.get("skipped"):
        # Не запрет, а видимость: пайплайн описывает ожидаемый маршрут
        print(f"[i] минуя {', '.join(result['skipped'])}")
    if result.get("announce"):
        print(f"[i] {result['announce']}")
    for warning in result.get("warnings", []):
        print(f"[!] {warning}")
    # Не гейт, а подсказка: переход уже выполнен, код возврата прежний
    for reminder in result.get("stage_reminders", []) + result.get("reminders", []):
        print(f"[!] {reminder}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CANCEL] Отменено пользователем", file=sys.stderr)
        sys.exit(1)
