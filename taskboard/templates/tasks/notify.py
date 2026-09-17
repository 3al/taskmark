#!/usr/bin/env python3
"""
Скрипт уведомления человека: всплывашка на доске, когда ход перешёл к нему.

Агент виден только в своём терминале. Человек уходит от экрана, пока идёт
долгая работа, — а доску держит открытой: туда и говорим.

Команда запуска: `py` (Windows), `python` (окружения без лаунчера py — например
Python из Microsoft Store), `python3` (macOS/Linux).

Использование:
  py tasks/notify.py "TASK-276 отдана на проверку" --agent "Claude Opus 5"
  python tasks/notify.py "TASK-276 на проверку" --agent "Claude Opus 5"   # без py
  python3 tasks/notify.py "нужен ответ: какой вариант берём?" --agent "Модель" --task TASK-276

Когда звать — правила проекта, раздел «Уведомления человека». Коротко: не
«я что-то сделал», а «дальше без тебя не поедет» — работа отдана на проверку,
нужен ответ, работа встала.

Уровень задаёт тон карточки (цвет полоски и фона), а не важность в очереди:

  --level success   работа готова, ход за человеком
  --level info      нужен ответ, чтобы продолжать (по умолчанию)
  --level warning   работа встала: упали тесты, нет доступа
  --level error     сломалось то, что чинить придётся человеку

Параметры:
  text                    Текст уведомления (одна-две строки по существу)
  --agent МОДЕЛЬ          Кто зовёт: **своя** модель из текущей сессии.
                          Обязателен — уведомление показывает имя, и безымянное
                          «вас зовут» не говорит человеку, кто именно
  --level УРОВЕНЬ         info | success | warning | error (по умолчанию info)
  --task TASK-NNN         Задача, о которой речь (необязательно)
  --tasks-dir PATH        Папка задач (по умолчанию — папка этого скрипта)

**Уведомление можно отозвать, когда повод отпал:**

  py tasks/notify.py --dismiss        # всё, что сказала эта сессия
  py tasks/notify.py --dismiss env    # только зовы среды: вопрос, разрешение

Человек ответил в терминале — звать его больше незачем, и сказанное этой
сессией с доски убирают. **Зовы среды и сообщения агента помечены по-разному**:
конец хода агента снимает только зовы (`env`), потому что сообщение «работа
готова» посылают ровно перед концом хода — гасить его там значит не показать
вовсе. Метку сессии скрипт берёт из окружения среды сам; среда её не даёт —
уведомление живёт по таймеру, как раньше. Обычно отзыв зовут не руками, а хуки
среды.

**Сказать не удалось — не беда.** Сервер не запущен, доска закрыта, источник
выключен в настройках: скрипт скажет об этом строкой и завершится успешно.
Уведомление вспомогательно, и ронять из-за него работу агента незачем.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Порт сервера доски. Дубль дефолта из backend/config.py: скрипт автономен и
# конфига может не найти вовсе — тогда остаётся поставочное значение
DEFAULT_PORT = 8765

# Сколько ждём ответа. Секунды, а не минуты: сервер локальный, а агент за этим
# вызовом стоит и ждёт — зависшая всплывашка дороже несказанной
TIMEOUT = 3


def _utf8_console() -> None:
    """UTF-8 в консоли Windows. Только для запуска как скрипт: при импорте
    подмена потоков ломает stdio вызывающего процесса (например, тестов)."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _read_json(path: Path) -> dict:
    """Прочитать json, при любой ошибке — пустой словарь."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def server_port(tasks_dir: Path) -> int:
    """Порт доски: слои конфига, как их читают остальные скрипты проекта.

    Глобальный конфиг, потом проектный: порт — свойство машины, но проект
    вправе его переопределить, и спорить с этим порядком незачем.
    """
    port = DEFAULT_PORT
    for source in (Path.home() / ".taskboard" / "config.json",
                   tasks_dir.parent / "taskboard" / "config.json",
                   tasks_dir / ".taskboard.json"):
        value = _read_json(source).get("port")
        try:
            port = int(value)
        except (TypeError, ValueError):
            continue
    return port


def project_name(tasks_dir: Path) -> str:
    """Имя проекта в реестре — по совпадению папки задач.

    Сервер один на все проекты, и доска может быть открыта на другом: имя
    показывается рядом с текстом, когда проект не тот. Активный проект тут ни
    при чём — называем **свой**, а не тот, в который человек смотрит.

    **Не нашли в реестре — берём имя папки проекта.** Проект могли убрать с
    доски или перенести его папку, а уведомление доезжает всё равно: канал
    один на весь реестр. Безымянная всплывашка при этом сходит за событие
    открытого проекта — то есть врёт ровно в том месте, ради которого имя и
    едет. Имя папки не всегда совпадает с именем в реестре, но отвечает на
    вопрос «откуда это пришло», а пустая строка не отвечает ни на какой.
    """
    registry = _read_json(Path.home() / ".taskboard" / "projects.json")
    try:
        here = tasks_dir.resolve()
    except OSError:
        here = tasks_dir
    for project in registry.get("projects") or []:
        try:
            if Path(project.get("tasks_dir", "")).resolve() == here:
                return str(project.get("name") or "")
        except OSError:
            continue
    # Папка задач лежит в корне проекта, поэтому его имя — имя родителя.
    # У папки задач в корне диска родителя нет: тогда молчим, как раньше
    return here.parent.name


# Тон уведомления. Набор закрыт и повторяет уровни службы: доска рисует
# каждый своим цветом, и выдуманный уровень доехал бы серой карточкой
LEVELS = ("info", "success", "warning", "error")

# Где среды держат идентификатор своей сессии. Им помечается уведомление,
# чтобы потом отозвать ровно свои: соседняя сессия и соседний проект зовут
# человека по своим поводам, и гасить их чужим ответом нельзя
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "OPENCODE_SESSION_ID",
                "CODEX_SESSION_ID")


# Метки различают, кто сказал: хук среды («ждёт ответа», «ждёт разрешения»)
# или сам агент («работа готова»). Разделены они ради конца хода агента: он
# снимает зовы среды, но не сообщение, посланное прямо перед ним
ENV_SCOPE = "env"
AGENT_SCOPE = "agent"


def session_key(scope: str = AGENT_SCOPE) -> str:
    """Метка сессии агента или пустая строка, если среда её не называет."""
    for name in SESSION_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return f"{scope}:{name}:{value}"
    return ""


def dismiss(tasks_dir: Path, scopes: tuple[str, ...]) -> dict:
    """Убрать с доски сказанное этой сессией. Ошибки — как у отправки."""
    answer = {"ok": True, "sent": False, "reason": "no_server"}
    for scope in scopes:
        key = session_key(scope)
        if not key:
            continue
        result = _post(tasks_dir, "/api/notify/dismiss", {"key": key})
        if result.get("sent"):
            answer = result
    return answer


def notify(tasks_dir: Path, text: str, agent: str = "", task: str = "",
           level: str = "", scope: str = AGENT_SCOPE) -> dict:
    """Отправить уведомление доске. Возвращает ответ сервера или причину молчания.

    Сетевая ошибка тут — обычное состояние, а не сбой: доску просто не
    запускали. Поэтому исключение наружу не идёт, а превращается в `reason`.
    """
    return _post(tasks_dir, "/api/notify",
                 {"text": text, "agent": agent, "task": task, "level": level,
                  "project": project_name(tasks_dir), "key": session_key(scope)})


def _post(tasks_dir: Path, path: str, body: dict) -> dict:
    """Позвать доску. Сетевая ошибка — обычное состояние, а не сбой."""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"http://127.0.0.1:{server_port(tasks_dir)}{path}"
    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"ok": False, "sent": False, "reason": f"http_{error.code}"}
    except Exception:
        return {"ok": False, "sent": False, "reason": "no_server"}


# Почему уведомление не показали — человеческими словами. Молчание без
# объяснения агент принимает за поломку и зовёт скрипт снова
_REASONS = {
    "no_server": "доска не запущена — уведомление не показано",
    "disabled": "источник «Сообщения агента» выключен в настройках доски",
    "no_listeners": "доска не открыта ни в одной вкладке — показывать некому",
}


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        description="Уведомить человека на доске: ход перешёл к нему")
    parser.add_argument("text", nargs="?", default="",
                        help="Текст уведомления")
    parser.add_argument("--dismiss", nargs="?", const="all",
                        choices=("all", ENV_SCOPE),
                        help="Убрать сказанное этой сессией: всё или только "
                             "зовы среды (env)")
    parser.add_argument("--scope", default=AGENT_SCOPE,
                        choices=(AGENT_SCOPE, ENV_SCOPE),
                        help="Чьё это уведомление: агента или среды")
    parser.add_argument("--agent", default="",
                        help="Кто зовёт: своя модель из текущей сессии")
    parser.add_argument("--level", default="info", choices=LEVELS,
                        help="Тон: info | success | warning | error")
    parser.add_argument("--task", default="", help="Задача (TASK-NNN)")
    parser.add_argument("--tasks-dir", default=None,
                        help="Папка задач (по умолчанию — папка этого скрипта)")
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else Path(__file__).parent

    if args.dismiss:
        # Метки нет — гасить нечего: среда своей сессии не называет, и
        # уведомления этой сессии ничем не помечены
        if not session_key():
            print("[i] среда не называет сессию — отзывать нечего")
            return 0
        scopes = (ENV_SCOPE,) if args.dismiss == ENV_SCOPE else (ENV_SCOPE, AGENT_SCOPE)
        print("[OK] сказанное этой сессией убрано с доски"
              if dismiss(tasks_dir, scopes).get("sent")
              else "[i] убирать нечего: доска не открыта или карточки уже истаяли")
        return 0

    text = args.text.strip()
    if not text:
        print("[ERROR] пустое уведомление не показывают", file=sys.stderr)
        return 2
    if not args.agent.strip():
        # Отказ разбора, а не свой код возврата: без имени модели всплывашка
        # говорит «вас зовут», не называя кто
        parser.error("--agent обязателен: представьтесь своей моделью")
    result = notify(tasks_dir, text, args.agent.strip(), args.task.strip(),
                    args.level, args.scope)
    if result.get("sent"):
        print(f"[OK] уведомление показано: {text}")
        return 0
    reason = _REASONS.get(result.get("reason", ""),
                          "доска уведомление не приняла")
    print(f"[i] {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
