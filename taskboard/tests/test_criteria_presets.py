"""Пресеты критериев приёмки в окне новой задачи (TASK-059).

Пользователь не знает заранее, что критерии по умолчанию — TDD: дефолт
заполняется в поле автоматически из пресетов. Набор пресетов встроенный
(TDD, SMOKE TEST, Ручная проверка) плюс сохранённые пользователем —
глобально (~/.taskboard/config.json), чтобы были доступны из всех проектов.

Запуск из корня репозитория:
    taskboard/.venv/Scripts/python.exe -m unittest discover -s taskboard/tests -t taskboard -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402

MODAL = (
    Path(__file__).resolve().parent.parent
    / "frontend" / "src" / "components" / "NewTaskModal.jsx"
)
API_JS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api.js"
# Подсказки задач вынесены в общий компонент (TASK-017): номер задачи вводят и
# в форме создания, и в открытой карточке при простановке блокировки
PICKER = (
    Path(__file__).resolve().parent.parent
    / "frontend" / "src" / "components" / "TaskPicker.jsx"
)


class CriteriaPresetsStoreTest(unittest.TestCase):
    """Хранилище пресетов: встроенные + пользовательские, глобально."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = (config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE)
        config.GLOBAL_DIR = Path(self._tmp.name)
        config.GLOBAL_CONFIG_FILE = Path(self._tmp.name) / "config.json"

    def tearDown(self) -> None:
        config.GLOBAL_DIR, config.GLOBAL_CONFIG_FILE = self._orig

    def test_builtin_presets_without_user_ones(self) -> None:
        self.assertEqual(
            config.criteria_presets(),
            ["TDD: RED -> GREEN -> ALL TESTS PASS", "SMOKE TEST", "Ручная проверка"],
        )

    def test_add_preset_persists_in_global_config(self) -> None:
        """Пресет доступен из всех проектов — лежит в глобальном конфиге."""
        config.add_criteria_preset("Нагрузочное тестирование")
        self.assertIn("Нагрузочное тестирование", config.criteria_presets())
        self.assertIn("Нагрузочное тестирование",
                      config.load_global_config()["criteria_presets"])

    def test_user_preset_goes_after_builtins(self) -> None:
        config.add_criteria_preset("Свой вариант")
        presets = config.criteria_presets()
        self.assertEqual(presets[-1], "Свой вариант")
        self.assertEqual(len(presets), 4)

    def test_add_duplicate_is_noop(self) -> None:
        before = config.criteria_presets()
        config.add_criteria_preset("SMOKE TEST")
        config.add_criteria_preset("  SMOKE TEST  ")
        self.assertEqual(config.criteria_presets(), before)
        self.assertNotIn("criteria_presets", config.load_global_config())

    def test_add_blank_is_noop(self) -> None:
        config.add_criteria_preset("   ")
        self.assertNotIn("criteria_presets", config.load_global_config())

    def test_remove_user_preset(self) -> None:
        config.add_criteria_preset("Свой вариант")
        config.remove_criteria_preset("Свой вариант")
        self.assertNotIn("Свой вариант", config.criteria_presets())
        self.assertEqual(config.load_global_config()["criteria_presets"], [])

    def test_remove_builtin_keeps_it(self) -> None:
        """Встроенные пресеты — часть поставки, удаляются только свои."""
        config.remove_criteria_preset("SMOKE TEST")
        self.assertIn("SMOKE TEST", config.criteria_presets())

    def test_custom_presets_listed_separately(self) -> None:
        """UI нужно знать, у каких пресетов рисовать крестик удаления."""
        config.add_criteria_preset("Свой вариант")
        self.assertEqual(config.custom_criteria_presets(), ["Свой вариант"])


