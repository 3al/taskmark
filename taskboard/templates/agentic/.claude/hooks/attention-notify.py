#!/usr/bin/env python3
"""Хук Claude Code: сказать доске, что ход перешёл к человеку.

Агент виден только в своём терминале, а человек, отойдя от экрана, держит
открытой доску. Про свои шаги агент рассказывает сам (`tasks/notify.py` по
правилам проекта), но два момента он назвать не может: они возникают глубже
его хода — среда показывает диалог разрешения или просто ждёт ввода, и
никакого «шага агента» в этот момент нет.

**Поводов два, и различает их имя инструмента.** Диалог доступа и вопрос с
вариантами приходят одним событием — синхронным `PermissionRequest`: среда
спрашивает решение и там, и там. Отличается только `tool_name`: у вопроса это
инструмент из `ASK_TOOLS`, у всего остального — действие, которое ждёт
разрешения.

`Notification` остаётся третьей стороне того же — ожиданию ввода
(`MOMENTS`). Вид `permission_prompt` в нём не слушают: он приезжает вторым
концом уже сказанного и отличить вопрос от разрешения не даёт вовсе — тот же
текст, никакого имени инструмента.

**Ни текст среды, ни команда инструмента не пересылаются.** В них может
оказаться секрет; доске достаточно знать, что человека ждут.

Решение за человека обработчик не принимает: пустой вывод означает «иди
обычным путём», и диалог доступа остаётся ровно таким, каким был бы без хука.
Отказ уведомления на работу среды тоже не влияет — обработчик всегда
завершается успешно.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Что сказать человеку, когда работа встала до его решения в диалоге доступа.
# Тон — `warning`: это не «нужен ответ, чтобы продолжать», а «дальше не идём»
PERMISSION = ("Claude ждёт разрешения: подтвердите или отклоните запрос "
              "в диалоге", "warning")

# Инструменты, которыми агент задаёт вопрос, а не просит разрешить действие.
# Событие у них общее с диалогом доступа, поэтому имя — единственное, чем
# «выберите вариант» отличается от «разрешите команду»
ASK_TOOLS = {"AskUserQuestion"}
QUESTION = ("Claude ждёт вашего ответа: задан вопрос", "info")

# Вид момента `Notification` → что сказать и каким тоном. Здесь только ожидание
# ответа: `permission_prompt` в таблицу не входит намеренно — он неотличим от
# вопроса и приезжает вторым концом уже сказанного разрешения
MOMENTS = {
    "idle_prompt": ("Claude ждёт вашего ответа", "info"),
    "agent_needs_input": ("Claude ждёт вашего ответа: субагенту нужен ввод",
                          "info"),
    "elicitation_dialog": ("Claude ждёт вашего ответа в диалоге", "info"),
    "elicitation_url_dialog": ("Claude ждёт вашего ответа в диалоге", "info"),
}

# Кто зовёт. Модель хуку неизвестна — среда её не передаёт, а выдуманная в
# всплывашке врала бы о том, кто работает
AGENT = "Claude Code"

# Сколько ждём скрипт доски. Он и сам не ждёт дольше секунды, но процесс
# запускается на Windows не мгновенно
TIMEOUT = 3


def project_root(payload: dict) -> Path | None:
    """Корень проекта — ближайший вверх, где лежит скрипт уведомлений."""
    start = Path(payload.get("cwd") or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "tasks" / "notify.py").is_file():
            return candidate
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0
    event = payload.get("hook_event_name")
    if event == "PermissionRequest":
        moment = (QUESTION if payload.get("tool_name") in ASK_TOOLS
                  else PERMISSION)
    elif event == "Notification":
        moment = MOMENTS.get(str(payload.get("notification_type") or ""))
    else:
        moment = None
    if moment is None:
        return 0
    text, level = moment

    root = project_root(payload)
    if root is None:
        return 0
    # Скрипт печатает по-русски, и на Windows без этого он ответил бы в
    # кодировке консоли — а вывод мы всё равно гасим, но падать на нём незачем
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        subprocess.run(
            [sys.executable, str(root / "tasks" / "notify.py"), text,
             "--agent", AGENT, "--level", level],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
