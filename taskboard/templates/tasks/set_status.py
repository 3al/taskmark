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
  py tasks/set_status.py TASK-004 --targets # куда можно двинуть задачу (JSON)
  py tasks/set_status.py TASK-004 --debt    # чем задача должна этапам (JSON)

Номер задачи в справочных режимах можно давать и значением флага
(`--targets TASK-004`) — обе формы работают.

Комментарий тоже пишет скрипт: время он берёт из системы (выставить его
задним числом «на глаз» нельзя), строку ставит в конец секции, а снесённый
заголовок «Комментарии» возвращает на его место в шаблоне:

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
  --note ТЕКСТ            Комментарий: время системное, строка — в конец секции
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
SCRIPT_CAPABILITIES = {"stall", "task_types", "task_sizes", "task_assignee",
                       "requires", "comments", "epics"}

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
    "queued": {"label": "Queue", "section": "Queue"},
    # reentry: сюда возвращают с проверки, но новую работу берут не отсюда
    "to_fix": {"label": "To Fix", "section": "To Fix", "reentry": True},
    "development": {"label": "Development", "section": "Development"},
    # Ту же рекомендацию несёт `testing`: в простом маршруте проверка автором
    # и есть тестирование. Там, где этапы разведены, совет остаётся здесь —
    # у первого по маршруту (см. `_thin_recommendations`)
    "local_testing": {"label": "Local Testing", "section": "Local Testing",
                      "recommends": [{"id": "verified", "check": "confirm",
                                      "ask": "проверку подтвердил человек"}]},
    "review": {"label": "Review", "section": "Review"},
    "to_testing": {"label": "To Testing", "section": "To Testing"},
    # recommends — что этап просит на выходе, пока проект не объявил этого
    # требованием: печатается напоминанием, ничего не запрещая. В рекомендации
    # попадает только то, что уже стреляло на практике (TASK-101), а не всё,
    # что легко проверить
    "testing": {"label": "Testing", "section": "Testing",
                "recommends": [{"id": "verified", "check": "confirm",
                                "ask": "проверку подтвердил человек"}]},
    "ready_for_release": {"label": "Ready for Release", "section": "Ready for Release"},
    "release_notes": {"label": "Release Notes", "section": "Release Notes",
                      # section_present, а не filled: пустая секция — принятое
                      # решение «пользователю сказать нечего», и требовать текст
                      # значило бы ломать это решение
                      # except_types: у обсуждения и ревью релизного хвоста нет
                      # вовсе — они закрываются коротким путём, и без исключения
                      # их закрытие упирается в оба требования сразу
                      "recommends": [{"id": "release_text", "check": "section_present",
                                      "name": "Изменение для пользователя",
                                      "ask": "тексты релиза написаны",
                                      "except_types": ["discussion", "review"]},
                                     {"id": "release_ok", "check": "confirm",
                                      "ask": "тексты релиза утверждены человеком",
                                      "except_types": ["discussion", "review"]}]},
    "to_release": {"label": "To Release", "section": "To Release"},
    "ready_to_deploy": {"label": "Ready to Deploy", "section": "Ready to Deploy"},
    "completed": {"label": "Completed", "section": "Completed"},
    "done": {"label": "Done", "section": "Done"},
    "cancelled": {"label": "Cancelled", "section": "Cancelled", "offramp": True},
}


# Типы задач — дубль backend/config.py (см. выше про автономность). В отличие
# от статусов это **не** библиотека дефолтов, а закрытый список: тип отвечает
# на вопрос «что это за работа», и ответ не зависит от жизненного цикла проекта.
# letter — буква кружка на превью доски, буквы не повторяются
# section — заголовок рубрики бэклога, куда create_task.py кладёт новую задачу
# commits: False — у работы этого типа коммитов не бывает, и пустая «История
# коммитов» у неё норма, а не долг (см. finish_reminders)
# skip_statuses — статусы, которые этому виду работы не нужны: у обсуждения и
# код-ревью нет релизного хвоста. Пропуск меняет только ожидаемый следующий
# шаг (`--targets`), достижимость статусов остаётся прежней
RELEASE_TAIL = ("ready_for_release", "release_notes", "to_release", "ready_to_deploy")

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
                   "letter": "О", "color": "amber", "commits": False,
                   "skip_statuses": RELEASE_TAIL},
    "design":     {"label": "Дизайн",           "section": "Дизайн",
                   "letter": "Д", "color": "fuchsia"},
    "review":     {"label": "Код-ревью",        "section": "Код-ревью",
                   "letter": "К", "color": "lime", "commits": False,
                   "skip_statuses": RELEASE_TAIL},
}


# Размер задачи — дубль backend/config.py. Оценка объёма работы, а не её вид:
# тип отвечает «что это за работа», размер — «браться ли за неё сейчас».
# Ключи прописные: в файле задачи это аббревиатура (`size: L`), строчная буква
# читалась бы как опечатка; разбор регистронезависим — файл правят руками.
# Порядок словаря — порядок возрастания объёма.
TASK_SIZES = {
    "S":  {"label": "S",  "hint": "мелкая правка, один заход"},
    "M":  {"label": "M",  "hint": "обычная задача на сессию"},
    "L":  {"label": "L",  "hint": "несколько сессий, лучше с планом"},
    "XL": {"label": "XL", "hint": "стоит разбить на задачи"},
}


def _titleize(key: str) -> str:
    return " ".join(part.capitalize() for part in key.split("_") if part)


def _thin_recommendations(statuses: list[dict]) -> None:
    """Оставить рекомендацию одного смысла только у самого раннего этапа.

    Каталог привязывает совет к **имени** статуса, а имя в разных маршрутах
    значит разное: там, где локальная проверка и стенд разведены по разным
    этапам, «проверку подтвердил человек» относится к первому из них — на
    втором человек подтвердил её этапом раньше.

    Роль принадлежит месту в маршруте, а не ключу, поэтому совет достаётся
    первому этапу, который его несёт. Это та же половина правила, что уже
    работает у требований: одно требование не должно звучать дважды.

    Повтором считается **та же рекомендация**, а не тот же предикат: `confirm`
    носят и подтверждение проверки, и утверждение текстов релиза — это разные
    события, и схлопывать их по типу проверки нельзя.
    """
    seen: set[tuple[str, str]] = set()
    for meta in statuses:
        recommends = meta.get("recommends")
        if not recommends:
            continue
        kept = []
        for req in recommends:
            same = (str(req.get("id", "")).strip().lower(),
                    str(req.get("check", "")).strip().lower())
            if same in seen:
                continue
            seen.add(same)
            kept.append(req)
        meta["recommends"] = kept


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

    _thin_recommendations(out)

    # Этапы, спрашивающие исполнителя: полный набор галочек из настроек.
    # Поставка не включает их нигде — зеркало backend/statuses.py
    chosen = cfg.get("assignee_statuses")
    if isinstance(chosen, (list, tuple, set)):
        allowed = {str(k) for k in chosen}
        for meta in out:
            meta["assignee"] = meta["key"] in allowed
    else:
        for meta in out:
            meta["assignee"] = bool(meta.get("assignee"))
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


def directions(pipeline: list[dict], status: str, task_type: str = "") -> dict:
    """Куда можно двинуть задачу: вперёд, назад и ожидаемый следующий шаг.

    Запретов нет — пайплайн описывает маршрут, а не забор: прыжок вперёд
    (простая задача, ночной хотфикс) законен. Ожидаемым считается ближайший
    следующий статус; съезды (cancelled) доступны всегда, но не ожидаемы.

    Тип задачи сужает **только** ожидаемый шаг: у обсуждения и код-ревью нет
    релизного хвоста, и вести их туда рекомендацией значит звать в работу,
    которой не будет. Достижимость статусов тип не трогает — там, где человек
    решит иначе, забором стоять нечему.
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
    ahead = [s["key"] for s in pipeline[idx + 1:] if not s.get("offramp")]
    skip = set(TASK_TYPES.get((task_type or "").strip().lower(), {})
               .get("skip_statuses") or ())
    # Пропуск сдвигает рекомендацию вперёд, но не отменяет её: пустой `next`
    # читается как конец маршрута (is_terminal), а вид работы его не задаёт
    wanted = [k for k in ahead if k not in skip] or ahead
    return {"forward": later + offramps, "backward": keys[:idx],
            "next": wanted[0] if wanted else None}


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


# Забор блока кода: ``` или ~~~ с отступом не больше трёх пробелов (дальше
# markdown считает строку частью списка или кодом-по-отступу)
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _in_fence(lines: list[str]) -> list[bool]:
    """Для каждой строки: лежит ли она внутри блока кода.

    Описания задач сплошь и рядом показывают куски `board.md` и других файлов
    задач, а там строки начинаются с `##`. Без маски такая строка обрывает
    секцию на середине — текст приезжает обрезанным, и заметить это трудно.
    Незакрытый забор маскирует всё до конца файла, как и трактует его markdown.
    """
    mask: list[bool] = []
    fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            fence = not fence
            mask.append(True)          # сама строка забора — тоже не заголовок
            continue
        mask.append(fence)
    return mask


def _section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """Границы раздела ## name: (индекс заголовка, индекс следующего ## или конец)."""
    start = None
    fenced = _in_fence(lines)
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
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


