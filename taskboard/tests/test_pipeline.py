"""Тесты пайплайна статусов: каталог, направления переходов, действия.

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

from backend.statuses import CATALOG, load_pipeline  # noqa: E402


class CatalogTest(unittest.TestCase):
    def test_every_brick_has_defaults(self) -> None:
        """У каждого кирпичика есть подпись, раздел доски и цвет."""
        for key, meta in CATALOG.items():
            for field in ("label", "section", "color"):
                self.assertTrue(meta.get(field), f"{key}: пустое поле {field}")

    def test_catalog_covers_agreed_bricks(self) -> None:
        """Состав каталога согласован с пользователем — сузиться он не должен."""
        expected = {"backlog", "todo", "queued", "to_fix", "development", "local_testing",
                    "review", "to_testing", "testing", "to_release",
                    "completed", "done", "cancelled"}
        self.assertEqual(expected, set(CATALOG))

    def test_only_cancelled_is_offramp(self) -> None:
        offramps = {k for k, m in CATALOG.items() if m.get("offramp")}
        self.assertEqual({"cancelled"}, offramps)


class PipelineOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = load_pipeline({"pipeline": [
            "backlog", "todo", "to_fix", "development", "local_testing",
            "review", "to_testing", "testing", "to_release", "done", "cancelled"]})

    def test_order_comes_from_config_list(self) -> None:
        """Порядок задаёт сам список, а не каталог."""
        p = load_pipeline({"pipeline": ["backlog", "testing", "development", "done"]})
        self.assertEqual(["backlog", "testing", "development", "done"], p.keys())

    def test_forward_includes_every_later_status(self) -> None:
        """Прыжки вперёд законны: пайплайн — маршрут, а не забор.

        development → testing (простая задача) и development → done (ночной
        хотфикс) — реальные ситуации, гонять задачу по всем статусам ради
        формальности бессмысленно.
        """
        forward = self.p.forward("development")
        self.assertIn("testing", forward)
        self.assertIn("done", forward)
        self.assertNotIn("to_fix", forward, "возвратный статус левее — это назад")

    def test_backward_includes_every_earlier_status(self) -> None:
        backward = self.p.backward("testing")
        self.assertIn("to_fix", backward, "возврат багов из тестирования — движение назад")
        self.assertIn("backlog", backward)
        self.assertNotIn("to_release", backward)

    def test_next_expected_is_nearest_forward(self) -> None:
        self.assertEqual("local_testing", self.p.next_expected("development"))
        self.assertEqual("development", self.p.next_expected("to_fix"),
                         "из возвратного статуса ожидаемый шаг — снова в работу")

    def test_terminal_has_no_next(self) -> None:
        self.assertIsNone(self.p.next_expected("done"))

    def test_unknown_status_has_no_directions(self) -> None:
        """Статус вне пайплайна (остался от прежней конфигурации) не ломает разбор."""
        self.assertEqual([], self.p.forward("legacy_status"))
        self.assertEqual([], self.p.backward("legacy_status"))
        self.assertIsNone(self.p.next_expected("legacy_status"))


class OfframpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = load_pipeline({"pipeline": [
            "backlog", "development", "testing", "completed", "cancelled"]})

    def test_reachable_from_everywhere(self) -> None:
        """Отменить задачу можно в любой момент."""
        for key in ("backlog", "development", "testing", "completed"):
            self.assertIn("cancelled", self.p.forward(key), f"из {key} нельзя отменить")

    def test_never_expected(self) -> None:
        """Иначе для completed ожидаемым следующим шагом оказалась бы отмена."""
        self.assertIsNone(self.p.next_expected("completed"))
        self.assertEqual("testing", self.p.next_expected("development"))

    def test_exit_backwards_to_any(self) -> None:
        """Отменённую задачу можно реанимировать в любой статус."""
        backward = self.p.backward("cancelled")
        for key in ("backlog", "development", "testing", "completed"):
            self.assertIn(key, backward)


class ActionsTest(unittest.TestCase):
    def test_pick_defaults_to_status_before_start(self) -> None:
        """С очередью работу берут из неё."""
        p = load_pipeline({"pipeline": ["backlog", "todo", "development", "done"],
                           "actions": {"create": "backlog", "start": "development"}})
        self.assertEqual("todo", p.action("pick"))

    def test_pick_falls_back_to_backlog_without_queue(self) -> None:
        """Очередь необязательна: без неё работу берут прямо из бэклога."""
        p = load_pipeline({"pipeline": ["backlog", "development", "done"],
                           "actions": {"create": "backlog", "start": "development"}})
        self.assertEqual("backlog", p.action("pick"))

    def test_return_may_point_to_custom_status(self) -> None:
        """Флоу с отдельной доработкой: fix-task ведёт туда, не зная имён."""
        p = load_pipeline({
            "pipeline": ["backlog", "rework", "development", "review", "done"],
            "actions": {"create": "backlog", "start": "development", "return": "rework"},
            "statuses": {"rework": {"label": "Доработка", "section": "Rework", "color": "rose"}},
        })
        self.assertEqual("rework", p.action("return"))
        self.assertEqual("Rework", p.section_of("rework"))

    def test_pick_skips_reentry_status(self) -> None:
        """to_fix стоит перед development, но работу берут из очереди, не из него."""
        p = load_pipeline({
            "pipeline": ["backlog", "todo", "to_fix", "development", "completed"],
            "actions": {"create": "backlog", "start": "development"},
        })
        self.assertEqual("todo", p.action("pick"))

    def test_reentry_is_not_a_skipped_step(self) -> None:
        """Маршрут через возвратный статус не проходит — «минуя to_fix» неверно."""
        p = load_pipeline({
            "pipeline": ["backlog", "todo", "to_fix", "development", "completed"]})
        self.assertEqual(["todo"], p.skipped("backlog", "development"))

    def test_return_defaults_to_start(self) -> None:
        p = load_pipeline({"pipeline": ["backlog", "development", "done"],
                           "actions": {"start": "development"}})
        self.assertEqual("development", p.action("return"))


class PresentationTest(unittest.TestCase):
    def test_defaults_come_from_catalog(self) -> None:
        p = load_pipeline({"pipeline": ["backlog", "development", "completed"]})
        self.assertEqual("Development", p.section_of("development"))
        self.assertEqual(CATALOG["development"]["color"], p.get("development")["color"])

    def test_project_overrides_catalog(self) -> None:
        """Переименование раздела — то, что раньше умел только queue_section."""
        p = load_pipeline({
            "pipeline": ["backlog", "queued", "development"],
            "statuses": {"queued": {"label": "To Do", "section": "To Do"}},
        })
        self.assertEqual("To Do", p.section_of("queued"))
        self.assertEqual("To Do", p.get("queued")["label"])
        self.assertEqual(CATALOG["queued"]["color"], p.get("queued")["color"],
                         "неуказанные поля берутся из каталога")

    def test_status_for_section_is_case_insensitive(self) -> None:
        p = load_pipeline({"pipeline": ["backlog", "development"]})
        self.assertEqual("development", p.status_for_section("  DEVELOPMENT "))
        self.assertIsNone(p.status_for_section("Чужой раздел"))

    def test_custom_status_without_meta_still_usable(self) -> None:
        """Ключ есть в пайплайне, а описания нет — раздел выводится из ключа."""
        p = load_pipeline({"pipeline": ["backlog", "hotfix_wait", "done"]})
        self.assertEqual("Hotfix Wait", p.section_of("hotfix_wait"))
        self.assertEqual("hotfix_wait", p.status_for_section("Hotfix Wait"))


class ProjectConfigLocationTest(unittest.TestCase):
    """Конфиг проекта не должен попадать в его git-дерево."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        self.tasks.mkdir(parents=True)

    def test_config_lives_inside_tasks(self) -> None:
        """tasks/ игнорируется git (scaffold кладёт туда .gitignore с `*`),
        поэтому настройки не засоряют чужой репозиторий."""
        from backend.config import project_config_path

        path = project_config_path(self.tasks)
        self.assertEqual(self.tasks, path.parent)
        self.assertNotIn("taskboard", path.relative_to(self.tasks.parent).parts[:-1])

    def test_legacy_location_still_read(self) -> None:
        """Конфиги, лежащие по прежнему пути, продолжают работать."""
        from backend.config import legacy_config_path, load_project_config

        legacy = legacy_config_path(self.tasks)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"pipeline": ["backlog", "development", "done"]}),
                          encoding="utf-8")

        cfg = load_project_config(self.tasks)
        self.assertEqual(["backlog", "development", "done"], cfg["pipeline"])

    def test_new_location_wins_over_legacy(self) -> None:
        from backend.config import legacy_config_path, project_config_path, load_project_config

        legacy = legacy_config_path(self.tasks)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"pipeline": ["backlog", "done"]}), encoding="utf-8")
        project_config_path(self.tasks).write_text(
            json.dumps({"pipeline": ["backlog", "development", "completed"]}), encoding="utf-8")

        cfg = load_project_config(self.tasks)
        self.assertEqual(["backlog", "development", "completed"], cfg["pipeline"])

    def test_save_writes_into_tasks(self) -> None:
        from backend.config import project_config_path, save_project_config

        save_project_config(self.tasks, {"pipeline": ["backlog", "development", "done"]})

        self.assertTrue(project_config_path(self.tasks).is_file())
        self.assertFalse((self.tasks.parent / "taskboard").exists(),
                         "конфиг создал папку в корне проекта")


