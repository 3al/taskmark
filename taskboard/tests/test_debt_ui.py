"""Видимость долга: бейдж на карточке, долг в окне задачи, диалог переноса (TASK-110).

После TASK-108 долг существовал только как текст отказа в момент, когда агент
двигает задачу вперёд. Человек, работающий мышью, не видел его вовсе — и упирался
в отказ чужими руками, не понимая, откуда он взялся.

Тест-раннера фронтенда в проекте нет, поэтому разметка проверяется чтением
исходников: связка «поле API ↔ элемент интерфейса» рвётся молча.

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

from backend.requirements import (annotate_debt, is_terminal,  # noqa: E402
                                  move_debt, task_debt)
from backend.statuses import load_pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
COMPONENTS = SRC / "components"

PIPELINE = ["backlog", "todo", "development", "testing",
            "ready_for_release", "release_notes", "done", "cancelled"]

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-08-03 10:00
confirmed: {confirmed}
---

## Описание

Тестовая задача.

## Чеклист

- [x] Пункт

## Комментарии

## История коммитов

- `abc1234` коммит
"""


class DebtOnBoardTest(unittest.TestCase):
    """Карточка знает свой долг: иначе доска молчит о том, на чём встанет агент."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.cfg = {"pipeline": PIPELINE,
                    "actions": {"create": "backlog", "start": "development",
                                "release_draft": "release_notes"},
                    "requires": {"testing": [{"id": "verified", "check": "confirm",
                                              "ask": "проверку подтвердил человек"}]}}
        (self.tasks / ".taskboard.json").write_text(
            json.dumps(self.cfg, ensure_ascii=False), encoding="utf-8")

    def _task(self, task_id: str, status: str, confirmed: str = "~") -> None:
        (self.tasks / f"{task_id}-t.md").write_text(
            TASK_FILE.format(task_id=task_id, title="Задача", status=status,
                             confirmed=confirmed), encoding="utf-8")

    def _board(self, *tasks: tuple[str, str]) -> dict:
        board = {"columns": [{"title": "Доска", "groups": [{"tasks": [
            {"id": tid, "file": f"{tid}-t.md"} for tid, _st in tasks]}]}]}
        for tid, status in tasks:
            self._task(tid, status)
        annotate_debt(self.tasks, board, self.cfg, load_pipeline(self.cfg))
        return board

    def _cards(self, board: dict) -> dict:
        return {t["id"]: t for t in board["columns"][0]["groups"][0]["tasks"]}

    def test_debt_lands_on_card(self) -> None:
        cards = self._cards(self._board(("TASK-001", "ready_for_release")))

        self.assertIn("debt", cards["TASK-001"], "карточка не знает о долге")
        self.assertEqual([r["id"] for r in cards["TASK-001"]["debt"]], ["verified"])
        self.assertIn("проверку подтвердил человек", cards["TASK-001"]["debt"][0]["text"],
                      "бейджу нужен человеческий текст, а не только идентификатор")

    def test_no_debt_no_field(self) -> None:
        """Свободная карточка полей не получает — маркер нужен только должникам."""
        cards = self._cards(self._board(("TASK-001", "development")))

        self.assertNotIn("debt", cards["TASK-001"])

    def test_terminal_and_offramp_are_clean(self) -> None:
        cards = self._cards(self._board(("TASK-001", "done"), ("TASK-002", "cancelled")))

        self.assertNotIn("debt", cards["TASK-001"], "у закрытой задачи долга нет")
        self.assertNotIn("debt", cards["TASK-002"], "у снятой с маршрута долга нет")

    def test_confirmed_task_is_clean(self) -> None:
        self._task("TASK-001", "ready_for_release", confirmed="verified")
        board = {"columns": [{"title": "Доска", "groups": [{"tasks": [
            {"id": "TASK-001", "file": "TASK-001-t.md"}]}]}]}
        annotate_debt(self.tasks, board, self.cfg, load_pipeline(self.cfg))

        self.assertNotIn("debt", self._cards(board)["TASK-001"])

    def test_project_without_requirements_is_not_walked(self) -> None:
        """Проект ничего не объявил — доска не должна читать все файлы задач зря."""
        cfg = dict(self.cfg)
        cfg.pop("requires")
        self._task("TASK-001", "ready_for_release")
        board = {"columns": [{"title": "Доска", "groups": [{"tasks": [
            {"id": "TASK-001", "file": "TASK-001-t.md"}]}]}]}

        annotate_debt(self.tasks, board, cfg, load_pipeline(cfg))

        self.assertNotIn("debt", self._cards(board)["TASK-001"])

    def test_move_debt_answers_before_the_move(self) -> None:
        """Цену переноса человек видит заранее, а не узнаёт от агента через этап."""
        self._task("TASK-001", "testing")
        pipeline = load_pipeline(self.cfg)

        forward = move_debt(self.tasks, "TASK-001", self.cfg, "ready_for_release", pipeline)
        backward = move_debt(self.tasks, "TASK-001", self.cfg, "development", pipeline)
        offramp = move_debt(self.tasks, "TASK-001", self.cfg, "cancelled", pipeline)

        self.assertEqual([r["id"] for r in forward], ["verified"])
        self.assertEqual(backward, [], "назад долг не считается")
        self.assertEqual(offramp, [], "в съезде долга нет")

    def test_terminal_move_is_marked_terminal(self) -> None:
        """Перенос в конец маршрута надо отличать: долга после него не будет.

        Требования пройденных этапов там по-прежнему не выполнены — и это
        последний момент, когда их можно выполнить, — но «долгом» они не
        станут: в терминальном статусе он не считается (см. `crossed`).
        Обещать «агент закроет позже» здесь значит врать: закрывать некому.
        """
        self._task("TASK-001", "testing")
        pipeline = load_pipeline(self.cfg)

        self.assertTrue(is_terminal(pipeline, "done"))
        self.assertFalse(is_terminal(pipeline, "ready_for_release"))
        # Долг в терминальную цель считается как обычно: список непройденного
        # нужен окну, меняется только то, как оно его называет
        self.assertEqual(
            [r["id"] for r in move_debt(self.tasks, "TASK-001", self.cfg, "done", pipeline)],
            ["verified"])
        self.assertEqual(
            task_debt(self.tasks, "TASK-001", self.cfg, pipeline)["debt"], [],
            "в testing долга ещё нет — он считается по пройденным этапам")


class HumanConfirmTest(unittest.TestCase):
    """`confirm` означает «человек сказал» — и закрыть его может только человек.

    Пока подтверждение умел писать один агент, требование оказывалось
    адресовано человеку и невыполнимо им же: happy path (проверил → бросил
    карточку дальше) выглядел исключением с предупреждением.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        (self.tasks / "TASK-001-t.md").write_text(
            TASK_FILE.format(task_id="TASK-001", title="Задача",
                             status="testing", confirmed="~"), encoding="utf-8")
        self.path = self.tasks / "TASK-001-t.md"
        self.cfg = {"pipeline": PIPELINE,
                    "requires": {"testing": [{"id": "verified", "check": "confirm",
                                              "ask": "проверку подтвердил человек"}]}}

    def _meta(self) -> dict:
        from backend.task_parser import parse_frontmatter

        meta, _body = parse_frontmatter(self.path.read_text(encoding="utf-8-sig"))
        return meta

    def _notes(self) -> list[str]:
        return [ln for ln in self.path.read_text(encoding="utf-8").splitlines()
                if ln.startswith("- **")]

    def test_confirm_writes_field_and_note(self) -> None:
        from backend.requirements import confirm_requirements

        result = confirm_requirements(self.tasks, "TASK-001", ["verified"],
                                      "Ready for Release", self.cfg)

        self.assertEqual(result["confirmed"], ["verified"])
        self.assertEqual(self._meta().get("confirmed"), "verified")
        trace = [n for n in self._notes() if "проверку подтвердил человек" in n]
        self.assertTrue(trace, "подтверждение с доски не оставило следа")
        self.assertIn("Ready for Release", trace[0], "не сказано, откуда подтверждение")

    def test_note_names_the_requirement_not_its_id(self) -> None:
        """Заметку читает человек: служебное имя `verified` ему ничего не говорит."""
        from backend.requirements import confirm_requirements

        confirm_requirements(self.tasks, "TASK-001", ["verified"], "", self.cfg)

        trace = [n for n in self._notes() if "проверку подтвердил человек" in n][0]
        self.assertNotIn("verified", trace, "идентификатор просочился в текст заметки")
        self.assertNotIn("подтверждено:", trace,
                         "префикс тавтологичен: формулировка сама говорит о подтверждении")

    def test_undeclared_requirement_is_rejected(self) -> None:
        """Требования нет в маршруте — подтверждать нечего.

        Отметка фиксирует чужое слово, и строка о том, чего никто не говорил,
        обесценивает соседние: разобрать потом, какая настоящая, будет нечем.
        """
        from backend.requirements import confirm_requirements

        result = confirm_requirements(self.tasks, "TASK-001", ["выдумка"], "", self.cfg)

        self.assertEqual([], result["confirmed"])
        self.assertEqual(["выдумка"], result["rejected"],
                         "отвергнутое проглочено молча — клиент считает его принятым")
        self.assertEqual("~", self._meta().get("confirmed"),
                         "во frontmatter записан несуществующий идентификатор")
        self.assertFalse([n for n in self._notes() if "выдумка" in n],
                         "в хронологию легла строка о том, чего никто не подтверждал")

    def test_requirement_closed_by_work_is_rejected(self) -> None:
        """Чужой предикат закрывается работой, а не решением человека."""
        from backend.requirements import confirm_requirements

        cfg = {"pipeline": PIPELINE,
               "requires": {"testing": [
                   {"id": "verified", "check": "confirm",
                    "ask": "проверку подтвердил человек"},
                   {"id": "commits", "check": "section_filled",
                    "name": "История коммитов"}]}}

        result = confirm_requirements(self.tasks, "TASK-001",
                                      ["verified", "commits"], "", cfg)

        self.assertEqual(["verified"], result["confirmed"])
        self.assertEqual(["commits"], result["rejected"],
                         "требование, закрываемое работой, подтверждено решением")
        self.assertEqual("verified", self._meta().get("confirmed"))
        self.assertFalse([n for n in self._notes() if "История коммитов" in n])

    def test_rejected_field_is_always_there(self) -> None:
        """Контракт ответа один на все ветки: клиенту не приходится гадать."""
        from backend.requirements import confirm_requirements

        confirm_requirements(self.tasks, "TASK-001", ["verified"], "", self.cfg)
        repeat = confirm_requirements(self.tasks, "TASK-001", ["verified"], "", self.cfg)
        empty = confirm_requirements(self.tasks, "TASK-001", [], "", self.cfg)

        self.assertEqual([], repeat["rejected"], "повтор — не отказ: слово уже записано")
        self.assertEqual([], empty["rejected"])

    def test_repeat_is_silent(self) -> None:
        from backend.requirements import confirm_requirements

        confirm_requirements(self.tasks, "TASK-001", ["verified"], "", self.cfg)
        confirm_requirements(self.tasks, "TASK-001", ["verified"], "", self.cfg)

        self.assertEqual(
            len([n for n in self._notes() if "проверку подтвердил человек" in n]), 1)

    def test_debt_item_marks_who_can_close_it(self) -> None:
        """Интерфейсу нужно знать, где кнопка уместна, а где долг закрывает работа."""
        import backend.app as app_module

        confirmable = app_module._debt_item({"id": "verified", "check": "confirm"})
        by_work = app_module._debt_item({"id": "commits", "check": "section_filled",
                                         "name": "История коммитов"})

        self.assertTrue(confirmable["confirmable"])
        self.assertFalse(by_work["confirmable"])

    def test_ui_splits_debt_by_subject(self) -> None:
        src = (SRC / "App.jsx").read_text(encoding="utf-8")

        self.assertIn("confirmable", src, "диалог не отделяет подтверждаемое от долга")
        self.assertIn("confirmThenMove", src, "нет пути «подтвердить и перенести»")

    def test_api_client_can_confirm(self) -> None:
        src = (SRC / "api.js").read_text(encoding="utf-8")

        self.assertIn("confirmRequirements", src)


