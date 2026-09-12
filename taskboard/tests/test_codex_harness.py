"""Codex как третья среда поставки: раскладка, общий файл правил, хуки (TASK-241).

Codex ломает два допущения, на которых стояла поставка.

**Файл правил больше не принадлежит одной среде.** `AGENTS.md` читают и
opencode, и Codex, поэтому связь «среда → файл» стала много-к-одному, а состав
файлов приходится схлопывать: без этого `AGENTS.md` разворачивался бы дважды
и дважды же попадал в отчёт валидатора.

**Правило «скиллы одной копией» держится не всегда.** Оно опиралось на то, что
opencode читает `.claude/skills`; Codex не читает ни её, ни `.opencode/skills`
— только `.codex/skills`. Вместе с Claude Code он требует второй копии, и её
элементы отличаются префиксом имени — тем же приёмом, что у обработчиков хуков.

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
from backend.scaffold import (CODEX_HOOKS, detect_harnesses,  # noqa: E402
                              hook_registered, hooks_unregistered, part_targets,
                              register_hook, rules_files, scaffold_project)
from backend.validator import validate_project  # noqa: E402

CODEX_ONLY = {"claude": False, "opencode": False, "codex": True}
CLAUDE_CODEX = {"claude": True, "opencode": False, "codex": True}
OPENCODE_CODEX = {"claude": False, "opencode": True, "codex": True}
CLAUDE_ONLY = {"claude": True, "opencode": False, "codex": False}


class _Project(unittest.TestCase):
    """Общая заготовка: пустой проект во временной папке."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "проект"
        self.tasks = self.root / "tasks"

    def deploy(self, harnesses: dict) -> dict:
        cfg = {**DEFAULTS, "harnesses": harnesses}
        return scaffold_project(self.tasks, cfg, {"harnesses": harnesses})

    def cfg(self, harnesses: dict) -> dict:
        return {**DEFAULTS, "harnesses": harnesses}


class RulesFileSharedTest(_Project):
    """`AGENTS.md` общий для двух сред — и разворачивается один раз."""

    def test_agents_md_is_not_duplicated(self) -> None:
        names = rules_files(self.root, self.cfg(OPENCODE_CODEX))
        self.assertEqual(["AGENTS.md"], names)

    def test_codex_alone_still_needs_agents_md(self) -> None:
        self.assertEqual(["AGENTS.md"], rules_files(self.root, self.cfg(CODEX_ONLY)))

    def test_claude_and_codex_need_both_files(self) -> None:
        names = rules_files(self.root, self.cfg(CLAUDE_CODEX))
        self.assertEqual({"CLAUDE.md", "AGENTS.md"}, set(names))
        self.assertEqual(len(names), len(set(names)))

    def test_rules_section_deployed_once(self) -> None:
        """Секция правил в общем файле — одна, а не приклеенная дважды."""
        self.deploy(OPENCODE_CODEX)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("# TASK MANAGEMENT"))


class DetectHarnessesTest(_Project):
    """Предзаполнение диалога выбора сред."""

    def test_codex_folder_names_codex(self) -> None:
        (self.root / ".codex").mkdir(parents=True)
        found = detect_harnesses(self.root)
        self.assertTrue(found["codex"])
        self.assertFalse(found["claude"])
        self.assertFalse(found["opencode"])

    def test_agents_md_prefills_both_readers(self) -> None:
        """По `AGENTS.md` среды не различить — предлагаем обе, лишнюю снимут."""
        self.root.mkdir(parents=True)
        (self.root / "AGENTS.md").write_text("правила", encoding="utf-8")
        found = detect_harnesses(self.root)
        self.assertTrue(found["opencode"])
        self.assertTrue(found["codex"])
        self.assertFalse(found["claude"])


