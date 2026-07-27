#!/usr/bin/env python3
"""
Скрипт смены статуса задачи: единая точка правки frontmatter и board.md.

Статус задачи хранится в двух местах — поле `status` в файле задачи и раздел
доски. Правка их по отдельности рано или поздно приводит к рассинхрону
(в файле одно, на доске другое). Этот скрипт меняет оба места за один вызов.

Использование:
  py tasks/set_status.py TASK-004 development
  py tasks/set_status.py TASK-004 testing --agent "Claude Opus 5"
  py tasks/set_status.py TASK-004 completed --position end
  py tasks/set_status.py --list             # пайплайн статусов проекта (JSON)
  py tasks/set_status.py --targets TASK-004 # куда можно двинуть задачу (JSON)

Параметры:
  task_id                 Идентификатор задачи (TASK-NNN)
  status                  Новый статус из пайплайна проекта (см. --list)
  --agent TEXT            Кто меняет статус: попадёт в хвост строки доски.
                          Без него сохраняется прежний исполнитель, дата обновляется
  --position start|end    Куда вставить в целевом разделе (по умолчанию start)
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
    "ready_to_deploy": {"label": "К релизу", "section": "Ready to Deploy"},
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


def _retail(entry: str, agent: str | None, date: str) -> str:
    """Заменить хвост записи на «· агент · дата», сохранив прежнего агента."""
    m = re.match(r"^(\s*-\s*(?:~~)?\s*TASK-\d+\s*·\s*\[[^\]]*\]\([^)]*\))(.*)$", entry)
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
               agent: str | None = None, position: str = "start") -> dict:
    """
    Перевести задачу в новый статус: frontmatter + раздел board.md.

    Возвращает {"ok": True, "section": ...} либо {"ok": False, "error": ...}.
    Операция идемпотентна: повторный вызов с тем же статусом ничего не ломает.
    """
    tasks_dir = Path(tasks_dir)
    cfg = load_config(tasks_dir)

    pipeline = pipeline_of(cfg)
    known = [s["key"] for s in pipeline]
    if status not in known:
        return {"ok": False,
                "error": f"Статус {status} не входит в пайплайн проекта. "
                         f"Допустимо: {', '.join(known)}"}

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

    return {"ok": True, "task": task_id, "status": status, "section": section,
            "file": task_file.name, "from": prev, "skipped": skipped}


def current_status(tasks_dir: Path, task_id: str) -> str | None:
    """Статус задачи из frontmatter."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return None
    m = re.search(r"^status:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return m.group(1) if m else None


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
    parser.add_argument("--agent", default=None,
                        help="Кто меняет статус (попадёт в строку доски)")
    parser.add_argument("--position", choices=["start", "end"], default="start",
                        help="Позиция в целевом разделе (default: start)")
    parser.add_argument("--tasks-dir", default=None,
                        help="Папка задач (default: папка этого скрипта)")
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else Path(__file__).parent

    if args.list or args.targets:
        print(json.dumps(describe(tasks_dir, args.targets), ensure_ascii=False, indent=2))
        return

    if not args.task_id or not args.status:
        parser.error("нужны TASK-NNN и статус (либо --list / --targets)")

    result = set_status(tasks_dir, args.task_id, args.status,
                        agent=args.agent, position=args.position)

    if not result.get("ok"):
        print(f"[ERROR] {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] {result['task']} → {result['status']} (раздел «{result['section']}»)")
    if result.get("skipped"):
        # Не запрет, а видимость: пайплайн описывает ожидаемый маршрут
        print(f"[i] минуя {', '.join(result['skipped'])}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CANCEL] Отменено пользователем", file=sys.stderr)
        sys.exit(1)
