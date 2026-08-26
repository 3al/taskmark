#!/usr/bin/env python3
"""Хук Claude Code: сказать агенту, что коммит — уже уход из работы.

Скрипт задач видит только собственные вызовы, а коммит, push и запрос на
слияние проходят мимо него: работа уезжает наружу, пока задача числится в
разработке, и передача не случается. Хук видит сам вызов инструмента.

**Решение принимает не он.** Есть ли задача в работе и что сказать — отвечает
`tasks/set_status.py --work-hint`; здесь только повод спросить и формат ответа,
которого ждёт среда. Иначе одно правило пришлось бы писать дважды — на JSON и
на JS соседней среды.

**Подсказка, а не запрет.** Коммит в середине работы законен, и блокировка
учила бы её обходить. Хук всегда завершается успешно и решения о запрете не
возвращает.

Событие — `PostToolUse`: несостоявшийся вызов обсуждать нечего.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Среда читает ответ как UTF-8, а Windows по умолчанию пишет в кодировке
# консоли: русская подсказка доехала бы кракозябрами или сломала бы разбор JSON
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Что считается уходом работы наружу. Запрос на слияние создают по-разному —
# ловим сам факт отправки, а не имя чужого инструмента
OUTBOUND = re.compile(r"\bgit\s+(commit|push)\b")


def project_root(event: dict) -> Path:
    """Корень проекта: среда передаёт рабочую папку сессии."""
    return Path(event.get("cwd") or ".").resolve()


def ask_script(root: Path) -> dict:
    """Спросить срез у скрипта задач. Молчим обо всём, что пошло не так."""
    script = root / "tasks" / "set_status.py"
    if not script.is_file():
        return {}
    # Тот же уговор в обратную сторону: просим скрипт печатать UTF-8, иначе на
    # Windows он ответит в кодировке консоли, а мы прочитаем не то
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        done = subprocess.run([sys.executable, str(script), "--work-hint"],
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=20, env=env)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {}
    if done.returncode != 0 or not (done.stdout or "").strip():
        return {}
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return {}


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    command = str((event.get("tool_input") or {}).get("command", ""))
    if not OUTBOUND.search(command):
        return

    hint = ask_script(project_root(event)).get("hint", "")
    if not hint:
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name", "PostToolUse"),
            "additionalContext": hint,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
