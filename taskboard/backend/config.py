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
    # Прятать колонки, в которых нет задач. Свойство глаз, а не репозитория:
    # человеку тесно на широком пайплайне, и решает это он, а не проект.
    # По умолчанию выключено — доска показывает маршрут целиком, иначе колонка
    # пропадает молча, и её ищут глазами
    "hide_empty_columns": False,
    # Удаление задачи крестиком: необратимая операция над файлами пользователя,
    # поэтому выключена по умолчанию и включается осознанно (TASK-043)
    "delete_tasks": False,
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
    # Метка типа на превью. По умолчанию включена: выключенная по умолчанию
    # возможность остаётся незамеченной. Флаг, а не число, поэтому границ у
    # него нет — но живёт он там же, рядом с видом карточки
    "card_show_type": True,
    # Полоска прогресса чеклиста в нижней строке превью. Включена по той же
    # причине, что метка типа; выключается там же, когда на превью тесно.
    # Полоска есть только у задач с планом — их на доске меньшинство
    "card_show_progress": True,
    # Порог залежалости: со скольких дней в статусе превью показывает возраст.
    # Неделя без движения уже заметна, а более короткий порог засветил бы
    # возрастом половину доски — строка перестала бы значить «посмотри сюда»
    "card_stale_days": 7,
    # Внешние источники ревью (merge request во внешнем форже через
    # MCP-инструменты окружения). Выключено по умолчанию: наличие инструмента
    # форжа агент увидеть может, а вот хочет ли человек, чтобы он ходил в
    # рабочий GitLab, — нет. Выключенная возможность из скилла вырезается
    # целиком (реестр OPTIONAL_BLOCKS в backend/scaffold.py)
    "review_sources": False,
}

# Границы вида карточки: за ними превью разваливается — заголовок перестаёт
# читаться или карточка занимает пол-экрана. Свобода настройки не должна
# доходить до возможности сломать доску, поэтому диапазоны — часть контракта,
# и их проверяет бэкенд, а не только форма
CARD_LIMITS: dict[str, tuple[int, int]] = {
    "card_title_size": (12, 18),
    "card_title_lines": (1, 4),
    "card_meta_size": (10, 14),
    # Порог залежалости — не размер, но живёт по тем же правилам: целое число,
    # заданное человеком, за границами которого нижняя строка теряет смысл
    # (нулевой порог светит всей доске, годовой — никогда)
    "card_stale_days": (1, 365),
}


# Типы задач — константа поставки, а не настройка проекта. Тип отвечает на
# вопрос «что это за работа», и ответ одинаков везде: баг остаётся багом при
# любом жизненном цикле. Настраиваемый список пришлось бы тянуть в чеклисты,
# в тексты скиллов и в будущие требования этапа — то есть повторить историю
# переименуемых имён артефактов (TASK-053).
#
# letter — буква кружка на превью: места там на один знак, поэтому буквы
# должны различаться (проверяется тестом). color — имя палитры фронта: цвета
# тоже обязаны быть разными и **различимыми** — дизайн начинал с cyan и сливался
# с sky у нового функционала, поэтому ушёл в фуксию.
# section — заголовок рубрики бэклога: тот же тип, только во множественном
# числе («Баги», а не «Баг»). Рубрики выводятся отсюда, второго списка нет:
# он уже разъезжался — `discussion` не имел рубрики вовсе (TASK-119).
# commits: False — у работы этого типа коммитов не бывает, и напоминание о
# пустой «Истории коммитов» к ней не относится. Хранится **исключение**, а не
# белый список: новый тип поставки коммиты даёт и молча выпасть не может.
# skip_statuses — статусы библиотеки, которые этому виду работы не нужны:
# у обсуждения и код-ревью нет релизного хвоста, выпускать по ним нечего.
# Список меняет только **рекомендацию** следующего шага (`set_status.py
# --targets`), а не состав достижимых статусов: маршрут остаётся маршрутом,
# а не забором. Своих статусов проекта здесь быть не может — тип константа
# поставки и про них не знает; такой статус просто не пропускается.
RELEASE_TAIL = ("ready_for_release", "release_notes", "to_release", "ready_to_deploy")

TASK_TYPES: dict[str, dict] = {
    "feature":    {"label": "Новый функционал", "section": "Новый функционал",
                   "letter": "Н", "color": "sky"},
    "bug":        {"label": "Баг",              "section": "Баги",
                   "letter": "Б", "color": "rose"},
    "refactor":   {"label": "Рефакторинг",      "section": "Рефакторинг",
                   "letter": "Р", "color": "violet"},
    "cleanup":    {"label": "Уборка",           "section": "Уборка",
                   "letter": "У", "color": "emerald"},
    "discussion": {"label": "Обсуждение",       "section": "Обсуждения",
                   "letter": "О", "color": "amber", "commits": False,
                   "skip_statuses": RELEASE_TAIL},
    "design":     {"label": "Дизайн",           "section": "Дизайн",
                   "letter": "Д", "color": "fuchsia"},
    "review":     {"label": "Код-ревью",        "section": "Код-ревью",
                   "letter": "К", "color": "lime", "commits": False,
                   "skip_statuses": RELEASE_TAIL},
}

DEFAULT_TASK_TYPE = "feature"


# Размер задачи — оценка объёма работы, а не её вид: тип отвечает «что это за
# работа», размер — «браться ли за неё сейчас». Список закрытый и, как у типа,
# **константа поставки**: S остаётся S при любом жизненном цикле.
#
# Ключи прописные (`size: L`): это аббревиатуры, и строчная `s` в файле задачи
# читается как опечатка. Разбор регистронезависим — файл правят руками.
# Порядок словаря — порядок возрастания: по нему рисуются чипы отбора.
#
# hint — чем размер отличается от соседнего. Одной буквы мало: «L» не говорит
# ничего, пока не сказано, что это работа на несколько сессий.
#
# Задача без размера — норма, а не ошибка: оценка появляется, когда её есть
# на чём построить (агент ставит её, взяв задачу в работу), и снимается тем же
# способом, каким ставится.
TASK_SIZES: dict[str, dict] = {
    "S":  {"label": "S",  "hint": "мелкая правка, один заход"},
    "M":  {"label": "M",  "hint": "обычная задача на сессию"},
    "L":  {"label": "L",  "hint": "несколько сессий, лучше с планом"},
    "XL": {"label": "XL", "hint": "стоит разбить на задачи"},
}


# Вид превью: числа с границами (CARD_LIMITS) плюс переключатели
CARD_FLAGS = ("card_show_type", "card_show_progress")


def card_style(cfg: dict) -> dict:
    """Значения вида карточки — с подстановкой дефолтов вместо пропусков."""
    keys = (*CARD_LIMITS, *CARD_FLAGS)
    out = {k: cfg.get(k, DEFAULTS[k]) for k in keys}
    for flag in CARD_FLAGS:
        out[flag] = bool(out[flag])
    return out


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
PROJECT_KEYS = {"pipeline", "actions", "statuses", "requires", "release_script",
                "dnd_full_board", "harnesses", "vault", "delete_tasks",
                # Ходить ли агенту во внешний форж — свойство репозитория и
                # договорённостей вокруг него, а не инструмента
                "review_sources",
                # Порог залежалости — свойство репозитория, а не глаз: неделя
                # без движения в одном проекте норма, а в другом уже беда.
                # Остальные `card_*` глобальны — это осознанное расхождение
                "card_stale_days",
                # Типы задач, про которые проекту уже говорили: требования
                # настраивает человек, и спрашивать второй раз незачем
                "known_task_types"}


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
