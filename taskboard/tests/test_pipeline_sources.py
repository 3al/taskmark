"""Тесты источников жизненного цикла: пресеты и пайплайны других проектов (TASK-031).

Один и тот же маршрут в компании повторяется во всех проектах, а настраивается
per-project — руками по десять раз. Настройки остаются в проекте (скрипты
`tasks/*.py` автономны и читают конфиг рядом с собой), но заполнять их можно,
взяв готовый пайплайн: встроенный пресет или пайплайн соседнего проекта.

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

from backend import config, registry  # noqa: E402
from backend.pipeline_sources import list_sources  # noqa: E402
from backend.statuses import PRESETS  # noqa: E402


class PipelineSourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

        # Реестр и глобальный конфиг ведутся в файлах пользователя — подменяем
        self._orig = (registry.PROJECTS_FILE, registry.GLOBAL_DIR,
                      config.GLOBAL_CONFIG_FILE, config.GLOBAL_DIR)
        registry.PROJECTS_FILE = self.tmp / "projects.json"
        registry.GLOBAL_DIR = self.tmp
        config.GLOBAL_DIR = self.tmp
        config.GLOBAL_CONFIG_FILE = self.tmp / "config.json"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        (registry.PROJECTS_FILE, registry.GLOBAL_DIR,
         config.GLOBAL_CONFIG_FILE, config.GLOBAL_DIR) = self._orig

    def _project(self, name: str, settings: dict | None = None) -> Path:
        tasks_dir = self.tmp / name / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        registry.register_project(tasks_dir, name=name, activate=False)
        if settings is not None:
            (tasks_dir / ".taskboard.json").write_text(
                json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        return tasks_dir

    def _names(self, sources: list[dict], kind: str) -> list[str]:
        return [s["name"] for s in sources if s["kind"] == kind]

    # --- Пресеты ---

    def test_presets_always_offered(self) -> None:
        """Первому проекту брать не у кого — пресеты должны быть всегда."""
        active = self._project("alpha")
        sources = list_sources(active)
        self.assertEqual(len(self._names(sources, "preset")), len(PRESETS))

    def test_preset_carries_pipeline_and_actions(self) -> None:
        source = next(s for s in list_sources(self._project("alpha"))
                      if s["kind"] == "preset")
        self.assertTrue(source["pipeline"], "пресет без статусов нечего применять")
        self.assertIn("start", source["actions"])
        # Статусы приходят разобранными: UI показывает подписи, а не голые ключи
        self.assertTrue(all(s.get("label") and s.get("section")
                            for s in source["pipeline"]))

    # --- Другие проекты реестра ---

    def test_other_project_with_own_pipeline_offered(self) -> None:
        active = self._project("alpha")
        self._project("beta", {"pipeline": ["backlog", "development", "done"],
                               "actions": {"create": "backlog", "start": "development"}})

        source = next(s for s in list_sources(active) if s["kind"] == "project")

        self.assertEqual(source["name"], "beta")
        self.assertEqual([s["key"] for s in source["pipeline"]],
                         ["backlog", "development", "done"])
        self.assertEqual(source["actions"]["start"], "development")

    def test_project_without_own_settings_not_offered(self) -> None:
        """Проект на дефолтах — не источник: копировать у него нечего."""
        active = self._project("alpha")
        self._project("beta", {"dnd_full_board": True})
        self.assertEqual(self._names(list_sources(active), "project"), [])

    def test_active_project_not_offered_to_itself(self) -> None:
        active = self._project("alpha", {"pipeline": ["backlog", "development"]})
        self._project("beta", {"pipeline": ["backlog", "development", "done"]})
        self.assertEqual(self._names(list_sources(active), "project"), ["beta"])

    def test_custom_labels_travel_with_pipeline(self) -> None:
        """Переименованные подписи и разделы — часть маршрута, а не украшение.

        Без них скопированный пайплайн разошёлся бы с исходным по названиям
        колонок доски, то есть перестал быть тем же самым.
        """
        active = self._project("alpha")
        self._project("beta", {
            "pipeline": ["backlog", "development", "done"],
            "statuses": {"development": {"label": "В работе", "section": "В работе"}},
        })

        source = next(s for s in list_sources(active) if s["kind"] == "project")

        self.assertEqual(source["statuses"]["development"]["section"], "В работе")
        dev = next(s for s in source["pipeline"] if s["key"] == "development")
        self.assertEqual(dev["label"], "В работе")

    def test_missing_project_dir_is_skipped(self) -> None:
        """Проект удалён с диска, но остался в реестре — не повод падать."""
        active = self._project("alpha")
        registry.register_project(self.tmp / "ghost" / "tasks", name="ghost", activate=False)
        self.assertEqual(self._names(list_sources(active), "project"), [])


if __name__ == "__main__":
    unittest.main()
