"""Напоминание о подходящем сроке задачи.

**Повод и канал разведены.** `due_events()` отвечает на один вопрос — у каких
задач срок попадает в окно порога — и про телеграм не знает вовсе: тот же повод
понадобится службе уведомлений внутри инструмента. Канал (`telegram_notify`)
решает, какие из этих задач его, кого тегать и как выглядит сообщение.

Проход живёт в цикле опроса чата (`telegram_source.start_polling`), как и
наблюдатель за статусами. Но ходит он **реже цикла**: срок меряется днями, а
тик у поллера — секунды, и открывать на каждом файлы всех задач незачем.

Просрочка сюда не входит: у неё своё сообщение и свои правила повтора.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Callable

from . import registry, telegram_messages, telegram_notify, telegram_source
from .board_parser import parse_board
from .config import load_project_config
from .statuses import is_terminal, load_pipeline
from .task_parser import due_left, parse_task

# Лестница порогов: за сколько дней до срока предупреждать. Глобальная, как и
# сам бот. **Не одно число**: у задачи на две недели и у задачи на день «скоро»
# наступает в разные моменты, и одно окно обслуживает только один из концов —
# лестница же даёт задаче ровно те границы, которые она реально пересекает.
# Пусто — осознанное «молчим», как у свежести карточки
THRESHOLD_KEY = "telegram_due_days"

# Отметки о посланном в файле состояния:
# `{папка задач: {TASK-NNN: "срок@граница"}}`. Рядом с курсором очереди и **не в
# конфиге**: конфиг хранит выбор человека, а не служебную память инструмента
MARKS_KEY = "due_notified"

# Как часто проходить по задачам. Чаще незачем: срок меняется днями, а не
# секундами, и каждый проход открывает файл каждой незавершённой задачи
PERIOD = 3600.0


def thresholds(cfg: dict) -> list[int]:
    """Границы напоминаний из настроек — от дальней к ближней, без повторов.

    Значение приходит списком (конфиг), строкой «7, 3, 1» (форма настроек) или
    одним числом. Мусор, ноль и отрицательные молча выпадают: «напомнить за ноль
    дней» не значит ничего, а требовать от человека чистого списка ради этого
    незачем.
    """
    raw = cfg.get(THRESHOLD_KEY)
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    elif not isinstance(raw, (list, tuple)):
        raw = [raw]
    found: list[int] = []
    for item in raw:
        try:
            day = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if day > 0 and day not in found:
            found.append(day)
    return sorted(found, reverse=True)


def _bucket(left: int, ladder: list[int]) -> int | None:
    """Ближайшая граница, которую задача уже прошла: наименьшая из `>= left`.

    Задача, заведённая за день до срока, попадает сразу в последнюю границу и
    получает **одно** напоминание: границ «за неделю» и «за три дня» она не
    пересекала, и говорить по ним не о чем.
    """
    passed = [day for day in ladder if day >= left]
    return min(passed) if passed else None


def due_events(tasks_dir: Path, project_cfg: dict, days: int,
               today: date | None = None) -> list[dict]:
    """Задачи, чей срок наступает в пределах порога. Повод, без каналов.

    Конец маршрута и съезды пропускаются целиком: у задачи, которая уже никуда
    не поедет, срок ничего не значит. Терминальность берётся из пайплайна
    **этого** проекта — имена статусов у всех свои.

    Просроченные не возвращаются: «скоро» и «уже поздно» — разные поводы.

    **Заведённая сегодня задача — ещё не повод.** Иначе задача, попавшая в окно
    сразу при создании (срок завтра, срок через два дня), получала бы
    напоминание через минуту после того, как человек сам её принёс. Дата
    заведения есть не у всех — у старых задач поля нет, и молчать из-за этого
    не надо.
    """
    tasks_dir = Path(tasks_dir)
    board = tasks_dir / "board.md"
    if days <= 0 or not board.is_file():
        return []
    pipeline = load_pipeline(project_cfg)
    try:
        parsed = parse_board(board, pipeline)
    except Exception:  # noqa: BLE001 — доску правят руками; битую переживаем молча
        return []

    found: list[dict] = []
    for column in parsed.get("columns", []):
        if is_terminal(pipeline, str(column.get("status") or "")):
            continue
        for group in column.get("groups", []):
            for task in group.get("tasks", []):
                task_id = str(task.get("id") or "").strip()
                if not task_id:
                    continue
                meta = (parse_task(tasks_dir, task_id) or {}).get("meta") or {}
                due = str(meta.get("due", "") or "").strip()
                left = due_left(due, today)
                if left is None or not 0 <= left <= days:
                    continue
                if _made_today(meta, today):
                    continue
                found.append({"id": task_id, "due": due, "left": left,
                              "title": str(meta.get("title") or task_id),
                              "meta": meta})
    return found


def _made_today(meta: dict, today: date | None = None) -> bool:
    """Задачу завели сегодня? Даты нет или она нечитаема — считаем, что нет."""
    raw = str(meta.get("created", "") or "").strip()[:10]
    try:
        return date.fromisoformat(raw) == (today or date.today())
    except ValueError:
        return False


def _phrase(left: int) -> str:
    """«Сколько осталось» словами: считать в уме не должен никто."""
    if left == 0:
        return "срок сегодня"
    if left == 1:
        return "срок завтра"
    last, teen = left % 10, 11 <= left % 100 <= 14
    word = "день" if not teen and last == 1 else (
        "дня" if not teen and 2 <= last <= 4 else "дней")
    return f"до срока {left} {word}"


def _message(event: dict, mentions: list[str], project: str = "") -> str:
    """Что человек прочитает в чате.

    Дата стоит рядом со словами: «через три дня» отвечает на вопрос «когда
    браться», а сама дата — на вопрос «а какое там число».
    """
    fields = [("Срок", f"{_phrase(event['left'])} · {event['due']}")]
    if project:
        fields.append(("Проект", f"«{project}»"))
    return telegram_messages.card(
        "⏰", "Срок задачи приближается",
        task_id=event["id"], task_title=event["title"],
        fields=fields, mentions=mentions)


def load_marks() -> dict:
    """Отметки о посланном с прошлых запусков."""
    return telegram_source.read_state().get(MARKS_KEY) or {}


def save_marks(marks: dict) -> None:
    """Сохранить отметки: без диска они не пережили бы перезапуск."""
    telegram_source.patch_state(**{MARKS_KEY: marks})


def check_project(tasks_dir: Path, cfg: dict, project_cfg: dict, marks: dict,
                  send: Callable | None = None,
                  today: date | None = None, project_name: str = "") -> int:
    """Один проход по проекту. Возвращает число отправленных напоминаний.

    Ключ отметки — **срок задачи вместе с пройденной границей**: пока задача
    стоит на одной ступени лестницы, напоминание одно. Шагнула на следующую —
    приходит новое; перенесли срок — тоже, это уже другое обещание. Отметки
    задач, ушедших из окна, на проходе пропадают, иначе файл состояния рос бы
    вечно.

    Отметка ставится и при неудачной отправке: иначе упавшая сеть повторяла бы
    одно и то же сообщение каждым проходом.
    """
    tasks_dir = Path(tasks_dir)
    ladder = thresholds(cfg)
    if not ladder or not telegram_source.enabled(cfg):
        return 0
    events = due_events(tasks_dir, project_cfg, ladder[0], today)

    key = str(tasks_dir)
    before = marks.get(key) or {}
    fresh: dict[str, str] = {}
    reply = send or telegram_source.send_message
    sent = 0
    for event in events:
        step = _bucket(event["left"], ladder)
        if step is None:
            continue
        mark = f"{event['due']}@{step}"
        fresh[event["id"]] = mark
        if before.get(event["id"]) == mark:
            continue
        targets = telegram_notify.targets(event["meta"], cfg)
        if not targets:
            continue
        text = _message(event, targets["mentions"],
                        project_name or tasks_dir.parent.name)
        try:
            if send is not None:
                reply(targets["chat_id"], text,
                      parse_mode=telegram_messages.PARSE_MODE)
            else:
                reply(telegram_source.token(cfg), targets["chat_id"], text,
                      proxy=telegram_source.proxy(cfg),
                      api_root=telegram_source.api_root(cfg),
                      parse_mode=telegram_messages.PARSE_MODE)
            sent += 1
        except Exception:  # noqa: BLE001 — сеть, отказ API: отметка уже стоит
            pass
    marks[key] = fresh
    return sent


def check_all(cfg: dict, state: dict, projects: list[dict] | None = None,
              now: float | None = None, today: date | None = None,
              send: Callable | None = None) -> int:
    """Проход по всем проектам реестра, но не чаще `PERIOD`.

    Расписание живёт в памяти процесса, а не на диске: после запуска сервера
    первый проход должен случиться сразу — задача со сроком завтра ждать часа
    не должна. От лавины при этом защищает не молчание, а отметка о посланном.
    """
    now = time.time() if now is None else now
    last = state.get("last_run")
    if last is not None and now - last < PERIOD:
        return 0
    state["last_run"] = now

    if projects is None:
        projects = registry.list_projects().get("projects", [])
    marks = load_marks()
    total = 0
    for project in projects:
        tasks_dir = Path(str(project.get("tasks_dir") or ""))
        if not tasks_dir.is_dir():
            continue
        try:
            total += check_project(tasks_dir, cfg, load_project_config(tasks_dir),
                                   marks, send=send, today=today,
                                   project_name=str(project.get("name") or ""))
        except Exception:  # noqa: BLE001 — один битый проект не должен
            continue      # останавливать остальные
    save_marks(marks)
    return total
