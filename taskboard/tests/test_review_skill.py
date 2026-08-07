"""Скилл ревью чужой работы (TASK-153).

Решение, по которому он написан, — TASK-159: правила берутся по объявленному
порядку источников, каждое замечание называет свой источник, а выключенный волт
из текста исчезает целиком.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import scaffold_project  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AGENTIC = ROOT / "templates" / "agentic"
SKILL = AGENTIC / ".claude" / "skills" / "review-task" / "SKILL.md"
WRAPPER = AGENTIC / ".opencode" / "commands" / "review-task.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SkillDeliveryTest(unittest.TestCase):
    """Скилл — часть поставки: шаблон, обёртка и заголовок на месте."""

    def test_skill_exists(self) -> None:
        self.assertTrue(SKILL.exists(), "нет шаблона скилла review-task")

    def test_wrapper_exists_and_calls_skill(self) -> None:
        """Без обёртки скилл недоступен проектам на opencode."""
        self.assertTrue(WRAPPER.exists(), "нет обёртки .opencode для review-task")
        self.assertIn("review-task skill", _text(WRAPPER))

    def test_frontmatter_names_the_skill(self) -> None:
        head = _text(SKILL).split("---")[1]
        self.assertIn("name: review-task", head)
        self.assertIn("argument-hint:", head)

    def test_start_task_hands_review_over(self) -> None:
        """Иначе задачу-ревью поведут по ветке реализации."""
        start = _text(AGENTIC / ".claude" / "skills" / "start-task" / "SKILL.md")
        self.assertIn("review-task TASK-NNN", start,
                      "start-task не говорит, с чем звать review-task")

    def test_handover_comes_after_text_cleanup(self) -> None:
        """Иначе задача-ревью, заведённая через форму, останется неоформленной:
        start-task до оформления не дойдёт, а review-task про него не знает.
        """
        start = _text(AGENTIC / ".claude" / "skills" / "start-task" / "SKILL.md")
        self.assertLess(start.index("### Привести текст задачи в читаемый вид"),
                        start.index("### Тип задачи — `review`?"),
                        "перенаправление стоит раньше оформления текста")


class RuleSourcesTest(unittest.TestCase):
    """Порядок источников и происхождение замечания — суть скилла."""

    def setUp(self) -> None:
        self.text = _text(SKILL)

    def test_rules_file_named(self) -> None:
        self.assertIn("CODE_REVIEW.md", self.text)

    def test_all_source_signatures_present(self) -> None:
        """Подпись источника — закрытый набор: пропуск строки лишает слой голоса."""
        for signature in ("Правила ревью →", "Архитектура проекта →",
                          "Правила проекта →", "Знание проекта →",
                          "Принято в коде", "Общая практика"):
            with self.subTest(signature=signature):
                self.assertIn(signature, self.text)

    def test_sources_are_not_numbered(self) -> None:
        """Нумерованный список ломается, когда волт-слой вырезан.

        «Источник 4» в проекте без волта — битая ссылка на то, чего в тексте
        нет; поэтому источники перечисляются маркерами, а не числами.
        """
        start = self.text.index("Сверху вниз по доверию")
        block = self.text[start:self.text.index("Кодовая база — самый опасный")]
        numbered = [ln for ln in block.splitlines()
                    if ln.strip()[:2] in ("1.", "2.", "3.", "4.", "5.", "6.")]
        self.assertEqual([], numbered, "источники пронумерованы — список порвётся")

    def test_comment_without_source_forbidden(self) -> None:
        self.assertIn("без источника не пишется", self.text)

    def test_address_is_full_path_with_line(self) -> None:
        """Замечание переносят в MR руками: адрес, по которому нельзя ткнуть,
        заставляет искать место заново — и до MR оно не доезжает.
        """
        self.assertIn("Путь полный, от корня репозитория", self.text)
        self.assertIn("Номер строки обязателен", self.text)

    def test_line_numbers_from_reviewed_revision(self) -> None:
        """В чекауте ревьюера строки уже уехали, а комментарий ложится на
        версию автора.
        """
        self.assertIn("по ревьюируемой ревизии", self.text)
        self.assertIn("git show <ревизия>:<путь> | grep -n", self.text)

    def test_quotes_the_line_itself(self) -> None:
        """Номер смещается от соседнего коммита, строка находится поиском."""
        self.assertIn("Строка кода приводится следом", self.text)

    def test_marks_remarks_outside_the_diff(self) -> None:
        """Инлайн-комментария в MR для них нет — человек должен знать заранее."""
        self.assertIn("(вне диффа)", self.text)

    def test_codebase_source_is_qualified(self) -> None:
        """Из кода выводится «так делают везде», а не «так правильно»."""
        self.assertIn("с остальным кодом (N мест)", self.text)


class AnalyzerRunTest(unittest.TestCase):
    """Запуск анализаторов — опция с рамками, а не обязанность."""

    def setUp(self) -> None:
        self.text = _text(SKILL)

    def test_run_is_optional_and_failure_is_not_fatal(self) -> None:
        self.assertIn("ревью не проваливает", self.text)

    def test_asks_before_first_run(self) -> None:
        """Запуск исполняет код ревьюируемой ветки — без спроса нельзя."""
        self.assertIn("Спроси один раз перед первым запуском", self.text)

    def test_never_runs_fix_mode(self) -> None:
        self.assertIn("`--fix` не запускай никогда", self.text)

    def test_does_not_install_dependencies(self) -> None:
        for forbidden in ("composer install", "npm ci"):
            with self.subTest(cmd=forbidden):
                self.assertIn(forbidden, self.text)
        self.assertIn("Только уже установленное", self.text)

    def test_blames_branch_only_against_base(self) -> None:
        """Падение — замечание лишь тогда, когда на базе тот же прогон живёт."""
        self.assertIn("прогони то же на базовой ветке", self.text)

    def test_says_what_was_not_run(self) -> None:
        """Молчание про непройденное читается как «прошло чисто»."""
        self.assertIn("тесты не запускались", self.text)


class SubjectTest(unittest.TestCase):
    """Предмет ревью — из локального git, тремя формами."""

    def setUp(self) -> None:
        self.text = _text(SKILL)

    def test_three_forms_documented(self) -> None:
        for command in ("git log --all --grep=", "git show <хэш>", "git diff <база>...<ветка>"):
            with self.subTest(form=command):
                self.assertIn(command, self.text)

    def test_base_branch_is_computed_not_guessed(self) -> None:
        """`main` и `master` живут оба — имя главной ветки не подставляют."""
        self.assertIn("git merge-base", self.text)

    def test_creates_task_when_missing(self) -> None:
        self.assertIn("create_task.py --type review", self.text)

    def test_task_argument_is_not_the_subject(self) -> None:
        """Передача от start-task приносит номер задачи, а не предмет ревью.

        Без этого случая скилл ищет коммиты по собственному ключу задачи —
        то есть разбирает работу над самим ревью.
        """
        self.assertIn("это **не** предмет", self.text)
        self.assertIn("Предмет ищи в её описании", self.text)

    def test_asks_when_subject_not_found_in_task(self) -> None:
        self.assertIn("не нашёл — спроси", self.text)

    def test_empty_subject_stops_the_review(self) -> None:
        """«Замечаний нет» по пустому диапазону — ложь, а не результат."""
        self.assertIn("Пустой", self.text)

    def test_hands_off_instead_of_moving_status(self) -> None:
        self.assertIn("handoff-task", self.text)

    def test_writes_nothing_to_foreign_repo(self) -> None:
        self.assertIn("В чужой репозиторий скилл ничего не пишет", self.text)


class VaultBlocksTest(unittest.TestCase):
    """Выключенный волт исчезает из скилла целиком, а не «есть, но нельзя»."""

    HARNESSES = {"claude": True, "opencode": False}

    def _deploy(self, vault: bool) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tasks = Path(tmp.name) / "proj" / "tasks"
        cfg = {**DEFAULTS, "vault": vault, "harnesses": self.HARNESSES}
        scaffold_project(tasks, cfg, {"harnesses": self.HARNESSES, "vault": vault,
                                      "skills": True, "rules": True})
        (tasks / ".taskboard.json").write_text(
            json.dumps({"vault": vault}, ensure_ascii=False), encoding="utf-8")
        deployed = tasks.parent / ".claude" / "skills" / "review-task" / "SKILL.md"
        self.assertTrue(deployed.exists(), "скилл не развернулся в проект")
        return deployed.read_text(encoding="utf-8")

    def test_vault_mentioned_when_enabled(self) -> None:
        self.assertIn("олт", self._deploy(vault=True))

    def test_no_trace_of_vault_when_disabled(self) -> None:
        text = self._deploy(vault=False).lower()
        for word in ("волт", "vault", "знание проекта"):
            with self.subTest(word=word):
                self.assertNotIn(word, text)

    def test_step_numbering_survives_the_cut(self) -> None:
        """Вырезание слоя не должно оставить дыру в нумерации шагов."""
        text = self._deploy(vault=False)
        steps = [int(ln.split()[2].rstrip(".")) for ln in text.splitlines()
                 if ln.startswith("## Шаг ")]
        self.assertEqual(list(range(len(steps))), steps)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
