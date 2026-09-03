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

import base64
import http.client
import json
import socket
import ssl
import threading
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import unquote, urlsplit

from .config import GLOBAL_DIR

API_ROOT = "https://api.telegram.org"

# Схемы прокси и порт по умолчанию у каждой. Реестр, а не набор проверок:
# схему, которой здесь нет, отказ назовёт человеку по имени
PROXY_SCHEMES = {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}

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


def proxy(cfg: dict) -> str:
    """Адрес прокси для Bot API. Пусто — прямое соединение."""
    return str(cfg.get("telegram_proxy") or "").strip()


def api_root(cfg: dict) -> str:
    """Куда ходить за Bot API. Пусто — сам телеграм.

    Свой адрес — это **реверс-прокси на своём домене** (nginx перед Bot API или
    локальный `telegram-bot-api`): третий путь туда, где `api.telegram.org`
    закрыт, и он не прокси, а корень адреса.
    """
    raw = str(cfg.get("telegram_api_root") or "").strip().rstrip("/")
    if not raw:
        return API_ROOT
    if "://" not in raw:
        raise TelegramError(
            "Адрес Bot API: не указана схема — нужен адрес вида https://ваш.домен")
    scheme = urlsplit(raw).scheme.lower()
    if scheme not in ("http", "https"):
        raise TelegramError(
            f"Адрес Bot API: схема «{scheme}» не подходит — нужен http или https")
    return raw


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


# --- Прокси -----------------------------------------------------------------
# Bot API — HTTPS, и прокси ему нужен HTTP или SOCKS5. **MTProto-прокси сюда не
# годится**: он прокси протокола MTProto, к нему подключаются клиенты Telegram,
# а не HTTP-клиенты, и ссылки `tg://proxy?...` тут бесполезны.

def parse_proxy(url: str) -> dict | None:
    """Строка человека → разобранный адрес. Пусто — прямое соединение.

    Ошибку поднимаем, а не глотаем: неразобранный адрес означает, что прокси не
    применился, и молчание об этом выглядит как «интеграция сама не работает».
    """
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raise TelegramError(
            "Прокси: не указана схема — нужен адрес вида socks5://хост:порт")
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme in ("mtproto", "tg"):
        raise TelegramError(
            "Прокси: MTProto не подходит — бот ходит в Bot API по HTTPS. "
            "Нужен http, https, socks5 или socks5h")
    if scheme not in PROXY_SCHEMES:
        raise TelegramError(
            f"Прокси: схема «{scheme}» не поддерживается — "
            "нужен http, https, socks5 или socks5h")
    try:
        port = parts.port or PROXY_SCHEMES[scheme]
    except ValueError:
        raise TelegramError("Прокси: порт должен быть числом") from None
    if not parts.hostname:
        raise TelegramError("Прокси: не указан адрес прокси-сервера")
    return {"scheme": scheme, "host": parts.hostname, "port": int(port),
            "user": unquote(parts.username or ""),
            "password": unquote(parts.password or "")}


def _proxy_address(parsed: dict) -> str:
    """Адрес для `ProxyHandler`: логин и пароль едут в самой строке."""
    auth = ""
    if parsed["user"]:
        auth = f"{parsed['user']}:{parsed['password']}@"
    return f"{parsed['scheme']}://{auth}{parsed['host']}:{parsed['port']}"


def _recv_exact(sock, size: int) -> bytes:
    """Прочитать ровно `size` байт. Оборвалось — это отказ прокси, не падение."""
    chunks = b""
    while len(chunks) < size:
        try:
            piece = sock.recv(size - len(chunks))
        except OSError as exc:
            raise TelegramError(f"Прокси оборвал соединение: {exc}") from None
        if not piece:
            raise TelegramError("Прокси оборвал соединение")
        chunks += piece
    return chunks


