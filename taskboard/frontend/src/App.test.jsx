import { act, cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'
import { setTabProject } from './api'

const project = { name: 'Демо-проект', tasks_dir: '/demo/tasks' }
const loading = 'Загрузка проектов…'
const noProject = '— проект не выбран —'
const boardLoading = 'Загрузка доски…'
const boardUnavailable = 'Доска недоступна'

const jsonResponse = (data) => ({ ok: true, json: async () => data })

function mockNetwork({ active = null, pendingProjects = null, boardResponse = jsonResponse({ columns: [] }) } = {}) {
  vi.stubGlobal('EventSource', class {
    close() {}
  })
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    switch (new URL(url, window.location.href).pathname) {
      case '/api/projects':
        return pendingProjects ?? jsonResponse({
          active: active?.name ?? null,
          projects: active ? [active] : [],
        })
      case '/api/health':
        return jsonResponse({
          ok: !!active,
          project: active,
          report: active ? {
            ok: true,
            degraded: [],
            warnings: [],
            features: { queue_section: true },
            harnesses: { choice: true },
          } : null,
        })
      case '/api/board':
        return boardResponse
      case '/api/update/status':
        return jsonResponse({ update_available: false })
      case '/api/brainstorm':
        return jsonResponse({ items: [], results: {} })
      default:
        throw new Error(`Неожиданный запрос в тесте: ${url}`)
    }
  }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  setTabProject(null)
  window.history.replaceState(null, '', window.location.pathname)
  localStorage.clear()
})

const header = () => within(screen.getByRole('banner'))
const boardArea = () => within(screen.getByRole('main'))

test('до ответа о проектах шапка показывает загрузку', async () => {
  let reply
  const pendingProjects = new Promise((resolve) => { reply = resolve })
  mockNetwork({ active: project, pendingProjects })
  await act(async () => { render(<App />) })

  try {
    expect(header().queryByText(noProject)).toBeNull()
    expect(header().getByText(loading)).toBeTruthy()
    expect(boardArea().getByText(boardLoading)).toBeTruthy()
    expect(boardArea().queryByText(boardUnavailable)).toBeNull()
  } finally {
    await act(async () => {
      reply(jsonResponse({ active: project.name, projects: [project] }))
    })
  }
})

test('после ответа с выбранным проектом шапка показывает его имя', async () => {
  mockNetwork({ active: project })
  await act(async () => { render(<App />) })

  expect(header().getByText(project.name)).toBeTruthy()
  expect(header().queryByText(loading)).toBeNull()
})

test('после ответа без выбранного проекта шапка сообщает об этом', async () => {
  mockNetwork()
  await act(async () => { render(<App />) })

  expect(header().getByText(noProject)).toBeTruthy()
  expect(header().queryByText(loading)).toBeNull()
  expect(boardArea().getByText(/Нет активного проекта/)).toBeTruthy()
})

test('до получения доски видно сообщение о загрузке, после ответа видна колонка', async () => {
  let reply
  const boardResponse = new Promise((resolve) => { reply = resolve })
  mockNetwork({ active: project, boardResponse })
  await act(async () => { render(<App />) })

  try {
    expect(header().getByText(project.name)).toBeTruthy()
    expect(boardArea().getByText(boardLoading)).toBeTruthy()
    expect(boardArea().queryByText(boardUnavailable)).toBeNull()
  } finally {
    await act(async () => {
      reply(jsonResponse({ columns: [{ title: 'Бэклог', status: 'backlog', groups: [] }] }))
    })
  }

  expect(boardArea().getByText('Бэклог')).toBeTruthy()
  expect(boardArea().queryByText(boardLoading)).toBeNull()
})

test('при ошибке загрузки доска сообщает, что недоступна', async () => {
  mockNetwork({ active: project, boardResponse: { ok: false, statusText: 'Ошибка загрузки' } })
  await act(async () => { render(<App />) })

  expect(boardArea().getByText(boardUnavailable)).toBeTruthy()
  expect(boardArea().queryByText(boardLoading)).toBeNull()
})
