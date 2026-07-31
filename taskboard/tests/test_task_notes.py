"""Заметки агента: запись скриптом и проверка структуры файла (TASK-067).

Текстовый слой правил про заметки существует с TASK-007 (формат строки, время
из системы, «модель — своя») и всё равно нарушается: агент дописывает несколько
заметок одной правкой в конце работы, выставляя время на глаз, а порядок строк
получается произвольным. Оттуда же сносимые заголовки секций и «История
коммитов», всплывающая выше «Заметок агента».

Поэтому заметку пишет скрипт: время он берёт из системы (выдумать нельзя),
строку ставит в конец секции (хронология), пропавшую секцию восстанавливает на
её месте в шаблоне. А смена статуса заодно докладывает о том, что в файле
разъехалось.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_set_status_script import load_script, render_board  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
AGENTIC = TEMPLATES / "agentic"
SCRIPT = TEMPLATES / "tasks" / "set_status.py"

NOTE_LINE = re.compile(r"^- \*\*\d{4}-\d{2}-\d{2} \d{2}:\d{2}\*\* · .+ · .+$")


def task_from_template(task_id: str, title: str, status: str = "backlog") -> str:
    """Файл задачи по эталону `_TEMPLATE.md` — той структуры, что у пользователя."""
    text = (TEMPLATES / "tasks" / "_TEMPLATE.md").read_text(encoding="utf-8")
    text = text.replace("id: TASK-NNN", f"id: {task_id}")
    text = text.replace("title: Краткое название задачи", f"title: {title}")
    text = text.replace("status: backlog", f"status: {status}")
    return text.replace("created: YYYY-MM-DD HH:MM", "created: 2026-07-30 01:26")


class NotesFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        (self.tasks / "board.md").write_text(render_board(), encoding="utf-8")
        self.mod = load_script()

    def _task(self, task_id: str = "TASK-001", title: str = "Тестовая",
              status: str = "backlog", body: str | None = None) -> Path:
        """Файл задачи + запись на доске в разделе приёма."""
        path = self.tasks / f"{task_id}-test.md"
        path.write_text(body if body is not None else task_from_template(task_id, title, status),
                        encoding="utf-8")
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        idx = lines.index("### Новый функционал")
        lines.insert(idx + 2, f"- {task_id} · [{title}]({path.name})")
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _notes(self, path: Path) -> list[str]:
        """Строки секции «Заметки агента»."""
        body = path.read_text(encoding="utf-8").split("## Заметки агента", 1)[-1]
        body = body.split("\n## ", 1)[0]
        return [ln for ln in body.splitlines() if ln.strip()]


class AddNoteTest(NotesFixture):
    """Заметку пишет скрипт: время системное, строка — в конец секции."""

    def test_note_appended_in_template_format(self) -> None:
        path = self._task()
        result = self.mod.add_note(self.tasks, "TASK-001", "корень бага в _apply_role_ui()",
                                   agent="Claude Opus 5")
        self.assertTrue(result.get("ok"), result.get("error"))

        notes = self._notes(path)
        self.assertEqual(len(notes), 1, "заметка не попала в секцию")
        self.assertRegex(notes[0], NOTE_LINE, "строка заметки не в формате правил")
        self.assertIn("Claude Opus 5", notes[0])
        self.assertIn("корень бага в _apply_role_ui()", notes[0])

    def test_time_comes_from_system(self) -> None:
        """Главный смысл команды: время нельзя выставить «на глаз»."""
        path = self._task()
        self.mod.add_note(self.tasks, "TASK-001", "первая", agent="Claude Opus 5")
        stamp = re.findall(r"\*\*(.+?)\*\*", self._notes(path)[0])
        self.assertTrue(stamp, "в заметке нет метки времени")
        written = datetime.strptime(stamp[0], "%Y-%m-%d %H:%M")
        self.assertLess(abs((datetime.now() - written).total_seconds()), 180,
                        "время заметки не системное")

    def test_notes_stay_chronological(self) -> None:
        """Ровно тот баг, с которого началась задача: свежая заметка идёт вниз."""
        path = self._task()
        for text in ("первая", "вторая", "третья"):
            self.mod.add_note(self.tasks, "TASK-001", text, agent="Claude Opus 5")
        notes = self._notes(path)
        self.assertEqual([n.split(" · ")[-1] for n in notes], ["первая", "вторая", "третья"])

    def test_agent_is_required(self) -> None:
        """Без явно названной модели заметка не пишется — иначе её копируют у соседа."""
        self._task()
        result = self.mod.add_note(self.tasks, "TASK-001", "без модели", agent=None)
        self.assertFalse(result.get("ok"), "заметка записалась без указания модели")
        self.assertIn("модел", result.get("error", "").lower())

    def test_empty_text_rejected(self) -> None:
        self._task()
        result = self.mod.add_note(self.tasks, "TASK-001", "   ", agent="Claude Opus 5")
        self.assertFalse(result.get("ok"), "пустая заметка записалась")

    def test_multiline_text_collapses(self) -> None:
        """Заметка — одна строка списка: перенос склеил бы её с соседней в абзац."""
        path = self._task()
        self.mod.add_note(self.tasks, "TASK-001", "первая мысль\nвторая мысль",
                          agent="Claude Opus 5")
        notes = self._notes(path)
        self.assertEqual(len(notes), 1, "заметка разорвана на две строки")
        self.assertIn("первая мысль вторая мысль", notes[0])

    def test_missing_section_restored_before_commits(self) -> None:
        """Снесённый заголовок восстанавливается на своём месте, а не в конце файла."""
        body = task_from_template("TASK-001", "Тестовая").replace("## Заметки агента\n\n", "")
        path = self._task(body=body)
        result = self.mod.add_note(self.tasks, "TASK-001", "секцию снесли", agent="Claude Opus 5")
        self.assertTrue(result.get("ok"), result.get("error"))

        text = path.read_text(encoding="utf-8")
        self.assertIn("## Заметки агента", text, "секция не восстановлена")
        self.assertLess(text.index("## Заметки агента"), text.index("## История коммитов"),
                        "секция восстановлена не на своём месте")
        self.assertEqual(len(self._notes(path)), 1)

    def test_commits_section_stays_last(self) -> None:
        path = self._task()
        self.mod.add_note(self.tasks, "TASK-001", "заметка", agent="Claude Opus 5")
        headings = re.findall(r"(?m)^## (.+)$", path.read_text(encoding="utf-8"))
        self.assertEqual(headings[-1].strip(), "История коммитов",
                         "«История коммитов» перестала быть последней секцией")

    def test_unknown_task_reports_error(self) -> None:
        result = self.mod.add_note(self.tasks, "TASK-404", "нет такой", agent="Claude Opus 5")
        self.assertFalse(result.get("ok"))


class TaskFileCheckTest(NotesFixture):
    """Смена статуса заодно докладывает, что в файле задачи разъехалось."""

    def test_healthy_file_has_no_warnings(self) -> None:
        self._task()
        self.assertEqual(self.mod.check_task_file(self.tasks / "TASK-001-test.md"), [])

    def test_out_of_order_notes_reported(self) -> None:
        body = task_from_template("TASK-001", "Тестовая").replace(
            "## Заметки агента\n",
            "## Заметки агента\n\n"
            "- **2026-07-30 02:25** · k3 · поздняя\n"
            "- **2026-07-30 02:18** · k3 · ранняя\n")
        self._task(body=body)
        warnings = self.mod.check_task_file(self.tasks / "TASK-001-test.md")
        self.assertTrue(any("хронолог" in w.lower() for w in warnings),
                        f"нарушенный порядок заметок не замечен: {warnings}")

    def test_missing_section_reported(self) -> None:
        body = task_from_template("TASK-001", "Тестовая").replace("## Заметки агента\n\n", "")
        self._task(body=body)
        warnings = self.mod.check_task_file(self.tasks / "TASK-001-test.md")
        self.assertTrue(any("Заметки агента" in w for w in warnings),
                        f"пропажа секции не замечена: {warnings}")

    def test_commits_not_last_reported(self) -> None:
        body = task_from_template("TASK-001", "Тестовая").replace(
            "## Заметки агента\n\n## История коммитов\n",
            "## История коммитов\n\n## Заметки агента\n")
        self._task(body=body)
        warnings = self.mod.check_task_file(self.tasks / "TASK-001-test.md")
        self.assertTrue(any("История коммитов" in w for w in warnings),
                        f"секции не на своих местах, но об этом молчат: {warnings}")

    def test_note_without_format_reported(self) -> None:
        """Заметка без времени или без модели — ровно то, что чинит --note."""
        body = task_from_template("TASK-001", "Тестовая").replace(
            "## Заметки агента\n",
            "## Заметки агента\n\n- k3: 2026-07-30: без времени и разделителей\n")
        self._task(body=body)
        warnings = self.mod.check_task_file(self.tasks / "TASK-001-test.md")
        self.assertTrue(any("формат" in w.lower() for w in warnings),
                        f"заметка не по формату прошла молча: {warnings}")

    def test_duplicated_section_reported(self) -> None:
        """Вторая «История коммитов» вместо дописывания в первую — реальный случай."""
        body = task_from_template("TASK-001", "Тестовая") + "\n## История коммитов\n"
        self._task(body=body)
        warnings = self.mod.check_task_file(self.tasks / "TASK-001-test.md")
        self.assertTrue(any("встречается" in w for w in warnings),
                        f"дубль секции прошёл молча: {warnings}")

    def test_legacy_notes_do_not_shout(self) -> None:
        """Старые задачи не должны сыпать предупреждениями на каждой смене статуса.

        Формат заметок менялся, «История коммитов» появляется только при первом
        коммите, а в файлах остались комментарии-памятки. Предупреждение о том,
        чего агент не делал, обесценивает предупреждение о том, что он сломал.
        """
        body = task_from_template("TASK-001", "Тестовая").replace(
            "## Заметки агента\n\n## История коммитов\n",
            "## Заметки агента\n\n"
            "<!-- Компактно. Макс ~15 строк.\n"
            "     Формат: АГЕНТ (модель): ДАТА ВРЕМЯ: суть -->\n\n"
            "k3: 2026-07-26: старый формат заметки до перехода на строки списка\n")
        self._task(body=body)
        self.assertEqual(self.mod.check_task_file(self.tasks / "TASK-001-test.md"), [])

    def test_set_status_returns_warnings(self) -> None:
        body = task_from_template("TASK-001", "Тестовая").replace("## Заметки агента\n\n", "")
        self._task(body=body)
        result = self.mod.set_status(self.tasks, "TASK-001", "development")
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertTrue(result.get("warnings"),
                        "смена статуса не доложила о проблемах структуры файла")


class NoteCliTest(NotesFixture):
    """То, чем пользуется агент: командная строка."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "--tasks-dir", str(self.tasks), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_cli_writes_note(self) -> None:
        path = self._task()
        result = self._run("TASK-001", "--note", "заметка из CLI", "--agent", "Claude Opus 5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(self._notes(path)[0], NOTE_LINE)

    def test_cli_note_without_agent_fails(self) -> None:
        self._task()
        result = self._run("TASK-001", "--note", "без модели")
        self.assertNotEqual(result.returncode, 0, "CLI записал заметку без модели")

    def test_cli_prints_warnings_on_status_change(self) -> None:
        body = task_from_template("TASK-001", "Тестовая").replace("## Заметки агента\n\n", "")
        self._task(body=body)
        result = self._run("TASK-001", "development")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[!]", result.stdout, "предупреждения о файле задачи не показаны")


class NotesRulesTest(unittest.TestCase):
    """Правила и скиллы обязаны вести к скрипту, а не к ручной правке файла."""

    def _skill(self, name: str) -> str:
        return (AGENTIC / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")

    def _rules(self) -> str:
        return (AGENTIC / "rules_section.md").read_text(encoding="utf-8")

    def test_rules_demand_script_for_notes(self) -> None:
        self.assertIn("--note", self._rules(), "правила не знают команды записи заметки")

    def test_rules_state_chronology(self) -> None:
        rules = self._rules().lower()
        self.assertIn("хронолог", rules)
        self.assertIn("в конец", rules)

    def test_rules_ban_removing_headings(self) -> None:
        rules = self._rules().lower()
        self.assertIn("заголовк", rules)
        self.assertTrue("не сноси" in rules or "не удаляй" in rules,
                        "в правилах нет запрета сносить заголовки секций")

    def test_rules_place_commits_last(self) -> None:
        self.assertIn("последн", self._rules().lower(),
                      "не сказано, что «История коммитов» — последняя секция")

    def test_skills_write_notes_through_script(self) -> None:
        for name in ("start-task", "fix-task", "finalize-task"):
            self.assertIn("--note", self._skill(name),
                          f"{name}: заметка пишется руками, мимо скрипта")


if __name__ == "__main__":
    unittest.main()