def _socks5_handshake(sock, host: str, port: int, parsed: dict) -> None:
    """Довести сокет до туннеля к `host:port` (RFC 1928/1929).

    Своя реализация, а не пакет: SOCKS5 CONNECT — три коротких обмена, а
    зависимость пришлось бы доставлять всем пользователям ради них.

    `socks5h` отдаёт прокси **имя**, `socks5` разрешает его локально: разница
    существенна там, где имя не разрешается вовсе.
    """
    user, password = parsed.get("user") or "", parsed.get("password") or ""
    methods = b"\x00\x02" if user else b"\x00"  # 0x00 — без авторизации, 0x02 — логин
    _send(sock, bytes([5, len(methods)]) + methods)
    answer = _recv_exact(sock, 2)
    if answer[0] != 5:
        raise TelegramError("Прокси ответил не по протоколу SOCKS5")
    if answer[1] == 0xFF:
        raise TelegramError("Прокси не принял способ авторизации")
    if answer[1] == 2:
        if not user:
            raise TelegramError("Прокси требует логин и пароль")
        name, secret = user.encode("utf-8"), password.encode("utf-8")
        _send(sock, bytes([1, len(name)]) + name + bytes([len(secret)]) + secret)
        if _recv_exact(sock, 2)[1] != 0:
            raise TelegramError("Прокси не принял логин или пароль")
    elif answer[1] != 0:
        raise TelegramError(f"Прокси выбрал неизвестный способ авторизации "
                            f"({answer[1]})")
    if parsed["scheme"] == "socks5h":
        name = host.encode("idna")
        target = bytes([3, len(name)]) + name
    else:
        try:
            target = b"\x01" + socket.inet_aton(socket.gethostbyname(host))
        except OSError as exc:
            raise TelegramError(f"Имя {host} не разрешилось: {exc}") from None
    _send(sock, b"\x05\x01\x00" + target + port.to_bytes(2, "big"))
    reply = _recv_exact(sock, 4)
    if reply[1] != 0:
        raise TelegramError(f"Прокси ответил отказом на соединение (код {reply[1]})")
    # Дочитать адрес привязки: он не нужен, но оставленный в сокете испортит ответ
    if reply[3] == 1:
        _recv_exact(sock, 4 + 2)
    elif reply[3] == 3:
        _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
    elif reply[3] == 4:
        _recv_exact(sock, 16 + 2)


def _send(sock, data: bytes) -> None:
    try:
        sock.sendall(data)
    except OSError as exc:
        raise TelegramError(f"Прокси недоступен: {exc}") from None


