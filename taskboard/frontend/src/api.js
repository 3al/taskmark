// Клиент API Taskmark

async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    let payload = null
    try {
      const data = await res.json()
      payload = data.detail
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch { /* игнорируем */ }
    const error = new Error(detail)
    // Структурированный отказ (например, «нужно подтверждение переноса»)
    // должен доезжать до места вызова, а не превращаться в текст ошибки
    if (payload && typeof payload === 'object') {
      error.code = payload.code
      error.message = payload.message || detail
    }
    throw error
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
  // confirm — стоящую задачу берут в работу: без признака сервер откажет;
  // reason — причина съезда с маршрута (отмены), без неё он тоже откажет
  moveTask: (id, toSection, position = null, afterTaskId = null, group = null,
             confirm = false, reason = null) =>
    request(`/api/tasks/${id}/move`, {
      method: 'POST',
      body: JSON.stringify({ to_section: toSection, position,
                             after_task_id: afterTaskId, group, confirm, reason }),
    }),
  // С каким долгом задача окажется в разделе: спрашивается до переноса, чтобы
  // цену движения человек видел заранее. Вопрос, а не действие — ничего не пишет
  moveDebt: (id, toSection) =>
    request(`/api/tasks/${id}/move-debt?section=${encodeURIComponent(toSection)}`),
  // Подтверждение требований этапа человеком: только предикат confirm — он и
  // означает «человек сказал». Остальное закрывается работой, а не нажатием
  confirmRequirements: (id, ids, section = null) =>
    request(`/api/tasks/${id}/confirm`, {
      method: 'POST', body: JSON.stringify({ ids, section }),
    }),
  // Подпапки каталога для выбора корня проекта мышью: путь к папке браузер не
  // отдаёт, поэтому файловую систему показывает сервер
  browseDirs: (path = '') => request(`/api/fs/dirs?path=${encodeURIComponent(path)}`),
  ensureQueue: () => request('/api/queue/ensure', { method: 'POST' }),
  // Разделы под статусы маршрута, которых нет на доске: без них у статуса
  // нет колонки, а задача в нём на доске не показывается
  ensureSections: () => request('/api/board/sections/ensure', { method: 'POST' }),
  repairPlan: () => request('/api/board/repair'),
  repairApply: () => request('/api/board/repair', { method: 'POST' }),
  scaffold: (options) => request('/api/scaffold', { method: 'POST', body: JSON.stringify(options) }),
  // Дописывает в требования проекта исключения по типу задачи, появившиеся
  // в поставке позже. Правит только их — остальное в requires писал человек
  applyRequiresExceptions: () => request('/api/requires/exceptions', { method: 'POST' }),
  // Отмечает типы просмотренными. Ничего не настраивает: требования правит
  // человек, а отметка лишь гасит повторный вопрос
  markTypesReviewed: () => request('/api/requires/types-reviewed', { method: 'POST' }),
  agenticStale: () => request('/api/agentic/stale'),
  agenticDiff: (part, name) =>
    request(`/api/agentic/diff?part=${encodeURIComponent(part)}&name=${encodeURIComponent(name)}`),
  // Исход расхождения выбирает пользователь: merge — слить, template — взять
  // шаблон, keep — оставить свою версию (файл не трогаем, элемент затихает)
  // Удаление лишнего: скилла выключенной возможности и его обёртки opencode.
  // Отдельно от resolve — там три исхода расхождения, здесь файла быть не должно
  agenticRemove: (part, name) =>
    request('/api/agentic/remove', {
      method: 'POST', body: JSON.stringify({ part, name }),
    }),
  agenticResolve: (part, name, action) =>
    request('/api/agentic/resolve', {
      method: 'POST',
      body: JSON.stringify({ part, name, action }),
    }),
  getConfig: () => request('/api/config'),
  pipeline: () => request('/api/pipeline'),
  pipelineSources: () => request('/api/pipeline/sources'),
  // Известные исполнители: список общий для всех проектов машины и
  // пополняется по ходу — новое имя запоминает сама правка задачи
  assignees: () => request('/api/assignees'),
  epics: () => request('/api/epics'),
  // Состав эпика: задачи в порядке маршрута проекта + имя из реестра
  epicTasks: (key) => request(`/api/epics/${encodeURIComponent(key)}/tasks`),
  // blockerFor — кем можно заблокировать эту задачу (список считает бэкенд)
  tasksList: (blockerFor = '') =>
    request(`/api/tasks/list${blockerFor ? `?blocker_for=${encodeURIComponent(blockerFor)}` : ''}`),
  deleteTaskPlan: (id) => request(`/api/tasks/${encodeURIComponent(id)}/delete-plan`),
  deleteTask: (id) =>
    request(`/api/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // Комментарий с доски: отдельный вызов, а не правка секции — строку собирает
  // бэкенд, и время в ней системное
  addComment: (id, text) =>
    request(`/api/tasks/${encodeURIComponent(id)}/comment`,
            { method: 'POST', body: JSON.stringify({ text }) }),
  updateTask: (id, updates) =>
    request(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
  criteriaPresets: () => request('/api/criteria-presets'),
  saveCriteriaPreset: (text) =>
    request('/api/criteria-presets', { method: 'POST', body: JSON.stringify({ text }) }),
  deleteCriteriaPreset: (text) =>
    request('/api/criteria-presets', { method: 'DELETE', body: JSON.stringify({ text }) }),
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
  // Обновления: status читает кэш и в сеть не ходит, check — ходит,
  // и само нажатие кнопки считается согласием на запрос
  updateStatus: () => request('/api/update/status'),
  updateCheck: () => request('/api/update/check', { method: 'POST' }),
  // Применение обновления: plan говорит, можно ли и почему нет; apply выходит
  // из сервера, дальше работает лаунчер
  updatePlan: () => request('/api/update/plan'),
  updateApply: () => request('/api/update/apply', { method: 'POST' }),
  updateSeen: () => request('/api/update/seen', { method: 'POST' }),
  // Без since — весь файл; с ним — только версии новее указанной, то есть
  // всё, что пользователь пропустил, а не одна последняя. limit ограничивает
  // выдачу свежими: пропустивший два десятка выпусков получил бы стену текста
  changelog: (since, limit) => {
    const q = new URLSearchParams()
    if (since) q.set('since_version', since)
    if (limit) q.set('limit', String(limit))
    const s = q.toString()
    return request('/api/changelog' + (s ? `?${s}` : ''))
  },
}

// Подписка на живые обновления (SSE)
export function subscribeChanges(onChange, onUpdate) {
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
    // По одному каналу едут и правки файлов задач, и находки проверки
    // обновлений: тип события — сама строка
    if (event.data === 'update') onUpdate?.()
    if (event.data === 'changed') onChange()
  }
  return () => source.close()
}
