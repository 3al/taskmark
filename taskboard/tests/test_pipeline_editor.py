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


class TestTypeExceptions(unittest.TestCase):
    """Исключение по типу задачи настраивается из редактора (TASK-141).

    Ключ `except_types` читали оба конца движка, а бейдж «кроме: …» показывался
    в списке — но задать его было нечем: требование, добавленное человеком,
    накрывало все типы, и поправить это можно было только правкой
    `tasks/.taskboard.json` в редакторе текста.
    """

    def setUp(self) -> None:
        # Исходник в сообщение не кладём: провал печатал бы весь файл целиком
        self.src = EDITOR.read_text(encoding='utf-8')

    def has(self, needle: str) -> bool:
        return needle in self.src

    def test_types_come_from_the_delivery_catalog(self) -> None:
        """Свой список типов в редакторе разошёлся бы с поставкой молча."""
        self.assertTrue(re.search(r"import \{[^}]*TASK_TYPES[^}]*\} from '\.\./taskTypes'",
                                  self.src),
                        'типы берутся не из каталога поставки')

    def test_form_puts_exceptions_into_the_requirement(self) -> None:
        m = re.search(r'function RequirementForm\((.+?)\n\}\n', self.src, re.S)
        self.assertIsNotNone(m, 'форма требования не найдена')
        body = m.group(1) if m else ''

        self.assertTrue('except_types' in body,
                        'форма не умеет задавать исключение по типу')

    def test_declared_requirement_exceptions_are_editable(self) -> None:
        """Бейдж читался, но не правился — а требование живёт дольше формы."""
        self.assertTrue(self.has('setExcept'),
                        'исключение существующего требования нельзя изменить')

    def test_types_named_by_label_not_key(self) -> None:
        """Человек имеет дело с «Обсуждением», а не с `discussion`."""
        self.assertFalse(self.has("req.except_types.join(', ')"),
                         'в бейдже показываются служебные ключи типов')


class TestSourceCarriesRequires(unittest.TestCase):
    """Заполнение ЖЦ из источника несёт требования этапов (TASK-142).

    Бэкенд отдавал их правильно (`pipeline_sources` кладёт `requires` в каждый
    источник), а редактор терял: `applySource` звал `emit` без них, и `emit`
    собирал `onChange` вовсе без такого ключа. Человек видел подставленный
    маршрут, сохранял — и получал пустые требования **без всякого сообщения**,
    как будто в исходном проекте их не было.
    """

    def setUp(self) -> None:
        self.src = EDITOR.read_text(encoding='utf-8')
        self.modal = (EDITOR.parent / 'SettingsModal.jsx').read_text(encoding='utf-8')

    def body(self, pattern: str, what: str) -> str:
        m = re.search(pattern, self.src, re.S)
        self.assertIsNotNone(m, f'{what} не найдена')
        return m.group(1)

    def test_emit_carries_requires_to_onchange(self) -> None:
        body = self.body(r'const emit = \((.+?)\n  \}', 'функция emit')
        self.assertIn('requires', body,
                      'emit собирает onChange без требований — источнику нечем их донести')

    def test_apply_source_takes_requires_from_the_source(self) -> None:
        body = self.body(r'const applySource = \(value\) => \{(.+?)\n  \}',
                         'функция применения источника')
        self.assertIn('source.requires', body,
                      'требования источника до формы не доходят')

    def test_manual_edit_keeps_requirements_of_the_form(self) -> None:
        """Правка маршрута после подстановки требований терять не должна.

        Стрелки, удаление и добавление статуса зовут `emit` без требований —
        держится это на том, что родитель отличает «не передали» от «пусто».
        """
        self.assertIn('if (requires !== undefined)', self.modal,
                      'форма перезаписывает требования при любой правке маршрута')


if __name__ == '__main__':
    unittest.main()