class RulesRenderTest(unittest.TestCase):
    """Правила для агентов описывают жизненный цикл конкретного проекта."""

    def test_rules_reflect_project_pipeline(self) -> None:
        from backend.scaffold import render_rules

        text = render_rules({
            "pipeline": ["backlog", "todo", "development", "local_testing",
                         "completed", "cancelled"],
            "actions": {"create": "backlog", "start": "development"},
        })
        self.assertIn("backlog → todo → development → local_testing → completed", text)
        self.assertIn("вне маршрута: cancelled", text)
        self.assertIn("## Живая очередь (To Do)", text)
        self.assertNotIn("{pipeline_line}", text, "плейсхолдер не подставлен")
        self.assertNotIn("review", text, "статус чужого пайплайна попал в правила")


class LegacyConfigTest(unittest.TestCase):
    """Конфиги, написанные до пайплайнов, должны продолжать работать."""

    def test_legacy_defaults(self) -> None:
        p = load_pipeline({})
        self.assertEqual(["backlog", "queued", "development", "review", "testing", "completed"],
                         p.keys())
        self.assertEqual("backlog", p.action("create"))
        self.assertEqual("development", p.action("start"))
        self.assertEqual("queued", p.action("pick"))

    def test_legacy_queue_renaming_is_honoured(self) -> None:
        """queue_section/queued_status переезжают в пайплайн как переопределения."""
        p = load_pipeline({"queue_section": "Живая очередь", "queued_status": "in_queue"})
        self.assertIn("in_queue", p.keys())
        self.assertNotIn("queued", p.keys())
        self.assertEqual("Живая очередь", p.section_of("in_queue"))
        self.assertEqual("in_queue", p.action("pick"))

    def test_legacy_statuses_list_defines_pipeline(self) -> None:
        """Старый ключ statuses был списком; новый — словарём оформления."""
        p = load_pipeline({"statuses": ["backlog", "development", "completed"]})
        self.assertEqual(["backlog", "development", "completed"], p.keys())


if __name__ == "__main__":
    unittest.main()