def _blocks(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Блоки раздела, каждый из которых держит свою заглушку.

    Подразделы ### — самостоятельные блоки: пустеют они поодиночке, и заглушка
    у каждого своя. Раздел без подразделов — один блок.
    """
    subs = [i for i in range(start + 1, min(end, len(lines)))
            if lines[i].startswith("### ")]
    if not subs:
        return [(start, end)]
    return [(at, subs[k + 1] if k + 1 < len(subs) else end)
            for k, at in enumerate(subs)]


def _add_placeholder_if_empty(lines: list[str], name: str) -> None:
    """Вернуть заглушку в опустевшие блоки раздела.

    Блоки обходим с конца: вставка сдвигает границы только тех, что ниже.
    """
    bounds = _section_bounds(lines, name)
    if not bounds:
        return
    start, end = bounds
    for b_start, b_end in reversed(_blocks(lines, start, end)):
        body = lines[b_start + 1:b_end]
        if any(re.match(r"^\s*-\s*(?:~~)?\s*TASK-\d+\s*·", ln) for ln in body):
            continue
        if any(ln.strip() == PLACEHOLDER for ln in body):
            continue
        lines.insert(b_start + 1, "")
        lines.insert(b_start + 2, PLACEHOLDER)


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
               force: bool = False, reason: str | None = None,
               via: str | None = None, manual: str | None = None) -> dict:
    """
    Перевести задачу в новый статус: frontmatter + раздел board.md.

    Возвращает {"ok": True, "section": ...} либо {"ok": False, "error": ...}.
    Операция идемпотентна: повторный вызов с тем же статусом ничего не ломает.

    force — взять в работу стоящую задачу: блокировка ровно от этого и
    защищает, поэтому без явного признака такой перевод не выполняется.

    reason — причина съезда с маршрута (отмены). Обязательна: из отмены не
    возвращаются, и «почему» должно остаться в файле.

    via / manual — источник перехода: `via` называет скилл, который его делает,
    `manual` — причину ручного хода. На запись влияет только `manual`: он меняет
    подпись строки перевода и добавляет причину в «Комментарии». Требует ли
    момент назвать источник — решает вызывающий (`transition_gate`): доска
    двигает задачи всегда, и правило это про агентский путь, а не про данные.
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
    stale_warning = ""
    if task_path is not None and status in (actions.get("start"),
                                            actions.get("return")):
        state = effective_stall(tasks_dir, _read_meta(task_path), pipeline)
        # Протухшая пометка старт не держит: блокер закрыт, ждать нечего.
        # Но и молчать нельзя — снимает её человек или агент, и сказать об
        # этом надо в тот момент, когда пометка попалась на глаза
        if state["stale"]:
            stale_warning = f"пометка простоя протухла: {state['stale_reason']}"
        if state["stalled"] and not force:
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
    via, manual = _one_line(via), _one_line(manual)
    owner = moment_skill(cfg, pipeline, from_status, status)

    pending = unmet(move_requirements(cfg, pipeline, from_status, status), task_file)
    blocked = [r for r in pending if r.get("mandatory")]
    if blocked:
        return {"ok": False,
                "error": gate_message(task_id, pipeline, from_status, status, blocked)}

    # Гейт входа: этап, спрашивающий исполнителя, спрашивает его сейчас, а не на
    # выходе — всё время, пока задача там стоит, должно быть видно, кто ею занят.
    # Имя знает только человек, поэтому агент обязан спросить: у доски свой путь
    # (перенос проходит, а незаполненный исполнитель становится долгом)
    if unmet(entry_requirements(pipeline, status), task_file):
        return {"ok": False,
                "error": f"Этап «{_label_of(pipeline, status)}» спрашивает "
                         f"исполнителя: кто займётся задачей? Назовите его — "
                         f"--assignee \"Имя\" (известные имена: --assignees)"}

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

    # Структуру файла снимаем до собственных записей: строка перевода вернёт
    # снесённый заголовок секции на место, и посчитанные после неё
    # предупреждения молчали бы о том, что заголовок вообще сносили
    structure = check_task_file(task_file)

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
    cleared = clear_stall(tasks_dir, task_id, agent) if is_terminal(pipeline, status) else None

    # След перевода — первым: дальше в ту же секцию может лечь строка о снятом
    # подтверждении, и она объясняет уже случившийся переход. Подпись строки —
    # источник, а не модель: модель уточняет его в скобках, но и без неё видно,
    # что переход прошёл через инструмент
    add_transition(tasks_dir, task_id, _label_of(pipeline, from_status),
                   _label_of(pipeline, status), agent, manual=bool(manual))

    # Причина ручного хода — отдельной строкой: подпись говорит, что переход
    # прошёл мимо скилла, а «почему» знает только тот, кто так решил
    if manual and owner:
        note_event(tasks_dir, task_id,
                   MANUAL_TEXT.format(skill=owner, reason=manual), agent)

    # Возврат назад: этапы правее цели задача пройдёт заново, и подтверждения
    # прошлой итерации к новой не относятся
    keys_all = [s["key"] for s in pipeline]
    unconfirmed: list[str] = []
    if (from_status in keys_all and status in keys_all
            and keys_all.index(status) < keys_all.index(from_status)):
        unconfirmed = reset_confirmations(task_file, cfg, pipeline, status)
        if unconfirmed:
            # Строкой в комментарии, а не только в консоль: снятие объясняет, почему
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

    # Задача уходит на проверку — знание записывают здесь, пока жив контекст
    # сессии, в которой его добыли. Точка вычисляется из конфига: это статус,
    # в котором идёт работа (`actions.start`), а событие — уход из него вперёд
    handoff = (handoff_reminders(cfg, task_file)
               if _is_handoff(cfg, pipeline, from_status, status) else [])

    # Конец работы — время прибрать хвосты в файле задачи: позже, при выпуске,
    # автор деталей уже не помнит
    reminders = (finish_reminders(tasks_dir, task_id, task_file, cfg)
                 if status == work_done_status(cfg, pipeline) else [])

    return {"ok": True, "task": task_id, "status": status, "section": section,
            "file": task_file.name, "from": prev, "skipped": skipped,
            "stall_cleared": bool(cleared and cleared.get("cleared")),
            "unconfirmed": unconfirmed,
            # Отдельным ключом от reminders: передача говорит о знании, конец
            # работы — о хвостах задачи. Склеенные, они теряют причину
            "handoff_reminders": handoff,
            "reminders": reminders,
            # Отдельным ключом от reminders: это разные механизмы — конец работы
            # напоминает о хвостах задачи, этап говорит о невыполненном на выходе
            "stage_reminders": stage_reminders(pipeline, from_status, pending, task_id),
            # Что потребует новый этап — сказанное на входе, а не в момент отказа
            "announce": stage_announcement(cfg, pipeline, status, task_file, task_id),
            # Кто ведёт этот момент маршрута: скилл вызывают вместо скрипта, а не
            # после него, поэтому имя звучит там, где агент точно окажется
            "moment_skill": moment_skill(cfg, pipeline, prev, status),
            # Незаполненное у задачи, которую только что взяли в работу: свой
            # канал от `reminders`, потому что момент противоположный — начало
            "entry_reminders": entry_reminders(task_file, task_id, cfg,
                                               pipeline, status),
            # Смена статуса — единственный момент, когда файл задачи заведомо
            # открывают: заодно показываем, что в нём разъехалось. Сюда же —
            # записи о требованиях, которых механизм не знает: они выглядят
            # закрытыми этапами, а не гасят ничего
            "warnings": structure + ([stale_warning] if stale_warning else []) + [
                f"в полях {CONFIRMED_FIELD}/{WAIVED_FIELD} не опознано: "
                f"{', '.join(unknown)} — таких требований в маршруте нет, и "
                f"этап они не закрывают"
                for unknown in [unknown_requirement_ids(cfg, pipeline, task_file)]
                if unknown]}


# --- Комментарии и структура файла задачи -----------------------------------
# Комментарий пишет скрипт, а не агент руками: время он берёт из системы,
# поэтому выставить его «на глаз» задним числом нельзя, а строка всегда встаёт
# в конец секции — так лог остаётся хронологией, а не набором строк в случайном
# порядке.

NOTES_SECTION = "Комментарии"
# Прежнее имя секции: она называлась по автору, а пишет туда давно не только
# агент (подтверждения с доски). Файлы задач переименовывает разовая миграция
# инструмента; здесь имя нужно, чтобы не заводить вторую секцию и не ругаться
# на файл, до которого миграция не дошла, — заголовок чинится на месте
LEGACY_NOTES_SECTION = "Заметки агента"
COMMITS_SECTION = "История коммитов"
CHECKLIST_SECTION = "Чеклист"

# Порядок секций файла задачи — эталон tasks/_TEMPLATE.md. «История доработок»
# появляется только после возврата с ревью, поэтому необязательна
RELEASE_SECTION = "Изменение для пользователя"

# Порядок секций файла задачи. «История доработок» появляется после возврата
# с ревью, «Изменение для пользователя» — при отборе в выпуск: обе создаются
# скиллами и в шаблоне новой задачи не нужны. «Чеклист» — тоже необязательная:
# это план под конкретную работу, который заводит агент, а не часть эталона
TASK_SECTIONS = ("Описание", RELEASE_SECTION, CHECKLIST_SECTION, "История доработок",
                 NOTES_SECTION, COMMITS_SECTION)

NOTE_RE = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\*\* · [^·]+ · .+$")

# Перевод статуса — та же строка комментария, но в позиции автора стоит
# источник перехода: «доска» у переноса мышью, «скрипт (Модель)» здесь. Источник
# — часть факта наравне со статусами: по нему видно, прошёл переход через
# инструмент или мимо него, а мимо инструмента теряются хвосты задачи.
# Формула зеркалит `backend/notes.py`: скрипт автономен и пишет сам, но
# расходиться форматам нельзя — историю читают разбором и рисуют одинаково.
TRANSITION_TEXT = "{was} → {now}"
SCRIPT_SOURCE = "скрипт"
# Тот же источник, но мимо скилла момента. Зеркало `backend/notes.py`
MANUAL_SOURCE = "скрипт вручную"

# Остальные действия жизненного цикла: простой, тип, название. Строку пишет тот
# же вызов, что правит поля, — иначе событие остаётся только во frontmatter, где
# видно нынешнее состояние, но не видно, когда и что с задачей сделали.
#
# Стрелки здесь нет намеренно: она — признак перевода статуса, и «было → стало»
# в другом событии читалось бы разбором как переход. По той же причине название
# берётся в кавычки: в нём стрелка может быть своя.
# Формулы зеркалят `backend/notes.py` — расходиться текстам нельзя
BLOCK_TEXT = "блокировка: ждёт {ids}"
UNBLOCK_TEXT = "блокировка снята: {ids}"
BLOCKS_TEXT = "блокирует {id}"
UNBLOCKS_TEXT = "больше не блокирует {id}"
PAUSE_TEXT = "пауза: {reason}"
RESUME_TEXT = "пауза снята"
TYPE_TEXT = "тип: {now} (было {was})"
SIZE_TEXT = "размер: {now} (было {was})"
ASSIGNEE_TEXT = "исполнитель: {now} (было {was})"
TITLE_TEXT = "название: «{now}» (было «{was}»)"


def _headings(lines: list[str]) -> list[str]:
    """Заголовки второго уровня файла в порядке следования."""
    out = []
    for line in lines:
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _rename_legacy_notes(lines: list[str]) -> list[str]:
    """Привести прежний заголовок секции комментариев к нынешнему имени.

    Секцию переименовала разовая миграция инструмента, но файл мог до неё не
    дойти (проект без запущенного taskboard, отдельная копия задачи). Чинить
    молча можно только заголовок, которого в файле ещё нет: если рядом уже
    стоит «Комментарии», два заголовка сливать нельзя — это правка содержимого,
    а не имени, и её должен увидеть человек.
    """
    heads = [h.lower() for h in _headings(lines)]
    if NOTES_SECTION.lower() in heads or LEGACY_NOTES_SECTION.lower() not in heads:
        return lines
    out = []
    for line in lines:
        m = re.match(r"^##\s+(.*)$", line)
        if m and m.group(1).strip().lower() == LEGACY_NOTES_SECTION.lower():
            line = f"## {NOTES_SECTION}"
        out.append(line)
    return out


def check_task_file(path: Path) -> list[str]:
    """Что разъехалось в файле задачи: секции, их порядок, хронология заметок.

    Не запрет, а видимость: файл правят руками, и нарушения копятся молча —
    снесённый заголовок секции, «История коммитов» посреди файла, комментарии
    вразнобой. Проверка идёт при каждой смене статуса, поэтому агент видит
    список сразу, а не через пять задач.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    # Прежнее имя секции — не нарушение структуры, а неприехавшая миграция:
    # ругаться на неё значит выдать агенту предупреждение за чужую работу
    lines = _rename_legacy_notes(lines)

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
                # Ругаемся только на попытку записать комментарий: свободный текст
                # старых задач (формат до перехода на строки списка) не наше дело
                malformed += 1
        if malformed:
            warnings.append(f"строк не в формате комментария: {malformed} "
                            f"(нужно `- **ГГГГ-ММ-ДД ЧЧ:ММ** · Модель · суть`)")
        for prev, cur in zip(stamps, stamps[1:]):
            if cur < prev:
                warnings.append(f"комментарии не в хронологии: {cur} после {prev} — "
                                f"новый комментарий пишется в конец секции (--note)")
                break

    return warnings


def _insert_notes_section(lines: list[str]) -> list[str]:
    """Вернуть снесённую секцию комментариев на её место — перед историей коммитов."""
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
    """Дописать комментарий в конец секции «Комментарии».

    agent обязателен: модель — единственное, чего скрипт про агента не знает,
    а копирование её из соседней строки как раз и делает историю ложной.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}

    text = _one_line(text)
    if not text:
        return {"ok": False, "error": "Пустой комментарий: нужен текст — --note \"суть\""}

    agent = _one_line(agent)
    if not agent:
        return {"ok": False,
                "error": "Не указана модель: добавьте --agent \"Модель\". Имя берётся "
                         "из текущей сессии, а не из соседней строки комментариев"}

    note = f"- **{datetime.now().strftime('%Y-%m-%d %H:%M')}** · {agent} · {text}"

    lines = _rename_legacy_notes(path.read_text(encoding="utf-8").splitlines())
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


