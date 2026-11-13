// Навигация по материалам через настоящие окна задачи и логов.
// Подменяется только сеть; оболочка связывает окна так же, как App.
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { setTabProject } from '../api'
import TaskModal from './TaskModal'
import LogsPanel from './LogsPanel'

const BODY = `## Описание

[Материал](logs/учебный%20материал.md)

[Полный путь](tasks/logs/учебный%20материал.md)

[От корня](/logs/учебный%20материал.md)

[Внешний источник](https://example.com/logs/source.md)

[Нет файла](logs/missing.md)

## Комментарии
`

const FILES = {
  'учебный материал.md': '# Учебный материал\n\n[Отчёт](report.md)\n\n[Данные](./data.json)',
  'report.md': '# Отчёт\n\n[Учебный материал](учебный%20материал.md)\n\n[Другой источник](https://example.com/report.md)',
  'data.json': '{"result": "ok"}',
}

function WindowPair({ initialLog = null }) {
  const [log, setLog] = useState(initialLog)
  const [task, setTask] = useState(true)
  return <>
    {task && <TaskModal taskId="TASK-001" project="p" onClose={() => setTask(false)}
                        onOpenLog={setLog} covered={log !== null} />}
    {log !== null && <LogsPanel initialFile={log} onClose={() => setLog(null)} />}
  </>
}

beforeEach(() => {
  Element.prototype.scrollIntoView = () => {}
  setTabProject('p')
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const path = decodeURIComponent(new URL(url, 'http://test').pathname)
    let data = {}
    let status = 200
    if (path === '/api/task/TASK-001') {
      data = {
        meta: { id: 'TASK-001', title: 'Задача с материалами', status: 'development' }, body: BODY,
        sections: [{ key: 'description', heading: '## Описание',
          text: BODY.split('## Описание\n')[1].split('\n## Комментарии')[0].trim() }],
      }
    } else if (path === '/api/logs') {
      data = { files: Object.keys(FILES).map((name) => ({ name, size: 100, mtime: 1000 })) }
    } else if (path === '/api/logs/search') {
      data = { terms: ['отчёт'], phrase: true, items: [{ name: 'report.md', kind: 'markdown', more: 0,
        hits: [{ line: 1, heading: 'Найденный отчёт', excerpt: 'Материал из поиска' }] }] }
    } else if (path.startsWith('/api/logs/')) {
      const name = path.slice('/api/logs/'.length)
      if (Object.hasOwn(FILES, name)) {
        data = { name, content: FILES[name], kind: name.endsWith('.md') ? 'markdown' : 'text' }
      } else {
        status = 404
        data = { detail: 'Лог не найден' }
      }
    }
    return { ok: status === 200, status, statusText: '', json: async () => data }
  }))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setTabProject(null) })

test('ссылки задачи открывают файл текущего проекта; Esc возвращает к задаче', async () => {
  render(<WindowPair />)
  for (const name of ['Материал', 'Полный путь', 'От корня']) {
    fireEvent.click(await screen.findByRole('link', { name, exact: true }))
    await screen.findByRole('heading', { name: 'Учебный материал' })
    expect(screen.getByText('Задача с материалами')).toBeTruthy()
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Учебный материал' })).toBeNull())
    expect(screen.getByText('Задача с материалами')).toBeTruthy()
  }
  const logRequests = fetch.mock.calls.map(([url]) => new URL(url, 'http://test'))
    .filter((url) => url.pathname.startsWith('/api/logs/'))
  expect(logRequests.length).toBe(3)
  expect(logRequests.every((url) => url.searchParams.get('project') === 'p')).toBe(true)
})

