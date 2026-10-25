"""Шапка задачи — валидный YAML: значения с `: ` и служебными символами.

Шапку пишут и читают скрипты проекта (`tasks/*.py`) и бэкенд, а разбирают ещё
и чужие парсеры — GitHub, Obsidian, PyYAML. Проверяется круг целиком: что
записали, то прочли без кавычек, и чужой парсер прочёл то же самое.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend import yaml_scalar
from backend.board_repair import apply_repair, plan_repair
from backend.task_parser import parse_frontmatter, set_meta_fields, set_task_title
from backend.validator import validate_project

try:
    import yaml
except ImportError:  # pragma: no cover — PyYAML в venv не ставится
    yaml = None

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "tasks"

# Значения, на которых промах правдоподобен: двоеточие с пробелом, комментарий,
# служебный символ в начале, кавычки и обратный слэш внутри — и безвредные,
# которые должны остаться без кавычек
TRICKY = ["Ресерч: что-нибудь", "Этап 1: план", "итог:", "фикс #12", "#хештег",
          "- пункт", "[черновик] план", "{x}", "&якорь", "*важно*", "!срочно",
          "| труба", "> цитата", "%процент", "@ник", "`код`", "'в кавычках'",
          '"в кавычках"', '"Foo" bar "baz"', 'C:\\temp: путь', 'и "кавычки": тоже',
          " пробел в начале"]
PLAIN = ["Фильтр по исполнителю", "telegram:123:45", "TASK-001, TASK-002",
         "2026-10-25 02:47", "verified, release_ok", "~", "Сценарии до кода",
         "a:b", "C#", "100%", "e-mail", "путь C:\\temp\\new"]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"_{name}", TEMPLATES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


SET_STATUS = _script("set_status")


def _yaml_load(header: str) -> dict:
    """Шапка глазами чужого парсера."""
    return yaml.safe_load(header) if yaml else {}


def _header(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text[3:text.index("\n---", 3)]


def _board() -> str:
    """Доска из шаблона с разделами по пайплайну — как её создаёт scaffold."""
    from backend.scaffold import render_sections
    from backend.statuses import load_pipeline

    return (TEMPLATES / "board.md").read_text(encoding="utf-8").format(
        sections=render_sections(load_pipeline({})))


def _task(tmp: Path, title: str = "старое", **fields) -> Path:
    path = tmp / "TASK-001-x.md"
    extra = "".join(f"\n{k}: {v}" for k, v in fields.items())
    path.write_text(f"---\nid: TASK-001\ntitle: {title}\nstatus: todo{extra}\n---\n\n"
                    f"## Описание\n\n## Комментарии\n", encoding="utf-8")
    return path


class RuleTest(unittest.TestCase):
    """Кавычки — только там, где без них YAML ломается."""

    def test_tricky_values_round_trip(self) -> None:
        for value in TRICKY:
            written = yaml_scalar.to_yaml(value)
            self.assertNotEqual(written, value, f"не взято в кавычки: {value!r}")
            self.assertEqual(yaml_scalar.from_yaml(written), value)
            if yaml:
                self.assertEqual(_yaml_load(f"title: {written}")["title"], value)

    def test_plain_values_unchanged(self) -> None:
        for value in PLAIN:
            self.assertEqual(yaml_scalar.to_yaml(value), value)
            self.assertEqual(yaml_scalar.from_yaml(value), value)

    def test_old_unquoted_values_read_as_before(self) -> None:
        """Файл, записанный до правила, читается сырым текстом, как раньше."""
        for value in TRICKY:
            if value.strip() != value:
                continue
            if yaml_scalar.is_quoted(value):
                continue
            self.assertEqual(yaml_scalar.from_yaml(value), value)

    def test_broken_quotes_stay_text(self) -> None:
        self.assertEqual(yaml_scalar.from_yaml('"Foo" bar "baz"'), '"Foo" bar "baz"')
        self.assertEqual(yaml_scalar.from_yaml('"обрыв\\"'), '"обрыв\\"')

    def test_header_issues(self) -> None:
        header = "id: TASK-1\ntitle: Ресерч: x\norigin: telegram:1:2\npaused: \"ждём: ответ\""
        self.assertEqual(yaml_scalar.header_issues(header), ["title"])

    @unittest.skipUnless(yaml, "нет PyYAML — проверка чужим парсером пропущена")
    def test_header_issues_match_real_parser(self) -> None:
        """Что правило считает сломанным, то не разбирает PyYAML, и наоборот."""
        for value in TRICKY + PLAIN:
            line = f"title: {value}"
            try:
                ok = _yaml_load(line)["title"] == value.strip() or value == "~"
            except yaml.YAMLError:  # type: ignore[union-attr]
                ok = False
            if yaml_scalar.header_issues(line):
                self.assertFalse(ok and value == value.strip(),
                                 f"правило ругается зря: {value!r}")


class ScriptParityTest(unittest.TestCase):
    """Копия правила в set_status.py ведёт себя так же, как в бэкенде."""

    def test_same_behaviour(self) -> None:
        for value in TRICKY + PLAIN:
            self.assertEqual(SET_STATUS.yaml_needs_quotes(value), yaml_scalar.needs_quotes(value))
            self.assertEqual(SET_STATUS.yaml_value(value), yaml_scalar.to_yaml(value))
            written = yaml_scalar.to_yaml(value)
            self.assertEqual(SET_STATUS.yaml_unquote(written), yaml_scalar.from_yaml(written))
            self.assertEqual(SET_STATUS.yaml_unquote(value), yaml_scalar.from_yaml(value))
            line = f"title: {value}"
            self.assertEqual(SET_STATUS.header_yaml_issues(line), yaml_scalar.header_issues(line))


class WritersTest(unittest.TestCase):
    """Каждый, кто пишет шапку, пишет её валидной и читает обратно без кавычек."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _assert_valid(self, path: Path, field: str, value: str) -> None:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(meta[field], value)
        self.assertEqual(SET_STATUS._read_meta(path)[field], value)
        self.assertEqual(yaml_scalar.header_issues(_header(path)), [])
        if yaml:
            self.assertEqual(_yaml_load(_header(path))[field], value)

    def test_backend_set_meta_fields(self) -> None:
        path = _task(self.tmp)
        set_meta_fields(path, {"assignee": "Иванов: тестировщик"})
        self._assert_valid(path, "assignee", "Иванов: тестировщик")

    def test_backend_rename(self) -> None:
        _task(self.tmp)
        result = set_task_title(self.tmp, "TASK-001", "Этап 1: план")
        self.assertTrue(result["ok"], result)
        self._assert_valid(self.tmp / result["file"], "title", "Этап 1: план")

    def test_script_set_fields(self) -> None:
        path = _task(self.tmp)
        SET_STATUS._set_fields(path, {"paused": 'ждём: ответ "контрагента"'})
        self._assert_valid(path, "paused", 'ждём: ответ "контрагента"')

    def test_script_writes_plain_value_as_before(self) -> None:
        path = _task(self.tmp)
        SET_STATUS._set_fields(path, {"origin": "telegram:123:45"})
        self.assertIn("\norigin: telegram:123:45\n", path.read_text(encoding="utf-8"))

    def test_other_lines_untouched(self) -> None:
        """Запись одного поля не трогает остальные строки шапки, даже сломанные."""
        path = _task(self.tmp, title="Ресерч: старое")
        SET_STATUS._set_fields(path, {"size": "M"})
        set_meta_fields(path, {"type": "bug"})
        self.assertIn("\ntitle: Ресерч: старое\n", path.read_text(encoding="utf-8"))