def _http_connect(sock, host: str, port: int, parsed: dict) -> None:
    """Попросить прокси открыть туннель до `host:port` (RFC 9110, CONNECT).

    Логин уходит заголовком `Proxy-Authorization`, и только поэтому TLS до
    прокси обязателен: в открытом CONNECT пароль виден целиком.
    """
    lines = [f"CONNECT {host}:{port} HTTP/1.1",
             f"Host: {host}:{port}",
             f"User-Agent: {USER_AGENT}"]
    if parsed.get("user"):
        secret = f"{parsed['user']}:{parsed.get('password') or ''}"
        lines.append("Proxy-Authorization: Basic "
                     + base64.b64encode(secret.encode("utf-8")).decode("ascii"))
    _send(sock, ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
    answer = b""
    while b"\r\n\r\n" not in answer:
        try:
            piece = sock.recv(4096)
        except OSError as exc:
            raise TelegramError(f"Прокси оборвал соединение: {exc}") from None
        if not piece:
            raise TelegramError("Прокси оборвал соединение")
        answer += piece
    status = answer.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = status.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise TelegramError(f"Прокси ответил не по HTTP: {status[:60]!r}")
    if parts[1] != "200":
        raise TelegramError(f"Прокси отказал в туннеле: {status}")


def _https_proxy_connection(parsed: dict):
    """Класс соединения для HTTPS-прокси: TLS до прокси, CONNECT внутри него.

    Штатный `ProxyHandler` со схемой `https` **обманывает**: CPython шлёт
    CONNECT открытым текстом, то есть работает как обычный HTTP-прокси. Схема,
    обещающая шифрование до прокси, обязана его ставить — иначе логин уходит в
    открытую.
    """

    class HttpsProxyConnection(http.client.HTTPSConnection):
        def connect(self) -> None:
            try:
                raw = socket.create_connection((parsed["host"], parsed["port"]),
                                               self.timeout or TIMEOUT)
            except OSError as exc:
                raise TelegramError(f"Прокси {parsed['host']}:{parsed['port']} "
                                    f"не отвечает: {exc}") from None
            try:
                tunnel = ssl.create_default_context().wrap_socket(
                    raw, server_hostname=parsed["host"])
                _http_connect(tunnel, self.host, self.port, parsed)
            except ssl.SSLError as exc:
                raw.close()
                raise TelegramError(f"TLS до прокси не поднялся: {exc}") from None
            except Exception:
                raw.close()
                raise
            # Второй TLS — уже до Bot API: прокси видит только имя хоста в CONNECT
            self.sock = self._context.wrap_socket(tunnel, server_hostname=self.host)

    return HttpsProxyConnection


class _HttpsProxyHandler(urllib.request.HTTPSHandler):
    """`urllib` поверх HTTPS-прокси."""

    def __init__(self, parsed: dict):
        super().__init__()
        self._parsed = parsed

    def https_open(self, req):
        return self.do_open(_https_proxy_connection(self._parsed), req,
                            context=self._context)


def _socks5_connection(parsed: dict):
    """Класс соединения, открывающий HTTPS через SOCKS5.

    Подменяется **соединение**, а не `socket.socket`: глобальный monkey-patch
    увёл бы в прокси и сокеты самого сервера.
    """

    class Socks5Connection(http.client.HTTPSConnection):
        def connect(self) -> None:
            try:
                raw = socket.create_connection((parsed["host"], parsed["port"]),
                                               self.timeout or TIMEOUT)
            except OSError as exc:
                # Иначе человек читает «конечный компьютер отверг запрос» и ищет
                # причину в боте, хотя не отозвался прокси
                raise TelegramError(
                    f"Прокси {parsed['host']}:{parsed['port']} "
                    f"не отвечает: {exc}") from None
            try:
                _socks5_handshake(raw, self.host, self.port, parsed)
            except Exception:
                raw.close()
                raise
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)

    return Socks5Connection


class _Socks5Handler(urllib.request.HTTPSHandler):
    """`urllib` поверх SOCKS5: TLS свой, а TCP открывает прокси."""

    def __init__(self, parsed: dict):
        super().__init__()
        self._parsed = parsed

    def https_open(self, req):
        return self.do_open(_socks5_connection(self._parsed), req,
                            context=self._context)


def _opener_for(parsed: dict):
    """Открыватель под разобранный адрес прокси.

    Пустой `ProxyHandler` у SOCKS5 стоит намеренно: без него `urllib` подставит
    системный прокси из переменных окружения, и запрос уйдёт мимо настройки. В
    саму цепочку он не попадает — обработчик без прокси нечем вызвать, — но
    место дефолтного занимает, а это здесь и нужно.
    """
    if parsed["scheme"] == "http":
        address = _proxy_address(parsed)
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": address, "https": address}))
    handler = (_HttpsProxyHandler(parsed) if parsed["scheme"] == "https"
               else _Socks5Handler(parsed))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), handler)


# --- Клиент Bot API ---------------------------------------------------------

def _request(url: str, payload: dict) -> urllib.request.Request:
    return urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})


def _explain(exc: urllib.error.URLError, url: str, parsed: dict | None) -> Exception:
    """Перевести неудачу запроса в то, что человек может починить.

    Различать здесь обязательно: **подменённый сертификат — не недоступность**.
    Прокси, который терминирует TLS у себя, отвечает и туннель открывает, а
    сообщение «прокси не отвечает» отправило бы человека чинить связь вместо
    того, чтобы увести токен бота с такого прокси.
    """
    reason = getattr(exc, "reason", exc)
    host = urlsplit(url).hostname or "Bot API"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return TelegramError(
            f"Сертификат {host} не прошёл проверку: соединение расшифровывает "
            f"посредник. Токен через него не уйдёт "
            f"({getattr(reason, 'verify_message', '') or reason})")
    if isinstance(reason, ssl.SSLError):
        return TelegramError(f"Защищённое соединение с {host} не поднялось: {reason}")
    if parsed is not None:
        return TelegramError(f"Прокси {parsed['host']}:{parsed['port']} "
                             f"не отвечает: {reason}")
    return exc