class SkillsLayoutTest(_Project):
    """Одна копия там, где среды читают общий каталог; две — где нет."""

    def _skill_paths(self, harnesses: dict) -> list[Path]:
        return [path for _name, path, _text
                in part_targets(self.root, "skills", self.cfg(harnesses))]

    def test_codex_alone_uses_its_own_folder(self) -> None:
        paths = self._skill_paths(CODEX_ONLY)
        self.assertTrue(paths)
        for path in paths:
            self.assertIn(".codex", path.parts)

    def test_claude_alone_is_unchanged(self) -> None:
        """Проекты без Codex раскладку не меняют — иначе слепки теряют предка."""
        for path in self._skill_paths(CLAUDE_ONLY):
            self.assertIn(".claude", path.parts)

    def test_claude_with_codex_gets_two_copies(self) -> None:
        targets = part_targets(self.root, "skills", self.cfg(CLAUDE_CODEX))
        claude = [p for _n, p, _t in targets if ".claude" in p.parts]
        codex = [p for _n, p, _t in targets if ".codex" in p.parts]
        self.assertTrue(claude)
        self.assertEqual(len(claude), len(codex))

    def test_second_copy_names_are_prefixed(self) -> None:
        """Имена основной копии не меняются, второй — различимы."""
        names = [n for n, _p, _t in part_targets(self.root, "skills",
                                                 self.cfg(CLAUDE_CODEX))]
        self.assertIn("start-task", names)
        self.assertIn("codex/start-task", names)
        self.assertEqual(len(names), len(set(names)))

    def test_deploy_puts_skills_in_both_folders(self) -> None:
        self.deploy(CLAUDE_CODEX)
        self.assertTrue((self.root / ".claude" / "skills" / "start-task"
                         / "SKILL.md").is_file())
        self.assertTrue((self.root / ".codex" / "skills" / "start-task"
                         / "SKILL.md").is_file())

    def test_codex_folder_is_ignored_by_git(self) -> None:
        """Развёрнутое не течёт в репозиторий пользователя."""
        self.deploy(CODEX_ONLY)
        self.assertTrue((self.root / ".codex" / ".gitignore").is_file())


class CodexHooksTest(_Project):
    """Codex получает общий work-hint и свой хук запроса разрешения."""

    def registration(self) -> dict:
        return json.loads((self.root / CODEX_HOOKS).read_text(encoding="utf-8"))

    def test_handler_and_registration_deployed(self) -> None:
        self.deploy(CODEX_ONLY)
        self.assertTrue((self.root / ".codex" / "hooks"
                         / "work-hint.py").is_file())
        self.assertTrue((self.root / ".codex" / "hooks"
                         / "permission-notify.py").is_file())
        self.assertTrue(hook_registered(self.root, "codex"))

    def test_permission_hook_registered_only_for_codex(self) -> None:
        self.deploy(CODEX_ONLY)
        entry = self.registration()["hooks"]["PermissionRequest"][0]

        self.assertEqual("*", entry["matcher"])
        handler = entry["hooks"][0]
        self.assertIn(".codex/hooks/permission-notify.py", handler["command"])
        self.assertTrue(handler["async"], "уведомление не должно задерживать диалог")

        claude_root = Path(self._tmp.name) / "claude"
        claude_tasks = claude_root / "tasks"
        scaffold_project(claude_tasks, self.cfg(CLAUDE_ONLY),
                         {"harnesses": CLAUDE_ONLY})
        settings = json.loads((claude_root / ".claude" / "settings.json")
                              .read_text(encoding="utf-8"))
        self.assertNotIn("PermissionRequest", settings["hooks"])
        self.assertFalse((claude_root / ".claude" / "hooks"
                          / "permission-notify.py").exists())

    def test_missing_permission_registration_is_reported_and_restored(self) -> None:
        self.deploy(CODEX_ONLY)
        data = self.registration()
        data["hooks"].pop("PermissionRequest")
        (self.root / CODEX_HOOKS).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

        self.assertFalse(hook_registered(self.root, "codex"))
        self.assertEqual([CODEX_HOOKS],
                         hooks_unregistered(self.root, self.cfg(CODEX_ONLY)))

        register_hook(self.root, self.cfg(CODEX_ONLY))

        self.assertTrue(hook_registered(self.root, "codex"))
        self.assertIn("PermissionRequest", self.registration()["hooks"])

    def test_missing_permission_handler_gets_banner_and_button_restores_all(self) -> None:
        self.deploy(CODEX_ONLY)
        handler = self.root / ".codex" / "hooks" / "permission-notify.py"
        handler.unlink()

        issues = validate_project(self.tasks, self.cfg(CODEX_ONLY))["degraded"]
        missing = [i for i in issues if i["code"] == "no_hooks"]
        self.assertTrue(missing)
        self.assertIn("codex/permission-notify.py", missing[0]["names"])

        # Кнопка «Развернуть» у части hooks восстанавливает и файл, и ссылку:
        # у старого проекта отсутствуют оба конца новой поставки.
        data = self.registration()
        data["hooks"].pop("PermissionRequest")
        (self.root / CODEX_HOOKS).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        scaffold_project(self.tasks, self.cfg(CODEX_ONLY), {"parts": ["hooks"]})

        self.assertTrue(handler.is_file())
        self.assertTrue(hook_registered(self.root, "codex"))
        codes = [i["code"] for i in
                 validate_project(self.tasks, self.cfg(CODEX_ONLY))["degraded"]]
        self.assertNotIn("no_hooks", codes)
        self.assertNotIn("no_hook_registration", codes)

    def test_command_path_is_relative(self) -> None:
        """`CLAUDE_PROJECT_DIR` у Codex нет, зато хук стартует из корня проекта."""
        self.deploy(CODEX_ONLY)
        entry = self.registration()["hooks"]["PostToolUse"][0]
        command = entry["hooks"][0]["command"]
        self.assertIn(".codex/hooks/work-hint.py", command)
        self.assertNotIn("CLAUDE_PROJECT_DIR", command)

    def test_own_hooks_survive(self) -> None:
        """Файл принадлежит пользователю: чужие записи остаются дословно."""
        path = self.root / CODEX_HOOKS
        path.parent.mkdir(parents=True, exist_ok=True)
        mine = {"matcher": "Bash",
                "hooks": [{"type": "command", "command": "echo своё"}]}
        path.write_text(json.dumps({"hooks": {"PostToolUse": [mine]}},
                                   ensure_ascii=False), encoding="utf-8")

        self.deploy(CODEX_ONLY)

        entries = self.registration()["hooks"]["PostToolUse"]
        self.assertIn(mine, entries)
        self.assertTrue(hook_registered(self.root, "codex"))

    def test_missing_registration_is_reported_per_harness(self) -> None:
        """Долг называет файл той среды, где записи нет."""
        self.assertEqual([CODEX_HOOKS],
                         hooks_unregistered(self.root, self.cfg(CODEX_ONLY)))

    def test_opencode_needs_no_registration(self) -> None:
        """Плагин opencode среда подхватывает из папки сама."""
        harnesses = {"claude": False, "opencode": True, "codex": False}
        self.assertEqual([], hooks_unregistered(self.root, self.cfg(harnesses)))


