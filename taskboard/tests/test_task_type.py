"""Тесты типа задачи (TASK-054).

Тип выбирали при заведении, а он не оставлял следа: влиял только на состав
чеклиста и исчезал. Ни доска, ни скрипт, ни будущий механизм требований этапа
о типе не знали, а перечень не покрывал ни обсуждений, ни дизайна.

Теперь тип живёт во frontmatter (`type:`), меняется скриптом и виден на доске.

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

from backend.config import DEFAULTS, TASK_TYPES  # noqa: E402
from backend.scaffold import TASKS_TEMPLATES, scaffold_project  # noqa: E402
from backend.task_parser import (annotate_marks, parse_frontmatter,  # noqa: E402
                                 set_task_type)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "src"


def _frontmatter(path: Path) -> dict:
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return meta


class ProjectCase(unittest.TestCase):
    """Временный проект со развёрнутой структурой tasks/."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)
        self.cfg["harnesses"] = {"claude": True, "opencode": True}
        scaffold_project(self.tasks_dir, self.cfg, {"harnesses": self.cfg["harnesses"]})

    def create(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks_dir / "create_task.py"),
             "-t", "Проверка типа", "-d", "описание", "-c", "критерии", *args],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))

    def status(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.tasks_dir / "set_status.py"), *args],
            capture_output=True, text=True, encoding="utf-8", cwd=str(self.root))

    def created_task(self) -> Path:
        return next(self.tasks_dir.glob("TASK-*.md"))


class TaskTypeStoredTest(ProjectCase):
    """Тип обязан остаться в файле: иначе о нём никто не узнает."""

    def test_template_declares_type(self) -> None:
        text = (TASKS_TEMPLATES / "_TEMPLATE.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^type: ", "в эталоне задачи нет поля type")

    def test_type_written_to_frontmatter(self) -> None:
        self.assertEqual(self.create("--type", "bug").returncode, 0)
        self.assertEqual(_frontmatter(self.created_task()).get("type"), "bug")

    def test_default_type_is_feature(self) -> None:
        self.assertEqual(self.create().returncode, 0)
        self.assertEqual(_frontmatter(self.created_task()).get("type"), "feature",
                         "тип по умолчанию должен быть записан явно")

    def test_new_types_accepted(self) -> None:
        """Обсуждение и дизайн — те самые непокрытые варианты."""
        for value in ("discussion", "design"):
            with self.subTest(value=value):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                root, tasks_dir = Path(tmp.name) / "p", None
                tasks_dir = root / "tasks"
                scaffold_project(tasks_dir, self.cfg, {"harnesses": self.cfg["harnesses"]})
                result = subprocess.run(
                    [sys.executable, str(tasks_dir / "create_task.py"),
                     "-t", "Проверка", "-d", "описание", "--type", value],
                    capture_output=True, text=True, encoding="utf-8", cwd=str(root))
                self.assertEqual(result.returncode, 0, result.stderr)
                created = next(tasks_dir.glob("TASK-*.md"))
                self.assertEqual(_frontmatter(created).get("type"), value)

    def test_type_no_longer_brings_a_checklist(self) -> None:
        """Тип больше не тащит за собой чеклист (TASK-146).

        Шаблонный список пунктов ставился по типу и почти никогда не описывал
        предстоящую работу: у обсуждения он требовал тестов, которых не будет,
        а закрывался всё равно — галочками в конце. Тип оставляет за собой
        метку на доске и исключения в требованиях.
        """
        for value in ("feature", "discussion"):
            with self.subTest(type=value):
                self.setUp()
                self.assertEqual(self.create("--type", value).returncode, 0)
                text = self.created_task().read_text(encoding="utf-8")

                self.assertNotIn("## Чеклист", text,
                                 "новая задача всё ещё приходит с чеклистом")
                self.assertNotIn("- [ ]", text, "в новой задаче есть чекбоксы")

    def test_unknown_type_rejected(self) -> None:
        result = self.create("--type", "nonsense")
        self.assertNotEqual(result.returncode, 0,
                            "неизвестный тип принят молча")


