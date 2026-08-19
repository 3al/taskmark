# -*- coding: utf-8 -*-
"""Тесты исполнителя задачи (TASK-046).

Исполнитель — человек, который занимается задачей на этапах проверки: поле
`assignee:` во frontmatter, правится из окна задачи. Он есть не в каждом
статусе — этап объявляет это флагом в пайплайне, — а список имён общий для
машины: заведённое в одном проекте предлагается во всех.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from backend import config as config_mod  # noqa: E402
from backend import registry  # noqa: E402
from backend.app import (TaskIn, TaskUpdateIn, api_assignees,  # noqa: E402
                         api_create_task, api_task, api_update_task)
from backend.config import (DEFAULTS, add_assignee, assignees,  # noqa: E402
                            load_project_config, save_project_config)
from backend.requirements import (entry_requirements, move_debt,  # noqa: E402
                                  task_debt)
from backend.scaffold import scaffold_project  # noqa: E402
from backend.statuses import accepts_assignee, load_pipeline  # noqa: E402
from backend.task_parser import (parse_task, set_task_assignee,  # noqa: E402
                                 set_task_status)

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


class AssigneeStatusesTest(unittest.TestCase):
    """Исполнитель — свойство этапа: его объявляет пайплайн, а не задача."""

    def test_delivery_asks_nowhere(self) -> None:
        """Поставка не отмечает ни одного этапа.

        Исполнитель на этапе обязателен, и включённая по умолчанию галочка
        доехала бы обновлением до проектов, которые о ней не просили: агент
        упёрся бы в требование, которого никто не объявлял.
        """
        pipeline = load_pipeline({"pipeline": ["backlog", "development", "review",
                                               "to_testing", "testing", "done"]})
        for key in pipeline.keys():
            with self.subTest(status=key):
                self.assertFalse(pipeline.get(key).get("assignee"),
                                 f"поставка отметила статус «{key}»")

    def test_checked_statuses_ask(self) -> None:
        """Отмеченный в настройках этап спрашивает исполнителя, остальные — нет."""
        pipeline = load_pipeline({
            "pipeline": ["backlog", "development", "review", "testing", "done"],
            "assignee_statuses": ["review", "testing"]})
        self.assertTrue(pipeline.get("testing").get("assignee"))
        self.assertTrue(pipeline.get("review").get("assignee"))
        self.assertFalse(pipeline.get("development").get("assignee"))

    def test_empty_list_turns_the_field_off(self) -> None:
        """Снятые галочки — «нигде», а не «вернуть как было»."""
        pipeline = load_pipeline({
            "pipeline": ["backlog", "development", "testing", "done"],
            "assignee_statuses": []})
        for key in pipeline.keys():
            self.assertFalse(pipeline.get(key).get("assignee"), key)

    def test_unknown_status_does_not_accept(self) -> None:
        """Статус выключили из маршрута, задача осталась — назначать некому."""
        pipeline = load_pipeline({"pipeline": ["backlog", "testing", "done"],
                                  "assignee_statuses": ["testing"]})
        self.assertFalse(accepts_assignee(pipeline, "review"))
        self.assertTrue(accepts_assignee(pipeline, "testing"))


class AssigneeListTest(unittest.TestCase):
    """Список имён — общий для машины: он живёт в глобальном конфиге."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self._saved = (config_mod.GLOBAL_CONFIG_FILE, config_mod.GLOBAL_DIR)
        config_mod.GLOBAL_CONFIG_FILE = tmp / "config.json"
        config_mod.GLOBAL_DIR = tmp
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        config_mod.GLOBAL_CONFIG_FILE, config_mod.GLOBAL_DIR = self._saved

    def test_empty_by_default(self) -> None:
        """Имён поставка не знает: их приносит работа, а не установка."""
        self.assertEqual([], assignees())

    def test_name_remembered(self) -> None:
        add_assignee("Иванов")
        self.assertEqual(["Иванов"], assignees())

    def test_same_name_stored_once(self) -> None:
        add_assignee("Иванов")
        add_assignee("Иванов")
        self.assertEqual(["Иванов"], assignees())

    def test_name_trimmed(self) -> None:
        add_assignee("  Иванов  ")
        self.assertEqual(["Иванов"], assignees())

    def test_empty_name_ignored(self) -> None:
        add_assignee("   ")
        self.assertEqual([], assignees())


