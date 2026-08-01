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
    # Технический раздел доски для строк, у которых не осталось файла задачи:
    # починка не удаляет чужие записи, а сносит их сюда. Колонкой не показывается
    "lost_section": "Потерянные",
    "dnd_full_board": True,
    # Жизненный цикл задачи: порядок статусов и цели действий скиллов.
    # Разбор и дефолты оформления — в backend/statuses.py
    "pipeline": ["backlog", "queued", "development", "review", "testing", "completed"],
    "actions": {"create": "backlog", "start": "development"},
    "theme": "dark",
    # Проверка обновлений. Единственное место, где инструмент ходит в сеть,
    # поэтому по умолчанию «ask» — пока пользователь не ответил, запросов нет
    "update_check": "ask",  # ask | auto | manual | off
    # Адрес манифеста релиза. Настройка, а не константа: манифест не обязан
    # лежать там же, где код, и хостинг может смениться
    "release_manifest_url":
        "https://raw.githubusercontent.com/3al/taskmark/main/release.json",
}

# Ключи, которые имеет смысл держать на уровне проекта: жизненный цикл у каждого
# проекта свой, а порт и тема — свойства инструмента, а не репозитория
PROJECT_KEYS = {"pipeline", "actions", "statuses", "board_file", "create_script",
                "status_script", "logs_dir", "queue_section", "queued_status",
                "lost_section", "dnd_full_board", "harnesses", "vault"}


def lost_section(cfg: dict) -> str:
    """Имя технического раздела доски для записей без файла задачи."""
    return cfg.get("lost_section") or DEFAULTS["lost_section"]


def is_lost_section(title: str, cfg: dict) -> bool:
    """Это тот самый технический раздел? (сравнение как у разделов доски)"""
    return title.strip().lower() == lost_section(cfg).strip().lower()


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


def stored_project_config(tasks_dir: Path) -> dict:
    """Только то, что реально записано в проекте, без дефолтов.

    Нужно, чтобы отличить «проект настроен по-своему» от «проект на дефолтах»:
    у второго копировать нечего.
    """
    stored = _read_json(legacy_config_path(tasks_dir))
    stored.update(_read_json(project_config_path(tasks_dir)))
    return stored


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


# Встроенные пресеты критериев приёмки для формы новой задачи: дефолт виден
# заранее, а не подставляется молча при создании
DEFAULT_CRITERIA_PRESETS = (
    "TDD: RED -> GREEN -> ALL TESTS PASS",
    "SMOKE TEST",
    "Ручная проверка",
)


def criteria_presets() -> list[str]:
    """Пресеты критериев: встроенные, затем сохранённые пользователем.

    Пользовательские лежат в глобальном конфиге (ключ criteria_presets) —
    пресет, добавленный в одном проекте, доступен во всех.
    """
    extra = load_global_config().get("criteria_presets") or []
    out: list[str] = []
    for preset in (*DEFAULT_CRITERIA_PRESETS, *extra):
        if preset not in out:
            out.append(preset)
    return out


def add_criteria_preset(text: str) -> list[str]:
    """Сохранить новый пресет в глобальный конфиг. Вернуть полный список."""
    text = text.strip()
    presets = criteria_presets()
    if not text or text in presets:
        return presets
    extra = load_global_config().get("criteria_presets") or []
    save_global_config({"criteria_presets": [*extra, text]})
    return criteria_presets()


def custom_criteria_presets() -> list[str]:
    """Только пользовательские пресеты — им одним положен крестик удаления."""
    return list(load_global_config().get("criteria_presets") or [])


def remove_criteria_preset(text: str) -> list[str]:
    """Удалить пользовательский пресет. Встроенные не трогаем: это поставка."""
    extra = [p for p in custom_criteria_presets() if p != text.strip()]
    save_global_config({"criteria_presets": extra})
    return criteria_presets()
