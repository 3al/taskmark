"""Конфигурация taskboard: дефолты + глобальный + per-project конфиг."""

from __future__ import annotations

import json
from pathlib import Path

# Директория глобальных данных taskboard (реестр проектов, глобальный конфиг)
GLOBAL_DIR = Path.home() / ".taskboard"
GLOBAL_CONFIG_FILE = GLOBAL_DIR / "config.json"
PROJECTS_FILE = GLOBAL_DIR / "projects.json"

# Дефолтная конфигурация
DEFAULTS: dict = {
    "port": 8765,
    "tasks_dir": "tasks",
    "board_file": "board.md",
    "create_script": "create_task.py",
    "logs_dir": "logs",
    "queue_section": "Queue",
    "queued_status": "queued",
    "dnd_full_board": False,
    "statuses": ["backlog", "queued", "development", "review", "testing", "completed"],
    "theme": "dark",
}


def _read_json(path: Path) -> dict:
    """Прочитать json-файл, при ошибке вернуть пустой словарь."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_global_config() -> dict:
    """Загрузить глобальный конфиг (создать с дефолтами при отсутствии)."""
    if not GLOBAL_CONFIG_FILE.exists():
        try:
            GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
            GLOBAL_CONFIG_FILE.write_text(
                json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        return dict(DEFAULTS)
    cfg = dict(DEFAULTS)
    cfg.update(_read_json(GLOBAL_CONFIG_FILE))
    return cfg


def project_config_path(tasks_dir: Path) -> Path:
    """Путь к per-project конфигу: <корень проекта>/taskboard/config.json."""
    return tasks_dir.parent / "taskboard" / "config.json"


def load_project_config(tasks_dir: Path) -> dict:
    """Загрузить per-project переопределения поверх глобального конфига."""
    cfg = load_global_config()
    cfg.update(_read_json(project_config_path(tasks_dir)))
    return cfg


def save_global_config(updates: dict) -> dict:
    """Слить updates в глобальный конфиг и сохранить. Возвращает итоговый конфиг."""
    cfg = load_global_config()
    cfg.update(updates)
    try:
        GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        GLOBAL_CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return cfg
