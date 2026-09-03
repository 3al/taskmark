"""Откуда взять готовый жизненный цикл: пресеты и пайплайны соседних проектов.

Настройки остаются в самом проекте (`tasks/.taskboard.json`): скрипты
`tasks/*.py` автономны и читают конфиг рядом с собой, поэтому маршрут должен
ездить вместе с проектом, а не жить в домашней папке инструмента. Повторный
ручной ввод лечится не переездом хранилища, а копированием — реестр и так
знает все проекты, значит может предложить их пайплайны как источник.
"""

from __future__ import annotations

from pathlib import Path

from backend import registry
from backend.config import load_project_config, stored_project_config
from backend.statuses import PRESETS, load_pipeline

# Ключи конфига, из которых состоит жизненный цикл. Проект, не переопределивший
# ни одного, живёт на дефолтах — предлагать его как источник нечего
LIFECYCLE_KEYS = ("pipeline", "actions", "statuses", "requires",
                  "notify_statuses")


def _source(kind: str, name: str, hint: str, cfg: dict) -> dict:
    """Источник в том виде, в каком его применяет редактор.

    Статусы отдаём разобранными (с подписями и разделами), а `statuses` —
    сырыми переопределениями: без них скопированный маршрут разошёлся бы с
    исходным по названиям колонок доски.

    `requires` и `notify_statuses` — тоже часть жизненного цикла: копируя маршрут
    соседнего проекта, человек ждёт и его проверок, и его уведомлений. Пресеты
    ни того, ни другого не несут — они дают статусы, а требования приходят
    рекомендациями каталога при добавлении статуса.
    """
    pipeline = load_pipeline(cfg)
    return {
        "kind": kind,
        "name": name,
        "hint": hint,
        "pipeline": pipeline.statuses(),
        "actions": pipeline.actions(),
        "statuses": cfg.get("statuses") or {},
        "requires": cfg.get("requires") or {},
        # Набор статусов, о которых уведомляют: копируя маршрут соседа, человек
        # ждёт и его уведомлений. Пустой у пресета значит «нигде» и вытесняет
        # прежний — маршрут заменяется целиком, как и с требованиями
        "notify_statuses": list(cfg.get("notify_statuses") or []),
    }


def list_sources(active_tasks_dir: Path | None = None) -> list[dict]:
    """Готовые жизненные циклы: сначала пресеты, затем проекты реестра.

    Активный проект себя не предлагает, проекты на дефолтах — тоже.
    """
    sources = [_source("preset", p["name"], p["hint"],
                       {"pipeline": p["pipeline"], "actions": p["actions"]})
               for p in PRESETS]

    active = Path(active_tasks_dir).resolve() if active_tasks_dir else None
    for project in registry.list_projects().get("projects", []):
        tasks_dir = Path(project["tasks_dir"])
        try:
            if active and tasks_dir.resolve() == active:
                continue
            if not tasks_dir.is_dir():
                continue  # проект удалён с диска, но остался в реестре
            stored = stored_project_config(tasks_dir)
            if not any(key in stored for key in LIFECYCLE_KEYS):
                continue
            sources.append(_source("project", project["name"], str(tasks_dir.parent),
                                   load_project_config(tasks_dir)))
        except OSError:
            continue  # недоступный путь (отключённый диск, сетевая шара)
    return sources