class BoardResetsConfirmationTest(unittest.TestCase):
    """Возврат мышью снимает подтверждение так же, как возврат скриптом.

    Рука не пишет решений — но убрать то, что перестало быть правдой, обязана:
    иначе устаревшее подтверждение переживает возврат молча, и агент не спросит
    заново. Основной путь возврата у человека — именно доска.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.cfg = {"pipeline": PIPELINE,
                    "actions": {"create": "backlog", "start": "development",
                                "release_draft": "release_notes"},
                    "requires": {"testing": [{"id": "verified", "check": "confirm",
                                              "ask": "проверку подтвердил человек"}]}}
        self.path = self.tasks / "TASK-001-t.md"
        self.path.write_text(
            TASK_FILE.format(task_id="TASK-001", title="Задача",
                             status="ready_for_release", confirmed="verified"),
            encoding="utf-8")
        sections = "\n\n".join(f"## {s}\n\n_(нет)_" for s in
                               ("Backlog", "To Do", "Development", "Testing",
                                "Ready for Release", "Release Notes", "Done", "Cancelled"))
        board = sections.replace("## Ready for Release\n\n_(нет)_",
                                 "## Ready for Release\n\n- TASK-001 · [Задача](TASK-001-t.md)")
        (self.tasks / "board.md").write_text(f"# Доска\n\n{board}\n", encoding="utf-8")

    def _move(self, to_section: str) -> dict:
        from backend.queue_ops import move_task

        return move_task(self.tasks, self.cfg, "TASK-001", to_section)

    def _meta(self) -> dict:
        from backend.task_parser import parse_frontmatter

        meta, _body = parse_frontmatter(self.path.read_text(encoding="utf-8-sig"))
        return meta

    def test_drag_back_drops_confirmation(self) -> None:
        result = self._move("Testing")

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(self._meta().get("confirmed"), "~")
        self.assertEqual(result.get("unconfirmed"), ["verified"])

    def test_drag_back_leaves_a_note(self) -> None:
        self._move("Development")

        trace = [ln for ln in self.path.read_text(encoding="utf-8").splitlines()
                 if "снято подтверждение" in ln]
        self.assertTrue(trace, "возврат мышью не оставил следа")
        self.assertIn("доска", trace[0], "не видно, что подтверждение снято с доски")

    def test_drag_forward_keeps_confirmation(self) -> None:
        self._move("Release Notes")

        self.assertEqual(self._meta().get("confirmed"), "verified")


class WaiverVisibilityTest(unittest.TestCase):
    """Списание — легальный обход, и он обязан быть громким."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)

    def _task(self, task_id: str, waived: str) -> None:
        (self.tasks / f"{task_id}-t.md").write_text(
            f"---\nid: {task_id}\ntitle: Задача\nstatus: done\nwaived: {waived}\n---\n",
            encoding="utf-8")

    def test_waivers_named_for_the_card(self) -> None:
        from backend.requirements import task_waivers

        self._task("TASK-001", "verified")
        cfg = {"pipeline": PIPELINE,
               "requires": {"testing": [{"id": "verified", "check": "confirm",
                                         "ask": "проверку подтвердил человек"}]}}

        found = task_waivers(self.tasks, "TASK-001", cfg)

        self.assertEqual([w["id"] for w in found], ["verified"])
        self.assertEqual(found[0]["text"], "проверку подтвердил человек",
                         "на карточке нужна формулировка, а не служебный id")

    def test_validator_is_silent_about_waivers(self) -> None:
        """Списание — принятое решение, а не расхождение данных.

        Строка в «Проблемах данных» была неустранимой: починить её нечем, а
        соседние строки, которые чинить как раз надо, она обесценивала.
        """
        validator = (ROOT / "backend" / "validator.py").read_text(encoding="utf-8")

        self.assertNotIn("Списаны требования", validator)

    def test_card_shows_waivers(self) -> None:
        src = (COMPONENTS / "TaskCard.jsx").read_text(encoding="utf-8")

        self.assertIn("task.waived", src, "на карточке не видно списанных требований")


