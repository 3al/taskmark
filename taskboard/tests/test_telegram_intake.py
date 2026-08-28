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
        parsed = intake.parse("#задача Сделать стоп-фильтр @kostya", self.cfg())
        self.assertEqual(parsed["title"], "Сделать стоп-фильтр")
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
            "#задача Разработать стоп-фильтр. Снимать котировки на быстром рынке @kostya",
            self.cfg())
        self.assertEqual(parsed["title"], "Разработать стоп-фильтр")
        self.assertEqual(parsed["description"], "Снимать котировки на быстром рынке")

    def test_точка_в_конце_строки_в_заголовок_не_идёт(self):
        parsed = intake.parse("#задача Разработать стоп-фильтр. @kostya", self.cfg())
        self.assertEqual(parsed["title"], "Разработать стоп-фильтр")
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
            "#задача Сделать стоп-фильтр. Пороги согласовать @kostya\nи ещё строка",
            self.cfg())
        self.assertEqual(parsed["title"], "Сделать стоп-фильтр")
        self.assertEqual(parsed["description"], "Пороги согласовать\nи ещё строка")

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
        self.projects = [{"name": "Прогрессор", "tasks_dir": str(self.tasks)},
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
                "telegram_chats": {"-100": "Прогрессор"}}
        base.update(over)
        return base

    def argv(self) -> list[str]:
        return json.loads((self.tasks / "argv.json").read_text(encoding="utf-8"))

    def handle(self, msg, **over):
        return intake.handle(msg, cfg=self.cfg(**over),
                             projects=self.projects, send=self.send)

    def test_задача_создаётся_в_привязанном_проекте(self):
        result = self.handle(message("#задача Сделать стоп-фильтр @kostya"))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["id"], "TASK-042")
        argv = self.argv()
        self.assertEqual(argv[argv.index("-t") + 1], "Сделать стоп-фильтр")

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
        self.handle(message("#задача Сделать стоп-фильтр @kostya"))
        chat_id, text, reply_to = self.sent[0]
        self.assertEqual(chat_id, -100)
        self.assertEqual(reply_to, 5)
        self.assertIn("TASK-042", text)
        self.assertIn("Сделать стоп-фильтр", text)
        self.assertIn("Прогрессор", text)
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
                             telegram_chats={"-100": ["Прогрессор", "Второй"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["project"], "Второй")

    def test_привязка_строкой_работает_как_прежде(self):
        result = self.handle(message("#задача Сделать X @kostya"),
                             telegram_chats={"-100": "Прогрессор"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["project"], "Прогрессор")

    def test_суффикс_не_достаёт_проект_мимо_привязки(self):
        """Право писать в проект даёт привязка чата, а не реестр."""
        result = self.handle(message("#задача-Второй Сделать X @kostya"),
                             telegram_chats={"-100": "Прогрессор"})
        self.assertFalse(result["ok"])
        self.assertFalse((self.tasks / "argv.json").exists())
        self.assertIn("нельзя писать", self.sent[0][1].lower())

    def test_неизвестный_проект_в_суффиксе_даёт_список_привязанных(self):
        result = self.handle(message("#задача-нетакого Сделать X @kostya"))
        self.assertFalse(result["ok"])
        self.assertFalse((self.tasks / "argv.json").exists())
        self.assertIn("Прогрессор", self.sent[0][1])

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


if __name__ == "__main__":
    unittest.main()
