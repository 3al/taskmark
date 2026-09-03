"""Прокси для Bot API: разбор адреса, выбор транспорта, хендшейк SOCKS5.

Внешнюю сеть тесты не трогают. Транспорт проверяется по тому, **что построено**
(какой обработчик получил адрес прокси), а хендшейк SOCKS5 — на паре сокетов
`socket.socketpair()`: на одном конце наш код, на другом — заготовленные ответы
прокси. Так проверяется протокол, а не доступность чужого сервера.
"""

import base64
import socket
import ssl
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from backend import telegram_source as ts

TOKEN = "123:AAH-test"


class TestРазборАдреса(unittest.TestCase):
    """Строка прокси — ввод человека: разобрать или внятно отказать."""

    def test_пусто_значит_напрямую(self):
        self.assertIsNone(ts.parse_proxy(""))
        self.assertIsNone(ts.parse_proxy("   "))

    def test_http_с_портом(self):
        parsed = ts.parse_proxy("http://10.0.0.1:3128")
        self.assertEqual(parsed["scheme"], "http")
        self.assertEqual(parsed["host"], "10.0.0.1")
        self.assertEqual(parsed["port"], 3128)
        self.assertEqual(parsed["user"], "")

    def test_socks5_с_авторизацией(self):
        parsed = ts.parse_proxy("socks5://вася:секрет@proxy.example:1080")
        self.assertEqual(parsed["scheme"], "socks5")
        self.assertEqual(parsed["host"], "proxy.example")
        self.assertEqual(parsed["port"], 1080)
        self.assertEqual(parsed["user"], "вася")
        self.assertEqual(parsed["password"], "секрет")

    def test_порт_по_умолчанию_у_каждой_схемы(self):
        self.assertEqual(ts.parse_proxy("http://p")["port"], 80)
        self.assertEqual(ts.parse_proxy("https://p")["port"], 443)
        self.assertEqual(ts.parse_proxy("socks5://p")["port"], 1080)
        self.assertEqual(ts.parse_proxy("socks5h://p")["port"], 1080)

    def test_схема_обязательна(self):
        """Без схемы непонятно, какой это прокси, — угадывать нельзя."""
        with self.assertRaises(ts.TelegramError):
            ts.parse_proxy("10.0.0.1:3128")

    def test_mtproto_отклоняется_с_объяснением(self):
        """Самая вероятная ошибка человека: Bot API — это HTTPS, не MTProto."""
        with self.assertRaises(ts.TelegramError) as caught:
            ts.parse_proxy("mtproto://proxy.example:443")
        self.assertIn("MTProto", str(caught.exception))

    def test_неподдержанная_схема_называет_поддержанные(self):
        with self.assertRaises(ts.TelegramError) as caught:
            ts.parse_proxy("socks4://proxy.example:1080")
        text = str(caught.exception)
        for scheme in ("http", "socks5"):
            self.assertIn(scheme, text)

    def test_адрес_без_хоста(self):
        with self.assertRaises(ts.TelegramError):
            ts.parse_proxy("socks5://:1080")

    def test_нечисловой_порт(self):
        with self.assertRaises(ts.TelegramError):
            ts.parse_proxy("http://proxy.example:порт")


