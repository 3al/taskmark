"""Уведомления о движении задачи по статусам: снимок, диф, отправка.

Смену статуса ловим **сравнением снимков**, а не перехватом вызова: статус
меняют тремя путями (доска, автономный `set_status.py`, рука в файле), и два из
них бэкенд не видит вовсе. Снимок строится по `board.md` — один файл на проект,
статусы в нём разделами; файл задачи открывается только для той, что переехала.

Сети здесь нет: отправка подменяется приёмником.
"""

import tempfile
import unittest
from pathlib import Path

from backend import notify_watch

BOARD = """# Доска

## Backlog

### Из Telegram
- TASK-014 · [Починить импорт](TASK-014-pochinit-import.md) · @petya · 2026-09-03

## Development

_(нет)_

## Done

_(нет)_
"""

TASK = """---
id: TASK-014
title: Починить импорт
status: {status}
author: @petya
origin: {origin}
---

## Описание

Текст.
"""


def cfg(**over) -> dict:
    base = {"telegram": True, "telegram_token": "t",
            "telegram_username": "kostya",
            "telegram_chats": {"-100": "Первый"}}
    base.update(over)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tasks = Path(self.tmp.name) / "project" / "tasks"
        self.tasks.mkdir(parents=True)
        self.write_board("Backlog")
        self.write_task("backlog", "telegram:-100")
        self.sent: list[tuple] = []
        self.state: dict = {}

    def send(self, chat_id, text, reply_to=None, **kw):
        self.sent.append((chat_id, text))

    def write_board(self, section: str) -> None:
        board = BOARD
        if section != "Backlog":
            board = board.replace(
                "- TASK-014 · [Починить импорт](TASK-014-pochinit-import.md) · @petya · 2026-09-03\n",
                "_(нет)_\n").replace(
                f"## {section}\n\n_(нет)_",
                f"## {section}\n\n- TASK-014 · [Починить импорт]"
                "(TASK-014-pochinit-import.md) · @petya · 2026-09-03")
        (self.tasks / "board.md").write_text(board, encoding="utf-8")

    def write_task(self, status: str, origin: str) -> None:
        (self.tasks / "TASK-014-pochinit-import.md").write_text(
            TASK.format(status=status, origin=origin), encoding="utf-8")

    # Маршрут проекта под тестовую доску: разделы в ней свои, и статус
    # определяется по разделу этого пайплайна, а не по именам поставки
    PIPELINE = {"pipeline": ["backlog", "development", "done", "cancelled"],
                "statuses": {"cancelled": {"offramp": True}}}

    def check(self, config=None, project_cfg=None):
        """Один проход наблюдателя по проекту."""
        return notify_watch.check_project(
            self.tasks, config if config is not None else cfg(),
            {**self.PIPELINE, **(project_cfg or {})}, self.state, send=self.send)


class TestПервыйПроход(Base):
    """Снимок берётся молча: иначе запуск сервера рассылал бы всю доску."""

    def test_первый_проход_ничего_не_шлёт(self):
        self.check()
        self.assertEqual(self.sent, [])

    def test_снимок_запомнен(self):
        self.check()
        self.assertEqual(self.state.get(str(self.tasks), {}).get("TASK-014"),
                         "Backlog")


class TestДвижение(Base):
    """Переезд задачи по доске — повод для уведомления."""

    def setUp(self):
        super().setUp()
        self.check()  # снимок

    def test_переезд_в_настроенный_статус(self):
        self.write_board("Done")
        self.write_task("done", "telegram:-100")
        self.check()
        self.assertEqual(len(self.sent), 1)
        chat_id, text = self.sent[0]
        self.assertEqual(chat_id, -100)
        self.assertIn("TASK-014", text)
        self.assertIn("Починить импорт", text)
        self.assertIn("Backlog", text)
        self.assertIn("Done", text)
        self.assertIn("@kostya", text)
        self.assertIn("@petya", text)

    def test_переезд_мимо_настроенных_статусов_молчит(self):
        self.write_board("Development")
        self.write_task("development", "telegram:-100")
        self.check()
        self.assertEqual(self.sent, [])

    def test_второй_проход_не_повторяет_уведомление(self):
        self.write_board("Done")
        self.write_task("done", "telegram:-100")
        self.check()
        self.check()
        self.assertEqual(len(self.sent), 1)

    def test_правка_не_про_статус_молчит(self):
        self.write_task("backlog", "telegram:-100")
        self.check()
        self.assertEqual(self.sent, [])

    def test_человек_выбрал_другие_статусы(self):
        self.write_board("Development")
        self.write_task("development", "telegram:-100")
        self.check(project_cfg={"notify_statuses": ["development"]})
        self.assertEqual(len(self.sent), 1)


class TestОхват(Base):
    """Кого уведомления не касаются вовсе."""

    def setUp(self):
        super().setUp()
        self.check()

    def _move_to_done(self, origin: str = "telegram:-100") -> None:
        self.write_board("Done")
        self.write_task("done", origin)

    def test_задача_не_из_чата(self):
        self._move_to_done(origin="~")
        self.check()
        self.assertEqual(self.sent, [])

    def test_выключенная_интеграция(self):
        self._move_to_done()
        self.check(config=cfg(telegram=False))
        self.assertEqual(self.sent, [])

    def test_нет_токена(self):
        self._move_to_done()
        self.check(config=cfg(telegram_token=""))
        self.assertEqual(self.sent, [])

    def test_ни_одного_привязанного_чата(self):
        """Настройка не доведена до конца — уведомлять неоткуда и незачем."""
        self._move_to_done()
        self.check(config=cfg(telegram_chats={}))
        self.assertEqual(self.sent, [])

    def test_молчание_снимка_не_ломает(self):
        """Выключенная возможность не должна терять движение задачи."""
        self._move_to_done()
        self.check(config=cfg(telegram=False))
        self.assertEqual(self.state.get(str(self.tasks), {}).get("TASK-014"),
                         "Done")


class TestУстойчивость(Base):
    """Наблюдатель живёт в фоне: одна беда не должна ронять остальное."""

    def setUp(self):
        super().setUp()
        self.check()

    def test_доска_исчезла(self):
        (self.tasks / "board.md").unlink()
        self.check()
        self.assertEqual(self.sent, [])

    def test_файл_задачи_исчез(self):
        self.write_board("Done")
        (self.tasks / "TASK-014-pochinit-import.md").unlink()
        self.check()
        self.assertEqual(self.sent, [])

    def test_отправка_упала_а_снимок_сдвинулся(self):
        """Иначе следующая попытка повторяла бы то же сообщение бесконечно."""
        def broken(chat_id, text, reply_to=None, **kw):
            raise RuntimeError("сеть отвалилась")

        self.write_board("Done")
        self.write_task("done", "telegram:-100")
        notify_watch.check_project(self.tasks, cfg(), self.PIPELINE, self.state,
                                   send=broken)
        self.assertEqual(self.state.get(str(self.tasks), {}).get("TASK-014"),
                         "Done")


if __name__ == "__main__":
    unittest.main()
