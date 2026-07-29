"""Починка рассинхрона доски и файлов задач.

Статус задачи живёт в двух местах — разделе `board.md` и поле `status:` во
frontmatter, — и агент, правящий один конец без второго, оставляет за собой
россыпь предупреждений валидатора. Здесь они чинятся разом.

Правило одно: **файлы задач — источник правды**. Доска может врать: её
копируют между проектами вместе с чужими строками, правят руками, оставляют
устаревшие ссылки. Файл задачи — единственное, что реально существует,
поэтому доска подстраивается под файлы, а не наоборот. Отсюда виды правок:

- `add`    — файл есть, строки нет: задача возвращается на доску в раздел
             своего `status:` (незнакомый статус → раздел создания задач)
- `move`   — строка лежит не в том разделе: переезжает в раздел статуса
             из файла (сам файл не трогаем — он правда)
- `lost`   — строке не соответствует никакой файл: запись уезжает в
             технический раздел. Сюда же попадают **чужие** строки со
             скопированной доски: id совпал с нашим файлом, но ни имя
             файла, ни заголовок — нет, значит это не наша задача
- `relink` — файл переименовали, а строка осталась со старой ссылкой:
             переписывается на актуальное имя (только если заголовок
             подтверждает, что задача та же)
- `retitle` — ссылка у строки точная, а заголовок чужой или устаревший:
             строка повторяет заголовок из frontmatter файла

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
from backend.queue_ops import (add_entry, ensure_plain_section, move_task,
                               relink_entry, retitle_entry)
from backend.statuses import Pipeline, load_pipeline
from backend.task_parser import parse_frontmatter

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


def _same_section(a: str, b: str) -> bool:
    """Сравнение разделов доски: регистр и отступы не важны."""
    return a.strip().lower() == b.strip().lower()


def task_files(tasks_dir: Path) -> dict[str, dict]:
    """Реальные файлы задач: id → {path, title, status}.

    Заголовок — только из frontmatter: производный от имени файла не годится
    для опознания строки (сравнивать нечего), а точное имя файла и так
    проверяется отдельно.
    """
    files: dict[str, dict] = {}
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        m = _TASK_ID_RE.match(path.name)
        if not m:
            continue
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        files.setdefault(m.group(1), {
            "path": path,
            "title": (meta.get("title") or "").strip(),
            "status": (meta.get("status") or "").strip(),
        })
    return files


def row_matches_file(entry: dict, info: dict) -> bool:
    """Строка доски относится к этому файлу задачи?

    Одного id мало: доску копируют между проектами, и чужая строка TASK-001
    не должна привязываться к нашему TASK-001. Строка своя, если ссылается
    на файл точным именем или повторяет его заголовок из frontmatter.
    """
    if entry["file"] == info["path"].name:
        return True
    return bool(info["title"]) and info["title"] == entry["title"].strip()


def plan_repair(tasks_dir: Path, cfg: dict) -> dict:
    """Что разошлось между доской и файлами. Ничего не меняет."""
    board_path = tasks_dir / cfg.get("board_file", "board.md")
    if not board_path.is_file():
        return {"add": [], "move": [], "lost": [], "relink": [], "retitle": []}

    pipeline = load_pipeline(cfg)
    board = parse_board(board_path, pipeline)
    on_board = _on_board(board, cfg)
    in_lost = _in_lost(board, cfg)
    files = task_files(tasks_dir)

    add: list[dict] = []
    move: list[dict] = []
    lost: list[dict] = []
    relink: list[dict] = []
    retitle: list[dict] = []

    # Строки доски: без своего файла — в свалку, со своим — сверяем ссылку,
    # заголовок и раздел. «Свой» определяется не одним id, а связкой
    # id+имя/заголовок
    for task_id, entry in on_board.items():
        info = files.get(task_id)
        if info is None or not row_matches_file(entry, info):
            lost.append({"id": task_id, "file": entry["file"], "title": entry["title"],
                         "section": entry["section"]})
            continue
        if entry["file"] != info["path"].name:
            relink.append({"id": task_id, "section": entry["section"],
                           "from": entry["file"], "to": info["path"].name})
        elif info["title"] and entry["title"].strip() != info["title"]:
            # Ссылка точная, а заголовок нет: чужой текст легализовала прошлая
            # починка или задачу переименовали в файле — файл правда
            retitle.append({"id": task_id, "section": entry["section"],
                            "from": entry["title"].strip(), "to": info["title"]})
        target_section = _section_for_status(pipeline, info["status"])
        if target_section and not _same_section(entry["section"], target_section):
            move.append({"id": task_id, "file": info["path"].name,
                         "from": entry["section"], "to": target_section})

    # Файлы задач, которых нет на доске (чужая строка с тем же id — не считается)
    for task_id, info in files.items():
        row = on_board.get(task_id)
        if row is not None and row_matches_file(row, info):
            continue
        section = _section_for_status(pipeline, info["status"])
        if not section:
            continue
        # Файл вернулся, а его строка ждёт в свалке — её и переносим обратно,
        # иначе на доске окажутся две записи об одной задаче. Чужая строка
        # в свалке под наш id — не наша, пусть лежит
        lost_row = in_lost.get(task_id)
        restore = lost_row is not None and row_matches_file(lost_row, info)
        add.append({"id": task_id, "file": info["path"].name,
                    "title": info["title"] or _title_from_file(info["path"]),
                    "status": info["status"], "section": section,
                    "restore": restore})

    return {"add": add, "move": move, "lost": lost, "relink": relink,
            "retitle": retitle}


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
            result = move_task(tasks_dir, cfg, item["id"], lost_section(cfg),
                               touch_status=False)
            if not result.get("ok"):
                failed.append(f"{item['id']}: {result.get('error')}")

    for item in plan["relink"]:
        result = relink_entry(board_path, item["id"], item["to"])
        if not result.get("ok"):
            failed.append(f"{item['id']}: {result.get('error')}")

    for item in plan["retitle"]:
        result = retitle_entry(board_path, item["id"], item["to"])
        if not result.get("ok"):
            failed.append(f"{item['id']}: {result.get('error')}")

    # Перенос строк под статус файла: frontmatter не трогаем — он источник правды
    for item in plan["move"]:
        result = move_task(tasks_dir, cfg, item["id"], item["to"], touch_status=False)
        if not result.get("ok"):
            failed.append(f"{item['id']}: {result.get('error')}")

    for item in plan["add"]:
        if item.get("restore"):
            result = move_task(tasks_dir, cfg, item["id"], item["section"],
                               touch_status=False)
            # Строка в свалке могла хранить ссылку на старое имя файла —
            # переносим с актуальной, иначе вернувшаяся запись снова «без файла»
            if result.get("ok"):
                result = relink_entry(board_path, item["id"], item["file"])
        else:
            entry = f"- {item['id']} · [{item['title']}]({item['file']})"
            result = add_entry(board_path, pipeline, item["section"], entry)
        if not result.get("ok"):
            failed.append(f"{item['id']}: {result.get('error')}")

    return {"ok": not failed,
            "added": len(plan["add"]),
            "moved": len(plan["move"]),
            "lost": len(plan["lost"]),
            "relinked": len(plan["relink"]),
            "retitled": len(plan["retitle"]),
            "failed": failed}