def add_transition(tasks_dir: Path, task_id: str, was: str, now: str,
                   agent: str | None = None, manual: bool = False) -> dict | None:
    """Записать перевод статуса строкой в «Комментарии».

    Пишется **всегда**, а не только когда на этапе объявлены требования:
    история переводов обязана быть полной у любого проекта. Возврат назад даёт
    две строки — сам перевод и снятое подтверждение: это разные факты об одном
    событии, и склеенные они теряют и полноту, и единый формат.

    None — переводить было нечего: повторный вызов с тем же статусом
    идемпотентен, и записывать «Testing → Testing» значит засорять хронологию.
    """
    was, now = _one_line(was), _one_line(now)
    if not was or not now or was == now:
        return None
    agent = _one_line(agent)
    # Ручной ход подписан отдельно: иначе он неотличим от прохода через скилл,
    # и заметить его можно только вычитав историю глазами
    kind = MANUAL_SOURCE if manual else SCRIPT_SOURCE
    source = f"{kind} ({agent})" if agent else kind
    return add_note(tasks_dir, task_id,
                    TRANSITION_TEXT.format(was=was, now=now), agent=source)


def current_status(tasks_dir: Path, task_id: str) -> str | None:
    """Статус задачи из frontmatter."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return None
    m = re.search(r"^status:\s*(.+?)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return m.group(1) if m else None


# --- Эпики ------------------------------------------------------------------
# Зеркало backend/epics.py: имя эпика живёт только в реестре `epics.md`, состав
# собирается по полю `epic:` задач. Два источника одного среза — скрипт и окно
# эпика — обязаны показывать одно и то же.

EPICS_FILE = "epics.md"

# Запись эпика: «## E056-18500 — Инвентаризация» (имя может отсутствовать).
# Суффикс ключа не обязан быть числовым: кроме Jira-ключей заводят мнемонические
# («E001-STALL»). Заголовки разделов самого реестра («## Список эпиков») под
# шаблон ключа не подходят и эпиками не считаются
_EPIC_RE = re.compile(
    r"^##\s+(?P<key>[A-Za-z][\w.]*-[A-Za-z0-9][\w.-]*)\s*(?:—|-|–)?\s*(?P<name>.*)$")


def epics(tasks_dir: Path) -> list[dict]:
    """Эпики реестра: [{key, name}]. Нет файла — пустой список, не ошибка."""
    try:
        content = (Path(tasks_dir) / EPICS_FILE).read_text(encoding="utf-8-sig")
    except OSError:
        return []
    out = []
    for line in content.splitlines():
        m = _EPIC_RE.match(line.strip())
        if m:
            out.append({"key": m.group("key"), "name": m.group("name").strip()})
    return out


def epic_slice(tasks_dir: Path, key: str) -> dict:
    """Состав эпика: {epic, name, total, tasks: [{id, status, label, title, file}]}.

    Порядок — **пайплайна проекта**, а не номера задачи: состав читают как
    маршрут, по которому эпик едет. Съезды (отмена) идут за терминальным
    статусом, статус вне пайплайна — следом: молча прятать задачу, у которой
    статус выключили из настроек, нельзя, но места в маршруте у неё уже нет.

    Пустой ключ задач без эпика не собирает: `epic: ~` — это «эпика нет».
    """
    tasks_dir = Path(tasks_dir)
    key = (key or "").strip()
    pipeline = pipeline_of(load_config(tasks_dir))
    name = next((e["name"] for e in epics(tasks_dir) if e["key"] == key), "")
    out: dict = {"epic": key, "name": name, "total": 0, "tasks": []}
    if not key or key == EMPTY or not tasks_dir.is_dir():
        return out

    order = {s["key"]: i for i, s in enumerate(pipeline)}
    offramp = len(order) + 1        # съезды — за терминальным статусом
    unknown = len(order) + 2        # статус вне пайплайна — следом за ними
    meta_of = {s["key"]: s for s in pipeline}

    tasks = []
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        m = re.match(r"^(TASK-\d+)", path.name)
        if not m:
            continue
        meta = _read_meta(path)
        if str(meta.get("epic", "")).strip() != key:
            continue
        status = str(meta.get("status", "")).strip()
        info = meta_of.get(status)
        rank = (order[status] if info and not info.get("offramp")
                else offramp if info else unknown)
        tasks.append({"id": m.group(1), "status": status,
                      "label": info["label"] if info else status,
                      "title": str(meta.get("title", "")).strip(),
                      "file": path.name, "_rank": rank})

    tasks.sort(key=lambda t: (t["_rank"], t["id"]))
    for task in tasks:
        task.pop("_rank")
    out["total"] = len(tasks)
    out["tasks"] = tasks
    return out


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
        # стоящую задачу от свободной и возьмёт первую сверху. Считается он
        # с поправкой на закрытые блокеры — иначе протухшая пометка выбрасывает
        # свободную работу из очереди, и никто об этом не узнаёт
        path = find_task_file(tasks_dir, m.group("id"))
        state = effective_stall(tasks_dir, _read_meta(path) if path else {}, pipeline)
        tasks.append({"position": len(tasks) + 1, "id": m.group("id"),
                      "title": m.group("title"), "file": m.group("file"),
                      "meta": re.sub(r"^·\s*", "", m.group("tail").strip()).strip(),
                      "stalled": state["stalled"],
                      "blocked_by": state["blocked_by"], "paused": state["paused"],
                      "stale": state["stale"], "stale_reason": state["stale_reason"]})
    out["total"] = len(tasks)
    out["tasks"] = tasks if limit <= 0 else tasks[:limit]
    return out


def _section_text(lines: list[str], name: str) -> str | None:
    """Тело секции или None, если её нет.

    Пустая строка и None — **разные ответы**: пустая секция «Изменение для
    пользователя» означает принятое решение «пользователю сказать нечего»,
    а отсутствие секции — что до задачи ещё не добирались.
    """
    bounds = _section_bounds(lines, name)
    if bounds is None:
        return None
    start, end = bounds
    return "\n".join(lines[start + 1:end]).strip()


def changelog(tasks_dir: Path, status: str = "") -> dict:
    """Задачи статуса вместе с текстами для changelog.

    Собирать changelog по файлам вручную — механическая работа, на которой
    легко перепутать «секции нет» с «секция пустая» или зацепить соседнюю.
    Скрипт отдаёт разбор, скилл выпуска принимает решения.

    Статус не задан — берётся цель `release_lock` (утверждённый состав выпуска).
    Режим только читает: ничего не переносит и не правит.
    """
    tasks_dir = Path(tasks_dir)
    cfg = load_config(tasks_dir)
    pipeline = pipeline_of(cfg)
    if not status:
        status = actions_of(cfg, pipeline).get("release_lock") or ""
        if not status:
            return {"status": "", "section": None, "tasks": [],
                    "error": "в пайплайне проекта нет статуса утверждённого состава "
                             "(действие release_lock)"}

    known = {s["key"] for s in pipeline}
    if status not in known:
        return {"status": status, "section": None, "tasks": [],
                "error": f"неизвестный статус: {status} "
                         f"(есть: {', '.join(sorted(known))})"}

    section = section_for_status(cfg, status)
    out: dict = {"status": status, "section": section, "tasks": []}
    board = tasks_dir / cfg.get("board_file", "board.md")
    if not section or not board.is_file():
        return out

    lines = board.read_text(encoding="utf-8").splitlines()
    bounds = _section_bounds(lines, section)
    if not bounds:
        return out

    start, end = bounds
    for i in range(start + 1, end):
        m = _ENTRY_RE.match(lines[i])
        if not m:
            continue
        path = find_task_file(tasks_dir, m.group("id"))
        notes = None
        if path is not None:
            try:
                notes = _section_text(
                    path.read_text(encoding="utf-8-sig").splitlines(), RELEASE_SECTION)
            except OSError:
                notes = None
        out["tasks"].append({"id": m.group("id"), "title": m.group("title"),
                             "file": m.group("file"), "notes": notes})
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


def resolve_ids(tasks_dir: Path, ids, pipeline: list[dict]) -> list[dict]:
    """Развернуть номера блокеров в `[{id, title, status, found, resolved, cancelled}]`.

    Зеркало backend/stall.py. Номера мало: «TASK-013» ничего не говорит о том,
    держит ли эта задача до сих пор. Ненайденный блокер не выбрасываем —
    помечаем `found: False`.
    """
    tasks_dir = Path(tasks_dir)
    out: list[dict] = []
    for task_id in parse_ids(ids):
        path = find_task_file(tasks_dir, task_id)
        meta = _read_meta(path) if path else {}
        status = meta.get("status", "")
        entry = next((s for s in pipeline if s["key"] == status), {})
        resolved = bool(path) and is_terminal(pipeline, status)
        out.append({"id": task_id, "title": meta.get("title", ""),
                    "status": status, "label": entry.get("label", status),
                    "found": path is not None,
                    "resolved": resolved,
                    "cancelled": resolved and bool(entry.get("offramp"))})
    return out


def is_stale(blockers: list[dict], paused: str = "") -> bool:
    """Держать нечему: пометка есть, а все блокеры дошли до конца маршрута.

    Зеркало backend/stall.py. Пауза этого не касается — она ждёт
    обстоятельства, а не задачу. Ненайденный блокер держит: битая ссылка —
    предупреждение валидатора, а не разрешение работать.
    """
    return bool(blockers) and all(b["resolved"] for b in blockers) and not paused


def stale_reason(blockers: list[dict], unblock_hint: str = "--unblock") -> str:
    """Чем протухла пометка и что с ней делать — одной строкой для агента.

    Зеркало backend/stall.py. Выполненный блокер и отменённый разводятся
    текстом: первый снимает вопрос, второй его задаёт — предмет ждущей задачи
    мог отпасть вместе с ним.
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


