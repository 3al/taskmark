"""Слой источника Telegram: получить новые сообщения, отправить ответ.

Нижний этаж интеграции с чатом. Всё, что выше — разбор текста, выбор проекта,
создание задачи — знает об этом слое ровно через два действия: «дай новые
сообщения» и «отправь ответ». Поэтому выбор источника остаётся обратимым: если
однажды упрёмся в чтение истории (Bot API её не отдаёт вовсе), рядом встаёт
второй драйвер, а верхние слои не меняются.

**Своих зависимостей у слоя нет.** Bot API — обычный HTTPS с JSON, и `urllib`
хватает: образец рядом, `updater.fetch_manifest`. Запрос вынесен параметром
`fetch`, поэтому тесты проверяют главное — что при выключенной возможности
запрос **не уходит** — не трогая сеть.

**Курсор подтверждает только обработанное.** У бота очередь апдейтов общая и
разрушающая: подтверждённое `offset` Telegram удаляет, и вернуть его нельзя.
Значит двигать курсор можно после того, как сообщение отдано обработчику, а не
после того, как оно получено. Нет обработчика (слой развёрнут, разбор ещё нет) —
курсор стоит, и сообщения дождутся своего часа в очереди Telegram.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Callable

from .config import GLOBAL_DIR

API_ROOT = "https://api.telegram.org"

# Сколько ждать ответа. Long polling не используем: поток проверяет очередь
# по таймеру, как это делает проверка обновлений, — так остановка сервера не
# упирается в висящий на минуту запрос
TIMEOUT = 15

# Как часто заглядывать в чат. Пять секунд — компромисс: человек, бросивший
# задачу в чат, ждёт ответа сразу, а лишние запросы к API ничего не стоят
WAKE_INTERVAL = 5.0

USER_AGENT = "taskboard"

# Курсор очереди апдейтов. Рядом с кэшем обновлений и **не в конфиге**: конфиг
# хранит только изменённое пользователем, и служебное состояние заморозило бы
# в нём дефолты поставки
STATE_FILE = GLOBAL_DIR / "telegram.json"

# Чаты, которые бот видел с момента запуска. Нужны настройкам: у групп id —
# отрицательное число вида -1001234567890, и заставлять человека искать его
# руками незачем — он пишет в нужный чат, а Taskmark показывает имя
_SEEN_CHATS: dict[int, str] = {}


class TelegramError(RuntimeError):
    """Bot API ответил отказом: неверный токен, бот выкинут из чата и прочее."""


def enabled(cfg: dict) -> bool:
    """Включена ли интеграция. Без токена флаг ничего не значит."""
    return bool(cfg.get("telegram")) and bool(str(cfg.get("telegram_token") or "").strip())


def token(cfg: dict) -> str:
    return str(cfg.get("telegram_token") or "").strip()


# --- Состояние --------------------------------------------------------------

def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — нет файла или он испорчен: начинаем сначала
        return {}


def write_state(data: dict) -> None:
    try:
        GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:  # noqa: BLE001 — диск недоступен: курсор переживёт в памяти
        pass


def patch_state(**changes) -> None:
    """Дописать ключи в состояние, перечитав файл.

    Писать снимок, снятый раньше, нельзя: за это время в тот же файл могли
    добавить чаты или отметку об обработанном сообщении, и запись курсора
    стёрла бы их.
    """
    state = read_state()
    state.update(changes)
    write_state(state)


def seen_chats() -> list[dict]:
    """Чаты, которые бот видел: id и имя для выбора в настройках.

    Читается из файла состояния, а не только из памяти процесса: перезапуск
    сервера иначе очищал список, и **сохранённая привязка становилась
    невидимой** — человек видел пустой шаг настройки и решал, что настройки
    пропали.
    """
    known = {**(read_state().get("chats") or {}), **{str(k): v for k, v in _SEEN_CHATS.items()}}
    return [{"id": int(chat_id), "title": title} for chat_id, title in known.items()]


# --- Клиент Bot API ---------------------------------------------------------

def _fetch(url: str, payload: dict) -> dict:
    """Запрос к Bot API. Выделен, чтобы тесты подставляли свой транспорт."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _call(tok: str, method: str, payload: dict,
          fetch: Callable | None = None):
    """Вызвать метод API и вернуть `result`, подняв отказ исключением."""
    answer = (fetch or _fetch)(f"{API_ROOT}/bot{tok}/{method}", payload)
    if not isinstance(answer, dict) or not answer.get("ok"):
        description = ""
        if isinstance(answer, dict):
            description = str(answer.get("description") or "")
        raise TelegramError(description or f"{method}: отказ Bot API")
    return answer.get("result")


def get_me(tok: str, fetch: Callable | None = None) -> dict:
    """Кто мы: имя бота для кнопки «Проверить» в настройках."""
    return _call(tok, "getMe", {}, fetch) or {}


