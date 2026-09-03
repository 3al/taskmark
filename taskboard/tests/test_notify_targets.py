"""Кому и куда идёт уведомление по задаче.

Два этажа, и граница между ними — предмет этих тестов. Общая часть знает только
задачу: кто её принёс и кого она касается. Канал чата знает, что с этими людьми
делать и куда писать, — и он же решает, его ли это задача вообще.

Сети здесь нет: проверяется, **кому** уведомление адресовано, а не отправка.
"""

import unittest

from backend import notify_targets as targets
from backend import telegram_notify as channel

OWNER = "kostya"


def task(**over) -> dict:
    """Frontmatter задачи в том виде, в каком его отдаёт парсер."""
    meta = {"id": "TASK-014", "title": "Починить импорт",
            "author": "@petya", "origin": "telegram:-1001234567890"}
    meta.update(over)
    return meta


def cfg(**over) -> dict:
    base = {"telegram": True, "telegram_token": "t", "telegram_username": OWNER}
    base.update(over)
    return base


class TestПричастные(unittest.TestCase):
    """Общая часть: кого задача касается. Про чат она не знает ничего."""

    def test_хозяин_доски_и_автор(self):
        people = targets.people(task())
        self.assertEqual([p["role"] for p in people], ["owner", "author"])
        self.assertEqual(people[1]["value"], "@petya")

    def test_автор_может_отсутствовать(self):
        people = targets.people(task(author="~"))
        self.assertEqual([p["role"] for p in people], ["owner"])

    def test_хозяин_идёт_без_значения(self):
        """Кто такой хозяин, знает канал: у чата ник, у службы — никто."""
        owner = targets.people(task())[0]
        self.assertNotIn("value", owner)

    def test_общая_часть_не_читает_настройки_канала(self):
        """Канал — один из получателей: рядом живёт служба уведомлений."""
        self.assertEqual(targets.people(task()), targets.people(task()))
        for person in targets.people(task()):
            self.assertEqual(set(person) - {"role", "value"}, set())


class TestПроисхождение(unittest.TestCase):
    """Отметка «откуда задача» — общая, разбирает её канал."""

    def test_задача_из_чата(self):
        self.assertEqual(targets.origin(task()), "telegram:-1001234567890")

    def test_задача_с_доски(self):
        self.assertEqual(targets.origin(task(origin="~")), "")
        self.assertEqual(targets.origin({}), "")


class TestАдресатыЧата(unittest.TestCase):
    """Канал чата: кого тегать и в какой чат писать."""

    def test_тегает_хозяина_и_автора(self):
        found = channel.targets(task(), cfg())
        self.assertEqual(found["chat_id"], -1001234567890)
        self.assertEqual(found["mentions"], ["@kostya", "@petya"])

    def test_хозяин_и_автор_одно_лицо_дают_один_тег(self):
        found = channel.targets(task(author="@kostya"), cfg())
        self.assertEqual(found["mentions"], ["@kostya"])

    def test_регистр_ника_не_создаёт_второго_тега(self):
        found = channel.targets(task(author="@Kostya"), cfg())
        self.assertEqual(found["mentions"], ["@kostya"])

    def test_пустой_ник_хозяина_не_ломает_отправку(self):
        found = channel.targets(task(), cfg(telegram_username=""))
        self.assertEqual(found["mentions"], ["@petya"])
        self.assertEqual(found["chat_id"], -1001234567890)

    def test_автор_не_ник_в_теги_не_идёт(self):
        """У задачи с доски автор «доска», у агентской — имя модели."""
        for value in ("доска", "Claude Opus 5", "Иван Иванов", "~"):
            found = channel.targets(task(author=value), cfg())
            self.assertEqual(found["mentions"], ["@kostya"], value)

    def test_задача_не_из_чата_каналу_не_принадлежит(self):
        """Охват — только задачи, заведённые через чат."""
        self.assertIsNone(channel.targets(task(origin="~"), cfg()))
        self.assertIsNone(channel.targets(task(origin="board"), cfg()))

    def test_чужое_происхождение_каналу_не_принадлежит(self):
        self.assertIsNone(channel.targets(task(origin="slack:C123"), cfg()))

    def test_битая_отметка_не_ломает_разбор(self):
        for broken in ("telegram:", "telegram:не-число", "telegram"):
            self.assertIsNone(channel.targets(task(origin=broken), cfg()), broken)

    def test_выключенная_интеграция_адресатов_не_даёт(self):
        self.assertIsNone(channel.targets(task(), cfg(telegram=False)))
        self.assertIsNone(channel.targets(task(), cfg(telegram_token="")))


class TestОтметкаПриЗаведении(unittest.TestCase):
    """Происхождение проставляется там же, где задача заводится."""

    def test_чат_собирает_отметку_из_id(self):
        self.assertEqual(channel.origin_of(-1001234567890),
                         "telegram:-1001234567890")


if __name__ == "__main__":
    unittest.main()
