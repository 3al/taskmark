"""Сообщить доске, что Codex ждёт человека: разрешения или ответа на вопрос.

Codex вызывает этот обработчик на PermissionRequest и на PreToolUse инструмента
вопроса: события «задан вопрос» у него нет, а разрешения вопрос не просит.
Обработчик намеренно не передаёт доске команду, параметры инструмента и текст
вопроса: в них могут находиться секреты. Отказ уведомления не должен влиять
ни на запрос разрешения, ни на вопрос.

**Отпавший повод убирают с доски.** Реплика человека (`UserPromptSubmit`),
конец хода агента (`Stop`) и ответ на вопрос (`PostToolUse` инструмента
вопроса) означают, что звать больше некого; в эти моменты обработчик просит
доску снять сказанное своей сессией. Отклонённое разрешение своего события не
даёт вовсе — его карточку снимает конец хода.

**Конец хода снимает только зовы среды**, а не сообщения самого агента: «работа
готова» он посылает прямо перед концом хода, и снимать её там значит не
показать вовсе.
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

# События, после которых ждать больше некого: человек ответил в терминале либо
# агент кончил ход. Ответ на сам вопрос приходит закрытием его вызова
DISMISS_EVENTS = {"UserPromptSubmit", "Stop"}
# Реплика человека снимает всё сказанное сессией, конец хода и ответ на
# вопрос — только зовы среды
DISMISS_SCOPE = {"UserPromptSubmit": "all"}


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


def _is_dismissal(payload: dict) -> bool:
    """Момент, когда сказанное доске больше не ждёт человека."""
    event = payload.get("hook_event_name")
    if event in DISMISS_EVENTS:
        return True
    return event == "PostToolUse" and payload.get("tool_name") in ASK_TOOLS


def _run(root: Path, args: list[str]) -> None:
    """Позвать скрипт доски. Его отказ на работу среды не влияет."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        subprocess.run(
            [sys.executable, str(root / "tasks" / "notify.py"), *args],
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


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    root = _project_root(payload)
    if root is None:
        return 0
    if _is_dismissal(payload):
        scope = DISMISS_SCOPE.get(str(payload.get("hook_event_name")), "env")
        _run(root, ["--dismiss", scope])
        return 0
    moment = _moment(payload)
    if moment is None:
        return 0
    message, level = moment
    _run(root, [message, "--agent", "Codex", "--level", level,
                "--scope", "env"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
