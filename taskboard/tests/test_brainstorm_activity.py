"""Активный брейншторм в превью: режим, этап, срок жизни и адресат."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import brainstorm
from backend import registry
from backend.app import (BrainstormIn, api_brainstorm_state, api_brainstorm_update,
                         api_brainstorm_snapshot)
from fastapi import HTTPException


class BrainstormActivityTest(unittest.TestCase):
    def setUp(self):
        self.store = brainstorm.ActivityStore(ttl=10)

    def test_started_mode_and_phase_are_available_to_later_reader(self):
        self.store.update("проект", "TASK-001", "run-a", "simple", "research", "Codex")
        state = self.store.get("проект", "TASK-001")
        self.assertEqual((state[0]["mode"], state[0]["phase"]), ("simple", "research"))

    def test_team_advances_through_announced_phases(self):
        for phase in ("research", "critique", "responses", "final"):
            self.store.update("проект", "TASK-001", "run-a", "team", phase, "Codex")
            self.assertEqual(self.store.get("проект", "TASK-001")[0]["phase"], phase)

    def test_finished_and_cancelled_runs_disappear(self):
        for phase in ("finished", "cancelled"):
            self.store.update("проект", "TASK-001", "run-a", "simple", "research", "Codex")
            self.store.update("проект", "TASK-001", "run-a", "simple", phase, "Codex")
            self.assertEqual(self.store.get("проект", "TASK-001"), [])

    def test_abandoned_run_expires(self):
        with patch("time.monotonic", return_value=100):
            self.store.update("проект", "TASK-001", "run-a", "team", "research", "Codex")
        with patch("time.monotonic", return_value=111):
            self.assertEqual(self.store.get("проект", "TASK-001"), [])

    def test_other_task_and_project_do_not_share_activity(self):
        self.store.update("проект", "TASK-001", "run-a", "team", "research", "Codex")
        self.assertEqual(self.store.get("проект", "TASK-002"), [])
        self.assertEqual(self.store.get("другой", "TASK-001"), [])

    def test_finishing_one_run_does_not_hide_another(self):
        for run in ("run-a", "run-b"):
            self.store.update("проект", "TASK-001", run, "team", "research", "Codex")
        self.store.update("проект", "TASK-001", "run-a", "team", "finished", "Codex")
        self.assertEqual([s["run"] for s in self.store.get("проект", "TASK-001")], ["run-b"])

    def test_invalid_phase_does_not_replace_current_state(self):
        self.store.update("проект", "TASK-001", "run-a", "simple", "research", "Codex")
        with self.assertRaises(ValueError):
            self.store.update("проект", "TASK-001", "run-a", "simple", "responses", "Codex")
        self.assertEqual(self.store.get("проект", "TASK-001")[0]["phase"], "research")

    def test_board_snapshot_contains_only_active_tasks_of_its_project(self):
        self.store.update("проект", "TASK-001", "run-a", "simple", "research", "Codex")
        self.store.update("проект", "TASK-002", "run-b", "team", "critique", "Codex")
        self.store.update("другой", "TASK-003", "run-c", "team", "research", "Codex")
        self.assertEqual({s["task"] for s in self.store.get("проект")}, {"TASK-001", "TASK-002"})
        self.store.update("проект", "TASK-001", "run-a", "simple", "finished", "Codex")
        self.assertEqual({s["task"] for s in self.store.get("проект")}, {"TASK-002"})


class BrainstormApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        projects = []
        for name in ("первый", "второй"):
            tasks = self.root / name / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "TASK-001-пример.md").write_text(
                "---\nid: TASK-001\ntitle: Пример\nstatus: todo\n---\n\n## Описание\nТекст\n",
                encoding="utf-8")
            projects.append({"name": name, "tasks_dir": str(tasks)})
        self.registry_file = self.root / "projects.json"
        self.registry_file.write_text(json.dumps({"active": "второй", "projects": projects}),
                                      encoding="utf-8")
        self.addCleanup(patch.stopall)
        patch.object(registry, "PROJECTS_FILE", self.registry_file).start()
        self.run = "api-check-" + self.root.name
        self.addCleanup(lambda: brainstorm.activity.update(
            "первый", "TASK-001", self.run, "team", "finished", "Codex"))

    def test_explicit_project_is_used_even_when_another_is_active(self):
        api_brainstorm_update(BrainstormIn(project="первый", task="TASK-001",
                                          run=self.run, mode="team",
                                          phase="critique", agent="Codex"))
        state = api_brainstorm_state("TASK-001", "первый")["items"]
        self.assertEqual(state[0]["phase"], "critique")
        self.assertEqual(api_brainstorm_state("TASK-001", "второй")["items"], [])

    def test_unknown_project_or_task_is_rejected(self):
        for project, task in (("нет", "TASK-001"), ("первый", "TASK-999")):
            with self.assertRaises(HTTPException) as failure:
                api_brainstorm_update(BrainstormIn(project=project, task=task, run=self.run,
                                                  mode="team", phase="research", agent="Codex"))
            self.assertEqual(failure.exception.status_code, 404)

    def test_board_api_returns_result_link_and_activity_for_explicit_project(self):
        logs = self.root / "первый" / "tasks" / "logs"
        logs.mkdir()
        name = "TASK-001-brainstorm-log.md"
        (logs / name).write_text("# Результат", encoding="utf-8")
        api_brainstorm_update(BrainstormIn(project="первый", task="TASK-001", run=self.run,
                                          mode="team", phase="research", agent="Codex"))
        result = api_brainstorm_snapshot("первый")
        self.assertEqual(result["results"], {"TASK-001": name})
        self.assertEqual(result["items"][0]["task"], "TASK-001")
        self.assertEqual(api_brainstorm_snapshot("второй")["results"], {})


class BrainstormScriptTest(unittest.TestCase):
    def test_template_builds_addressed_update_without_notification(self):
        path = Path(__file__).resolve().parents[1] / "templates" / "tasks" / "notify.py"
        spec = importlib.util.spec_from_file_location("brainstorm_notify", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with patch("sys.argv", [str(path), "--brainstorm", "team", "--task", "TASK-001",
                                "--run", "check-1", "--phase", "responses", "--agent", "Codex"]):
            # Только внешняя сеть: код CLI, выбор проекта и построение тела настоящие.
            with patch("urllib.request.urlopen") as network:
                network.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
                self.assertEqual(script.main(), 0)
                request = network.call_args.args[0]
                self.assertTrue(request.full_url.endswith("/api/brainstorm"))
                payload = json.loads(request.data)
                self.assertEqual((payload["mode"], payload["phase"], payload["run"]),
                                 ("team", "responses", "check-1"))
                self.assertEqual(payload["task"], "TASK-001")
                self.assertNotIn("text", payload)


class BrainstormResultsTest(unittest.TestCase):
    def test_result_links_choose_latest_protocol_and_ignore_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            for name, stamp in (("TASK-001-brainstorm-log.md", 100),
                                ("TASK-001-brainstorm-team-log.md", 200),
                                ("TASK-002-brainstorm-log.md", 300),
                                ("TASK-001-notes.md", 400)):
                path = logs / name
                path.write_text("Протокол", encoding="utf-8")
                os.utime(path, (stamp, stamp))
            self.assertEqual(brainstorm.log_results(logs), {
                "TASK-001": "TASK-001-brainstorm-team-log.md",
                "TASK-002": "TASK-002-brainstorm-log.md",
            })

    def test_no_protocol_means_no_result_link(self):
        self.assertEqual(brainstorm.log_results(Path("несуществующая-папка-логов")), {})
