"""Тесты реестра проектов: разрегистрация и переключение активного.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import registry  # noqa: E402


class RemoveProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        # Реестр ведётся в файле — подменяем пути на временные
        self._orig_file = registry.PROJECTS_FILE
        self._orig_dir = registry.GLOBAL_DIR
        registry.PROJECTS_FILE = tmp / "projects.json"
        registry.GLOBAL_DIR = tmp
        self.addCleanup(self._restore)

        for name in ("alpha", "beta", "gamma"):
            registry.register_project(tmp / name / "tasks", name=name, activate=False)

    def _restore(self) -> None:
        registry.PROJECTS_FILE = self._orig_file
        registry.GLOBAL_DIR = self._orig_dir

    def test_remove_active_switches_to_next_in_list(self) -> None:
        registry.activate_project("alpha")
        self.assertTrue(registry.remove_project("alpha"))
        # Следующий по списку после удалённого становится активным
        self.assertEqual(registry.list_projects()["active"], "beta")

    def test_remove_active_last_switches_to_previous(self) -> None:
        registry.activate_project("gamma")
        self.assertTrue(registry.remove_project("gamma"))
        # Следующего нет — активным становится предыдущий (новый последний)
        self.assertEqual(registry.list_projects()["active"], "beta")

    def test_remove_inactive_keeps_active(self) -> None:
        registry.activate_project("gamma")
        self.assertTrue(registry.remove_project("alpha"))
        self.assertEqual(registry.list_projects()["active"], "gamma")

    def test_remove_last_project_clears_active(self) -> None:
        for name in ("alpha", "beta", "gamma"):
            registry.remove_project(name)
        self.assertIsNone(registry.list_projects()["active"])

    def test_remove_missing_returns_false(self) -> None:
        self.assertFalse(registry.remove_project("nonexistent"))


if __name__ == "__main__":
    unittest.main()


class ProjectOrderTest(unittest.TestCase):
    """Список проектов отдаётся по имени, а не в порядке добавления (TASK-201)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        orig_file, orig_dir = registry.PROJECTS_FILE, registry.GLOBAL_DIR
        registry.PROJECTS_FILE = self.tmp / "projects.json"
        registry.GLOBAL_DIR = self.tmp

        def restore() -> None:
            registry.PROJECTS_FILE, registry.GLOBAL_DIR = orig_file, orig_dir
        self.addCleanup(restore)

    def _names(self) -> list[str]:
        return [p["name"] for p in registry.list_projects()["projects"]]

    def test_по_имени(self) -> None:
        for name in ("taskboard", "Imagelib", "alpha", "Zeta", "бета"):
            registry.register_project(self.tmp / name / "tasks", name=name, activate=False)
        self.assertEqual(["alpha", "Imagelib", "taskboard", "Zeta", "бета"], self._names())

    def test_регистр_не_влияет(self) -> None:
        for name in ("b", "A", "a2", "B1"):
            registry.register_project(self.tmp / name / "tasks", name=name, activate=False)
        self.assertEqual(["A", "a2", "b", "B1"], self._names())

    def test_файл_реестра_не_переписывается(self) -> None:
        for name in ("zeta", "alpha"):
            registry.register_project(self.tmp / name / "tasks", name=name, activate=False)
        before = registry.PROJECTS_FILE.read_text(encoding="utf-8")
        registry.list_projects()
        self.assertEqual(before, registry.PROJECTS_FILE.read_text(encoding="utf-8"))

    def test_активный_сохраняется(self) -> None:
        for name in ("zeta", "alpha"):
            registry.register_project(self.tmp / name / "tasks", name=name, activate=False)
        registry.activate_project("zeta")
        self.assertEqual("zeta", registry.list_projects()["active"])
