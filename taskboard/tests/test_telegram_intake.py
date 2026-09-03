"""Разбор сообщения чата и создание задачи на доске.

Сеть не трогается: ответ в чат подменяется функцией-приёмником, а создание
задачи идёт через скрипт-заглушку проекта — как в тестах `create_task_runner`.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import telegram_intake as intake
from backend import telegram_source as ts

STUB = '''import json, sys
from pathlib import Path
Path(__file__).parent.joinpath("argv.json").write_text(
    json.dumps(sys.argv[1:], ensure_ascii=False), encoding="utf-8")
print("ID: TASK-042")
'''


def message(text: str, chat_id: int = -100, update_id: int = 5,
            message_id: int = 5, chat_title: str = "Разработка") -> dict:
    """Сообщение в том виде, в каком его отдаёт слой источника."""
    return {"update_id": update_id, "message_id": message_id,
            "chat_id": chat_id, "chat_title": chat_title,
            "text": text, "username": "author"}


class ParseTest(unittest.TestCase):
    """Разбор текста: что здесь задача, кому она и в какой проект."""

    def cfg(self, **over) -> dict:
        base = {"telegram_tag": "задача", "telegram_username": "kostya"}
        base.update(over)
        return base

    def test_хэштег_текст_и_исполнитель(self):
        parsed = intake.parse("#задача Обновить документацию @kostya", self.cfg())
        self.assertEqual(parsed["title"], "Обновить документацию")
        self.assertEqual(parsed["mentions"], ["kostya"])
        self.assertIsNone(parsed["project"])

    def test_без_хэштега_не_задача(self):
        self.assertIsNone(intake.parse("просто болтовня @kostya", self.cfg()))

    def test_хэштег_в_середине_сообщения(self):
        parsed = intake.parse("@kostya глянь #задача поправить импорт", self.cfg())
        self.assertEqual(parsed["title"], "глянь поправить импорт")

    def test_суффикс_называет_проект(self):
        parsed = intake.parse("#задача-progressor Сделать X @kostya", self.cfg())
        self.assertEqual(parsed["project"], "progressor")
        self.assertEqual(parsed["title"], "Сделать X")

    def test_хэштег_настраивается(self):
        parsed = intake.parse("#task Do it @kostya", self.cfg(telegram_tag="task"))
        self.assertEqual(parsed["title"], "Do it")

    def test_многострочное_сообщение_даёт_описание(self):
        parsed = intake.parse("#задача Сделать X @kostya\nподробности тут\nи тут",
                              self.cfg())
        self.assertEqual(parsed["title"], "Сделать X")
        self.assertEqual(parsed["description"], "подробности тут\nи тут")

    def test_первое_предложение_становится_заголовком(self):
        parsed = intake.parse(
            "#задача Обновить документацию. Начать с раздела про фильтры @kostya",
            self.cfg())
        self.assertEqual(parsed["title"], "Обновить документацию")
        self.assertEqual(parsed["description"], "Начать с раздела про фильтры")

    def test_точка_в_конце_строки_в_заголовок_не_идёт(self):
        parsed = intake.parse("#задача Обновить документацию. @kostya", self.cfg())
        self.assertEqual(parsed["title"], "Обновить документацию")
        self.assertEqual(parsed["description"], "")

    def test_номер_версии_строку_не_режет(self):
        """«1.2» — не конец предложения: точке нужен пробел за собой."""
        parsed = intake.parse("#задача В версии 1.2 сломался импорт @kostya",
                              self.cfg())
        self.assertEqual(parsed["title"], "В версии 1.2 сломался импорт")

    def test_сокращение_заголовком_не_становится(self):
        """«т.е.» короче предложения — режем по следующей точке или не режем."""
        parsed = intake.parse("#задача т.е. надо поправить импорт @kostya",
                              self.cfg())
        self.assertEqual(parsed["title"], "т.е. надо поправить импорт")

    def test_предложение_и_перенос_строки_работают_вместе(self):
        parsed = intake.parse(
            "#задача Обновить документацию. Начать с фильтров @kostya\nи ещё строка",
            self.cfg())
        self.assertEqual(parsed["title"], "Обновить документацию")
        self.assertEqual(parsed["description"], "Начать с фильтров\nи ещё строка")

    def test_длинный_заголовок_уезжает_в_описание(self):
        """Заголовок идёт в имя файла — простыню туда класть нельзя."""
        long = "очень длинная мысль " * 20
        parsed = intake.parse(f"#задача {long} @kostya", self.cfg())
        self.assertLessEqual(len(parsed["title"]), intake.TITLE_LIMIT)
        self.assertIn("очень длинная мысль", parsed["description"])

    def test_пустой_текст_после_разбора_не_задача(self):
        self.assertIsNone(intake.parse("#задача @kostya", self.cfg()))


class ForMeTest(unittest.TestCase):
    """Задачу создаёт только тот, кого тегнули: чужие теги — не наше дело."""

    def cfg(self, **over) -> dict:
        base = {"telegram_tag": "задача", "telegram_username": "kostya"}
        base.update(over)
        return base

    def test_тегнули_меня(self):
        parsed = intake.parse("#задача Сделать X @kostya", self.cfg())
        self.assertTrue(intake.is_for_me(parsed, self.cfg()))

    def test_собачка_в_настройках_не_мешает(self):
        parsed = intake.parse("#задача Сделать X @kostya", self.cfg())
        self.assertTrue(intake.is_for_me(parsed, self.cfg(telegram_username="@kostya")))

    def test_регистр_ника_не_важен(self):
        parsed = intake.parse("#задача Сделать X @KosTya", self.cfg())
        self.assertTrue(intake.is_for_me(parsed, self.cfg()))

    def test_тегнули_другого(self):
        parsed = intake.parse("#задача Сделать X @petya", self.cfg())
        self.assertFalse(intake.is_for_me(parsed, self.cfg()))

    def test_тегнули_двоих_включая_меня(self):
        parsed = intake.parse("#задача Сделать X @petya @kostya", self.cfg())
        self.assertTrue(intake.is_for_me(parsed, self.cfg()))

    def test_никого_не_тегнули(self):
        parsed = intake.parse("#задача Сделать X", self.cfg())
        self.assertFalse(intake.is_for_me(parsed, self.cfg()))

    def test_свой_ник_не_задан(self):
        parsed = intake.parse("#задача Сделать X @kostya", self.cfg())
        self.assertFalse(intake.is_for_me(parsed, self.cfg(telegram_username="")))


class HandleTest(unittest.TestCase):
    """Создание задачи: проект, ответ в чат, защита от повторной обработки."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)

        self.tasks = root / "progressor" / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "create_task.py").write_text(STUB, encoding="utf-8")
        # Второй проект реестра — чтобы проверить, что суффикс до него не
        # дотягивается, пока чат к нему не привязан
        self.other = root / "other" / "tasks"
        self.other.mkdir(parents=True)
        (self.other / "create_task.py").write_text(STUB, encoding="utf-8")
        self.projects = [{"name": "Первый", "tasks_dir": str(self.tasks)},
                         {"name": "Второй", "tasks_dir": str(self.other)}]

        state = root / "state"
        state.mkdir()
        patches = [
            mock.patch.object(ts, "GLOBAL_DIR", state),
            mock.patch.object(ts, "STATE_FILE", state / "telegram.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.sent: list[tuple] = []

    def send(self, chat_id, text, reply_to=None):
        self.sent.append((chat_id, text, reply_to))

    def cfg(self, **over) -> dict:
        base = {"telegram": True, "telegram_token": "t",
                "telegram_tag": "задача", "telegram_username": "kostya",
                "telegram_chats": {"-100": "Первый"}}
        base.update(over)
        return base

    def argv(self) -> list[str]:
        return json.loads((self.tasks / "argv.json").read_text(encoding="utf-8"))

    def handle(self, msg, **over):
        return intake.handle(msg, cfg=self.cfg(**over),
                             projects=self.projects, send=self.send)

    def test_задача_создаётся_в_привязанном_проекте(self):
        result = self.handle(message("#задача Обновить документацию @kostya"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["id"], "TASK-042")
        argv = self.argv()
        self.assertEqual(argv[argv.index("-t") + 1], "Обновить документацию")

    def test_критерии_из_чата_не_выдумываются(self):
        """Скрипт без -c подставляет TDD — но в чате про него никто не говорил."""
        self.handle(message("#задача Сделать X @kostya"))
        argv = self.argv()
        self.assertIn("-c", argv)
        self.assertEqual(argv[argv.index("-c") + 1], "")

    def test_задача_ложится_в_свою_рубрику(self):
        """«В конец раздела приёма» означает «внутрь последней рубрики» —
        поэтому у задач из чата рубрика своя, и она же сигнал «разбери»."""
        self.handle(message("#задача Сделать X @kostya"))
        argv = self.argv()
        self.assertIn("--section", argv)
        self.assertEqual(argv[argv.index("--section") + 1], intake.CHAT_SECTION)

    def test_тип_из_чата_не_выдумывается(self):
        """Вид работы в сообщении не назван — ставит его тот, кто возьмёт."""
        self.handle(message("#задача Сделать X @kostya"))
        argv = self.argv()
        self.assertIn("--type", argv)
        self.assertEqual(argv[argv.index("--type") + 1], "")

    def test_в_ответе_номер_заголовок_и_проект(self):
        self.handle(message("#задача Обновить документацию @kostya"))
        chat_id, text, reply_to = self.sent[0]
        self.assertEqual(chat_id, -100)
        self.assertEqual(reply_to, 5)
        self.assertIn("TASK-042", text)
        self.assertIn("Обновить документацию", text)
        self.assertIn("Первый", text)
        self.assertIn("бэклог", text.lower())

    def test_чужой_тег_проходит_молча(self):
        result = self.handle(message("#задача Сделать X @petya"))
        self.assertFalse(result["ok"])
        self.assertEqual(self.sent, [])
        self.assertFalse((self.tasks / "argv.json").exists())

    def test_болтовня_проходит_молча(self):
        result = self.handle(message("а помните мы обсуждали @kostya"))
        self.assertFalse(result["ok"])
        self.assertEqual(self.sent, [])

    def test_чат_без_привязки_отвечает_ошибкой(self):
        result = self.handle(message("#задача Сделать X @kostya", chat_id=-999))
        self.assertFalse(result["ok"])
        self.assertFalse((self.tasks / "argv.json").exists())
        self.assertIn("не привязан", self.sent[0][1].lower())

    def test_суффикс_выбирает_среди_привязанных(self):
        """Чат ведёт в два проекта: первый по умолчанию, суффикс берёт второй."""
        result = self.handle(message("#задача-Второй Сделать X @kostya"),
                             telegram_chats={"-100": ["Первый", "Второй"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["project"], "Второй")

    def test_суффиксом_можно_назвать_и_основной_проект(self):
        """Он в списке привязанных первым — запрещать его незачем."""
        result = self.handle(message("#задача-Первый Сделать X @kostya"),
                             telegram_chats={"-100": ["Первый", "Второй"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["project"], "Первый")

    def test_привязка_строкой_работает_как_прежде(self):
        result = self.handle(message("#задача Сделать X @kostya"),
                             telegram_chats={"-100": "Первый"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["project"], "Первый")

    def test_суффикс_не_достаёт_проект_мимо_привязки(self):
        """Право писать в проект даёт привязка чата, а не реестр."""
        result = self.handle(message("#задача-Второй Сделать X @kostya"),
                             telegram_chats={"-100": "Первый"})
        self.assertFalse(result["ok"])
        self.assertFalse((self.tasks / "argv.json").exists())
        self.assertIn("нельзя писать", self.sent[0][1].lower())

    def test_неизвестный_проект_в_суффиксе_даёт_список_привязанных(self):
        result = self.handle(message("#задача-нетакого Сделать X @kostya"))
        self.assertFalse(result["ok"])
        self.assertFalse((self.tasks / "argv.json").exists())
        self.assertIn("Первый", self.sent[0][1])

    def test_повторная_обработка_дубля_не_создаёт(self):
        """Падение между созданием и ответом не должно родить вторую задачу."""
        msg = message("#задача Сделать X @kostya")
        self.handle(msg)
        (self.tasks / "argv.json").unlink()
        result = self.handle(msg)
        self.assertTrue(result["ok"])
        self.assertEqual(result["id"], "TASK-042")
        self.assertFalse((self.tasks / "argv.json").exists(),
                         "задача создана второй раз")
        self.assertEqual(len(self.sent), 2, "ответ должен уйти повторно")

    def test_разные_сообщения_создают_разные_задачи(self):
        self.handle(message("#задача Первая @kostya", update_id=5))
        first = self.argv()
        self.handle(message("#задача Вторая @kostya", update_id=6))
        self.assertNotEqual(first, self.argv())


class AuthorFromChatTest(HandleTest):
    """Автором задачи становится тот, кто бросил её в чат (TASK-220).

    Имя берётся из сообщения, а не выдумывается: ник, а если его нет —
    отображаемое имя, а если и его нет — числовой идентификатор. Пустым автор
    не остаётся: «откуда пришла задача» — такой же ответ, как имя человека.
    """

    def author_of(self, **sender) -> str:
        msg = {**message("#задача Сделать X @kostya"), **sender}
        self.handle(msg)
        argv = self.argv()
        self.assertIn("--author", argv, "автор не передан скрипту создания")
        return argv[argv.index("--author") + 1]

    def test_автором_становится_ник_отправителя(self):
        self.assertEqual("@author", self.author_of(username="author"))

    def test_без_ника_берётся_отображаемое_имя(self):
        """Ник в телеграме есть не у всех, а имя показывают всем."""
        self.assertEqual("Иван Иванов",
                         self.author_of(username="", sender_name="Иван Иванов"))

    def test_без_ника_и_имени_остаётся_номер(self):
        """Последний рубеж: по номеру человека хотя бы отличают от другого."""
        self.assertEqual("77", self.author_of(username="", sender_name="",
                                              sender_id=77))

    def test_ник_приходит_с_собачкой_один_раз(self):
        """Bot API отдаёт ник без «@» — приписываем ровно одну."""
        self.assertEqual("@author", self.author_of(username="@author"))


class SenderFieldsTest(unittest.TestCase):
    """Слой источника отдаёт наверх всё, из чего складывается автор."""

    def raw(self, **sender) -> dict:
        return {"update_id": 1,
                "message": {"message_id": 2, "chat": {"id": -100, "title": "Ч"},
                            "from": sender, "text": "привет"}}

    def test_ник_имя_и_номер_доезжают(self):
        msg = ts._message_of(self.raw(username="ivan", first_name="Иван",
                                      last_name="Иванов", id=77))
        self.assertEqual("ivan", msg["username"])
        self.assertEqual("Иван Иванов", msg["sender_name"])
        self.assertEqual(77, msg["sender_id"])

    def test_только_имя_без_фамилии(self):
        msg = ts._message_of(self.raw(first_name="Иван", id=77))
        self.assertEqual("Иван", msg["sender_name"])

    def test_отправителя_нет_вовсе(self):
        """Сообщение из канала приходит без `from` — полям остаётся пустота."""
        msg = ts._message_of(self.raw())
        self.assertEqual("", msg["username"])
        self.assertEqual("", msg["sender_name"])
        self.assertIsNone(msg["sender_id"])


class ManyMentionsTest(HandleTest):
    """Задача заводится на одного: двое тегнутых — отказ, а не две задачи.

    Отвечают **все тегнутые**: бот у каждого свой, и каждый разбирает сообщение
    сам. Две одинаковых жалобы в чате — допустимая цена: так виднее, что
    сообщение не сработало ни у кого.
    """

    def created(self) -> bool:
        return (self.tasks / "argv.json").is_file()

    def test_двое_тегнутых_задачу_не_создают(self):
        result = self.handle(message("#задача Сделать X @kostya @ivan"))
        self.assertFalse(result["ok"], result)
        self.assertFalse(self.created(), "задача создана, хотя тегнуты двое")

    def test_двое_тегнутых_получают_ответ(self):
        self.handle(message("#задача Сделать X @kostya @ivan"))
        self.assertEqual(1, len(self.sent), "ответа в чат нет")
        self.assertIn("одного", self.sent[0][1])

    def test_ответ_привязан_к_исходному_сообщению(self):
        """Реплаем, как и остальные ответы: в живом чате иначе не найти повод."""
        self.handle(message("#задача Сделать X @kostya @ivan", message_id=17))
        self.assertEqual(17, self.sent[0][2])

    def test_тегнули_двоих_без_меня_молчим(self):
        """Чужой тег не мой повод: жалуются те, кого тегнули."""
        result = self.handle(message("#задача Сделать X @ivan @petya"))
        self.assertFalse(result["ok"])
        self.assertEqual([], self.sent, "ответ ушёл на чужое сообщение")
        self.assertFalse(self.created())

    def test_трое_тегнутых_тоже_отказ(self):
        self.handle(message("#задача Сделать X @kostya @ivan @petya"))
        self.assertFalse(self.created())
        self.assertEqual(1, len(self.sent))

    def test_один_тегнутый_работает_как_прежде(self):
        result = self.handle(message("#задача Сделать X @kostya"))
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.created())

    def test_повтор_того_же_сообщения_снова_отвечает_отказом(self):
        """Задачи нет — значит и помнить нечего: отказ повторяется."""
        msg = message("#задача Сделать X @kostya @ivan")
        self.handle(msg)
        self.handle(msg)
        self.assertEqual(2, len(self.sent))
        self.assertFalse(self.created())


class ReplyPathTest(HandleTest):
    """Ответ уходит тем же путём, что и приём.

    Приём идёт через прокси, а ответ уходил напрямую: за прокси задача
    создавалась, подтверждение не доходило, и человек присылал сообщение заново —
    получая **вторую** задачу, потому что дедупликация ловит только повтор того
    же апдейта.
    """

    def test_ответ_идёт_через_прокси_и_свой_адрес(self):
        with mock.patch.object(ts, "send_message") as sent:
            intake.handle(message("#задача Сделать X @kostya"),
                          cfg=self.cfg(telegram_proxy="socks5://p:1080",
                                       telegram_api_root="https://мой.домен"),
                          projects=self.projects)
        self.assertTrue(sent.called, "ответ в чат не отправлялся вовсе")
        kwargs = sent.call_args.kwargs
        self.assertEqual(kwargs.get("proxy"), "socks5://p:1080")
        self.assertEqual(kwargs.get("api_root"), "https://мой.домен")

    def test_без_настроек_путь_прежний(self):
        with mock.patch.object(ts, "send_message") as sent:
            intake.handle(message("#задача Сделать X @kostya"),
                          cfg=self.cfg(), projects=self.projects)
        kwargs = sent.call_args.kwargs
        self.assertEqual(kwargs.get("proxy"), "")
        self.assertEqual(kwargs.get("api_root"), ts.API_ROOT)


if __name__ == "__main__":


    unittest.main()