class TaskTypeChangeTest(ProjectCase):
    """Тип правится скриптом: поставленный мимо не должен требовать редактора."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.create("--type", "feature").returncode, 0)
        self.task_id = _frontmatter(self.created_task())["id"]

    def test_type_changed_by_script(self) -> None:
        result = self.status(self.task_id, "--type", "discussion")
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = _frontmatter(self.created_task())
        self.assertEqual(meta.get("type"), "discussion")
        self.assertEqual(meta.get("status"), "backlog",
                         "смена типа не должна двигать задачу по маршруту")

    def test_unknown_type_rejected_by_script(self) -> None:
        result = self.status(self.task_id, "--type", "nonsense")
        self.assertNotEqual(result.returncode, 0, "скрипт принял неизвестный тип")
        self.assertEqual(_frontmatter(self.created_task()).get("type"), "feature")

    def test_types_listed_by_script(self) -> None:
        """Список типов спрашивают у скрипта, а не помнят наизусть."""
        result = self.status("--types")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual([t["key"] for t in data["types"]], list(TASK_TYPES))


class BoardAnnotationTest(unittest.TestCase):
    """Тип приезжает на доску: в строке board.md его нет."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name)

    def _write(self, name: str, body: str) -> None:
        (self.tasks_dir / name).write_text(body, encoding="utf-8")

    def test_type_annotated_from_frontmatter(self) -> None:
        self._write("TASK-001-a.md", "---\nid: TASK-001\ntype: bug\n---\n")
        board = {"columns": [{"groups": [{"tasks": [
            {"id": "TASK-001", "file": "TASK-001-a.md"}]}]}]}
        annotate_marks(self.tasks_dir, board)
        self.assertEqual(board["columns"][0]["groups"][0]["tasks"][0]["type"], "bug")

    def test_task_without_type_stays_clean(self) -> None:
        """Задача, заведённая до появления поля, метки не получает."""
        self._write("TASK-002-b.md", "---\nid: TASK-002\n---\n")
        board = {"columns": [{"groups": [{"tasks": [
            {"id": "TASK-002", "file": "TASK-002-b.md"}]}]}]}
        annotate_marks(self.tasks_dir, board)
        self.assertNotIn("type", board["columns"][0]["groups"][0]["tasks"][0])

    def test_unknown_type_ignored(self) -> None:
        """Чужое значение в поле не должно рисовать пустой кружок."""
        self._write("TASK-003-c.md", "---\nid: TASK-003\ntype: whatever\n---\n")
        board = {"columns": [{"groups": [{"tasks": [
            {"id": "TASK-003", "file": "TASK-003-c.md"}]}]}]}
        annotate_marks(self.tasks_dir, board)
        self.assertNotIn("type", board["columns"][0]["groups"][0]["tasks"][0])


