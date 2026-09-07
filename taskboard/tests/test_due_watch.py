"""Напоминание о подходящем сроке: окно порога, кого не трогаем, повтор.

Повод и канал разведены: `due_events()` отвечает только на вопрос «у каких
задач срок в окне порога» и про телеграм не знает — отсев чужих задач и текст
сообщения остаются за каналом.

Сети здесь нет (отправка подменяется приёмником), реального времени тоже:
сегодняшний день и момент прохода передаются аргументами.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from backend import due_watch, telegram_source

BOARD = """# Доска

## Backlog

### Из Telegram
- TASK-014 · [Починить импорт](TASK-014-pochinit-import.md) · @petya · 2026-09-03

## Development

_(нет)_

## Done

_(нет)_

## Cancelled

_(нет)_
"""

TASK = """---
id: TASK-014
title: Починить импорт
status: {status}
author: @petya
origin: {origin}
due: {due}
created: {created} 10:00
---

## Описание

Текст.
"""

TODAY = date(2026, 9, 10)


def cfg(**over) -> dict:
    base = {"telegram": True, "telegram_token": "t",
            "telegram_username": "kostya",
            "telegram_chats": {"-100": "Первый"},
            "telegram_due_days": [7, 3, 1]}
    base.update(over)
    return base


class Base(unittest.TestCase):
    # Маршрут под тестовую доску: разделы в ней свои, и терминальность
    # определяется этим пайплайном, а не именами поставки
    PIPELINE = {"pipeline": ["backlog", "development", "done", "cancelled"],
                "statuses": {"cancelled": {"offramp": True}}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tasks = Path(self.tmp.name) / "project" / "tasks"
        self.tasks.mkdir(parents=True)
        self.write_board("Backlog")
        self.write_task(due="2026-09-12")
        self.sent: list[tuple] = []
        self.marks: dict = {}

    def send(self, chat_id, text, reply_to=None, **kw):
        self.sent.append((chat_id, text))

    def write_board(self, section: str) -> None:
        row = ("- TASK-014 · [Починить импорт](TASK-014-pochinit-import.md)"
               " · @petya · 2026-09-03\n")
        board = BOARD
        if section != "Backlog":
            board = board.replace(row, "_(нет)_\n").replace(
                f"## {section}\n\n_(нет)_", f"## {section}\n\n{row.rstrip()}")
        (self.tasks / "board.md").write_text(board, encoding="utf-8")

    def write_task(self, due: str, status: str = "backlog",
                   origin: str = "telegram:-100",
                   created: str = "2026-09-01") -> None:
        (self.tasks / "TASK-014-pochinit-import.md").write_text(
            TASK.format(status=status, origin=origin, due=due or "~",
                        created=created),
            encoding="utf-8")

    def check(self, config=None, today=TODAY):
        """Один проход по проекту."""
        return due_watch.check_project(
            self.tasks, config if config is not None else cfg(),
            self.PIPELINE, self.marks, send=self.send, today=today)


class TestОкноПорога(Base):
    """Лестница порогов задаёт окно. Пустая — молчим везде."""

    def test_срок_в_окне_даёт_одно_сообщение_с_тегами(self):
        self.assertEqual(1, self.check())
        chat_id, text = self.sent[0]
        self.assertEqual(-100, chat_id)
        self.assertIn("TASK-014", text)
        self.assertIn("Починить импорт", text)
        self.assertIn("2026-09-12", text)
        self.assertIn("@kostya", text)
        self.assertIn("@petya", text)

    def test_срок_дальше_порога_молчит(self):
        self.write_task(due="2026-09-20")
        self.check()
        self.assertEqual([], self.sent)

    def test_срок_сегодня_попадает_в_окно(self):
        self.write_task(due="2026-09-10")
        self.assertEqual(1, self.check())

    def test_пустая_лестница_выключает_напоминания(self):
        self.check(config=cfg(telegram_due_days=[]))
        self.assertEqual([], self.sent)

    def test_задача_без_срока_молчит(self):
        self.write_task(due="")
        self.check()
        self.assertEqual([], self.sent)


class TestКогоНеТрогаем(Base):
    """Конец маршрута, съезд, чужая задача и просрочка — не наш повод."""

    def test_конец_маршрута_молчит(self):
        self.write_board("Done")
        self.write_task(due="2026-09-12", status="done")
        self.check()
        self.assertEqual([], self.sent)

    def test_съезд_молчит(self):
        self.write_board("Cancelled")
        self.write_task(due="2026-09-12", status="cancelled")
        self.check()
        self.assertEqual([], self.sent)

    def test_задача_не_из_чата_молчит(self):
        self.write_task(due="2026-09-12", origin="")
        self.check()
        self.assertEqual([], self.sent)

    def test_просроченная_молчит(self):
        # Просрочка — соседняя задача со своим сообщением и своими правилами
        self.write_task(due="2026-09-08")
        self.check()
        self.assertEqual([], self.sent)

    def test_выключенная_интеграция_молчит(self):
        self.check(config=cfg(telegram=False))
        self.assertEqual([], self.sent)


class TestПовтор(Base):
    """Одно напоминание на срок, а не на каждый тик таймера."""

    def test_второй_проход_молчит(self):
        self.check()
        self.check()
        self.assertEqual(1, len(self.sent))

    def test_перенос_срока_напоминает_заново(self):
        self.check()
        self.write_task(due="2026-09-11")
        self.check()
        self.assertEqual(2, len(self.sent))

    def test_отметка_ушедшей_из_окна_задачи_убирается(self):
        self.check()
        self.write_task(due="2026-09-30")
        self.check()
        self.assertEqual({}, self.marks.get(str(self.tasks), {}))


class TestЛестницаПорогов(Base):
    """Границы проходятся по очереди: горизонт задачи сам задаёт число пингов."""

    def at(self, day: int):
        """Проход в сентябрьский день `day`."""
        return self.check(today=date(2026, 9, day))

    def test_двухнедельная_задача_пингуется_на_каждой_границе(self):
        self.write_task(due="2026-09-15", created="2026-09-01")
        self.at(4)   # осталось 11 — до первой границы далеко
        self.assertEqual([], self.sent)
        self.at(8)   # 7 — первая граница
        self.at(10)  # 5 — всё ещё та же граница
        self.assertEqual(1, len(self.sent))
        self.at(12)  # 3 — вторая граница
        self.at(14)  # 1 — третья
        self.assertEqual(3, len(self.sent))

    def test_однодневная_задача_пингуется_один_раз(self):
        # Границ 7 и 3 такая задача не пересекала: пинговать по ним нечего
        self.write_task(due="2026-09-10", created="2026-09-09")
        self.at(9)
        self.at(10)
        self.assertEqual(1, len(self.sent))
        self.assertIn("срок сегодня", self.sent[0][1])


class TestДеньЗаведения(Base):
    """Заведённая сегодня задача — ещё не повод: человек её только что принёс."""

    def test_в_день_заведения_молчим(self):
        self.write_task(due="2026-09-11", created="2026-09-10")
        self.check()
        self.assertEqual([], self.sent)

    def test_на_следующий_день_напоминаем(self):
        self.write_task(due="2026-09-11", created="2026-09-10")
        self.check()
        self.check(today=date(2026, 9, 11))
        self.assertEqual(1, len(self.sent))

    def test_задача_без_даты_заведения_напоминается(self):
        # Старые задачи поля не имеют — это не повод молчать о сроке
        path = self.tasks / "TASK-014-pochinit-import.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("created: 2026-09-01 10:00" + chr(10), ""),
                        encoding="utf-8")
        self.check()
        self.assertEqual(1, len(self.sent))


class TestЧтениеПорогов(unittest.TestCase):
    """Лестница приходит из формы строкой, а из старого конфига — числом."""

    def test_список(self):
        self.assertEqual([7, 3, 1], due_watch.thresholds({"telegram_due_days": [1, 7, 3]}))

    def test_строка_из_формы(self):
        self.assertEqual([7, 3, 1],
                         due_watch.thresholds({"telegram_due_days": "7, 3, 1"}))

    def test_одно_число(self):
        self.assertEqual([3], due_watch.thresholds({"telegram_due_days": 3}))

    def test_мусор_и_ноль_читаются_как_молчание(self):
        for value in (0, "", [], "завтра", None, [0, -5]):
            with self.subTest(value=value):
                self.assertEqual([], due_watch.thresholds({"telegram_due_days": value}))

    def test_дубли_схлопываются(self):
        self.assertEqual([3, 1], due_watch.thresholds({"telegram_due_days": [3, 1, 3]}))


class TestПоводБезКанала(Base):
    """`due_events()` — состояние задачи; про чат он не знает."""

    def test_повод_отдаёт_и_задачу_не_из_чата(self):
        self.write_task(due="2026-09-12", origin="")
        events = due_watch.due_events(self.tasks, self.PIPELINE, 3, TODAY)
        self.assertEqual(["TASK-014"], [e["id"] for e in events])
        self.assertEqual(2, events[0]["left"])

    def test_повод_не_видит_конца_маршрута(self):
        self.write_board("Done")
        self.write_task(due="2026-09-12", status="done")
        self.assertEqual([], due_watch.due_events(self.tasks, self.PIPELINE, 3,
                                                  TODAY))


class TestПерезапуск(Base):
    """Отметка о посланном живёт в файле состояния и переживает перезапуск."""

    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name) / "global"
        # `enterContext` появился только в 3.11, а проект держит 3.10
        for name, value in (("GLOBAL_DIR", root),
                            ("STATE_FILE", root / "telegram.json")):
            patcher = mock.patch.object(telegram_source, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def projects(self):
        return [{"tasks_dir": str(self.tasks)}]

    def test_второй_запуск_не_повторяет_напоминание(self):
        state: dict = {}
        due_watch.check_all(cfg(), state, self.projects(), now=100.0,
                            today=TODAY, send=self.send)
        self.assertEqual(1, len(self.sent))
        # «Перезапуск»: память процесса пуста, на диске осталась отметка
        due_watch.check_all(cfg(), {}, self.projects(), now=100.0, today=TODAY,
                            send=self.send)
        self.assertEqual(1, len(self.sent))


class TestРасписание(Base):
    """Срок меряется днями: чаще раза в час ходить по файлам незачем."""

    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name) / "global"
        # `enterContext` появился только в 3.11, а проект держит 3.10
        for name, value in (("GLOBAL_DIR", root),
                            ("STATE_FILE", root / "telegram.json")):
            patcher = mock.patch.object(telegram_source, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.state: dict = {}

    def run_at(self, now, today=TODAY):
        return due_watch.check_all(cfg(), self.state, [{"tasks_dir": str(self.tasks)}],
                                   now=now, today=today, send=self.send)

    def test_первый_проход_после_старта_не_молчит(self):
        self.assertEqual(1, self.run_at(100.0))

    def test_следующий_тик_файлы_не_читает(self):
        self.run_at(100.0)
        self.write_task(due="2026-09-11")  # перенос заметили бы, если бы читали
        self.run_at(105.0)
        self.assertEqual(1, len(self.sent))

    def test_через_час_проход_повторяется(self):
        self.run_at(100.0)
        self.write_task(due="2026-09-11")
        self.run_at(100.0 + due_watch.PERIOD)
        self.assertEqual(2, len(self.sent))


if __name__ == "__main__":
    unittest.main()
