"""Тесты базового слепка окружения и слияния правок с новым шаблоном.

TASK-014: обновление больше не выбирает между «свежий шаблон без своих правок»
и «свои правки без новых возможностей». Слепок того, из чего разворачивали,
даёт причину расхождения (правки в проекте / ушедший вперёд шаблон) и общего
предка для трёхстороннего merge.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import baseline, template_history  # noqa: E402
from backend.config import DEFAULTS  # noqa: E402
from backend.scaffold import (SKILLS_TEMPLATES, agentic_diff,  # noqa: E402
                              agentic_stale_details, resolve_element,
                              resolved_base, scaffold_project,
                              strip_optional_blocks)
from backend.validator import validate_project  # noqa: E402


class BaselineTestCase(unittest.TestCase):
    """Общая обвязка: развёрнутый проект и доступ к его слепку."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.tasks_dir = self.root / "tasks"
        self.cfg = dict(DEFAULTS)
        self.cfg["harnesses"] = {"claude": True, "opencode": True}

    def _scaffold(self, vault: bool = False) -> dict:
        return scaffold_project(self.tasks_dir, self.cfg, {
            "skills": True, "commands": True,
            "rules_agents": True, "rules_claude": False, "vault": vault,
        })

    def _skill(self, name: str = "start-task") -> Path:
        return self.root / ".claude" / "skills" / name / "SKILL.md"

    def _base_path(self, part: str, name: str) -> Path:
        return (baseline.store_dir(self.root, self.cfg) / baseline.BASELINE_DIR
                / part / name)

    def _codes(self) -> list[str]:
        report = validate_project(self.tasks_dir, self.cfg)
        return [d["code"] for d in report["degraded"]]

    def _stale(self, part: str, name: str) -> dict | None:
        return next((i for i in agentic_stale_details(self.root, self.cfg)
                     if i["part"] == part and i["name"] == name), None)

    def _pretend_template_moved(self, name: str = "start-task") -> str:
        """Сделать вид, что проект развернули из шаблона постарше.

        Подменяем слепок: в нём остаётся версия без последнего абзаца, значит
        шаблон с тех пор ушёл вперёд ровно на этот абзац. Тот же приём, что
        реальное обновление инструмента, но без правки файлов поставки.
        """
        skill = self._skill(name)
        text = skill.read_text(encoding="utf-8")
        old = text.replace("## Важно", "## Прежний заголовок", 1)
        self._base_path("skills", name).write_text(old, encoding="utf-8")
        return old


class BaselineRecordedTest(BaselineTestCase):
    """Слепок пишется при каждом развёртывании — иначе сравнивать не с чем."""

    def test_scaffold_records_skill_baseline(self) -> None:
        self._scaffold()
        base = baseline.read(self.root, "skills", "start-task", self.cfg)
        self.assertEqual(base, self._skill().read_text(encoding="utf-8"))

    def test_scaffold_records_command_rules_and_vault(self) -> None:
        self._scaffold(vault=True)
        self.assertIsNotNone(baseline.read(self.root, "commands", "new-task", self.cfg))
        self.assertIsNotNone(baseline.read(self.root, "rules", "AGENTS.md", self.cfg))
        self.assertIsNotNone(
            baseline.read(self.root, "vault", "SYS/structure.md", self.cfg))

    def test_scaffold_records_script_baseline(self) -> None:
        self._scaffold()
        base = baseline.read(self.root, "status_script", "set_status.py", self.cfg)
        self.assertEqual(base, (self.tasks_dir / "set_status.py").read_text(encoding="utf-8"))

    def test_meta_records_tool_version(self) -> None:
        """Из какой версии развёрнут проект, по самим файлам не узнать."""
        self._scaffold()
        self.assertTrue(baseline.meta(self.root, self.cfg).get("version"))

    def test_store_lives_inside_tasks(self) -> None:
        """Служебные копии — в папке задач: она и так вне git проекта."""
        self._scaffold()
        self.assertEqual(baseline.store_dir(self.root, self.cfg).parent, self.tasks_dir)


