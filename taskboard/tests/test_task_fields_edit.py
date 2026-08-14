"""Правка полей задачи из окна доски (TASK-032).

Из окна уже правились название, тип, размер, тексты секций и простой. Здесь
добавляются два поля, которых человеку не хватало:

- **эпик** — назначить, сменить или снять. Поле есть в форме создания, а после
  создания менялось только правкой файла руками;
- **комментарий** — своя строка в «Комментарии»: время из системы, подпись
  `доска`. Формат один со скриптом, иначе хронологию перестанет читать и
  человек, и разбор.

Дата создания намеренно остаётся закрытой: `created` кормит возраст задачи и
подсветку свежести на доске.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from backend import config as config_mod  # noqa: E402
from backend import registry  # noqa: E402
from backend.app import (CommentIn, TaskIn, TaskUpdateIn,  # noqa: E402
                         api_add_comment, api_create_task, api_update_task)
from backend.config import DEFAULTS  # noqa: E402
from backend.epics import list_epics  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.task_parser import parse_task  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
TASK_MODAL = SRC / "components" / "TaskModal.jsx"
API_JS = SRC / "api.js"


class ProjectCase(unittest.TestCase):
    """Временный проект: правка идёт через те же функции API, что и из окна."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tasks = tmp / "project" / "tasks"

        # Глобальное состояние живёт в ~/.taskboard — подменяем на временное,
        # иначе тест правит реестр и конфиг пользователя
        self._saved = {
            "projects": registry.PROJECTS_FILE, "dir": registry.GLOBAL_DIR,
            "cfg_file": config_mod.GLOBAL_CONFIG_FILE, "cfg_dir": config_mod.GLOBAL_DIR,
        }
        registry.PROJECTS_FILE = tmp / "projects.json"
        registry.GLOBAL_DIR = tmp
        config_mod.GLOBAL_CONFIG_FILE = tmp / "config.json"
        config_mod.GLOBAL_DIR = tmp
        self.addCleanup(self._restore)

        cfg = dict(DEFAULTS)
        cfg["harnesses"] = {"claude": True, "opencode": False}
        scaffold_project(self.tasks, cfg, {"harnesses": cfg["harnesses"]})
        registry.register_project(self.tasks, name="project")

        self.task_id = self._create("Задача под правку")

    def _restore(self) -> None:
        registry.PROJECTS_FILE = self._saved["projects"]
        registry.GLOBAL_DIR = self._saved["dir"]
        config_mod.GLOBAL_CONFIG_FILE = self._saved["cfg_file"]
        config_mod.GLOBAL_DIR = self._saved["cfg_dir"]

    def _create(self, title: str, **extra) -> str:
        result = api_create_task(TaskIn(title=title, description="описание",
                                        criteria="критерии", **extra))
        self.assertTrue(result.get("ok"), result)
        return result["id"]

    def _meta(self, task_id: str | None = None) -> dict:
        task = parse_task(self.tasks, task_id or self.task_id)
        self.assertIsNotNone(task, "файл задачи не найден")
        return (task or {}).get("meta", {})

    def _text(self, task_id: str | None = None) -> str:
        path = next(self.tasks.glob(f"{task_id or self.task_id}-*.md"))
        return path.read_text(encoding="utf-8")

    def _notes(self, task_id: str | None = None) -> list[str]:
        """Строки секции «Комментарии» — хронология задачи."""
        text = self._text(task_id)
        body = text[text.index("## Комментарии") + len("## Комментарии"):]
        end = body.find("\n## ")
        body = body[:end] if end >= 0 else body
        return [ln for ln in body.splitlines() if ln.strip().startswith("- ")]