def effective_stall(tasks_dir: Path, meta: dict, pipeline: list[dict]) -> dict:
    """Стоит ли задача **на самом деле** — с поправкой на закрытые блокеры.

    Пометка `blocked_by` живёт в файле, пока её не снимут, а блокер тем
    временем уезжает в конец маршрута. Дальше все, кто читает простой, обязаны
    считать одинаково: очередь, срез и гейт старта.
    """
    state = stall_of(meta)
    blockers = resolve_ids(tasks_dir, state["blocked_by"], pipeline)
    stale = is_stale(blockers, state["paused"])
    state["blocked_by_tasks"] = blockers
    state["stale"] = stale
    state["stale_reason"] = stale_reason(blockers) if stale else ""
    state["stalled"] = bool(state["paused"]) or bool(blockers) and not stale
    return state


def _edit_list_field(path: Path, field: str, task_id: str, add: bool) -> None:
    ids = parse_ids(_read_meta(path).get(field))
    if add and task_id not in ids:
        ids.append(task_id)
    elif not add and task_id in ids:
        ids.remove(task_id)
    else:
        return
    _set_fields(path, {field: format_ids(ids)})


def note_event(tasks_dir: Path, task_id: str, text: str, agent: str | None = None) -> None:
    """Записать действие жизненного цикла строкой в «Комментарии».

    Подпись — источник, а не модель: модель уточняет его в скобках. Файл задачи,
    прочитанный сверху вниз, обязан давать полную картину произошедшего, а
    frontmatter показывает только нынешнее состояние.
    """
    source = f"{SCRIPT_SOURCE} ({agent})" if _one_line(agent) else SCRIPT_SOURCE
    add_note(tasks_dir, task_id, text, agent=source)


def set_blocked_by(tasks_dir: Path, task_id: str, ids, agent: str | None = None) -> dict:
    """Задать блокеров задачи, синхронно правя `blocks` у них.

    Два конца зависимости — та же ловушка, что статус в файле и на доске:
    правка одного руками разъезжается молча. Поэтому правим оба сразу.

    В хронологию идёт **разница**, а не поданный список: повторный вызов чинит
    односторонние ссылки, и событием это не является. Пишется обоим концам —
    файл блокера иначе молчит о том, что на него встали.
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

    added = [i for i in ids if i not in old]
    removed = [i for i in old if i not in ids]
    if added:
        note_event(tasks_dir, task_id, BLOCK_TEXT.format(ids=", ".join(added)), agent)
    if removed:
        note_event(tasks_dir, task_id, UNBLOCK_TEXT.format(ids=", ".join(removed)), agent)
    for blocker, text in ([(b, BLOCKS_TEXT) for b in added]
                          + [(b, UNBLOCKS_TEXT) for b in removed]):
        if find_task_file(tasks_dir, blocker) is not None:
            note_event(tasks_dir, blocker, text.format(id=task_id), agent)

    return {"ok": True, "task": task_id, "blocked_by": ids, "missing": missing}


def _stall_allowed(tasks_dir: Path, path: Path) -> dict:
    """Разрешено ли ставить простой на задачу в её текущем статусе."""
    cfg = load_config(Path(tasks_dir))
    return can_stall(pipeline_of(cfg), _read_meta(path).get("status", ""))


def block(tasks_dir: Path, task_id: str, blocker: str, agent: str | None = None) -> dict:
    """Добавить блокера к задаче."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    verdict = _stall_allowed(tasks_dir, path)
    if not verdict["ok"]:
        return {"ok": False, "error": verdict["reason"]}
    current = parse_ids(_read_meta(path).get("blocked_by"))
    return set_blocked_by(tasks_dir, task_id, current + parse_ids(blocker), agent)


def unblock(tasks_dir: Path, task_id: str, blocker: str = "",
            agent: str | None = None) -> dict:
    """Снять блокера (без аргумента — всех)."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    drop = parse_ids(blocker)
    current = parse_ids(_read_meta(path).get("blocked_by"))
    keep = [i for i in current if i not in drop] if drop else []
    return set_blocked_by(tasks_dir, task_id, keep, agent)


def set_paused(tasks_dir: Path, task_id: str, reason: str,
               agent: str | None = None) -> dict:
    """Поставить задачу на паузу с причиной (пустая причина — снять).

    Пауза — метка, а не статус: `status` и раздел доски остаются как были.

    Строка в хронологию идёт только при смене состояния: «снял паузу», когда её
    не было, — не событие, а повторный вызов скрипта.
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
    was = _one_line(_read_meta(path).get("paused", "")).strip(EMPTY)
    _set_fields(path, {"paused": reason or EMPTY})
    if reason and reason != was:
        note_event(tasks_dir, task_id, PAUSE_TEXT.format(reason=reason), agent)
    elif not reason and was:
        note_event(tasks_dir, task_id, RESUME_TEXT, agent)
    return {"ok": True, "task": task_id.strip().upper(), "paused": reason}


def set_type(tasks_dir: Path, task_id: str, value: str,
             agent: str | None = None) -> dict:
    """Сменить тип задачи. Тип — метка работы, статус и доску он не трогает.

    Правится скриптом, а не руками: значение закрытое, и опечатка в нём
    означает молча пропавшую метку на доске.

    Тип задаёт исключения в требованиях этапа, поэтому его смена идёт в
    хронологию: ею объясняется, почему переход прошёл или не прошёл.
    """
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    value = (value or "").strip().lower()
    if value not in TASK_TYPES:
        return {"ok": False,
                "error": f"Неизвестный тип задачи: {value or '(пусто)'} "
                         f"(допустимо: {', '.join(TASK_TYPES)})"}
    was = str(_read_meta(path).get("type", "") or "").strip().lower()
    _set_fields(path, {"type": value})
    if value != was:
        note_event(tasks_dir, task_id,
                   TYPE_TEXT.format(now=value, was=was or "не указан"), agent)
    return {"ok": True, "task": task_id.strip().upper(), "type": value,
            "label": TASK_TYPES[value]["label"]}


def types() -> dict:
    """Каталог типов задач: список спрашивают у скрипта, а не помнят."""
    return {"types": [{"key": key, **meta} for key, meta in TASK_TYPES.items()]}


def set_size(tasks_dir: Path, task_id: str, value: str,
             agent: str | None = None) -> dict:
    """Проставить или снять размер задачи. Как и тип, по маршруту не двигает.

    Пустое значение **снимает оценку** (`size: ~`): оценка наугад хуже её
    отсутствия, и передумать должно быть чем.

    Смена идёт в хронологию: «взял как M, оказалось XL» объясняет ход работы
    следующей сессии. Повтор того же значения событием не считается.
    """
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    value = (value or "").strip().upper()
    if value and value not in TASK_SIZES:
        return {"ok": False,
                "error": f"Неизвестный размер задачи: {value} "
                         f"(допустимо: {', '.join(TASK_SIZES)})"}
    was = str(_read_meta(path).get("size", "") or "").strip().upper()
    was = was if was in TASK_SIZES else ""
    _set_fields(path, {"size": value or "~"})
    if value != was:
        note_event(tasks_dir, task_id,
                   SIZE_TEXT.format(now=value or "не указан",
                                    was=was or "не указан"), agent)
    return {"ok": True, "task": task_id.strip().upper(), "size": value,
            "label": TASK_SIZES[value]["label"] if value else "не указан"}


def sizes() -> dict:
    """Каталог размеров задачи: список спрашивают у скрипта, а не помнят."""
    return {"sizes": [{"key": key, **meta} for key, meta in TASK_SIZES.items()]}

def _global_config_path() -> Path:
    """Файл глобального конфига инструмента — общий для всех проектов машины."""
    return Path.home() / ".taskboard" / "config.json"


def assignees() -> dict:
    """Известные исполнители: список общий для всех проектов машины.

    Людей поставка не знает — имена приносит работа, поэтому список открытый и
    пополняется тем, что назначили. Он нужен, чтобы спрашивать «кто занимается
    задачей» с вариантами, а не с чистого листа.
    """
    stored = _read_json(_global_config_path())
    return {"assignees": [str(n) for n in (stored.get("assignees") or [])]}