test('ссылка из предпросмотра открывает логи, сохраняя несохранённый текст задачи', async () => {
  render(<WindowPair />)
  await screen.findByRole('link', { name: 'Материал', exact: true })
  fireEvent.click(screen.getAllByTitle('Изменить текст')[0])
  const editor = screen.getByRole('textbox')
  fireEvent.change(editor, { target: { value: BODY + '\nНесохранённая правка' } })
  fireEvent.click(screen.getByRole('button', { name: 'Как выглядит' }))
  fireEvent.click(screen.getByRole('link', { name: 'Материал', exact: true }))
  await screen.findByRole('heading', { name: 'Учебный материал' })
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(screen.getByText('Несохранённая правка')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Текст', exact: true }))
  expect(screen.getByRole('textbox').value).toContain('Несохранённая правка')
})

test('относительные ссылки переключают материалы в одном окне, включая текстовый файл', async () => {
  render(<WindowPair initialLog="учебный материал.md" />)
  await screen.findByRole('heading', { name: 'Учебный материал' })
  fireEvent.click(screen.getByRole('link', { name: 'Отчёт', exact: true }))
  await screen.findByRole('heading', { name: 'Отчёт', exact: true })
  expect(screen.queryByRole('heading', { name: 'Учебный материал' })).toBeNull()
  fireEvent.click(screen.getByRole('link', { name: 'Учебный материал', exact: true }))
  await screen.findByRole('heading', { name: 'Учебный материал' })
  fireEvent.click(screen.getByRole('link', { name: 'Данные' }))
  await screen.findByText('{"result": "ok"}')
  expect(screen.getAllByText('Логи').length).toBe(1)
})

test('внешние адреса задачи и отчёта открываются в новой вкладке браузера', async () => {
  const { container } = render(<WindowPair initialLog="report.md" />)
  for (const [name, href] of [['Внешний источник', 'https://example.com/logs/source.md'],
                            ['Другой источник', 'https://example.com/report.md']]) {
    const link = await screen.findByRole('link', { name })
    expect(link.getAttribute('href')).toBe(href)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel').split(' ')).toContain('noopener')
    let intercepted
    // Наблюдаем результат обработки клика приложением, затем отменяем только
    // переход браузера: jsdom не умеет загружать внешние страницы.
    container.addEventListener('click', (event) => {
      intercepted = event.defaultPrevented
      event.preventDefault()
    }, { once: true })
    fireEvent.click(link)
    expect(intercepted).toBe(false)
  }
})

test('история ссылок и списка возвращает файлы на прежнюю высоту без повторного шага', async () => {
  render(<WindowPair initialLog="учебный материал.md" />)
  const first = await screen.findByRole('heading', { name: 'Учебный материал' })
  expect(screen.queryByTitle(/^Назад:/)).toBeNull()
  first.closest('.md-body').scrollTop = 320
  fireEvent.click(screen.getByRole('link', { name: 'Отчёт', exact: true }))
  const report = await screen.findByRole('heading', { name: 'Отчёт', exact: true })
  expect(report.closest('.md-body').scrollTop).toBe(0)
  report.closest('.md-body').scrollTop = 70
  fireEvent.click(screen.getByTitle('report.md'))
  fireEvent.click(screen.getByTitle('data.json'))
  await screen.findByText('{"result": "ok"}')
  fireEvent.click(screen.getByTitle('Назад: report.md'))
  await screen.findByRole('heading', { name: 'Отчёт', exact: true })
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Отчёт', exact: true }).closest('.md-body').scrollTop).toBe(70))
  fireEvent.click(screen.getByTitle('Назад: учебный материал.md'))
  await screen.findByRole('heading', { name: 'Учебный материал' })
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Учебный материал' }).closest('.md-body').scrollTop).toBe(320))
  expect(screen.queryByTitle(/^Назад:/)).toBeNull()
})

test('переход из поиска входит в историю и возвращается к прежнему месту', async () => {
  render(<WindowPair initialLog="учебный материал.md" />)
  const first = await screen.findByRole('heading', { name: 'Учебный материал' })
  first.closest('.md-body').scrollTop = 210
  fireEvent.change(screen.getByPlaceholderText('Поиск по логам'), { target: { value: 'отчёт' } })
  fireEvent.click(await screen.findByRole('button', { name: /Найденный отчёт/ }))
  await screen.findByRole('heading', { name: 'Отчёт', exact: true })
  fireEvent.click(screen.getByTitle('Назад: учебный материал.md'))
  await screen.findByRole('heading', { name: 'Учебный материал' })
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Учебный материал' }).closest('.md-body').scrollTop).toBe(210))
})

test('закрытие окна очищает локальную историю переходов', async () => {
  render(<WindowPair />)
  fireEvent.click(await screen.findByRole('link', { name: 'Материал', exact: true }))
  await screen.findByRole('heading', { name: 'Учебный материал' })
  fireEvent.click(screen.getByRole('link', { name: 'Отчёт', exact: true }))
  await screen.findByRole('heading', { name: 'Отчёт', exact: true })
  expect(screen.getByTitle(/^Назад:/)).toBeTruthy()
  fireEvent.keyDown(window, { key: 'Escape' })
  fireEvent.click(screen.getByRole('link', { name: 'Материал', exact: true }))
  await screen.findByRole('heading', { name: 'Учебный материал' })
  expect(screen.queryByTitle(/^Назад:/)).toBeNull()
})

test('отсутствующий файл показывает ошибку в логах, оставляя задачу открытой', async () => {
  render(<WindowPair />)
  fireEvent.click(await screen.findByRole('link', { name: 'Нет файла' }))
  await screen.findByText('Ошибка: Лог не найден')
  expect(screen.getByText('Логи')).toBeTruthy()
  expect(screen.getByText('Задача с материалами')).toBeTruthy()
})
