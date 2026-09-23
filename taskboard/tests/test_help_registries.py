"""Перечни справки сверяются с реестрами кода (TASK-303).

Справка перечисляет то, что в коде живёт реестром: файлы развёртывания,
баннеры, скиллы, хуки, статусы, типы, поля задачи, команды скрипта. Правило
«обнови руководство» ловит прямую правку, но не перечень в соседнем разделе:
добавили `notify.py` или хуки — а список развёртывания и таблица баннеров
остались прежними. Здесь каждый элемент реестра обязан найтись в своём разделе,
а новый элемент без строки в справке роняет тест и называет, чего не хватает.

Служебное, что в справку не идёт, перечислено явно — с причиной, — а не
пропущено молча.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import requirements, scaffold  # noqa: E402
from backend.config import DEFAULTS, TASK_SIZES, TASK_TYPES  # noqa: E402
from backend.statuses import CATALOG  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HELP = ROOT.parent / "docs" / "help"
APP = ROOT / "frontend" / "src" / "App.jsx"
TASKS_TEMPLATES = ROOT / "templates" / "tasks"


def help_text(name: str) -> str:
    return (HELP / name).read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """Раздел от заголовка до следующего заголовка того же или старшего уровня."""
    match = re.search(rf"^(#+) {re.escape(heading)}\s*$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"нет заголовка «{heading}»")
    level = len(match.group(1))
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{level}}} ", rest, re.MULTILINE)
    return rest[:end.start()] if end else rest


def table_rows(text: str) -> list[list[str]]:
    """Строки таблиц раздела ячейками, без шапки и разделителя."""
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows


def code_names(cell: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", cell))


def deployed_name(template: Path) -> str:
    """Имя файла шаблона `tasks/` так, как оно лежит в проекте."""
    return ".gitignore" if template.name == "gitignore_template" else template.name


def tasks_template_files() -> list[str]:
    return sorted(deployed_name(p) for p in TASKS_TEMPLATES.iterdir() if p.is_file())


# Как часть поставки названа в списке развёртывания (`01-start`). Карта явная:
# часть называется в коде ключом, а в справке — по-человечески, и новая часть
# без записи здесь тоже роняет тест
DEPLOY_WORDING = {
    "create_script": "`tasks/create_task.py`",
    "status_script": "`tasks/set_status.py`",
    "notify_script": "`tasks/notify.py`",
    "template": "`tasks/_TEMPLATE.md`",
    "epics": "`tasks/epics.md`",
    "logs": "`tasks/logs/`",
    "skills": "скиллы",
    "commands": "команды opencode",
    "rules": "`CLAUDE.md` / `AGENTS.md`",
    "hooks": "хуки",
    "hook_registration": "их подключение в настройках среды",
    "vault": "`vault/SYS/`",
}


class DeployListTest(unittest.TestCase):
    """«Развернуть структуру» называет всё, что разворачивается."""

    def setUp(self) -> None:
        self.text = section(help_text("01-start.md"), "Развернуть структуру")

    def test_every_env_part_is_named(self) -> None:
        parts = {spec["part"] for spec in scaffold.ENV_PARTS}
        self.assertEqual(set(DEPLOY_WORDING), parts,
                         "карта DEPLOY_WORDING разошлась с ENV_PARTS: "
                         "новую часть впишите в карту и в справку")
        for part, wording in DEPLOY_WORDING.items():
            with self.subTest(part=part):
                self.assertIn(wording, self.text,
                              f"01-start «Развернуть структуру» не называет {part}")

    def test_every_tasks_file_is_named(self) -> None:
        for name in tasks_template_files():
            with self.subTest(file=name):
                self.assertIn(f"`tasks/{name}`", self.text,
                              f"01-start «Развернуть структуру» не называет tasks/{name}")


class TasksTreeTest(unittest.TestCase):
    """Дерево `tasks/` в `03-structure` полно."""

    def setUp(self) -> None:
        text = help_text("03-structure.md")
        self.tree = re.search(r"```\n(tasks/.*?)```", text, re.DOTALL).group(1)
        self.names = {line.split()[0] for line in self.tree.splitlines()[1:] if line.strip()}

    def test_every_tasks_file_is_in_tree(self) -> None:
        for name in tasks_template_files():
            with self.subTest(file=name):
                self.assertIn(name, self.names, f"дерева tasks/ нет {name}")

    def test_logs_dir_is_in_tree(self) -> None:
        self.assertIn(f"{DEFAULTS['logs_dir']}/", self.names)


def degraded_fixes() -> dict[str, str]:
    """Коды баннера из `DEGRADED_FIX` фронтенда → подпись кнопки."""
    src = APP.read_text(encoding="utf-8")
    start = src.index("const DEGRADED_FIX = {")
    block = src[start:src.index("\n  }\n", start)]
    fixes = {}
    for code, body in re.findall(r"^\s*(\w+):\s*\{([^}]*)\}", block, re.MULTILINE):
        fixes[code] = re.search(r"label:\s*'([^']+)'", body).group(1)
    return fixes


# Код баннера → как строка названа в таблице `06-validation`. Несколько кодов
# делят одну строку: справке всё равно, какой скрипт устарел
BANNER_ROWS = {
    "no_create_script": "Нет `create_task.py`",
    "no_status_script": "Нет `set_status.py`",
    "no_notify_script": "Нет `notify.py`",
    "no_epics": "Нет `epics.md`",
    "no_template": "Нет `_TEMPLATE.md`",
    "no_logs": "Нет папки логов",
    "no_board_sections": "Нет разделов доски",
    "outdated_script": "Скрипт или `_TEMPLATE.md` устарел",
    "outdated_status_script": "Скрипт или `_TEMPLATE.md` устарел",
    "outdated_notify_script": "Скрипт или `_TEMPLATE.md` устарел",
    "outdated_template": "Скрипт или `_TEMPLATE.md` устарел",
    "no_rules": "Скиллы, команды, правила или хуки не развёрнуты",
    "no_skills": "Скиллы, команды, правила или хуки не развёрнуты",
    "no_commands": "Скиллы, команды, правила или хуки не развёрнуты",
    "no_hooks": "Скиллы, команды, правила или хуки не развёрнуты",
    "no_hook_registration": "Хук не подключён",
    "outdated_skills": "Скиллы, команды, правила, хуки или файлы волта отличаются",
    "outdated_commands": "Скиллы, команды, правила, хуки или файлы волта отличаются",
    "outdated_hooks": "Скиллы, команды, правила, хуки или файлы волта отличаются",
    "outdated_rules": "Скиллы, команды, правила, хуки или файлы волта отличаются",
    "outdated_vault": "Скиллы, команды, правила, хуки или файлы волта отличаются",
    "no_harness_choice": "Не выбраны среды агентов",
    "no_vault": "Волт не развёрнут",
    "extra_skills": "Остались скиллы или команды выключенной возможности",
    "extra_commands": "Остались скиллы или команды выключенной возможности",
    "extra_rules": "старая секция Task Management",
    "requires_unsupported": "не знает о требованиях этапов",
    "requires_exceptions_stale": "Рекомендованные требования не учитывают",
    "requires_types_unreviewed": "Ваши требования не настроены",
}


class BannerTableTest(unittest.TestCase):
    """Таблица жёлтого баннера знает каждый код и его кнопку."""

    def setUp(self) -> None:
        text = section(help_text("06-validation.md"),
                       "Жёлтый баннер — часть возможностей отключена")
        self.rows = table_rows(text)[1:]
        self.fixes = degraded_fixes()

    def test_map_covers_every_code(self) -> None:
        self.assertTrue(self.fixes, "не удалось прочитать DEGRADED_FIX")
        self.assertEqual(set(BANNER_ROWS), set(self.fixes),
                         "карта BANNER_ROWS разошлась с DEGRADED_FIX: новый код "
                         "впишите в карту и строкой в таблицу 06-validation")

    def test_every_env_code_has_a_fix(self) -> None:
        """Код, который рождает поставка, без кнопки на баннере повиснет строкой."""
        for spec in scaffold.ENV_PARTS:
            for state in ("missing", "outdated", "extra"):
                code = spec.get(state)
                if code:
                    with self.subTest(code=code):
                        self.assertIn(code, self.fixes)

    def test_rows_name_the_button(self) -> None:
        for code, phrase in BANNER_ROWS.items():
            with self.subTest(code=code):
                rows = [r for r in self.rows if phrase in r[0]]
                self.assertEqual(len(rows), 1,
                                 f"06-validation: строка «{phrase}» не найдена "
                                 f"или не единственна")
                self.assertEqual(rows[0][1], self.fixes.get(code),
                                 f"06-validation: у «{phrase}» не та кнопка")


class SkillsTableTest(unittest.TestCase):
    """Таблица скиллов `05-agentic` совпадает с поставкой."""

    def test_table_matches_templates(self) -> None:
        text = section(help_text("05-agentic.md"), "Скиллы")
        listed = set().union(*(code_names(r[0]) for r in table_rows(text)[1:]))
        shipped = {p.name for p in scaffold.SKILLS_TEMPLATES.iterdir() if p.is_dir()}
        self.assertEqual(shipped - listed, set(), "скиллы без строки в 05-agentic")
        self.assertEqual(listed - shipped, set(), "в 05-agentic скиллы, которых нет")


class HooksTableTest(unittest.TestCase):
    """Таблица хуков `05-agentic`: каждый обработчик, его файл и события."""

    def setUp(self) -> None:
        self.rows = table_rows(section(help_text("05-agentic.md"), "Хуки"))[1:]

    def row_for(self, path: str) -> list[str]:
        rows = [r for r in self.rows if f"`{path}`" in r[1]]
        self.assertEqual(len(rows), 1, f"05-agentic «Хуки»: нет строки для {path}")
        return rows[0]

    def test_every_handler_is_listed(self) -> None:
        for harness, folder in scaffold.HOOK_TEMPLATES.items():
            rel = folder.relative_to(scaffold.AGENTIC_TEMPLATES).as_posix()
            for handler in sorted(folder.glob("*.*")):
                path = f"{rel}/{handler.name}"
                with self.subTest(handler=path):
                    row = self.row_for(path)
                    registered = [spec["event"]
                                  for spec in scaffold.HOOK_REGISTRATIONS.get(harness, ())
                                  if spec["script"] == handler.name]
                    if registered:
                        self.assertIn(scaffold.HOOK_REGISTRATION_FILE[harness], row[2])
                    for event in registered:
                        self.assertIn(f"`{event}`", row[2],
                                      f"{path}: событие {event} не названо")

    def test_no_stale_rows(self) -> None:
        shipped = {f"{folder.relative_to(scaffold.AGENTIC_TEMPLATES).as_posix()}/{p.name}"
                   for folder in scaffold.HOOK_TEMPLATES.values()
                   for p in folder.glob("*.*")}
        listed = set().union(*(code_names(r[1]) for r in self.rows))
        self.assertEqual(listed - shipped, set(), "в 05-agentic хуки, которых нет")


class StatusSetTest(unittest.TestCase):
    """«Как собрать пайплайн» перечисляет весь набор статусов."""

    def test_every_catalog_status_is_named(self) -> None:
        text = section(help_text("04-lifecycle.md"), "Как собрать пайплайн")
        for key in CATALOG:
            with self.subTest(status=key):
                self.assertIn(f"`{key}`", text, f"04-lifecycle не называет статус {key}")


def field_rows() -> dict[str, str]:
    """Таблица «Поля задачи»: поле → описание и способ правки."""
    text = section(help_text("03-structure.md"), "Поля задачи")
    rows = {}
    for cells in table_rows(text)[1:]:
        for name in code_names(cells[0]):
            rows[name] = " | ".join(cells[1:])
    return rows


def template_fields() -> list[str]:
    text = (TASKS_TEMPLATES / "_TEMPLATE.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    return re.findall(r"^(\w+):", front, re.MULTILINE)


# Поля, которых нет в шаблоне: их дописывают скрипт и доска по ходу работы
WRITTEN_FIELDS = (requirements.ASSIGNEE_FIELD, "cancel_reason",
                  requirements.CONFIRMED_FIELD, requirements.WAIVED_FIELD)


class TaskFieldsTest(unittest.TestCase):
    """Таблица полей задачи `03-structure` — все поля и все значения."""

    def test_every_field_has_a_row(self) -> None:
        rows = field_rows()
        for name in (*template_fields(), *WRITTEN_FIELDS):
            with self.subTest(field=name):
                self.assertIn(name, rows, f"03-structure: нет поля {name}")

    def test_no_stale_rows(self) -> None:
        known = {*template_fields(), *WRITTEN_FIELDS}
        self.assertEqual(set(field_rows()) - known, set(),
                         "03-structure: поля, которых задача не знает")

    def test_every_type_is_named(self) -> None:
        row = field_rows()["type"]
        for key in TASK_TYPES:
            with self.subTest(type=key):
                self.assertIn(f"`{key}`", row, f"03-structure: нет типа {key}")

    def test_every_size_is_named(self) -> None:
        row = field_rows()["size"]
        for key in TASK_SIZES:
            with self.subTest(size=key):
                self.assertIn(f"`{key}`", row, f"03-structure: нет размера {key}")


# Флаги, которыми пользуются только скиллы и хуки: человек их не набирает и
# агенту о них говорят тексты скиллов, а не справка
AGENT_ONLY_FLAGS = {
    "--work-hint": "зовёт хук подсказки при коммите",
    "--handoff-write": "ведёт скилл write-handoff",
    "--handoff-read": "ведёт скилл read-handoff",
    "--handoff-clear": "ведёт скилл read-handoff",
    "--harness": "параметр буфера перехода, ставит скилл",
    "--tasks": "параметр буфера перехода, ставит скилл",
    "--tasks-dir": "запуск скрипта не из корня проекта; скиллы зовут его из корня",
}


def status_script_flags() -> list[str]:
    src = (TASKS_TEMPLATES / "set_status.py").read_text(encoding="utf-8")
    return re.findall(r'add_argument\("(--[\w-]+)"', src)


class StatusScriptFlagsTest(unittest.TestCase):
    """Каждый флаг `set_status.py` назван в справке или исключён с причиной."""

    def test_flags_are_documented(self) -> None:
        flags = status_script_flags()
        self.assertTrue(flags, "не удалось прочитать флаги set_status.py")
        text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(HELP.glob("*.md")))
        for flag in flags:
            if flag in AGENT_ONLY_FLAGS:
                continue
            with self.subTest(flag=flag):
                # assertTrue, а не assertRegex: тот печатает в отказе всю справку
                found = re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text)
                self.assertTrue(found, f"флаг {flag} не назван в справке — допишите "
                                       f"или внесите в AGENT_ONLY_FLAGS с причиной")

    def test_exceptions_are_real_flags(self) -> None:
        self.assertEqual(set(AGENT_ONLY_FLAGS) - set(status_script_flags()), set(),
                         "в исключениях флаги, которых у скрипта нет")


if __name__ == "__main__":
    unittest.main()