class AssigneeCase(unittest.TestCase):
    """Временный проект: правка идёт теми же функциями, что и из окна."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tasks = tmp / "project" / "tasks"

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
        cfg["pipeline"] = ["backlog", "todo", "development", "review", "testing", "done"]
        scaffold_project(self.tasks, cfg, {"harnesses": cfg["harnesses"]})
        # Галочка «исполнитель» у этапов проверки: поставка их не отмечает,
        # поэтому проект объявляет это сам — как это делают настройки
        save_project_config(self.tasks, {"pipeline": cfg["pipeline"],
                                         "assignee_statuses": ["review", "testing"]})
        registry.register_project(self.tasks, name="project")

        result = api_create_task(TaskIn(title="Задача под исполнителя",
                                        description="описание", criteria="критерии"))
        self.task_id = result["id"]

    def _restore(self) -> None:
        registry.PROJECTS_FILE = self._saved["projects"]
        registry.GLOBAL_DIR = self._saved["dir"]
        config_mod.GLOBAL_CONFIG_FILE = self._saved["cfg_file"]
        config_mod.GLOBAL_DIR = self._saved["cfg_dir"]

    def _meta(self) -> dict:
        return (parse_task(self.tasks, self.task_id) or {}).get("meta", {})

    def _text(self) -> str:
        return next(self.tasks.glob(f"{self.task_id}-*.md")).read_text(encoding="utf-8")

    def _notes(self) -> list[str]:
        text = self._text()
        body = text[text.index("## Комментарии") + len("## Комментарии"):]
        end = body.find("\n## ")
        body = body[:end] if end >= 0 else body
        return [ln for ln in body.splitlines() if ln.strip().startswith("- ")]

    def _at(self, status: str) -> None:
        """Поставить задаче статус, не двигая её по доске: важен только он."""
        set_task_status(self.tasks, self.task_id, status)


class AssigneeFileTest(AssigneeCase):
    """Имя пишется во frontmatter, смена уходит в хронологию задачи."""

    def test_name_written(self) -> None:
        result = set_task_assignee(self.tasks, self.task_id, "Иванов")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("Иванов", self._meta().get("assignee"))

    def test_change_goes_to_history(self) -> None:
        """Кто занимается задачей — событие: во frontmatter видно только «сейчас»."""
        set_task_assignee(self.tasks, self.task_id, "Иванов")
        set_task_assignee(self.tasks, self.task_id, "Петров")
        self.assertTrue(any("Петров" in ln for ln in self._notes()),
                        "смена исполнителя не записана в хронологию")

    def test_same_name_is_not_an_event(self) -> None:
        set_task_assignee(self.tasks, self.task_id, "Иванов")
        before = len(self._notes())
        set_task_assignee(self.tasks, self.task_id, "Иванов")
        self.assertEqual(before, len(self._notes()),
                         "повтор того же имени записан событием")

    def test_name_cleared(self) -> None:
        """Назначение снимают: человек мог уйти, а задача остаться."""
        set_task_assignee(self.tasks, self.task_id, "Иванов")
        result = set_task_assignee(self.tasks, self.task_id, "")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("~", self._meta().get("assignee"))

    def test_unknown_task_refused(self) -> None:
        self.assertFalse(set_task_assignee(self.tasks, "TASK-999", "Иванов").get("ok"))


class AssigneeApiTest(AssigneeCase):
    """Правка из окна: этап решает, можно ли назначать, список пополняется сам."""

    def test_assigned_on_a_status_that_asks_for_it(self) -> None:
        self._at("testing")
        result = api_update_task(self.task_id, TaskUpdateIn(assignee="Иванов"))
        self.assertEqual("Иванов", result.get("assignee"), result)
        self.assertEqual("Иванов", self._meta().get("assignee"))

    def test_refused_on_a_status_that_does_not(self) -> None:
        """Правило одно и живёт на бэкенде: UI поле прячет, API — отказывает."""
        self._at("development")
        with self.assertRaises(HTTPException) as caught:
            api_update_task(self.task_id, TaskUpdateIn(assignee="Иванов"))
        self.assertEqual(400, caught.exception.status_code)
        self.assertNotIn("assignee: Иванов", self._text())

    def test_clearing_allowed_anywhere(self) -> None:
        """Снять можно всегда — иначе имя, застрявшее после переноса, не убрать."""
        self._at("testing")
        api_update_task(self.task_id, TaskUpdateIn(assignee="Иванов"))
        self._at("development")
        result = api_update_task(self.task_id, TaskUpdateIn(assignee=""))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("~", self._meta().get("assignee"))

    def test_new_name_joins_the_list(self) -> None:
        """Имя вводят один раз: дальше оно предлагается подсказкой."""
        self._at("testing")
        api_update_task(self.task_id, TaskUpdateIn(assignee="Иванов"))
        self.assertIn("Иванов", api_assignees()["items"])

    def test_task_says_whether_it_can_be_assigned(self) -> None:
        """Окно не считает правило само: признак приезжает с задачей."""
        self._at("testing")
        self.assertTrue(api_task(self.task_id).get("can_assign"))
        self._at("development")
        self.assertFalse(api_task(self.task_id).get("can_assign"))


class AssigneeDebtTest(AssigneeCase):
    """Незаполненный исполнитель — долг этапа, а не тихая пустота."""

    def _cfg(self) -> dict:
        return load_project_config(self.tasks)

    def _debt_ids(self) -> list[str]:
        result = task_debt(self.tasks, self.task_id, self._cfg())
        return [r.get("id") for r in (result.get("debt") or [])]

    def test_entry_requirement_only_where_the_stage_asks(self) -> None:
        pipeline = load_pipeline({"pipeline": ["backlog", "development",
                                               "testing", "done", "cancelled"],
                                  "assignee_statuses": ["testing"]})
        self.assertTrue(entry_requirements(pipeline, "testing"))
        self.assertEqual([], entry_requirements(pipeline, "development"))

    def test_offramp_asks_nobody(self) -> None:
        """Отменённой задачей никто не занимается — имени с неё не спрашивают."""
        pipeline = load_pipeline({"pipeline": ["backlog", "testing", "cancelled"],
                                  "assignee_statuses": ["testing", "cancelled"]})
        self.assertEqual([], entry_requirements(pipeline, "cancelled"))

    def test_entry_requirement_is_marked_as_such(self) -> None:
        """Признак `entry` отличает его от долга, который закроет агент."""
        pipeline = load_pipeline({"pipeline": ["backlog", "testing", "done"],
                                  "assignee_statuses": ["testing"]})
        self.assertTrue(entry_requirements(pipeline, "testing")[0].get("entry"))

    def test_debt_appears_on_the_stage_itself(self) -> None:
        """Долг виден, пока задача стоит на этапе, а не при попытке уйти с него."""
        self._at("testing")
        self.assertIn("assignee", self._debt_ids())

    def test_debt_gone_once_assigned(self) -> None:
        self._at("testing")
        set_task_assignee(self.tasks, self.task_id, "Иванов")
        self.assertNotIn("assignee", self._debt_ids())

    def test_no_debt_where_the_stage_does_not_ask(self) -> None:
        self._at("development")
        self.assertNotIn("assignee", self._debt_ids())

    def test_requirement_says_what_to_do_not_what_is_done(self) -> None:
        """Указание, а не утверждение: «исполнитель назначен» в списке дел
        читается как «уже есть», и человек не понимает, чего от него хотят."""
        pipeline = load_pipeline({"pipeline": ["backlog", "testing", "done"],
                                  "assignee_statuses": ["testing"]})
        req = entry_requirements(pipeline, "testing")[0]
        self.assertEqual("назначить исполнителя", req.get("todo"))

    def test_move_debt_warns_before_the_drop(self) -> None:
        """Цену переноса человек видит заранее: доска спрашивает до движения."""
        self._at("development")
        debt = move_debt(self.tasks, self.task_id, self._cfg(), "testing")
        self.assertIn("assignee", [r.get("id") for r in debt])


class AssigneeScriptTest(unittest.TestCase):
    """Агентский путь: без имени этап не пропускает, имя ставится тем же вызовом."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.home = tmp / "home"
        self.home.mkdir()
        self.tasks = tmp / "project" / "tasks"

        cfg = dict(DEFAULTS)
        cfg["harnesses"] = {"claude": True, "opencode": False}
        cfg["pipeline"] = ["backlog", "todo", "development", "testing", "done"]
        scaffold_project(self.tasks, cfg, {"harnesses": cfg["harnesses"]})
        save_project_config(self.tasks, {"pipeline": cfg["pipeline"],
                                         "assignee_statuses": ["testing"]})
        self.root = self.tasks.parent
        self._run(self.tasks / "create_task.py",
                  "-t", "Проверка", "-d", "описание", "-c", "критерии")

    def _run(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        import os
        # Список имён общий для машины и лежит в ~/.taskboard: подменяем дом,
        # иначе тест пишет в конфиг пользователя
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        return subprocess.run([sys.executable, str(script), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              cwd=str(self.root), env=env)

    def _status(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(self.tasks / "set_status.py", *args)

    def _meta(self) -> dict:
        from backend.task_parser import parse_frontmatter
        path = next(self.tasks.glob("TASK-001-*.md"))
        meta, _rest = parse_frontmatter(path.read_text(encoding="utf-8"))
        return meta

    def test_stage_refuses_without_a_name(self) -> None:
        result = self._status("TASK-001", "testing", "--agent", "Модель")
        self.assertNotEqual(0, result.returncode, "этап пропустил задачу без имени")
        self.assertIn("--assignee", result.stderr)
        self.assertEqual("backlog", self._meta().get("status"),
                         "отказ гейта всё-таки сдвинул задачу")

    def test_name_and_move_in_one_call(self) -> None:
        result = self._status("TASK-001", "testing", "--agent", "Модель",
                              "--assignee", "Иванов")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("testing", self._meta().get("status"))
        self.assertEqual("Иванов", self._meta().get("assignee"))

    def test_name_remembered_for_other_projects(self) -> None:
        self._status("TASK-001", "testing", "--agent", "Модель", "--assignee", "Иванов")
        listed = self._status("--assignees")
        self.assertIn("Иванов", json.loads(listed.stdout)["assignees"])

    def test_stage_that_does_not_ask_refuses_the_name(self) -> None:
        result = self._status("TASK-001", "--assignee", "Иванов", "--agent", "Модель")
        self.assertNotEqual(0, result.returncode, "имя принято на этапе без исполнителя")

    def test_name_cleared_anywhere(self) -> None:
        self._status("TASK-001", "testing", "--agent", "Модель", "--assignee", "Иванов")
        self._status("TASK-001", "development", "--agent", "Модель")
        result = self._status("TASK-001", "--assignee", "", "--agent", "Модель")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("~", self._meta().get("assignee"))

    def test_script_debt_matches_the_board(self) -> None:
        """Зеркала судят одинаково: агент и доска не должны видеть разный долг."""
        self._status("TASK-001", "testing", "--agent", "Модель", "--assignee", "Иванов")
        self._status("TASK-001", "--assignee", "", "--agent", "Модель")
        mine = json.loads(self._status("--debt", "TASK-001").stdout)
        self.assertIn("assignee", [r.get("id") for r in mine.get("debt", [])])
        theirs = task_debt(self.tasks, "TASK-001", load_project_config(self.tasks))
        self.assertEqual([r.get("id") for r in mine.get("debt", [])],
                         [r.get("id") for r in theirs.get("debt", [])])


class AssigneeUiTest(unittest.TestCase):
    """Поле в окне задачи и галочка у статуса в настройках."""

    def test_modal_has_the_field(self) -> None:
        text = (SRC / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("assignee", text, "в окне задачи нет исполнителя")

    def test_api_client_knows_the_list(self) -> None:
        text = (SRC / "api.js").read_text(encoding="utf-8")
        self.assertIn("assignees", text, "клиент не умеет спрашивать список имён")

    def test_move_dialog_speaks_in_imperative(self) -> None:
        """Диалог переноса говорит, что сделать, а не перечисляет утверждения."""
        text = (SRC / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("d.todo", text,
                      "диалог показывает формулировку долга вместо указания")

    def test_pipeline_editor_has_the_checkbox(self) -> None:
        text = (SRC / "components" / "PipelineEditor.jsx").read_text(encoding="utf-8")
        self.assertIn("assignee", text, "у статуса нет галочки «исполнитель»")

    def test_card_preview_stays_clean(self) -> None:
        """На превью исполнителя нет: там и так тесно (решение по TASK-046)."""
        text = (SRC / "components" / "TaskCard.jsx").read_text(encoding="utf-8")
        self.assertNotIn("assignee", text)


if __name__ == "__main__":
    unittest.main()
