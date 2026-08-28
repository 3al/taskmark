"""Слой источника Telegram: клиент Bot API, курсор апдейтов, фоновый поллер.

Сеть здесь не трогается ни разу: и клиент, и поллер принимают функцию запроса
параметром — тесты подставляют свою. Так проверяется главное: пока возможность
выключена, запрос **не уходит** вовсе.

Файл состояния (`~/.taskboard/telegram.json`) подменяется временной папкой:
глобальное состояние пользователя тесты трогать не должны.
"""

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from backend import telegram_source as ts

TOKEN = "123:AAH-test"


def update(update_id: int, text: str = "привет", chat_id: int = -100,
           chat_title: str = "Разработка", message_id: int | None = None,
           username: str = "kostya") -> dict:
    """Апдейт в том виде, в каком его отдаёт Bot API."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id if message_id is not None else update_id,
            "text": text,
            "chat": {"id": chat_id, "title": chat_title, "type": "group"},
            "from": {"id": 1, "username": username},
        },
    }


class Fake:
    """Транспорт: отдаёт заготовленные ответы и помнит, о чём его просили."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, payload: dict) -> dict:
        self.calls.append((url, payload))
        if not self.responses:
            return {"ok": True, "result": []}
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def methods(self) -> list[str]:
        return [url.rsplit("/", 1)[-1] for url, _ in self.calls]


