"""Применение обновления: что проверяется до git-операции (TASK-087).

В итерации «узнать» из манифеста брались только версия и заметки — чужой ответ
испортил бы максимум показанный текст. Здесь иначе: из манифеста приходит тег,
на который выполняется `git merge`, то есть исполняемое действие по данным
из сети. Отсюда три гейта — remote берётся из локального git, тег проверяется
как данные, версия обязана быть строго новее.

Сети в тестах нет: git-проверки идут на временном репозитории.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import updater, version  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")


def write_tree(root: Path, ver: str) -> None:
    """Минимальное дерево инструмента: VERSION и собранный dist."""
    tool = root / "taskboard"
    (tool / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
    (tool / "VERSION").write_text(f"{ver}\n", encoding="utf-8")
    (tool / "frontend" / "dist" / "index.html").write_text("<html>", encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    """Выполнить git в репозитории и вернуть stdout."""
    out = subprocess.run([GIT, *args], cwd=str(repo), capture_output=True,
                         text=True, encoding="utf-8")
    if out.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {out.stderr}")
    return out.stdout.strip()


class TagValidationTest(unittest.TestCase):
    """Имя тега приходит по сети и попадает в командную строку git."""

    def test_release_tag_is_accepted(self) -> None:
        self.assertTrue(updater.valid_tag("v1.2.3"))
        self.assertTrue(updater.valid_tag("v10.0.11"))

    def test_branch_name_is_not_a_tag(self) -> None:
        self.assertFalse(updater.valid_tag("main"))
        self.assertFalse(updater.valid_tag("refs/heads/main"))

    def test_version_without_v_is_refused(self) -> None:
        self.assertFalse(updater.valid_tag("1.2.3"))

    def test_incomplete_version_is_refused(self) -> None:
        self.assertFalse(updater.valid_tag("v1.2"))

    def test_command_injection_is_refused(self) -> None:
        for bad in ("v1.2.3; rm -rf /", "v1.2.3 && echo", "--upstream",
                    "v1.2.3\nv9.9.9", "v1.2.3 --exec=sh", "../v1.2.3",
                    "v1.2.3 -f", "$(id)", "`id`"):
            with self.subTest(tag=bad):
                self.assertFalse(updater.valid_tag(bad), bad)

    def test_empty_and_wrong_types_are_refused(self) -> None:
        for bad in ("", "   ", None, 123, ["v1.2.3"]):
            with self.subTest(tag=bad):
                self.assertFalse(updater.valid_tag(bad))


@unittest.skipIf(GIT is None, "git не найден в PATH")
class RepoTest(unittest.TestCase):
    """Временный репозиторий с origin — как установка пользователя."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)

        # bare-репозиторий вместо сети: fetch ходит в него
        self.origin = base / "origin.git"
        # -b main: иначе HEAD голого репозитория смотрит на master, и клон
        # приезжает без локальной ветки
        git(base, "init", "--bare", "-b", "main", "-q", str(self.origin))

        self.repo = base / "work"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        # Структура как у настоящей копии: версия и собранный фронтенд на месте
        write_tree(self.repo, "1.0.0")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "первый")
        git(self.repo, "remote", "add", "origin", str(self.origin))
        git(self.repo, "push", "-q", "origin", "main")

        # Кэш проверки — во временную папку: он глобальный и per-user
        self._cache = updater.CACHE_FILE
        updater.CACHE_FILE = base / "update.json"
        self.addCleanup(lambda: setattr(updater, "CACHE_FILE", self._cache))

        self._supervised = os.environ.pop("TASKBOARD_SUPERVISED", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._supervised is None:
            os.environ.pop("TASKBOARD_SUPERVISED", None)
        else:
            os.environ["TASKBOARD_SUPERVISED"] = self._supervised

    def cache_latest(self, ver: str = "9.9.9", tag: str | None = None) -> None:
        updater.write_cache({"checked_at": 0, "error": None,
                             "latest": {"version": ver,
                                        "tag": tag if tag is not None else f"v{ver}",
                                        "notes": "", "date": ""}})

    def plan(self) -> dict:
        return updater.plan({"update_check": "manual"}, self.repo)

    def reasons(self, plan: dict) -> str:
        return " | ".join(plan.get("blockers", []))


class LocalRemoteTest(RepoTest):
    """Обновляемся оттуда, откуда клонировались, — не по адресу из манифеста."""

    def test_origin_is_found(self) -> None:
        self.assertEqual("origin", updater.local_remote(self.repo))

    def test_other_remote_name_is_used(self) -> None:
        git(self.repo, "remote", "rename", "origin", "upstream")

        self.assertEqual("upstream", updater.local_remote(self.repo))

    def test_origin_wins_when_several(self) -> None:
        git(self.repo, "remote", "add", "mirror", str(self.origin))

        self.assertEqual("origin", updater.local_remote(self.repo))

    def test_no_remote_is_none(self) -> None:
        git(self.repo, "remote", "remove", "origin")

        self.assertIsNone(updater.local_remote(self.repo))


class PlanTest(RepoTest):
    """Можно ли обновляться и, если нет, почему именно."""

    def test_clean_repo_with_newer_version_is_ready(self) -> None:
        self.cache_latest()

        plan = self.plan()

        self.assertTrue(plan["ok"], self.reasons(plan))
        self.assertEqual("v9.9.9", plan["tag"])
        self.assertEqual("9.9.9", plan["version"])

    def test_head_is_recorded_for_rollback(self) -> None:
        """Откат возможен только если знаешь, куда откатывать."""
        self.cache_latest()

        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.plan()["head"])

    def test_same_version_is_not_an_update(self) -> None:
        self.cache_latest(version.current())

        plan = self.plan()

        self.assertFalse(plan["ok"])
        self.assertTrue(plan["blockers"])

    def test_older_version_never_rolls_user_back(self) -> None:
        """Чужой манифест не должен уметь откатить пользователя назад."""
        self.cache_latest("0.0.1")

        self.assertFalse(self.plan()["ok"])

    def test_dirty_worktree_blocks(self) -> None:
        self.cache_latest()
        (self.repo / "taskboard" / "VERSION").write_text("правка пользователя\n", encoding="utf-8")

        plan = self.plan()

        self.assertFalse(plan["ok"])
        self.assertIn("незакоммич", self.reasons(plan).lower())

    def test_untracked_files_do_not_block(self) -> None:
        """У части установок папка задач лежит в дереве инструмента."""
        self.cache_latest()
        (self.repo / "tasks").mkdir()
        (self.repo / "tasks" / "board.md").write_text("доска", encoding="utf-8")

        plan = self.plan()

        self.assertTrue(plan["ok"], self.reasons(plan))

    def test_bad_tag_blocks(self) -> None:
        self.cache_latest(tag="main")

        plan = self.plan()

        self.assertFalse(plan["ok"])
        self.assertIn("тег", self.reasons(plan).lower())

    def test_missing_remote_blocks(self) -> None:
        self.cache_latest()
        git(self.repo, "remote", "remove", "origin")

        plan = self.plan()

        self.assertFalse(plan["ok"])
        self.assertIn("remote", self.reasons(plan).lower())

    def test_dev_mode_blocks(self) -> None:
        """dev-супервизор перезапустит сервер посреди git-операции."""
        self.cache_latest()
        os.environ["TASKBOARD_SUPERVISED"] = "1"

        plan = self.plan()

        self.assertFalse(plan["ok"])
        self.assertIn("dev", self.reasons(plan).lower())

    def test_not_a_repository_blocks(self) -> None:
        self.cache_latest()
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()

        plan = updater.plan({"update_check": "manual"}, plain)

        self.assertFalse(plan["ok"])

    def test_all_blockers_are_listed_at_once(self) -> None:
        """Чинить преграды по одной, запуская обновление заново, — худший сценарий."""
        self.cache_latest(tag="main")
        (self.repo / "taskboard" / "VERSION").write_text("правка\n", encoding="utf-8")
        os.environ["TASKBOARD_SUPERVISED"] = "1"

        self.assertGreaterEqual(len(self.plan()["blockers"]), 3)

    def test_manual_command_is_offered_anyway(self) -> None:
        """Отказали — покажи, что сделать руками."""
        self.cache_latest()
        (self.repo / "taskboard" / "VERSION").write_text("правка\n", encoding="utf-8")

        self.assertIn("merge --ff-only", self.plan()["command"])