class TypeCatalogTest(unittest.TestCase):
    """Один список типов на бэкенд, скрипты и фронт — иначе разъедутся."""

    def test_backend_catalog_covers_all(self) -> None:
        self.assertEqual(set(TASK_TYPES),
                         {"feature", "bug", "refactor", "cleanup", "discussion",
                          "design", "review"})

    def test_commitless_types_marked(self) -> None:
        """Тип, у которого коммитов не бывает, назван в каталоге (TASK-152).

        Хранится **исключение** (`commits: False`), а не белый список: новый тип
        поставки по умолчанию коммиты даёт, и молчаливо выпасть из напоминания
        не может.
        """
        commitless = {k for k, m in TASK_TYPES.items() if not m.get("commits", True)}
        self.assertEqual({"discussion", "review"}, commitless)

    def test_scripts_know_same_types(self) -> None:
        for name in ("create_task.py", "set_status.py"):
            with self.subTest(script=name):
                text = (TASKS_TEMPLATES / name).read_text(encoding="utf-8")
                for key in TASK_TYPES:
                    self.assertIn(key, text, f"{name} не знает тип {key}")

    def test_script_marks_same_commitless_types(self) -> None:
        """Скрипт автономен и держит свой каталог — пометка обязана совпасть.

        Разъезд тихий: напоминание про пустую «Историю коммитов» печатает
        скрипт, и знай он про типы иначе — карточка и консоль разошлись бы.
        """
        from tests.test_set_status_script import load_script

        script = load_script()
        backend = {k for k, m in TASK_TYPES.items() if not m.get("commits", True)}
        in_script = {k for k, m in script.TASK_TYPES.items()
                     if not m.get("commits", True)}
        self.assertEqual(backend, in_script)

    def test_types_without_release_tail_marked(self) -> None:
        """Тип называет статусы, которые ему не нужны (TASK-151).

        Как и `commits`, это **исключение**: новый тип поставки идёт маршрутом
        целиком и молча выпасть из него не может.
        """
        skipping = {k for k, m in TASK_TYPES.items() if m.get("skip_statuses")}
        self.assertEqual({"discussion", "review"}, skipping)
        for key in skipping:
            self.assertEqual(["ready_for_release", "release_notes", "to_release",
                              "ready_to_deploy"],
                             list(TASK_TYPES[key]["skip_statuses"]))

    def test_script_skips_same_statuses(self) -> None:
        """Рекомендацию считает скрипт — его каталог обязан совпасть."""
        from tests.test_set_status_script import load_script

        script = load_script()
        backend = {k: list(m.get("skip_statuses") or []) for k, m in TASK_TYPES.items()}
        in_script = {k: list(m.get("skip_statuses") or [])
                     for k, m in script.TASK_TYPES.items()}
        self.assertEqual(backend, in_script)

    def test_skipped_statuses_exist_in_catalog(self) -> None:
        """Пропуск называет статус библиотеки: опечатка не сработала бы молча."""
        from backend.statuses import CATALOG

        for key, meta in TASK_TYPES.items():
            for status in meta.get("skip_statuses") or []:
                self.assertIn(status, CATALOG,
                              f"тип {key} пропускает неизвестный статус {status}")

    def test_frontend_catalog_matches(self) -> None:
        text = (FRONTEND / "taskTypes.js").read_text(encoding="utf-8")
        for key, meta in TASK_TYPES.items():
            self.assertIn(f"{key}:", text, f"фронт не знает тип {key}")
            self.assertIn(meta["label"], text, f"подпись типа {key} разошлась с бэкендом")

    def test_letters_are_unique(self) -> None:
        """Кружок на превью узнаётся по букве — две одинаковые бессмысленны."""
        letters = [meta["letter"] for meta in TASK_TYPES.values()]
        self.assertEqual(len(letters), len(set(letters)), "буквы типов повторяются")

    def test_colors_are_unique_and_distinct(self) -> None:
        """Цвет метки — второй способ её узнать, и соседи по палитре его портят.

        `design` начинал с cyan и сливался с sky у `feature`; пары ниже —
        именно такие соседи, различимые только рядом друг с другом.
        """
        colors = [meta["color"] for meta in TASK_TYPES.values()]
        self.assertEqual(len(colors), len(set(colors)), "цвета типов повторяются")
        for a, b in (("sky", "cyan"), ("cyan", "teal"), ("emerald", "teal"),
                     ("amber", "yellow"), ("violet", "purple"), ("rose", "pink")):
            self.assertFalse({a, b} <= set(colors),
                             f"{a} и {b} — соседи по палитре, метки сольются")

    def test_frontend_colors_match_backend(self) -> None:
        """Классы фронта построены на том же цвете, что назван в каталоге."""
        text = (FRONTEND / "taskTypes.js").read_text(encoding="utf-8")
        for key, meta in TASK_TYPES.items():
            start = text.find(f"{key}: {{")
            self.assertGreater(start, -1, f"в taskTypes.js нет блока типа {key}")
            block = text[start:text.index("},", start)]
            self.assertIn(meta["color"], block,
                          f"цвет типа {key} на фронте не {meta['color']}")


