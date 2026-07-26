"""Запуск tasks/create_task.py для создания задачи из UI."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def create_task(tasks_dir: Path, cfg: dict, payload: dict) -> dict:
    """
    Вызвать create_task.py в не-интерактивном режиме.

    payload: title (обяз.), description, criteria, blocked_by, task_type, section.
    """
    script = tasks_dir / cfg.get("create_script", "create_task.py")
    if not script.is_file():
        return {"ok": False, "error": f"Скрипт не найден: {script}"}

    title = (payload.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "Название задачи обязательно"}

    args = [sys.executable, str(script), "-t", title]
    if payload.get("description"):
        args += ["-d", payload["description"]]
    if payload.get("criteria"):
        args += ["-c", payload["criteria"]]
    if payload.get("blocked_by"):
        args += ["-b", payload["blocked_by"]]
    if payload.get("task_type"):
        args += ["--type", payload["task_type"]]
    if payload.get("section"):
        args += ["--section", payload["section"]]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            cwd=str(tasks_dir.parent), timeout=30,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()}

    # Извлечь id созданной задачи из вывода ("ID: TASK-NNN")
    m = re.search(r"ID:\s*(TASK-\d+)", result.stdout)
    return {"ok": True, "id": m.group(1) if m else None, "output": result.stdout}
