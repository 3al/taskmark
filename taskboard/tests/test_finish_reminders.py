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

## Комментарии

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

    def test_finish_status_reminds_about_tails(self) -> None:
        self.make(status="testing", section="## Testing", commits="")
        self.write_config(vault=True)

        reminders = self.reminders("TASK-001", "ready_for_release")

        self.assertTrue(any("История коммитов" in r for r in reminders),
                        f"о хвостах не напомнили: {reminders}")

    def test_intermediate_status_is_silent(self) -> None:
        self.make(status="development", section="## Development")

        self.assertEqual([], self.reminders("TASK-001", "testing"))

    def test_release_statuses_are_silent(self) -> None:
        """Дальше по хвосту напоминать поздно: работа кончилась раньше."""
        self.make(status="ready_for_release", section="## Ready for Release")
        self.write_config(vault=True)

        self.assertEqual([], self.reminders("TASK-001", "to_release"))

    def test_finish_no_longer_mentions_vault(self) -> None:
        """Про знание напоминают раньше — на передаче задачи (TASK-137).

        К концу работы контекст сессии, в которой знание добыто, уже не жив:
        между разработкой и этим статусом могут пройти дни, и напоминание там
        даёт заметку-отписку ради прохода вместо знания.
        """
        self.make(status="testing", section="## Testing")
        self.write_config(vault=True)

        reminders = self.reminders("TASK-001", "ready_for_release")

        self.assertFalse(any("волт" in r.lower() for r in reminders), reminders)


class ReminderContentTest(Project):
    """Проверяемое — проверяем, а не напоминаем о нём общей фразой."""

    def test_unchecked_boxes_belong_to_the_handoff(self) -> None:
        """План закрывается вместе с работой — конец работы о нём молчит."""
        self.make(status="testing", section="## Testing",
                  checklist="- [x] Сделать\n- [ ] Написать тест")

        text = "\n".join(self.reminders("TASK-001", "ready_for_release"))

        self.assertNotIn("Написать тест", text)

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

    def _typed(self, task_id: str, task_type: str) -> None:
        """Проставить тип уже созданной задаче: в шаблоне теста поля нет."""
        path = next(self.tasks.glob(f"{task_id}-*.md"))
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("epic: ~", f"epic: ~\ntype: {task_type}"),
                        encoding="utf-8")

    def test_commit_history_silent_for_commitless_types(self) -> None:
        """У ревью и обсуждения коммитов не бывает — пустая секция это норма.

        Напоминание, которое нечем закрыть, обесценивает соседние: агент
        привыкает пролистывать `[!]` вместо того, чтобы чинить (TASK-152).
        """
        for task_id, task_type in (("TASK-010", "review"),
                                   ("TASK-011", "discussion")):
            with self.subTest(type=task_type):
                self.make(task_id, f"Задача{task_id[-1]}", status="testing",
                          section="## Testing", commits="")
                self._typed(task_id, task_type)

                text = "\n".join(self.reminders(task_id, "ready_for_release"))

                self.assertNotIn("История коммитов", text)

    def test_commit_history_still_named_for_code_types(self) -> None:
        """Исключение — по названному типу, а не «раз секция пуста, молчим»."""
        self.make("TASK-012", "Кодовая", status="testing", section="## Testing",
                  commits="")
        self._typed("TASK-012", "feature")

        text = "\n".join(self.reminders("TASK-012", "ready_for_release"))

        self.assertIn("История коммитов", text)

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


class HandoffReminderTest(Project):
    """Знание записывается, пока жив контекст: на уходе из статуса работы.

    Точка вычисляется из конфига — это `actions.start`, тот же ключ, по которому
    скиллы определяют рабочий статус. Имена статусов не зашиты.
    """

    def handoff(self, task_id: str, status: str) -> list[str]:
        result = self.move(task_id, status)
        self.assertTrue(result.get("ok"), result.get("error"))
        return result.get("handoff_reminders", [])

    def test_leaving_work_status_reminds_about_vault(self) -> None:
        self.make(status="development", section="## Development")
        self.write_config(vault=True)

        text = "\n".join(self.handoff("TASK-001", "testing"))

        self.assertIn("волт", text.lower())

    def test_no_vault_no_vault_line(self) -> None:
        """Волта в проекте нет — про хранилище молчим, но сам момент называем."""
        self.make(status="development", section="## Development")

        text = " ".join(self.handoff("TASK-001", "testing"))

        self.assertNotIn("волт", text.lower())
        self.assertIn("что именно проверять", text)

    def test_backward_move_is_not_a_handoff(self) -> None:
        """Возврат в очередь — не передача на проверку: работа не кончилась."""
        self.make(status="development", section="## Development")
        self.write_config(vault=True)

        self.assertEqual([], self.handoff("TASK-001", "todo"))

    def test_other_statuses_are_silent(self) -> None:
        """Уход с любого другого этапа передачей не является."""
        self.make(status="testing", section="## Testing")
        self.write_config(vault=True)

        self.assertEqual([], self.handoff("TASK-001", "ready_for_release"))

    def test_channels_do_not_merge(self) -> None:
        """Прыжок из разработки прямо в конец работы: оба канала, но раздельно.

        Механизмы разные — передача говорит о знании, конец работы о хвостах
        задачи, — и склеенные в один список они теряют причину. Тот же вывод
        уже получен на `stage_reminders` против `reminders`.
        """
        self.make(status="development", section="## Development", commits="")
        self.write_config(vault=True)

        result = self.move("TASK-001", "ready_for_release")

        handoff = "\n".join(result.get("handoff_reminders", []))
        finish = "\n".join(result.get("reminders", []))
        self.assertIn("волт", handoff.lower())
        self.assertIn("История коммитов", finish)
        self.assertNotIn("волт", finish.lower())

    def test_cli_prints_handoff(self) -> None:
        self.make(status="development", section="## Development")
        self.write_config(vault=True)

        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "TASK-001", "testing",
             "--agent", "Тест", "--via", "тест", "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("[!]", proc.stdout)
        self.assertIn("волт", proc.stdout.lower())