class TestВыборТранспорта(unittest.TestCase):
    """Какой транспорт строится под адрес."""

    def test_без_прокси_прямой_транспорт(self):
        self.assertIs(ts.fetcher(""), ts._fetch)

    def test_http_прокси_уходит_в_ProxyHandler(self):
        opener = ts._opener_for(ts.parse_proxy("http://10.0.0.1:3128"))
        proxies = [h.proxies for h in opener.handlers
                   if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(proxies, [{"http": "http://10.0.0.1:3128",
                                    "https": "http://10.0.0.1:3128"}])

    def test_авторизация_едет_в_адресе_прокси(self):
        opener = ts._opener_for(ts.parse_proxy("http://вася:секрет@p:3128"))
        proxies = [h.proxies for h in opener.handlers
                   if isinstance(h, urllib.request.ProxyHandler)][0]
        self.assertIn("вася:секрет@", proxies["https"])

    def test_socks5_не_ходит_через_ProxyHandler(self):
        """`urllib` SOCKS не умеет: подмена идёт на уровне соединения."""
        opener = ts._opener_for(ts.parse_proxy("socks5://p:1080"))
        self.assertFalse([h for h in opener.handlers
                          if isinstance(h, urllib.request.ProxyHandler)])

    def test_глобальный_сокет_не_подменяется(self):
        """Через сокеты работает сам сервер: monkey-patch увёл бы в прокси всё."""
        before = socket.socket
        ts.fetcher("socks5://p:1080")
        self.assertIs(socket.socket, before)


class TestНедоступныйПрокси(unittest.TestCase):
    """Молчащий прокси должен называть себя, а не пугать системным текстом."""

    def _fetch_through(self, address: str, error: Exception):
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(ts, "_opener_for", return_value=opener):
            fetch = ts.fetcher(address)
        return fetch

    def test_прокси_назван_в_ошибке(self):
        fetch = self._fetch_through("http://10.0.0.1:3128",
                                    urllib.error.URLError("отказано"))
        with self.assertRaises(ts.TelegramError) as caught:
            fetch("https://api.telegram.org/botX/getMe", {})
        self.assertIn("10.0.0.1:3128", str(caught.exception))

    def test_подменённый_сертификат_называют_подменой(self):
        """Прокси ответил и туннель открыл — врать «не отвечает» здесь нельзя."""
        fetch = self._fetch_through(
            "socks5://10.0.0.1:1080",
            urllib.error.URLError(ssl.SSLCertVerificationError(
                "certificate verify failed: self-signed certificate")))
        with self.assertRaises(ts.TelegramError) as caught:
            fetch("https://api.telegram.org/botX/getMe", {})
        text = str(caught.exception)
        self.assertIn("api.telegram.org", text)
        self.assertIn("сертификат", text.lower())
        self.assertNotIn("не отвечает", text)

    def test_сертификат_проверяется_и_без_прокси(self):
        """Подменять TLS умеет не только прокси — текст нужен тот же."""
        with mock.patch.object(ts.urllib.request, "urlopen") as opened:
            opened.side_effect = urllib.error.URLError(
                ssl.SSLCertVerificationError("certificate verify failed"))
            with self.assertRaises(ts.TelegramError) as caught:
                ts._fetch("https://api.telegram.org/botX/getMe", {})
        self.assertIn("сертификат", str(caught.exception).lower())

    def test_прочие_ошибки_tls_не_выдают_за_подмену(self):
        fetch = self._fetch_through(
            "socks5://10.0.0.1:1080",
            urllib.error.URLError(ssl.SSLError("WRONG_VERSION_NUMBER")))
        with self.assertRaises(ts.TelegramError) as caught:
            fetch("https://api.telegram.org/botX/getMe", {})
        self.assertNotIn("подмен", str(caught.exception).lower())

    def test_ответ_api_остаётся_ответом_api(self):
        """Прокси дошёл, отказал сам Telegram — подменять смысл ошибки нельзя."""
        fetch = self._fetch_through(
            "http://10.0.0.1:3128",
            urllib.error.HTTPError("url", 401, "Unauthorized", {}, None))
        with self.assertRaises(urllib.error.HTTPError):
            fetch("https://api.telegram.org/botX/getMe", {})


class TestТуннельЧерезHttps(unittest.TestCase):
    """CONNECT к HTTPS-прокси. TLS до самого прокси ставится нами.

    Штатный `ProxyHandler` для схемы `https` не годится: CPython шлёт открытый
    CONNECT, и заголовок авторизации ушёл бы в открытую.
    """

    def setUp(self):
        self.ours, self.theirs = socket.socketpair()
        self.addCleanup(self.ours.close)
        self.addCleanup(self.theirs.close)
        self.seen = b""

    def _proxy(self, reply: bytes):
        def serve() -> None:
            try:
                self.seen += self.theirs.recv(4096)
                self.theirs.sendall(reply)
            except OSError:
                return
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 1)

    def test_запрос_connect(self):
        self._proxy(b"HTTP/1.1 200 Connection established\r\n\r\n")
        ts._http_connect(self.ours, "api.telegram.org", 443,
                         ts.parse_proxy("https://p:443"))
        self.assertIn(b"CONNECT api.telegram.org:443 HTTP/1.1", self.seen)
        self.assertIn(b"Host: api.telegram.org:443", self.seen)
        self.assertTrue(self.seen.endswith(b"\r\n\r\n"))

    def test_авторизация_заголовком(self):
        self._proxy(b"HTTP/1.1 200 OK\r\n\r\n")
        ts._http_connect(self.ours, "api.telegram.org", 443,
                         ts.parse_proxy("https://вася:секрет@p:443"))
        expected = base64.b64encode("вася:секрет".encode()).decode()
        self.assertIn(f"Proxy-Authorization: Basic {expected}".encode(), self.seen)

    def test_без_логина_заголовка_нет(self):
        self._proxy(b"HTTP/1.1 200 OK\r\n\r\n")
        ts._http_connect(self.ours, "api.telegram.org", 443,
                         ts.parse_proxy("https://p:443"))
        self.assertNotIn(b"Proxy-Authorization", self.seen)

    def test_отказ_прокси(self):
        self._proxy(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
        with self.assertRaises(ts.TelegramError) as caught:
            ts._http_connect(self.ours, "api.telegram.org", 443,
                             ts.parse_proxy("https://p:443"))
        self.assertIn("407", str(caught.exception))

    def test_мусор_вместо_ответа(self):
        self._proxy(b"\x00\x01\x02\r\n\r\n")
        with self.assertRaises(ts.TelegramError):
            ts._http_connect(self.ours, "api.telegram.org", 443,
                             ts.parse_proxy("https://p:443"))

    def test_https_прокси_не_отдан_ProxyHandler(self):
        """Иначе TLS до прокси не поднимется, а схема обещает обратное."""
        opener = ts._opener_for(ts.parse_proxy("https://p:443"))
        self.assertFalse([h for h in opener.handlers
                          if isinstance(h, urllib.request.ProxyHandler)])

    def test_http_прокси_остаётся_на_ProxyHandler(self):
        """У него TLS до прокси и не предполагается — штатного пути достаточно."""
        opener = ts._opener_for(ts.parse_proxy("http://p:3128"))
        self.assertTrue([h for h in opener.handlers
                         if isinstance(h, urllib.request.ProxyHandler)])


class TestСвойАдресAPI(unittest.TestCase):
    """Реверс-прокси на своём домене: корень адреса, а не прокси."""

    def test_по_умолчанию_телеграм(self):
        self.assertEqual(ts.api_root({}), ts.API_ROOT)

    def test_свой_адрес(self):
        self.assertEqual(ts.api_root({"telegram_api_root": "https://бот.мой:8443"}),
                         "https://бот.мой:8443")

    def test_хвостовой_слэш_убирается(self):
        """Иначе в URL метода приезжает двойной слэш."""
        self.assertEqual(ts.api_root({"telegram_api_root": "https://мой.домен/"}),
                         "https://мой.домен")

    def test_схема_обязательна(self):
        with self.assertRaises(ts.TelegramError):
            ts.api_root({"telegram_api_root": "мой.домен"})

    def test_только_http_и_https(self):
        with self.assertRaises(ts.TelegramError):
            ts.api_root({"telegram_api_root": "socks5://мой.домен"})

    def test_вызов_идёт_в_свой_адрес(self):
        seen = []
        ts.get_me("1:AAA", fetch=lambda url, payload: seen.append(url) or
                  {"ok": True, "result": {}}, api_root="https://мой.домен")
        self.assertEqual(seen, ["https://мой.домен/bot1:AAA/getMe"])

    def test_поллер_берёт_адрес_из_конфига(self):
        cfg = {"telegram": True, "telegram_token": TOKEN,
               "telegram_api_root": "https://мой.домен"}
        seen = []
        with mock.patch.object(ts, "get_updates") as updates:
            updates.side_effect = lambda tok, offset, fetch=None, proxy="", api_root="": (
                seen.append(api_root) or [])
            ts.poll_once(cfg)
        self.assertEqual(seen, ["https://мой.домен"])


class TestХендшейкSocks5(unittest.TestCase):
    """RFC 1928/1929 на паре сокетов: внешней сети здесь нет."""

    def setUp(self):
        self.ours, self.theirs = socket.socketpair()
        self.addCleanup(self.ours.close)
        self.addCleanup(self.theirs.close)
        self.seen = b""

    def _proxy(self, *replies: bytes):
        """Фейковый прокси: отвечает заготовленным, копит полученное."""
        def serve() -> None:
            for reply in replies:
                try:
                    self.seen += self.theirs.recv(4096)
                    self.theirs.sendall(reply)
                except OSError:
                    return
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 1)

    OK = b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50"  # CONNECT удался

    def test_без_авторизации(self):
        self._proxy(b"\x05\x00", self.OK)
        ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                             ts.parse_proxy("socks5h://p:1080"))
        greeting, request = self.seen[:3], self.seen[3:]
        self.assertEqual(greeting[:2], b"\x05\x01")   # одна метода
        self.assertEqual(greeting[2:3], b"\x00")      # без авторизации
        self.assertEqual(request[:4], b"\x05\x01\x00\x03")  # CONNECT по имени
        self.assertIn(b"api.telegram.org", request)
        self.assertEqual(request[-2:], (443).to_bytes(2, "big"))

    def test_socks5h_отдаёт_имя_прокси(self):
        """`socks5h` — имя разрешает прокси: иначе DNS выдаёт сам факт похода."""
        self._proxy(b"\x05\x00", self.OK)
        ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                             ts.parse_proxy("socks5h://p:1080"))
        self.assertIn(b"api.telegram.org", self.seen)

    def test_socks5_разрешает_имя_локально(self):
        self._proxy(b"\x05\x00", self.OK)
        with mock.patch.object(ts.socket, "gethostbyname", return_value="1.2.3.4"):
            ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                                 ts.parse_proxy("socks5://p:1080"))
        self.assertIn(b"\x05\x01\x00\x01" + bytes((1, 2, 3, 4)), self.seen)
        self.assertNotIn(b"api.telegram.org", self.seen)

    def test_логин_и_пароль(self):
        self._proxy(b"\x05\x02", b"\x01\x00", self.OK)
        ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                             ts.parse_proxy("socks5h://вася:секрет@p:1080"))
        user, password = "вася".encode(), "секрет".encode()
        self.assertIn(bytes([1, len(user)]) + user
                      + bytes([len(password)]) + password, self.seen)

    def test_прокси_отверг_методы(self):
        self._proxy(b"\x05\xff")
        with self.assertRaises(ts.TelegramError) as caught:
            ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                                 ts.parse_proxy("socks5h://p:1080"))
        self.assertIn("прокси", str(caught.exception).lower())

    def test_неверный_логин(self):
        self._proxy(b"\x05\x02", b"\x01\x01")
        with self.assertRaises(ts.TelegramError):
            ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                                 ts.parse_proxy("socks5h://вася:нет@p:1080"))

    def test_прокси_не_дал_соединение(self):
        self._proxy(b"\x05\x00", b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
        with self.assertRaises(ts.TelegramError) as caught:
            ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                                 ts.parse_proxy("socks5h://p:1080"))
        self.assertIn("отказ", str(caught.exception).lower())

    def test_прокси_молчит(self):
        """Оборванное соединение — отказ прокси, а не падение с непонятным."""
        self.theirs.close()
        with self.assertRaises(ts.TelegramError):
            ts._socks5_handshake(self.ours, "api.telegram.org", 443,
                                 ts.parse_proxy("socks5h://p:1080"))


