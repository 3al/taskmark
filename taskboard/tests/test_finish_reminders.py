"""Напоминание в момент, когда работа над задачей кончается (TASK-093).

Текстовое правило «финализируй скиллом» не сработало: напоминание, лежащее
вдали от места действия, проигрывает контексту, который «вроде бы уже есть».
Поэтому говорит сам скрипт — в момент операции.

Где именно кончается работа, выводится из конфига, а не из имён статусов:
есть релизный хвост (`actions.release_draft`) — работа кончается перед ним,
нет — в терминальном статусе.

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
from backend.scaffold import scaffold_project  # noqa: E402
from tests.test_set_status_script import load_script  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "templates" / "tasks" / "set_status.py"
SKILLS = ROOT / "templates" / "agentic" / ".claude" / "skills"

# Проект с релизным хвостом: работа кончается в «Готово к выпуску», а done
# наступает при выпуске — когда задачу закрывает уже релизный скилл
RELEASE_CFG = {**DEFAULTS,
               "pipeline": ["backlog", "todo", "development", "testing",
                            "ready_for_release", "release_notes", "to_release",
                            "done", "cancelled"],
               "actions": {"create": "backlog", "pick": "todo",
                           "start": "development", "return": "development",
                           "release_draft": "release_notes",
                           "release_lock": "to_release"},
               "harnesses": {"claude": True, "opencode": False}}

# Проект без релизного хвоста: работа кончается в терминальном статусе
PLAIN_CFG = {**DEFAULTS,
             "pipeline": ["backlog", "todo", "development", "testing",
                          "completed", "cancelled"],
             "actions": {"create": "backlog", "pick": "todo",
                         "start": "development", "return": "development"},
             "harnesses": {"claude": True, "opencode": False}}

TASK_FILE = """---
id: {task_id}
title: {title}
epic: ~
status: {status}
created: 2026-08-01 10:00
blocked_by: {blocked_by}
---

## Описание

Тестовая задача.

## Чеклист

{checklist}

## Заметки агента

## История коммитов

