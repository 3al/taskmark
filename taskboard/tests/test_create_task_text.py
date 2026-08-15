"""Текст описания доезжает до файла задачи без правки руками (TASK-085).

Скрипт создания обращался с текстом автора как с плоской строкой: вставлял
пустую строку между соседними непустыми, чтобы одиночный перенос стал абзацем.
На размеченном тексте это ломало ровно то, ради чего его размечали:

- многострочный пункт списка разваливался на пункт и оторванные абзацы;
- абзац, перенесённый по ширине строки, превращался в лесенку обрывков;
- секция «### Критерии приёмки», принесённая в описании, задваивалась с той,
  что подставляет шаблон.

Теперь правило одно на все входы: **текст — это markdown**. Перенос внутри
абзаца мягкий, абзацы разделяются пустой строкой. Скрипт ничего не вставляет.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


class CreatedTaskText(unittest.TestCase):
    """Файл задачи содержит ровно то, что дал автор."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        cfg = dict(DEFAULTS)
        cfg["harnesses"] = {"claude": True, "opencode": False}
        scaffold_project(self.tasks_dir, cfg, {"harnesses": cfg["harnesses"]})

    def _create(self, description: str, criteria: str = "критерии") -> str:
        result = subprocess.run(
            [sys.executable, str(self.tasks_dir / "create_task.py"),
             "-t", "Проверка текста", "-d", description, "-c", criteria],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        return next(self.tasks_dir.glob("TASK-*.md")).read_text(encoding="utf-8")

    # --- Списки ---

    def test_multiline_list_item_stays_one_item(self) -> None:
        """Строка продолжения пункта — не новый абзац.

        Она начинается не с маркера, а с отступа, поэтому «структурной» её
        никакая регулярка по одной строке не признает.
        """
        item = ("- пункт, который не поместился\n"
                "  в одну строку и продолжается\n"
                "  дальше третьей строкой\n"
                "- второй пункт")
        text = self._create(item)

        self.assertIn(item, text, "пункт списка разорван пустой строкой")

    def test_nested_list_is_not_broken(self) -> None:
        text = self._create("- внешний пункт\n  - вложенный\n  - ещё вложенный")

        self.assertIn("- внешний пункт\n  - вложенный\n  - ещё вложенный", text,
                      "вложенный список разорван")

    # --- Абзацы ---

    def test_soft_wrap_stays_one_paragraph(self) -> None:
        """Перенос ради ширины строки — часть оформления исходника.

        Тексты этого проекта пишутся с шириной около 90 символов; каждая такая
        строка становилась отдельным абзацем.
        """
        text = self._create(
            "Длинная мысль, перенесённая по строкам ради читаемости\n"
            "исходника, остаётся одним абзацем.")

        self.assertIn("Длинная мысль, перенесённая по строкам ради читаемости\n"
                      "исходника, остаётся одним абзацем.", text,
                      "мягкий перенос превратился в разрыв абзаца")

    def test_blank_line_still_separates_paragraphs(self) -> None:
        text = self._create("Первая мысль.\n\nВторая мысль.")

        self.assertIn("Первая мысль.\n\nВторая мысль.", text,
                      "пустая строка перестала разделять абзацы")

    def test_code_block_survives(self) -> None:
        text = self._create("Пример:\n\n```\nstatus: todo\nepic: ~\n```")

        self.assertIn("```\nstatus: todo\nepic: ~\n```", text,
                      "блок кода разорван")

    def test_markup_reaches_the_file_as_is(self) -> None:
        """Размеченный текст доезжает без правки руками — и через -d, и через -c."""
        description = ("### Контекст\n\n"
                       "Абзац с **акцентом** и `кодом`.\n\n"
                       "- пункт\n- второй")
        text = self._create(description, criteria="- проверяемый результат\n- второй")

        self.assertIn(description, text, "описание доехало изменённым")
        self.assertIn("- проверяемый результат\n- второй", text,
                      "критерии доехали изменёнными")

    # --- Секция критериев ---

    def test_criteria_section_is_not_duplicated(self) -> None:
        """Описание со своей секцией критериев не даёт второй такой же."""
        text = self._create(
            "Что сделать.\n\n### Критерии приёмки\n\nВсё работает.",
            criteria="")

        self.assertEqual(text.count("### Критерии приёмки"), 1,
                         "секция «Критерии приёмки» задвоилась")
        self.assertIn("Всё работает.", text, "текст автора потерялся")

    def test_template_placeholder_is_gone(self) -> None:
        """Заглушка шаблона не остаётся рядом с авторским текстом."""
        text = self._create(
            "Что сделать.\n\n### Критерии приёмки\n\nВсё работает.",
            criteria="")

        self.assertNotIn("По чему видно, что задача закрыта", text,
                         "в файле осталась незаполненная заглушка шаблона")

    def test_author_section_wins_over_flag(self) -> None:
        """Заданы оба — берём секцию из описания: она оформлена автором."""
        text = self._create(
            "Что сделать.\n\n### Критерии приёмки\n\nИз описания.",
            criteria="Из флага.")

        self.assertEqual(text.count("### Критерии приёмки"), 1)
        self.assertIn("Из описания.", text)

    def test_criteria_flag_used_when_description_has_no_section(self) -> None:
        text = self._create("Просто описание.", criteria="Из флага.")

        self.assertEqual(text.count("### Критерии приёмки"), 1)
        self.assertIn("Из флага.", text)


class FormHintTest(unittest.TestCase):
    """Форма создания идёт через тот же `-d` — подсказка обязана совпасть."""

    def setUp(self) -> None:
        self.src = (FRONTEND / "components" / "NewTaskModal.jsx").read_text(
            encoding="utf-8")

    def test_hint_does_not_promise_paragraphs_from_newlines(self) -> None:
        self.assertNotIn("одиночные переносы станут абзацами", self.src,
                         "форма обещает поведение, которого больше нет")

    def test_hint_explains_blank_line(self) -> None:
        self.assertIn("Абзацы через пустую строку", self.src,
                      "форма не говорит, чем разделяются абзацы")


if __name__ == "__main__":
    unittest.main()
