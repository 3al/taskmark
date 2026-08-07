"""Поставка требований этапа: конфиг, источники пайплайна, валидатор (TASK-109).

Механизм требований (TASK-108) работает из руками написанного
`tasks/.taskboard.json`. Эти тесты держат его **доступность**: требования должны
переживать сохранение настроек, ехать вместе с копируемым жизненным циклом и
не молчать, когда декларация битая.

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

from backend import registry  # noqa: E402
from backend.config import (DEFAULTS, PROJECT_KEYS, load_project_config,  # noqa: E402
                            project_config_path, save_project_config)
from backend.validator import validate_project  # noqa: E402

REQUIRES = {"testing": [{"id": "verified", "check": "confirm",
                         "ask": "проверку подтвердил человек"}]}

PIPELINE = ["backlog", "development", "testing", "done", "cancelled"]


class RequiresProject(unittest.TestCase):
    """Проект с доской и настройками — как у пользователя."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "board.md").write_text(
            "# Tasks Board\n\n## Backlog\n\n_(нет)_\n\n## Development\n\n_(нет)_\n"
            "\n## Testing\n\n_(нет)_\n\n## Done\n\n_(нет)_\n\n## Cancelled\n\n_(нет)_\n",
            encoding="utf-8")

    def config(self, **extra) -> dict:
        cfg = {**DEFAULTS, "pipeline": PIPELINE,
               "actions": {"create": "backlog", "start": "development"}, **extra}
        save_project_config(self.tasks, {k: v for k, v in cfg.items()
                                         if k in PROJECT_KEYS})
        return cfg

    def warnings(self, **extra) -> list[str]:
        cfg = self.config(**extra)
        return validate_project(self.tasks, cfg)["warnings"]


class ConfigKeyTest(RequiresProject):
    """Требования — настройка проекта, а не инструмента."""

    def test_requires_is_a_project_key(self) -> None:
        """Иначе они уедут в общий конфиг и достанутся всем проектам сразу."""
        self.assertIn("requires", PROJECT_KEYS)

    def test_requires_survives_saving_settings(self) -> None:
        """Сохранение настроек не должно терять объявленные требования.

        `save_project_config` обновляет ключи поверх сохранённых, и требования
        обязаны лежать ключом верхнего уровня: вложенное в `statuses` затирается
        применением источника пайплайна.
        """
        save_project_config(self.tasks, {"requires": REQUIRES})
        save_project_config(self.tasks, {"pipeline": PIPELINE})

        stored = json.loads(project_config_path(self.tasks).read_text(encoding="utf-8"))

        self.assertEqual(REQUIRES, stored.get("requires"))
        self.assertEqual(REQUIRES, load_project_config(self.tasks).get("requires"))


