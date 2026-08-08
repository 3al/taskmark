"""Секция «Изменение для пользователя» правится из окна задачи.

Текст, который уедет в changelog, человек вычитывает перед выпуском. Если его
нельзя поправить в карточке, сценарий ломается: остаётся текстовый редактор.

Отдельно проверяется, что список редактируемых секций **не дублируется**:
обработчик правки должен обходить реестр, иначе следующая секция снова
не доедет — ровно так эта и не доехала.
"""

import tempfile
import unittest
from pathlib import Path

from backend import task_parser
from backend.task_parser import (EDITABLE_SECTIONS, section_body,
                                 set_task_section, task_sections)

RELEASE_HEADING = "## Изменение для пользователя"

TASK = """---
id: TASK-001
title: Проверка блокировок
status: development
---

## Описание

Что-то про блокировки.

### Критерии приёмки

Всё работает.

## Изменение для пользователя

Черновик от агента.

## Чеклист

- [ ] раз

## Комментарии

## История коммитов
"""

WITHOUT = TASK.replace("## Изменение для пользователя\n\nЧерновик от агента.\n\n", "")


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _task(self, content=TASK):
        path = self.dir / "TASK-001-проверка.md"
        path.write_text(content, encoding="utf-8")
        return path


class TestRegistry(unittest.TestCase):

    def test_секция_в_реестре_редактируемых(self):
        self.assertIn(RELEASE_HEADING, dict(EDITABLE_SECTIONS).values())

    def test_ключ_называется_release_notes(self):
        self.assertEqual(dict(EDITABLE_SECTIONS).get("release_notes"), RELEASE_HEADING)


class TestSections(Base):

    def test_секция_попадает_в_список_для_окна(self):
        keys = [s["key"] for s in task_sections(self._task().read_text(encoding="utf-8"))]
        self.assertIn("release_notes", keys)

    def test_без_секции_её_в_списке_нет(self):
        # Старые и урезанные задачи: карандаша нет, но это не ошибка
        keys = [s["key"] for s in task_sections(WITHOUT)]
        self.assertNotIn("release_notes", keys)

    def test_тело_секции_читается_целиком(self):
        section = next(s for s in task_sections(self._task().read_text(encoding="utf-8"))
                       if s["key"] == "release_notes")
        self.assertIn("Черновик от агента", section["text"])
        self.assertNotIn("Чеклист", section["text"], "тело залезло в соседнюю секцию")


class TestWrite(Base):

    def test_правка_сохраняется(self):
        self._task()
        result = set_task_section(self.dir, "TASK-001", "release_notes",
                                  "Блокировки между задачами больше не расходятся.")
        self.assertTrue(result["ok"], result)
        content = (self.dir / "TASK-001-проверка.md").read_text(encoding="utf-8")
        self.assertIn("больше не расходятся", content)
        self.assertNotIn("Черновик от агента", content)

    def test_соседние_секции_не_тронуты(self):
        # Пока карточка открыта, в тот же файл пишет агент — правка точечная
        self._task()
        set_task_section(self.dir, "TASK-001", "release_notes", "Новый текст.")
        content = (self.dir / "TASK-001-проверка.md").read_text(encoding="utf-8")
        self.assertIn("Что-то про блокировки", content)
        self.assertIn("## Чеклист", content)
        self.assertIn("## История коммитов", content)
        self.assertEqual(section_body(content, "### Критерии приёмки").strip(), "Всё работает.")

    def test_без_секции_внятный_отказ(self):
        path = self.dir / "TASK-001-проверка.md"
        path.write_text(WITHOUT, encoding="utf-8")
        result = set_task_section(self.dir, "TASK-001", "release_notes", "текст")
        self.assertFalse(result["ok"])
        self.assertIn("Изменение для пользователя", result["error"])


class TestNoDuplicateRegistry(unittest.TestCase):
    """Список секций в обработчике правки не должен быть вторым источником."""

    def test_обработчик_обходит_реестр(self):
        import ast
        source = (Path(task_parser.__file__).parent / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('("description", "criteria")', source,
                         "список секций продублирован в app.py — новая секция "
                         "в реестре до правки не доедет")
        self.assertIn("EDITABLE_SECTIONS", source,
                      "обработчик правки должен брать секции из реестра")
        ast.parse(source)


if __name__ == "__main__":
    unittest.main()
