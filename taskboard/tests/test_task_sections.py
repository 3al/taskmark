"""Правка описания и критериев из UI (TASK-060).

Описание задачи заводит человек, а оформляет агент — значит в окне почти всегда
открывают **уже размеченный markdown**. Отсюда два свойства правки:

1. текст пишется в файл дословно: никаких «переносы → абзацы» (`as_paragraphs`
   уместна на вводе сырого текста в форме создания, а здесь она разорвала бы
   каждый абзац, набранный по ширине, на россыпь однострочных);
2. пишется **только тело своей секции** — соседние секции и заметки агента,
   которые тот дописывает параллельно, остаются нетронутыми.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.task_parser import parse_task, set_task_section  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
MODAL = SRC / "components" / "TaskModal.jsx"
EDITOR = SRC / "components" / "MarkdownEditor.jsx"
AGENTIC = Path(__file__).resolve().parent.parent / "templates" / "agentic"

TASK = """---
id: TASK-001
title: Тестовая задача
epic: ~
status: development
created: 2026-08-01 00:00
---

## Описание

Первый абзац задачи,
перенесённый по ширине.

### Критерии приёмки

TDD: RED -> GREEN -> ALL TESTS PASS

## Чеклист

- [ ] Написать тест (RED)

## Комментарии

- **2026-08-01 00:10** · Claude Opus 5 · заметка, дописанная агентом