class DeclarationWarningTest(RequiresProject):
    """Битую декларацию видно на доске, а не в момент отказа у агента."""

    def test_unknown_predicate_reported(self) -> None:
        text = "\n".join(self.warnings(requires={
            "testing": [{"id": "verified", "check": "нет-такого"}]}))

        self.assertIn("нет-такого", text)

    def test_status_outside_pipeline_reported(self) -> None:
        """Требование этапа, которого нет в маршруте, не сработает никогда."""
        text = "\n".join(self.warnings(requires={
            "нет-этапа": [{"id": "verified", "check": "confirm"}]}))

        self.assertIn("нет-этапа", text)

    def test_duplicate_id_reported(self) -> None:
        """Один id на этапе — одно требование: подтверждение гасит их вместе."""
        text = "\n".join(self.warnings(requires={"testing": [
            {"id": "verified", "check": "confirm"},
            {"id": "verified", "check": "section_filled", "name": "Чеклист"}]}))

        self.assertIn("verified", text)

    def test_predicate_without_its_parameter_reported(self) -> None:
        """`section_filled` без имени секции проверяет несуществующее."""
        text = "\n".join(self.warnings(requires={
            "testing": [{"id": "commits", "check": "section_filled"}]}))

        self.assertIn("commits", text)

    def test_requirement_without_id_reported(self) -> None:
        """Без идентификатора требование нечем погасить."""
        text = "\n".join(self.warnings(requires={
            "testing": [{"check": "confirm", "ask": "проверено"}]}))

        self.assertTrue(text, "требование без id должно быть замечено")

    def test_unknown_except_type_reported(self) -> None:
        """Исключение по несуществующему типу не сработает ни на одной задаче.

        Промах здесь бесшумный вдвойне: движок исключение читает, ни на что не
        находит и требование применяет ко всем — то есть человек видит в списке
        «кроме: …» и уверен, что настроил, а гейт стоит там, где не должен.
        """
        text = "\n".join(self.warnings(requires={"testing": [
            {"id": "verified", "check": "confirm", "except_types": ["обсуждение"]}]}))

        self.assertIn("обсуждение", text)

    def test_excluding_every_type_reported(self) -> None:
        """Требование, исключённое для всех типов, не сработает никогда."""
        from backend.config import TASK_TYPES

        text = "\n".join(self.warnings(requires={"testing": [
            {"id": "verified", "check": "confirm",
             "except_types": list(TASK_TYPES)}]}))

        self.assertIn("verified", text)

    def test_known_except_type_is_silent(self) -> None:
        text = "\n".join(self.warnings(requires={"testing": [
            {"id": "commits", "check": "section_filled", "name": "История коммитов",
             "except_types": ["discussion"]}]}))

        self.assertEqual("", text)

    def test_correct_declaration_is_silent(self) -> None:
        self.assertEqual([], [w for w in self.warnings(requires=REQUIRES)
                              if "требован" in w.lower()])

    def test_project_without_requires_is_silent(self) -> None:
        self.assertEqual([], [w for w in self.warnings() if "требован" in w.lower()])


class PredicateVocabularyTest(unittest.TestCase):
    """Словарь предикатов — то, что редактор предлагает человеку.

    Разойдись он со движком, редактор предложит проверку, которой тот не умеет:
    декларация запишется, а этап будет пропускаться молча (fail-open).
    """

    def test_vocabulary_matches_the_engine(self) -> None:
        from tests.test_set_status_script import load_script

        from backend.requirements import PREDICATES

        script = load_script()
        # Предикаты движка перечислены в `requirement_met`: других он не знает
        source = Path(script.__file__).read_text(encoding="utf-8")
        engine = {m for m in ("checklist_done", "section_present", "section_filled",
                              "field", "confirm")
                  if f'check == "{m}"' in source}

        self.assertEqual(engine, set(PREDICATES),
                         "словарь предикатов разошёлся с движком")

    def test_every_predicate_says_what_it_asks(self) -> None:
        from backend.requirements import PREDICATES

        for name, spec in PREDICATES.items():
            with self.subTest(predicate=name):
                self.assertTrue(spec.get("label"), "предикат без человеческого имени")
                if spec.get("param"):
                    self.assertTrue(spec.get("param_label"),
                                    "параметр без подписи: человек не поймёт, что вводить")
                    self.assertIn("{}", spec.get("phrase", ""),
                                  "нет фразы с параметром — UI склеит её сам и "
                                  "получится «проверка: «значение»»")

    def test_confirmation_asks_for_its_wording(self) -> None:
        """У подтверждения формулировка обязательна: без неё непонятно, что
        именно человек подтверждает, — сам предикат об этом не говорит."""
        from backend.requirements import PREDICATES

        self.assertTrue(PREDICATES["confirm"].get("ask_label"))

    def test_section_predicates_need_no_wording(self) -> None:
        """У секции заголовок и есть человеческий текст: отдельная формулировка
        к нему — то же самое, написанное дважды."""
        from backend.requirements import PREDICATES

        for name in ("section_present", "section_filled"):
            with self.subTest(predicate=name):
                self.assertIsNone(PREDICATES[name].get("ask_label"))