class FreshnessCriterionTest(BaselineTestCase):
    """Свежесть — это `слепок == шаблон`, а не `файл == шаблон`."""

    def test_customized_skill_is_silent(self) -> None:
        """Правка при неизменном шаблоне — кастомизация, а не устаревание.

        Прежний критерий означал вечный баннер: любая своя правка навсегда
        делала скилл «устаревшим», и сообщение переставало нести информацию.
        """
        self._scaffold()
        skill = self._skill()
        skill.write_text(skill.read_text(encoding="utf-8") + "\n## Свой шаг\n",
                         encoding="utf-8")

        self.assertNotIn("outdated_skills", self._codes())
        self.assertIsNone(self._stale("skills", "start-task"))

    def test_customized_script_is_silent(self) -> None:
        self._scaffold()
        script = self.tasks_dir / "set_status.py"
        script.write_text(script.read_text(encoding="utf-8") + "\n# свой хвост\n",
                          encoding="utf-8")
        self.assertNotIn("outdated_status_script", self._codes())

    def test_template_moved_is_reported_as_outdated(self) -> None:
        """Файл не трогали, шаблон ушёл вперёд — обновление безопасно."""
        self._scaffold()
        old = self._pretend_template_moved()
        self._skill().write_text(old, encoding="utf-8")

        item = self._stale("skills", "start-task")
        self.assertIsNotNone(item)
        self.assertEqual(item["state"], baseline.OUTDATED)
        self.assertIn("outdated_skills", self._codes())

    def test_both_moved_is_conflict(self) -> None:
        self._scaffold()
        old = self._pretend_template_moved()
        self._skill().write_text(old + "\n## Свой шаг\n", encoding="utf-8")

        item = self._stale("skills", "start-task")
        self.assertEqual(item["state"], baseline.CONFLICT)
        self.assertIn("outdated_skills", self._codes())

    def test_without_baseline_state_is_unknown(self) -> None:
        """Проект развёрнут до появления слепка: причину расхождения не знаем.

        Врать в любую сторону нельзя — ни «вы это правили», ни «просто отстал».
        Предок, восстановленный из истории шаблонов, состояние не меняет: он
        догадка, и слияние с ним предлагается отдельно (см. HistoryBaseTest).
        """
        self._scaffold()
        self._base_path("skills", "start-task").unlink()
        skill = self._skill()
        skill.write_text(skill.read_text(encoding="utf-8") + "\nхвост\n", encoding="utf-8")

        self.assertEqual(self._stale("skills", "start-task")["state"], baseline.UNKNOWN)

    def test_missing_file_still_missing(self) -> None:
        self._scaffold()
        self._skill("fix-task").unlink()
        self.assertEqual(self._stale("skills", "fix-task")["state"], baseline.MISSING)

    def test_state_matrix(self) -> None:
        """Пять состояний по трём текстам — таблица целиком."""
        self.assertEqual(baseline.state("a", "a", None), baseline.SAME)
        self.assertEqual(baseline.state(None, "a", "a"), baseline.MISSING)
        self.assertEqual(baseline.state("b", "a", None), baseline.UNKNOWN)
        self.assertEqual(baseline.state("b", "a", "a"), baseline.CUSTOMIZED)
        self.assertEqual(baseline.state("a", "b", "a"), baseline.OUTDATED)
        self.assertEqual(baseline.state("c", "b", "a"), baseline.CONFLICT)