## История коммитов
"""


class TaskSectionsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir(parents=True)
        self.path = self.tasks / "TASK-001-тестовая-задача.md"
        self.path.write_text(TASK, encoding="utf-8")

    def _text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def _section(self, heading: str) -> str:
        """Тело секции из файла — для проверки, что соседей не задело."""
        text = self._text()
        start = text.index(heading) + len(heading)
        rest = text[start:]
        m = re.search(r"^#{1,6} ", rest, flags=re.M)
        return rest[:m.start()].strip() if m else rest.strip()


class SectionsInTaskTest(TaskSectionsFixture):
    """Что бэкенд отдаёт окну задачи: сырые тексты редактируемых секций."""

    def test_parse_task_returns_editable_sections(self) -> None:
        task = parse_task(self.tasks, "TASK-001")
        assert task is not None
        sections = {s["key"]: s for s in task.get("sections", [])}
        self.assertIn("description", sections, "описание не отдаётся как редактируемая секция")
        self.assertIn("criteria", sections, "критерии не отдаются как редактируемая секция")

    def test_section_carries_heading_and_text(self) -> None:
        """Заголовок нужен окну, чтобы разрезать тело задачи на блоки."""
        task = parse_task(self.tasks, "TASK-001")
        assert task is not None
        section = next(s for s in task["sections"] if s["key"] == "description")
        self.assertEqual(section["heading"], "## Описание")
        self.assertIn("Первый абзац задачи,\nперенесённый по ширине.", section["text"])

    def test_subheadings_stay_inside_description(self) -> None:
        """Длинное описание разбивают подзаголовками — правила это советуют.

        Если границей секции считать заголовок любого уровня, описание
        обрывается на первом же `### Что делаем`, и большая часть текста
        перестаёт редактироваться.
        """
        self.path.write_text(TASK.replace(
            "Первый абзац задачи,\nперенесённый по ширине.",
            "Вступление.\n\n### Что делаем\n\nСуть.\n\n### Границы\n\nПределы."),
            encoding="utf-8")
        task = parse_task(self.tasks, "TASK-001")
        assert task is not None
        section = next(s for s in task["sections"] if s["key"] == "description")
        self.assertIn("### Что делаем", section["text"], "подзаголовок выпал из описания")
        self.assertIn("Пределы.", section["text"], "хвост описания выпал из правки")
        self.assertNotIn("Критерии приёмки", section["text"],
                         "критерии затянуло в описание — они правятся отдельно")

    def test_criteria_end_at_next_section(self) -> None:
        task = parse_task(self.tasks, "TASK-001")
        assert task is not None
        section = next(s for s in task["sections"] if s["key"] == "criteria")
        self.assertEqual(section["text"], "TDD: RED -> GREEN -> ALL TESTS PASS")

    def test_heading_inside_code_block_is_not_a_boundary(self) -> None:
        """Заголовок внутри блока кода — часть примера, а не граница секции.

        В этом проекте описание задачи сплошь и рядом показывает фрагмент
        `board.md` или другого файла задачи, а там строки начинаются с `##`.
        Пока такая строка обрывала секцию, API отдавал обрезанное описание,
        окно рисовало незакрытый блок кода, а правка карандашом работала не
        с тем текстом, который человек считает описанием (TASK-120).
        """
        self.path.write_text(TASK.replace(
            "Первый абзац задачи,\nперенесённый по ширине.",
            "Симптом:\n\n```\n## Release Notes\n\n\n## To Release\n\n_(нет)_\n```\n\n"
            "Хвост описания после блока."),
            encoding="utf-8")
        task = parse_task(self.tasks, "TASK-001")
        assert task is not None
        section = next(s for s in task["sections"] if s["key"] == "description")
        self.assertIn("## Release Notes", section["text"],
                      "блок кода вырезан из описания")
        self.assertIn("Хвост описания после блока.", section["text"],
                      "описание оборвалось на заголовке внутри блока кода")
        self.assertNotIn("Критерии приёмки", section["text"],
                         "критерии затянуло в описание")

    def test_edit_keeps_code_block_intact(self) -> None:
        """Правка соседней секции не должна рвать блок кода в описании."""
        self.path.write_text(TASK.replace(
            "Первый абзац задачи,\nперенесённый по ширине.",
            "Симптом:\n\n```\n## Release Notes\n```\n\nХвост."),
            encoding="utf-8")
        set_task_section(self.tasks, "TASK-001", "criteria", "Новые критерии")
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("```"), 2, "блок кода в описании развалился")
        self.assertIn("Хвост.", text, "хвост описания потерялся при правке соседа")

    def test_tilde_fence_counts_too(self) -> None:
        """Забор бывает и из тильд — markdown принимает оба вида."""
        self.path.write_text(TASK.replace(
            "Первый абзац задачи,\nперенесённый по ширине.",
            "Пример:\n\n~~~\n## Done\n~~~\n\nПосле примера."),
            encoding="utf-8")
        task = parse_task(self.tasks, "TASK-001")
        assert task is not None
        section = next(s for s in task["sections"] if s["key"] == "description")
        self.assertIn("После примера.", section["text"],
                      "описание оборвалось на заголовке внутри ~~~-блока")

    def test_missing_section_is_not_reported(self) -> None:
        """Секции нет в файле — правки нет: карандаш рисовать не над чем."""
        self.path.write_text(
            "---\nid: TASK-001\ntitle: Т\nstatus: todo\n---\n\n## Чеклист\n\n- [ ] раз\n",
            encoding="utf-8")
        task = parse_task(self.tasks, "TASK-001")
        assert task is not None
        self.assertEqual([s["key"] for s in task.get("sections", [])], [])


class SetSectionTest(TaskSectionsFixture):
    """Запись: своя секция меняется, всё остальное остаётся как было."""

    def test_description_replaced(self) -> None:
        result = set_task_section(self.tasks, "TASK-001", "description", "Новый текст задачи.")
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual(self._section("## Описание"), "Новый текст задачи.")

    def test_criteria_survive_description_edit(self) -> None:
        """Подзаголовок внутри «Описания» — та поломка, ради которой поля разные."""
        set_task_section(self.tasks, "TASK-001", "description", "Новый текст.")
        text = self._text()
        self.assertIn("### Критерии приёмки", text, "подзаголовок критериев снесло правкой")
        self.assertIn("TDD: RED -> GREEN -> ALL TESTS PASS", text)

    def test_agent_notes_survive(self) -> None:
        """Пока карточка открыта, агент дописывает заметки — их нельзя потерять."""
        set_task_section(self.tasks, "TASK-001", "description", "Новый текст.")
        self.assertIn("заметка, дописанная агентом", self._text())
        self.assertIn("## История коммитов", self._text())

    def test_frontmatter_untouched(self) -> None:
        set_task_section(self.tasks, "TASK-001", "criteria", "Новые критерии.")
        self.assertTrue(self._text().startswith("---\nid: TASK-001\ntitle: Тестовая задача"))
        self.assertIn("status: development", self._text())

    def test_criteria_replaced_without_touching_description(self) -> None:
        set_task_section(self.tasks, "TASK-001", "criteria", "Проверяется руками.")
        self.assertEqual(self._section("### Критерии приёмки"), "Проверяется руками.")
        self.assertIn("Первый абзац задачи,", self._text())

    def test_text_saved_verbatim(self) -> None:
        """Ключевое решение задачи: никаких автопреобразований текста.

        Открывают уже оформленное описание, где перенос внутри абзаца — часть
        оформления. Преобразование «одиночный перенос → абзац» разорвало бы его.
        """
        body = ("Абзац, набранный\nпо ширине окна.\n\n"
                "- пункт списка\n- второй пункт\n\n"
                "**Акцент** и `код`.")
        set_task_section(self.tasks, "TASK-001", "description", body)
        self.assertEqual(self._section("## Описание"), body)

    def test_description_with_subheadings_replaced_whole(self) -> None:
        """Правка описания с подзаголовками не оставляет за собой хвостов."""
        self.path.write_text(TASK.replace(
            "Первый абзац задачи,\nперенесённый по ширине.",
            "Вступление.\n\n### Что делаем\n\nСуть.\n\n### Границы\n\nПределы."),
            encoding="utf-8")
        set_task_section(self.tasks, "TASK-001", "description", "Коротко и ясно.")
        text = self._text()
        self.assertEqual(self._section("## Описание"), "Коротко и ясно.")
        self.assertNotIn("### Что делаем", text, "старые подзаголовки описания остались")
        self.assertNotIn("Пределы.", text)
        self.assertIn("### Критерии приёмки", text)
        self.assertIn("TDD: RED -> GREEN -> ALL TESTS PASS", text)

    def test_empty_text_allowed(self) -> None:
        """Очистить описание — законное действие; заголовки при этом на месте."""
        result = set_task_section(self.tasks, "TASK-001", "description", "")
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual(self._section("## Описание"), "")
        self.assertIn("### Критерии приёмки", self._text())

    def test_unknown_section_key_rejected(self) -> None:
        result = set_task_section(self.tasks, "TASK-001", "notes", "мимо")
        self.assertFalse(result.get("ok"), "правка неизвестной секции прошла")

    def test_section_absent_in_file_rejected(self) -> None:
        self.path.write_text(
            "---\nid: TASK-001\ntitle: Т\nstatus: todo\n---\n\n## Чеклист\n\n- [ ] раз\n",
            encoding="utf-8")
        result = set_task_section(self.tasks, "TASK-001", "description", "текст")
        self.assertFalse(result.get("ok"), "правка несуществующей секции прошла")

    def test_unknown_task_rejected(self) -> None:
        result = set_task_section(self.tasks, "TASK-404", "description", "текст")
        self.assertFalse(result.get("ok"))

    def test_crlf_file_survives(self) -> None:
        """Файлы пользователя бывают с CRLF — правка не должна плодить смесь."""
        self.path.write_text(TASK.replace("\n", "\r\n"), encoding="utf-8", newline="")
        result = set_task_section(self.tasks, "TASK-001", "description", "Новый текст.")
        self.assertTrue(result.get("ok"), result.get("error"))
        raw = self.path.read_bytes()
        self.assertNotIn(b"\r\r", raw, "переводы строк размножились")
        self.assertIn("Новый текст.", self._text())


class TaskModalEditingTest(unittest.TestCase):
    """Вёрстка правки: тест-раннера фронтенда нет, читаем исходник."""

    def setUp(self) -> None:
        self.src = MODAL.read_text(encoding="utf-8")

    def test_section_editor_exists(self) -> None:
        self.assertIn("saveSection", self.src, "в окне задачи нет сохранения секции")
        # Имена секций окно не знает: заголовки и ключи приходят с бэкенда,
        # который и владеет структурой файла задачи
        self.assertIn("splitSections", self.src, "тело задачи не режется на блоки по секциям")
        self.assertNotIn("'## Описание'", self.src,
                         "имя секции захардкожено во фронтенде — источник правды один, бэкенд")

    def test_split_skips_code_fences(self) -> None:
        """Резка на блоки обязана понимать блоки кода — иначе `## Release Notes`
        из примера обрывает описание, окно рисует незакрытый забор, и дальше
        весь текст расползается подложками (TASK-120).

        Правило то же, что на бэкенде (`mask_code_fences`): границу секции ищут
        по тексту без содержимого заборов.
        """
        self.assertIn("maskCodeFences", self.src, "фронт режет текст, не глядя на блоки кода")
        split = self.src[self.src.index("export function splitSections"):
                         self.src.index("// Модалка с полным содержимым")]
        self.assertIn("maskCodeFences", split, "маска не применяется при поиске границ")

    def test_pencil_sits_next_to_heading(self) -> None:
        """Карандаш — вплотную за словом заголовка, как у названия задачи.

        У края блока он читается как кнопка «чего-то там справа»; симметрия с
        названием важнее: одно действие — один вид.
        """
        self.assertIn("SectionHeading", self.src, "заголовок секции не рисуется отдельно")
        self.assertIn("TitleActions", self.src)
        self.assertNotIn("absolute right-0 top-3", self.src,
                         "карандаш секции снова уехал к краю блока")

    def test_open_editing_holds_the_window(self) -> None:
        """Пока правка открыта, окно не закрывается ни Esc, ни промахом мыши.

        Esc в поле гасится самим редактором, но в режиме предпросмотра фокуса
        на textarea нет — нажатие уходит в окно и унесло бы набранный текст.
        """
        self.assertIn("if (editSection) { cancelSection(); return }", self.src,
                      "Esc при открытой правке закрывает всё окно")
        self.assertIn("if (!editSection) onClose()", self.src,
                      "клик мимо карточки закрывает окно вместе с несохранённым текстом")

    def test_editing_does_not_resize_the_window(self) -> None:
        """Правка меняет только высоту поля: раздвигать окно пробовали — от этого
        под коротким описанием повисает пустая область."""
        self.assertNotIn("min-w-[min(64rem,92vw)]", self.src,
                         "окно снова расширяется на время правки")
        self.assertIn("minRows={10}", self.src,
                      "поле открывается слишком низким для набора")

    def test_editor_is_a_separate_component(self) -> None:
        """Правка текста нужна не только описанию — редактор общий."""
        self.assertTrue(EDITOR.is_file(), "редактор не вынесен в свой компонент")
        self.assertIn("MarkdownEditor", self.src, "окно задачи не использует общий редактор")
        self.assertNotIn("textarea", self.src.split("saveSection")[-1],
                         "поле правки текста снова живёт внутри окна задачи")


class MarkdownEditorTest(unittest.TestCase):
    """Общий редактор: поведение поля и минимальная панель разметки."""

    def setUp(self) -> None:
        self.src = EDITOR.read_text(encoding="utf-8")

    def test_enter_does_not_save(self) -> None:
        """Поле многострочное: Enter — перенос, сохраняет Ctrl+Enter."""
        self.assertIn("ctrlKey", self.src,
                      "нет сохранения по Ctrl+Enter — Enter в описании обязан давать перенос")

    def test_escape_does_not_close_modal(self) -> None:
        self.assertIn("stopPropagation", self.src,
                      "Esc в поле правки закроет всё окно вместе с текстом")

    def test_preview_toggle(self) -> None:
        """Правят сырой markdown — результат надо видеть до сохранения."""
        self.assertIn("preview", self.src.lower(),
                      "нет предпросмотра: человек не увидит, что сломал разметку")

    def test_markup_panel_covers_basics(self) -> None:
        """Минимум, ради которого панель и заводилась: жирный, курсив, код, список."""
        for markup in ("'**'", "'*'", "'`'"):
            self.assertIn(f"applyWrap({markup}", self.src, f"нет кнопки {markup}")
        self.assertIn("applyList", self.src, "нет кнопки списка")

    def test_markup_panel_has_code_block(self) -> None:
        """Инлайн-кода мало: кусок текста заворачивают в блок — забор даёт <pre><code>."""
        self.assertIn("applyCodeBlock", self.src, "нет кнопки блока кода")
        self.assertIn("```", self.src, "блок кода ставится не забором ```")

    def test_code_block_works_by_lines(self) -> None:
        """Забор живёт своей строкой: посреди строки markdown его не увидит."""
        block = self.src[self.src.index("const applyCodeBlock"):self.src.index("const onKeyDown")]
        self.assertIn("lastIndexOf('\\n'", block,
                      "выделение не расширяется до границ строк — забор встанет посреди текста")

    def test_code_block_toggles_off(self) -> None:
        """Повторное нажатие снимает забор — и захваченный выделением, и стоящий вокруг."""
        block = self.src[self.src.index("const applyCodeBlock"):self.src.index("const onKeyDown")]
        self.assertIn("lines.slice(1, -1)", block,
                      "забор, попавший в выделение, не снимается")
        self.assertIn("prevStart", block,
                      "курсор внутри блока: забор вокруг выделения не снимается")

    def test_markup_toggles_off(self) -> None:
        """Повторное нажатие на размеченном куске снимает разметку.

        Разметка стоит либо внутри выделения (выделили `код` со знаками), либо
        вокруг него (выделили слово внутри уже размеченного) — сниматься должна
        в обоих случаях, иначе кнопка «ставит вторые звёздочки».
        """
        self.assertIn("picked.startsWith(before)", self.src,
                      "разметка внутри выделения не снимается")
        self.assertIn("text.slice(from - before.length, from) === before", self.src,
                      "разметка вокруг выделения не снимается")
        self.assertIn("marked ?", self.src, "префикс строки (список, заголовок) не снимается")

    def test_selection_trimmed_before_wrapping(self) -> None:
        """Двойной клик по слову захватывает пробел за ним — внутрь знаков он не идёт."""
        self.assertIn("while (to > from && /\\s/.test(text[to - 1])) to--", self.src,
                      "хвостовой пробел выделения попадёт внутрь разметки")

    def test_field_looks_like_the_form(self) -> None:
        """Поле правки и поля формы создания — одно и то же по виду.

        Разное оформление у полей, делающих одно и то же, читается как разный
        смысл; поэтому классы живут в `fields.js`, а не копируются.
        """
        self.assertIn("FORM_FIELD", self.src, "поле правки оформлено само по себе")
        # Моноширинный остаётся только на кнопках панели (` и #) — там это знак,
        # а не текст
        field = self.src.split("<textarea")[-1].split("/>")[0]
        self.assertNotIn("font-mono", field,
                         "моноширинный в поле: markdown его не требует, а рядом с "
                         "рендером он читается мельче")
        fields = (SRC / "fields.js").read_text(encoding="utf-8")
        self.assertIn("FORM_FIELD", fields, "общего класса поля формы нет")
        form = (SRC / "components" / "NewTaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("FORM_FIELD", form, "форма создания задачи живёт своей копией стиля")

    def test_panel_does_not_reformat_text(self) -> None:
        """Панель вставляет разметку по требованию и ничего не делает сама.

        Автопреобразования здесь запрещены: правят уже оформленный markdown,
        где перенос внутри абзаца — часть оформления.
        """
        self.assertNotIn("as_paragraphs", self.src)
        self.assertNotIn("replace(/\\n(?!\\n)/g", self.src,
                         "редактор переписывает переводы строк за автором")

    def test_editor_is_reusable(self) -> None:
        """Компонент не знает, что правит: только текст и что делать по сохранению."""
        self.assertNotIn("api.", self.src, "редактор сам ходит в API — переиспользовать не выйдет")
        self.assertNotIn("description", self.src, "редактор знает про описание задачи")
        for prop in ("value", "onChange", "onSave", "onCancel"):
            self.assertIn(prop, self.src, f"в контракте редактора нет {prop}")


class FormattingRulesTest(unittest.TestCase):
    """Оформление после ручной правки: у правила должен быть момент исполнения.

    Правило «оформи описание» живёт в start-task и срабатывает один раз — при
    взятии задачи в работу. Правку, внесённую позже (например, в тестировании),
    ловить нечем, поэтому тот же шаг нужен там, где агент снова открывает файл.
    """

    def _skill(self, name: str) -> str:
        return (AGENTIC / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def test_finalize_checks_description_formatting(self) -> None:
        skill = self._skill("finalize-task").lower()
        self.assertIn("описание", skill)
        self.assertTrue("оформ" in skill or "причеш" in skill,
                        "finalize-task не причёсывает описание, испорченное ручной правкой")

    def test_fix_task_checks_description_formatting(self) -> None:
        skill = self._skill("fix-task").lower()
        self.assertTrue("оформ" in skill or "причеш" in skill,
                        "fix-task не причёсывает описание, испорченное ручной правкой")


if __name__ == "__main__":
    unittest.main()