class GateImpactTest(RequiresProject):
    """Цена включения требования видна до сохранения, а не после.

    Требование действует задним числом: живые задачи, прошедшие этап раньше,
    упрутся на следующем движении вперёд. Это то, ради чего механизм заводился,
    но человек должен видеть цену **до** нажатия.
    """

    def _task(self, task_id: str, status: str, commits: str = "") -> Path:
        path = self.tasks / f"{task_id}-t.md"
        path.write_text(f"""---
id: {task_id}
title: Задача
epic: ~
type: feature
status: {status}
created: 2026-08-01 10:00
---

## Описание

Текст.

## Чеклист

- [x] Сделано

## Заметки агента

## История коммитов
{commits}
""", encoding="utf-8")
        return path

    def impact(self, new_requires: dict) -> list[dict]:
        from backend.requirements import gate_impact

        old = self.config()
        return gate_impact(self.tasks, old, {**old, "requires": new_requires})

    COMMITS = {"id": "commits", "check": "section_filled",
               "name": "История коммитов"}

    def test_tasks_past_the_stage_are_counted(self) -> None:
        self._task("TASK-001", "testing")   # development пройден, коммитов нет
        self._task("TASK-002", "backlog")   # до этапа не дошла — шаг вперёд её не спросит

        hit = self.impact({"development": [dict(self.COMMITS)]})

        self.assertEqual(["TASK-001"], [t["id"] for t in hit])

    def test_requirement_already_met_is_not_counted(self) -> None:
        self._task("TASK-001", "testing", commits="\n- `abc1234` правка")

        self.assertEqual([], self.impact({"development": [dict(self.COMMITS)]}))

    def test_task_standing_on_the_stage_is_counted(self) -> None:
        """Задача стоит на этапе, которому объявили требование.

        Долга у неё сейчас нет — этап не пройден, — но упрётся она в него на
        первом же шаге вперёд. Не показать её значит недооценить цену: человек
        включает требование, видит «никого не задело» и узнаёт правду от агента.
        """
        self._task("TASK-001", "development")

        hit = self.impact({"development": [dict(self.COMMITS)]})

        self.assertEqual(["TASK-001"], [t["id"] for t in hit])

    def test_debt_now_and_on_exit_are_told_apart(self) -> None:
        """Значок долга появится не у всех сразу — и это должно быть видно.

        Задача, прошедшая этап, получает долг немедленно; та, что стоит на этапе,
        — только когда уйдёт. Одно число в предупреждении и другое на доске
        выглядят как ошибка, хотя оба верны.
        """
        self._task("TASK-001", "testing")      # development пройден → долг сразу
        self._task("TASK-002", "development")  # стоит на этапе → при выходе

        hit = self.impact({"development": [dict(self.COMMITS)]})

        self.assertEqual({"TASK-001": "now", "TASK-002": "exit"},
                         {t["id"]: t["when"] for t in hit})
        self.assertEqual(["now", "exit"], [t["when"] for t in hit],
                         "ближайшее последствие решения читают первым")

    def test_closed_task_is_not_counted(self) -> None:
        """У задачи в конце маршрута долга нет: её больше никуда не двигают,
        и пугать человеком числом закрытых задач незачем."""
        self._task("TASK-001", "done")

        self.assertEqual([], self.impact({"development": [dict(self.COMMITS)]}))

    def test_removing_a_requirement_hits_nobody(self) -> None:
        """Снятие требования долгов не создаёт — предупреждать не о чем."""
        self._task("TASK-001", "done")

        self.assertEqual([], self.impact({}))


