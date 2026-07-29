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

from backend.epics import annotate_epics, epic_name, list_epics, register_epic  # noqa: E402

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

    # --- Имя эпика для показа в задаче (TASK-019) ---

    def test_epic_name_resolved(self) -> None:
        """В задаче хранится ключ, а показывать нужно имя — оно только в реестре."""
        register_epic(self.tasks, "E056-18500", "Инвентаризация")
        self.assertEqual("Инвентаризация", epic_name(self.tasks, "E056-18500"))

    def test_mnemonic_key_is_recognised(self) -> None:
        """Ключ не обязан заканчиваться цифрами: бывают «E001-STALL», «STALL-001».

        Раньше такая запись реестра молча не считалась эпиком — ключ на карточке
        был, а имя пропадало без единого сообщения.
        """
        self.file.write_text(
            "## Список эпиков\n\n"
            "## E001-STALL — Почему задача стоит\n\n"
            "## STALL-001 — Мнемонический с цифрами\n",
            encoding="utf-8")
        keys = {e["key"]: e["name"] for e in list_epics(self.tasks)}
        self.assertEqual(keys.get("E001-STALL"), "Почему задача стоит")
        self.assertEqual(keys.get("STALL-001"), "Мнемонический с цифрами")

    def test_registry_headings_are_not_epics(self) -> None:
        """Заголовки самого реестра не должны попадать в список эпиков."""
        self.file.write_text(
            "# Epics\n\n## Формат записи\n\n## Как найти задачи эпика\n\n"
            "## Список эпиков\n\n## E056-18500 — Инвентаризация\n",
            encoding="utf-8")
        self.assertEqual([e["key"] for e in list_epics(self.tasks)], ["E056-18500"])

    def test_epic_name_unknown_key(self) -> None:
        """Ключ есть в задаче, но эпика нет в реестре — показываем один ключ."""
        self.assertEqual("", epic_name(self.tasks, "E404-1"))

    def test_epic_name_ignores_placeholder(self) -> None:
        """`epic: ~` означает «эпика нет» — не искать и ничего не показывать."""
        register_epic(self.tasks, "E056-18500", "Инвентаризация")
        self.assertEqual("", epic_name(self.tasks, "~"))
        self.assertEqual("", epic_name(self.tasks, ""))

    # --- Эпики карточек на доске (TASK-019) ---

    def _task_file(self, task_id: str, epic: str) -> None:
        (self.tasks / f"{task_id}-test.md").write_text(
            f"---\nid: {task_id}\ntitle: Тестовая\nepic: {epic}\nstatus: backlog\n---\n\nТело.\n",
            encoding="utf-8")

    def _board(self, *ids: str) -> dict:
        return {"columns": [{"status": "backlog", "title": "Backlog", "groups": [
            {"title": None, "tasks": [{"id": i, "file": f"{i}-test.md"} for i in ids]}]}]}

    def test_board_tasks_annotated_with_epic(self) -> None:
        """Эпика нет в строке доски — его берут из frontmatter файла задачи.

        На превью нужен только ключ: имя эпика показывается в открытой карточке,
        на маленькой карточке для него нет места.
        """
        register_epic(self.tasks, "E056-18500", "Инвентаризация")
        self._task_file("TASK-001", "E056-18500")
        self._task_file("TASK-002", "~")

        board = self._board("TASK-001", "TASK-002")
        annotate_epics(self.tasks, board)

        tasks = board["columns"][0]["groups"][0]["tasks"]
        self.assertEqual("E056-18500", tasks[0]["epic"])
        self.assertNotIn("epic_name", tasks[0], "имя эпика на превью не нужно")
        self.assertNotIn("epic", tasks[1], "задача без эпика не должна получать поле")

    def test_annotate_survives_missing_file(self) -> None:
        """Битая ссылка на доске не должна ронять отдачу доски."""
        board = self._board("TASK-404")
        annotate_epics(self.tasks, board)
        self.assertNotIn("epic", board["columns"][0]["groups"][0]["tasks"][0])

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