def load_launcher():
    """Загрузить taskboard.py как модуль: он вне пакета и без зависимостей."""
    import importlib.util
    path = ROOT.parent / "taskboard.py"
    spec = importlib.util.spec_from_file_location("taskboard_launcher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(GIT is None, "git не найден в PATH")
class ApplyTest(RepoTest):
    """Сама git-операция: fast-forward на тег, верификация, откат."""

    def setUp(self) -> None:
        super().setUp()
        self.launcher = load_launcher()
        self.installed = []
        # Обмен с сервером идёт через ~/.taskboard — глобальное состояние
        # пользователя. Тест, записавший туда свой результат, покажет человеку
        # плашку о провале обновления, которого не было (так и случилось)
        base = Path(self._tmp.name)
        self.launcher.UPDATE_DIR = base / "global"
        self.launcher.UPDATE_REQUEST = base / "global" / "update_apply.json"
        self.launcher.UPDATE_RESULT = base / "global" / "update_result.json"

    def publish(self, ver: str = "9.9.9", tag: str = "v9.9.9") -> None:
        """Выложить в origin новую версию с тегом — как это делает выпуск."""
        clone = Path(self._tmp.name) / f"maker-{tag}"
        if clone.exists():
            shutil.rmtree(clone)
        git(Path(self._tmp.name), "clone", "-q", str(self.origin), str(clone))
        git(clone, "config", "user.email", "test@example.com")
        git(clone, "config", "user.name", "Test")
        write_tree(clone, ver)
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", f"релиз {ver}")
        git(clone, "tag", tag)
        git(clone, "push", "-q", "origin", "main", "--tags")

    def apply(self, tag: str = "v9.9.9", version_: str = "9.9.9") -> dict:
        request = {"tag": tag, "version": version_,
                   "head": git(self.repo, "rev-parse", "HEAD")}
        return self.launcher.apply_update(
            self.repo, request,
            install_deps=lambda: self.installed.append(True) or True)

    def version_file(self) -> str:
        return (self.repo / "taskboard" / "VERSION").read_text(encoding="utf-8").strip()

    def test_fast_forward_updates_the_copy(self) -> None:
        self.publish()

        result = self.apply()

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual("9.9.9", self.version_file())

    def test_dependencies_are_installed_after_merge(self) -> None:
        """requirements.txt мог смениться — иначе новая версия не стартует."""
        self.publish()

        self.apply()

        self.assertTrue(self.installed, "pip install не выполнялся")

    def test_local_commits_stop_the_update(self) -> None:
        """Правки пользователя не затираем: fast-forward невозможен — отказ."""
        self.publish()
        (self.repo / "мой.txt").write_text("моё", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "моя работа")
        mine = git(self.repo, "rev-parse", "HEAD")

        result = self.apply()

        self.assertFalse(result["ok"])
        self.assertEqual(mine, git(self.repo, "rev-parse", "HEAD"),
                         "коммит пользователя потерян")

    def test_missing_tag_in_remote_is_refused(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD")

        result = self.apply(tag="v9.9.9")

        self.assertFalse(result["ok"])
        self.assertEqual(head, git(self.repo, "rev-parse", "HEAD"))

    def test_invalid_tag_never_reaches_git(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD")

        result = self.apply(tag="main; rm -rf /")

        self.assertFalse(result["ok"])
        self.assertEqual(head, git(self.repo, "rev-parse", "HEAD"))

    def test_version_mismatch_rolls_back(self) -> None:
        """Тег есть, а версия в нём не та — стартовать такое нельзя."""
        self.publish(ver="9.9.9", tag="v9.9.9")
        head = git(self.repo, "rev-parse", "HEAD")

        result = self.apply(tag="v9.9.9", version_="8.8.8")

        self.assertFalse(result["ok"])
        self.assertEqual(head, git(self.repo, "rev-parse", "HEAD"),
                         "откат к записанному HEAD не выполнен")
        self.assertEqual("1.0.0", self.version_file())

    def test_rollback_message_mentions_dependencies(self) -> None:
        """Отката зависимостей не существует — об этом говорим прямо."""
        self.publish(ver="9.9.9", tag="v9.9.9")

        result = self.apply(tag="v9.9.9", version_="8.8.8")

        self.assertIn("зависимост", (result.get("error") or "").lower())

    def test_result_never_lands_in_the_real_home(self) -> None:
        """Итог пишется по пути модуля — иначе тест покажет человеку плашку
        о провале обновления, которого не было."""
        self.publish()

        self.apply()

        self.assertTrue(self.launcher.UPDATE_RESULT.is_file())
        self.assertIn(self._tmp.name, str(self.launcher.UPDATE_RESULT))
        self.assertFalse((Path.home() / ".taskboard" / "update_result.json").exists(),
                         "тест записал результат в глобальное состояние пользователя")

    def test_result_is_written_for_the_interface(self) -> None:
        """После перезапуска сервер должен знать, чем кончилось обновление."""
        self.publish()
        target = Path(self._tmp.name) / "result.json"

        self.launcher.apply_update(
            self.repo, {"tag": "v9.9.9", "version": "9.9.9",
                        "head": git(self.repo, "rev-parse", "HEAD")},
            install_deps=lambda: True, result_file=target)

        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertTrue(saved["ok"])
        self.assertEqual("9.9.9", saved["version"])


class ResultVisibilityTest(RepoTest):
    """Плашка об итоге показывается только когда итог есть."""

    def setUp(self) -> None:
        super().setUp()
        self._result = updater.RESULT_FILE
        updater.RESULT_FILE = Path(self._tmp.name) / "update_result.json"
        self.addCleanup(lambda: setattr(updater, "RESULT_FILE", self._result))

    def test_no_result_is_null_not_empty_object(self) -> None:
        """`{}` во фронте истинно — окно нарисует плашку о пустоте."""
        info = updater.status({"update_check": "manual"}, self.repo)

        self.assertIsNone(info["last_result"])

    def test_existing_result_is_returned(self) -> None:
        updater.RESULT_FILE.write_text(
            json.dumps({"ok": True, "version": "9.9.9", "at": 1}), encoding="utf-8")

        info = updater.status({"update_check": "manual"}, self.repo)

        self.assertTrue(info["last_result"]["ok"])
        self.assertEqual("9.9.9", info["last_result"]["version"])

    def test_frontend_requires_a_timestamp(self) -> None:
        modal = (ROOT / "frontend" / "src" / "components"
                 / "UpdateModal.jsx").read_text(encoding="utf-8")

        self.assertIn("last_result?.at", modal,
                      "окно верит любому непустому значению — вернётся ложная плашка")


class WiringTest(unittest.TestCase):
    """Части собраны в цепочку: кнопка → сервер → лаунчер → git."""

    def test_launcher_accepts_the_flag(self) -> None:
        text = (ROOT.parent / "taskboard.py").read_text(encoding="utf-8")

        self.assertIn('"--apply-update"', text)

    def test_launcher_waits_for_the_port_before_touching_git(self) -> None:
        """Git при живом сервере — то, ради чего всё и делается отдельно."""
        text = (ROOT.parent / "taskboard.py").read_text(encoding="utf-8")
        wait = text.index("args.respawn or args.apply_update")
        apply_call = text.index("apply_update(ROOT, request)")

        self.assertLess(wait, apply_call, "git запускается до освобождения порта")

    def test_launcher_applies_before_importing_backend(self) -> None:
        text = (ROOT.parent / "taskboard.py").read_text(encoding="utf-8")

        self.assertLess(text.index("apply_update(ROOT, request)"),
                        text.index("from backend.app import app"),
                        "backend импортируется до обновления")

    def test_lifecycle_spawns_launcher_with_the_flag(self) -> None:
        text = (ROOT / "backend" / "lifecycle.py").read_text(encoding="utf-8")

        self.assertIn('extra=["--apply-update"]', text)

    def test_api_endpoints_exist(self) -> None:
        text = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

        self.assertIn('"/api/update/plan"', text)
        self.assertIn('"/api/update/apply"', text)
        self.assertIn("updater.plan(cfg, ROOT_DIR)", text)

    def test_frontend_calls_apply(self) -> None:
        js = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
        modal = (ROOT / "frontend" / "src" / "components"
                 / "UpdateModal.jsx").read_text(encoding="utf-8")

        self.assertIn("/api/update/apply", js)
        self.assertIn("api.updateApply()", modal)
        self.assertIn("plan.blockers", modal)


class NoGitCleanTest(unittest.TestCase):
    """`git clean` не используется нигде: он снёс бы незаигноренную tasks/."""

    def test_sources_never_call_git_clean(self) -> None:
        for path in (ROOT / "backend" / "updater.py", ROOT.parent / "taskboard.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('"clean"', text, f"{path.name}: git clean запрещён")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