class CreateTaskTest(unittest.TestCase):
    """create_task.py: новая задача с двоеточием в заголовке — валидная шапка."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir()
        for name in ("create_task.py", "set_status.py", "board.md", "_TEMPLATE.md"):
            shutil.copy(TEMPLATES / name, self.tasks / name)
        (self.tasks / "board.md").write_text(_board(), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _create(self) -> Path:
        done = subprocess.run(
            [sys.executable, str(self.tasks / "create_task.py"), "-t", "Ресерч: что-нибудь",
             "-d", "текст", "--author", "@ник: бот", "--origin", "telegram:1:2"],
            cwd=str(self.tasks.parent), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return next(self.tasks.glob("TASK-001-*.md"))

    def test_new_task_valid(self) -> None:
        path = self._create()
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(meta["title"], "Ресерч: что-нибудь")
        self.assertEqual(meta["author"], "@ник: бот")
        self.assertEqual(yaml_scalar.header_issues(_header(path)), [])
        self.assertIn("\norigin: telegram:1:2\n", path.read_text(encoding="utf-8"))
        if yaml:
            self.assertEqual(_yaml_load(_header(path))["title"], "Ресерч: что-нибудь")

    def test_queue_shows_title_without_quotes(self) -> None:
        self._create()
        done = subprocess.run([sys.executable, str(self.tasks / "set_status.py"), "TASK-001",
                               "queued", "--agent", "тест", "--manual", "тест"],
                              cwd=str(self.tasks.parent), capture_output=True, text=True,
                              encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        queue = subprocess.run([sys.executable, str(self.tasks / "set_status.py"), "--queue"],
                               cwd=str(self.tasks.parent), capture_output=True, text=True,
                               encoding="utf-8")
        titles = [t["title"] for t in json.loads(queue.stdout)["tasks"]]
        self.assertEqual(titles, ["Ресерч: что-нибудь"])

    def test_old_set_status_gets_unquoted(self) -> None:
        """Сосед старый, кавычек снимать не умеет — значение пишется как раньше."""
        script = self.tasks / "set_status.py"
        text = script.read_text(encoding="utf-8")
        script.write_text(text.replace("def yaml_value(", "def _gone_yaml_value("),
                          encoding="utf-8")
        path = self._create()
        self.assertIn("\ntitle: Ресерч: что-нибудь\n", path.read_text(encoding="utf-8"))


class RepairTest(unittest.TestCase):
    """Предупреждение валидатора и «Починить данные» для сломанных шапок."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tasks = Path(self._tmp.name) / "tasks"
        self.tasks.mkdir()
        (self.tasks / "board.md").write_text(_board(), encoding="utf-8")
        _task(self.tasks, title="Ресерч: старое", paused="ждём: ответ")
        (self.tasks / "TASK-002-y.md").write_text(
            "---\nid: TASK-002\ntitle: Обычная\nstatus: todo\norigin: telegram:1:2\n---\n",
            encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plan_names_broken_fields(self) -> None:
        plan = plan_repair(self.tasks, {})
        self.assertEqual(plan["header"],
                         [{"id": "TASK-001", "file": "TASK-001-x.md", "fields": ["title", "paused"]}])

    def test_apply_keeps_values(self) -> None:
        path = self.tasks / "TASK-001-x.md"
        before, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        untouched = (self.tasks / "TASK-002-y.md").read_bytes()
        result = apply_repair(self.tasks, {})
        self.assertEqual(result["headers"], 1)
        after, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(after, before)
        self.assertEqual(yaml_scalar.header_issues(_header(path)), [])
        self.assertEqual((self.tasks / "TASK-002-y.md").read_bytes(), untouched)
        self.assertEqual(plan_repair(self.tasks, {})["header"], [])

    def test_apply_keeps_crlf(self) -> None:
        path = self.tasks / "TASK-001-x.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        apply_repair(self.tasks, {})
        data = path.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn('title: "Ресерч: старое"\r\n'.encode("utf-8"), data)

    def test_validator_warns_and_counts(self) -> None:
        report = validate_project(self.tasks, {})
        lines = [w for w in report["warnings"] if "YAML" in w]
        self.assertEqual(len(lines), 1, report["warnings"])
        self.assertIn("TASK-001", lines[0])
        self.assertNotIn("TASK-002", lines[0])

    def test_status_change_warns_agent(self) -> None:
        warnings = SET_STATUS.check_task_file(self.tasks / "TASK-001-x.md")
        self.assertTrue(any("YAML" in w and "title" in w for w in warnings), warnings)
        self.assertFalse([w for w in SET_STATUS.check_task_file(self.tasks / "TASK-002-y.md")
                          if "YAML" in w])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
