"""Тесты реестра эпиков: чтение списка и регистрация нового ключа.

Имя эпика хранится только в tasks/epics.md — задачи ссылаются на него ключом.
Создание задачи из UI не должно оставлять ссылку на эпик, которого нет в реестре.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.epics import list_epics, register_epic  # noqa: E402

TEMPLATE = (Path(__file__).resolve().parent.parent / "templates" / "tasks" / "epics.md")


class EpicsRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.file = self.tasks / "epics.md"
        self.file.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

    def test_empty_registry(self) -> None:
        self.assertEqual([], list_epics(self.tasks))

    def test_missing_file_is_not_an_error(self) -> None:
        """Реестра может не быть — создание задач от этого падать не должно."""
        self.file.unlink()
        self.assertEqual([], list_epics(self.tasks))

    def test_register_and_list(self) -> None:
        register_epic(self.tasks, "E056-18500", "Инвентаризация")

        self.assertEqual([{"key": "E056-18500", "name": "Инвентаризация"}],
                         list_epics(self.tasks))
        self.assertNotIn("_(нет)_", self.file.read_text(encoding="utf-8"),
                         "заглушка пустого списка осталась")

    def test_register_keeps_existing(self) -> None:
        register_epic(self.tasks, "E056-18500", "Инвентаризация")
        register_epic(self.tasks, "E057-1", "Сборка заказа")

        self.assertEqual(["E056-18500", "E057-1"], [e["key"] for e in list_epics(self.tasks)])

    def test_register_is_idempotent(self) -> None:
        """Повторное создание задачи в том же эпике не плодит записей."""
        register_epic(self.tasks, "E056-18500", "Инвентаризация")
        register_epic(self.tasks, "E056-18500", "Другое имя")

        epics = list_epics(self.tasks)
        self.assertEqual(1, len(epics))
        self.assertEqual("Инвентаризация", epics[0]["name"],
                         "имя эпика — единственный источник правды, перезаписывать нельзя")

    def test_register_without_name_keeps_key(self) -> None:
        """Имя не указали — ключ всё равно попадает в реестр, имя допишут потом."""
        register_epic(self.tasks, "E999-1", "")

        self.assertEqual([{"key": "E999-1", "name": ""}], list_epics(self.tasks))

    def test_registry_survives_manual_notes(self) -> None:
        """Пользователь мог дописать описание под эпиком — оно не должно потеряться."""
        register_epic(self.tasks, "E056-18500", "Инвентаризация")
        text = self.file.read_text(encoding="utf-8")
        self.file.write_text(text + "Пара слов про эпик.\n", encoding="utf-8")

        register_epic(self.tasks, "E057-1", "Сборка заказа")

        content = self.file.read_text(encoding="utf-8")
        self.assertIn("Пара слов про эпик.", content)
        self.assertEqual(2, len(list_epics(self.tasks)))


if __name__ == "__main__":
    unittest.main()