class DebtApiTest(unittest.TestCase):
    """Эндпоинт вопроса о долге: спрашивает доска, ничего не записывая."""

    def test_endpoint_declared(self) -> None:
        app = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertIn("move-debt", app, "нет эндпоинта, у которого доска спросит долг")
        self.assertIn("annotate_debt", app, "долг не приезжает на карточки доски")

    def test_endpoint_tells_target_is_terminal(self) -> None:
        """Окно должно знать, конец ли это маршрута: после него долга не будет."""
        app = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertIn('"terminal"', app, "ответ move-debt не говорит о конце маршрута")

    def test_task_endpoint_carries_debt(self) -> None:
        app = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertIn('task["debt"]', app, "окно задачи не получает долг")


class DebtUiTest(unittest.TestCase):
    """Интерфейс показывает долг там, где человек принимает решение."""

    def test_card_shows_debt_badge(self) -> None:
        src = (COMPONENTS / "TaskCard.jsx").read_text(encoding="utf-8")

        self.assertIn("task.debt", src, "на карточке нет бейджа долга")

    def test_modal_shows_debt(self) -> None:
        src = (COMPONENTS / "TaskModal.jsx").read_text(encoding="utf-8")

        self.assertIn("debt", src, "в окне задачи долг не показан")

    def test_move_asks_before_dropping(self) -> None:
        src = (SRC / "App.jsx").read_text(encoding="utf-8")

        self.assertIn("moveDebt", src, "доска не спрашивает долг перед переносом")
        self.assertIn("pendingDebt", src, "нет диалога о переносе с долгом")

    def test_terminal_move_is_not_called_debt(self) -> None:
        """В конце маршрута окно не обещает ни долга, ни того, что агент его закроет.

        Долг там не считается вовсе, а «позже» не существует: задача закрыта.
        Требования при этом остаются невыполненными — про это сказать надо,
        но своими словами.
        """
        src = (SRC / "App.jsx").read_text(encoding="utf-8")

        self.assertIn("terminal", src, "окно не различает конец маршрута")
        self.assertIn("Останутся невыполненными", src,
                      "нет честной формулировки для терминального переноса")
        self.assertIn("агент закроет позже", src,
                      "формулировка для обычного переноса пропала")

    def test_api_client_has_move_debt(self) -> None:
        src = (SRC / "api.js").read_text(encoding="utf-8")

        self.assertIn("move-debt", src, "в клиенте API нет запроса долга")