class Base(unittest.TestCase):
    """Подменяет файл состояния временной папкой."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        patches = [
            mock.patch.object(ts, "GLOBAL_DIR", root),
            mock.patch.object(ts, "STATE_FILE", root / "telegram.json"),
            mock.patch.object(ts, "_SEEN_CHATS", {}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def cfg(self, **over) -> dict:
        base = {"telegram": True, "telegram_token": TOKEN}
        base.update(over)
        return base


class TestEnabled(Base):
    """Выключенная возможность не существует: без неё в сеть никто не ходит."""

    def test_по_умолчанию_выключена(self):
        self.assertFalse(ts.enabled({}))

    def test_флага_без_токена_мало(self):
        self.assertFalse(ts.enabled({"telegram": True, "telegram_token": ""}))

    def test_токена_без_флага_мало(self):
        self.assertFalse(ts.enabled({"telegram": False, "telegram_token": TOKEN}))

    def test_включена_при_флаге_и_токене(self):
        self.assertTrue(ts.enabled(self.cfg()))


class TestClient(Base):
    """Клиент Bot API: адрес, параметры, разбор ответа."""

    def test_get_updates_отдаёт_нормализованные_сообщения(self):
        fake = Fake({"ok": True, "result": [update(5, "первое"), update(6, "второе")]})
        msgs = ts.get_updates(TOKEN, offset=5, fetch=fake)
        self.assertEqual([m["update_id"] for m in msgs], [5, 6])
        self.assertEqual(msgs[0]["text"], "первое")
        self.assertEqual(msgs[0]["chat_id"], -100)
        self.assertEqual(msgs[0]["chat_title"], "Разработка")
        self.assertEqual(msgs[0]["username"], "kostya")

    def test_токен_в_адресе_а_offset_в_запросе(self):
        fake = Fake({"ok": True, "result": []})
        ts.get_updates(TOKEN, offset=42, fetch=fake)
        url, payload = fake.calls[0]
        self.assertIn(TOKEN, url)
        self.assertTrue(url.endswith("/getUpdates"))
        self.assertEqual(payload["offset"], 42)

    def test_сообщение_без_текста_пропускается(self):
        """Вход в группу, картинка, стикер — апдейты без текста нам не нужны."""
        silent = {"update_id": 7, "message": {"message_id": 7,
                                              "chat": {"id": -100, "title": "Р"}}}
        fake = Fake({"ok": True, "result": [silent, update(8)]})
        msgs = ts.get_updates(TOKEN, fetch=fake)
        self.assertEqual([m["update_id"] for m in msgs], [8])

    def test_ошибка_api_поднимается_наверх(self):
        fake = Fake({"ok": False, "description": "Unauthorized"})
        with self.assertRaises(ts.TelegramError):
            ts.get_updates(TOKEN, fetch=fake)

    def test_send_message_передаёт_ответ_на_сообщение(self):
        fake = Fake({"ok": True, "result": {}})
        ts.send_message(TOKEN, -100, "TASK-1 добавлена", reply_to=17, fetch=fake)
        url, payload = fake.calls[0]
        self.assertTrue(url.endswith("/sendMessage"))
        self.assertEqual(payload["chat_id"], -100)
        self.assertEqual(payload["text"], "TASK-1 добавлена")
        self.assertEqual(payload["reply_to_message_id"], 17)

    def test_get_me_отдаёт_имя_бота(self):
        fake = Fake({"ok": True, "result": {"username": "team_tasks_bot"}})
        self.assertEqual(ts.get_me(TOKEN, fetch=fake)["username"], "team_tasks_bot")


class TestCursor(Base):
    """Курсор: что обработано, то и подтверждено — и переживает перезапуск."""

    def test_курсор_двигается_по_обработанным(self):
        fake = Fake({"ok": True, "result": [update(5), update(6)]})
        seen = []
        ts.poll_once(self.cfg(), handle=seen.append, fetch=fake)
        self.assertEqual([m["update_id"] for m in seen], [5, 6])
        self.assertEqual(ts.read_state()["offset"], 7)

    def test_курсор_переживает_перезапуск(self):
        ts.write_state({"offset": 99})
        fake = Fake({"ok": True, "result": []})
        ts.poll_once(self.cfg(), handle=lambda m: None, fetch=fake)
        self.assertEqual(fake.calls[0][1]["offset"], 99)

    def test_один_апдейт_не_выдаётся_дважды(self):
        fake = Fake({"ok": True, "result": [update(5)]},
                    {"ok": True, "result": []})
        seen = []
        ts.poll_once(self.cfg(), handle=seen.append, fetch=fake)
        ts.poll_once(self.cfg(), handle=seen.append, fetch=fake)
        self.assertEqual([m["update_id"] for m in seen], [5])
        self.assertEqual(fake.calls[1][1]["offset"], 6)

    def test_ошибка_сети_не_двигает_курсор(self):
        ts.write_state({"offset": 10})
        fake = Fake(urllib.error.URLError("сеть отвалилась"))
        ts.poll_once(self.cfg(), handle=lambda m: None, fetch=fake)
        self.assertEqual(ts.read_state()["offset"], 10)

    def test_без_обработчика_курсор_стоит(self):
        """Слой источника один, обработчика ещё нет — сообщения не теряем."""
        fake = Fake({"ok": True, "result": [update(5)]})
        ts.poll_once(self.cfg(), handle=None, fetch=fake)
        self.assertEqual(ts.read_state().get("offset", 0), 0)

    def test_упавший_обработчик_не_держит_очередь(self):
        """Иначе одно неудачное сообщение навсегда закрывает вход."""
        def explode(msg):
            raise ValueError("разбор не удался")

        fake = Fake({"ok": True, "result": [update(5)]})
        ts.poll_once(self.cfg(), handle=explode, fetch=fake)
        self.assertEqual(ts.read_state()["offset"], 6)


class TestDisabled(Base):
    """Пока возможность не включена, обращений в сеть нет вовсе."""

    def test_poll_once_не_ходит_в_сеть(self):
        fake = Fake({"ok": True, "result": [update(5)]})
        ts.poll_once({"telegram": False}, handle=lambda m: None, fetch=fake)
        self.assertEqual(fake.calls, [])

    def test_поток_не_стартует(self):
        fake = Fake({"ok": True, "result": []})
        stop = ts.start_polling({"telegram": False}, handle=lambda m: None,
                                fetch=fake)
        stop()
        self.assertEqual(fake.calls, [])


class TestSeenChats(Base):
    """Чаты, которые бот видел: из них человек выберет нужный без id."""

    def test_чат_запоминается_по_имени(self):
        fake = Fake({"ok": True, "result": [update(5, chat_id=-77,
                                                   chat_title="Разработка")]})
        ts.poll_once(self.cfg(), handle=lambda m: None, fetch=fake)
        self.assertEqual(ts.seen_chats(), [{"id": -77, "title": "Разработка"}])

    def test_чат_переживает_перезапуск(self):
        """Иначе сохранённая привязка становится невидимой: человек открывает
        настройки и видит пустой шаг, будто ничего не настраивал."""
        fake = Fake({"ok": True, "result": [update(5, chat_id=-77,
                                                   chat_title="Разработка")]})
        ts.poll_once(self.cfg(), handle=lambda m: None, fetch=fake)
        _SEEN_CHATS = ts._SEEN_CHATS
        _SEEN_CHATS.clear()  # как после перезапуска сервера
        self.assertEqual(ts.seen_chats(), [{"id": -77, "title": "Разработка"}])

    def test_имя_чата_спрашивается_у_api_и_запоминается(self):
        """У чата, привязанного до появления сохранения имён, взять имя больше
        неоткуда — иначе в настройках останется голый id."""
        fake = Fake({"ok": True, "result": {"id": -77, "title": "Разработка"}})
        self.assertEqual(ts.chat_title(self.cfg(), -77, fetch=fake), "Разработка")
        self.assertEqual(fake.methods, ["getChat"])
        self.assertEqual(ts.read_state()["chats"]["-77"], "Разработка")

    def test_известное_имя_в_сеть_не_ходит(self):
        ts.write_state({"chats": {"-77": "Разработка"}})
        fake = Fake()
        self.assertEqual(ts.chat_title(self.cfg(), -77, fetch=fake), "Разработка")
        self.assertEqual(fake.calls, [])

    def test_отказ_api_оставляет_строку_с_id(self):
        fake = Fake({"ok": False, "description": "chat not found"})
        self.assertEqual(ts.chat_title(self.cfg(), -77, fetch=fake), "")

    def test_чат_не_дублируется(self):
        fake = Fake({"ok": True, "result": [update(5, chat_id=-77),
                                            update(6, chat_id=-77)]})
        ts.poll_once(self.cfg(), handle=lambda m: None, fetch=fake)
        self.assertEqual(len(ts.seen_chats()), 1)


if __name__ == "__main__":
    unittest.main()
