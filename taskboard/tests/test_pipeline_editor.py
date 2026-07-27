"""Тест редактора пайплайна: статус из каталога встаёт на своё место.

Проверяем исходник (как в test_new_task_modal): JS-раннера в проекте нет,
а регресс к наивному `[...pipeline, meta]` ломает ожидание пользователя —
добавленный local_testing должен оказаться между разработкой и ревью,
а не в конце списка, откуда его пришлось бы поднимать стрелками.
"""

import re
import unittest
from pathlib import Path

EDITOR = (
    Path(__file__).resolve().parent.parent
    / 'frontend' / 'src' / 'components' / 'PipelineEditor.jsx'
)


class TestPipelineEditorInsert(unittest.TestCase):
    def setUp(self) -> None:
        src = EDITOR.read_text(encoding='utf-8')
        m = re.search(r'const add = \(key\) => \{(.+?)\n  \}', src, re.S)
        self.assertIsNotNone(m, 'функция добавления статуса не найдена')
        self.body = m.group(1)

    def test_insert_position_computed_from_catalog(self) -> None:
        self.assertIn('order', self.body,
                      'позиция вставки не считается по каноническому порядку каталога')
        self.assertIn('splice', self.body, 'статус вставляется без выбора позиции')

    def test_no_naive_append(self) -> None:
        self.assertNotIn('[...pipeline, meta]', self.body,
                         'статус снова добавляется в конец списка')


if __name__ == '__main__':
    unittest.main()
