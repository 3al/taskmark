import { useEffect, useState } from 'react'
import { api } from '../api'

// Модалка создания задачи (вызов create_task.py через API)
// backlogSections — реальные подразделы ### колонки Backlog из board.md
export default function NewTaskModal({ backlogSections = [], onClose, onCreated }) {
  const [form, setForm] = useState({
    title: '',
    description: '',
    criteria: '',
    blocked_by: '',
    epic: '',
    epic_name: '',
    task_type: 'feature',
    target: 'backlog',
    section: backlogSections[0] || '',
    queue_position: 'end',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // Реестр эпиков: известные ключи подсказываем, для нового спрашиваем имя —
  // имя эпика хранится только в epics.md, ссылка на безымянный эпик бесполезна
  const [epics, setEpics] = useState([])

  useEffect(() => {
    api.epics().then((d) => setEpics(d.items || [])).catch(() => { /* нет реестра */ })
  }, [])

  const epicKey = form.epic.trim()
  const knownEpic = epics.find((e) => e.key.toLowerCase() === epicKey.toLowerCase())
  const needsEpicName = !!epicKey && !knownEpic

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  const submit = async () => {
    if (!form.title.trim()) {
      setError('Название обязательно')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const payload = { ...form, title: form.title.trim() }
      // section имеет смысл только для бэклога
      if (payload.target !== 'backlog') delete payload.section
      const result = await api.createTask(payload)
      onCreated(result.id)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const field = 'w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-500'
  const radio = 'accent-sky-500'

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-zinc-800 text-lg font-semibold">Новая задача</div>

        <div className="px-5 py-4 space-y-3">
          <input className={field} placeholder="Название (обязательно)" value={form.title} onChange={set('title')} autoFocus />
          <textarea className={`${field} h-24 resize-none`} placeholder="Описание" value={form.description} onChange={set('description')} />
          <input className={field} placeholder="Критерии приёмки (опционально)" value={form.criteria} onChange={set('criteria')} />
          <input className={field} placeholder="Заблокировано задачей (TASK-NNN, опционально)" value={form.blocked_by} onChange={set('blocked_by')} />

          <div className="flex gap-3">
            <input
              className={field}
              list="epic-keys"
              placeholder="Эпик — Jira-ключ (опционально)"
              value={form.epic}
              onChange={set('epic')}
            />
            <datalist id="epic-keys">
              {epics.map((e) => (
                <option key={e.key} value={e.key}>{e.name}</option>
              ))}
            </datalist>
            {needsEpicName && (
              <input
                className={field}
                placeholder="Название нового эпика"
                value={form.epic_name}
                onChange={set('epic_name')}
              />
            )}
          </div>
          {knownEpic && (
            <div className="text-[11px] text-zinc-500 -mt-1">Эпик: {knownEpic.name || knownEpic.key}</div>
          )}

          <div className="flex gap-3">
            <label className="flex-1 text-xs text-zinc-500">
              Тип
              <select className={`${field} mt-1`} value={form.task_type} onChange={set('task_type')}>
                <option value="feature">feature</option>
                <option value="bug">bug</option>
                <option value="refactor">refactor</option>
                <option value="cleanup">cleanup</option>
              </select>
            </label>
            <div className="flex-1 text-xs text-zinc-500">
              Куда
              <div className={`${field} mt-1 flex items-center gap-3`}>
                <label className="flex items-center gap-1.5 text-sm text-zinc-300 cursor-pointer">
                  <input
                    type="radio"
                    name="target"
                    checked={form.target === 'backlog'}
                    onChange={() => setForm({ ...form, target: 'backlog' })}
                    className={radio}
                  />
                  Бэклог
                </label>
                <label className="flex items-center gap-1.5 text-sm text-zinc-300 cursor-pointer">
                  <input
                    type="radio"
                    name="target"
                    checked={form.target === 'queue'}
                    onChange={() => setForm({ ...form, target: 'queue' })}
                    className={radio}
                  />
                  В очередь
                </label>
              </div>
            </div>
          </div>

          {form.target === 'backlog' && backlogSections.length > 0 && (
            <label className="block text-xs text-zinc-500">
              Раздел бэклога
              <select className={`${field} mt-1`} value={form.section} onChange={set('section')}>
                {backlogSections.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
          )}

          {form.target === 'queue' && (
            <label className="block text-xs text-zinc-500">
              Позиция в очереди
              <select className={`${field} mt-1`} value={form.queue_position} onChange={set('queue_position')}>
                <option value="end">В конец очереди</option>
                <option value="start">В начало очереди</option>
              </select>
            </label>
          )}

          {error && <div className="text-sm text-rose-400">{error}</div>}
        </div>

        <div className="px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
            Отмена
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 disabled:opacity-50 rounded-lg"
          >
            {busy ? 'Создаю…' : 'Создать'}
          </button>
        </div>
      </div>
    </div>
  )
}
