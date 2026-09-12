"""Сообщить доске, что Codex ждёт решения в системном диалоге доступа.

Codex вызывает этот обработчик на PermissionRequest. Он намеренно не передаёт
доске команду или параметры инструмента: в них могут находиться секреты.
Отказ уведомления не должен влиять на сам запрос разрешения.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


MESSAGE = "Codex ждёт разрешения: подтвердите или отклоните запрос в диалоге"


def _project_root(payload: dict) -> Path | None:
    start = Path(payload.get("cwd") or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "tasks" / "notify.py").is_file():
            return candidate
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if payload.get("hook_event_name") != "PermissionRequest":
        return 0

    root = _project_root(payload)
    if root is None:
        return 0
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        subprocess.run(
            [sys.executable, str(root / "tasks" / "notify.py"), MESSAGE,
             "--agent", "Codex", "--level", "warning"],
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
