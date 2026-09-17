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

**Простой зовёт не сразу и один раз на ожидание.** Среда сообщает о нём
примерно через минуту после конца реплики, когда человек ещё читает ответ.
Поэтому `idle_prompt` помечает ожидание и запускает отсоединённую проверку:
через задержку из настроек доски (`notice_idle_minutes` глобального конфига)
она зовёт, только если ожидание всё то же. Новое ожидание открывают реплика
человека (`UserPromptSubmit`) и конец хода агента (`Stop`) — они стирают
пометку и запоминают время, с которого ожидание пошло.

**Отсчёт идёт от конца хода, а не от события среды.** Своя пауза среды иначе
прибавлялась бы к настроенной, и «три минуты» означали бы четыре: к моменту
события часть задержки уже прошла, поэтому ждут только остаток, а истёкший
остаток зовёт сразу. Времени начала нет (первое ожидание после старта сессии) —
считаем от события, как если бы оно и было началом.

Повторный `idle_prompt` при живой пометке молчит: сколько раз среда его шлёт,
от нас не зависит. Пометки лежат во временной папке системы, по файлу на
сессию, — в проект пользователя они не пишутся.

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
import re
import subprocess
import sys
import tempfile
import time
import uuid
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

# Вид момента, который зовёт с задержкой и раз на ожидание
IDLE = "idle_prompt"
# События, открывающие новое ожидание: человек ответил или агент кончил ход
WAIT_RESET = {"UserPromptSubmit", "Stop"}
# Задержка, если настройку на доске не сохраняли. Дубль умолчания из
# backend/config.py: хук автономен и конфига может не найти вовсе
DEFAULT_IDLE_MINUTES = 3
# Папка пометок ожидания — во временной папке системы, не в проекте
STATE_DIR = "taskboard-attention"
# Шаг сна отложенной проверки, секунды: чаще смотреть незачем, реже — значит
# держать процесс после того, как человек уже ответил
WAIT_STEP = 5

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


def _no_window() -> int:
    """Флаг запуска без консольного окна: у отложенной проверки консоли нет,
    и консольный скрипт иначе получил бы собственное окно."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def idle_minutes() -> float:
    """Задержка уведомления о простое из глобального конфига доски."""
    try:
        data = json.loads((Path.home() / ".taskboard" / "config.json")
                          .read_text(encoding="utf-8"))
        return max(0.0, float(data["notice_idle_minutes"]))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return float(DEFAULT_IDLE_MINUTES)


def state_file(session: str) -> Path:
    """Файл пометки ожидания одной сессии среды."""
    name = re.sub(r"[^\w.-]", "_", session) or "default"
    return Path(tempfile.gettempdir()) / STATE_DIR / f"{name}.json"


def read_state(session: str) -> dict:
    """Состояние ожидания сессии: когда началось и позвали ли уже."""
    try:
        data = json.loads(state_file(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(session: str, state: dict) -> None:
    path = state_file(session)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def read_mark(session: str) -> str:
    """Пометка текущего ожидания или пустая строка, если ожидание новое."""
    return str(read_state(session).get("idle") or "")


def start_wait(session: str) -> None:
    """Открыть новое ожидание: пометка снята, время пошло отсюда."""
    write_state(session, {"since": time.time()})


def send(root: Path, text: str, level: str) -> None:
    """Позвать скрипт доски. Отказ на работу среды не влияет."""
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
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def console_less_python() -> str:
    """Интерпретатор, не показывающий консольного окна.

    На Windows `python.exe` — консольное приложение: у отсоединённого процесса
    консоли нет, и система рисует ему собственное окно поверх работы человека.
    `pythonw.exe` (GUI-подсистема) не создаёт его никогда.
    """
    if os.name == "nt":
        gui = Path(sys.executable).with_name("pythonw.exe")
        if gui.is_file():
            return str(gui)
    return sys.executable


def spawn_waiter(args: list[str]) -> None:
    """Запустить отложенную проверку отдельным процессом и не ждать её.

    Среда ждёт обработчик, а задержка — минуты. Процесс отсоединяется от
    консоли и группы среды, чтобы пережить конец обработчика.
    """
    command = [console_less_python(), str(Path(__file__).resolve()), *args]
    if os.name != "nt":
        try:
            subprocess.Popen(command, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        except OSError:
            pass
        return
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | _no_window())
    # Среда может держать обработчик в задании Windows, которое убьёт всё
    # дерево по его концу. Выйти из задания разрешают не всегда — тогда
    # запускаем без выхода
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    for extra in (breakaway, 0):
        try:
            subprocess.Popen(command, creationflags=flags | extra,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            return
        except OSError:
            continue


def wait_and_send(session: str, mark: str, seconds: str, root: str) -> int:
    """Отложенная проверка: ожидание всё то же — зовём, иначе молчим.

    Сон разбит на шаги: ожидание кончается репликой человека, и висеть после
    неё оставшиеся минуты процессу незачем.
    """
    try:
        left = max(0.0, float(seconds))
    except ValueError:
        return 0
    while left > 0:
        step = min(WAIT_STEP, left)
        time.sleep(step)
        left -= step
        if read_mark(session) != mark:
            return 0
    if read_mark(session) == mark:
        text, level = MOMENTS[IDLE]
        send(Path(root), text, level)
    return 0


def on_idle(payload: dict, root: Path) -> None:
    """Простой: пометить ожидание и позвать — сразу или отложенно."""
    session = str(payload.get("session_id") or "")
    state = read_state(session)
    if state.get("idle"):
        return  # по этому ожиданию уже позвали или вот-вот позовут
    now = time.time()
    try:
        since = float(state["since"])
    except (KeyError, TypeError, ValueError):
        # Начала ожидания не знаем — считаем им само событие
        since = now
    mark = uuid.uuid4().hex
    write_state(session, {"since": since, "idle": mark})
    # Ждём остаток: часть задержки прошла, пока среда молчала о простое
    left = idle_minutes() * 60 - max(0.0, now - since)
    if left <= 0:
        text, level = MOMENTS[IDLE]
        send(root, text, level)
        return
    spawn_waiter(["--wait", session, mark, str(left), str(root)])


def main() -> int:
    if len(sys.argv) == 6 and sys.argv[1] == "--wait":
        return wait_and_send(*sys.argv[2:])
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0
    event = payload.get("hook_event_name")
    if event in WAIT_RESET:
        start_wait(str(payload.get("session_id") or ""))
        return 0
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
    if event == "Notification" and payload.get("notification_type") == IDLE:
        on_idle(payload, root)
    else:
        send(root, text, level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
