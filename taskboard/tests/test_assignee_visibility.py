"""Признак «этап спрашивает исполнителя» виден скиллам (TASK-192).

Скилл передачи спрашивает исполнителя, «если этап его спрашивает», — но
`describe()`, единственный источник знаний о жизненном цикле для скиллов,
собирал этапы поимённо и признак `assignee` терял. Условие оказывалось
непроверяемым, и агент спрашивал имя в проекте, где исполнителей никто не
настраивал.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import SKILLS_TEMPLATES, scaffold_project  # noqa: E402
from tests.test_set_status_script import load_script  # noqa: E402

PIPELINE = ["backlog", "todo", "development", "local_testing", "testing",
            "done", "cancelled"]
ACTIONS = {"create": "backlog", "pick": "todo", "start": "development",
           "return": "development"}


class DescribeTest(unittest.TestCase):
    """Пайплайн в ответе скрипта несёт признак каждого этапа."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        cfg = {**DEFAULTS, "pipeline": PIPELINE, "actions": ACTIONS,
               "harnesses": {"claude": True, "opencode": False}}
        scaffold_project(self.tasks, cfg, {"harnesses": cfg["harnesses"]})
        self.mod = load_script()

    def write_config(self, **extra) -> None:
        (self.tasks / ".taskboard.json").write_text(
            json.dumps({"pipeline": PIPELINE, "actions": ACTIONS, **extra},
                       ensure_ascii=False), encoding="utf-8")

    def stages(self) -> dict[str, dict]:
        self.assertTrue((self.tasks / ".taskboard.json").exists())
        described = self.mod.describe(self.tasks)
        return {s["key"]: s for s in described["pipeline"]}

    def test_flag_present_when_nothing_configured(self) -> None:
        """Ключа в настройках нет — признак есть и равен false у всех этапов."""
        self.write_config()

        stages = self.stages()

        for key, stage in stages.items():
            with self.subTest(key):
                self.assertIn("assignee", stage,
                              "скилл не может проверить условие, которого нет в ответе")
                self.assertFalse(stage["assignee"])

    def test_flag_follows_the_setting(self) -> None:
        """Отмеченный этап — true, остальные — false."""
        self.write_config(assignee_statuses=["testing"])

        stages = self.stages()

        self.assertTrue(stages["testing"]["assignee"])
        self.assertFalse(stages["local_testing"]["assignee"])
        self.assertFalse(stages["development"]["assignee"])

    def test_flag_present_without_a_task(self) -> None:
        """`--list` спрашивают до того, как задача выбрана, — признак нужен и там."""
        self.write_config(assignee_statuses=["testing"])

        described = self.mod.describe(self.tasks)

        self.assertTrue(any(s.get("assignee") for s in described["pipeline"]))


class HandoffSkillTest(unittest.TestCase):
    """Шаг про исполнителя начинается с проверки, а не с вопроса человеку."""

    def setUp(self) -> None:
        self.text = (SKILLS_TEMPLATES / "handoff-task"
                     / "SKILL.md").read_text(encoding="utf-8")

    def step(self) -> str:
        start = self.text.index("## Шаг 4.5")
        return self.text[start:self.text.index("## Шаг 5", start)]

    def test_step_names_the_flag(self) -> None:
        self.assertIn("assignee", self.step())

    def test_step_says_to_skip_silently(self) -> None:
        """Этап исполнителя не спрашивает — вопроса человеку быть не должно."""
        step = self.step().lower()
        self.assertIn("пропусти", step)

    def test_pipeline_field_is_named_in_step_zero(self) -> None:
        """Шаг 0 перечисляет, что берут из ответа, — признак должен быть там."""
        zero = self.text[self.text.index("## Шаг 0"):self.text.index("## Шаг 1")]
        self.assertIn("assignee", zero)


if __name__ == "__main__":
    unittest.main()
