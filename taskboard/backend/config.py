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
    # Скрипт выпуска версии. Пуст по умолчанию: у каждого проекта «выпустить»
    # значит своё, и универсального механизма тут быть не может. Задан — скилл
    # выпуска зовёт его; не задан — доводит подготовку и останавливается
    "release_script": "",
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
    # Вид карточки на доске. Числами, а не пресетами: «чтобы больше влезало» —
    # это про конкретную высоту колонки и длину заголовков, и у каждого она своя
    "card_title_size": 14,
    "card_title_lines": 3,
    "card_meta_size": 12,
}

# Границы вида карточки: за ними превью разваливается — заголовок перестаёт
# читаться или карточка занимает пол-экрана. Свобода настройки не должна
# доходить до возможности сломать доску, поэтому диапазоны — часть контракта,
# и их проверяет бэкенд, а не только форма
CARD_LIMITS: dict[str, tuple[int, int]] = {
    "card_title_size": (12, 18),
    "card_title_lines": (1, 4),
    "card_meta_size": (10, 14),
}


def card_style(cfg: dict) -> dict:
    """Значения вида карточки — с подстановкой дефолтов вместо пропусков."""
    return {k: cfg.get(k, DEFAULTS[k]) for k in CARD_LIMITS}


def validate_card_style(updates: dict) -> tuple[dict, list[str]]:
    """Привести размеры карточки к целым и проверить границы.

    Возвращает (updates с числами вместо строк, список ошибок). Форма шлёт
    значения полей строками, а «14» и 14 — одно и то же число; а вот 40 или
    «много» — уже нет, и такое сохранять нельзя.
    """
    out = dict(updates)
    errors: list[str] = []
    for key, (low, high) in CARD_LIMITS.items():
        if key not in out:
            continue
        value = out[key]
        if isinstance(value, bool) or not isinstance(value, (int, str, float)):
            errors.append(f"{key}: нужно целое число от {low} до {high}")
            continue
        try:
            number = int(str(value).strip())
        except ValueError:
            errors.append(f"{key}: нужно целое число от {low} до {high}")
            continue
        if not low <= number <= high:
            errors.append(f"{key}: допустимо от {low} до {high}, получено {number}")
            continue
        out[key] = number
    return out, errors

# Ключи, которые имеет смысл держать на уровне проекта: жизненный цикл у каждого
# проекта свой, а порт и тема — свойства инструмента, а не репозитория
# Имена системных артефактов сюда не входят: они перестали быть настройкой
# (TASK-053). Переименование шло по данным, но не по текстам скиллов и правил,
# где имена зашиты, — переименовавший получал скиллы, зовущие несуществующий
# файл. `release_script` остаётся: это не переименование, а точка расширения
PROJECT_KEYS = {"pipeline", "actions", "statuses", "release_script",
                "dnd_full_board", "harnesses", "vault"}


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


def only_changed(stored: dict) -> dict:
    """Убрать из сохранённого то, что совпадает с дефолтом поставки.

    Отличить «пользователь выбрал значение, совпавшее с дефолтом» от «значение
    записалось само» невозможно — и не нужно: поведение при этом не меняется,
    зато ключ снова начинает следовать за поставкой.
    """
    return {k: v for k, v in stored.items()
            if k not in DEFAULTS or v != DEFAULTS[k]}


def stored_global_config() -> dict:
    """Только то, что реально записано в файле, без дефолтов."""
    return _read_json(GLOBAL_CONFIG_FILE)


def write_stored_global(stored: dict) -> None:
    """Записать в файл именно то, что передали, — без дефолтов."""
    try:
        GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        GLOBAL_CONFIG_FILE.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def load_global_config() -> dict:
    """Дефолты поставки + то, что пользователь менял.

    Файл при первом запуске создаётся **пустым**, а не слепком `DEFAULTS`:
    записанное значение всегда побеждает дефолт, поэтому полный слепок
    замораживал конфиг в том виде, в каком поставка выглядела в день первого
    запуска, и правки дефолтов не доезжали ни до кого (TASK-088).
    """
    if not GLOBAL_CONFIG_FILE.exists():
        write_stored_global({})
        return dict(DEFAULTS)
    cfg = dict(DEFAULTS)
    cfg.update(stored_global_config())
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
    """Слить updates в глобальный конфиг и сохранить. Возвращает итоговый конфиг.

    В файл идёт **только изменённое пользователем**: писать эффективный конфиг
    целиком значит замораживать дефолты остальных ключей (TASK-088).
    """
    stored = stored_global_config()
    stored.update(updates)
    write_stored_global(only_changed(stored))
    return load_global_config()


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
