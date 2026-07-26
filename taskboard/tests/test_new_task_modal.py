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


if __name__ == '__main__':
    unittest.main()
