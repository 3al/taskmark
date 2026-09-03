"""О каких статусах уведомляют: признак `notify` у этапа маршрута.

Настройка живёт там же, где остальной жизненный цикл, и устроена как галочки
исполнителя: список ключей в конфиге проекта, признак у каждого статуса.

Разница одна и она важна — **дефолт вычисляется, а не записывается**. Пока
человек не трогал галочки, отмечен конец маршрута **этого** проекта; зашивать
имя `done` нельзя (состав статусов у проектов свой), записывать дефолт в конфиг
тоже — замороженное значение перестаёт следовать за правкой маршрута.
"""

import unittest

from backend import pipeline_sources
from backend.statuses import load_pipeline


def cfg(**over) -> dict:
    base = {"pipeline": ["backlog", "todo", "development", "done", "cancelled"],
            "statuses": {"cancelled": {"offramp": True}}}
    base.update(over)
    return base


def notified(config: dict) -> list[str]:
    return [s["key"] for s in load_pipeline(config).statuses() if s.get("notify")]


class TestДефолт(unittest.TestCase):
    """Ключа нет — отмечен конец маршрута, вычисленный из пайплайна."""

    def test_конец_маршрута(self):
        self.assertEqual(notified(cfg()), ["done"])

    def test_съезд_концом_маршрута_не_считается(self):
        """Отмена — не финиш: из неё не возвращаются, но и не приходят по плану."""
        self.assertNotIn("cancelled", notified(cfg()))

    def test_дефолт_следует_за_маршрутом(self):
        """Записанный в конфиг дефолт отстал бы от правки пайплайна."""
        other = cfg(pipeline=["backlog", "development", "released", "cancelled"])
        self.assertEqual(notified(other), ["released"])

    def test_маршрут_из_одних_съездов_не_отмечает_никого(self):
        """Конец маршрута ищется среди тех, куда приходят по плану."""
        only_offramp = cfg(pipeline=["cancelled"],
                           statuses={"cancelled": {"offramp": True}})
        self.assertEqual(notified(only_offramp), [])


class TestОтмена(unittest.TestCase):
    """Съезд с маршрута не в дефолте, но отметить его можно.

    «Вашу задачу отменили» — ровно то, что постановщику из чата важно узнать, и
    узнать больше неоткуда: доска у него не открыта. Аргумент «отменённой
    задачей никто не занимается» — про исполнителя, а не про уведомление.
    """

    def test_по_умолчанию_молчим(self):
        self.assertNotIn("cancelled", notified(cfg()))

    def test_отмену_можно_отметить(self):
        self.assertEqual(notified(cfg(notify_statuses=["cancelled"])), ["cancelled"])

    def test_отмена_вместе_с_концом_маршрута(self):
        chosen = cfg(notify_statuses=["done", "cancelled"])
        self.assertEqual(notified(chosen), ["done", "cancelled"])


class TestВыборЧеловека(unittest.TestCase):
    """Ключ есть — отмечено ровно то, что он отметил."""

    def test_перечисленные_статусы(self):
        chosen = cfg(notify_statuses=["development", "done"])
        self.assertEqual(notified(chosen), ["development", "done"])

    def test_пустой_список_значит_нигде(self):
        """Это выбор «молчать», и от «не трогал» он отличим."""
        self.assertEqual(notified(cfg(notify_statuses=[])), [])

    def test_неизвестный_ключ_никого_не_отмечает(self):
        self.assertEqual(notified(cfg(notify_statuses=["нет-такого"])), [])


class TestПереносИсточником(unittest.TestCase):
    """Заполнение ЖЦ из другого проекта переносит и эти галочки."""

    def test_ключ_входит_в_жизненный_цикл(self):
        self.assertIn("notify_statuses", pipeline_sources.LIFECYCLE_KEYS)

    def test_источник_отдаёт_набор(self):
        source = pipeline_sources._source(
            "project", "Соседний", "", cfg(notify_statuses=["development"]))
        self.assertEqual(source["notify_statuses"], ["development"])

    def test_источник_без_набора_отдаёт_пустое(self):
        """Пресеты уведомлений не несут: маршрут заменяется целиком."""
        source = pipeline_sources._source("preset", "Пресет", "", cfg())
        self.assertEqual(source["notify_statuses"], [])


if __name__ == "__main__":
    unittest.main()