class ResolveTest(BaselineTestCase):
    """Три исхода расхождения: слить, взять шаблон, оставить своё."""

    def test_keep_local_clears_banner_and_keeps_file(self) -> None:
        self._scaffold()
        old = self._pretend_template_moved()
        mine = old + "\n## Свой шаг\n"
        self._skill().write_text(mine, encoding="utf-8")

        result = resolve_element(self.root, "skills", "start-task", "keep", self.cfg)

        self.assertTrue(result["ok"])
        self.assertEqual(self._skill().read_text(encoding="utf-8"), mine)
        self.assertIsNone(self._stale("skills", "start-task"),
                          "намеренно расходящийся элемент не светится в баннере")
        self.assertNotIn("outdated_skills", self._codes())

    def test_keep_local_shows_again_on_next_template_change(self) -> None:
        """«Оставить своё» — не вечное молчание, а согласие с текущим шаблоном."""
        self._scaffold()
        self._pretend_template_moved()
        skill = self._skill()
        skill.write_text(skill.read_text(encoding="utf-8") + "\n## Свой шаг\n",
                         encoding="utf-8")
        resolve_element(self.root, "skills", "start-task", "keep", self.cfg)

        self._pretend_template_moved()  # шаблон снова ушёл вперёд

        self.assertIsNotNone(self._stale("skills", "start-task"))

    def test_take_template_backs_up_previous(self) -> None:
        self._scaffold()
        template = self._skill().read_text(encoding="utf-8")
        mine = template + "\n## Свой шаг\n"
        self._skill().write_text(mine, encoding="utf-8")

        result = resolve_element(self.root, "skills", "start-task", "template", self.cfg)

        self.assertEqual(self._skill().read_text(encoding="utf-8"), template)
        self.assertTrue(result["backup"], "перезапись без бэкапа необратима")
        self.assertEqual((self.root / result["backup"]).read_text(encoding="utf-8"), mine)

    @unittest.skipUnless(baseline.git_available(), "нужен git: слияние делает merge-file")
    def test_merge_keeps_local_edit_and_takes_template(self) -> None:
        """Правки в проекте остаются, новинки шаблона приезжают."""
        self._scaffold()
        old = self._pretend_template_moved()
        self._skill().write_text("# Мой заголовок\n" + old, encoding="utf-8")

        result = resolve_element(self.root, "skills", "start-task", "merge", self.cfg)

        merged = self._skill().read_text(encoding="utf-8")
        self.assertTrue(result["ok"])
        self.assertEqual(result["conflicts"], 0)
        self.assertIn("# Мой заголовок", merged, "правка проекта потеряна")
        self.assertIn("## Важно", merged, "новинка шаблона не приехала")
        self.assertIsNone(self._stale("skills", "start-task"), "после слияния баннер уходит")

    @unittest.skipUnless(baseline.git_available(), "нужен git: слияние делает merge-file")
    def test_merge_conflict_is_marked_in_file(self) -> None:
        """Правка в той же строке, что и обновление шаблона: решает человек."""
        self._scaffold()
        old = self._pretend_template_moved()
        self._skill().write_text(old.replace("## Прежний заголовок", "## Мой заголовок", 1),
                                 encoding="utf-8")

        result = resolve_element(self.root, "skills", "start-task", "merge", self.cfg)

        merged = self._skill().read_text(encoding="utf-8")
        self.assertGreater(result["conflicts"], 0)
        self.assertIn("<<<<<<<", merged)
        self.assertIn("## Мой заголовок", merged)
        self.assertIn("## Важно", merged)
        self.assertTrue(result["backup"])

    def test_merge_needs_baseline(self) -> None:
        """Без общего предка трёхстороннего слияния не бывает — отказываем явно.

        Предка нет совсем: ни слепка, ни похожей версии в истории шаблонов.
        """
        self._scaffold()
        self._base_path("skills", "start-task").unlink()
        self._skill().write_text("совсем другой текст\n", encoding="utf-8")

        result = resolve_element(self.root, "skills", "start-task", "merge", self.cfg)

        self.assertFalse(result["ok"])

    def test_unknown_part_reports_error(self) -> None:
        self._scaffold()
        self.assertFalse(resolve_element(self.root, "нет", "такого", "keep", self.cfg)["ok"])

    def test_resolve_works_for_rules_section(self) -> None:
        """Правила живут секцией внутри чужого файла — остальной текст не трогаем."""
        self._scaffold()
        path = self.root / "AGENTS.md"
        path.write_text("# Мой раздел\n\nтекст\n\n"
                        + path.read_text(encoding="utf-8") + "\nсвоя строка\n",
                        encoding="utf-8")
        base = baseline.read(self.root, "rules", "AGENTS.md", self.cfg) or ""
        self._base_path("rules", "AGENTS.md").write_text(
            base.replace("## Эпики", "## Прежние эпики", 1), encoding="utf-8")
        self.assertEqual(self._stale("rules", "AGENTS.md")["state"], baseline.CONFLICT)

        result = resolve_element(self.root, "rules", "AGENTS.md", "template", self.cfg)

        content = path.read_text(encoding="utf-8")
        self.assertTrue(result["ok"])
        self.assertIn("# Мой раздел", content, "чужой текст файла не должен пострадать")
        self.assertNotIn("своя строка", content, "секция приведена к эталону")
        self.assertNotIn("outdated_rules", self._codes())


