"""Валидация структуры папки tasks/ проекта."""

from __future__ import annotations

import re
from pathlib import Path

from backend.board_parser import parse_board
from backend.scaffold import TASKS_TEMPLATES, agentic_stale

_TASK_ID_RE = re.compile(r"^(TASK-\d+)")


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
    queue_section = cfg.get("queue_section", "Queue")

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

    try:
        board = parse_board(
            board_path,
            queue_section=cfg.get("queue_section", "Queue"),
            queued_status=cfg.get("queued_status", "queued"),
        )
    except Exception as exc:
        critical.append(f"Не удалось распарсить {board_file}: {exc}")
        return _report(critical, degraded, warnings, features)

    known = {t.lower() for t in board["known_sections"]}
    if "backlog" not in known:
        critical.append(f"В {board_file} нет раздела Backlog")

    features["queue_section"] = queue_section.lower() in known

    # Деградация функционала (code — для точечного восстановления из UI)
    features["create_task"] = (tasks_dir / create_script).is_file()
    if not features["create_task"]:
        degraded.append({"code": "no_create_script",
                         "message": f"Нет {create_script} — создание задач отключено"})
    elif _script_outdated(tasks_dir / create_script):
        degraded.append({"code": "outdated_script",
                         "message": f"{create_script} устарел — не поддерживает актуальные возможности"})

    features["logs"] = (tasks_dir / logs_dir).is_dir()
    if not features["logs"]:
        degraded.append({"code": "no_logs",
                         "message": f"Нет папки {logs_dir}/ — просмотр логов отключён"})

    # Развёрнутое агентское окружение — тоже инструмент: следим за актуальностью
    stale = agentic_stale(tasks_dir.parent)
    if stale["skills"]:
        degraded.append({"code": "outdated_skills",
                         "message": "Скиллы устарели: " + ", ".join(stale["skills"])})
    if stale["commands"]:
        degraded.append({"code": "outdated_commands",
                         "message": "Команды opencode устарели: " + ", ".join(stale["commands"])})

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

    return _report(critical, degraded, warnings, features)


def _script_outdated(script_path: Path) -> bool:
    """Скрипт создания задач отличается от шаблонной версии."""
    try:
        current = script_path.read_text(encoding="utf-8-sig")
        template = (TASKS_TEMPLATES / "create_task.py").read_text(encoding="utf-8")
        return current != template
    except Exception:
        return False


def _report(critical: list, degraded: list, warnings: list, features: dict) -> dict:
    return {
        "ok": not critical,
        "structure": "present",
        "critical": critical,
        "degraded": degraded,
        "warnings": warnings,
        "features": features,
    }
