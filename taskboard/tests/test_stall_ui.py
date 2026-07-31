"""Простой задачи в интерфейсе: маркер, панель, фильтр и DnD (TASK-017).

Данные приходят из TASK-015 (`stalled` / `blocked_by` / `paused` на карточках
доски и в `/api/task/{id}`) — здесь проверяется, что интерфейс их показывает и
даёт править.

Тест-раннера фронтенда в проекте нет, поэтому разметка проверяется чтением
исходников: связка «поле API ↔ элемент интерфейса» рвётся молча.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.stall import set_blocked_by, set_paused, stall_details  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
SRC = ROOT / "frontend" / "src"
COMPONENTS = SRC / "components"
DOCS = ROOT.parent / "docs" / "help"

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


class StallDetailsTest(unittest.TestCase):
    """Окну задачи мало номеров: нужно видеть, кто блокирует и на каком он этапе."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        for task_id, title, status in (("TASK-013", "Первая", "development"),
                                       ("TASK-014", "Вторая", "todo")):
            (self.tasks / f"{task_id}-{title}.md").write_text(
                TASK_FILE.format(task_id=task_id, title=title, status=status),
                encoding="utf-8")

    def details(self, task_id: str) -> dict:
        from backend.task_parser import parse_task
        task = parse_task(self.tasks, task_id)
        return stall_details(self.tasks, task["meta"])

    def test_blocker_carries_title_and_status(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])

        state = self.details("TASK-014")

        self.assertTrue(state["stalled"])
        self.assertEqual([{"id": "TASK-013", "title": "Первая",
                           "status": "development", "found": True}],
                         state["blocked_by_tasks"])

    def test_missing_blocker_is_marked(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-404"])

        blocker = self.details("TASK-014")["blocked_by_tasks"][0]

        self.assertFalse(blocker["found"], "несуществующий блокер выдаётся за настоящий")
        self.assertEqual("TASK-404", blocker["id"])

    def test_blocks_side_resolved_too(self) -> None:
        set_blocked_by(self.tasks, "TASK-014", ["TASK-013"])

        state = self.details("TASK-013")

        self.assertEqual(["TASK-014"], [t["id"] for t in state["blocks_tasks"]])

    def test_paused_task_details(self) -> None:
        set_paused(self.tasks, "TASK-013", "ждём стенд")

        state = self.details("TASK-013")

        self.assertEqual("ждём стенд", state["paused"])
        self.assertEqual([], state["blocked_by_tasks"])


class TaskApiTest(unittest.TestCase):
    def test_endpoint_returns_details(self) -> None:
        src = (BACKEND / "app.py").read_text(encoding="utf-8")
        self.assertIn("stall_details", src,
                      "/api/task отдаёт только номера блокеров — окну задачи этого мало")


class CardMarkerTest(unittest.TestCase):
    """Причина простоя видна на превью: иначе заблокированную задачу берут в работу."""

    def setUp(self) -> None:
        self.src = (COMPONENTS / "TaskCard.jsx").read_text(encoding="utf-8")

    def test_card_reads_stall_fields(self) -> None:
        """Карточке нужны причины, а не производное `stalled`: у блокировки и
        паузы разные цвета, и по одному флагу их не различить."""
        for field in ("task.blocked_by", "task.paused"):
            self.assertIn(field, self.src, f"карточка не смотрит на {field}")

    def test_marker_names_blocker(self) -> None:
        """Маркер без номера блокера бесполезен: непонятно, чего ждём."""
        self.assertIn("blocked_by[0]", self.src,
                      "маркер не показывает, какая задача блокирует")

    def test_preview_row_stays_compact(self) -> None:
        """Верхняя строка разваливалась при полном наборе: номер переносился
        по слогам, ключ эпика уезжал за край."""
        self.assertNotIn("⏸ пауза", self.src,
                         "слово «пауза» рядом со значком ничего не добавляет, но занимает место")
        self.assertIn("gap-1.5", self.src, "отступы в строке номера прежние")
        self.assertIn('<span className="shrink-0">{task.id}</span>', self.src,
                      "номер задачи может перенестись по слогам")

    def test_stripe_is_not_a_border(self) -> None:
        """Полоска простоя — отдельный элемент, а не левая рамка.

        У статуса свой `hover:border-*`, он красит рамку целиком: пока полоска
        была границей, на наведении она пропадала и карточка переставала
        выглядеть стоящей.
        """
        self.assertNotIn("border-l-2", self.src, "полоска снова нарисована рамкой")
        self.assertIn("STALL_STRIPE", self.src)

    def test_stripe_shows_both_reasons(self) -> None:
        """Блокировка и пауза разом — полоска делится пополам, а не теряет одну
        из причин; второй полоски у правого края нет (там граница колонки)."""
        self.assertIn("both", self.src)
        self.assertIn("from-rose-500/70", self.src)
        self.assertIn("to-amber-400/70", self.src)
        self.assertNotIn("right-0", self.src, "появилась вторая полоска у правого края")

    def test_pause_sits_in_the_corner(self) -> None:
        """У значка без номера постоянное место читается лучше, чем позиция,
        зависящая от того, есть ли рядом блокировка."""
        block = self.src[self.src.index("task.paused &&"):self.src.index("⏸")]
        self.assertIn("ml-auto", block, "значок паузы не прижат к правому краю строки")

    def test_stall_above_epic(self) -> None:
        """Пометки простоя — наверху, эпик — вниз.

        Взгляд идёт по колонке сверху вниз: «чего задача ждёт» должно читаться
        до заголовка, а эпик — самый широкий элемент строки и самый редко
        нужный — вытеснял пометки наверху.
        """
        self.assertLess(self.src.index("⛔"), self.src.index("line-clamp-2"),
                        "пометка блокировки ушла под заголовок")
        self.assertGreater(self.src.index("task.epic"), self.src.index("line-clamp-2"),
                           "эпик всё ещё занимает верхнюю строку")


class TaskModalStallTest(unittest.TestCase):
    """Открытая карточка: полный вид простоя и правка из UI."""

    def setUp(self) -> None:
        self.src = (COMPONENTS / "TaskModal.jsx").read_text(encoding="utf-8")

    def test_shows_blocker_details(self) -> None:
        self.assertIn("blocked_by_tasks", self.src,
                      "окно задачи не показывает блокеров с их статусом")

    def test_blocker_is_clickable(self) -> None:
        self.assertIn("onOpenTask", self.src,
                      "номер блокера не открывает блокирующую задачу")

    def test_way_back_from_blocker(self) -> None:
        """Переход по блокеру — не билет в один конец."""
        self.assertIn("onBack", self.src, "из открытой блокирующей задачи некуда вернуться")
        app = (SRC / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("taskTrail", app, "путь по задачам нигде не хранится")

    def test_pause_and_block_are_editable(self) -> None:
        self.assertIn("paused", self.src)
        self.assertIn("updateTask", self.src,
                      "из окна задачи нельзя поставить или снять простой")

    def test_reason_input_is_shared_component(self) -> None:
        self.assertIn("ReasonPrompt", self.src,
                      "ввод причины сделан на месте, а не общим компонентом")

    def test_both_forms_are_built_the_same(self) -> None:
        """Пауза и блокировка — соседние поля одного смысла.

        Разная конструкция (подпись у одной, поле другой высоты у второй)
        читается как разное назначение, хотя действие одно.
        """
        self.assertIn("INLINE_FIELD", self.src,
                      "форма блокировки оформлена отдельно от формы паузы")
        self.assertIn('label="Пауза:"', self.src)
        self.assertIn("Ждёт:", self.src, "у формы блокировки нет подписи")

    def test_blocker_link_matches_card_marker(self) -> None:
        """Один и тот же простой на превью и в карточке — одного цвета.

        Блокировка красная, пауза жёлтая: два разных состояния одним цветом
        сливаются в одно пятно.
        """
        card = (COMPONENTS / "TaskCard.jsx").read_text(encoding="utf-8")
        self.assertIn("rose", card, "маркер блокировки на превью не красный")
        self.assertIn("amber", card, "маркер паузы на превью не жёлтый")
        self.assertIn("text-rose-400/90 hover:text-rose-300", self.src,
                      "номер блокера в карточке другого цвета, чем маркер на доске")

    def test_error_does_not_move_the_button(self) -> None:
        """Подпись об ошибке появляется поверх, а не раздвигает строку ввода."""
        picker = (COMPONENTS / "TaskPicker.jsx").read_text(encoding="utf-8")
        self.assertIn("absolute left-0 top-full", picker,
                      "сообщение в потоке — кнопка съезжает вниз, когда оно появляется")

    def test_buttons_match_the_window(self) -> None:
        """Синяя заливка кнопок спорит с цветом статуса в шапке карточки."""
        self.assertNotIn("bg-sky-600", self.src,
                         "кнопки правки простоя всё ещё акцентно-синие")
        reason = (COMPONENTS / "ReasonPrompt.jsx").read_text(encoding="utf-8")
        self.assertNotIn("bg-sky-600", reason)

    def test_field_focus_is_neutral(self) -> None:
        """Синяя рамка фокуса спорит с цветом статуса в шапке карточки."""
        fields = (SRC / "fields.js").read_text(encoding="utf-8")
        self.assertNotIn("focus:border-sky", fields)
        self.assertIn("focus:border-zinc", fields)


class SharedComponentsTest(unittest.TestCase):
    """Общие компоненты: их переиспользуют другие задачи эпика."""

    def test_reason_prompt_exists_and_is_generic(self) -> None:
        path = COMPONENTS / "ReasonPrompt.jsx"
        self.assertTrue(path.is_file(), "общего компонента ввода причины нет")
        src = path.read_text(encoding="utf-8")
        # TASK-042 вводит им причину отмены — «пауза» внутри сделала бы его частным
        self.assertNotIn("паузу", src.lower())
        self.assertNotIn("paused", src)

    def test_picker_returns_task_not_text(self) -> None:
        """Блокировать можно только существующую задачу, а не любой ввод."""
        src = (COMPONENTS / "TaskPicker.jsx").read_text(encoding="utf-8")
        self.assertIn("exact", src, "поле не сверяет введённое со списком задач")
        self.assertIn("onChange(next, found)", src,
                      "наверх уходит текст без найденной задачи")

    def test_picker_walks_by_keyboard(self) -> None:
        """По списку подсказок ходят стрелками, выбирают Enter."""
        src = (COMPONENTS / "TaskPicker.jsx").read_text(encoding="utf-8")
        for key in ("ArrowDown", "ArrowUp", "Enter"):
            self.assertIn(key, src, f"клавиша {key} в списке подсказок не работает")

    def test_picker_reports_unknown_task(self) -> None:
        src = (COMPONENTS / "TaskPicker.jsx").read_text(encoding="utf-8")
        self.assertIn("Такой задачи нет", src,
                      "непонятый ввод молчит до предупреждения валидатора")

    def test_typing_reopens_the_list(self) -> None:
        """Закрытый список — не то же самое, что уход из поля.

        Пока это был один флаг, после выбора или Esc человек стирал написанное,
        набирал заново — и подсказок больше не видел до повторного клика в поле.
        """
        src = (COMPONENTS / "TaskPicker.jsx").read_text(encoding="utf-8")
        self.assertIn("dismissed", src, "список закрывается тем же флагом, что и фокус")
        self.assertIn("setDismissed(false)", src, "правка текста не открывает список обратно")
        # Фокус меняют только события фокуса — иначе он снова начнёт врать
        self.assertEqual(1, src.count("setFocus(true)"))
        self.assertEqual(1, src.count("setFocus(false)"))

    def test_new_task_form_is_strict_too(self) -> None:
        """Тот же запрет в форме создания: компонент один, правило одно."""
        src = (COMPONENTS / "NewTaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("blockedTask", src)
        self.assertIn("не найдена", src)

    def test_task_picker_extracted_and_reused(self) -> None:
        path = COMPONENTS / "TaskPicker.jsx"
        self.assertTrue(path.is_file(), "подсказки задач не вынесены в общий компонент")
        new_task = (COMPONENTS / "NewTaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("TaskPicker", new_task,
                      "форма создания задачи всё ещё несёт свою копию подсказок")


class StalledFilterTest(unittest.TestCase):
    """Фильтр «стоят»: остановленные задачи разом, каждая на своём этапе."""

    def setUp(self) -> None:
        self.app = (SRC / "App.jsx").read_text(encoding="utf-8")
        self.header = (COMPONENTS / "Header.jsx").read_text(encoding="utf-8")

    def test_header_has_toggle(self) -> None:
        self.assertIn("onStalledOnly", self.header,
                      "в шапке нет переключателя простоя")

    def test_board_filters_by_stalled(self) -> None:
        self.assertIn("stalledOnly", self.app)
        self.assertIn("t.stalled", self.app,
                      "фильтр не смотрит на состояние простоя карточки")

    def test_filter_works_together_with_search(self) -> None:
        """Оба фильтра сужают доску, а не отменяют друг друга."""
        block = self.app[self.app.index("visibleColumns"):]
        block = block[:block.index("const findColumn")]
        self.assertIn("found", block)
        self.assertIn("stalledOnly", block)


class DndConfirmTest(unittest.TestCase):
    """Перенос остановленной задачи в работу — вопрос, а не запрет."""

    def setUp(self) -> None:
        self.app = (SRC / "App.jsx").read_text(encoding="utf-8")

    def test_move_asks_instead_of_blocking(self) -> None:
        self.assertIn("pendingMove", self.app,
                      "перенос заблокированной задачи не переспрашивает")

    def test_only_taking_into_work_asks(self) -> None:
        """Ждать внутри ревью или тестирования — норма, вопрос там лишний.

        Аномален ровно один переход — «взять в работу»: блокировка это и
        значит. Статусы не подставляем по именам: их даёт пайплайн проекта.
        """
        self.assertIn("isStartOfWork", self.app)
        self.assertIn("actions.start", self.app)
        self.assertIn("actions.return", self.app)

    def test_dialog_can_drop_the_stall(self) -> None:
        """Берут в работу — простой обычно уже неактуален, пусть снимется здесь же."""
        self.assertIn("Снять простой и перенести", self.app)
        self.assertIn("clearStall", self.app)

    def test_question_names_the_blocker(self) -> None:
        self.assertIn("blocked_by", self.app,
                      "вопрос не называет, чего ждёт задача")

    def test_no_native_confirm(self) -> None:
        self.assertNotIn("window.confirm", self.app,
                         "нативный confirm выбивается из темы доски")


class HelpDocsTest(unittest.TestCase):
    """Справку раздаёт сам инструмент: устаревшая врёт прямо в интерфейсе."""

    def test_board_section_explains_stall(self) -> None:
        text = (DOCS / "02-board.md").read_text(encoding="utf-8").lower()
        self.assertIn("блокир", text, "в справке о доске нет блокировок")
        self.assertIn("пауз", text, "в справке о доске нет паузы")


if __name__ == "__main__":
    unittest.main()
