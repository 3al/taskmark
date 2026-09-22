"""Поиск по разделу помощи: находит место под подзаголовком, а не файл целиком.

Справка длинная, и человек помнит слова, а не раздел, где они написаны. Поиск
отвечает местами — подзаголовком и фрагментом, — чтобы переход вёл сразу туда.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import app as app_module
from backend import help_docs, text_match

DOCS = {
    '01-start.md': (
        '# Быстрый старт\n\n'
        'Вступление про **доску** задач.\n\n'
        '## Установка\n\n'
        'Запустите `py taskboard.py` из корня.\n\n'
        '```\n'
        '## не заголовок: строка внутри кода\n'
        'python3 taskboard.py\n'
        '```\n'
    ),
    '02-board.md': (
        '# Доска\n\n'
        '## Карточка\n\n'
        'Задержка перед подсказкой о простое настраивается.\n\n'
        '### Ещё Ёлки\n\n'
        'Подробнее — в [жизненном цикле](04-lifecycle.md).\n'
    ),
}


class _DocsDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for name, text in DOCS.items():
            (root / name).write_text(text, encoding='utf-8')
        patcher = mock.patch.object(help_docs, 'DOCS_DIR', root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)


class TestTextMatch(unittest.TestCase):
    def test_terms_are_words_folded(self) -> None:
        self.assertEqual(text_match.terms('  Задержка   ПРОСТОЯ Ёж '), ['задержк', 'просто', 'еж'])

    def test_word_forms_find_each_other(self) -> None:
        for query, text in (('задержка простоя', 'через заданную задержку снимают простой'),
                            ('настройки', 'в настройках доски'),
                            ('блокировка', 'блокировки и паузу')):
            self.assertTrue(text_match.matches(text, text_match.terms(query)), query)

    def test_non_cyrillic_words_kept_whole(self) -> None:
        self.assertEqual(text_match.terms('TASK-166 api() set_status.py'),
                         ['task-166', 'api()', 'set_status.py'])

    def test_empty_query_has_no_terms(self) -> None:
        self.assertEqual(text_match.terms('   '), [])

    def test_all_terms_required_in_any_order(self) -> None:
        terms = text_match.terms('простое задержка')
        self.assertTrue(text_match.matches('Задержка перед подсказкой о простое', terms))
        self.assertFalse(text_match.matches('Задержка перед подсказкой', terms))

    def test_yo_folded_both_ways(self) -> None:
        self.assertTrue(text_match.matches('Ещё ёлки', text_match.terms('еще елки')))
        self.assertTrue(text_match.matches('еще елки', text_match.terms('ещё ёлки')))

    def test_literal_not_regex(self) -> None:
        self.assertTrue(text_match.matches('вызов api() и C++', text_match.terms('api() c++')))

    def test_word_matches_from_word_start(self) -> None:
        self.assertTrue(text_match.matches('нейросеть пишет', text_match.terms('ней')))
        self.assertFalse(text_match.matches('линейный вывод', text_match.terms('ней')))

    def test_short_word_matches_whole(self) -> None:
        self.assertTrue(text_match.matches('путь к файлу', text_match.terms('к')))
        self.assertFalse(text_match.matches('кэш и лишний шаг', text_match.terms('к')))

    def test_identifier_parts_found(self) -> None:
        self.assertTrue(text_match.matches('вызов set_status.py', text_match.terms('status')))
        self.assertTrue(text_match.matches('вызов set_status.py', text_match.terms('.py')))

    def test_phrase_whole_across_spaces(self) -> None:
        query = text_match.Query('Привязать к ней  спеки')
        self.assertTrue(query.has_phrase('решили: привязать к\nней спеки сразу'))
        self.assertFalse(query.has_phrase('спеки привязать к ней'))
        self.assertTrue(query.has_words('спеки привязать к ней'))

    def test_phrase_is_literal(self) -> None:
        self.assertTrue(text_match.Query('api() и C++').has_phrase('вызов API() и c++ рядом'))

    def test_excerpt_around_first_hit(self) -> None:
        text = 'слово ' * 60 + 'цель рядом' + ' хвост' * 60
        piece = text_match.excerpt(text, ['цель'], width=40)
        self.assertIn('цель', piece)
        self.assertTrue(piece.startswith('…') and piece.endswith('…'))
        self.assertLessEqual(len(piece), 44)


class TestHelpSearch(_DocsDir):
    def test_hit_under_subheading(self) -> None:
        result = help_docs.search('задержка простое')
        self.assertEqual([s['id'] for s in result], ['board'])
        section = result[0]
        self.assertEqual(section['title'], 'Доска')
        [hit] = section['hits']
        self.assertEqual(hit['heading'], 'Карточка')
        self.assertEqual(hit['line'], 3)
        self.assertIn('Задержка', hit['excerpt'])

    def test_words_across_sections_do_not_combine(self) -> None:
        # «установка» — в одном подразделе, «карточка» — в другом
        self.assertEqual(help_docs.search('установка карточка'), [])

    def test_sections_in_order_with_several_hits(self) -> None:
        result = help_docs.search('taskboard')
        self.assertEqual([s['id'] for s in result], ['start'])
        self.assertEqual([h['heading'] for h in result[0]['hits']], ['Установка'])

    def test_heading_itself_is_searched(self) -> None:
        [section] = help_docs.search('еще елки')
        self.assertEqual(section['hits'][0]['heading'], 'Ещё Ёлки')

    def test_markup_ignored_link_target_not_searched(self) -> None:
        self.assertTrue(help_docs.search('жизненном цикле'))
        self.assertTrue(help_docs.search('доску задач'))
        self.assertEqual(help_docs.search('lifecycle'), [])

    def test_hash_inside_code_block_is_not_heading(self) -> None:
        [section] = help_docs.search('python3')
        self.assertEqual(section['hits'][0]['heading'], 'Установка')

    def test_phrase_places_only_when_phrase_found(self) -> None:
        # Слова «перед подсказкой» есть и в другом порядке, но фраза — только в «Карточке»
        (help_docs.DOCS_DIR / '03-more.md').write_text(
            '# Ещё\n\n## Порядок\n\nподсказкой перед сном\n', encoding='utf-8')
        highlight, result = help_docs.search_places('перед подсказкой')
        self.assertEqual(highlight, {'terms': ['перед', 'подсказкой'], 'phrase': True})
        self.assertEqual([(s['id'], [h['heading'] for h in s['hits']]) for s in result],
                         [('board', ['Карточка'])])

    def test_words_when_phrase_nowhere(self) -> None:
        highlight, result = help_docs.search_places('простое задержка')
        self.assertFalse(highlight['phrase'])
        self.assertEqual(highlight['terms'], ['прост', 'задержк'])
        self.assertEqual(result[0]['hits'][0]['heading'], 'Карточка')

    def test_nothing_found(self) -> None:
        self.assertEqual(help_docs.search('несуществующееслово'), [])

    def test_empty_query(self) -> None:
        self.assertEqual(help_docs.search('  '), [])


class TestHelpSearchApi(_DocsDir):
    def test_endpoint(self) -> None:
        body = app_module.api_help_search(q='Задержка ПРОСТОЕ')
        self.assertEqual(body['terms'], ['задержк', 'прост'])
        self.assertFalse(body['phrase'])
        self.assertEqual(body['items'][0]['hits'][0]['heading'], 'Карточка')

    def test_endpoint_phrase(self) -> None:
        body = app_module.api_help_search(q='Задержка перед')
        self.assertEqual((body['terms'], body['phrase']), (['задержка', 'перед'], True))

    def _route_for(self, path: str):
        return next(r for r in app_module.app.routes
                    if getattr(r, 'path_regex', None) and r.path_regex.match(path))

    def test_search_route_not_taken_for_section(self) -> None:
        # /api/help/{section_id} объявлен рядом и поймал бы «search» как раздел
        self.assertIs(self._route_for('/api/help/search').endpoint, app_module.api_help_search)
        self.assertIs(self._route_for('/api/help/board').endpoint, app_module.api_help_section)

    def test_capability_advertised(self) -> None:
        self.assertTrue(app_module.CAPABILITIES.get('help_search'))


if __name__ == '__main__':
    unittest.main()
