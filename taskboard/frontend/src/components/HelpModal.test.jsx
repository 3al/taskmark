// Окно помощи: переход к подразделу по якорю.
// Тексты — настоящие файлы docs/help: подменяется только сеть (fetch), сами
// разделы отдаются теми же, что раздаёт сервер
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import HelpModal from './HelpModal'

const DOCS = join(__dirname, '..', '..', '..', '..', 'docs', 'help')

// id раздела — имя файла без числового префикса, заголовок — первая строка `# …`
const sections = readdirSync(DOCS).filter((f) => f.endsWith('.md')).sort().map((file) => {
  const content = readFileSync(join(DOCS, file), 'utf-8')
  return {
    id: file.replace(/^\d+-/, '').replace(/\.md$/, ''),
    title: content.match(/^#\s+(.+)$/m)[1].trim(),
    content,
  }
})

function serve(url) {
  const path = decodeURIComponent(new URL(url, 'http://test').pathname)
  if (path === '/api/help') return { items: sections.map(({ id, title }) => ({ id, title })) }
  const found = sections.find((s) => path === `/api/help/${s.id}`)
  return found || null
}

let scrolled
beforeEach(() => {
  scrolled = []
  // jsdom не прокручивает: запоминаем, к чему и как просили прокрутить
  Element.prototype.scrollIntoView = function (options) { scrolled.push({ el: this, options }) }
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const body = serve(url)
    return { ok: !!body, status: body ? 200 : 404, statusText: '', json: async () => body }
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const heading = (text) => screen.findByRole('heading', { name: text })
const scrolledTo = () => scrolled.map(({ el }) => el.textContent)

test('таблицы справки остаются в общей обёртке горизонтальной прокрутки', async () => {
  render(<HelpModal section="validation" onClose={() => {}} />)
  await heading('Баннеры проблем')
  const tables = screen.getAllByRole('table')
  expect(tables.every((table) => table.parentElement.classList.contains('md-scroll-x'))).toBe(true)
})

test('кнопка «подробнее» открывает раздел на заголовке подраздела', async () => {
  render(<HelpModal section="agentic#сценарии-до-кода" onClose={() => {}} />)
  const target = await heading('Сценарии до кода')
  await waitFor(() => expect(scrolled.at(-1)?.el).toBe(target))
  expect(scrolled.at(-1).options).toMatchObject({ block: 'start' })
})

test('ссылка с якорем на другой раздел переключает раздел и прокручивает к заголовку', async () => {
  render(<HelpModal section="validation" onClose={() => {}} />)
  await heading('Баннеры проблем')
  fireEvent.click(screen.getByRole('link', { name: '«Обновление и правки»' }))
  const target = await heading('Обновление и правки')
  await waitFor(() => expect(scrolled.at(-1)?.el).toBe(target))
})

test('ссылка с якорем внутри раздела прокручивает к заголовку без новой вкладки', async () => {
  const open = vi.fn()
  vi.stubGlobal('open', open)
  render(<HelpModal section="agentic" onClose={() => {}} />)
  await heading('Агентское окружение и среды')
  const link = screen.getByRole('link', { name: '«Обновлении и правках»' })
  expect(link.getAttribute('target')).toBeNull()
  fireEvent.click(link)
  await waitFor(() => expect(scrolledTo()).toContain('Обновление и правки'))
  expect(open).not.toHaveBeenCalled()
})

test('якорь без заголовка — раздел открыт сверху, без ошибки', async () => {
  render(<HelpModal section="agentic#нет-такого-заголовка" onClose={() => {}} />)
  await heading('Агентское окружение и среды')
  expect(scrolled).toEqual([])
  expect(screen.queryByText(/ошибк/i)).toBeNull()
})

test('«назад» после перехода по якорю возвращает в прежний раздел на ту же высоту', async () => {
  const { container } = render(<HelpModal section="validation" onClose={() => {}} />)
  await heading('Баннеры проблем')
  const body = container.querySelector('.md-body')
  body.scrollTop = 300
  fireEvent.click(screen.getByRole('link', { name: '«Обновление и правки»' }))
  await heading('Обновление и правки')
  await act(async () => fireEvent.click(screen.getByTitle(/^Назад:/)))
  await heading('Баннеры проблем')
  await waitFor(() => expect(body.scrollTop).toBe(300))
})