class PermissionNotifyHookTest(_Project):
    """Хук зовёт существующий скрипт и не решает запрос за человека."""

    def hook(self) -> Path:
        return (Path(__file__).resolve().parent.parent / "templates" / "agentic"
                / ".codex" / "hooks" / "permission-notify.py")

    def call(self, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.hook())], input=json.dumps(event),
            capture_output=True, text=True, encoding="utf-8", timeout=10)

    def test_permission_request_notifies_without_leaking_command(self) -> None:
        self.tasks.mkdir(parents=True)
        called = self.tasks / "called.json"
        (self.tasks / "notify.py").write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "Path(__file__).with_name('called.json').write_text("
            "json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')\n",
            encoding="utf-8")
        secret = "команда-с-секретом"

        done = self.call({
            "hook_event_name": "PermissionRequest",
            "cwd": str(self.root),
            "tool_name": "Bash",
            "tool_input": {"command": secret, "description": "нужно разрешение"},
        })

        self.assertEqual(0, done.returncode, done.stderr)
        args = json.loads(called.read_text(encoding="utf-8"))
        self.assertIn("--agent", args)
        self.assertIn("Codex", args)
        self.assertIn("--level", args)
        self.assertIn("warning", args)
        self.assertNotIn(secret, json.dumps(args, ensure_ascii=False))
        self.assertEqual("", done.stdout)

    def test_missing_notify_script_is_silent(self) -> None:
        self.root.mkdir(parents=True)

        done = self.call({"hook_event_name": "PermissionRequest", "cwd": str(self.root)})

        self.assertEqual(0, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertEqual("", done.stderr)


if __name__ == "__main__":
    unittest.main()