def _remember_assignee(name: str) -> None:
    """Запомнить имя в глобальном конфиге, если оно новое.

    Читаем-правим-пишем один ключ: остальное в файле принадлежит инструменту, и
    переписывать его целиком отсюда нельзя.
    """
    name = " ".join((name or "").split())
    known = assignees()["assignees"]
    if not name or name in known:
        return
    path = _global_config_path()
    stored = _read_json(path)
    stored["assignees"] = [*known, name]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass  # список подсказок — удобство, а не условие работы


def set_assignee(tasks_dir: Path, task_id: str, value: str,
                 agent: str | None = None, status: str = "") -> dict:
    """Назначить исполнителя задачи или снять назначение (`assignee: ~`).

    Список имён открытый: проверять нечего, кроме пустоты, — зато назначают не
    на каждом этапе. Спрашивает ли этап исполнителя, объявляет пайплайн
    (флаг `assignee`); `status` — этап, на котором задача окажется, если имя
    ставят вместе с переводом.

    **Снятие разрешено везде**: задачу могли перенести с этапа проверки, и
    застрявшее имя надо чем-то убирать.
    """
    tasks_dir = Path(tasks_dir)
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Файл задачи не найден: {task_id}"}
    value = " ".join((value or "").split())
    cfg = load_config(tasks_dir)
    pipeline = pipeline_of(cfg)
    status = status or _one_line(_read_meta(path).get("status"))
    if value and not entry_requirements(pipeline, status):
        return {"ok": False,
                "error": f"Этап «{_label_of(pipeline, status)}» исполнителя не "
                         f"спрашивает — включить можно в настройках инструмента, "
                         f"галочкой «исполнитель» у статуса"}
    was = _one_line(_read_meta(path).get(ASSIGNEE_FIELD))
    was = "" if was == EMPTY else was
    _set_fields(path, {ASSIGNEE_FIELD: value or EMPTY})
    if value != was:
        note_event(tasks_dir, task_id,
                   ASSIGNEE_TEXT.format(now=value or "не назначен",
                                        was=was or "не назначен"), agent)
        _remember_assignee(value)
    return {"ok": True, "task": task_id.strip().upper(), "assignee": value}


def clear_stall(tasks_dir: Path, task_id: str, agent: str | None = None) -> dict:
    """Снять с задачи и блокировки, и паузу (при переезде в конец маршрута)."""
    path = find_task_file(Path(tasks_dir), task_id)
    if path is None:
        return {"ok": False, "cleared": False}
    state = stall_of(_read_meta(path))
    if not state["stalled"]:
        return {"ok": True, "cleared": False}
    if state["blocked_by"]:
        set_blocked_by(tasks_dir, task_id, [], agent)
    if state["paused"]:
        set_paused(tasks_dir, task_id, "", agent)
    return {"ok": True, "cleared": True,
            "blocked_by": state["blocked_by"], "paused": state["paused"]}


def stalled(tasks_dir: Path) -> dict:
    """Срез «что сейчас стоит и почему» — для агента, как `--queue`.

    Причина простоя лежит во frontmatter, поэтому доску читать незачем.

    Задача с протухшей пометкой из среза не исчезает — пометка-то стоит, и
    снять её кому-то придётся, — но помечена `stale` с объяснением: иначе
    читающий идёт смотреть блокер и обнаруживает, что тот давно закрыт.
    """
    tasks_dir = Path(tasks_dir)
    pipeline = pipeline_of(load_config(tasks_dir))
    tasks = []
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        m = re.match(r"^(TASK-\d+).*\.md$", path.name)
        if not m:
            continue
        try:
            meta = _read_meta(path)
        except OSError:
            continue
        if not stall_of(meta)["stalled"]:
            continue
        state = effective_stall(tasks_dir, meta, pipeline)
        tasks.append({"id": m.group(1).upper(), "title": meta.get("title", ""),
                      "status": meta.get("status", ""), "file": path.name,
                      "blocked_by": state["blocked_by"],
                      "blocked_by_tasks": state["blocked_by_tasks"],
                      "paused": state["paused"],
                      "stale": state["stale"], "stale_reason": state["stale_reason"]})
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
    поэтому в комментариях она стоит сама по себе, без служебного префикса.
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


def _requirement_kind(req: dict) -> tuple[str, str]:
    """Отпечаток требования: что проверяется и у чего.

    Сопоставлять декларации по `id` нельзя: идентификатор человек придумывает
    сам, и вытеснение по имени заставляло его **угадывать** идентификатор
    рекомендации из каталога — иначе своё требование звучало вторым ритуалом
    (TASK-135). Смысл задают предикат и его параметр; регистр имени секции не
    важен, как и при её поиске в файле.
    """
    return (_one_line(req.get("check")).lower(), _one_line(req.get("name")).lower())


# Исполнитель — требование **входа**, а не выхода: остальные проверки говорят,
# что должно быть сделано, чтобы уйти с этапа, а имя нужно ровно тогда, когда
# задача на этап попала, — иначе всё время, пока она там стоит, неизвестно, кто
# ею занят. Объявить его в `requires` значило бы спросить имя на выходе, то есть
# когда оно уже не нужно.
#
# Декларации в конфиге у него нет: этап объявляет это флагом `assignee` в
# пайплайне (галочка «исполнитель» в настройках инструмента)
ASSIGNEE_FIELD = "assignee"
# `ask` — утверждение о выполненном («исполнитель назначен»): так требования
# читаются в долге и в отказе. `todo` — то же самое указанием, для мест, где
# человеку говорят, что сделать: «уедет с долгом — исполнитель назначен» звучит
# как сообщение о том, что он уже есть
ASSIGNEE_REQUIREMENT = {"id": "assignee", "check": "field", "name": ASSIGNEE_FIELD,
                        "ask": "исполнитель назначен",
                        "todo": "назначить исполнителя"}


def entry_requirements(pipeline: list[dict], status: str) -> list[dict]:
    """Что этап требует на входе. Сейчас это только исполнитель.

    Съезд с маршрута не спрашивает никого: отменённой задачей не занимаются.
    """
    meta = next((s for s in pipeline if s["key"] == status), {})
    if not meta.get("assignee") or meta.get("offramp"):
        return []
    return [dict(ASSIGNEE_REQUIREMENT, mandatory=True, entry=True,
                 stage=status, stage_label=meta.get("label", status))]


def stage_requirements(cfg: dict, pipeline: list[dict], status: str) -> list[dict]:
    """Что этап просит на выходе: объявленное проектом и рекомендованное каталогом.

    `mandatory` — объявлено в `requires` и потому даёт отказ; иначе рекомендация,
    дающая напоминание. Объявленное вытесняет рекомендацию **того же смысла**:
    одно требование не должно звучать дважды, отказом и напоминанием.
    """
    meta = next((s for s in pipeline if s["key"] == status), {})
    # `stage` — чьё это требование. Долг копится с разных этапов, и назвать
    # чужой (тот, откуда задачу двигают) значит соврать в тексте отказа
    stage = {"stage": status, "stage_label": meta.get("label", status)}
    declared = [dict(r, mandatory=True, **stage)
                for r in _req_list((cfg.get("requires") or {}).get(status))]
    seen = {_requirement_kind(r) for r in declared}
    seen |= {_one_line(r.get("id")).lower() for r in declared}
    recommended = [dict(r, mandatory=False, **stage)
                   for r in _req_list(meta.get("recommends"))
                   if _requirement_kind(r) not in seen
                   and _one_line(r.get("id")).lower() not in seen]
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


def unknown_requirement_ids(cfg: dict, pipeline: list[dict], task_path) -> list[str]:
    """Идентификаторы в `confirmed` / `waived`, которых нет ни у одного требования.

    Движок сравнивает записи с `id` из конфига и **всё незнакомое игнорирует**:
    требование остаётся невыполненным, хотя поле выглядит заполненным. Возврат
    назад такую запись не снимает (снимается только распознанное), и увидеть её
    иначе как чтением конфига нельзя — значит сказать обязан скрипт.

    Известными считаются идентификаторы **всех** этапов маршрута, включая
    рекомендации каталога: задача могла закрыть требование другого этапа раньше,
    и честная запись об этом не мусор.

    Удалять ничего нельзя: запись может быть опечаткой в конфиге, а не в задаче,
    и тогда стёрся бы факт.
    """
    meta = _read_meta(Path(task_path))
    written = parse_req_ids(meta.get(CONFIRMED_FIELD)) + parse_req_ids(meta.get(WAIVED_FIELD))
    if not written:
        return []
    known = {_one_line(r.get("id")).lower()
             for s in pipeline
             for r in stage_requirements(cfg, pipeline, s["key"])}
    seen: list[str] = []
    for value in written:
        if value.lower() not in known and value not in seen:
            seen.append(value)
    return seen


def requirement_applies(req: dict, task_path) -> bool:
    """Относится ли требование к этой задаче — по её типу.

    Часть требований бессмысленна по типу работы: «История коммитов» у
    задачи-обсуждения пуста не по недосмотру, а потому что коммитов там не
    будет никогда. Требование, невыполнимое в принципе, учит списывать и всё
    остальное.

    Хранится **исключение** (`except_types`), а не белый список: новый тип в
    поставке белый список молча перестал бы покрывать — тихая потеря, — а
    исключение молча включит, и это заметно. Нет ключа — требование ко всем
    типам; нет типа у задачи (заведена до появления поля) — тоже.
    """
    skip = req.get("except_types") or req.get("except_type")
    if isinstance(skip, str):
        skip = [skip]
    skip = [_one_line(t).lower() for t in (skip or [])]
    if not skip:
        return True
    return _one_line(_read_meta(Path(task_path)).get("type")).lower() not in skip


