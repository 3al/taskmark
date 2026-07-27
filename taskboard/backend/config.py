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
    "status_script": "set_status.py",
    "logs_dir": "logs",
    "queue_section": "Queue",
    "queued_status": "queued",
    "dnd_full_board": False,
    # Жизненный цикл задачи: порядок статусов и цели действий скиллов.
    # Разбор и дефолты оформления — в backend/statuses.py
    "pipeline": ["backlog", "queued", "development", "review", "testing", "completed"],
    "actions": {"create": "backlog", "start": "development"},
    "theme": "dark",
}

# Ключи, которые имеет смысл держать на уровне проекта: жизненный цикл у каждого
# проекта свой, а порт и тема — свойства инструмента, а не репозитория
PROJECT_KEYS = {"pipeline", "actions", "statuses", "board_file", "create_script",
                "status_script", "logs_dir", "queue_section", "queued_status",
                "dnd_full_board"}


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
    """Путь к per-project конфигу: <tasks_dir>/.taskboard.json.

    Внутри tasks/, а не в корне проекта: scaffold кладёт туда `.gitignore` с `*`,
    поэтому настройки не засоряют чужой репозиторий. Прежнее расположение
    (<корень>/taskboard/config.json) продолжаем читать — см. legacy_config_path.
    """
    return tasks_dir / ".taskboard.json"


def legacy_config_path(tasks_dir: Path) -> Path:
    """Прежнее место per-project конфига (до переезда внутрь tasks/)."""
    return tasks_dir.parent / "taskboard" / "config.json"


def load_project_config(tasks_dir: Path) -> dict:
    """Загрузить per-project переопределения поверх глобального конфига."""
    cfg = load_global_config()
    cfg.update(_read_json(legacy_config_path(tasks_dir)))
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


def save_project_config(tasks_dir: Path, updates: dict) -> dict:
    """Слить updates в per-project конфиг проекта и сохранить.

    Глобальный конфиг при этом не трогаем: жизненный цикл задач у каждого
    проекта свой, и правка одного не должна переучивать остальные.
    Возвращает итоговый конфиг проекта (дефолты → глобальный → проектный).
    """
    path = project_config_path(tasks_dir)
    stored = _read_json(path)
    stored.update(updates)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return load_project_config(tasks_dir)