class EpicEditTest(ProjectCase):
    """Эпик назначается, меняется и снимается из окна задачи."""

    def test_epic_assigned(self) -> None:
        result = api_update_task(self.task_id,
                                 TaskUpdateIn(epic="E056-18500",
                                              epic_name="Инвентаризация"))

        self.assertEqual(result.get("epic"), "E056-18500", result)
        self.assertEqual(self._meta().get("epic"), "E056-18500")

    def test_new_key_registered_with_name(self) -> None:
        """Имя эпика хранится только в реестре: ссылка на безымянный бесполезна."""
        api_update_task(self.task_id,
                        TaskUpdateIn(epic="E056-18500", epic_name="Инвентаризация"))

        self.assertEqual([{"key": "E056-18500", "name": "Инвентаризация"}],
                         list_epics(self.tasks))

    def test_known_epic_keeps_its_name(self) -> None:
        """Реестр — источник правды: назначение задачи эпик не переименовывает."""
        api_update_task(self.task_id,
                        TaskUpdateIn(epic="E056-18500", epic_name="Инвентаризация"))
        second = self._create("Вторая")
        api_update_task(second, TaskUpdateIn(epic="E056-18500", epic_name="Другое имя"))

        self.assertEqual([{"key": "E056-18500", "name": "Инвентаризация"}],
                         list_epics(self.tasks))

    def test_epic_cleared(self) -> None:
        api_update_task(self.task_id, TaskUpdateIn(epic="E056-18500"))
        api_update_task(self.task_id, TaskUpdateIn(epic=""))

        self.assertIn(str(self._meta().get("epic") or ""), ("", "~"),
                      "эпик не снялся")

    def test_unreadable_key_refused(self) -> None:
        """Ключ, которого реестр не прочтёт, — отказ, а не молчаливая запись.

        Запись эпика в `epics.md` разбирается регуляркой: ключ с пробелом туда
        попадёт, но обратно не прочитается — имя пропадёт без единого сообщения.
        """
        with self.assertRaises(HTTPException) as caught:
            api_update_task(self.task_id, TaskUpdateIn(epic="мой эпик"))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn(str(self._meta().get("epic") or ""), ("", "~"),
                      "отказ не должен трогать файл задачи")
        self.assertEqual([], list_epics(self.tasks),
                         "нечитаемый ключ попал в реестр")

    def test_change_goes_to_history(self) -> None:
        """Смена эпика — событие жизненного цикла, как смена типа и размера."""
        api_update_task(self.task_id, TaskUpdateIn(epic="E056-18500"))

        self.assertTrue([n for n in self._notes() if "эпик" in n.lower()],
                        "смена эпика не попала в «Комментарии»")

    def test_same_epic_is_not_an_event(self) -> None:
        api_update_task(self.task_id, TaskUpdateIn(epic="E056-18500"))
        before = len(self._notes())
        api_update_task(self.task_id, TaskUpdateIn(epic="E056-18500"))

        self.assertEqual(before, len(self._notes()),
                         "повтор того же значения записан как событие")

    def test_other_fields_untouched(self) -> None:
        """Правка эпика не двигает задачу по маршруту и не трогает тип."""
        meta_before = self._meta()
        api_update_task(self.task_id, TaskUpdateIn(epic="E056-18500"))
        meta = self._meta()

        self.assertEqual(meta.get("status"), meta_before.get("status"))
        self.assertEqual(meta.get("type"), meta_before.get("type"))


