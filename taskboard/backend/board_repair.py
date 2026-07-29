"""Починка рассинхрона доски и файлов задач.

Статус задачи живёт в двух местах — разделе `board.md` и поле `status:` во
frontmatter, — и агент, правящий один конец без второго, оставляет за собой
россыпь предупреждений валидатора. Здесь они чинятся разом.

Правило одно: **доска — источник правды**. Она задаёт, где задача находится;
файл под неё подстраивается. Отсюда три вида правок:

- `add`    — файл есть, строки нет: задача возвращается на доску в раздел
             своего `status:` (незнакомый статус → раздел создания задач)
- `status` — строка и файл разошлись: `status:` в файле приводится к разделу
- `lost`   — строки не на что ссылаться: запись уезжает в технический раздел

Ничего не удаляется: чужие данные нам не принадлежат, и «сирота» на доске
может оказаться файлом, который просто не подтянули из чужой ветки.

План (`plan_repair`) и применение (`apply_repair`) разделены намеренно —
пользователь сначала видит список правок, а соглашается вторым нажатием.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.board_parser import parse_board
from backend.config import is_lost_section, lost_section
from backend.queue_ops import add_entry, ensure_plain_section, move_task
from backend.statuses import Pipeline, load_pipeline
from backend.task_parser import parse_frontmatter, set_task_status

_TASK_ID_RE = re.compile(r"^(TASK-\d+)")


def visible_columns(board: dict, cfg: dict) -> list[dict]:
    """Колонки доски без технических разделов — то, что показывает UI."""
    return [c for c in board["columns"] if not is_lost_section(c["title"], cfg)]


def _title_from_file(path: Path) -> str:
    """Заголовок задачи: из frontmatter, иначе из имени файла."""
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    title = (meta.get("title") or "").strip()
    if title:
        return title
    return re.sub(r"^TASK-\d+-", "", path.stem).replace("-", " ")


def _on_board(board: dict, cfg: dict) -> dict[str, dict]:
    """Задачи, стоящие на доске: id → запись + раздел (без технического)."""
    found: dict[str, dict] = {}
    for column in board["columns"]:
        if is_lost_section(column["title"], cfg):
            continue
        for group in column["groups"]:
            for task in group["tasks"]:
                found.setdefault(task["id"], {**task, "section": column["title"],
                                              "status": column["status"]})
    return found


def _in_lost(board: dict, cfg: dict) -> dict[str, dict]:
    """Записи, лежащие в техническом разделе: id → запись."""
    found: dict[str, dict] = {}
    for column in board["columns"]:
        if not is_lost_section(column["title"], cfg):
            continue
        for group in column["groups"]:
            for task in group["tasks"]:
                found.setdefault(task["id"], task)
    return found


def _section_for_status(pipeline: Pipeline, status: str) -> str | None:
    """Раздел доски для статуса файла; незнакомый статус — не повод потерять задачу."""
    section = pipeline.section_of(status) if status else None
    if section:
        return section
    return pipeline.section_of(pipeline.action("create") or "")


def plan_repair(tasks_dir: Path, cfg: dict) -> dict:
    """Что разошлось между доской и файлами. Ничего не меняет."""
    board_path = tasks_dir / cfg.get("board_file", "board.md")
    if not board_path.is_file():
        return {"add": [], "status": [], "lost": []}

    pipeline = load_pipeline(cfg)
    board = parse_board(board_path, pipeline)
    on_board = _on_board(board, cfg)
    in_lost = _in_lost(board, cfg)

    add: list[dict] = []
    status_fix: list[dict] = []
    lost: list[dict] = []

    # Файлы задач, которых нет на доске
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        m = _TASK_ID_RE.match(path.name)
        if not m or m.group(1) in on_board:
            continue
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        status = (meta.get("status") or "").strip()
        section = _section_for_status(pipeline, status)
        if not section:
            continue
        # Файл вернулся, а строка ждёт в свалке — её и переносим обратно,
        # иначе на доске окажутся две записи об одной задаче
        add.append({"id": m.group(1), "file": path.name, "title": _title_from_file(path),
                    "status": status, "section": section,
                    "restore": m.group(1) in in_lost})

    # Строки доски: без файла — в свалку, с файлом — сверяем статус
    for task_id, entry in on_board.items():
        path = tasks_dir / entry["file"]
        if not path.is_file():
            lost.append({"id": task_id, "file": entry["file"], "section": entry["section"]})
            continue
        target = pipeline.status_for_section(entry["section"])
        if not target:
            continue  # раздел вне пайплайна: статуса, к которому приводить, нет
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        current = (meta.get("status") or "").strip()
        if current != target:
            status_fix.append({"id": task_id, "file": entry["file"], "section": entry["section"],
                               "from": current, "to": target})

    return {"add": add, "status": status_fix, "lost": lost}


def apply_repair(tasks_dir: Path, cfg: dict) -> dict:
    """Выполнить починку. Возвращает счётчики сделанного и остаток проблем."""
    board_path = tasks_dir / cfg.get("board_file", "board.md")
    pipeline = load_pipeline(cfg)
    plan = plan_repair(tasks_dir, cfg)
    failed: list[str] = []

    # Сироты первыми: они уезжают из разделов, где потом окажутся возвращённые задачи
    if plan["lost"]:
        ensure_plain_section(board_path, lost_section(cfg))
        for item in plan["lost"]:
            result = move_task(tasks_dir, cfg, item["id"], lost_section(cfg))
            if not result.get("ok"):
                failed.append(f"{item['id']}: {result.get('error')}")

    for item in plan["add"]:
        if item.get("restore"):
            result = move_task(tasks_dir, cfg, item["id"], item["section"])
        else:
            entry = f"- {item['id']} · [{item['title']}]({item['file']})"
            result = add_entry(board_path, pipeline, item["section"], entry)
        if not result.get("ok"):
            failed.append(f"{item['id']}: {result.get('error')}")

    for item in plan["status"]:
        if not set_task_status(tasks_dir, item["id"], item["to"]):
            failed.append(f"{item['id']}: не удалось обновить status в файле")

    return {"ok": not failed,
            "added": len(plan["add"]),
            "restatused": len(plan["status"]),
            "lost": len(plan["lost"]),
            "failed": failed}
