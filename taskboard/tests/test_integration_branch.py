"""Интеграционная ветка проекта и коммиты задач в составе выпуска.

Проверенное коммитится в интеграционную ветку, а выпуск переносит в выпускаемую
только коммиты отобранных задач. Для этого скиллам нужны два ответа скрипта:
какая ветка интеграционная (`--list`) и какие коммиты у каждой задачи состава
(`--changelog`). Схема включена по умолчанию, пустое значение её выключает.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from tests.test_changelog_slice import TASK_FILE, _use_release_pipeline  # noqa: E402
from tests.test_set_status_script import load_script  # noqa: E402

SETTINGS = (Path(__file__).resolve().parent.parent
            / "frontend" / "src" / "components" / "SettingsModal.jsx")


class _Project(unittest.TestCase):
    """Временный проект с релизным маршрутом и пустым глобальным конфигом."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tasks = root / "project" / "tasks"
        self.tasks.mkdir(parents=True)
        _use_release_pipeline(self.tasks)
        # Глобальный конфиг машины не должен влиять на ответ скрипта
        home = root / "home"
        home.mkdir()
        patcher = mock.patch.object(Path, "home", return_value=home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mod = load_script()

    def _set_project(self, **values) -> None:
        path = self.tasks / ".taskboard.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg.update(values)
        path.write_text(json.dumps(cfg), encoding="utf-8")


class IntegrationBranchListTest(_Project):
    """`--list` называет интеграционную ветку проекта."""

    def test_enabled_by_default(self) -> None:
        self.assertEqual(self.mod.describe(self.tasks)["integration_branch"], "dev")

    def test_empty_value_turns_the_scheme_off(self) -> None:
        self._set_project(integration_branch="")
        self.assertEqual(self.mod.describe(self.tasks)["integration_branch"], "")

    def test_own_branch(self) -> None:
        self._set_project(integration_branch="  develop ")
        self.assertEqual(self.mod.describe(self.tasks)["integration_branch"], "develop")


class IntegrationBranchConfigTest(unittest.TestCase):
    """Настройка проекта: дефолт поставки, слой проекта, форма настроек."""

    def test_default_matches_the_script(self) -> None:
        self.assertEqual(config.DEFAULTS["integration_branch"], "dev")
        self.assertEqual(load_script().DEFAULTS["integration_branch"], "dev")

    def test_belongs_to_the_project(self) -> None:
        self.assertIn("integration_branch", config.PROJECT_KEYS)

    def test_empty_value_survives_saving(self) -> None:
        """Пустое значение — выбор «выключено», а не «не задано»."""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks"
            tasks.mkdir()
            with mock.patch.object(config, "GLOBAL_CONFIG_FILE",
                                   Path(tmp) / "global.json"):
                config.save_project_config(tasks, {"integration_branch": ""})
                self.assertEqual(
                    config.load_project_config(tasks)["integration_branch"], "")
            stored = json.loads((tasks / ".taskboard.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["integration_branch"], "")

    def test_settings_form_edits_and_saves_it(self) -> None:
        src = SETTINGS.read_text(encoding="utf-8")
        payload = src[src.index("const updates = ()"):src.index("const check = async")]
        self.assertIn("integration_branch", payload, "ветка не уходит в сохранение")
        release_tab = src[src.index("tab === 'release'"):src.index("tab === 'telegram'")]
        self.assertIn("integration_branch", release_tab, "поля нет на вкладке «Выпуск»")


class ChangelogCommitsTest(_Project):
    """`--changelog` отдаёт коммиты каждой задачи состава."""

    def _add(self, task_id: str, commits: str | None) -> None:
        filename = f"{task_id}-test.md"
        text = TASK_FILE.format(task_id=task_id, title="Задача", status="to_release")
        if commits is None:
            text = text.replace("\n## История коммитов\n", "\n")
        else:
            text += commits
        (self.tasks / filename).write_text(text, encoding="utf-8")

        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        idx = lines.index("## To Release")
        entry = f"- {task_id} · [Задача]({filename}) · Тест · 2026-08-01"
        if lines[idx + 2].strip() == "_(нет)_":
            lines[idx + 2] = entry
        else:
            lines.insert(idx + 2, entry)
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _commits(self) -> list:
        return self.mod.changelog(self.tasks)["tasks"][0]["commits"]

    def test_commits_in_section_order(self) -> None:
        self._add("TASK-001", "\n- `abc1234` TASK-001: первое\n"
                              "- `def5678` TASK-001: доработка\n")
        self.assertEqual(self._commits(), ["abc1234", "def5678"])

    def test_no_section_gives_empty_list(self) -> None:
        self._add("TASK-001", None)
        self.assertEqual(self._commits(), [])

    def test_empty_section_gives_empty_list(self) -> None:
        self._add("TASK-001", "")
        self.assertEqual(self._commits(), [])

    def test_lines_without_hash_are_skipped(self) -> None:
        self._add("TASK-001", "\nкоммиты ниже переписаны после rebase\n"
                              "- `abc1234` TASK-001: первое\n"
                              "- без хэша\n")
        self.assertEqual(self._commits(), ["abc1234"])

    def test_other_fields_stay(self) -> None:
        self._add("TASK-001", "\n- `abc1234` x\n")
        task = self.mod.changelog(self.tasks)["tasks"][0]
        self.assertEqual(set(task), {"id", "title", "file", "notes", "commits"})


if __name__ == "__main__":
    unittest.main()
