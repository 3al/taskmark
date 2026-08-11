"""Тест: окно добавления задачи — модальное.

Баг TASK-003: клик по фону закрывал окно и терял введённый текст.
Проверяем, что у бэкдропа NewTaskModal нет обработчика onClick={onClose}.
"""

import re
import unittest
from pathlib import Path

MODAL = (
    Path(__file__).resolve().parent.parent
    / 'frontend' / 'src' / 'components' / 'NewTaskModal.jsx'
)


class TestNewTaskModalBackdrop(unittest.TestCase):
    def test_backdrop_has_no_close_onclick(self):
        src = MODAL.read_text(encoding='utf-8')
        # Ищем блок бэкдропа: fixed inset-0 ... до вложенного контентного div
        m = re.search(r'<div\s+className="fixed inset-0[^>]*>', src, re.S)
        self.assertIsNotNone(m, 'бэкдроп-модалка не найдена')
        self.assertNotIn(
            'onClick={onClose}', m.group(0),
            'бэкдроп закрывает окно по клику — введённый текст теряется',
        )


EDITOR = (
    Path(__file__).resolve().parent.parent
    / 'frontend' / 'src' / 'components' / 'MarkdownEditor.jsx'
)


class TestDescriptionEditor(unittest.TestCase):
    """Описание в форме создания правится тем же редактором, что и в задаче.

    Голая textarea оставляла человека наедине с синтаксисом: разметку негде
    подсмотреть, результат не увидеть до создания задачи.
    """

    def setUp(self) -> None:
        self.src = MODAL.read_text(encoding='utf-8')
        self.editor = EDITOR.read_text(encoding='utf-8')

    def test_form_uses_shared_editor(self) -> None:
        self.assertIn('MarkdownEditor', self.src,
                      'описание в форме создания — голая textarea')

    def test_criteria_stay_a_single_line_field(self) -> None:
        """Критерии приёмки — строка из пресета: панель и предпросмотр над
        однострочником занимают больше места, чем дают пользы."""
        criteria = self.src[self.src.index('form.criteria'):]
        self.assertIn('<input', self.src[:self.src.index('form.criteria')] + criteria[:400],
                      'поле критериев перестало быть однострочным')

    def test_editor_actions_are_optional(self) -> None:
        """Сохраняет и отменяет сама форма — вторая пара кнопок в поле лишняя."""
        self.assertIn('actions', self.editor,
                      'у редактора нет режима без собственных кнопок')
        self.assertIn('actions={false}', self.src,
                      'в форме показаны кнопки поля вдобавок к кнопкам формы')

    def test_ctrl_enter_submits_the_form(self) -> None:
        """В форме сохранять нечего: Ctrl+Enter создаёт задачу целиком."""
        self.assertIn('onSave={submit}', self.src,
                      'Ctrl+Enter в описании не создаёт задачу')

    def test_placeholder_survives(self) -> None:
        """Подсказка в пустом поле объясняет, что писать, — она не должна пропасть."""
        self.assertIn('placeholder', self.editor,
                      'редактор не принимает placeholder — форма потеряет подсказку')


class TestFormFitsTheScreen(unittest.TestCase):
    """Окно формы не должно вырастать за края экрана.

    Описание растёт под текст (у копии задачи — под текст оригинала), и без
    предела высоты шапка с полями и кнопки «Создать»/«Отмена» уезжали за края:
    форма оставалась без единственного способа её отправить.
    """

    def setUp(self) -> None:
        self.src = MODAL.read_text(encoding='utf-8')

    def test_height_is_capped_by_viewport(self) -> None:
        self.assertIn('max-h-[90vh]', self.src, 'высота окна ничем не ограничена')
        self.assertIn('flex flex-col', self.src,
                      'без колонки шапка и кнопки не удержатся по краям окна')

    def test_fields_scroll_inside_the_form(self) -> None:
        self.assertIn('overflow-y-auto', self.src,
                      'поля не прокручиваются — при пределе высоты они просто обрежутся')


if __name__ == '__main__':
    unittest.main()