class BoardReadBudgetTest(unittest.TestCase):
    """Отрисовка доски читает файл задачи один раз.

    Долг считается на каждую отрисовку, а отрисовку дёргает SSE при любой правке
    в `tasks/`: правка одной задачи агентом перечитывает всю доску. При сотне
    задач лишнее чтение внутри прохода — это лишняя сотня чтений и столько же
    `glob` на каждое нажатие.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.cfg = {
            "pipeline": PIPELINE,
            "actions": {"create": "backlog", "start": "development",
                        "release_draft": "release_notes"},
            # Несколько требований на этапе: предикат, читающий файл сам, множит
            # чтения не на задачу, а на задачу × требование
            "requires": {"testing": [
                {"id": "verified", "check": "confirm",
                 "ask": "проверку подтвердил человек"},
                {"id": "commits", "check": "section_filled",
                 "name": "История коммитов"},
            ]},
        }

    def _board(self, count: int) -> dict:
        tasks = []
        for i in range(1, count + 1):
            task_id = f"TASK-{i:03d}"
            (self.tasks / f"{task_id}-t.md").write_text(
                TASK_FILE.format(task_id=task_id, title="Задача",
                                 status="ready_for_release", confirmed="~"),
                encoding="utf-8")
            tasks.append({"id": task_id, "file": f"{task_id}-t.md"})
        return {"columns": [{"title": "Доска", "groups": [{"tasks": tasks}]}]}

    def test_task_file_is_read_once_per_pass(self) -> None:
        board = self._board(3)
        reads: dict[str, int] = {}
        original = Path.read_text

        def counting(self, *args, **kwargs):  # noqa: ANN001 — подмена метода
            if self.suffix == ".md":
                reads[self.name] = reads.get(self.name, 0) + 1
            return original(self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", counting):
            annotate_debt(self.tasks, board, self.cfg, load_pipeline(self.cfg))

        # Долг посчитан — иначе тест мерил бы бюджет пустого прохода
        cards = board["columns"][0]["groups"][0]["tasks"]
        self.assertTrue(all("debt" in t for t in cards), "долг не посчитан")
        self.assertEqual({name: 1 for name in reads}, reads,
                         f"файл задачи читается больше одного раза: {reads}")

    def test_known_file_is_not_searched_again(self) -> None:
        """Путь задачи известен из строки доски — искать его `glob` незачем."""
        board = self._board(3)
        with mock.patch("backend.requirements.find_task_file") as search:
            annotate_debt(self.tasks, board, self.cfg, load_pipeline(self.cfg))

        self.assertEqual(0, search.call_count,
                         "файл задачи ищется заново, хотя путь уже известен")


if __name__ == "__main__":
    unittest.main()