class TypeEditFromUiTest(unittest.TestCase):
    """Тип правится и из окна задачи: клик по метке — выбор из списка."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_dir = Path(self._tmp.name)
        self.path = self.tasks_dir / "TASK-001-a.md"
        self.path.write_text("---\nid: TASK-001\nepic: ~\ntype: feature\n---\n\n## Описание\n",
                             encoding="utf-8")

    def test_type_written(self) -> None:
        result = set_task_type(self.tasks_dir, "TASK-001", "design")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(_frontmatter(self.path).get("type"), "design")

    def test_unknown_type_refused(self) -> None:
        result = set_task_type(self.tasks_dir, "TASK-001", "nonsense")
        self.assertFalse(result.get("ok"), "чужое значение принято")
        self.assertEqual(_frontmatter(self.path).get("type"), "feature",
                         "отказ не должен трогать файл")

    def test_type_added_to_old_task(self) -> None:
        """Задача без поля получает его правкой из окна, а не руками."""
        path = self.tasks_dir / "TASK-002-b.md"
        path.write_text("---\nid: TASK-002\nepic: ~\n---\n", encoding="utf-8")
        self.assertTrue(set_task_type(self.tasks_dir, "TASK-002", "bug").get("ok"))
        self.assertEqual(_frontmatter(path).get("type"), "bug")

    def test_patch_endpoint_knows_type(self) -> None:
        text = (Path(__file__).resolve().parent.parent / "backend" / "app.py").read_text(
            encoding="utf-8")
        self.assertIn("set_task_type", text, "PATCH задачи не умеет менять тип")


class BacklogSectionsTest(ProjectCase):
    """Рубрики бэклога — те же типы задач, а не второй список (TASK-119)."""

    def test_sections_come_from_catalog(self) -> None:
        from backend.scaffold import BACKLOG_SUBSECTIONS
        self.assertEqual(list(BACKLOG_SUBSECTIONS),
                         [meta["section"] for meta in TASK_TYPES.values()],
                         "перечень рубрик разошёлся с каталогом типов")

    def test_every_type_has_its_rubric_on_the_board(self) -> None:
        board = (self.tasks_dir / "board.md").read_text(encoding="utf-8")
        for key, meta in TASK_TYPES.items():
            self.assertIn(f"### {meta['section']}", board,
                          f"развёрнутая доска без рубрики для типа {key}")

    def test_task_lands_in_rubric_of_its_type(self) -> None:
        """Задача любого типа попадает в свою рубрику, а не в конец раздела."""
        for key, meta in TASK_TYPES.items():
            with self.subTest(type=key):
                self.assertEqual(self.create("--type", key).returncode, 0)
                board = (self.tasks_dir / "board.md").read_text(encoding="utf-8")
                after = board[board.index(f"### {meta['section']}"):]
                head = after[:after.index("###", 3)] if "###" in after[3:] else after
                self.assertIn("TASK-", head,
                              f"задача типа {key} не попала в рубрику «{meta['section']}»")

    def test_missing_rubric_is_created(self) -> None:
        """Рубрики типа на доске нет — она заводится, а не «просто в конец».

        «Конец раздела приёма» физически лежит **внутри последнего
        подраздела**: задачи-обсуждения так оказывались в «Дизайне». У досок,
        развёрнутых до появления типа, рубрики нет по построению, поэтому
        случай не редкий, а обычный.
        """
        board_path = self.tasks_dir / "board.md"
        rubric = TASK_TYPES["discussion"]["section"]
        board = board_path.read_text(encoding="utf-8")
        board_path.write_text(board.replace(f"### {rubric}\n\n_(нет)_\n", ""),
                              encoding="utf-8")
        self.assertEqual(self.create("--type", "discussion").returncode, 0)

        board = board_path.read_text(encoding="utf-8")
        self.assertIn(f"### {rubric}", board, "рубрика не заведена")
        after = board[board.index(f"### {rubric}"):]
        head = after[:after.index("\n## ")] if "\n## " in after else after
        self.assertIn("TASK-", head, "задача легла не в свою рубрику")
        design = board[board.index(f"### {TASK_TYPES['design']['section']}"):
                       board.index(f"### {rubric}")]
        self.assertNotIn("TASK-", design, "задача-обсуждение снова попала в «Дизайн»")

    def test_board_without_rubrics_at_all(self) -> None:
        """Подразделов нет вовсе (человек их снёс) — навязывать не начинаем."""
        board_path = self.tasks_dir / "board.md"
        board = board_path.read_text(encoding="utf-8")
        lines = [ln for ln in board.splitlines(keepends=True) if not ln.startswith("### ")]
        board_path.write_text("".join(lines), encoding="utf-8")
        result = self.create("--type", "bug")
        self.assertEqual(result.returncode, 0, result.stderr)
        board = board_path.read_text(encoding="utf-8")
        self.assertNotIn("### ", board, "на доске без рубрик появился подраздел")
        self.assertIn("TASK-", board)


class BacklogSectionFieldGoneTest(unittest.TestCase):
    """Раздел определяется типом, поэтому отдельного поля в форме нет (TASK-124)."""

    def test_form_has_no_section_field(self) -> None:
        text = (FRONTEND / "components" / "NewTaskModal.jsx").read_text(encoding="utf-8")
        self.assertNotIn("Раздел бэклога", text,
                         "в форме осталось поле раздела — оно спорит с типом")
        self.assertNotIn("backlogSections", text,
                         "форма всё ещё принимает список подразделов доски")

    def test_api_does_not_take_section(self) -> None:
        text = (Path(__file__).resolve().parent.parent / "backend" / "app.py").read_text(
            encoding="utf-8")
        model = text[text.index("class TaskIn"):text.index("class TaskUpdateIn")]
        self.assertNotIn("section", model, "API создания задачи всё ещё принимает раздел")

    def test_runner_does_not_pass_section(self) -> None:
        text = (Path(__file__).resolve().parent.parent / "backend"
                / "create_task_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("--section", text, "раннер всё ещё передаёт раздел скрипту")


class TypeUiTest(unittest.TestCase):
    """Тип видно там, где на него смотрят: превью и окно задачи."""

    def test_card_shows_type_mark(self) -> None:
        text = (FRONTEND / "components" / "TaskCard.jsx").read_text(encoding="utf-8")
        self.assertIn("taskTypes", text, "превью не знает о типах")
        self.assertIn("letter", text, "на превью нет буквы типа")

    def test_modal_shows_type_label(self) -> None:
        text = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("taskTypes", text, "в окне задачи не показан тип")
        self.assertIn("label", text, "в окне задачи нет названия типа")

    def test_modal_edits_type_by_click(self) -> None:
        """Метка кликабельна: список цветных меток, выбор ставит тип."""
        text = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("TASK_TYPES", text, "в окне нет списка типов для выбора")
        self.assertIn("typePicker", text, "метка не открывает выбор типа")
        self.assertRegex(text, r"updateTask\(taskId, \{ type",
                         "выбор типа не сохраняется")

    def test_type_picker_closes_on_outside_click(self) -> None:
        """Передумал — клик мимо списка закрывает его, ничего не меняя.

        Ловит клик подложка под списком, а не слушатель на окне: слушатель на
        `mousedown` успевал закрыть список раньше, чем до фона модалки доходил
        `click`, и фон — уже не видя открытого списка — закрывал задачу целиком.
        Подложка гасит событие у себя, поэтому до фона оно не доходит вовсе.
        """
        text = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        picker = text[text.index("{typePicker && ("):text.index("создана: {task.meta.created")]
        self.assertIn("fixed inset-0", picker, "под списком нет подложки")
        self.assertIn("stopPropagation", picker,
                      "клик по подложке уходит дальше и закрывает окно")

    def test_task_without_type_can_get_one(self) -> None:
        """У задачи без типа метка всё равно есть — иначе её нечем поставить."""
        text = (FRONTEND / "components" / "TaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("без типа", text, "задаче без типа нечего нажать")

    def test_new_task_form_offers_all_types(self) -> None:
        text = (FRONTEND / "components" / "NewTaskModal.jsx").read_text(encoding="utf-8")
        self.assertIn("taskTypes", text, "форма перечисляет типы своим списком")


if __name__ == "__main__":
    unittest.main()
