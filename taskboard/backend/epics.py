"""Реестр эпиков проекта (tasks/epics.md).

Имя эпика хранится только здесь — задачи ссылаются на него ключом во
frontmatter. Поэтому создание задачи с новым эпиком обязано пополнить реестр,
иначе на доске появится ссылка на эпик, имени которого никто не знает.

Формат записи в файле: `## <ключ> — <имя>`, под ней может быть описание,
дописанное пользователем; трогать его нельзя.
"""

from __future__ import annotations

import re
from pathlib import Path

EPICS_FILE = "epics.md"
_LIST_HEADING = "## Список эпиков"
_EMPTY = "_(нет)_"

# Запись эпика: «## E056-18500 — Инвентаризация» (имя может отсутствовать).
# Суффикс ключа не обязан быть числовым: кроме Jira-ключей люди заводят
# мнемонические — «STALL-001», «E001-STALL». Раньше такая запись молча не
# считалась эпиком, и его имя пропадало из карточки без единого сообщения.
# Ключ — одно «слово» с дефисом внутри, поэтому заголовки разделов реестра
# («## Список эпиков», «## Формат записи») под него не подходят
_ENTRY_RE = re.compile(
    r"^##\s+(?P<key>[A-Za-z][\w.]*-[A-Za-z0-9][\w.-]*)\s*(?:—|-|–)?\s*(?P<name>.*)$")


def epics_path(tasks_dir: Path) -> Path:
    return Path(tasks_dir) / EPICS_FILE


def list_epics(tasks_dir: Path) -> list[dict]:
    """Эпики реестра: [{key, name}]. Нет файла — пустой список, не ошибка."""
    path = epics_path(tasks_dir)
    try:
        content = path.read_text(encoding="utf-8-sig")
    except Exception:
        return []

    out: list[dict] = []
    for line in content.splitlines():
        m = _ENTRY_RE.match(line.strip())
        if m:
            out.append({"key": m.group("key"), "name": m.group("name").strip()})
    return out


def epic_name(tasks_dir: Path, key: str) -> str:
    """Имя эпика по ключу. Неизвестный ключ или `~` — пустая строка."""
    key = (key or "").strip()
    if not key or key == "~":
        return ""
    for epic in list_epics(tasks_dir):
        if epic["key"] == key:
            return epic["name"]
    return ""


def _task_epic(path: Path) -> str:
    """Ключ эпика из frontmatter задачи (читаем шапку, а не файл целиком)."""
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for i, line in enumerate(fh):
                if i and line.startswith("---"):
                    break
                m = re.match(r"^epic:\s*(.*)$", line.strip())
                if m:
                    value = m.group(1).strip()
                    return "" if value in ("", "~") else value
    except Exception:
        pass
    return ""


def annotate_epics(tasks_dir: Path, board: dict) -> dict:
    """Проставить карточкам доски ключ эпика.

    В строке board.md эпика нет, поэтому берём его из frontmatter файлов задач.
    Только ключ: на превью нужна короткая метка, имя эпика показывается уже
    в открытой карточке задачи. Задачи без эпика поля не получают.
    """
    for column in board.get("columns", []):
        for group in column.get("groups", []):
            for task in group.get("tasks", []):
                key = _task_epic(Path(tasks_dir) / task.get("file", ""))
                if key:
                    task["epic"] = key
    return board


def register_epic(tasks_dir: Path, key: str, name: str = "") -> bool:
    """Добавить эпик в реестр, если его там нет. Возвращает True, если добавили.

    Имя существующего эпика не переписываем: реестр — источник правды, а
    создание задачи не повод переименовывать эпик задним числом.
    """
    key = (key or "").strip()
    if not key:
        return False
    if any(e["key"] == key for e in list_epics(tasks_dir)):
        return False

    path = epics_path(tasks_dir)
    entry = f"## {key} — {name.strip()}" if name.strip() else f"## {key}"
    try:
        content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    except Exception:
        content = ""

    if _LIST_HEADING in content:
        head, _, tail = content.partition(_LIST_HEADING)
        tail = tail.replace(f"\n\n{_EMPTY}\n", "\n", 1) if _EMPTY in tail else tail
        content = f"{head}{_LIST_HEADING}{tail.rstrip()}\n\n{entry}\n"
    else:
        content = (content.rstrip() + "\n\n" if content.strip() else "# Epics\n\n") \
            + f"{_LIST_HEADING}\n\n{entry}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
