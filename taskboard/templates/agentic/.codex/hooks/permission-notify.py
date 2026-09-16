"""Сообщить доске, что Codex ждёт человека: разрешения или ответа на вопрос.

Codex вызывает этот обработчик на PermissionRequest и на PreToolUse инструмента
вопроса: события «задан вопрос» у него нет, а разрешения вопрос не просит.
Обработчик намеренно не передаёт доске команду, параметры инструмента и текст
вопроса: в них могут находиться секреты. Отказ уведомления не должен влиять
ни на запрос разрешения, ни на вопрос.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


# Событие → что сказать и каким тоном. Разрешение останавливает работу
# (`warning`), вопрос — ждёт ответа, чтобы продолжать (`info`)
PERMISSION = ("Codex ждёт разрешения: подтвердите или отклоните запрос "
              "в диалоге", "warning")
QUESTION = ("Codex ждёт вашего ответа: задан вопрос", "info")

# Инструмент, которым Codex задаёт вопрос человеку
ASK_TOOLS = {"request_user_input"}


def _project_root(payload: dict) -> Path | None:
    start = Path(payload.get("cwd") or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "tasks" / "notify.py").is_file():
            return candidate
    return None


def _moment(payload: dict) -> tuple[str, str] | None:
    event = payload.get("hook_event_name")
    if event == "PermissionRequest":
        return PERMISSION
    if event == "PreToolUse" and payload.get("tool_name") in ASK_TOOLS:
        return QUESTION
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    moment = _moment(payload)
    if moment is None:
        return 0
    message, level = moment

    root = _project_root(payload)
    if root is None:
        return 0
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        subprocess.run(
            [sys.executable, str(root / "tasks" / "notify.py"), message,
             "--agent", "Codex", "--level", level],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