def _message_of(raw: dict) -> dict | None:
    """Апдейт Bot API → нейтральное сообщение, каким его видят верхние слои.

    Формат телеграма дальше этого модуля не уходит: смена источника не должна
    задевать разбор. Апдейты без текста (вход в группу, картинка, стикер)
    отбрасываются здесь же — верхним слоям они не нужны.
    """
    message = raw.get("message") or raw.get("edited_message") or {}
    text = message.get("text")
    if not text:
        return None
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    # Отправитель едет тремя полями, а не одним готовым именем: кем из них
    # назвать автора задачи — решение верхнего слоя, а не источника. Ник есть
    # не у всех, отображаемое имя тоже не обязательно, номер есть всегда
    name = " ".join(part for part in (sender.get("first_name"),
                                      sender.get("last_name")) if part)
    return {
        "update_id": raw.get("update_id"),
        "message_id": message.get("message_id"),
        "chat_id": chat.get("id"),
        "chat_title": chat.get("title") or chat.get("username") or "",
        "text": text,
        "username": sender.get("username") or "",
        "sender_name": name,
        "sender_id": sender.get("id"),
    }


def get_updates(tok: str, offset: int = 0, fetch: Callable | None = None) -> list[dict]:
    """Новые сообщения начиная с `offset`, уже в нейтральном виде."""
    result = _call(tok, "getUpdates", {"offset": offset, "timeout": 0}, fetch) or []
    messages = []
    for raw in result:
        if not isinstance(raw, dict):
            continue
        _remember_chat(raw)
        message = _message_of(raw)
        if message:
            messages.append(message)
    return messages


def _remember_chat(raw: dict) -> None:
    chat = ((raw.get("message") or raw.get("edited_message") or {}).get("chat")) or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    title = chat.get("title") or chat.get("username") or ""
    if _SEEN_CHATS.get(chat_id) == title:
        return
    _SEEN_CHATS[chat_id] = title
    # На диск — только на новом или переименованном чате: иначе состояние
    # переписывалось бы на каждом опросе очереди
    chats = read_state().get("chats") or {}
    chats[str(chat_id)] = title
    patch_state(chats=chats)


def chat_title(cfg: dict, chat_id, fetch: Callable | None = None) -> str:
    """Имя чата по id: спрашиваем у Bot API один раз и запоминаем.

    Нужно для настроек: привязка чата хранит id, и у чата, настроенного до
    того, как имена начали сохраняться, взять имя больше неоткуда — человек
    видел бы в списке голое число. Неудача не страшна: строка останется с id.
    """
    known = read_state().get("chats") or {}
    if known.get(str(chat_id)):
        return known[str(chat_id)]
    if not enabled(cfg):
        return ""
    try:
        chat = _call(token(cfg), "getChat", {"chat_id": chat_id}, fetch) or {}
    except Exception:  # noqa: BLE001 — бота выкинули из чата, сеть, битый токен
        return ""
    title = chat.get("title") or chat.get("username") or ""
    if title:
        known[str(chat_id)] = title
        patch_state(chats=known)
    return title


def send_message(tok: str, chat_id: int, text: str, reply_to: int | None = None,
                 fetch: Callable | None = None) -> dict:
    """Ответить в чат. `reply_to` привязывает ответ к исходному сообщению."""
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    return _call(tok, "sendMessage", payload, fetch) or {}


# --- Поллер -----------------------------------------------------------------

def poll_once(cfg: dict, handle: Callable | None = None,
              fetch: Callable | None = None) -> int:
    """Один проход очереди: забрать новые сообщения и отдать обработчику.

    Возвращает число обработанных сообщений. Курсор двигается **после** вызова
    обработчика и по каждому сообщению отдельно: падение на середине пачки не
    съедает то, до чего не дошли.
    """
    if not enabled(cfg):
        return 0
    state = read_state()
    offset = int(state.get("offset") or 0)
    try:
        messages = get_updates(token(cfg), offset, fetch)
    except (TelegramError, urllib.error.URLError, OSError, ValueError):
        # Сеть отвалилась или API ответил отказом — курсор не двигаем:
        # подтверждённое Telegram удаляет навсегда
        return 0
    if handle is None:
        # Слой источника есть, разбора ещё нет: подтверждать нечего.
        # Сообщения подождут в очереди Telegram (около суток)
        return 0
    done = 0
    for message in messages:
        try:
            handle(message)
        except Exception:  # noqa: BLE001 — разбор упал: одно сообщение не должно
            pass          # навсегда закрыть вход, поэтому курсор всё равно двигаем
        update_id = message.get("update_id")
        if isinstance(update_id, int):
            patch_state(offset=update_id + 1)
        done += 1
    return done


def start_polling(cfg: dict, handle: Callable | None = None,
                  interval: float = WAKE_INTERVAL,
                  fetch: Callable | None = None):
    """Проверять чат по таймеру, пока живёт сервер. Возвращает «стоп».

    Поток — демон, как у проверки обновлений: он не держит выход процесса и не
    мешает остановке и перезапуску сервера из UI. Выключенная возможность
    потока не создаёт вовсе — в сеть при ней не уходит ни одного запроса.
    """
    stop = threading.Event()
    if not enabled(cfg):
        return stop.set

    def loop() -> None:
        while not stop.wait(interval):
            try:
                poll_once(cfg, handle, fetch)
            except Exception:  # noqa: BLE001 — цикл переживает любую неудачу
                pass

    threading.Thread(target=loop, name="telegram-poll-loop", daemon=True).start()
    return stop.set
