"""Парсинг файлов задач: frontmatter + markdown-тело."""

from __future__ import annotations

import re
from pathlib import Path

_TASK_FILE_RE = re.compile(r"^TASK-\d+.*\.md$")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Разобрать YAML-lite frontmatter (key: value между --- строками).

    Возвращает (meta, body). При отсутствии frontmatter — ({}, весь текст).
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end < 0:
        return {}, content

    meta: dict[str, str] = {}
    for line in content[3:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    body = content[end + 4:].lstrip("\n")
    return meta, body


def find_task_file(tasks_dir: Path, task_id: str) -> Path | None:
    """Найти файл задачи по id (TASK-NNN)."""
    for f in tasks_dir.glob(f"{task_id}*.md"):
        if _TASK_FILE_RE.match(f.name):
            return f
    return None


def parse_task(tasks_dir: Path, task_id: str) -> dict | None:
    """Прочитать задачу: meta + markdown-тело."""
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return None
    content = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)
    return {"id": task_id, "file": path.name, "meta": meta, "body": body}


def list_all_tasks(tasks_dir: Path) -> list[dict]:
    """Все задачи проекта: [{id, title}] — для подсказок blocked_by.

    Сортировка по номеру задачи (TASK-001 < TASK-002 < TASK-010).
    """
    if not tasks_dir or not tasks_dir.is_dir():
        return []
    results: list[dict] = []
    for path in sorted(tasks_dir.glob("TASK-*.md")):
        if not _TASK_FILE_RE.match(path.name):
            continue
        match_id = re.match(r"^(TASK-\d+)", path.name)
        if not match_id:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _body = parse_frontmatter(content)
        results.append({"id": match_id.group(1), "title": meta.get("title", "")})
    return results


def set_meta_fields(path: Path, updates: dict[str, str]) -> bool:
    """Записать поля frontmatter файла задачи. Возвращает успех.

    Поля, которых в шапке нет, дописываются в конец: задачи, заведённые до
    появления поля, не должны требовать ручной правки. Тело файла не трогаем.
    """
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
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
            # Замена функцией: в значении может быть \1 или обратный слэш —
            # как шаблон подстановки re такую строку либо испортит, либо уронит
            header = re.sub(pattern, lambda _m, ln=line: ln, header, flags=re.MULTILINE)
        else:
            header = header.rstrip("\n") + f"\n{line}"

    path.write_text(header + content[end:], encoding="utf-8")
    return True


def set_task_status(tasks_dir: Path, task_id: str, status: str) -> bool:
    """Обновить status в frontmatter задачи. Возвращает успех."""
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return False
    return set_meta_fields(path, {"status": status})


def slugify(text: str) -> str:
    """Преобразовать текст в slug для имени файла (как в create_task.py)."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text


def normalize_title(text: str) -> str:
    """Название в одну строку: любые пробелы и переносы схлопываются в пробел.

    Заголовок живёт во frontmatter (одна строка `title:`) и в строке доски —
    перевод строки внутри разорвал бы и то, и другое.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def set_task_title(tasks_dir: Path, task_id: str, new_title: str) -> dict:
    """Обновить title во frontmatter и переименовать файл задачи.

    Возвращает {"ok": True, "title": "...", "file": "TASK-NNN-новый-slug.md"}
    или {"ok": False, "error": "..."}.
    """
    new_title = normalize_title(new_title)
    if not new_title:
        return {"ok": False, "error": "Название не может быть пустым"}
    # Строка доски — `[Заголовок](файл)`, и `](` внутри заголовка обрывает
    # ссылку раньше времени: одиночные скобки безобидны, эта пара — нет
    if "](" in new_title:
        return {"ok": False, "error": "Название не может содержать «](»"}
    path = find_task_file(tasks_dir, task_id)
    if path is None:
        return {"ok": False, "error": f"Задача не найдена: {task_id}"}
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {"ok": False, "error": "Нет frontmatter"}
    end = content.find("\n---", 3)
    if end < 0:
        return {"ok": False, "error": "Нет закрывающего ---"}

    header = content[:end]
    # Замена — функцией: в названии может быть \1 или обратный слэш, и как
    # шаблон подстановки re такую строку либо испортит, либо уронит
    if re.search(r"^title:.*$", header, flags=re.MULTILINE):
        header = re.sub(r"^title:.*$", lambda _m: f"title: {new_title}", header, flags=re.MULTILINE)
    else:
        header += f"\ntitle: {new_title}"

    # Имя файла собирается как в create_task.py; название из одних знаков
    # препинания даёт пустой slug — тогда остаётся голый номер задачи
    slug = slugify(new_title).strip("-")
    new_name = f"{task_id}-{slug}.md" if slug else f"{task_id}.md"
    new_path = path.with_name(new_name)

    if path != new_path and new_path.exists():
        return {"ok": False, "error": f"Файл уже существует: {new_name}"}

    path.write_text(header + content[end:], encoding="utf-8")

    if path != new_path:
        path.rename(new_path)

    return {"ok": True, "title": new_title, "file": new_name}