class TestПрокиВызовахAPI(unittest.TestCase):
    """Прокси применяется ко всем вызовам, а не к одному «Проверить»."""

    def test_proxy_из_конфига(self):
        self.assertEqual(ts.proxy({"telegram_proxy": " socks5://p:1080 "}),
                         "socks5://p:1080")
        self.assertEqual(ts.proxy({}), "")

    def test_get_me_строит_транспорт_с_прокси(self):
        with mock.patch.object(ts, "fetcher") as build:
            build.return_value = lambda url, payload: {"ok": True, "result": {}}
            ts.get_me(TOKEN, proxy="socks5://p:1080")
        build.assert_called_once_with("socks5://p:1080")

    def test_send_message_строит_транспорт_с_прокси(self):
        with mock.patch.object(ts, "fetcher") as build:
            build.return_value = lambda url, payload: {"ok": True, "result": {}}
            ts.send_message(TOKEN, -100, "текст", proxy="http://p:3128")
        build.assert_called_once_with("http://p:3128")

    def test_get_updates_строит_транспорт_с_прокси(self):
        with mock.patch.object(ts, "fetcher") as build:
            build.return_value = lambda url, payload: {"ok": True, "result": []}
            ts.get_updates(TOKEN, 0, proxy="http://p:3128")
        build.assert_called_once_with("http://p:3128")

    def test_подменённый_транспорт_прокси_не_строит(self):
        """Тесты и вызовы со своим `fetch` в сеть не ходят вовсе."""
        with mock.patch.object(ts, "fetcher") as build:
            ts.get_me(TOKEN, fetch=lambda url, payload: {"ok": True, "result": {}},
                      proxy="http://p:3128")
        build.assert_not_called()


class TestПоллерСПрокси(unittest.TestCase):
    """Поллер берёт прокси из конфига сам: у него формы перед глазами нет."""

    def test_poll_once_передаёт_прокси(self):
        cfg = {"telegram": True, "telegram_token": TOKEN,
               "telegram_proxy": "socks5://p:1080"}
        seen: list[str] = []
        with mock.patch.object(ts, "get_updates") as updates:
            updates.side_effect = lambda tok, offset, fetch=None, proxy="", api_root="": (
                seen.append(proxy) or [])
            ts.poll_once(cfg)
        self.assertEqual(seen, ["socks5://p:1080"])

    def test_битый_прокси_не_валит_поллер(self):
        """Ошибка настройки не должна ронять поток опроса."""
        cfg = {"telegram": True, "telegram_token": TOKEN,
               "telegram_proxy": "socks4://p:1080"}
        self.assertEqual(ts.poll_once(cfg, handle=lambda message: None), 0)


if __name__ == "__main__":
    unittest.main()