class OrphanRequirementTest(RequiresProject):
    """Статус выключили — его требования уходят вместе с ним.

    Иначе в конфиге остаётся блок, который не сработает никогда, а валидатор
    честно ругается на «статус вне маршрута» — то есть человек получает
    предупреждение за то, чего не делал.
    """

    def test_disabled_status_loses_its_requirements(self) -> None:
        from backend.migrations import apply_config_migrations

        old = self.config(requires={"testing": [{"id": "verified", "check": "confirm"}],
                                    "development": [{"id": "x", "check": "checklist_done"}]})
        new = {**old, "pipeline": ["backlog", "development", "done", "cancelled"]}

        apply_config_migrations(self.tasks, old, new, {"testing": "development"})

        left = load_project_config(self.tasks).get("requires") or {}
        self.assertNotIn("testing", left, "требования выключенного статуса остались")
        self.assertIn("development", left, "требования живого статуса не должны исчезать")

    def test_pipeline_untouched_keeps_requirements(self) -> None:
        from backend.migrations import apply_config_migrations

        old = self.config(requires=REQUIRES)

        apply_config_migrations(self.tasks, old, old, {})

        self.assertEqual(REQUIRES, load_project_config(self.tasks).get("requires"))


class UnknownIdOnBoardTest(RequiresProject):
    """Неопознанная запись видна на доске, а не только в консоли (TASK-133).

    Задачу двигают и мышью, и тогда скрипт не зовут вовсе: единственный, кто
    расскажет о записи, которую механизм не понимает, — валидатор.
    """

    def _task(self, task_id: str, confirmed: str = "~", waived: str = "~") -> Path:
        path = self.tasks / f"{task_id}-t.md"
        path.write_text(f"""---
id: {task_id}
title: Задача
epic: ~
type: feature
status: testing
created: 2026-08-01 10:00
confirmed: {confirmed}
waived: {waived}
---

## Описание

Текст.

## Чеклист

- [x] Сделано

## Заметки агента

## История коммитов
""", encoding="utf-8")
        return path

    def test_unknown_id_is_a_data_problem(self) -> None:
        self._task("TASK-001", confirmed="testing/verified")

        text = "\n".join(self.warnings(requires=REQUIRES))

        self.assertIn("TASK-001", text)
        self.assertIn("testing/verified", text)

    def test_known_id_is_silent(self) -> None:
        self._task("TASK-001", confirmed="verified")

        text = "\n".join(self.warnings(requires=REQUIRES))

        self.assertNotIn("не опознано", text)

    def test_waiver_itself_is_not_reported(self) -> None:
        """Списание в «Проблемах данных» не показывается — принятое решение:
        неустранимая строка обесценивала бы соседние. Речь только о неопознанном."""
        self._task("TASK-001", waived="verified")

        text = "\n".join(self.warnings(requires=REQUIRES))

        self.assertNotIn("не опознано", text)


class SourceTest(unittest.TestCase):
    """Копирование жизненного цикла соседнего проекта берёт и требования."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.donor = root / "donor" / "tasks"
        self.donor.mkdir(parents=True)
        save_project_config(self.donor, {"pipeline": PIPELINE, "requires": REQUIRES})
        self._patch_registry(root)

    def _patch_registry(self, root: Path) -> None:
        projects = {"projects": [{"name": "Донор", "tasks_dir": str(self.donor)}],
                    "active": str(self.donor)}
        path = root / "projects.json"
        path.write_text(json.dumps(projects, ensure_ascii=False), encoding="utf-8")
        original = registry.PROJECTS_FILE
        registry.PROJECTS_FILE = path
        self.addCleanup(lambda: setattr(registry, "PROJECTS_FILE", original))

    def test_source_carries_requires(self) -> None:
        from backend.pipeline_sources import list_sources

        sources = [s for s in list_sources() if s["kind"] == "project"]

        self.assertTrue(sources, "проект-донор не предложен источником")
        self.assertEqual(REQUIRES, sources[0].get("requires"))

    def test_project_with_only_requires_is_offered(self) -> None:
        """Требования — часть жизненного цикла: проект, настроивший лишь их,
        предлагать как источник тоже стоит."""
        from backend.pipeline_sources import LIFECYCLE_KEYS

        self.assertIn("requires", LIFECYCLE_KEYS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
