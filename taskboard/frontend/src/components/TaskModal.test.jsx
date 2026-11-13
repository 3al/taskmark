// Окно задачи: сценарии КОГДА/ТОГДА в «Критериях приёмки» выделены блоком.
// Подменяется только сеть (fetch): тело задачи — в том виде, в каком его
// пишет скилл scenarios, остальные запросы окна получают пустой ответ
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import TaskModal from './TaskModal'

const BODY = `## Описание

Текст задачи с **жирным акцентом**.

| Поле | Значение |
|---|---|
| Результат | Материал |

### Критерии приёмки

- **КОГДА-то** обычный пункт критериев, не сценарий

#### Сценарий: тест сначала падает

- **КОГДА** по сценарию пишется автотест
- **ТОГДА** он падает до реализации
- **И** в трассе отмечено, что падал
- *Проверка: автотест через экран — \`npm test\`*

Проектов без галочки ничего из этого не касается.

#### Трасса

Граница — экран окна задачи.

- Сценарий «тест сначала падает» → ТОГДА автотестом: \`TaskModal.test.jsx\`, падал до кода

## Комментарии

- обычный комментарий`

function serve(url) {
  const path = new URL(url, 'http://test').pathname
  if (path === '/api/task/TASK-001') {
    return {
      meta: { id: 'TASK-001', title: 'Задача', status: 'development' },
      body: BODY,
      sections: [],
    }
  }
  return {}
}

beforeEach(() => {
  Element.prototype.scrollIntoView = () => {}
  vi.stubGlobal('fetch', vi.fn(async (url) => ({
    ok: true, status: 200, statusText: '', json: async () => serve(url),
  })))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const open = () => render(<TaskModal taskId="TASK-001" project="p" onClose={() => {}} />)

test('таблица задачи остаётся в общей обёртке горизонтальной прокрутки', async () => {
  open()
  const table = await screen.findByRole('table')
  expect(table.parentElement.classList.contains('md-scroll-x')).toBe(true)
})

test('сценарий — отдельный блок: заголовок и его строки внутри, соседний текст снаружи', async () => {
  open()
  const heading = await screen.findByRole('heading', { name: 'Сценарий: тест сначала падает' })
  const block = heading.closest('.md-scenario')
  expect(block).not.toBeNull()
  expect(block.textContent).toContain('он падает до реализации')
  expect(block.textContent).not.toContain('Проектов без галочки')
  expect(screen.getByText(/обычный пункт критериев/).closest('.md-scenario')).toBeNull()
  expect(screen.getByText('Проектов без галочки ничего из этого не касается.').closest('.md-scenario')).toBeNull()
})

test('ключевые слова сценария выделены, обычный жирный текст — нет', async () => {
  open()
  await screen.findByRole('heading', { name: 'Сценарий: тест сначала падает' })
  const words = [...document.querySelectorAll('.scenario-kw')].map((el) => el.textContent)
  expect(words).toEqual(['КОГДА', 'ТОГДА', 'И'])
  expect(screen.getByText('жирным акцентом').classList.contains('scenario-kw')).toBe(false)
  expect(screen.getByText('КОГДА-то').classList.contains('scenario-kw')).toBe(false)
})

test('строка «Проверка» приглушена отдельным классом', async () => {
  open()
  const check = await screen.findByText(/Проверка: автотест через экран/)
  expect(check.closest('li').classList.contains('scenario-check')).toBe(true)
  expect(screen.getByText(/он падает до реализации/).closest('li').classList.contains('scenario-check')).toBe(false)
})

test('трасса — блок той же полосы до следующего заголовка', async () => {
  open()
  const heading = await screen.findByRole('heading', { name: 'Трасса' })
  const block = heading.closest('.md-trace')
  expect(block).not.toBeNull()
  expect(block.classList.contains('md-scenario')).toBe(false)
  expect(block.textContent).toContain('Граница — экран окна задачи.')
  expect(block.textContent).toContain('ТОГДА автотестом')
  expect(screen.getByText('обычный комментарий').closest('.md-trace')).toBeNull()
})