class ReminderIsNotAGateTest(Project):
    """Напоминание — подсказка: переход идёт, код возврата прежний."""

    def test_transition_completes(self) -> None:
        path = self.make(status="testing", section="## Testing",
                         checklist="- [ ] Не закрыт", commits="")

        result = self.move("TASK-001", "ready_for_release")

        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertIn("status: ready_for_release", path.read_text(encoding="utf-8"))

    def test_cli_prints_and_exits_zero(self) -> None:
        self.make(status="testing", section="## Testing", commits="")
        self.write_config(vault=True)

        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "TASK-001", "ready_for_release",
             "--agent", "Тест", "--via", "тест", "--tasks-dir", str(self.tasks)],
            capture_output=True, text=True, encoding="utf-8")

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("[!]", proc.stdout)
        self.assertIn("История коммитов", proc.stdout)


class PlainProjectTest(Project):
    """Проект без релизного хвоста: напоминание приходит в терминальном статусе."""

    CFG = PLAIN_CFG

    def test_terminal_status_reminds(self) -> None:
        """Хвоста нет — работа кончается в терминальном статусе, там и напоминание."""
        self.make(status="testing", section="## Testing", commits="")

        text = "\n".join(self.reminders("TASK-001", "completed"))

        self.assertIn("История коммитов", text)

    def test_testing_is_silent(self) -> None:
        self.make(status="development", section="## Development",
                  checklist="- [ ] Не закрыт")

        self.assertEqual([], self.reminders("TASK-001", "testing"))


class SkillBoundaryTest(unittest.TestCase):
    """Скиллы описывают то же разделение моментов, что и скрипт (TASK-137).

    Расхождение текста скилла с поведением скрипта опаснее их обоих: агент
    читает скилл, а напоминание приходит из скрипта, и противоречие он решает
    в пользу того, что прочитал первым.
    """

    def skill(self, name: str) -> str:
        return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")

    def test_finalize_names_the_work_done_rule(self) -> None:
        self.assertIn("release_draft", self.skill("finalize-task"),
                      "скилл не говорит, откуда берётся статус завершения работы")

    def test_handoff_skill_exists_and_owns_the_vault(self) -> None:
        text = self.skill("handoff-task")

        self.assertIn("actions.start", text,
                      "скилл передачи должен брать рабочий статус из конфига")
        self.assertIn("write-vault", text,
                      "запись знания — главный шаг передачи на проверку")

    def test_handoff_removes_a_requirement_duplicate(self) -> None:
        """Пункт, который закрывает чужое событие, не отмечают, а удаляют.

        Закрывать за человека неподтверждённое по-прежнему нельзя — но и
        держать рядом с требованием его копию незачем: галочку поставить
        нечем, а расходиться они будут всегда.
        """
        text = self.skill("handoff-task")

        self.assertNotIn("которые закрывает проверка, не трогай", text)
        self.assertIn("удали его", text)

    def test_finalize_no_longer_writes_the_vault(self) -> None:
        """Финализация про волт только проверяет, что шаг не пропущен."""
        text = self.skill("finalize-task")

        self.assertIn("handoff-task", text,
                      "финализация должна отсылать к скиллу передачи")

    def test_handoff_is_called_by_the_agent_not_the_user(self) -> None:
        """Скилл вызывает агент, закончив работу, — просить об этом некому.

        Пользователь не знает ни про статусы, ни про срок записи знания: он ждёт,
        что ему скажут, куда смотреть. Описание, написанное «когда пользователь
        говорит…», означало бы, что скилл не вызовется никогда.
        """
        self.assertIn("Вызывай сам", self.skill("handoff-task"))

    def test_start_task_hands_over_to_handoff(self) -> None:
        """Эстафета: скилл старта — единственное место, которое агент читал,
        начиная работу, и оттуда должен узнать, чем её кончают."""
        self.assertIn("handoff-task", self.skill("start-task"))

    def test_status_change_is_the_last_step(self) -> None:
        """Статус меняется, когда файл задачи уже приведён в порядок.

        Он публикует факт «задача на этом этапе», и публиковать его раньше правды
        нельзя. Плюс требования этапа проверяются ровно в момент перехода: скилл,
        который двигает задачу до заполнения истории коммитов, упирается в
        собственный гейт.
        """
        finalize = self.skill("finalize-task")
        self.assertLess(finalize.index("Заполнить «Историю коммитов»"),
                        finalize.index("Сменить статус"),
                        "финализация двигает задачу до того, как прибрала хвосты")

        handoff = self.skill("handoff-task")
        self.assertLess(handoff.index("Закрыть план"),
                        handoff.index("Перевести в следующий статус"),
                        "передача двигает задачу до того, как прибрала работу")

    def test_handoff_has_opencode_wrapper(self) -> None:
        """Скиллы поставки парны: без обёртки opencode-проект команды не увидит."""
        wrapper = SKILLS.parent.parent / ".opencode" / "commands" / "handoff-task.md"

        self.assertTrue(wrapper.exists(), "нет обёртки .opencode для handoff-task")
        self.assertIn("handoff-task skill", wrapper.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
