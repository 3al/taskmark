"""Парсинг board.md в структуру колонок и задач."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from backend.config import DEFAULTS
from backend.stall import is_terminal
from backend.statuses import Pipeline

# Строка задачи: - TASK-NNN · [Заголовок](файл.md) · агент · дата
# Допускается зачёркивание ~~...~~ вокруг всей записи
# Заголовок пишет человек, и скобки в нём — обычное дело (`[BE] [VIEWER] Счетчик`),
# поэтому он ленивый: конец ссылки — первое `](`, а не первая `]`
_ENTRY_RE = re.compile(
    r"^\s*-\s*(?P<struck>~~)?\s*"
    r"(?P<id>TASK-\d+)\s*·\s*"
    r"\[(?P<title>.+?)\]\((?P<file>[^)]+)\)"
    r"(?P<tail>.*?)(?P=struck)?\s*$"
)

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$")

# Дата перехода в хвосте записи. Ею меряется возраст задачи в статусе, поэтому
# отличать её от имени исполнителя приходится по форме: порядок сегментов
# хвоста никем не гарантирован, а даты в имени модели не бывает
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Голова записи до хвоста: «- TASK-NNN · [Заголовок](файл)». Ленивый заголовок —
# по той же причине, что и в _ENTRY_RE: скобки внутри него норма
_HEAD_RE = re.compile(r"^(\s*-\s*(?:~~)?\s*TASK-\d+\s*·\s*\[.+?\]\([^)]*\))(.*)$")


def _split_tail(tail: str) -> tuple[str, str]:
    """Разложить хвост «Агент · дата» на исполнителя и дату перехода.

    Обе части необязательны: у задачи, которую ни разу не двигали, хвоста нет
    вовсе, а у строки, написанной руками, может не быть даты.
    """
    parts = [p.strip() for p in tail.split("·") if p.strip()]
    moved = next((p for p in parts if _DATE_RE.match(p)), "")
    agent = next((p for p in parts if not _DATE_RE.match(p)), "")
    return agent, moved


def retail_entry(entry: str, moved: str) -> str:
    """Переписать хвост записи на «· прежний агент · дата».

    Перенос мышью — такая же смена статуса, как вызов скрипта, и дату он
    обязан обновлять: иначе она показывает предыдущий переход, а возраст в
    статусе врёт. Исполнителя при этом сохраняем прежнего — как делает
    `set_status.py` без `--agent`: кто двигал строку мышью, доска не знает.

    Хвоста не было — не выдумываем его: строка без исполнителя остаётся
    строкой без исполнителя.
    """
    m = _HEAD_RE.match(entry)
    if not m:
        return entry
    head, tail = m.group(1), m.group(2)
    struck = tail.rstrip().endswith("~~")
    agent, old_date = _split_tail(tail.strip().rstrip("~"))
    if not agent and not old_date:
        return entry
    out = f"{head} · {agent} · {moved}" if agent else f"{head} · {moved}"
    return f"{out}~~" if struck else out


def _parse_entry(line: str) -> dict | None:
    """Распарсить строку задачи, вернуть dict или None."""
    m = _ENTRY_RE.match(line)
    if not m:
        return None
    tail = m.group("tail").strip()
    # tail: " · Агент · дата" — убираем ведущий разделитель
    tail = re.sub(r"^·\s*", "", tail).strip()
    agent, moved = _split_tail(tail)
    return {
        "id": m.group("id"),
        "title": m.group("title"),
        "file": m.group("file"),
        # meta — хвост целиком: его читают старые сборки фронта, и он же
        # остаётся исходником, если разбор на части когда-нибудь разойдётся
        "meta": tail,
        "agent": agent,
        "moved": moved,
        "struck": bool(m.group("struck")),
    }


def _edited_days_ago(path: Path, today: date) -> int | None:
    """Сколько дней назад правили файл задачи (None — файла нет).

    Время правки берётся у файловой системы, а не из поля во frontmatter:
    задачу правят четверо — `set_status.py`, API, доска и агент, редактирующий
    файл напрямую, — и поле, которое обязан писать каждый из них, четвёртый
    молча не напишет. `tasks/` лежит в `.gitignore`, так что клонирование
    репозитория время правки не сбрасывает.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return (today - date.fromtimestamp(mtime)).days


