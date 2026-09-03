"""Из сообщения чата — задача на доске.

Верхний этаж интеграции: слой источника (`telegram_source`) отдаёт нейтральное
сообщение, здесь оно превращается в задачу того проекта, к которому привязан
чат. Про Telegram этот модуль знает только то, что у сообщения есть чат, автор
и текст.

**Задачу создаёт только тот, кого тегнули.** Сообщение видят боты всех
участников чата — каждый разбирает его сам и молча проходит мимо чужого тега.
Доски у всех локальные, общей правды нет, общая шина — сам чат.

**Тегнуть можно одного.** Двое тегнутых — отказ, а не две задачи: доски
локальные, и «одна задача» распалась бы на две со своими номерами. Отвечают все
тегнутые: молчание одного из ботов читалось бы как «у него получилось».

**Проект берётся из настроек, а не из сообщения.** Чат один, проектов у
человека десяток; выбор делается один раз в настройках, и автор задачи не
приписывает к хэштегу ничего. Суффикс `#задача-<проект>` остаётся
необязательным уточнением для того, кому из одного чата нужно в разные
проекты — но выбирает он **только среди привязанных к этому чату**: право
писать в проект даёт привязка, а не наличие проекта в реестре. Иначе любой
участник чата, зная имя чужого проекта, клал бы задачи туда, куда чат никто
не привязывал.

Имя вне списка — **ошибка в ответ с перечнем доступных**, а не догадка:
переноса задачи между проектами в Taskmark нет, и ошибка чинится руками
вместе с конфликтом номеров.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import registry, telegram_notify, telegram_source
from .config import load_global_config, load_project_config
from .create_task_runner import create_task

# Заголовок уезжает в имя файла задачи — простыня там никому не нужна.
# Всё, что длиннее, не теряется: остаток уходит в описание
TITLE_LIMIT = 120

# Сколько разобранных сообщений помним. Защита от повторной обработки **того
# же** сообщения (перезапуск, падение между созданием задачи и ответом), а не
# от повторного сообщения человека: то — новое сообщение, и новая задача по
# нему правильна
MEMORY = 200

# Рубрика бэклога для задач из чата. Своя, а не по типу задачи: типа у неё нет,
# а «просто в конец раздела приёма» означает «внутрь последней рубрики». Заодно
# рубрика работает сигналом: появилось — разбери. Пустой она не заводится —
# создаётся при первой задаче из чата
CHAT_SECTION = "Из Telegram"

_MENTION = re.compile(r"@([A-Za-z0-9_]{3,})")

# Задачу ставят одному. Тегнули двоих — это не «задача на двоих», а сообщение,
# которое у каждого тегнутого завелось бы своей задачей со своим номером: доски
# локальные, общей правды нет, и «одна задача» распалась бы на две молча.
# Формулировка без числа намеренно: «тегнуто несколько» не требует согласования
# с количеством, а человеку хватает и этого
MANY_TEXT = ("Задача заводится на одного, а в сообщении тегнуто несколько. "
             "Пришлите отдельное сообщение каждому.")

# Неудача заведения — такой же отказ, как остальные, и говорит он то же, что
# все они: что случилось и что делать. Подстановок из внутренностей здесь нет
# намеренно — `usage` argparse и трассировка адресованы тому, кто чинит
# инструмент, и уходят в лог сервера
FAILED_TEXT = ("Задачу завести не удалось. Загляните в лог Taskmark — "
               "причина записана там.")

# Конец предложения: точка (или «!»/«?») и пробел за ней. Пробел обязателен —
# иначе «версия 1.2 сломалась» разрежется по номеру версии
_SENTENCE_END = re.compile(r"[.!?]+\s")

# Слишком короткий кусок предложением не считаем: «т.е.», «и т.д.» и прочие
# сокращения иначе дают заголовок из двух букв
SENTENCE_MIN = 12


def tag(cfg: dict) -> str:
    """Слово-хэштег, по которому сообщение считается задачей."""
    return str(cfg.get("telegram_tag") or "задача").strip().lstrip("#")


def _my_username(cfg: dict) -> str:
    return str(cfg.get("telegram_username") or "").strip().lstrip("@").lower()


def parse(text: str, cfg: dict) -> dict | None:
    """Текст сообщения → заготовка задачи. Не задача — `None`.

    Хэштег может стоять где угодно: люди пишут и «#задача сделать X», и
    «@kostya глянь #задача сделать X».
    """
    if not text:
        return None
    word = re.escape(tag(cfg))
    hashtag = re.search(rf"#{word}(?:-(\S+))?", text, re.IGNORECASE)
    if not hashtag:
        return None
    project = hashtag.group(1) or None

    body = text[:hashtag.start()] + " " + text[hashtag.end():]
    mentions = [name.lower() for name in _MENTION.findall(body)]
    body = _MENTION.sub(" ", body)

    lines = [line.strip() for line in body.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    title = re.sub(r"\s+", " ", lines[0]).strip(" -–—:")
    description = "\n".join(lines[1:]).strip()
    if not title:
        return None
    # Задачу часто пишут одной строкой, дописывая подробности через точку:
    # первое предложение — заголовок, остаток строки уходит в описание
    title, tail = _split_sentence(title)
    if tail:
        description = (tail + "\n" + description).strip()
    if len(title) > TITLE_LIMIT:
        # Режем по границе слова, а хвост не теряем: он уходит в описание
        cut = title.rfind(" ", 0, TITLE_LIMIT)
        cut = cut if cut > 0 else TITLE_LIMIT
        description = (title[cut:].strip() + "\n" + description).strip()
        title = title[:cut].strip()
    return {"title": title, "description": description,
            "project": project, "mentions": mentions}


def _split_sentence(line: str) -> tuple[str, str]:
    """Первое предложение строки и её остаток.

    Конца предложения нет — вся строка остаётся заголовком; тогда её, если она
    длинная, подрежет `TITLE_LIMIT`. Одно правило другое не отменяет: точка
    работает на коротких строках, длина — на тех, где точку не поставили.
    """
    for match in _SENTENCE_END.finditer(line):
        head = line[:match.start()].strip()
        if len(head) >= SENTENCE_MIN:
            return head, line[match.end():].strip()
    # Точка в самом конце пробела за собой не имеет, но предложение закрывает:
    # заголовку она не нужна
    tail_stripped = line.rstrip().rstrip(".").strip()
    if line.rstrip().endswith(".") and len(tail_stripped) >= SENTENCE_MIN:
        return tail_stripped, ""
    return line, ""


def author_of(message: dict) -> str:
    """Кто принёс задачу — строкой для поля `author:`.

    Три ступени, потому что у Bot API нет поля, которое есть всегда: ник ставят
    не все, отображаемое имя тоже необязательно, номер остаётся последним
    рубежом. Пустым автор не остаётся — «откуда пришла задача» такой же ответ,
    как имя человека.

    Ник пишется с «@»: так его отличают от ФИО исполнителя в общем списке
    подсказок и так его копируют обратно в чат.
    """
    nick = str(message.get("username") or "").strip().lstrip("@")
    if nick:
        return f"@{nick}"
    name = " ".join(str(message.get("sender_name") or "").split())
    if name:
        return name
    sender_id = message.get("sender_id")
    return str(sender_id) if sender_id is not None else ""


def is_for_me(parsed: dict | None, cfg: dict) -> bool:
    """Тегнули ли в сообщении меня. Свой ник знает только человек."""
    me = _my_username(cfg)
    if not parsed or not me:
        return False
    return me in parsed["mentions"]


def _find_project(name: str, projects: list[dict]) -> dict | None:
    wanted = name.strip().lower()
    for project in projects:
        if str(project.get("name", "")).strip().lower() == wanted:
            return project
    return None


def bound_projects(cfg: dict, chat_id) -> list[str]:
    """Проекты, куда этот чат может писать. Первый — по умолчанию.

    Значение привязки читается и строкой, и списком: у большинства чат ведёт в
    один проект, и заставлять их писать список незачем.
    """
    value = (cfg.get("telegram_chats") or {}).get(str(chat_id))
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(name).strip() for name in value if str(name).strip()]


def resolve_project(parsed: dict, message: dict, cfg: dict,
                    projects: list[dict]) -> tuple[dict | None, str]:
    """Куда класть задачу. Второй элемент — текст ошибки для ответа в чат.

    **Разрешение писать в проект даёт привязка чата, а не наличие проекта в
    реестре.** Поэтому суффикс `#задача-<проект>` выбирает только среди
    привязанных: иначе любой участник чата, зная имя чужого проекта, клал бы
    задачи туда, куда чат никто не привязывал.
    """
    allowed = bound_projects(cfg, message.get("chat_id"))
    if not allowed:
        return None, ("Этот чат не привязан к проекту — откройте настройки "
                      "Taskmark и выберите, куда складывать задачи.")
    names = ", ".join(allowed)

    wanted = parsed.get("project") or allowed[0]
    if parsed.get("project") and not _same(parsed["project"], allowed):
        return None, (f"Из этого чата нельзя писать в «{parsed['project']}». "
                      f"Доступны: {names}")

    found = _find_project(wanted, projects)
    if not found:
        return None, (f"Чат привязан к проекту «{wanted}», а его больше нет — "
                      "поправьте привязку в настройках Taskmark.")
    return found, ""


def _same(name: str, allowed: list[str]) -> bool:
    needle = name.strip().lower()
    return any(item.strip().lower() == needle for item in allowed)


def _remember(update_id, done: dict) -> None:
    handled = telegram_source.read_state().get("handled") or {}
    handled[str(update_id)] = done
    if len(handled) > MEMORY:
        for key in list(handled)[:-MEMORY]:
            handled.pop(key, None)
    telegram_source.patch_state(handled=handled)


def _recall(update_id) -> dict | None:
    handled = telegram_source.read_state().get("handled") or {}
    return handled.get(str(update_id))


def handle(message: dict, cfg: dict | None = None,
           projects: list[dict] | None = None, send=None) -> dict:
    """Разобрать сообщение и, если оно адресовано нам, завести задачу.

    Возвращает итог для лога и тестов. Ответ в чат уходит через `send` —
    он же подменяется в тестах, чтобы не трогать сеть.
    """
    cfg = cfg if cfg is not None else load_global_config()
    projects = (projects if projects is not None
                else registry.list_projects().get("projects", []))
    # Ответ идёт **тем же путём, что и приём**: прокси и свой адрес API берутся
    # из того же конфига. Иначе за прокси сообщения принимаются, задача
    # создаётся, а подтверждение не уходит — и человек, для которого молчание
    # значит «не доехало», присылает сообщение заново, получая вторую задачу
    reply = send or (lambda chat_id, text, reply_to=None: telegram_source.send_message(
        telegram_source.token(cfg), chat_id, text, reply_to,
        proxy=telegram_source.proxy(cfg),
        api_root=telegram_source.api_root(cfg)))

    parsed = parse(message.get("text", ""), cfg)
    if parsed is None or not is_for_me(parsed, cfg):
        # Не задача или тегнули не нас. Молчим: чужой тег разберёт бот того,
        # кому он адресован, а на болтовню в чате отвечать незачем
        return {"ok": False, "skipped": "не нам"}

    if len(parsed["mentions"]) > 1:
        # Отвечают **все** тегнутые: бот у каждого свой, и молчание одного из
        # них выглядело бы как «у него получилось». Дубли в чате — цена того,
        # что сообщение не сработало ни у кого
        reply(message["chat_id"], MANY_TEXT, message.get("message_id"))
        return {"ok": False, "error": MANY_TEXT}

    known = _recall(message.get("update_id"))
    if known:
        # То же самое сообщение уже разбирали: задача есть, а вот ответ мог
        # не уйти — падение между созданием и ответом выглядит для человека
        # молчанием, и он пришлёт сообщение заново
        reply(message["chat_id"], _reply_text(known["id"], known["title"],
                                              known["project"]),
              message.get("message_id"))
        return {"ok": True, **known, "repeat": True}

    project, error = resolve_project(parsed, message, cfg, projects)
    if error:
        reply(message["chat_id"], error, message.get("message_id"))
        return {"ok": False, "error": error}

    tasks_dir = Path(project["tasks_dir"])
    project_cfg = load_project_config(tasks_dir)
    result = create_task(tasks_dir, project_cfg, {
        "title": parsed["title"],
        "description": parsed["description"],
        # Критериев и типа из чата не приходит, а скрипт без этих ключей
        # подставляет свои дефолты: задача начинала утверждать то, чего никто
        # не говорил — TDD-критерий и «новый функционал»
        "criteria": "",
        "task_type": "",
        "section": CHAT_SECTION,
        # Автор — тот, кто бросил задачу в чат. Единственный путь заведения,
        # где имя приходит само: доска и агент называют себя сами
        "author": author_of(message),
        # Откуда задача пришла. По этой метке канал чата потом узнаёт свои
        # задачи и адрес: рубрика бэклога для этого не годится (её переносят и
        # переименовывают), а автор-ник не доказательство
        "origin": telegram_notify.origin_of(message.get("chat_id")),
    })
    if not result.get("ok"):
        error = str(result.get("error") or "").strip()
        _log(f"[taskboard] telegram: задача из чата не заведена — {error}")
        reply(message["chat_id"], FAILED_TEXT, message.get("message_id"))
        return {"ok": False, "error": result.get("error")}

    done = {"id": result.get("id"), "title": parsed["title"],
            "project": str(project.get("name", ""))}
    _remember(message.get("update_id"), done)
    reply(message["chat_id"], _reply_text(done["id"], done["title"], done["project"]),
          message.get("message_id"))
    return {"ok": True, **done}


def _log(text: str) -> None:
    """Написать в лог, не уронив разбор.

    В сообщение попадает вывод чужого процесса, а у консоли Windows своя
    кодировка: `print` с символом, которого в ней нет, падает исключением — и
    сообщение оставалось бы необработанным из-за строчки в логе.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(encoding, "replace").decode(encoding, "replace"), flush=True)


def _reply_text(task_id: str, title: str, project: str) -> str:
    """Ответ называет проект: неверную привязку видно на первой же задаче.

    Статус и раздел доски здесь не нужны: задача из чата всегда попадает в
    бэклог, и повторять это в каждом ответе — шум, а не сведения.
    """
    return f"{task_id} · {title} → бэклог проекта «{project}»"