def _fetch(url: str, payload: dict) -> dict:
    """Запрос к Bot API. Выделен, чтобы тесты подставляли свой транспорт."""
    try:
        with urllib.request.urlopen(_request(url, payload), timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        raise  # ответ Bot API: разбирается выше, подменять его нечем
    except urllib.error.URLError as exc:
        raise _explain(exc, url, None) from None


def fetcher(proxy_url: str = "") -> Callable:
    """Транспорт под настройку прокси. Пусто — прямой, как было."""
    parsed = parse_proxy(proxy_url)
    if parsed is None:
        return _fetch
    opener = _opener_for(parsed)

    def fetch(url: str, payload: dict) -> dict:
        try:
            with opener.open(_request(url, payload), timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise  # прокси дошёл: это ответ Bot API, а не беда с прокси
        except urllib.error.URLError as exc:
            raise _explain(exc, url, parsed) from None

    return fetch


def _call(tok: str, method: str, payload: dict,
          fetch: Callable | None = None, proxy_url: str = "",
          root: str = ""):
    """Вызвать метод API и вернуть `result`, подняв отказ исключением."""
    answer = (fetch or fetcher(proxy_url))(
        f"{root or API_ROOT}/bot{tok}/{method}", payload)
    if not isinstance(answer, dict) or not answer.get("ok"):
        description = ""
        if isinstance(answer, dict):
            description = str(answer.get("description") or "")
        raise TelegramError(description or f"{method}: отказ Bot API")
    return answer.get("result")


def get_me(tok: str, fetch: Callable | None = None, proxy: str = "",
           api_root: str = "") -> dict:
    """Кто мы: имя бота для кнопки «Проверить» в настройках."""
    return _call(tok, "getMe", {}, fetch, proxy, api_root) or {}


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


def get_updates(tok: str, offset: int = 0, fetch: Callable | None = None,
                proxy: str = "", api_root: str = "") -> list[dict]:
    """Новые сообщения начиная с `offset`, уже в нейтральном виде."""
    result = _call(tok, "getUpdates", {"offset": offset, "timeout": 0},
                   fetch, proxy, api_root) or []
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
        chat = _call(token(cfg), "getChat", {"chat_id": chat_id}, fetch,
                     proxy(cfg), api_root(cfg)) or {}
    except Exception:  # noqa: BLE001 — бота выкинули из чата, сеть, битый токен
        return ""
    title = chat.get("title") or chat.get("username") or ""
    if title:
        known[str(chat_id)] = title
        patch_state(chats=known)
    return title


def send_message(tok: str, chat_id: int, text: str, reply_to: int | None = None,
                 fetch: Callable | None = None, proxy: str = "",
                 api_root: str = "") -> dict:
    """Ответить в чат. `reply_to` привязывает ответ к исходному сообщению."""
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    return _call(tok, "sendMessage", payload, fetch, proxy, api_root) or {}


# --- Поллер -----------------------------------------------------------------

# О чём поллер уже пожаловался. Опрос идёт каждые пять секунд, и без этого
# неверный адрес прокси залил бы лог одной и той же строкой
_LAST_COMPLAINT = ""


def _complain(exc: Exception) -> None:
    """Сказать в лог, почему опрос не удался, — но не повторяться.

    Ошибка настройки (прокси не разобран, токен отвергнут) сама не пройдёт, и
    молчащая интеграция выглядит сломанной без объяснений.
    """
    global _LAST_COMPLAINT
    message = f"{type(exc).__name__}: {exc}"
    if message == _LAST_COMPLAINT:
        return
    _LAST_COMPLAINT = message
    print(f"[taskboard] telegram: опрос не удался — {message}", flush=True)

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
        messages = get_updates(token(cfg), offset, fetch, proxy(cfg),
                               api_root(cfg))
    except (TelegramError, urllib.error.URLError, OSError, ValueError) as exc:
        # Сеть отвалилась, прокси не принял или API ответил отказом — курсор не
        # двигаем: подтверждённое Telegram удаляет навсегда
        _complain(exc)
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
