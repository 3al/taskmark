"""Тесты структуры и читаемости файла задачи (TASK-007, поглощает TASK-025).

Файл задачи читают двое: человек в окне доски и агент в следующей сессии.
Сейчас структура захардкожена в create_task.py, эталона `_TEMPLATE.md` нет
(хотя скиллы и правила на него ссылаются), а заметки агента сливаются в одну
нечитаемую строку без времени и с чужой моделью.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import TASKS_TEMPLATES, scaffold_project  # noqa: E402
from backend.validator import validate_project  # noqa: E402

AGENTIC = Path(__file__).resolve().parent.parent / "templates" / "agentic"
FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"

# Секции, которые обязан иметь файл задачи. Порядок важен: по нему читают
SECTIONS = ("## Описание", "### Критерии приёмки", "## Чеклист",
            "## Заметки агента", "## История коммитов")

# Формат строки заметки агента: дата, время, модель, суть — одной строкой списка.
# Отступ допускаем: в скиллах примеры живут внутри нумерованных списков
NOTE_LINE = re.compile(r"^\s*- \*\*\d{4}-\d{2}-\d{2} \d{2}:\d{2}\*\* · .+ · .+$", re.M)


class TaskTemplateTest(unittest.TestCase):
    """`_TEMPLATE.md` — видимый эталон структуры, а не фантом из инструкций."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)
        self.cfg["harnesses"] = {"claude": True, "opencode": True}

    def test_template_is_shipped(self) -> None:
        template = TASKS_TEMPLATES / "_TEMPLATE.md"
        self.assertTrue(template.is_file(), "скиллы ссылаются на шаблон, которого нет в поставке")
        text = template.read_text(encoding="utf-8")
        for section in SECTIONS:
            self.assertIn(section, text, f"в шаблоне нет секции {section}")

    def test_template_deployed_and_checked(self) -> None:
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": self.cfg["harnesses"]})
        deployed = self.tasks_dir / "_TEMPLATE.md"
        self.assertTrue(deployed.is_file(), "шаблон не разворачивается вместе со структурой")

        deployed.unlink()
        codes = [d["code"] for d in validate_project(self.tasks_dir, self.cfg)["degraded"]]
        self.assertIn("no_template", codes, "пропажа шаблона проходит незамеченной")

        scaffold_project(self.tasks_dir, self.cfg, {"parts": ["template"]})
        self.assertTrue(deployed.is_file(), "шаблон не восстанавливается кнопкой")

    def test_created_task_matches_template(self) -> None:
        """Задача из скрипта и эталон не должны расходиться по структуре."""
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": self.cfg["harnesses"]})
        result = subprocess.run(
            [sys.executable, str(self.tasks_dir / "create_task.py"),
             "-t", "Проверка структуры", "-d", "описание", "-c", "критерии"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)

        created = next(self.tasks_dir.glob("TASK-*.md"))
        text = created.read_text(encoding="utf-8")
        for section in SECTIONS:
            self.assertIn(section, text, f"созданная задача без секции {section}")

        # Время создания: по одной дате не отличить две задачи, заведённые подряд
        self.assertRegex(text, r"(?m)^created: \d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
                         "в created нет времени")
        # `patch` — рудимент прежнего процесса, в новых задачах его быть не должно
        self.assertNotIn("patch:", text, "поле patch снова попало в задачу")

    def test_input_newlines_become_paragraphs(self) -> None:
        """Enter в поле формы должен давать абзац, а не склейку в простыню.

        Markdown соединяет одиночные переводы строк, поэтому текст автора
        разделяем пустой строкой на входе — файл остаётся обычным markdown.
        """
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": self.cfg["harnesses"]})
        result = subprocess.run(
            [sys.executable, str(self.tasks_dir / "create_task.py"),
             "-t", "Многострочное описание",
             "-d", "Первая мысль.\nВторая мысль.\n- пункт списка\n- второй пункт",
             "-c", "критерии"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)

        text = next(self.tasks_dir.glob("TASK-*.md")).read_text(encoding="utf-8")
        self.assertIn("Первая мысль.\n\nВторая мысль.", text,
                      "соседние строки не разделены в абзацы")
        self.assertIn("- пункт списка\n- второй пункт", text,
                      "список разорван пустой строкой")


class StartTaskQueueTest(unittest.TestCase):
    """Очередь спрашивают у скрипта, а не помнят (TASK-062).

    Первая итерация задачи велела «перечитать board.md». Не сработало:
    напоминание про чтение файла проигрывает контексту, который «вроде бы
    уже есть», и агент назвал устаревшую очередь в отчёте о финализации.
    Работает другая форма — та же, что для статусов: **обязательный вызов
    команды**. Поэтому скиллы и правила требуют `--queue`, а не чтения файла.
    """

    def _skill(self, name: str) -> str:
        return (AGENTIC / ".claude" / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8")

    def _rules_queue_section(self) -> str:
        rules = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")
        return rules.split("## Живая очередь")[-1].split("\n## ")[0]

    def test_start_task_asks_script_for_queue(self) -> None:
        skill = self._skill("start-task")
        self.assertIn("--queue", skill, "скилл старта не спрашивает очередь у скрипта")
        self.assertIn("устар", skill.lower(),
                      "не объяснено, почему прочитанное в начале сессии не годится")

    def test_start_task_important_notes_queue_freshness(self) -> None:
        important = self._skill("start-task").split("## Важно")[-1]
        self.assertIn("--queue", important,
                      "инвариант свежей очереди не закреплён в разделе «Важно»")

    def test_finalize_task_asks_script_before_naming_next(self) -> None:
        """Именно здесь всё и сломалось: отчёт о финализации назвал очередь по памяти."""
        skill = self._skill("finalize-task")
        self.assertIn("--queue", skill,
                      "скилл финализации позволяет назвать следующую задачу по памяти")

    def test_rules_demand_asking_the_script(self) -> None:
        """Правила говорят то же, иначе скилл и правила противоречат друг другу."""
        queue = self._rules_queue_section()
        self.assertIn("--queue", queue,
                      "правила не требуют спрашивать очередь у скрипта")

    def test_rules_ban_naming_queue_from_memory(self) -> None:
        """Запрет распространяется на любое упоминание очереди, а не только на выбор."""
        queue = self._rules_queue_section().lower()
        self.assertIn("по памяти", queue,
                      "в правилах нет прямого запрета называть очередь по памяти")


class AgentNotesFormatTest(unittest.TestCase):
    """Формат заметок агента: модель, время, отдельная строка (бывш. TASK-025)."""

    def test_template_carries_no_agent_instructions(self) -> None:
        """Файл задачи читает человек: инструкции агенту живут в правилах и скиллах.

        HTML-комментарии с памяткой «как писать заметки» невидимы в рендере, но
        мозолят глаза в самом файле и попадают в каждую новую задачу.
        """
        for path in (TASKS_TEMPLATES / "_TEMPLATE.md", TASKS_TEMPLATES / "create_task.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("<!--", text,
                             f"{path.name}: инструкция агенту протекла в файл задачи")

    def test_rules_require_time_and_model(self) -> None:
        rules = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")
        self.assertRegex(rules, NOTE_LINE, "правила не показывают формат строки заметки")
        self.assertIn("время", rules.lower(), "в правилах не сказано про время")

    def test_rules_warn_about_copied_model(self) -> None:
        """Агенты копируют чужую модель из соседней строки — про это надо сказать прямо."""
        rules = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")
        self.assertIn("свою", rules.lower())

    def test_skills_use_same_format(self) -> None:
        """Скиллы пишут заметки сами: их пример обязан совпадать с правилами."""
        for name in ("start-task", "fix-task", "finalize-task"):
            text = (AGENTIC / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertRegex(text, NOTE_LINE, f"{name}: пример заметки не в формате правил")


class TaskRenderingTest(unittest.TestCase):
    """Разметка задачи должна читаться глазами, а не только парситься."""

    def test_all_heading_levels_styled(self) -> None:
        css = (FRONTEND / "index.css").read_text(encoding="utf-8")
        for level in ("h4", "h5", "h6"):
            self.assertIn(f".md-body {level}", css, f"{level} в теле задачи не оформлен")

    def test_note_meta_is_dimmed(self) -> None:
        """Метку заметки (дата, время, модель) приглушаем, а не подсвечиваем.

        Она служебная: подсвеченная — спорит с сутью, ради которой заметку
        и читают. Шаблон узкий, чтобы обычные списки описания не задело.
        """
        src = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("rehypeNoteMeta", src, "метка заметки не оформляется")
        css = (FRONTEND / "index.css").read_text(encoding="utf-8")
        self.assertIn(".note-meta", css, "нет стиля приглушённой метки")

    def test_checklist_rendered_as_checkboxes(self) -> None:
        """Чеклист должен быть чекбоксами, а не текстом `[x]`: это GFM-разметка."""
        src = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("remarkGfm", src, "в окне задачи не включён GFM — чеклист остаётся текстом")

    def test_start_task_formats_description(self) -> None:
        """Задачу из формы оформляет тот, кто берёт её в работу.

        Человек в поле ввода размечать текст не будет — иначе задача так и
        останется простынёй на всё время работы над ней.
        """
        skill = (AGENTIC / ".claude" / "skills" / "start-task" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("читаемый вид", skill, "скилл старта не оформляет описание")
        self.assertIn("дословно", skill,
                      "не сказано, что формулировки автора менять нельзя")

    def test_rules_demand_structured_text(self) -> None:
        """Правила и скилл создания задачи требуют оформлять текст, а не лить простынёй."""
        rules = (AGENTIC / "rules_section.md").read_text(encoding="utf-8")
        self.assertIn("Абзац — одна мысль", rules)
        new_task = (AGENTIC / ".claude" / "skills" / "new-task" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("абзац — одна мысль", new_task.lower(),
                      "скилл создания задачи не требует оформления текста")

    def test_html_comments_not_rendered(self) -> None:
        """Служебные комментарии в файлах задач не должны доезжать до читателя.

        Старые задачи полны пометок `<!-- ... -->` для агентов; на доске они
        читаются как обычные блоки текста и мешают.
        """
        src = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("<!--", src, "комментарии не вычищаются перед рендером")
        self.assertNotIn("{task.body}", src,
                         "в рендер уходит сырое тело задачи, а не очищенное")

    def test_nested_lists_have_spacing(self) -> None:
        css = (FRONTEND / "index.css").read_text(encoding="utf-8")
        self.assertIn("li > ul", css, "вложенные списки сливаются с родительским")


if __name__ == "__main__":
    unittest.main()
