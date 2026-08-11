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

    def test_label_repeats_the_board_section(self) -> None:
        """Подпись статуса — то же имя, что у его раздела на доске.

        Правило уже действует для статуса, которого нет в каталоге: там и
        подпись, и раздел собираются из ключа (`_titleize`). Каталог обязан
        подчиняться ему же — иначе в настройках половина статусов подписана
        не тем, что человек читает в заголовке колонки.
        """
        for key, meta in CATALOG.items():
            with self.subTest(status=key):
                self.assertEqual(meta["section"], meta["label"],
                                 f"{key}: подпись разошлась с разделом доски")

    def test_catalog_covers_agreed_bricks(self) -> None:
        """Состав каталога согласован с пользователем — сузиться он не должен."""
        expected = {"backlog", "todo", "queued", "to_fix", "development", "local_testing",
                    "review", "to_testing", "testing", "ready_for_release",
                    "release_notes", "to_release", "ready_to_deploy",
                    "completed", "done", "cancelled"}
        self.assertEqual(expected, set(CATALOG))

    def test_only_cancelled_is_offramp(self) -> None:
        offramps = {k for k, m in CATALOG.items() if m.get("offramp")}
        self.assertEqual({"cancelled"}, offramps)


class PipelineOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = load_pipeline({"pipeline": [
            "backlog", "todo", "to_fix", "development", "local_testing",
            "review", "to_testing", "testing", "ready_to_deploy", "done", "cancelled"]})

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
        self.assertNotIn("ready_to_deploy", backward)

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


class CatalogColorsTest(unittest.TestCase):
    """Цвет статуса — способ узнать колонку не читая её заголовок.

    Соседи по палитре на это не годятся: две колонки рядом сливаются в одно
    пятно, а зелёный вдобавок читается как «уже готово» — на предрелизном шаге
    это прямая ложь о состоянии задачи.
    """

    # Пары, различимые только рядом друг с другом (тот же список, что у меток
    # типов задач в test_task_type.py)
    NEAR = (("sky", "cyan"), ("cyan", "teal"), ("emerald", "teal"),
            ("amber", "yellow"), ("violet", "purple"), ("rose", "pink"),
            ("emerald", "green"), ("green", "lime"))

    def test_last_step_before_the_end_is_not_a_shade_of_done(self) -> None:
        """Последний шаг перед терминальным статусом стоит с ним вплотную.

        Таких шагов два — «В ближайший релиз» (выпуск версии) и «К деплою»
        (выкатка); в одном маршруте они не встречаются, поэтому цвет у них
        общий, а вот с терминальным совпадать не должен ни у одного.
        """
        near = {tuple(sorted(pair)) for pair in self.NEAR}
        for last in ("to_release", "ready_to_deploy"):
            color = CATALOG[last]["color"]
            for terminal in ("done", "completed"):
                end = CATALOG[terminal]["color"]
                self.assertNotEqual(color, end, f"{last} и {terminal} одного цвета")
                self.assertNotIn(tuple(sorted((color, end))), near,
                                 f"{last} и {terminal} — соседи по палитре")

    def test_every_catalog_color_is_described_on_the_front(self) -> None:
        """Цвет, которого нет в реестре фронта, молча превращается в серый."""
        src = (Path(__file__).resolve().parent.parent
               / "frontend" / "src" / "statuses.js").read_text(encoding="utf-8")
        css = (Path(__file__).resolve().parent.parent
               / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
        for key, meta in CATALOG.items():
            color = meta["color"]
            self.assertIn(f"  {color}: {{", src,
                          f"цвет {color} (статус {key}) не описан в COLOR_STYLE")
            self.assertIn(f".md-tint-{color} ", css,
                          f"для цвета {color} нет оттенка заголовков md-tint")


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
        # Ищем статус, а не слово: скилл `review-task` называется в правилах
        # законно и к пайплайну отношения не имеет
        self.assertNotIn("review", text.replace("review-task", ""),
                         "статус чужого пайплайна попал в правила")


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
