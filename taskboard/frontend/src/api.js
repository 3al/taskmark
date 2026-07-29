// Клиент API taskboard

async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch { /* игнорируем */ }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  health: () => request('/api/health'),
  board: () => request('/api/board'),
  task: (id) => request(`/api/task/${id}`),
  projects: () => request('/api/projects'),
  registerProject: (tasksDir, name) =>
    request('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ tasks_dir: tasksDir, name: name || null }),
    }),
  activateProject: (name) =>
    request('/api/projects/activate', { method: 'POST', body: JSON.stringify({ name }) }),
  removeProject: (name) => request(`/api/projects/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  createTask: (payload) => request('/api/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  moveTask: (id, toSection, position = null, afterTaskId = null, group = null) =>
    request(`/api/tasks/${id}/move`, {
      method: 'POST',
      body: JSON.stringify({ to_section: toSection, position, after_task_id: afterTaskId, group }),
    }),
  ensureQueue: () => request('/api/queue/ensure', { method: 'POST' }),
  repairPlan: () => request('/api/board/repair'),
  repairApply: () => request('/api/board/repair', { method: 'POST' }),
  scaffold: (options) => request('/api/scaffold', { method: 'POST', body: JSON.stringify(options) }),
  agenticStale: () => request('/api/agentic/stale'),
  agenticDiff: (part, name) =>
    request(`/api/agentic/diff?part=${encodeURIComponent(part)}&name=${encodeURIComponent(name)}`),
  getConfig: () => request('/api/config'),
  pipeline: () => request('/api/pipeline'),
  pipelineSources: () => request('/api/pipeline/sources'),
  epics: () => request('/api/epics'),
  saveConfig: (updates, moves) =>
    request('/api/config', { method: 'POST', body: JSON.stringify({ updates, moves }) }),
  previewConfig: (updates) =>
    request('/api/config/preview', { method: 'POST', body: JSON.stringify({ updates }) }),
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  help: () => request('/api/help'),
  helpSection: (id) => request(`/api/help/${encodeURIComponent(id)}`),
  logs: () => request('/api/logs'),
  log: (name) => request(`/api/logs/${encodeURIComponent(name)}`),
  stopServer: () => request('/api/server/stop', { method: 'POST' }),
  restartServer: () => request('/api/server/restart', { method: 'POST' }),
}

// Подписка на живые обновления (SSE)
export function subscribeChanges(onChange) {
  const source = new EventSource('/api/events')
  let opened = false
  // Переподключение после обрыва (перезапуск сервера, сон машины): события,
  // случившиеся в разрыве, потеряны — перечитываем состояние сами, иначе
  // доска и баннеры застывают до ручного F5
  source.onopen = () => {
    if (opened) onChange()
    opened = true
  }
  source.onmessage = (event) => {
    if (event.data === 'changed') onChange()
  }
  return () => source.close()
}
