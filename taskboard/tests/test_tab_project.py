"""Проект вкладки: запрос сам называет, к какому проекту он относится (TASK-071).

Активный проект в реестре один на машину, а вкладок у доски несколько. Вкладка,
которая показывает один проект, не должна писать в другой только потому, что
соседняя переключилась последней. Запрос называет проект параметром `project`;
без параметра действует прежний активный.

Запросы идут в настоящее приложение через ASGI — так проверяется вся цепочка,
от адреса запроса до файлов проекта.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import app as app_module  # noqa: E402
from backend import config, registry  # noqa: E402
from backend.app import app  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402
from backend.watcher import TasksWatcher  # noqa: E402


def call(method: str, path: str, body: dict | None = None,
         project: str | None = None) -> tuple[int, dict]:
    """Один запрос к приложению по ASGI: (код ответа, тело JSON)."""
    query = urlencode({"project": project}) if project is not None else ""
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "",
        "headers": [(b"content-type", b"application/json"),
                    (b"host", b"127.0.0.1")],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8765),
    }
    sent = False
    status = 0
    chunks: list[bytes] = []

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    asyncio.run(app(scope, receive, send))
    raw = b"".join(chunks)
    return status, (json.loads(raw) if raw else {})


class TabProjectTest(unittest.TestCase):
    """Два проекта в реестре, последним выбран второй."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        saved = (registry.PROJECTS_FILE, registry.GLOBAL_DIR,
                 config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE)

        def restore() -> None:
            (registry.PROJECTS_FILE, registry.GLOBAL_DIR,
             config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE) = saved
        self.addCleanup(restore)
        registry.PROJECTS_FILE = root / "home" / "projects.json"
        registry.GLOBAL_DIR = root / "home"
        config.GLOBAL_DIR = root / "home"
        config.GLOBAL_CONFIG_FILE = root / "home" / "config.json"
        # Наблюдатель сервера поднимал бы watchdog на временных папках: их потом
        # не удалить на Windows, а потоки держат процесс тестов
        for method in ("watch", "stop"):
            stub = patch.object(app_module.watcher, method)
            stub.start()
            self.addCleanup(stub.stop)

        cfg = dict(DEFAULTS)
        cfg["harnesses"] = {"claude": True, "opencode": False}
        self.dirs = {}
        for name in ("первый", "второй"):
            tasks = root / name / "tasks"
            scaffold_project(tasks, cfg, {"harnesses": cfg["harnesses"]})
            registry.register_project(tasks, name=name, activate=True)
            self.dirs[name] = tasks
        self.assertEqual(registry.list_projects()["active"], "второй")

    def _tasks(self, name: str) -> list[str]:
        return sorted(p.name for p in self.dirs[name].glob("TASK-*.md"))

    def test_task_created_from_tab_goes_to_its_project(self) -> None:
        status, body = call("POST", "/api/tasks",
                            {"title": "Из первой вкладки"}, project="первый")

        self.assertEqual(status, 200, body)
        self.assertEqual(len(self._tasks("первый")), 1)
        self.assertEqual(self._tasks("второй"), [])
        self.assertIn("Из первой вкладки",
                      (self.dirs["первый"] / "board.md").read_text(encoding="utf-8"))

    def test_other_edits_from_tab_go_to_its_project(self) -> None:
        for name in ("первый", "второй"):
            call("POST", "/api/tasks", {"title": f"Задача {name}"}, project=name)
        before = {p.name: p.read_text(encoding="utf-8")
                  for p in self.dirs["второй"].glob("*.md")}

        status, body = call("POST", "/api/tasks/TASK-001/comment",
                            {"text": "из первой вкладки"}, project="первый")
        self.assertEqual(status, 200, body)
        status, body = call("PATCH", "/api/tasks/TASK-001",
                            {"title": "Переименована в первой"}, project="первый")
        self.assertEqual(status, 200, body)

        first = next(self.dirs["первый"].glob("TASK-001-*.md")).read_text(encoding="utf-8")
        self.assertIn("из первой вкладки", first)
        self.assertIn("Переименована в первой", first)
        after = {p.name: p.read_text(encoding="utf-8")
                 for p in self.dirs["второй"].glob("*.md")}
        self.assertEqual(after, before)

    def test_reads_answer_for_named_project(self) -> None:
        status, health = call("GET", "/api/health", project="первый")

        self.assertEqual(status, 200)
        self.assertEqual(health["project"]["name"], "первый")

    def test_request_without_project_uses_last_selected(self) -> None:
        status, health = call("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(health["project"]["name"], "второй")

    def test_named_request_does_not_change_last_selected(self) -> None:
        call("POST", "/api/tasks", {"title": "Из первой вкладки"}, project="первый")

        self.assertEqual(registry.list_projects()["active"], "второй")

    def test_removed_project_rejects_changes_without_writing(self) -> None:
        status, _ = call("DELETE", "/api/projects/первый")
        self.assertEqual(status, 200)

        status, body = call("POST", "/api/tasks",
                            {"title": "В убранный проект"}, project="первый")

        self.assertEqual(status, 404)
        self.assertEqual(body["detail"]["code"], "unknown_project")
        self.assertEqual(self._tasks("первый"), [])
        self.assertEqual(self._tasks("второй"), [])

    def test_project_list_works_with_any_tab_project(self) -> None:
        """Список проектов нужен вкладке, чтобы выбрать, куда перейти."""
        call("DELETE", "/api/projects/первый")

        status, body = call("GET", "/api/projects", project="первый")

        self.assertEqual(status, 200)
        self.assertEqual([p["name"] for p in body["projects"]], ["второй"])


class WatchAllProjectsTest(unittest.TestCase):
    """Живые обновления приходят и о проекте, выбранном не последним."""

    def test_change_in_any_watched_project_is_announced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dirs = [Path(tmp) / name / "tasks" for name in ("первый", "второй")]
            for d in dirs:
                d.mkdir(parents=True)
            w = TasksWatcher(debounce_sec=0.05)
            self.addCleanup(w.shutdown)
            q = w.subscribe()
            w.watch(dirs)
            time.sleep(0.3)

            for d in dirs:
                while not q.empty():
                    q.get_nowait()
                (d / "board.md").write_text("# доска\n", encoding="utf-8")
                got = threading.Event()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        if q.get(timeout=0.2) == "changed":
                            got.set()
                            break
                    except Exception:
                        continue
                self.assertTrue(got.is_set(), f"нет события о правке в {d}")


if __name__ == "__main__":
    unittest.main()
