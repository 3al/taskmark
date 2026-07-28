"""Валидация структуры папки tasks/ проекта."""

from __future__ import annotations

import re
from pathlib import Path

from backend.board_parser import parse_board
from backend.scaffold import detect_harnesses, environment_issues, harness_choice
from backend.statuses import load_pipeline

_TASK_ID_RE = re.compile(r"^(TASK-\d+)")

# Как назвать часть окружения в сообщении о пробеле поставки:
# (нет целиком, не хватает элементов, устарело).
# Формулировки одинаковой формы для всех частей — чтобы отсутствие скиллов
# читалось так же серьёзно, как отсутствие скрипта, а не терялось в молчании.
# «Нет» и «устарело» не смешиваются: назвать несуществующий файл устаревшим
# значит отправить пользователя искать, что же в нём поменялось
_PART_WORDING = {
    "create_script": ("Нет {names} — создание задач отключено", "",
                      "{names} устарел — не поддерживает актуальные возможности"),
    "status_script": ("Нет {names} — инструмент агента для управления задачами", "",
                      "{names} устарел — не поддерживает актуальные возможности"),
    "template": ("Нет {names} — эталона структуры задачи, на него ссылаются скиллы", "",
                 "{names} устарел — новые задачи заводятся по старой структуре"),
    "epics": ("Нет {names} — реестру эпиков негде хранить имена", "", ""),
    "logs": ("Нет папки {names} — просмотр логов отключён", "", ""),
    "skills": ("Скиллы не развёрнуты — агент не знает процесса работы с задачами",
               "Не хватает скиллов: {names}",
               "Скиллы устарели: {names}"),
    "commands": ("Команды opencode не развёрнуты: без них среда не видит скиллы",
                 "Не хватает команд opencode: {names}",
                 "Команды opencode устарели: {names}"),
    "rules": ("Правила для агентов не развёрнуты: {names}", "",
              "Правила для агентов устарели: {names}"),
    "vault": ("Волт знаний не развёрнут — скиллы ссылаются на несуществующий vault/",
              "Волт развёрнут не полностью: {names}",
              "Правила и шаблоны волта устарели: {names}"),
}

_STATE_INDEX = {"missing": 0, "partial": 1, "outdated": 2}


def _issue_message(issue: dict) -> str:
    """Сообщение о пробеле поставки по реестру частей."""
    wording = _PART_WORDING[issue["part"]]
    template = wording[_STATE_INDEX[issue["state"]]]
    return template.format(names=", ".join(issue["names"]))


def validate_project(tasks_dir: Path, cfg: dict) -> dict:
    """
    Проверить структуру tasks/.

    Возвращает отчёт:
      ok           — можно работать полноценно
      critical     — список критичных проблем (read-only режим)
      degraded     — деградация функционала (off кнопки/вкладки)
      warnings     — мягкие предупреждения (панель "Проблемы данных")
      features     — доступные возможности: create_task, logs, queue_section
    """
    critical: list[str] = []
    degraded: list[dict] = []
    warnings: list[str] = []
    features = {"create_task": False, "logs": False, "queue_section": False}

    board_file = cfg.get("board_file", "board.md")
    create_script = cfg.get("create_script", "create_task.py")
    logs_dir = cfg.get("logs_dir", "logs")

    # Критичные проверки
    if not tasks_dir.is_dir():
        critical.append(f"Папка задач не найдена: {tasks_dir}")
        report = _report(critical, degraded, warnings, features)
        # Отдельный маркер: структуры нет совсем — UI предложит scaffold
        report["structure"] = "missing"
        return report

    board_path = tasks_dir / board_file
    if not board_path.is_file():
        critical.append(f"Файл доски не найден: {board_path}")
        report = _report(critical, degraded, warnings, features)
        # Папка есть, доски нет — UI предложит scaffold (досоздаст недостающее)
        report["structure"] = "no_board"
        return report

    pipeline = load_pipeline(cfg)
    try:
        board = parse_board(board_path, pipeline)
    except Exception as exc:
        critical.append(f"Не удалось распарсить {board_file}: {exc}")
        return _report(critical, degraded, warnings, features)

    known = {t.lower() for t in board["known_sections"]}

    # Раздел создания задач критичен: без него новую задачу некуда положить
    create_status = pipeline.action("create")
    create_section = pipeline.section_of(create_status) if create_status else None
    if not create_section:
        critical.append("Пайплайн статусов пуст: нечего показывать на доске")
    elif create_section.lower() not in known:
        critical.append(f"В {board_file} нет раздела {create_section}")

    # Раздел, откуда берут работу (живая очередь)
    pick_section = pipeline.section_of(pipeline.action("pick") or "")
    features["queue_section"] = bool(pick_section) and pick_section.lower() in known

    # Разделы пайплайна, которых на доске нет: колонки не будет, пока не создадим
    missing = [s["section"] for s in pipeline.statuses()
               if s["section"].lower() not in known]
    if missing:
        warnings.append("Нет разделов доски для статусов пайплайна: " + ", ".join(missing))

    # Разделы вне пайплайна не трогаем молча — показываем как есть и предупреждаем
    extra = [t for t in board["known_sections"]
             if pipeline.status_for_section(t) is None]
    if extra:
        warnings.append("Разделы вне пайплайна статусов: " + ", ".join(extra))

    # Деградация функционала (code — для точечного восстановления из UI).
    # Возможности зависят от наличия инструментов, сами проблемы полноты
    # поставки перечисляет реестр частей окружения
    features["create_task"] = (tasks_dir / create_script).is_file()
    features["set_status"] = (tasks_dir / cfg.get("status_script", "set_status.py")).is_file()
    features["logs"] = (tasks_dir / logs_dir).is_dir()

    # Пока среды не выбраны, про их части молчим: спрашиваем выбор один раз
    if harness_choice(cfg) is None:
        degraded.append({"code": "no_harness_choice",
                         "message": "Не выбраны среды агентов — окружение проверяется не полностью"})

    for issue in environment_issues(tasks_dir, cfg):
        degraded.append({"code": issue["code"],
                         "message": _issue_message(issue),
                         "names": issue["names"]})

    # Мягкие предупреждения: битые ссылки и файлы вне доски
    on_board: set[str] = set()
    for column in board["columns"]:
        for group in column["groups"]:
            for task in group["tasks"]:
                on_board.add(task["id"])
                if not (tasks_dir / task["file"]).is_file():
                    warnings.append(f"{task['id']}: файл {task['file']} не найден")

    for f in sorted(tasks_dir.glob("TASK-*.md")):
        m = _TASK_ID_RE.match(f.name)
        if m and m.group(1) not in on_board:
            warnings.append(f"{f.name}: файла нет на доске")

    report = _report(critical, degraded, warnings, features)
    # Выбор сред и предзаполнение диалога: по нему UI решает, спрашивать ли
    report["harnesses"] = {"choice": harness_choice(cfg),
                           "detected": detect_harnesses(tasks_dir.parent)}
    return report


def _report(critical: list, degraded: list, warnings: list, features: dict) -> dict:
    return {
        "ok": not critical,
        "structure": "present",
        "critical": critical,
        "degraded": degraded,
        "warnings": warnings,
        "features": features,
    }