class BoardCommentTest(ProjectCase):
    """Комментарий с доски: строка в хронологии, а не правка секции."""

    def test_comment_appended(self) -> None:
        result = api_add_comment(self.task_id, CommentIn(text="проверил локально"))

        self.assertTrue(result.get("ok"), result)
        self.assertTrue([n for n in self._notes() if "проверил локально" in n],
                        "комментарий не попал в секцию")

    def test_format_matches_the_script(self) -> None:
        """Формат один со скриптом: дата, время, подпись источника, суть."""
        api_add_comment(self.task_id, CommentIn(text="проверил локально"))
        line = self._notes()[-1]

        self.assertRegex(line,
                         r"^- \*\*\d{4}-\d{2}-\d{2} \d{2}:\d{2}\*\* · доска · проверил локально$")

    def test_comment_goes_to_the_end(self) -> None:
        """Комментарии — хронология: свежий снизу, прежние не переставляются."""
        api_add_comment(self.task_id, CommentIn(text="первый"))
        api_add_comment(self.task_id, CommentIn(text="второй"))
        notes = self._notes()

        self.assertIn("первый", notes[-2])
        self.assertIn("второй", notes[-1])

    def test_multiline_collapsed(self) -> None:
        """Перенос внутри строки списка разорвал бы хронологию — схлопываем."""
        api_add_comment(self.task_id, CommentIn(text="первая строка\nвторая строка"))
        line = self._notes()[-1]

        self.assertIn("первая строка вторая строка", line)

    def test_empty_comment_refused(self) -> None:
        before = len(self._notes())
        with self.assertRaises(HTTPException) as caught:
            api_add_comment(self.task_id, CommentIn(text="   "))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(before, len(self._notes()), "пустая строка записана")

    def test_unknown_task_refused(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            api_add_comment("TASK-999", CommentIn(text="в никуда"))

        self.assertEqual(caught.exception.status_code, 404)


class EditingUiTest(unittest.TestCase):
    """Оба поля правятся из окна задачи (автотестов JS в проекте нет)."""

    def setUp(self) -> None:
        self.modal = TASK_MODAL.read_text(encoding="utf-8")

    def test_epic_is_editable_in_modal(self) -> None:
        self.assertIn("epicForm", self.modal, "эпик в окне не правится")
        self.assertRegex(self.modal, r"updateTask\(taskId, \{ epic",
                         "выбор эпика не сохраняется")

    def test_epic_field_is_shared_with_the_create_form(self) -> None:
        """Поле эпика одно на оба места: две формы ввода разъехались бы.

        Подсказки, разбор «ключ · название» и вопрос об имени нового эпика
        живут в общем компоненте, а не копией в каждом окне.
        """
        form = (SRC / "components" / "NewTaskModal.jsx").read_text(encoding="utf-8")
        for name, text in (("окно задачи", self.modal), ("форма создания", form)):
            with self.subTest(place=name):
                self.assertIn("EpicField", text, f"{name} не использует общее поле эпика")
                self.assertNotIn("epicSuggestions", text,
                                 f"в {name} осталась своя копия подсказок")

    def test_epic_suggestions_come_from_registry(self) -> None:
        """Ключ выбирают из реестра, а не вспоминают."""
        field = (SRC / "components" / "EpicField.jsx").read_text(encoding="utf-8")
        self.assertIn("api.epics(", field, "поле эпика не спрашивает реестр")

    def test_new_epic_asks_for_a_name(self) -> None:
        """Незнакомый ключ без имени оставил бы реестр с безымянной записью."""
        field = (SRC / "components" / "EpicField.jsx").read_text(encoding="utf-8")
        self.assertIn("Название нового эпика", field,
                      "имя нового эпика не спрашивается")
        self.assertIn("epic_name", self.modal,
                      "окно задачи не отправляет имя нового эпика")

    def test_epic_can_be_dropped(self) -> None:
        self.assertRegex(self.modal, r"снять эпик",
                         "снять эпик из окна нечем")

    def test_comment_form_present(self) -> None:
        self.assertIn("addComment", self.modal, "окно не умеет добавлять комментарий")

    def test_api_client_knows_comment(self) -> None:
        text = API_JS.read_text(encoding="utf-8")
        self.assertIn("addComment", text, "в api.js нет вызова добавления комментария")
        self.assertIn("/comment", text, "вызов идёт не на эндпоинт комментария")

    def test_created_stays_read_only(self) -> None:
        """Дата создания кормит возраст и свежесть — правке не подлежит."""
        self.assertNotRegex(self.modal, r"updateTask\(taskId, \{ created",
                            "из окна правится дата создания")


if __name__ == "__main__":
    unittest.main()
