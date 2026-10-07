"""Выпуск переносит в main только коммиты задач состава.

Проверенное коммитится в интеграционную ветку `dev`, а `main` — только
выпущенное: пользователи обновляются на тег из `main`. Выпуск переносит в `main`
коммиты отобранных задач, пересобирает фронтенд, ставит тег и возвращает работу
на `dev`. Репозиторий поднимается во временной папке: переносы, ветки и теги
нужны настоящие, а трогать репозиторий проекта нельзя.
"""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOL = Path(__file__).resolve().parents[2] / "tools" / "release.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DIST = "taskboard/frontend/dist"
SRC = "taskboard/frontend/src"


def fake_build(root: Path) -> None:
    """Сборка, как у vite: хэш в имени файла, содержимое — из исходников."""
    src = root / SRC
    text = "".join(p.read_text(encoding="utf-8")
                   for p in sorted(src.rglob("*")) if p.is_file())
    dist = root / DIST
    for old in (dist / "assets").glob("*"):
        old.unlink()
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    name = f"index-{abs(hash(text)) % 10**8}.js"
    (dist / "assets" / name).write_text(text, encoding="utf-8")
    (dist / "index.html").write_text(f"<script src=assets/{name}>", encoding="utf-8")


class _Repo(unittest.TestCase):
    """main с выпуском 1.0.0 и ветка dev от него."""

    integration = "dev"

    def setUp(self):
        self.tool = _load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        home = self.root / "home"
        home.mkdir()
        patcher = mock.patch.object(Path, "home", return_value=home)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.git("init", "-q", "-b", "main")
        for key, value in (("user.email", "release@example.com"),
                           ("user.name", "Тест выпуска"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false"),
                           ("core.autocrlf", "false")):
            self.git("config", key, value)
        self.write("taskboard/VERSION", "1.0.0\n")
        self.write("CHANGELOG.md", "# Изменения\n\n## [1.0.0] — 2026-01-01\n\n- старт\n")
        self.write("release.json", "{}\n")
        self.write(f"{SRC}/app.js", "app\n")
        self.write("shared.txt", "один\n")
        fake_build(self.root)
        (self.root / ".gitignore").write_text("tasks/\nhome/\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "старт")
        self.git("tag", "-a", "v1.0.0", "-m", "1.0.0")

        tasks = self.root / "tasks"
        tasks.mkdir()
        (tasks / ".taskboard.json").write_text(
            json.dumps({"integration_branch": self.integration}), encoding="utf-8")
        if self.integration:
            self.git("switch", "-q", "-c", self.integration)

    # --- помощники ---------------------------------------------------------

    def git(self, *args):
        return subprocess.run(("git", *args), cwd=self.root, check=True,
                              capture_output=True, text=True,
                              encoding="utf-8").stdout.strip()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, task, files, build=True):
        """Коммит задачи, как его делает агент: правки, пересборка, история."""
        for rel, text in files.items():
            self.write(rel, text)
        if build:
            fake_build(self.root)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"{task}: правка")
        short = self.git("rev-parse", "--short", "HEAD")
        task_file = self.root / "tasks" / f"{task}-x.md"
        old = task_file.read_text(encoding="utf-8") if task_file.exists() else (
            f"---\nid: {task}\n---\n\n## Описание\n\n## История коммитов\n")
        task_file.write_text(old + f"\n- `{short}` {task}: правка\n", encoding="utf-8")
        return short

    def show(self, ref, rel):
        try:
            return self.git("show", f"{ref}:{rel}")
        except subprocess.CalledProcessError:
            return None

    def apply(self, commits, tasks=("TASK-001",)):
        return self.tool.apply("patch", "### Добавлено\n\n- новое\n", list(tasks),
                               commits=commits, root=self.root, build=fake_build)


