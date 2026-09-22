"""Поиск по логам: место — подзаголовок в Markdown, строка в консольном выводе.

Логи бывают длинными (вывод тестов на сотни килобайт), и человек ищет в них
конкретное: упавший тест, решение из брейншторма. Ответ — место в файле, чтобы
переход вёл сразу к нему.
"""

import codecs
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import app as app_module
from backend import log_files


class _LogsDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.logs = Path(self._tmp.name)
        self._write('TASK-001-brainstorm-log.md',
                    '# Брейншторм\n\n## Раунд 1\n\nАрхитектор предлагает кэш.\n\n'
                    '## Итог\n\nРешили без кэша: файлы задач мелкие.\n', mtime=100)
        self._write('TASK-002-tests.log',
                    'test_a ... ok\n\x1b[31mFAIL\x1b[0m: test_b (Ошибка в Ёлке)\ntest_c ... ok\n', mtime=200)

    def _write(self, name: str, text: str, mtime: int, raw: bytes | None = None) -> None:
        path = self.logs / name
        path.write_bytes(raw if raw is not None else text.encode('utf-8'))
        os.utime(path, (mtime, mtime))


class TestLogSearch(_LogsDir):
    def test_markdown_hit_under_subheading(self) -> None:
        [found] = log_files.search_logs(self.logs, 'файлы кэша')
        self.assertEqual(found['name'], 'TASK-001-brainstorm-log.md')
        self.assertEqual(found['kind'], 'markdown')
        [hit] = found['hits']
        self.assertEqual((hit['heading'], hit['line']), ('Итог', 7))
        self.assertIn('кэша', hit['excerpt'])

    def test_text_hit_is_line(self) -> None:
        [found] = log_files.search_logs(self.logs, 'test_b fail')
        self.assertEqual(found['kind'], 'text')
        [hit] = found['hits']
        self.assertEqual(hit['line'], 2)
        self.assertEqual(hit['excerpt'], 'FAIL: test_b (Ошибка в Ёлке)')

    def test_phrase_found_whole_and_only_there(self) -> None:
        self._write('TASK-004-brainstorm-log.md',
                    '# Итоги\n\n## Решение\n\nПривязать к ней спеки.\n\n'
                    '## Прочее\n\nКэш, спеки и лишний шаг — привязать к нему потом.\n', mtime=400)
        highlight, found = log_files.search_log_places(self.logs, 'Привязать к ней спеки')
        self.assertEqual(highlight, {'terms': ['привязать', 'к', 'ней', 'спеки'], 'phrase': True})
        self.assertEqual([(f['name'], [h['heading'] for h in f['hits']]) for f in found],
                         [('TASK-004-brainstorm-log.md', ['Решение'])])

    def test_text_words_must_share_a_line(self) -> None:
        self.assertEqual(log_files.search_logs(self.logs, 'test_a test_c'), [])

    def test_forms_case_and_yo(self) -> None:
        [found] = log_files.search_logs(self.logs, 'ошибки елка')
        self.assertEqual(found['name'], 'TASK-002-tests.log')

    def test_fresh_files_first(self) -> None:
        names = [f['name'] for f in log_files.search_logs(self.logs, 'кэш ok')]
        self.assertEqual(names, [])
        self._write('TASK-003-notes.md', '# Заметки\n\nкэш\n', mtime=300)
        names = [f['name'] for f in log_files.search_logs(self.logs, 'кэш')]
        self.assertEqual(names, ['TASK-003-notes.md', 'TASK-001-brainstorm-log.md'])

    def test_many_hits_capped_with_rest_counted(self) -> None:
        self._write('big.log', 'FAIL x\n' * 30, mtime=50)
        [found] = log_files.search_logs(self.logs, 'fail x', limit=20)
        self.assertEqual(len(found['hits']), 20)
        self.assertEqual(found['more'], 10)
        self.assertEqual(found['hits'][0]['line'], 1)

    def test_line_numbers_count_only_line_feeds(self) -> None:
        # Окно нумерует строки по переводу строки; \x0c и \u2028 в выводе — не разрыв
        self._write('odd.log', 'шаг\x0cодин\r\nшаг\u2028два\nцель здесь\n', mtime=5)
        [found] = log_files.search_logs(self.logs, 'цель')
        self.assertEqual(found['hits'][0]['line'], 3)

    def test_utf16_log_searched(self) -> None:
        self._write('ps.log', '', mtime=10,
                    raw=codecs.BOM_UTF16_LE + 'тесты зелёные\n'.encode('utf-16-le'))
        [found] = log_files.search_logs(self.logs, 'зеленые')
        self.assertEqual(found['name'], 'ps.log')

    def test_nothing_found_and_empty_query(self) -> None:
        self.assertEqual(log_files.search_logs(self.logs, 'несуществующееслово'), [])
        self.assertEqual(log_files.search_logs(self.logs, '  '), [])

    def test_missing_dir(self) -> None:
        self.assertEqual(log_files.search_logs(self.logs / 'нет', 'кэш'), [])


class TestLogSearchApi(_LogsDir):
    def test_endpoint(self) -> None:
        with mock.patch.object(app_module, '_ctx', return_value=(self.logs.parent, {'logs_dir': self.logs.name})):
            body = app_module.api_logs_search(q='FAIL')
        self.assertEqual((body['terms'], body['phrase']), (['fail'], False))
        self.assertEqual(body['items'][0]['name'], 'TASK-002-tests.log')

    def test_search_route_not_taken_for_file(self) -> None:
        route = next(r for r in app_module.app.routes
                     if getattr(r, 'path_regex', None) and r.path_regex.match('/api/logs/search'))
        self.assertIs(route.endpoint, app_module.api_logs_search)

    def test_capability_advertised(self) -> None:
        self.assertTrue(app_module.CAPABILITIES.get('log_search'))


if __name__ == '__main__':
    unittest.main()