class CriteriaPresetsUiTest(unittest.TestCase):
    """Форма новой задачи: пресеты видны и дефолт предзаполнен."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = MODAL.read_text(encoding="utf-8")
        cls.api = API_JS.read_text(encoding="utf-8")

    def test_modal_loads_presets(self) -> None:
        self.assertIn("criteriaPresets", self.src)

    def test_api_client_has_endpoints(self) -> None:
        self.assertIn("/api/criteria-presets", self.api)
        self.assertIn("saveCriteriaPreset", self.api)

    def test_criteria_prefilled_from_first_preset(self) -> None:
        """Пользователь видит дефолт заранее, а не узнаёт о нём после создания."""
        self.assertRegex(self.src, r"criteria:\s*\w+\[0\]")

    def test_fill_from_preset_select(self) -> None:
        """Поле предзаполнено, плейсхолдер не виден — смысл несёт подпись кнопки."""
        self.assertIn("Заполнить критерий приёмки из пресета", self.src)

    def test_save_as_preset_offered(self) -> None:
        self.assertIn("saveCriteriaPreset", self.src)

    def test_custom_preset_has_delete_button(self) -> None:
        self.assertIn("deleteCriteriaPreset", self.src)
        self.assertIn("Удалить пресет", self.src)

    def test_api_client_can_delete(self) -> None:
        self.assertIn("deleteCriteriaPreset", self.api)

    def test_description_got_taller(self) -> None:
        """Прицепом из TASK-059: полю «Описание» чуть добавлено высоты."""
        self.assertNotIn("h-24", self.src, "поле описания осталось прежней высоты")


class EpicSuggestionsUiTest(unittest.TestCase):
    """Поле эпика: подсказки в стиле приложения, а не нативный datalist (TASK-061).

    Нативный <datalist> рисуется браузером белым и выбивается из тёмной темы;
    кастомный список — как у пресетов критериев. Свободный ввод нового ключа
    сохраняется: список только подсказывает известные эпики.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = MODAL.read_text(encoding="utf-8")

    def test_no_native_datalist(self) -> None:
        self.assertNotIn("<datalist", self.src, "белая браузерная подсказка")
        self.assertNotIn('list="epic-keys"', self.src)

    def test_custom_suggestion_list(self) -> None:
        # Отфильтрованные по вводу эпики, кликабельные строки тёмного списка
        self.assertIn("epicSuggestions", self.src)
        self.assertIn("onMouseDown", self.src,
                      "клик по подсказке должен успеть до blur поля")

    def test_suggestion_shows_key_and_name(self) -> None:
        self.assertRegex(self.src, r"epicSuggestions\.map")

    def test_selected_epic_fills_key_and_name(self) -> None:
        """Выбор из списка пишет в поле «ключ · название» — оно же и подсказка."""
        self.assertIn("epicLabel", self.src)

    def test_no_separate_hint_line_below(self) -> None:
        """Подсказка «Эпик: …» под формой дёргала верстку — её больше нет."""
        self.assertNotIn("Эпик: {knownEpic.name", self.src)

    def test_epic_row_fields_can_shrink(self) -> None:
        """Инпут имеет интринсическую ширину: без min-w-0 появление второго поля
        в строке схлопывает поле эпика до полусантиметра."""
        self.assertIn("min-w-0", self.src)

    def test_submit_sends_key_not_label(self) -> None:
        """В поле может лежать «ключ · название», на бэкенд уходит только ключ."""
        self.assertIn("epicPicked", self.src)


class BlockedBySuggestionsUiTest(unittest.TestCase):
    """Поле blocked_by: подсказки списком задач проекта (TASK-063)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = MODAL.read_text(encoding="utf-8")
        cls.picker = PICKER.read_text(encoding="utf-8")
        cls.api = API_JS.read_text(encoding="utf-8")

    def test_api_client_has_endpoint(self) -> None:
        self.assertIn("tasksList", self.api)
        self.assertIn("/api/tasks/list", self.api)

    def test_form_uses_shared_picker(self) -> None:
        """Своей копии подсказок в форме больше нет — она общая с карточкой."""
        self.assertIn("TaskPicker", self.src)
        self.assertIn("blocked_by", self.src)

    def test_tasks_loaded_on_mount(self) -> None:
        self.assertIn("tasksList", self.picker)

    def test_custom_suggestion_list(self) -> None:
        """Список свой и тёмный: нативный datalist браузер рисует белым."""
        self.assertIn("suggestions", self.picker)
        self.assertNotIn("<datalist", self.picker)

    def test_suggestion_shows_id_and_title(self) -> None:
        self.assertRegex(self.picker, r"suggestions\.map")
        self.assertIn("t.title", self.picker)

    def test_no_create_new_option(self) -> None:
        """В отличие от эпика, для blocked_by нет поля «создать новую задачу»."""
        self.assertNotIn("blocked_name", self.src)

    def test_input_has_onfocus_onblur(self) -> None:
        self.assertIn("onFocus={() => setFocus(true)}", self.picker)
        self.assertIn("onBlur={() => setFocus(false)}", self.picker)

    def test_selected_task_fills_id(self) -> None:
        self.assertIn("onChange(task.id, task)", self.picker)

    def test_submit_sends_id_not_label(self) -> None:
        """В подсказке может быть ID · название, в поле пишется только ID."""
        self.assertIn("onMouseDown={() => pick(t)}", self.picker)


if __name__ == "__main__":
    unittest.main()