class DiffTest(BaselineTestCase):
    """Окно расхождения показывает обе стороны: что нового и что своё."""

    def test_diff_splits_template_news_and_local_edits(self) -> None:
        self._scaffold()
        old = self._pretend_template_moved()
        self._skill().write_text("# Мой заголовок\n" + old, encoding="utf-8")

        diff = agentic_diff(self.root, "skills", "start-task", self.cfg)

        self.assertEqual(diff["state"], baseline.CONFLICT)
        self.assertIn("## Важно", diff["template_diff"], "не видно, что нового в шаблоне")
        self.assertIn("# Мой заголовок", diff["local_diff"], "не видно собственных правок")

    def test_diff_without_baseline_has_no_sides(self) -> None:
        """Предка нет вовсе — разделить расхождение на две стороны нечем."""
        self._scaffold()
        self._base_path("skills", "start-task").unlink()
        self._skill().write_text("совсем другой текст\n", encoding="utf-8")

        diff = agentic_diff(self.root, "skills", "start-task", self.cfg)

        self.assertEqual(diff["state"], baseline.UNKNOWN)
        self.assertEqual(diff["template_diff"], "")
        self.assertEqual(diff["local_diff"], "")


class HistoryBaseTest(BaselineTestCase):
    """Предок для проектов, развёрнутых до появления слепка (из истории шаблонов).

    Историю берём настоящую — репозиторий самого инструмента; если он поставлен
    архивом, а не клоном, тесты пропускаются вместе с возможностью.
    """

    TEMPLATE = SKILLS_TEMPLATES / "start-task" / "SKILL.md"

    def setUp(self) -> None:
        super().setUp()
        if not template_history.available():
            self.skipTest("история шаблонов недоступна: инструмент не git-клон")
        self.history = template_history.revisions(self.TEMPLATE)
        if len(self.history) < 2:
            self.skipTest("в истории шаблона меньше двух версий")
        self._scaffold()
        self._base_path("skills", "start-task").unlink()  # проект без слепка

    def _deploy_old_revision(self) -> str:
        """Положить в проект версию скилла из старой ревизии шаблона."""
        _commit, raw = self.history[-1]
        old = strip_optional_blocks(raw, set())
        self._skill().write_text(old, encoding="utf-8")
        return old

    def test_exact_match_proves_file_untouched(self) -> None:
        """Развёрнутое совпало с исторической версией — это отставание, не догадка."""
        self._deploy_old_revision()

        item = self._stale("skills", "start-task")

        self.assertEqual(item["state"], baseline.OUTDATED)
        self.assertEqual(item["base_origin"], "history")

    def test_edited_legacy_file_becomes_mergeable(self) -> None:
        """Правленный файл старого проекта: состояние честное, но слить можно."""
        old = self._deploy_old_revision()
        self._skill().write_text("# Мой заголовок\n" + old, encoding="utf-8")

        item = self._stale("skills", "start-task")

        self.assertEqual(item["state"], baseline.UNKNOWN,
                         "подбором состояние не переименовываем")
        self.assertTrue(item["mergeable"], "основа найдена — слияние доступно")
        self.assertEqual(item["base_origin"], "history")
        self.assertFalse(item["base_exact"], "это подбор, а не факт происхождения")
        self.assertGreater(item["base_ratio"], 0.5, "процент совпадения показывается")

    def test_recovered_base_is_not_called_deployed(self) -> None:
        """Подобранную версию нельзя подписывать «было развёрнуто».

        Тексты проекта бывают старше самого инструмента и из шаблона никогда
        не разворачивались — утверждать обратное значит врать в подписи diff.
        """
        old = self._deploy_old_revision()
        self._skill().write_text("# Мой заголовок\n" + old, encoding="utf-8")

        diff = agentic_diff(self.root, "skills", "start-task", self.cfg)

        self.assertIn("основа сравнения", diff["template_diff"])
        self.assertNotIn("было развёрнуто", diff["template_diff"])

    @unittest.skipUnless(baseline.git_available(), "нужен git: слияние делает merge-file")
    def test_merge_from_recovered_base(self) -> None:
        old = self._deploy_old_revision()
        self._skill().write_text("# Мой заголовок\n" + old, encoding="utf-8")

        result = resolve_element(self.root, "skills", "start-task", "merge", self.cfg)

        merged = self._skill().read_text(encoding="utf-8")
        self.assertTrue(result["ok"], result)
        self.assertIn("# Мой заголовок", merged, "правка проекта потеряна")
        self.assertIsNone(self._stale("skills", "start-task"),
                          "после слияния слепок записан, баннер уходит")

    def test_unrelated_content_gives_no_base(self) -> None:
        """Файл, ничем не похожий на шаблон: «ближайшая» ревизия была бы шумом.

        Кнопки слияния нет, но процент совпадения всё равно приходит: отказ
        объясняют числом, а не отсутствием кнопки — иначе правило выглядит
        произволом, ведь у соседнего элемента кнопка есть.
        """
        self._skill().write_text("совсем другой текст\n", encoding="utf-8")

        item = self._stale("skills", "start-task")

        self.assertEqual(item["state"], baseline.UNKNOWN)
        self.assertFalse(item["mergeable"])
        self.assertFalse(item["base_usable"])
        self.assertIsNotNone(item["base_ratio"], "процент совпадения не показан")

    def test_distant_base_is_offered_with_its_number(self) -> None:
        """Далёкая, но осмысленная основа предлагается — с процентом совпадения.

        Порог отсекает бессмыслицу, а не решает за человека: скилл, правленный
        до неузнаваемости, всё равно можно слить, зная, что конфликтов будет много.
        """
        _commit, raw = self.history[-1]
        old = strip_optional_blocks(raw, set()).splitlines()
        # Половина строк своя, половина из старой ревизии: совпадение заведомо
        # ниже «шумного» порога, но выше дна
        mixed = old[:len(old) // 2] + [f"своя строка {i}" for i in range(len(old) // 2)]
        self._skill().write_text("\n".join(mixed) + "\n", encoding="utf-8")

        item = self._stale("skills", "start-task")

        self.assertTrue(item["mergeable"], f"основа отвергнута: {item['base_ratio']}")
        self.assertLess(item["base_ratio"], 0.7)

    def test_stored_baseline_wins_over_history(self) -> None:
        """Слепок достовернее догадки — при его наличии история не спрашивается."""
        skill = self._skill()
        baseline.write(self.root, "skills", "start-task", "прежняя версия", self.cfg)
        skill.write_text("прежняя версия", encoding="utf-8")

        item = self._stale("skills", "start-task")

        self.assertEqual(item["state"], baseline.OUTDATED)
        self.assertEqual(item["base_origin"], "store")

    def test_rules_have_no_history_base(self) -> None:
        """Секция правил собирается кодом под пайплайн — старый текст не восстановить."""
        path = self.root / "AGENTS.md"
        content = path.read_text(encoding="utf-8")
        path.write_text(content + "\nсвоя строка\n", encoding="utf-8")
        self._base_path("rules", "AGENTS.md").unlink()

        base = resolved_base(self.root, "rules", "AGENTS.md",
                             content, self.cfg)

        self.assertIsNone(base["origin"])


class UiTest(unittest.TestCase):
    """Тест-раннера фронтенда в проекте нет: разметку проверяем чтением исходников.

    Связка «состояние из API ↔ кнопка в окне» рвётся молча, а цена разрыва —
    предложенное действие, которое гарантированно откажет.
    """

    SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
    DOCS = Path(__file__).resolve().parent.parent.parent / "docs" / "help"

    def _read(self, *parts: str) -> str:
        return self.SRC.joinpath(*parts).read_text(encoding="utf-8")

    def test_modal_offers_every_outcome(self) -> None:
        modal = self._read("components", "AgenticStaleModal.jsx")
        for action in ("'merge'", "'template'", "'keep'"):
            self.assertIn(action, modal, f"в окне нет исхода {action}")
        self.assertIn('Оставить свою', modal)

    def test_modal_names_every_state(self) -> None:
        """Состояние без подписи выглядит в окне пустой строкой."""
        modal = self._read("components", "AgenticStaleModal.jsx")
        for state in (baseline.OUTDATED, baseline.CONFLICT,
                      baseline.UNKNOWN, baseline.MISSING):
            self.assertIn(f"{state}:", modal, f"состояние {state} не подписано")

    def test_modal_names_recovered_base(self) -> None:
        """Слияние по подбору подписано: какая версия взята и насколько совпала."""
        modal = self._read("components", "AgenticStaleModal.jsx")
        for field in ("base_origin", "base_version", "base_exact", "base_ratio",
                      "base_usable"):
            self.assertIn(field, modal, f"окно не показывает {field}")
        self.assertIn("основа слияния", modal)

    def test_modal_explains_why_merge_is_absent(self) -> None:
        """Отсутствие кнопки объясняется числом, иначе правило выглядит произволом."""
        modal = self._read("components", "AgenticStaleModal.jsx")
        self.assertIn("слияние невозможно", modal)
        self.assertIn("конфликтов будет много", modal)

    def test_mass_buttons_split_by_risk(self) -> None:
        """Массовое обновление не трогает правленое, массовое слияние — не теряет.

        Одна кнопка «обновить всё» и была той самой перезаписью, из-за которой
        правки исчезали: безопасное и правленое обязаны разъезжаться.
        """
        modal = self._read("components", "AgenticStaleModal.jsx")
        self.assertIn("Обновить безопасные", modal)
        self.assertIn("Слить все", modal)
        self.assertIn("SAFE_STATES", modal)
        self.assertNotIn("Обновить все", modal, "массовая перезапись вернулась")

    def test_mass_merge_reports_conflicts(self) -> None:
        """После слияния пачкой единственное, что требует рук, — маркеры конфликтов."""
        modal = self._read("components", "AgenticStaleModal.jsx")
        self.assertIn("conflicted", modal)

    def test_merge_button_respects_missing_git(self) -> None:
        modal = self._read("components", "AgenticStaleModal.jsx")
        self.assertIn("canMerge", modal, "кнопка «Слить» не смотрит на наличие git")

    def test_outdated_codes_open_the_window(self) -> None:
        """Устаревшие скрипты ведут в окно, а не переписываются вслепую.

        В расхождении могут быть правки пользователя — кнопка «Обновить»
        прямо в баннере их не покажет и не спросит.
        """
        app = self._read("App.jsx")
        for code in ("outdated_script", "outdated_status_script", "outdated_template"):
            line = next(ln for ln in app.splitlines() if ln.strip().startswith(f"{code}:"))
            self.assertIn("modal: 'agentic'", line, f"{code} чинится в обход окна")

    def test_help_describes_outcomes(self) -> None:
        text = (self.DOCS / "05-agentic.md").read_text(encoding="utf-8")
        for word in ("Слить", "Оставить свою", "backup", "baseline"):
            self.assertIn(word, text, f"в руководстве не описано: {word}")


if __name__ == "__main__":
    unittest.main()
