// Настоящее окно настроек и клиент API; подменяется только сеть.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import SettingsModal from './SettingsModal'

let state
let enableError
let configError
let configValues
const warning = 'Автозапуск отключён средствами Windows. Установите галочку и нажмите «Сохранить» или разрешите taskboard в Диспетчере задач → Автозагрузка приложений.'

beforeEach(() => {
  localStorage.clear()
  enableError = null
  configError = null
  configValues = { port: 8765 }
  state = { supported: true, registered: true, enabled: false, stale: false,
            disabled_by_windows: true, warning, hint: '' }
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const path = new URL(url, 'http://test').pathname
    let body = {}
    if (path === '/api/config') {
      if (options.method === 'POST') {
        if (configError) return { ok: false, json: async () => ({ detail: configError }) }
        body = { config: JSON.parse(options.body).updates }
      } else body = configValues
    }
    if (path === '/api/pipeline') body = { pipeline: [], actions: {} }
    if (path === '/api/autostart') {
      if (options.method === 'POST') {
        if (enableError) return { ok: false, json: async () => ({ detail: enableError }) }
        state = { ...state, enabled: JSON.parse(options.body).enabled,
                  disabled_by_windows: false, warning: '' }
      }
      body = state
    }
    return { ok: true, json: async () => body }
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('системное отключение показано с объяснением и снятой галочкой', async () => {
  render(<SettingsModal onClose={() => {}} initialTab="tool" />)
  const checkbox = await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' })
  expect(checkbox.checked).toBe(false)
  expect(await screen.findByText(warning)).toBeTruthy()
})

const changes = () => fetch.mock.calls.filter(([url, options]) => url === '/api/autostart'
  && options.method === 'POST')

test('галочка остаётся черновиком и отменяется при закрытии', async () => {
  const onClose = vi.fn()
  const view = render(<SettingsModal onClose={onClose} initialTab="tool" />)
  const checkbox = await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' })
  fireEvent.click(checkbox)
  expect(checkbox.checked).toBe(true)
  expect(screen.getByText(warning)).toBeTruthy()
  expect(changes()).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Отмена' }))
  expect(onClose).toHaveBeenCalledOnce()
  view.unmount()
  render(<SettingsModal onClose={() => {}} initialTab="tool" />)
  expect((await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' })).checked).toBe(false)
  expect(changes()).toHaveLength(0)
})

test('включение применяется только общим сохранением', async () => {
  const onClose = vi.fn()
  render(<SettingsModal onClose={onClose} onSaved={() => {}} initialTab="tool" />)
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' }))
  expect(changes()).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  expect(changes()).toHaveLength(1)
  expect(JSON.parse(changes()[0][1].body).enabled).toBe(true)
  expect(screen.queryByText(warning)).toBeNull()
  expect(state.enabled).toBe(true)
})

test('отключение также ждёт общего сохранения', async () => {
  state = { ...state, enabled: true, disabled_by_windows: false, warning: '' }
  const onClose = vi.fn()
  render(<SettingsModal onClose={onClose} onSaved={() => {}} initialTab="tool" />)
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' }))
  expect(changes()).toHaveLength(0)
  expect(state.enabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  expect(JSON.parse(changes()[0][1].body).enabled).toBe(false)
  expect(state.enabled).toBe(false)
})

test('отказ сохранения оставляет черновик для повторной попытки', async () => {
  enableError = 'Не удалось разрешить автозапуск: нет доступа к реестру'
  const onClose = vi.fn()
  render(<SettingsModal onClose={onClose} onSaved={() => {}} initialTab="tool" />)
  const checkbox = await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' })
  fireEvent.click(checkbox)
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  expect(await screen.findByText(new RegExp(enableError))).toBeTruthy()
  expect(checkbox.checked).toBe(true)
  expect(screen.getByText(warning)).toBeTruthy()
  expect(onClose).not.toHaveBeenCalled()
  enableError = null
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  expect(state.enabled).toBe(true)
})

test('отказ сохранения основных полей не применяет автозапуск', async () => {
  configError = 'Порт недопустим'
  render(<SettingsModal onClose={() => {}} onSaved={() => {}} initialTab="tool" />)
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' }))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  expect(await screen.findByText(configError)).toBeTruthy()
  expect(changes()).toHaveLength(0)
  expect(state.enabled).toBe(false)
})

test('возврат галочки к исходному значению не меняет автозапуск', async () => {
  const onClose = vi.fn()
  render(<SettingsModal onClose={onClose} onSaved={() => {}} initialTab="tool" />)
  const checkbox = await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' })
  fireEvent.click(checkbox)
  fireEvent.click(checkbox)
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  expect(changes()).toHaveLength(0)
})

test('обновление устаревшей записи также ждёт сохранения', async () => {
  state = { ...state, enabled: true, stale: true, disabled_by_windows: false, warning: '' }
  const onClose = vi.fn()
  render(<SettingsModal onClose={onClose} onSaved={() => {}} initialTab="tool" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Обновить запись' }))
  expect(changes()).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  expect(JSON.parse(changes()[0][1].body).enabled).toBe(true)
})

test('включение из Telegram сохраняет черновик при переходе между вкладками', async () => {
  configValues.telegram = true
  const onClose = vi.fn()
  render(<SettingsModal onClose={onClose} onSaved={() => {}} initialTab="telegram" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Включить автозапуск' }))
  expect(changes()).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: /Общие/ }))
  expect((await screen.findByRole('checkbox', { name: 'Запускать при входе в систему' })).checked).toBe(true)
  expect(screen.getByText(warning)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  expect(changes()).toHaveLength(1)
})