def annotate_age(tasks_dir: Path, board: dict, cfg: dict, pipeline=None,
                 today: date | None = None) -> dict:
    """Проставить карточкам возраст в статусе — только залежавшимся.

    Порог считает бэкенд, а не превью: правило «что залежалось» одно на доску,
    и держать его в двух местах незачем. Задача моложе порога поля не получает
    вовсе — нижней строки у неё не будет, карточка останется короткой.

    **Возраст в статусе и залежалость — не одно и то же.** Задачу, которую
    неделю дорабатывают, не двигая статус, дата перехода объявляла бы
    застрявшей (TASK-178). Поэтому порог проверяется дважды: и по дате
    перехода, и по времени правки файла задачи. Показывается при этом возраст
    в статусе — то, что он и означает; работающая задача просто молчит.

    Молчим и там, где возраст неизвестен: задачу ни разу не двигали (даты в
    строке нет), дату испортили руками или она из будущего — залежалостью это
    не является. Время правки из будущего (съехали часы) молчит по той же
    причине: ложная метка «залежалась» хуже её отсутствия.

    Молчим и в конце маршрута: в терминальном статусе и в съезде задача стоит
    по определению — работа окончена. Возраст там был бы шумом того же класса,
    что долг у закрытой задачи.
    """
    threshold = cfg.get("card_stale_days", DEFAULTS["card_stale_days"])
    try:
        threshold = int(threshold)
    except (TypeError, ValueError):
        threshold = DEFAULTS["card_stale_days"]
    today = today or date.today()
    tasks_dir = Path(tasks_dir)

    for column in board.get("columns", []):
        if is_terminal(pipeline, column.get("status", "")):
            continue
        for group in column.get("groups", []):
            for task in group.get("tasks", []):
                moved = task.get("moved") or ""
                try:
                    days = (today - date.fromisoformat(moved)).days
                except ValueError:
                    continue
                if days < threshold or days < 0:
                    continue
                # Файла нет — строка осталась от удалённой задачи; молчать не
                # за что, возраст стоит на одной дате перехода
                edited = _edited_days_ago(tasks_dir / task.get("file", ""), today)
                if edited is not None and edited < threshold:
                    continue
                task["stale_days"] = days
    return board


def parse_board(board_path: Path, pipeline: Pipeline) -> dict:
    """
    Распарсить board.md.

    Возвращает:
      columns: [{status, title, groups: [{title | None, tasks: [...]}]}]
      known_sections: [заголовки ##]
    Подразделы ### внутри раздела становятся группами; записи вне
    подразделов попадают в группу с title=None.

    Соответствие «раздел ↔ статус» и порядок колонок задаёт пайплайн проекта;
    разделы вне пайплайна остаются колонками (со статусом по заголовку) и
    уезжают в конец — молча терять чужие данные нельзя.
    """
    content = board_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    sections: list[dict] = []
    current_section: dict | None = None
    current_group: dict | None = None

    for line in lines:
        hm = _HEADING_RE.match(line)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            if level == 2:
                current_section = {"title": title, "groups": []}
                sections.append(current_section)
                current_group = None
                # Группа по умолчанию для записей вне подразделов
                current_group = {"title": None, "tasks": []}
                current_section["groups"].append(current_group)
            elif level == 3 and current_section is not None:
                current_group = {"title": title, "tasks": []}
                current_section["groups"].append(current_group)
            continue

        if current_section is None or current_group is None:
            continue

        entry = _parse_entry(line)
        if entry:
            current_group["tasks"].append(entry)

    # Собрать колонки по известным статусам + прочие разделы в конец
    status_map = pipeline.section_map()
    order_list = pipeline.keys()
    columns: list[dict] = []
    for section in sections:
        key = section["title"].strip().lower()
        status = status_map.get(key)
        # Убрать пустые группы по умолчанию, если есть именованные
        groups = [g for g in section["groups"] if g["tasks"] or g["title"]]
        columns.append({"status": status or key, "title": section["title"], "groups": groups})

    order = {s: i for i, s in enumerate(order_list)}
    columns.sort(key=lambda c: order.get(c["status"], len(order_list)))

    return {
        "columns": columns,
        "known_sections": [s["title"] for s in sections],
    }
