"""Правила простоя: где его можно ставить и куда двигать стоящую задачу (TASK-079).

Решение принимает бэкенд — одной функцией на пайплайне, без имён статусов:
пайплайн настраивается, и в чужом проекте терминал может называться как угодно.
UI, API и автономный `tasks/set_status.py` спрашивают одно и то же правило,
иначе разъедутся в трактовках.

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

from backend.config import DEFAULTS  # noqa: E402
from backend.queue_ops import move_task  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.stall import (  # noqa: E402
    blocker_candidates,
    can_stall,
    clear_stall,
    move_confirmation,
    set_blocked_by,
    set_paused,
    stall_details,
    task_stall,
)
from backend.statuses import load_pipeline  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "templates" / "tasks" / "set_status.py"

# Маршрут со съездом: терминал — done, cancelled — съезд
CFG = {**DEFAULTS,
       "pipeline": ["backlog", "todo", "development", "testing", "done", "cancelled"],
       "actions": {"create": "backlog", "pick": "todo",
                   "start": "development", "return": "development"},
       "harnesses": {"claude": True, "opencode": False}}

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-07-31 10:00
blocked_by: ~
---

## Описание

Тестовая задача.
"""


class Project(unittest.TestCase):
    """Песочница с развёрнутой структурой tasks/ и доской."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks = self.root / "tasks"
        scaffold_project(self.tasks, CFG, {"harnesses": CFG["harnesses"]})
        # Автономный скрипт читает конфиг рядом с собой: без него он возьмёт
        # дефолтный пайплайн, где нет ни todo, ни done
        (self.tasks / ".taskboard.json").write_text(
            json.dumps({"pipeline": CFG["pipeline"], "actions": CFG["actions"]},
                       ensure_ascii=False), encoding="utf-8")
        self.pipeline = load_pipeline(CFG)

    def make(self, task_id: str, title: str, status: str = "todo") -> None:
        """Задача в файле и строкой на доске — в разделе своего статуса."""
        name = f"{task_id}-{title.lower().replace(' ', '-')}.md"
        (self.tasks / name).write_text(
            TASK_FILE.format(task_id=task_id, title=title, status=status),
            encoding="utf-8")
        section = self.pipeline.section_of(status)
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower() == f"## {section}".lower():
                lines.insert(i + 1, f"\n- {task_id} · [{title}]({name})")
                break
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def meta(self, task_id: str) -> dict:
        from backend.task_parser import parse_task
        return parse_task(self.tasks, task_id)["meta"]


class CanStallTest(unittest.TestCase):
    """Стоять может задача, у которой есть следующий шаг маршрута."""

    def setUp(self) -> None:
        self.pipeline = load_pipeline(CFG)

    def test_working_statuses_allow_stall(self) -> None:
        for status in ("backlog", "todo", "development", "testing"):
            self.assertTrue(can_stall(self.pipeline, status)["ok"], status)

    def test_terminal_status_refuses(self) -> None:
        verdict = can_stall(self.pipeline, "done")
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["reason"], "отказ без причины непонятен")

    def test_offramp_refuses(self) -> None:
        self.assertFalse(can_stall(self.pipeline, "cancelled")["ok"])

    def test_unknown_status_does_not_block(self) -> None:
        """Пайплайн могли поменять — не мешаем работать с чужим статусом."""
        self.assertTrue(can_stall(self.pipeline, "hotfix_wait")["ok"])
        self.assertTrue(can_stall(self.pipeline, "")["ok"])

    def test_rule_has_no_hardcoded_names(self) -> None:
        """Проект с другими именами статусов работает по тому же правилу."""
        other = load_pipeline({"pipeline": ["idea", "doing", "released"],
                               "actions": {"create": "idea", "start": "doing"}})
        self.assertTrue(can_stall(other, "doing")["ok"])
        self.assertFalse(can_stall(other, "released")["ok"])


class MoveConfirmationTest(Project):
    """Аномален один переход — «взять в работу»."""

    def setUp(self) -> None:
        super().setUp()
        self.make("TASK-013", "Первая")
        self.make("TASK-014", "Вторая")
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])

    def confirm(self, task_id: str, target: str) -> dict:
        return move_confirmation(self.tasks, self.pipeline, task_id, target)

    def test_into_work_needs_confirmation(self) -> None:
        verdict = self.confirm("TASK-014", "development")
        self.assertTrue(verdict["confirm"])
        self.assertIn("TASK-013", verdict["reason"], "вопрос не называет блокер")

    def test_review_and_testing_are_silent(self) -> None:
        """Две задачи могут проверяться только вместе — ожидание там законно."""
        self.assertFalse(self.confirm("TASK-014", "testing")["confirm"])

    def test_backwards_is_silent(self) -> None:
        self.assertFalse(self.confirm("TASK-014", "backlog")["confirm"])

    def test_terminal_is_silent(self) -> None:
        """В терминальный статус переносить можно — простой снимется сам."""
        self.assertFalse(self.confirm("TASK-014", "done")["confirm"])

    def test_free_task_is_silent(self) -> None:
        self.assertFalse(self.confirm("TASK-013", "development")["confirm"])

    def test_pause_asks_too(self) -> None:
        set_paused(self.tasks, "TASK-013", "ждём стенд")
        verdict = self.confirm("TASK-013", "development")
        self.assertTrue(verdict["confirm"])
        self.assertIn("ждём стенд", verdict["reason"])


class ClearOnTerminalTest(Project):
    """Задача закрыта — «ждёт» про неё больше не правда."""

    def setUp(self) -> None:
        super().setUp()
        self.make("TASK-013", "Первая")
        self.make("TASK-014", "Вторая", status="testing")
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        set_paused(self.tasks, "TASK-014", "ждём стенд")

    def test_move_to_terminal_clears_stall(self) -> None:
        result = move_task(self.tasks, CFG, "TASK-014", "Done")

        self.assertTrue(result["ok"], result)
        self.assertEqual("~", self.meta("TASK-014")["blocked_by"])
        self.assertEqual("~", self.meta("TASK-014")["paused"])
        self.assertEqual("~", self.meta("TASK-013")["blocks"],
                         "обратная ссылка осталась у блокера")
        self.assertTrue(result.get("stall_cleared"), "о снятии простоя не сообщается")

    def test_move_to_working_status_keeps_stall(self) -> None:
        move_task(self.tasks, CFG, "TASK-014", "Development")

        self.assertEqual("TASK-013", self.meta("TASK-014")["blocked_by"])

    def test_clear_is_idempotent(self) -> None:
        self.assertFalse(clear_stall(self.tasks, "TASK-013")["cleared"],
                         "снятие простоя у свободной задачи что-то поменяло")


class BlockerCandidatesTest(Project):
    """Кем можно заблокировать: список считает бэкенд, фронт графа не знает."""

    def setUp(self) -> None:
        super().setUp()
        self.make("TASK-010", "Кандидат")
        self.make("TASK-011", "Завершённая", status="done")
        self.make("TASK-012", "Отменённая", status="cancelled")
        self.make("TASK-013", "Блокер")
        self.make("TASK-014", "Текущая")
        self.make("TASK-015", "Зависимая")
        self.make("TASK-016", "Дальняя зависимая")
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])
        set_blocked_by(self.tasks, "TASK-015", ["TASK-014"])
        set_blocked_by(self.tasks, "TASK-016", ["TASK-015"])

    def candidates(self) -> list[str]:
        return [t["id"] for t in blocker_candidates(self.tasks, CFG, "TASK-014")]

    def test_free_task_is_offered(self) -> None:
        self.assertIn("TASK-010", self.candidates())

    def test_finished_and_cancelled_are_hidden(self) -> None:
        """Блокировка мертва в момент создания, ждать нечего."""
        self.assertNotIn("TASK-011", self.candidates())
        self.assertNotIn("TASK-012", self.candidates())

    def test_self_and_existing_blockers_are_hidden(self) -> None:
        self.assertNotIn("TASK-014", self.candidates())
        self.assertNotIn("TASK-013", self.candidates())

    def test_dependents_are_hidden(self) -> None:
        """Цикл: обе задачи ждут друг друга и не двинется никто."""
        self.assertNotIn("TASK-015", self.candidates(), "прямой цикл разрешён")
        self.assertNotIn("TASK-016", self.candidates(), "цикл через цепочку разрешён")

    def test_dependents_found_without_back_links(self) -> None:
        """Граф считается по `blocked_by`: обратных ссылок может не быть.

        В проекте, где блокировки проставляли руками, `blocks` пустой — обход
        по нему пропускал цикл и предлагал замкнуть его первым же кандидатом.
        """
        path = next(self.tasks.glob("TASK-010*.md"))
        path.write_text(path.read_text(encoding="utf-8").replace(
            "blocked_by: ~", "blocked_by: TASK-014"), encoding="utf-8")

        self.assertNotIn("TASK-010", self.candidates())

    def test_candidate_carries_status(self) -> None:
        """По статусу видно, стоит ли вообще ждать эту задачу."""
        item = next(t for t in blocker_candidates(self.tasks, CFG, "TASK-014")
                    if t["id"] == "TASK-010")
        self.assertEqual("todo", item["status"])
        self.assertTrue(item["label"], "подсказке нужна подпись статуса")


class StaleBlockerTest(Project):
    """Блокер дошёл до терминального статуса — больше не держит."""

    def setUp(self) -> None:
        super().setUp()
        self.make("TASK-013", "Блокер")
        self.make("TASK-014", "Ждущая")
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])

    def details(self) -> dict:
        return stall_details(self.tasks, self.meta("TASK-014"), self.pipeline)

    def test_active_blocker_is_not_resolved(self) -> None:
        self.assertFalse(self.details()["blocked_by_tasks"][0]["resolved"])
        self.assertFalse(self.details()["stale"])

    def test_finished_blocker_is_resolved(self) -> None:
        move_task(self.tasks, CFG, "TASK-013", "Done")

        details = self.details()
        self.assertTrue(details["blocked_by_tasks"][0]["resolved"],
                        "завершённый блокер выдаётся за действующий")
        self.assertTrue(details["stale"], "простой не помечен как неактуальный")
        self.assertTrue(details["stalled"],
                        "пометка снимается сама — а решать должен человек")


class StaleInTerminalTest(Project):
    """У закрытой задачи «ждёт» смысла не имеет — даже если пометка осталась."""

    def setUp(self) -> None:
        super().setUp()
        self.make("TASK-013", "Блокер")
        self.make("TASK-014", "Закрытая", status="done")
        # Пометка из прошлого: поставлена до того, как задачу закрыли
        path = next(self.tasks.glob("TASK-014*.md"))
        path.write_text(path.read_text(encoding="utf-8").replace(
            "blocked_by: ~", "blocked_by: TASK-013"), encoding="utf-8")

    def test_details_mark_it_stale(self) -> None:
        details = stall_details(self.tasks, self.meta("TASK-014"), self.pipeline)

        self.assertTrue(details["stale"],
                        "пометка на завершённой задаче выдаётся за действующую")

    def test_board_card_is_muted(self) -> None:
        from backend.stall import annotate_stall
        file = next(self.tasks.glob("TASK-014*.md")).name
        board = {"columns": [{"title": "Done", "groups": [{"tasks": [
            {"id": "TASK-014", "file": file}]}]}]}

        annotate_stall(self.tasks, board, self.pipeline)

        self.assertTrue(board["columns"][0]["groups"][0]["tasks"][0]["stall_stale"])


class ScriptRulesTest(Project):
    """Те же правила в автономном tasks/set_status.py."""

    def setUp(self) -> None:
        super().setUp()
        self.make("TASK-013", "Первая")
        self.make("TASK-014", "Вторая")
        self.make("TASK-020", "Готовая", status="done")

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks / "set_status.py"),
             "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_block_refused_in_terminal_status(self) -> None:
        done = self.run_script("TASK-020", "--block", "TASK-013")

        self.assertNotEqual(0, done.returncode, "простой поставлен на завершённую задачу")
        self.assertEqual("~", self.meta("TASK-020")["blocked_by"])

    def test_pause_refused_in_terminal_status(self) -> None:
        self.assertNotEqual(0, self.run_script("TASK-020", "--pause", "ждём").returncode)

    def test_status_change_to_terminal_clears_stall(self) -> None:
        self.run_script("TASK-014", "--block", "TASK-013")
        done = self.run_script("TASK-014", "done")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("~", self.meta("TASK-014")["blocked_by"])
        self.assertEqual("~", self.meta("TASK-013")["blocks"])

    def test_into_work_needs_force(self) -> None:
        self.run_script("TASK-014", "--block", "TASK-013")

        refused = self.run_script("TASK-014", "development")
        self.assertNotEqual(0, refused.returncode, "стоящая задача взята в работу молча")
        self.assertIn("TASK-013", refused.stderr + refused.stdout,
                      "отказ не называет, чего задача ждёт")
        self.assertEqual("todo", self.meta("TASK-014")["status"])

        forced = self.run_script("TASK-014", "development", "--force")
        self.assertEqual(0, forced.returncode, forced.stderr)
        self.assertEqual("development", self.meta("TASK-014")["status"])

    def test_stalled_report_still_works(self) -> None:
        self.run_script("TASK-014", "--block", "TASK-013")
        report = json.loads(self.run_script("--stalled").stdout)
        self.assertEqual(1, report["total"])


class StaleForAgentTest(Project):
    """Протухшую пометку видит и агент, а не только доска (TASK-148).

    Пока скрипт считал простой по одному наличию поля, закрытый блокер держал
    задачу насмерть: очередь помечала её стоящей, агент пропускал свободную
    работу, а старт отказывал ссылкой на задачу, которая давно в `done`.
    """

    def setUp(self) -> None:
        super().setUp()
        self.make("TASK-013", "Блокер")
        self.make("TASK-014", "Ждущая")
        self.run_script("TASK-014", "--block", "TASK-013")

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks / "set_status.py"),
             "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def close_blocker(self, status: str = "done", reason: str = "") -> None:
        args = ["TASK-013", status] + (["--reason", reason] if reason else [])
        done = self.run_script(*args)
        self.assertEqual(0, done.returncode, done.stderr)

    def entry(self, task_id: str = "TASK-014") -> dict:
        queue = json.loads(self.run_script("--queue").stdout)
        return next(t for t in queue["tasks"] if t["id"] == task_id)

    def report(self, task_id: str = "TASK-014") -> dict:
        report = json.loads(self.run_script("--stalled").stdout)
        return next(t for t in report["tasks"] if t["id"] == task_id)

    def test_live_blocker_still_holds_the_queue(self) -> None:
        entry = self.entry()

        self.assertTrue(entry["stalled"], "задача с живым блокером выдана за свободную")
        self.assertFalse(entry["stale"])

    def test_closed_blocker_frees_the_queue(self) -> None:
        self.close_blocker()

        entry = self.entry()
        self.assertFalse(entry["stalled"],
                         "закрытый блокер держит задачу — агент пропустит свободную работу")
        self.assertTrue(entry["stale"], "очередь не говорит, что пометка протухла")
        self.assertEqual(["TASK-013"], entry["blocked_by"],
                         "номер из пометки пропал — снимать станет нечего")

    def test_report_names_the_stale_mark(self) -> None:
        self.close_blocker()

        task = self.report()
        self.assertTrue(task["stale"], "срез выдаёт протухшую пометку за действующую")
        self.assertTrue(task["blocked_by_tasks"][0]["resolved"])
        self.assertIn("TASK-013", task["stale_reason"])
        self.assertIn("--unblock", task["stale_reason"],
                      "срез не говорит, чем снять пометку")

    def test_start_passes_and_warns(self) -> None:
        self.close_blocker()

        started = self.run_script("TASK-014", "development")

        self.assertEqual(0, started.returncode,
                         "старт отказывает из-за блокера, который уже закрыт")
        self.assertEqual("development", self.meta("TASK-014")["status"])
        self.assertIn("--unblock", started.stdout,
                      "о протухшей пометке сказано, а чем снять — нет")

    def test_live_blocker_still_refuses_the_start(self) -> None:
        refused = self.run_script("TASK-014", "development")

        self.assertNotEqual(0, refused.returncode, "стоящая задача взята в работу молча")
        self.assertEqual("todo", self.meta("TASK-014")["status"])

    def test_paused_task_stays_stalled(self) -> None:
        """Блокер закрыт, но задача на паузе — она всё ещё стоит."""
        self.run_script("TASK-014", "--pause", "ждём стенд")
        self.close_blocker()

        self.assertTrue(self.entry()["stalled"])
        self.assertFalse(self.entry()["stale"], "пауза — не протухшая пометка")

    def test_missing_blocker_still_holds(self) -> None:
        """Битая ссылка молча не освобождает: она предупреждение, а не разрешение."""
        self.run_script("TASK-014", "--unblock")
        self.run_script("TASK-014", "--block", "TASK-404")

        self.assertTrue(self.entry()["stalled"])

    def test_cancelled_blocker_speaks_differently(self) -> None:
        """Отменённый блокер освобождает, но об отмене говорит прямо: вместе с
        ним мог отпасть и предмет самой задачи."""
        self.close_blocker("cancelled", reason="дублирует TASK-002")

        self.assertFalse(self.entry()["stalled"])
        task = self.report()
        self.assertTrue(task["blocked_by_tasks"][0]["cancelled"])
        self.assertIn("отменена", task["stale_reason"])

        started = self.run_script("TASK-014", "development")
        self.assertEqual(0, started.returncode, started.stderr)
        self.assertIn("отменена", started.stdout,
                      "отмена блокера прошла под видом выполнения")

    def test_backend_slice_says_the_same(self) -> None:
        """Срез бэкенда — тот же ответ: два источника одного среза не спорят."""
        from backend.stall import stalled_tasks
        self.close_blocker()

        task = next(t for t in stalled_tasks(self.tasks, self.pipeline)["tasks"]
                    if t["id"] == "TASK-014")

        self.assertTrue(task["stale"])
        self.assertEqual(self.report()["stale_reason"], task["stale_reason"],
                         "скрипт и бэкенд объясняют протухшую пометку по-разному")


class ApiWiringTest(unittest.TestCase):
    """Правило спрашивают все клиенты, а не только доска."""

    def setUp(self) -> None:
        self.app = (Path(__file__).resolve().parent.parent
                    / "backend" / "app.py").read_text(encoding="utf-8")

    def test_patch_checks_the_rule(self) -> None:
        self.assertIn("can_stall", self.app,
                      "PATCH ставит простой, не спрашивая правило")

    def test_move_requires_confirmation(self) -> None:
        self.assertIn("move_confirmation", self.app)
        self.assertIn("confirm", self.app,
                      "перенос стоящей задачи в работу проходит без подтверждения")

    def test_candidates_endpoint(self) -> None:
        self.assertIn("blocker_candidates", self.app,
                      "список кандидатов в блокеры считает фронт")


class UiFollowsTheRuleTest(unittest.TestCase):
    """Интерфейс спрашивает правило, а не решает сам."""

    def setUp(self) -> None:
        src = Path(__file__).resolve().parent.parent / "frontend" / "src"
        self.app = (src / "App.jsx").read_text(encoding="utf-8")
        self.modal = (src / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.card = (src / "components" / "TaskCard.jsx").read_text(encoding="utf-8")
        self.picker = (src / "components" / "TaskPicker.jsx").read_text(encoding="utf-8")
        self.api = (src / "api.js").read_text(encoding="utf-8")

    def test_buttons_hidden_in_terminal_status(self) -> None:
        """Пояснений про недоступное действие не выводим — лишний шум."""
        self.assertIn("stall?.can_set", self.modal,
                      "кнопки простоя показываются в любом статусе")

    def test_move_sends_confirmation(self) -> None:
        self.assertIn("confirm", self.api, "признак подтверждения не уходит на сервер")
        self.assertIn("stall_confirm", self.app,
                      "отказ сервера не превращается в вопрос пользователю")

    def test_candidates_come_from_backend(self) -> None:
        self.assertIn("blockerFor", self.picker)
        self.assertIn("blockerFor={taskId}", self.modal,
                      "поле блокировки предлагает всех подряд")

    def test_dialog_closes_by_escape(self) -> None:
        """Окна доски закрываются Esc — вопрос о переносе не исключение."""
        block = self.app[self.app.index("pendingMove"):]
        self.assertIn("Escape", block[:block.index("const refresh")],
                      "диалог переноса не закрывается по Esc")

    def test_dialog_button_is_not_loud(self) -> None:
        """Сплошная заливка акцентным цветом выбивается из оформления доски."""
        self.assertNotIn("bg-amber-600 hover:bg-amber-500", self.app,
                         "кнопка снятия простоя всё ещё залита акцентным цветом")

    def test_stale_blocker_is_muted(self) -> None:
        self.assertIn("stall_stale", self.card, "маркер завершённого блокера не приглушается")
        self.assertIn("resolved", self.modal, "в карточке не видно, что блокер уже не держит")
        self.assertIn("можно снимать", self.modal)

    def test_muted_marker_is_grey_including_the_glyph(self) -> None:
        """Значок — эмодзи: его цвет рисует шрифт, text-* на него не действует.

        Без обесцвечивания приглушённая пометка выглядела наполовину красной —
        как сбой отрисовки, а не как состояние.
        """
        self.assertIn("grayscale", self.card, "значок на превью остаётся цветным")
        self.assertIn("grayscale", self.modal, "значок в карточке остаётся цветным")

    def test_label_is_not_clipped_by_long_title(self) -> None:
        """Цветовой код понятен не всем — подпись обязана быть видна.

        Внутри строки с заголовком блокера её срезало многоточием.
        """
        row = self.modal[self.modal.index("blocked_by_tasks?.map"):]
        row = row[:row.index("stall?.paused")]
        self.assertIn('shrink-0 text-emerald-400/80', row,
                      "подпись «можно снимать» снова обрезается вместе с заголовком")


if __name__ == "__main__":
    unittest.main()
