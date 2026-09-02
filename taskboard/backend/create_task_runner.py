"""Запуск tasks/create_task.py для создания задачи из UI."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from backend.config import add_author
from backend.proc import no_window_flags


def create_task(tasks_dir: Path, cfg: dict, payload: dict) -> dict:
    """
    Вызвать create_task.py в не-интерактивном режиме.

    payload: title (обяз.), description, criteria, blocked_by, task_type,
    epic (ключ эпика), author (кто принёс задачу). Рубрику бэклога скрипт
    выводит из типа задачи.
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
    elif "criteria" in payload:
        # Ключ передан пустым — это «критериев нет», а не «подставь дефолт».
        # Скрипт без -c пишет TDD-критерий сам, и задача, заведённая из чата
        # одной строкой, начинала утверждать то, чего никто не говорил
        args += ["-c", ""]
    if payload.get("blocked_by"):
        args += ["-b", payload["blocked_by"]]
    # Рубрику бэклога скрипт выводит из типа задачи, поэтому форма её не
    # передаёт. Явный раздел нужен источникам, у которых типа нет: задача из
    # чата ложится в свою рубрику, и она же служит сигналом «пришло, разбери»
    if payload.get("section"):
        args += ["--section", payload["section"]]
    # Автор задачи — тот, кто её принёс. Ключ передают все три пути
    # заведения, и пустым он приходит только у задач, заведённых до появления
    # поля: подставлять что-то за вызывающего здесь нечем
    if payload.get("author"):
        args += ["--author", payload["author"]]
    if payload.get("task_type"):
        args += ["--type", payload["task_type"]]
    elif "task_type" in payload:
        # Как и с критериями: пустой ключ значит «типа нет», а не «поставь
        # feature». Задача из чата — одна строка от человека, и вид работы
        # в ней никто не называл; тип поставит тот, кто возьмёт её в работу
        args += ["--type", ""]
    if payload.get("epic"):
        args += ["-e", payload["epic"]]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            cwd=str(tasks_dir.parent), timeout=30,
            creationflags=no_window_flags(),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()}

    # Имя автора запоминается **после** записи в файл, а не до: список
    # подсказок не должен пополняться тем, что до задачи не доехало. Порядок
    # тот же, что у исполнителя и у реестра эпиков
    if payload.get("author"):
        add_author(payload["author"])

    # Извлечь id созданной задачи из вывода ("ID: TASK-NNN")
    m = re.search(r"ID:\s*(TASK-\d+)", result.stdout)
    return {"ok": True, "id": m.group(1) if m else None, "output": result.stdout}
