# -*- coding: utf-8 -*-
"""Тесты автора задачи (TASK-220).

Автор — тот, кто задачу **принёс**, в отличие от исполнителя, который ею
занимается. Поле `author:` во frontmatter проставляется при заведении: из чата
это ник отправителя, из формы доски — «доска», у агента — имя его модели.

Список известных авторов ведётся **отдельно от исполнителей**: у исполнителей
ФИО, у авторов ник из чата, и общая подсказка стала бы мешаниной.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config as config_mod  # noqa: E402
from backend import registry  # noqa: E402
from backend.app import TaskIn, api_create_task  # noqa: E402
from backend.config import (DEFAULTS, add_assignee, add_author,  # noqa: E402
                            assignees, authors, save_project_config)
from backend.notes import BOARD_AUTHOR  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.task_parser import parse_task, set_task_author  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "tasks" / "_TEMPLATE.md"


class AuthorTemplateTest(unittest.TestCase):
    """Поле объявлено в шаблоне: новая задача заводится с ним, а не без."""

    def test_template_declares_author(self) -> None:
        head = TEMPLATE.read_text(encoding="utf-8").split("---")[1]
        self.assertIn("author:", head, "шаблон задачи не объявляет автора")


class AuthorListTest(unittest.TestCase):
    """Список авторов — свой, и с исполнителями он не смешивается."""

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
        self.assertEqual([], authors())

    def test_name_remembered(self) -> None:
        add_author("@ivanov")
        self.assertEqual(["@ivanov"], authors())

    def test_same_name_stored_once(self) -> None:
        add_author("@ivanov")
        add_author("@ivanov")
        self.assertEqual(["@ivanov"], authors())

    def test_name_trimmed(self) -> None:
        add_author("  @ivanov  ")
        self.assertEqual(["@ivanov"], authors())

    def test_empty_name_ignored(self) -> None:
        add_author("   ")
        self.assertEqual([], authors())

    def test_lists_do_not_mix(self) -> None:
        """Два списка живут врозь: имя автора не предлагается исполнителем."""
        add_author("@ivanov")
        add_assignee("Иванов Иван Иванович")
        self.assertEqual(["@ivanov"], authors())
        self.assertEqual(["Иванов Иван Иванович"], assignees())


class AuthorCase(unittest.TestCase):
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
        scaffold_project(self.tasks, cfg, {"harnesses": cfg["harnesses"]})
        save_project_config(self.tasks, {"pipeline": cfg["pipeline"]})
        registry.register_project(self.tasks, name="project")

        result = api_create_task(TaskIn(title="Задача под автора",
                                        description="описание", criteria="критерии"))
        self.task_id = result["id"]

    def _restore(self) -> None:
        registry.PROJECTS_FILE = self._saved["projects"]
        registry.GLOBAL_DIR = self._saved["dir"]
        config_mod.GLOBAL_CONFIG_FILE = self._saved["cfg_file"]
        config_mod.GLOBAL_DIR = self._saved["cfg_dir"]

    def _meta(self) -> dict:
        return (parse_task(self.tasks, self.task_id) or {}).get("meta", {})

    def _notes(self) -> list[str]:
        text = next(self.tasks.glob(f"{self.task_id}-*.md")).read_text(encoding="utf-8")
        body = text[text.index("## Комментарии") + len("## Комментарии"):]
        end = body.find("\n## ")
        body = body[:end] if end >= 0 else body
        return [ln for ln in body.splitlines() if ln.strip().startswith("- ")]


class AuthorFileTest(AuthorCase):
    """Имя пишется во frontmatter, смена уходит в хронологию задачи."""

    def test_name_written(self) -> None:
        result = set_task_author(self.tasks, self.task_id, "@ivanov")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("@ivanov", self._meta().get("author"))

    def test_change_goes_to_history(self) -> None:
        """Автора правят редко, но если правят — это событие, а не опечатка."""
        set_task_author(self.tasks, self.task_id, "@ivanov")
        set_task_author(self.tasks, self.task_id, "@petrov")
        self.assertTrue(any("@petrov" in ln for ln in self._notes()),
                        "смена автора не записана в хронологию")

    def test_same_name_is_not_an_event(self) -> None:
        set_task_author(self.tasks, self.task_id, "@ivanov")
        before = len(self._notes())
        set_task_author(self.tasks, self.task_id, "@ivanov")
        self.assertEqual(before, len(self._notes()),
                         "повтор того же имени записан событием")

    def test_name_cleared(self) -> None:
        set_task_author(self.tasks, self.task_id, "@ivanov")
        result = set_task_author(self.tasks, self.task_id, "")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual("~", self._meta().get("author"))

    def test_unknown_task_refused(self) -> None:
        self.assertFalse(set_task_author(self.tasks, "TASK-999", "@ivanov").get("ok"))

    def test_board_author_is_the_same_word(self) -> None:
        """Задачу завела доска — имя берётся из готовой константы, не из строки."""
        set_task_author(self.tasks, self.task_id, BOARD_AUTHOR)
        self.assertEqual("доска", self._meta().get("author"))


class AuthorFromBoardTest(AuthorCase):
    """Задачу завела форма доски — автором становится сама доска."""

    def test_board_is_the_author(self) -> None:
        self.assertEqual(BOARD_AUTHOR, self._meta().get("author"))

    def test_board_joins_the_list(self) -> None:
        """«Доска» попадает в подсказки наравне с людьми: это тоже ответ."""
        self.assertIn(BOARD_AUTHOR, authors())


class AuthorScriptTest(unittest.TestCase):
    """Скрипт создания принимает автора флагом: путей заведения три, флаг один.

    Дом подменяется: список авторов живёт в глобальном конфиге, и без подмены
    имя из теста осело бы в конфиге пользователя.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.home = tmp / "home"
        self.tasks = tmp / "project" / "tasks"
        cfg = dict(DEFAULTS)
        cfg["harnesses"] = {"claude": True, "opencode": False}
        scaffold_project(self.tasks, cfg, {"harnesses": cfg["harnesses"]})

    def create(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        return subprocess.run(
            [sys.executable, str(self.tasks / "create_task.py"),
             "-t", "Проба автора", "-d", "текст", "-c", "", *args],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(self.tasks.parent), env=env)

    def _frontmatter(self) -> dict:
        text = next(self.tasks.glob("TASK-*.md")).read_text(encoding="utf-8")
        out = {}
        for line in text.split("---")[1].splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                out[key.strip()] = value.strip()
        return out

    def _known(self) -> list:
        path = self.home / ".taskboard" / "config.json"
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("authors") or []

    def test_author_written_to_frontmatter(self) -> None:
        result = self.create("--author", "@ivanov")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("@ivanov", self._frontmatter().get("author"))

    def test_script_does_not_touch_the_global_config(self) -> None:
        """Список подсказок ведёт инструмент, а не скрипт.

        Скрипт автономен и о доме пользователя судить не должен: запущенный
        бэкендом, он писал бы конфиг мимо того, что бэкенд считает своим, — а в
        тестах и вовсе в настоящий конфиг человека.
        """
        self.create("--author", "@ivanov")
        self.assertEqual([], self._known())

    def test_without_the_flag_the_field_is_empty(self) -> None:
        """Поле есть всегда, значение — нет: у старых вызовов автора не было."""
        self.create()
        self.assertEqual("~", self._frontmatter().get("author"))

    def test_field_order_follows_the_template(self) -> None:
        """Порядок полей шапки — из `_TEMPLATE.md`: автор сразу за созданием."""
        self.create("--author", "@ivanov")
        keys = list(self._frontmatter())
        self.assertEqual("author", keys[keys.index("created") + 1])


if __name__ == "__main__":

    unittest.main()