def unmet(reqs: list[dict], task_path) -> list[dict]:
    """Требования, ложные сейчас, не списанные и относящиеся к этой задаче."""
    waived = [i.lower()
              for i in parse_req_ids(_read_meta(Path(task_path)).get(WAIVED_FIELD))]
    return [r for r in reqs
            if _one_line(r.get("id")).lower() not in waived
            and requirement_applies(r, task_path)
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


def _requirement_line(req: dict, task_id: str = "") -> str:
    """Строка о невыполненном требовании: чем оно гасится, сказано тут же.

    Идентификатор задачи входит в подсказку: её копируют как есть, а без
    `TASK-NNN` буквальный запуск отвечает «нужен TASK-NNN для --confirm». Имя
    интерпретатора читающий только что набрал сам, номер задачи за него может
    подставить только скрипт.
    """
    how = _confirm_hint(req, task_id) or "сделать и повторить"
    return f"{requirement_wording(req)} — {how}"


def _confirm_hint(req: dict, task_id: str = "") -> str:
    """Чем гасится требование-подтверждение — пустая строка для остальных.

    Отметку делает агент за человека, поэтому подсказка нужна там же, где
    названо требование: слова «проверил, всё ок» приходят в чат, и другого
    сигнала, что их надо записать, у агента нет. Остальные требования закрывает
    сама работа, и советовать по ним нечего.
    """
    if _one_line(req.get("check")) != "confirm":
        return ""
    task = f"{task_id} " if task_id else ""
    return f"отметить: {task}--confirm {_one_line(req.get('id'))} \"как подтвердили\""


def gate_message(task_id: str, pipeline: list[dict], current: str,
                 target: str, blocked: list[dict]) -> str:
    """Текст отказа: что не выполнено, чем гасится и как обойти намеренно.

    Этап называется у каждого требования: долг копится с разных этапов, в том
    числе с пройденных мимо гейта — рукой на доске.
    """
    lines = [f"{task_id} → {target}: не закрыты требования пройденных этапов:"]
    lines += [f"  - {_requirement_line(r, task_id)} "
              f"[{r.get('stage_label') or r.get('stage')}]"
              for r in blocked]
    lines.append(f"Пропустить намеренно: {task_id} --waive <id> --reason \"почему\" "
                 "(останется строкой в комментариях)")
    return "\n".join(lines)


def stage_reminders(pipeline: list[dict], current: str, pending: list[dict],
                    task_id: str = "") -> list[str]:
    """Напоминания о невыполненных рекомендациях — теми же словами, что и отказ.

    Печатается только невыполненное и только на движении вперёд: шум равен тому,
    что не сделано. Это то, чем раньше был обвес скиллов, — но работает у всех,
    включая тех, кто ничего не настраивал.

    Называется **этап требования**, а не покидаемый: долг приходит и с этапов,
    пройденных раньше, и «уходя из „X“» в таком случае врёт про момент —
    человек начинает искать причину не там.
    """
    return [f"этап «{r.get('stage_label') or _label_of(pipeline, current)}» остался "
            f"незакрытым: {_requirement_line(r, task_id)}"
            for r in pending if not r.get("mandatory")]


def stage_announcement(cfg: dict, pipeline: list[dict], status: str,
                       task_path=None, task_id: str = "") -> str:
    """Что этап потребует на выходе — сказанное при входе в него.

    Требование, о котором узнают в момент отказа, выглядит придиркой; то же
    самое, сказанное на входе, — условием работы.

    Задача известна — требования, к её типу не относящиеся, не называются:
    обещать то, чего не спросят, хуже, чем молчать.

    У требований-подтверждений названо и то, чем они гасятся: человек говорит
    «проверил» посреди этапа, а не при уходе с него, — на выходе подсказка
    опоздает.

    Рекомендации каталога называются наравне с объявленными, но своими словами:
    иначе проект, ничего не объявлявший, узнавал бы имя требования только из
    напоминания при уходе — то есть уже после момента, — и агенту оставалось бы
    угадывать его. Угаданное мимо каталога ничего не гасит и оседает мусором.
    """
    reqs = stage_requirements(cfg, pipeline, status)
    if task_path is not None:
        reqs = [r for r in reqs if requirement_applies(r, task_path)]
    parts = []
    label = _label_of(pipeline, status)
    for mandatory, what in ((True, "требует на выходе: "), (False, "на выходе ждёт: ")):
        said = [_announced(r, task_id) for r in reqs
                if bool(r.get("mandatory")) is mandatory]
        if said:
            # Этап называется один раз: во второй части он тот же самый
            lead = what if parts else f"этап «{label}» {what}"
            parts.append(lead + "; ".join(said))
    return ", ".join(parts)


def _announced(req: dict, task_id: str) -> str:
    """Требование в анонсе: формулировка и — у подтверждений — чем гасится."""
    hint = _confirm_hint(req, task_id)
    return f"{requirement_wording(req)} — {hint}" if hint else requirement_wording(req)


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
    # Плюс то, что спрашивает нынешний этап на входе: незаполненный исполнитель —
    # долг сразу, пока задача на этапе стоит, а не при попытке уйти с него
    reqs += entry_requirements(pipeline, status)
    pending = unmet(reqs, path)
    return {"ok": True, "task": task_id, "status": status,
            "debt": [r for r in pending if r.get("mandatory")],
            "recommended": [r for r in pending if not r.get("mandatory")]}


def markable_requirements(cfg: dict, pipeline: list[dict], check: str = "") -> list[dict]:
    """Требования маршрута, которые вообще можно отметить, — без повторов.

    Идентификатор человек придумывает сам, и опечатка в нём неотличима от
    честной записи: перечислить known — единственный способ показать разницу.
    `check` сужает список до одного предиката (подтверждают только `confirm`).
    """
    seen: set[str] = set()
    out: list[dict] = []
    for status in [s["key"] for s in pipeline]:
        for req in stage_requirements(cfg, pipeline, status):
            req_id = _one_line(req.get("id")).lower()
            if not req_id or req_id in seen:
                continue
            if check and _one_line(req.get("check")) != check:
                continue
            seen.add(req_id)
            out.append(req)
    return out


def _markable_hint(cfg: dict, pipeline: list[dict], check: str, what: str) -> str:
    """Хвост отказа: что вместо этого можно отметить."""
    known = markable_requirements(cfg, pipeline, check)
    if not known:
        return f"{what} в этом проекте нечего: таких требований в маршруте нет"
    return f"{what} можно: {'; '.join(requirement_wording(r) for r in known)}"


def _mark_requirement(tasks_dir, task_id: str, field: str, req_id: str,
                      text: str, agent: str | None, what: str | None,
                      check: str = "") -> dict:
    """Записать факт (подтверждение или списание) и оставить строку в комментариях.

    Во frontmatter идёт только идентификатор — плоским списком, как `blocked_by`.
    Человеческая причина живёт в «Комментариях» со временем из системы: она
    нужна тому, кто придёт к задаче позже, а не механизму.

    **Отмечается только объявленное требование.** Запись фиксирует факт о
    конкретном требовании маршрута; идентификатор, которого там нет, — опечатка,
    и принятая молча она даёт строку в хронологии о том, чего никто не делал.
    Зеркало `backend/requirements.py`.
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

    cfg = load_config(tasks_dir)
    pipeline = pipeline_of(cfg)
    req = requirement_by_id(cfg, pipeline, req_id)
    doing = "Списать" if what else "Подтвердить"
    if req is None:
        return {"ok": False,
                "error": f"«{req_id}»: такого требования в маршруте нет. "
                         f"{_markable_hint(cfg, pipeline, check, doing)}"}
    # Предикат `confirm` означает «человек сказал»; остальные закрываются
    # работой, и словом их не закрыть — в отличие от списания, которое обходит
    # любое требование, потому и требует причины
    if check and _one_line(req.get("check")) != check:
        return {"ok": False,
                "error": f"«{req_id}»: закрывается работой, а не решением — "
                         f"подтверждать нечем. "
                         f"{_markable_hint(cfg, pipeline, check, doing)}"}

    # Повтор ничего не меняет — и писать о нём нечего: событие было одно, а
    # вторая одинаковая строка засоряет хронологию, ради которой комментарии и ведут
    ids = parse_req_ids(_read_meta(path).get(field))
    if req_id.lower() in [i.lower() for i in ids]:
        return {"ok": True, "task": task_id, "id": req_id, "already": True, "note": ""}

    # В строке — формулировка требования, а не его идентификатор: комментарии
    # читает человек, и служебное имя ему ничего не говорит. Для подтверждения
    # префикса нет вовсе — формулировка сама им является («проверку подтвердил
    # человек»), и «подтверждено: проверку подтвердил человек» было тавтологией
    named = requirement_text(req)
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
                             agent, None, check="confirm")


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


# Каждый момент маршрута ведёт свой скилл, и обход скилла прямым вызовом скрипта
# найден трижды подряд (TASK-189): терялись то оценка, то знание и хвосты, то
# сверка коммитов и разблокированные. Три одинаковые заплаты означали нехватку
# общего правила, а не три особых случая, поэтому карта здесь одна.
#
# Слова-триггеры в описании скилла этому не противостоят: описание срабатывает,
# когда человек формулирует запрос его словами, а агент с заученным маршрутом
# команд идёт коротким путём. Зато скрипт он зовёт неизбежно — там и встречаем.
# Отказ на переходе, у которого есть свой скилл. Обойти можно (`--manual`), но
# громко и с причиной — тем же приёмом, что отмена требует `--reason`, а
# требование этапа списывается через `--waive`. Смысл не в защите: подделать
# `--via` ничего не стоит, — а в том, что молчаливый пропуск становится выбором
GATE_TEXT = ("этот переход делает скилл {skill}: позовите его — или, если "
             "двигаете вручную, назовите причину: {task} {target} "
             "--manual \"почему\"")
MANUAL_TEXT = "перевод вручную, мимо скилла {skill}: {reason}"
START_SKILL = "start-task"
HANDOFF_SKILL = "handoff-task"
FIX_SKILL = "fix-task"
FINALIZE_SKILL = "finalize-task"
RELEASE_SKILL = "release"


def moment_skill(cfg: dict, pipeline: list[dict], from_status: str | None,
                 target: str) -> str:
    """Какой скилл отвечает за этот момент маршрута — строкой для агента.

    Карта выводится из `actions`, а не из имён статусов: у каждого проекта свой
    маршрут, и зашитое имя неверно для половины пользователей.

    Строка одна и шагов скилла не пересказывает: длинную инструкцию пролистают,
    и обесценятся заодно соседние строки. Она звучит и у того, кто скилл вызвал,
    — проверить это скрипт не может, а молчать «на всякий случай» значит молчать
    ровно там, где нужнее.

    Момент, у которого своего скилла нет (возврат мимо рабочего статуса),
    остаётся без имени: назвать не тот скилл хуже, чем не назвать никакого.
    """
    keys = [s["key"] for s in pipeline]
    if target not in keys:
        return ""
    work = actions_of(cfg, pipeline).get("start")
    here = keys.index(target)
    there = keys.index(from_status) if from_status in keys else None
    offramp = bool(pipeline[here].get("offramp"))

    if target == work and not offramp:
        # Возврат в работу — это доработка после чужих замечаний, а не старт
        return (FIX_SKILL if there is not None and there > here else START_SKILL)
    if _is_handoff(cfg, pipeline, from_status, target):
        return HANDOFF_SKILL
    if _in_release_tail(cfg, pipeline, target):
        # За концом работы задачу ведёт уже выпуск, и финализировать там нечего
        return RELEASE_SKILL
    if offramp or there is None or here > there:
        # Съезд ведёт та же финализация: причину отмены спрашивает она
        return FINALIZE_SKILL
    return ""


# Оценка объёма ставится не при заведении задачи (строить её тогда не на чем) и
# не в конце (там она уже никому не нужна), а при взятии в работу — когда контекст
# изучен. Момент этот агент проходит вызовом скрипта, поэтому пустое поле называем
# здесь: проверяемое проверяем, как незакрытые чекбоксы в конце работы.
SIZE_REMINDER_TEXT = ("размер не оценён — изучите контекст и проставьте: "
                      "{task} --size <ключ>, каталог с пояснениями — --sizes")

# Уход из работы агент узнаёт по перевод статуса — и именно этот признак поглощает
# соседний процесс: коммит, push и MR успевают пройти раньше, чем кто-то вспомнит
# про статус. Поэтому выход описывается **событиями** и говорится на входе: на
# выходе подсказка опоздает к тому, ради чего она есть.
# Про MR тут не говорится намеренно: это словарь опциональной возможности, а
# строку читают все проекты. Коммит и push универсальны и в том же сценарии
# случаются раньше — момент они ловят не хуже
EXIT_RULE_TEXT = ("выход из работы ведёт скилл handoff-task, и выходом считается "
                  "коммит, push или слова человеку «готово, проверьте» — даже "
                  "если статус в этот момент трогать не собирались")


def entry_reminders(task_path: Path, task_id: str, cfg: dict,
                    pipeline: list[dict], target: str) -> list[str]:
    """Что сказать задаче, которую только что взяли в работу.

    Момент — вход в `actions.start`, любым путём: и первый старт, и возврат на
    доработку. Канал один, а строк в нём две природы: проверяемое по данным
    (незаполненная оценка) и правило самого этапа (чем работа кончается).

    Правило выхода говорится **здесь**, а не при уходе: коммит, push и MR
    случаются раньше первого вызова скрипта, и сказанное на выходе опаздывает
    ровно к тому событию, ради которого написано.

    Не гейт: оценку ставят, изучив контекст, — то есть уже после перевода. Отказ
    здесь требовал бы оценки раньше, чем её на чём строить.
    """
    if target != actions_of(cfg, pipeline).get("start"):
        return []
    out: list[str] = []
    try:
        size = str(_read_meta(Path(task_path)).get("size", "") or "").strip().upper()
    except OSError:
        size = ""
    if size not in TASK_SIZES:
        out.append(SIZE_REMINDER_TEXT.format(task=task_id))
    out.append(EXIT_RULE_TEXT)
    return out


def transition_gate(tasks_dir: Path, task_id: str, target: str,
                    via: str | None, manual: str | None) -> str:
    """Текст отказа, если переход требует назвать источник. Пусто — можно идти.

    **Стоит на агентском пути, а не в данных.** У требований этапа есть
    эквивалент на стороне доски (долг карточки), а здесь его нет и быть не
    может: человек двигает карточки мышью и объяснять это не обязан. Правило
    про то, чем агент зовёт инструмент, — значит его место у интерфейса.

    Гейт молчит там, где скилла нет: момент без своего скилла (возврат мимо
    рабочего статуса) и проект, где окружение не развёрнуто.
    """
    if _one_line(via) or _one_line(manual):
        return ""
    tasks_dir = Path(tasks_dir)
    cfg = load_config(tasks_dir)
    pipeline = pipeline_of(cfg)
    from_status = current_status(tasks_dir, task_id) or ""
    owner = moment_skill(cfg, pipeline, from_status, target)
    if not owner or not skill_deployed(tasks_dir, owner):
        return ""
    return GATE_TEXT.format(skill=owner, task=task_id.upper(), target=target)


def skill_deployed(tasks_dir: Path, name: str) -> bool:
    """Лежит ли скилл рядом с проектом.

    Структура задач разворачивается **отдельно** от агентского окружения: папку
    `tasks/` со скриптом копируют в другие репозитории, окружение разворачивают
    кнопкой позже, а скилл можно удалить руками. Требовать вызова того, чего в
    проекте нет, — значит упираться на каждом переходе.

    Раскладок две: одна копия в `.claude/skills`, а в `.opencode/skills` скиллы
    попадают только у проекта без Claude Code.
    """
    root = Path(tasks_dir).parent
    return any((root / harness / "skills" / name / "SKILL.md").is_file()
               for harness in (".claude", ".opencode"))


def _in_release_tail(cfg: dict, pipeline: list[dict], target: str) -> bool:
    """Начался ли релизный хвост — то, что ведёт уже выпуск, а не автор задачи.

    Граница та же, по которой считается конец работы (`work_done_status`): цель
    подготовки текстов и всё за ней. Хвоста в проекте нет — нет и границы.
    """
    keys = [s["key"] for s in pipeline if not s.get("offramp")]
    draft = actions_of(cfg, pipeline).get("release_draft")
    if draft not in keys or target not in keys:
        return False
    return keys.index(target) >= keys.index(draft)


def _is_handoff(cfg: dict, pipeline: list[dict], from_status: str | None,
                target: str) -> bool:
    """Отдаёт ли этот переход задачу на проверку.

    Событие — **уход вперёд из статуса, где идёт работа** (`actions.start`, тот
    же ключ, по которому скиллы находят рабочий статус). Возврат назад передачей
    не является: работа не кончилась, а началась заново. Съезд с маршрута — тоже
    не передача, это отмена.
    """
    work = actions_of(cfg, pipeline).get("start")
    if not work or from_status != work:
        return False
    keys = [s["key"] for s in pipeline]
    if work not in keys or target not in keys:
        return False
    if pipeline[keys.index(target)].get("offramp"):
        return False
    return keys.index(target) > keys.index(work)


def _unchecked_boxes(lines: list[str]) -> list[str]:
    """Незакрытые пункты плана задачи.

    Секция необязательна — её заводит агент под конкретную работу. Нет её —
    возвращаем пусто: спрашивать план с того, кто его не вёл, незачем.
    """
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


def _type_has_commits(lines: list[str]) -> bool:
    """Бывают ли коммиты у работы этого типа — по каталогу, а не по списку здесь.

    Тип неизвестен или не назван (задача заведена до появления поля) — считаем,
    что бывают: молчать о пустой секции по недостатку данных нельзя.
    """
    for line in lines[1:]:
        if line.startswith("---"):
            break
        if line.startswith("type:"):
            meta = TASK_TYPES.get(line[len("type:"):].strip())
            return bool(meta.get("commits", True)) if meta else True
    return True


def waiting_on(tasks_dir: Path, task_id: str) -> list[str]:
    """Кто помечен ждущим эту задачу — по их собственным `blocked_by`."""
    task_id = task_id.strip().upper()
    return [t["id"] for t in stalled(tasks_dir)["tasks"]
            if task_id in t["blocked_by"] and t["id"] != task_id]


def _unclosed_plan_text(boxes: list[str]) -> str:
    """Незакрытые пункты плана — первые три поимённо, остальные счётом."""
    shown = ", ".join(f"«{b}»" for b in boxes[:3])
    tail = f" и ещё {len(boxes) - 3}" if len(boxes) > 3 else ""
    return f"незакрытых пунктов плана: {len(boxes)} — {shown}{tail}"


def handoff_reminders(cfg: dict, task_path: Path | None = None) -> list[str]:
    """Что сделать, отдавая задачу на проверку: пока жив контекст работы.

    Отдельный канал от `finish_reminders`, и по единственному критерию —
    **что можно потерять, отложив**. Знание теряется: до конца работы задача
    доезжает и без агента (рукой на доске), и тогда записывать его некому — а
    ухода из рабочего статуса не минует ни одна задача (TASK-137). Остальные
    хвосты наоборот раньше невыполнимы: план работы закрывается вместе с самой
    работой, а коммиты в части процессов идут уже после проверки человеком.

    Про волт можно только напомнить: «стоит ли сохранять знание» — суждение, а
    не проверка. Ничего не запрещает: задача может честно не давать знаний.

    **Сама передача от волта не зависит.** Строка про знание — добавка к моменту,
    а не его причина: проект без волта передаёт задачу так же, и молчать там
    значит не комментировать момент вовсе.
    """
    # Причина не называет, что будет дальше с задачей: строка звучит и при
    # закрытии коротким путём, где дальше не будет ничего. Верное всегда — то,
    # ради чего скилл и нужен
    # Скилл передачи назван строкой момента, которая печатается рядом; здесь —
    # то, чего в ней нет: что именно осталось сделать, раз работа кончилась
    out = ["работа над задачей кончилась — закройте план работы и скажите "
           "человеку, что именно проверять"]
    try:
        lines = Path(task_path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []
    boxes = _unchecked_boxes(lines)
    if boxes:
        out.append(_unclosed_plan_text(boxes))
    if cfg.get("vault"):
        out.append("что выяснилось про проект, сохраните в волт (внутри "
                   "handoff-task — скилл write-vault), пока помните: в следующей "
                   "задаче на ту же тему это не придётся вычитывать заново")
    return out


def finish_reminders(tasks_dir: Path, task_id: str, task_path: Path,
                     cfg: dict) -> list[str]:
    """Что осталось прибрать, раз задача дошла до конца работы.

    Проверяемое — проверяем: незакрытые чекбоксы, пустая история коммитов и
    чужие пометки видны в данных, поэтому о них говорится конкретно. Про волт
    здесь не напоминают — его срок раньше, см. `handoff_reminders`.
    """
    out: list[str] = []

    try:
        lines = Path(task_path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []

    # Про незакрытый план здесь не говорим: он закрывается вместе с работой, и
    # момент у него один — уход из рабочего статуса (`handoff_reminders`)
    if _type_has_commits(lines) and not _has_entries(lines, COMMITS_SECTION):
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
        # `assignee` — спрашивает ли этап исполнителя. Условие «если этап его
        # спрашивает» скиллы проверяют здесь: собранный без признака пайплайн
        # делал его непроверяемым, и агент спрашивал имя там, где оно не нужно
        "pipeline": [{"key": s["key"], "label": s["label"], "section": s["section"],
                      "offramp": bool(s.get("offramp")),
                      "assignee": bool(s.get("assignee"))} for s in pipeline],
        "actions": actions_of(cfg, pipeline),
        # Чем проект выпускает версии. Пусто — своего механизма нет, скилл
        # выпуска доводит подготовку и останавливается, а не гадает
        "release_script": (cfg.get("release_script") or "").strip(),
    }
    if task_id:
        status = current_status(Path(tasks_dir), task_id)
        path = find_task_file(Path(tasks_dir), task_id)
        task_type = str((_read_meta(path).get("type") if path else "") or "")
        out["task"] = task_id
        out["current"] = status
        out["type"] = task_type
        out.update(directions(pipeline, status or "", task_type))
        # Долг по каждой цели — тем же вызовом, которым скилл и так спрашивает
        # маршрут: второй команде «а можно ли туда» взяться неоткуда
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
    # nargs="?" — номер задачи можно дать и позиционно (`TASK-004 --targets`):
    # так выглядят все остальные операции над задачей, и рука тянется туда же
    parser.add_argument("--targets", metavar="TASK-NNN", nargs="?", const="",
                        default=None,
                        help="Законные цели перехода для задачи (JSON)")
    parser.add_argument("--queue", action="store_true",
                        help="Живая очередь доски прямо сейчас (JSON)")
    parser.add_argument("--changelog", metavar="СТАТУС", nargs="?", const="", default=None,
                        help="задачи статуса и их тексты для changelog "
                             "(без значения — утверждённый состав выпуска)")
    parser.add_argument("--limit", type=int, default=5,
                        help="Сколько задач очереди показать, 0 — все (default: 5)")
    parser.add_argument("--via", metavar="СКИЛЛ", default=None,
                        help="Чем делается переход: имя скилла, который ведёт этот момент")
    parser.add_argument("--manual", metavar="ПРИЧИНА", default=None,
                        help="Перевод вручную, мимо скилла: причина остаётся в задаче")
    parser.add_argument("--epic", metavar="КЛЮЧ", default=None,
                        help="Состав эпика: задачи в порядке маршрута (JSON)")
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
    parser.add_argument("--size", dest="task_size", metavar="РАЗМЕР", default=None,
                        help="оценить объём задачи: S | M | L | XL (пусто — снять)")
    parser.add_argument("--sizes", action="store_true",
                        help="каталог размеров задачи (JSON)")
    parser.add_argument("--assignee", metavar="ИМЯ", default=None,
                        help="кто занимается задачей на этапе проверки (пусто — снять)")
    parser.add_argument("--assignees", action="store_true",
                        help="известные исполнители: общий на машине список (JSON)")
    parser.add_argument("--stalled", action="store_true",
                        help="Что сейчас стоит и почему (JSON)")
    parser.add_argument("--debt", metavar="TASK-NNN", nargs="?", const="",
                        default=None,
                        help="Долг задачи: требования пройденных этапов (JSON)")
    parser.add_argument("--confirm", nargs=2, metavar=("ID", "ЧТО СКАЗАЛ ЧЕЛОВЕК"),
                        default=None,
                        help="Отметить требование выполненным (нужен --agent)")
    parser.add_argument("--waive", metavar="ID", default=None,
                        help="Списать требование: нужен --reason, след — в комментариях")
    parser.add_argument("--unwaive", metavar="ID", nargs="?", const="", default=None,
                        help="Снять списание (без значения — все)")
    parser.add_argument("--unconfirm", metavar="ID", nargs="?", const="", default=None,
                        help="Снять подтверждение, не двигая задачу (без значения — все)")
    parser.add_argument("--note", metavar="ТЕКСТ", default=None,
                        help="Дописать комментарий (время — системное, строка — в конец)")
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

    if args.changelog is not None:
        report = changelog(tasks_dir, args.changelog)
        # Отказ говорится словами и ненулевым кодом: пустой список задач —
        # нормальный ответ, и путать его с «такого статуса нет» нельзя
        if report.get("error"):
            print(f"[ERROR] {report['error']}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.epic is not None:
        report = epic_slice(tasks_dir, args.epic)
        known = [e["key"] for e in epics(tasks_dir)]
        # Пустой вывод на опечатку в ключе неотличим от эпика без задач, поэтому
        # неизвестный ключ — ошибка с перечнем верных. Ключа нет в реестре, но
        # задачи с ним есть — показываем: реестр мог отстать от файлов
        if args.epic not in known and not report["total"]:
            print(f"[ERROR] Эпик не найден: {args.epic or '(пусто)'}"
                  + (f" (в реестре: {', '.join(known)})" if known
                     else f" (реестр {EPICS_FILE} пуст)"), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.types:
        print(json.dumps(types(), ensure_ascii=False, indent=2))
        return

    if args.sizes:
        print(json.dumps(sizes(), ensure_ascii=False, indent=2))
        return

    if args.assignees:
        print(json.dumps(assignees(), ensure_ascii=False, indent=2))
        return

    if args.stalled:
        print(json.dumps(stalled(tasks_dir), ensure_ascii=False, indent=2))
        return

    if args.debt is not None:
        task_id = args.debt or args.task_id
        if not task_id:
            parser.error("нужен TASK-NNN для --debt")
        result = task_debt(tasks_dir, task_id)
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.list or args.targets is not None:
        task_id = (args.targets or args.task_id) if args.targets is not None else None
        if args.targets is not None and not task_id:
            parser.error("нужен TASK-NNN для --targets")
        print(json.dumps(describe(tasks_dir, task_id), ensure_ascii=False, indent=2))
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
        result = set_type(tasks_dir, args.task_id, args.task_type, args.agent)
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"[OK] {result['task']}: тип — {result['label']} ({result['type']})")
        if not args.status and args.note is None:
            return

    # Размер — оценка объёма, не этап: тот же порядок, что у типа
    if args.task_size is not None:
        if not args.task_id:
            parser.error("нужен TASK-NNN для --size")
        result = set_size(tasks_dir, args.task_id, args.task_size, args.agent)
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"[OK] {result['task']}: размер — {result['label']}")
        if not args.status and args.note is None:
            return

    # Исполнитель — свойство этапа, а не этап: флаг работает и сам по себе, и
    # вместе с переводом. Именно вместе он и нужен чаще всего: этап, спрашивающий
    # исполнителя, без имени не пропустит, поэтому назначаем **до** перевода и
    # проверяем имя по целевому статусу, а не по нынешнему
    if args.assignee is not None:
        if not args.task_id:
            parser.error("нужен TASK-NNN для --assignee")
        result = set_assignee(tasks_dir, args.task_id, args.assignee, args.agent,
                              status=args.status or "")
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"[OK] {result['task']}: исполнитель — "
              f"{result['assignee'] or 'не назначен'}")
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
            apply(block(tasks_dir, args.task_id, args.block, args.agent),
                  lambda r: f"[OK] {r['task']} ждёт {format_ids(r['blocked_by'])}")
        if args.unblock is not None:
            apply(unblock(tasks_dir, args.task_id, args.unblock, args.agent),
                  lambda r: f"[OK] {r['task']}: блокировки — {format_ids(r['blocked_by'])}")
        if args.pause:
            apply(set_paused(tasks_dir, args.task_id, args.pause, args.agent),
                  lambda r: f"[OK] {r['task']} на паузе: {r['paused']}")
        if args.resume:
            apply(set_paused(tasks_dir, args.task_id, "", args.agent),
                  lambda r: f"[OK] {r['task']}: пауза снята")

        if not args.status and args.note is None:
            return

    # Комментарий сам по себе статус не трогает. Но вместе со сменой статуса он
    # описывает **её** («переведена в …»), поэтому пишется только после того, как
    # переход состоялся: при отказе гейта строка оставалась в файле, и история
    # задачи начинала врать о событии, которого не было
    if args.note is not None and not args.status:
        if not args.task_id:
            parser.error("нужен TASK-NNN для --note")
        result = add_note(tasks_dir, args.task_id, args.note, agent=args.agent)
        if not result.get("ok"):
            print(f"[ERROR] {result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"[OK] {result['task']}: комментарий записан в «{NOTES_SECTION}»")
        print(result["note"])
        for warning in result.get("warnings", []):
            print(f"[!] {warning}")
        return

    if not args.task_id or not args.status:
        parser.error("нужны TASK-NNN и статус "
                     "(либо --list / --targets / --queue / --stalled / --block / --pause)")

    refusal = transition_gate(tasks_dir, args.task_id, args.status,
                              args.via, args.manual)
    if refusal:
        print(f"[ERROR] {refusal}", file=sys.stderr)
        sys.exit(1)

    result = set_status(tasks_dir, args.task_id, args.status,
                        agent=args.agent, position=args.position, force=args.force,
                        reason=args.reason, via=args.via, manual=args.manual)

    if not result.get("ok"):
        print(f"[ERROR] {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] {result['task']} → {result['status']} (раздел «{result['section']}»)")

    # Переход состоялся — теперь можно записать комментарий о нём
    if args.note is not None:
        note = add_note(tasks_dir, args.task_id, args.note, agent=args.agent)
        if note.get("ok"):
            print(f"[OK] {note['task']}: комментарий записан в «{NOTES_SECTION}»")
            print(note["note"])
        else:
            print(f"[ERROR] {note.get('error')}", file=sys.stderr)

    if result.get("stall_cleared"):
        print("[i] простой снят: задача дошла до конца маршрута")
    if result.get("unconfirmed"):
        print(f"[i] снято подтверждение: {', '.join(result['unconfirmed'])} — "
              f"эти этапы задача проходит заново")
    if result.get("skipped"):
        # Не запрет, а видимость: пайплайн описывает ожидаемый маршрут
        print(f"[i] минуя {', '.join(result['skipped'])}")
    for reminder in result.get("entry_reminders", []):
        print(f"[i] {reminder}")
    if result.get("announce"):
        print(f"[i] {result['announce']}")
    for warning in result.get("warnings", []):
        print(f"[!] {warning}")
    # Не гейт, а подсказка: переход уже выполнен, код возврата прежний.
    # Каналы раздельные по механизму, печатаются подряд одним видом строки
    for reminder in (result.get("stage_reminders", [])
                     + result.get("handoff_reminders", [])
                     + result.get("reminders", [])):
        print(f"[!] {reminder}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CANCEL] Отменено пользователем", file=sys.stderr)
        sys.exit(1)