class TransferTest(_Repo):

    def test_only_selected_commits_reach_main(self):
        a = self.commit("TASK-001", {"a.txt": "A\n", f"{SRC}/a.js": "a\n"})
        self.commit("TASK-002", {"b.txt": "B\n", f"{SRC}/b.js": "b\n"})

        result = self.apply([a])

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.show("main", "a.txt"), "A")
        self.assertIsNone(self.show("main", "b.txt"), "код невыбранной задачи уехал")
        self.assertEqual(self.git("log", "-1", "--format=%s", "main"), "Релиз 1.0.1")
        self.assertEqual(self.git("rev-parse", "v1.0.1^{commit}"),
                         self.git("rev-parse", "main"))

    def test_work_continues_on_dev(self):
        a = self.commit("TASK-001", {"a.txt": "A\n"})
        self.commit("TASK-002", {"b.txt": "B\n"})

        self.assertTrue(self.apply([a])["ok"])

        self.assertEqual(self.git("branch", "--show-current"), "dev")
        self.assertEqual((self.root / "taskboard/VERSION").read_text(encoding="utf-8").strip(),
                         "1.0.1")
        self.assertIn("1.0.1", (self.root / "release.json").read_text(encoding="utf-8"))
        self.assertTrue((self.root / "b.txt").exists(), "код B пропал из dev")
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no"), "")

    def test_dependency_on_unselected_task_is_refused(self):
        b = self.commit("TASK-002", {"shared.txt": "один\nдва от B\n"})
        a = self.commit("TASK-001", {"shared.txt": "один\nдва от B, поправил A\n"})
        main_before = self.git("rev-parse", "main")

        result = self.apply([a])

        self.assertFalse(result["ok"])
        self.assertEqual(result["conflict"]["commit"], a)
        self.assertIn(b, result["conflict"]["needs"])
        self.assertEqual(self.git("rev-parse", "main"), main_before, "main тронут")
        self.assertEqual(self.git("branch", "--show-current"), "dev")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dist_is_rebuilt_from_main_sources(self):
        self.commit("TASK-002", {f"{SRC}/b.js": "b\n"})
        a = self.commit("TASK-001", {f"{SRC}/a.js": "a\n"})
        self.commit("TASK-003", {f"{SRC}/c.js": "c\n"})

        self.assertTrue(self.apply([a])["ok"])

        html = self.show("main", f"{DIST}/index.html")
        asset = html.split("src=")[1].rstrip(">")
        built = self.show("main", f"{DIST}/{asset}")
        self.assertIn("a\n".strip(), built)
        self.assertNotIn("b", built.replace("app", ""), "сборка main видит код B")

    def test_selected_commit_already_in_main_is_skipped(self):
        a = self.commit("TASK-001", {"a.txt": "A\n"})
        self.assertTrue(self.apply([a])["ok"])
        c = self.commit("TASK-003", {"c.txt": "C\n"})

        result = self.tool.apply("patch", "- ещё\n", ["TASK-001", "TASK-003"],
                                 commits=[a, c], root=self.root, build=fake_build)

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.show("main", "c.txt"), "C")

    def test_unknown_commit_is_refused(self):
        result = self.apply(["0000000"])
        self.assertFalse(result["ok"])
        self.assertIn("0000000", result["error"])


class CheckTest(_Repo):

    def test_not_on_dev_is_a_blocker(self):
        self.git("switch", "-q", "main")
        blockers = self.tool.check(root=self.root)["blockers"]
        self.assertTrue(any("dev" in b for b in blockers), blockers)

    def test_missing_main_is_a_blocker(self):
        self.git("branch", "-q", "-m", "main", "trunk")
        blockers = self.tool.check(root=self.root)["blockers"]
        self.assertTrue(any("main" in b for b in blockers), blockers)

    def test_commit_outside_tasks_is_a_warning(self):
        self.commit("TASK-001", {"a.txt": "A\n"})
        self.write("x.txt", "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "мимо задач")
        orphan = self.git("rev-parse", "--short", "HEAD")

        result = self.tool.check(root=self.root)

        self.assertTrue(any(orphan in w for w in result["warnings"]), result)
        self.assertFalse(any(orphan in b for b in result["blockers"]))
        self.assertEqual(len(result["warnings"]), 1, "коммит задачи принят за чужой")


class SchemeOffTest(_Repo):
    integration = ""

    def test_release_tags_the_current_branch(self):
        self.write("a.txt", "A\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "правка")

        result = self.tool.apply("patch", "- новое\n", [], root=self.root,
                                 build=fake_build)

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.show("v1.0.1", "a.txt"), "A")
        self.assertEqual(self.git("branch", "--show-current"), "main")


class PublishTest(_Repo):

    def test_pushes_main_dev_and_tags(self):
        calls = []

        def fake_git(*args, **_kwargs):
            calls.append(args)
            return ""

        (self.root / "release.json").write_text(
            json.dumps({"version": "1.0.1", "tag": "v1.0.1", "notes": "x"}),
            encoding="utf-8")
        with mock.patch.object(self.tool, "_git", fake_git), \
             mock.patch.object(self.tool, "create_github_release",
                               return_value={"ok": True}):
            self.assertTrue(self.tool.publish(root=self.root)["ok"])

        push = next(c for c in calls if c[0] == "push")
        self.assertIn("main", push)
        self.assertIn("dev", push)
        self.assertIn("--tags", push)


if __name__ == "__main__":
    unittest.main()
