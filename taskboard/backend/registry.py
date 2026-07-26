"""Реестр проектов taskboard (~/.taskboard/projects.json)."""

from __future__ import annotations

import json
from pathlib import Path

from backend.config import GLOBAL_DIR, PROJECTS_FILE


def _load() -> dict:
    """Прочитать реестр, при ошибке — пустая структура."""
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"active": None, "projects": []}


def _save(data: dict) -> None:
    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _default_name(tasks_dir: Path) -> str:
    """Имя проекта по умолчанию — имя родительской папки tasks/."""
    return tasks_dir.parent.name or str(tasks_dir)


def list_projects() -> dict:
    """Вернуть реестр целиком."""
    return _load()


def register_project(tasks_dir: Path, name: str | None = None, activate: bool = True) -> dict:
    """
    Зарегистрировать проект (или обновить существующий по tasks_dir).

    Дубликаты имён разрешаются числовым суффиксом.
    """
    tasks_dir = tasks_dir.resolve()
    data = _load()

    for proj in data["projects"]:
        if Path(proj["tasks_dir"]) == tasks_dir:
            if activate:
                data["active"] = proj["name"]
            _save(data)
            return proj

    base = name or _default_name(tasks_dir)
    existing = {p["name"] for p in data["projects"]}
    final = base
    n = 2
    while final in existing:
        final = f"{base}-{n}"
        n += 1

    proj = {"name": final, "tasks_dir": str(tasks_dir)}
    data["projects"].append(proj)
    if activate or not data["active"]:
        data["active"] = final
    _save(data)
    return proj


def remove_project(name: str) -> bool:
    """Удалить проект из реестра (файлы проекта не трогаются)."""
    data = _load()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p["name"] != name]
    if len(data["projects"]) == before:
        return False
    if data["active"] == name:
        data["active"] = data["projects"][0]["name"] if data["projects"] else None
    _save(data)
    return True


def activate_project(name: str) -> dict | None:
    """Сделать проект активным, вернуть его запись."""
    data = _load()
    for proj in data["projects"]:
        if proj["name"] == name:
            data["active"] = name
            _save(data)
            return proj
    return None


def get_active() -> dict | None:
    """Вернуть запись активного проекта."""
    data = _load()
    for proj in data["projects"]:
        if proj["name"] == data["active"]:
            return proj
    return None
