"""Тесты раздела помощи: документация читается из docs/help и не двоится.

Помощь нужна пользователю с чужим проектом на руках: до README нашего
репозитория он не дойдёт. Поэтому пользовательские разделы живут отдельными
файлами, README на них ссылается, а UI показывает те же самые файлы — тест
следит, чтобы источник остался один, а ссылки из UI не указывали в пустоту.
"""

import re
import unittest
from pathlib import Path

from backend import help_docs

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND = Path(__file__).resolve().parent.parent / 'frontend' / 'src'

# Разделы, на которые ссылается UI: редактор пайплайна, баннеры структуры
# и агентского окружения. Переименование файла без правки ссылок — регресс
LINKED_SECTIONS = ('board', 'lifecycle', 'agentic', 'validation')


class TestHelpSections(unittest.TestCase):
    def test_sections_listed(self) -> None:
        sections = help_docs.list_sections()
        self.assertTrue(sections, 'разделы помощи не найдены')
        for item in sections:
            self.assertTrue(item['id'], 'у раздела нет идентификатора')
            self.assertTrue(item['title'], f'у раздела {item["id"]} нет заголовка')

    def test_linked_sections_exist(self) -> None:
        ids = {item['id'] for item in help_docs.list_sections()}
        for key in LINKED_SECTIONS:
            self.assertIn(key, ids, f'UI ссылается на несуществующий раздел: {key}')

    def test_section_content(self) -> None:
        section = help_docs.get_section('lifecycle')
        self.assertIsNotNone(section, 'раздел жизненного цикла не читается')
        self.assertIn('#', section['content'], 'раздел пуст')

    def test_unknown_section(self) -> None:
        self.assertIsNone(help_docs.get_section('нет-такого'))

    def test_no_path_traversal(self) -> None:
        for evil in ('../../README', 'a/b', '..'):
            self.assertIsNone(help_docs.get_section(evil), f'выход за docs/help: {evil}')


class TestSingleSource(unittest.TestCase):
    """README не дублирует помощь, а ссылается на неё."""

    def setUp(self) -> None:
        self.readme = (ROOT / 'README.md').read_text(encoding='utf-8')

    def test_readme_links_help(self) -> None:
        self.assertIn('docs/help/', self.readme,
                      'README не ссылается на разделы помощи')

    def test_readme_has_no_duplicated_body(self) -> None:
        # Подробности жизненного цикла переехали в docs/help: их присутствие
        # в README означает две копии одного текста, которые разойдутся
        for marker in ('Как собрать свой пайплайн', 'Правила движения',
                       'Что происходит при изменении пайплайна'):
            self.assertNotIn(marker, self.readme,
                             f'раздел «{marker}» остался копией в README')


class TestHelpUi(unittest.TestCase):
    """Ссылка на помощь есть там, где возникает вопрос."""

    def test_modal_exists(self) -> None:
        self.assertTrue((FRONTEND / 'components' / 'HelpModal.jsx').is_file(),
                        'нет окна помощи во фронтенде')

    def test_header_opens_help(self) -> None:
        src = (FRONTEND / 'components' / 'Header.jsx').read_text(encoding='utf-8')
        self.assertIn('onOpenHelp', src, 'в шапке нет кнопки помощи')

    def test_pipeline_editor_links_help(self) -> None:
        src = (FRONTEND / 'components' / 'PipelineEditor.jsx').read_text(encoding='utf-8')
        self.assertIn('onOpenHelp', src, 'в редакторе пайплайна нет ссылки на помощь')
        self.assertNotIn('Подробнее — README', src,
                         'редактор всё ещё отправляет пользователя в README репозитория')

    def test_banners_link_help(self) -> None:
        src = (FRONTEND / 'src' / 'App.jsx').read_text(encoding='utf-8') \
            if (FRONTEND / 'src').is_dir() else (FRONTEND / 'App.jsx').read_text(encoding='utf-8')
        self.assertIsNotNone(re.search(r'openHelp\(', src),
                             'баннеры валидации не ведут в помощь')

    def test_api_client_has_help(self) -> None:
        src = (FRONTEND / 'api.js').read_text(encoding='utf-8')
        self.assertIn('/api/help', src, 'клиент API не умеет запрашивать помощь')


if __name__ == '__main__':
    unittest.main()
