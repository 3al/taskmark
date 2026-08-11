"""Копирование задачи (TASK-128).

Копия — это новая задача, предзаполненная данными оригинала: название,
описание, критерии, тип, эпик, блокировки и пауза. Кладётся она **в бэклог**,
в рубрику своего типа, где бы ни стоял оригинал: копия рядом с оригиналом
унаследовала бы и долг этапа, которого у новой работы нет.

Проверяем оба конца: рождение копии (бэкенд) и то, что форма создания умеет
режим копии, а превью с окном задачи его зовут (исходники — автотестов JS в
проекте нет).

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config as config_mod  # noqa: E402
from backend import registry  # noqa: E402
from backend.app import TaskIn, api_create_task  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402
from backend.queue_ops import move_task  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.task_parser import parse_task  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
APP = SRC / "App.jsx"
NEW_TASK = SRC / "components" / "NewTaskModal.jsx"
TASK_MODAL = SRC / "components" / "TaskModal.jsx"


class CopyCreationTest(unittest.TestCase):
    """Копия рождается в бэклоге и уносит пользовательские данные оригинала."""

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

    def _restore(self) -> None:
        registry.PROJECTS_FILE = self._saved["projects"]
        registry.GLOBAL_DIR = self._saved["dir"]
        config_mod.GLOBAL_CONFIG_FILE = self._saved["cfg_file"]
        config_mod.GLOBAL_DIR = self._saved["cfg_dir"]

    def _board(self) -> list[str]:
        return (self.tasks / "board.md").read_text(encoding="utf-8").splitlines()

    def _line_of(self, task_id: str) -> int:
        for i, line in enumerate(self._board()):
            if re.match(rf"^\s*-\s*{task_id}\s*·", line):
                return i
        self.fail(f"{task_id} нет на доске")
        raise AssertionError  # для типов: fail() уже прервал тест

    def _rubric_of(self, task_id: str) -> str:
        """Ближайший заголовок ### над строкой задачи."""
        lines = self._board()
        for i in range(self._line_of(task_id), -1, -1):
            if lines[i].startswith("### "):
                return lines[i]
        return ""

    def _create(self, **kwargs) -> str:
        payload = {"title": "Задача", **kwargs}
        result = api_create_task(TaskIn(**payload))
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("id"), result)
        return result["id"]

    def _meta(self, task_id: str) -> dict:
        task = parse_task(self.tasks, task_id)
        self.assertIsNotNone(task, f"файл {task_id} не найден")
        return (task or {}).get("meta", {})

    def test_copy_of_a_task_in_work_goes_to_backlog(self) -> None:
        """Оригинал в работе — копия всё равно начинает с бэклога.

        Рядом с оригиналом она унаследовала бы чужой этап и его долг, которого
        у только что заведённой работы нет.
        """
        original = self._create(title="Оригинал", task_type="bug")
        move_task(self.tasks, dict(DEFAULTS), original, "Development")

        copy = self._create(title="Оригинал", task_type="bug")

        self.assertEqual("backlog", self._meta(copy).get("status"))

    def test_copy_lands_in_the_rubric_of_its_type(self) -> None:
        """Рубрику бэклога задаёт тип копии — тот же, что у оригинала."""
        same_type = self._create(title="Соседка того же типа", task_type="bug")
        copy = self._create(title="Копия", task_type="bug")

        self.assertEqual(self._rubric_of(same_type), self._rubric_of(copy),
                         "копия легла не в рубрику своего типа")

    def test_copy_inherits_pause_and_blockers(self) -> None:
        """Простой — тоже пользовательские данные: копия ждёт того же."""
        blocker = self._create(title="Блокер")

        copy = self._create(title="Копия", blocked_by=blocker, paused="ждём макеты")

        meta = self._meta(copy)
        self.assertIn(blocker, meta.get("blocked_by", ""))
        self.assertEqual("ждём макеты", meta.get("paused", ""))
        # Зависимость живёт двумя концами: у блокера должна появиться обратная ссылка
        self.assertIn(copy, self._meta(blocker).get("blocks", ""),
                      "у блокера нет обратной ссылки на копию")

    def test_several_blockers_are_copied(self) -> None:
        """У оригинала блокеров может быть несколько — копии нужны все."""
        first = self._create(title="Первый блокер")
        second = self._create(title="Второй блокер")

        copy = self._create(title="Копия", blocked_by=f"{first}, {second}")

        blocked_by = self._meta(copy).get("blocked_by", "")
        for blocker in (first, second):
            self.assertIn(blocker, blocked_by)

    def test_pause_is_optional(self) -> None:
        """Обычное создание не задето: без паузы поле остаётся пустым."""
        task = self._create(title="Обычная")

        self.assertEqual("backlog", self._meta(task).get("status"))
        self.assertNotIn("ждём", self._meta(task).get("paused", ""))


class CopyFormTest(unittest.TestCase):
    """Форма создания умеет режим копии: предзаполнение и наследуемый простой."""

    def setUp(self) -> None:
        self.text = NEW_TASK.read_text(encoding="utf-8")

    def test_form_takes_a_source_task(self) -> None:
        self.assertIn("source", self.text, "форма не знает про копируемую задачу")

    def test_prefills_user_fields(self) -> None:
        """Название, описание, критерии, тип и эпик приходят из оригинала."""
        for field in ("title", "description", "criteria", "task_type", "epic"):
            with self.subTest(field=field):
                self.assertRegex(self.text, rf"source[^\n]*\b{field}\b|{field}[^\n]*source",
                                 f"поле {field} не предзаполняется из оригинала")

    def test_inherited_stall_travels_with_the_copy(self) -> None:
        """Блокировки и пауза наследуются — и их видно до создания копии."""
        self.assertIn("paused", self.text, "пауза оригинала не доезжает до копии")
        self.assertIn("blocked_by", self.text)


class CopyEntryPointsTest(unittest.TestCase):
    """Позвать копирование можно и с превью, и из открытой задачи."""

    def test_context_menu_has_copy_item(self) -> None:
        """Пункт живёт в группе «Копировать» — рядом с номером и содержимым."""
        app = APP.read_text(encoding="utf-8")
        self.assertIn("Задачу целиком", app, "в меню превью нет пункта копирования задачи")
        self.assertRegex(app, r"copyTask|startCopy|openCopy",
                         "меню не зовёт форму копии")
        self.assertLess(app.index("'Копировать'"), app.index("Задачу целиком"),
                        "пункт копии задачи стоит вне группы «Копировать»")

    def test_task_modal_offers_copy(self) -> None:
        modal = TASK_MODAL.read_text(encoding="utf-8")
        self.assertIn("onCopy", modal, "в окне задачи нет кнопки копирования")

    def test_board_passes_source_to_the_form(self) -> None:
        app = APP.read_text(encoding="utf-8")
        self.assertRegex(app, r"<NewTaskModal[\s\S]{0,400}source=",
                         "форма создания не получает копируемую задачу")


if __name__ == "__main__":
    unittest.main()
