"""Переход со своим скиллом требует назвать источник (TASK-196).

Карта «момент → скилл» была подсказкой: строка печаталась уже после
совершившегося перехода, и пропустить её ничего не стоило. Гейт стоит вместо
перехода — обойти можно, но громко и с именем, как у отмены с причиной и
списания требования.

Гейт стоит только там, где скилл развёрнут: структура задач разворачивается
отдельно от агентского окружения, и в проекте без скиллов отказ на каждом шаге
превратил бы `--manual` в механическую приписку.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.notes import TRANSITION_RE  # noqa: E402
from tests.test_finish_reminders import PLAIN_CFG, Project  # noqa: E402

MARKDOWN = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "markdown.jsx")


class GateTest(Project):
    """Без источника перехода скрипт не двигает задачу."""

    CFG = PLAIN_CFG

    def setUp(self) -> None:
        super().setUp()
        # Проект развёрнут скаффолдом, значит скиллы лежат рядом
        self.assertTrue(self.skill_path("handoff-task").exists(),
                        "тест бессмыслен без развёрнутых скиллов")

    def skill_path(self, name: str) -> Path:
        return self.tasks.parent / ".claude" / "skills" / name / "SKILL.md"

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        """Вызов командой: гейт стоит у интерфейса, а не в данных."""
        return subprocess.run(
            [sys.executable, str(self.tasks / "set_status.py"),
             "--tasks-dir", str(self.tasks), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_refuses_without_source(self) -> None:
        self.make("TASK-001", status="development", section="## Development")

        done = self.cli("TASK-001", "testing", "--agent", "Тест")

        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("handoff-task", done.stderr)

    def test_status_unchanged_after_refusal(self) -> None:
        path = self.make("TASK-002", status="development", section="## Development")

        self.cli("TASK-002", "testing", "--agent", "Тест")

        self.assertIn("status: development", path.read_text(encoding="utf-8"))

    def test_via_lets_it_through(self) -> None:
        path = self.make("TASK-003", status="development", section="## Development")

        done = self.cli("TASK-003", "testing", "--agent", "Тест",
                        "--via", "handoff-task")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("status: testing", path.read_text(encoding="utf-8"))

    def test_manual_lets_it_through_with_a_reason(self) -> None:
        path = self.make("TASK-004", status="development", section="## Development")

        done = self.cli("TASK-004", "testing", "--agent", "Тест",
                        "--manual", "скилла под рукой нет")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("скилла под рукой нет", path.read_text(encoding="utf-8"))

    def test_moment_without_a_skill_is_not_gated(self) -> None:
        """Возврат мимо рабочего статуса своего скилла не имеет — гейта нет."""
        self.make("TASK-005", status="testing", section="## Testing")

        done = self.cli("TASK-005", "todo", "--agent", "Тест")

        self.assertEqual(0, done.returncode, done.stderr)

    def test_queue_placement_is_not_gated(self) -> None:
        """Постановка в очередь — тоже момент без скилла: приоритизация."""
        path = self.make("TASK-008", status="backlog", section="## Backlog")

        done = self.cli("TASK-008", "todo", "--agent", "Тест")

        self.assertEqual(0, done.returncode, done.stderr)
        text = path.read_text(encoding="utf-8")
        self.assertIn("status: todo", text)
        self.assertNotIn("вручную", text)

    def test_no_deployed_skill_no_gate(self) -> None:
        """Скилл удалён из проекта — требовать его вызова не с чего."""
        self.skill_path("handoff-task").unlink()
        self.make("TASK-006", status="development", section="## Development")

        done = self.cli("TASK-006", "testing", "--agent", "Тест")

        self.assertEqual(0, done.returncode, done.stderr)

    def test_board_path_is_never_gated(self) -> None:
        """Правило про агентский путь: перенос мышью идёт мимо скрипта и мимо гейта."""
        self.make("TASK-007", status="development", section="## Development")

        result = self.mod.set_status(self.tasks, "TASK-007", "testing", agent="Тест")

        self.assertTrue(result.get("ok"), result.get("error"))


class ManualSignatureTest(Project):
    """Ручной ход виден в подписи перевода, а не только в причине."""

    CFG = PLAIN_CFG

    def notes(self, path: Path) -> str:
        return path.read_text(encoding="utf-8").split("## Комментарии", 1)[-1]

    def test_manual_move_is_signed_apart(self) -> None:
        path = self.make("TASK-001", status="development", section="## Development")

        self.mod.set_status(self.tasks, "TASK-001", "testing",
                            agent="Модель", manual="почему бы и нет")

        line = next(ln for ln in self.notes(path).splitlines() if "→" in ln)
        self.assertIn("вручную", line)

    def test_skill_move_is_signed_as_before(self) -> None:
        path = self.make("TASK-002", status="development", section="## Development")

        self.mod.set_status(self.tasks, "TASK-002", "testing",
                            agent="Модель", via="handoff-task")

        line = next(ln for ln in self.notes(path).splitlines() if "→" in ln)
        self.assertNotIn("вручную", line)
        self.assertIn("скрипт (Модель)", line)

    def test_manual_signature_is_still_a_transition(self) -> None:
        """Разбор перевода новую подпись принимает — иначе строка не покрасится."""
        line = ("- **2026-08-26 21:00** · скрипт вручную (Claude Opus 5) · "
                "Development → Testing")

        self.assertIsNotNone(TRANSITION_RE.match(line), line)

    def test_front_recognises_the_manual_signature(self) -> None:
        """Зеркало во фронтенде обязано принимать её же."""
        import re

        text = MARKDOWN.read_text(encoding="utf-8")
        m = re.search(r"const MOVE_SOURCE = /(.+)/\s*$", text, flags=re.M)
        self.assertIsNotNone(m, "MOVE_SOURCE не найдена")
        source = re.compile(m.group(1))

        self.assertIsNotNone(source.search(" · скрипт вручную (Claude Opus 5) · "))


class MomentLineTest(Project):
    """Строка «момент ведёт скилл X» больше не печатается: её место занял отказ."""

    CFG = PLAIN_CFG

    def test_moment_skill_returns_a_name(self) -> None:
        self.make("TASK-001", status="todo", section="## To Do")

        result = self.mod.set_status(self.tasks, "TASK-001", "development",
                                     agent="Тест", via="start-task")

        self.assertEqual("start-task", result.get("moment_skill"))

    def test_content_reminders_survive(self) -> None:
        """Содержательные напоминания к гейту отношения не имеют."""
        self.make("TASK-002", status="development", section="## Development",
                  checklist="- [ ] Вынести парсер")

        result = self.mod.set_status(self.tasks, "TASK-002", "testing",
                                     agent="Тест", via="handoff-task")

        self.assertIn("Вынести парсер", " ".join(result.get("handoff_reminders", [])))


if __name__ == "__main__":
    unittest.main()