{commits}
"""


class Project(unittest.TestCase):
    """Развёрнутый проект с доской и скриптом — как у пользователя."""

    CFG = RELEASE_CFG

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks = Path(self._tmp.name) / "project" / "tasks"
        scaffold_project(self.tasks, self.CFG, {"harnesses": self.CFG["harnesses"]})
        self.write_config()
        self.mod = load_script()

    def write_config(self, **extra) -> None:
        (self.tasks / ".taskboard.json").write_text(
            json.dumps({"pipeline": self.CFG["pipeline"],
                        "actions": self.CFG["actions"], **extra},
                       ensure_ascii=False), encoding="utf-8")

    def make(self, task_id: str = "TASK-001", title: str = "Первая",
             status: str = "testing", section: str = "## Testing",
             checklist: str = "- [x] Сделать", commits: str = "- `abc1234` правка",
             blocked_by: str = "~") -> Path:
        name = f"{task_id}-{title.lower()}.md"
        path = self.tasks / name
        path.write_text(TASK_FILE.format(task_id=task_id, title=title, status=status,
                                         checklist=checklist, commits=commits,
                                         blocked_by=blocked_by), encoding="utf-8")
        board = self.tasks / "board.md"
        lines = board.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower() == section.lower():
                lines.insert(i + 1, f"\n- {task_id} · [{title}]({name})")
                break
        else:  # pragma: no cover - раздел обязан быть на доске
            self.fail(f"раздел {section} отсутствует на доске")
        board.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def move(self, task_id: str, status: str) -> dict:
        return self.mod.set_status(self.tasks, task_id, status, agent="Тест")

    def reminders(self, task_id: str, status: str) -> list[str]:
        result = self.move(task_id, status)
        self.assertTrue(result.get("ok"), result.get("error"))
        return result.get("reminders", [])


class WorkDoneStatusTest(Project):
    """Статус завершения работы выводится из конфига, а не из имён."""

    def _work_done(self, cfg: dict) -> str | None:
        pipeline = self.mod.pipeline_of(cfg)
        return self.mod.work_done_status(cfg, pipeline)

    def test_release_tail_moves_finish_earlier(self) -> None:
        """Есть подготовка релиза — работа кончается перед ней, а не в done."""
        self.assertEqual("ready_for_release", self._work_done(RELEASE_CFG))

    def test_without_release_tail_finish_is_terminal(self) -> None:
        self.assertEqual("completed", self._work_done(PLAIN_CFG))

    def test_offramp_is_not_the_finish(self) -> None:
        """Съезд с маршрута — не завершение работы: это отмена."""
        cfg = {**PLAIN_CFG, "pipeline": ["backlog", "development", "cancelled", "done"]}
        self.assertEqual("done", self._work_done(cfg))

    def test_unknown_release_draft_ignored(self) -> None:
        """Цель, которой нет в пайплайне, не делает финиш пустым."""
        cfg = {**PLAIN_CFG,
               "actions": {**PLAIN_CFG["actions"], "release_draft": "нет-такого"}}
        self.assertEqual("completed", self._work_done(cfg))


class ReminderScopeTest(Project):
    """Напоминание печатается ровно там, где работа кончается."""

    def test_finish_status_reminds_about_vault(self) -> None:
        self.make(status="testing", section="## Testing")
        self.write_config(vault=True)

        reminders = self.reminders("TASK-001", "ready_for_release")

        self.assertTrue(any("волт" in r.lower() for r in reminders),
                        f"о волте не напомнили: {reminders}")

    def test_intermediate_status_is_silent(self) -> None:
        self.make(status="development", section="## Development")

        self.assertEqual([], self.reminders("TASK-001", "testing"))

    def test_release_statuses_are_silent(self) -> None:
        """Дальше по хвосту напоминать поздно: работа кончилась раньше."""
        self.make(status="ready_for_release", section="## Ready for Release")
        self.write_config(vault=True)

        self.assertEqual([], self.reminders("TASK-001", "to_release"))

    def test_no_vault_no_vault_reminder(self) -> None:
        """Волта в проекте нет — незачем звать к хранилищу, которого нет."""
        self.make(status="testing", section="## Testing")

        reminders = self.reminders("TASK-001", "ready_for_release")

        self.assertFalse(any("волт" in r.lower() for r in reminders), reminders)


class ReminderContentTest(Project):
    """Проверяемое — проверяем, а не напоминаем о нём общей фразой."""

    def test_unchecked_boxes_named(self) -> None:
        self.make(status="testing", section="## Testing",
                  checklist="- [x] Сделать\n- [ ] Написать тест\n- [ ] Обновить справку")

        reminders = self.reminders("TASK-001", "ready_for_release")
        text = "\n".join(reminders)

        self.assertIn("Написать тест", text)
        self.assertIn("Обновить справку", text)

    def test_closed_checklist_silent(self) -> None:
        self.make(status="testing", section="## Testing",
                  checklist="- [x] Сделать\n- [x] Проверить")

        text = "\n".join(self.reminders("TASK-001", "ready_for_release"))

        self.assertNotIn("чеклист", text.lower())

    def test_empty_commit_history_named(self) -> None:
        self.make(status="testing", section="## Testing", commits="")

        text = "\n".join(self.reminders("TASK-001", "ready_for_release"))

        self.assertIn("История коммитов", text)

    def test_filled_commit_history_silent(self) -> None:
        self.make(status="testing", section="## Testing", commits="- `abc1234` правка")

        text = "\n".join(self.reminders("TASK-001", "ready_for_release"))

        self.assertNotIn("История коммитов", text)

    def test_waiting_tasks_named(self) -> None:
        """Свой простой скрипт снимет, а чужие пометки остаются на них."""
        self.make("TASK-001", "Первая", status="testing", section="## Testing")
        self.make("TASK-002", "Вторая", status="todo", section="## To Do",
                  blocked_by="TASK-001")

        text = "\n".join(self.reminders("TASK-001", "ready_for_release"))

        self.assertIn("TASK-002", text)
        self.assertIn("--unblock", text)

    def test_nobody_waits_silent(self) -> None:
        self.make("TASK-001", "Первая", status="testing", section="## Testing")
        self.make("TASK-002", "Вторая", status="todo", section="## To Do")

        text = "\n".join(self.reminders("TASK-001", "ready_for_release"))

        self.assertNotIn("TASK-002", text)


class ReminderIsNotAGateTest(Project):
    """Напоминание — подсказка: переход идёт, код возврата прежний."""

    def test_transition_completes(self) -> None:
        path = self.make(status="testing", section="## Testing",
                         checklist="- [ ] Не закрыт", commits="")

        result = self.move("TASK-001", "ready_for_release")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertIn("status: ready_for_release", path.read_text(encoding="utf-8"))

    def test_cli_prints_and_exits_zero(self) -> None:
        self.make(status="testing", section="## Testing", checklist="- [ ] Не закрыт")
        self.write_config(vault=True)

        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "TASK-001", "ready_for_release",
             "--agent", "Тест", "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("[!]", proc.stdout)
        self.assertIn("Не закрыт", proc.stdout)


class PlainProjectTest(Project):
    """Проект без релизного хвоста: напоминание приходит в терминальном статусе."""

    CFG = PLAIN_CFG

    def test_terminal_status_reminds(self) -> None:
        self.make(status="testing", section="## Testing",
                  checklist="- [ ] Не закрыт")

        text = "\n".join(self.reminders("TASK-001", "completed"))

        self.assertIn("Не закрыт", text)

    def test_testing_is_silent(self) -> None:
        self.make(status="development", section="## Development",
                  checklist="- [ ] Не закрыт")

        self.assertEqual([], self.reminders("TASK-001", "testing"))


class FinalizeSkillTest(unittest.TestCase):
    """Скилл описывает то же правило: волт обновляется в конце работы."""

    def test_skill_names_the_rule(self) -> None:
        text = (SKILLS / "finalize-task" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("release_draft", text,
                      "скилл не говорит, откуда берётся статус завершения работы")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
