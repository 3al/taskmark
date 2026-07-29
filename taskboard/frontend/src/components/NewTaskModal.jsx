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
  // Пресеты критериев: дефолт виден заранее (поле предзаполнено первым),
  // свой вариант можно сохранить — он появится во всех проектах
  const [presets, setPresets] = useState([])
  // Пользовательские пресеты: только их можно удалять (встроенные — поставка)
  const [customPresets, setCustomPresets] = useState([])
  const [presetsOpen, setPresetsOpen] = useState(false)

  useEffect(() => {
    api.epics().then((d) => setEpics(d.items || [])).catch(() => { /* нет реестра */ })
    api.criteriaPresets()
      .then((d) => {
        const items = d.presets || []
        setPresets(items)
        setCustomPresets(d.custom || [])
        // Не затираем уже введённый текст
        setForm((f) => (f.criteria ? f : { ...f, criteria: items[0] || '' }))
      })
      .catch(() => { /* старый сервер без пресетов */ })
  }, [])

  const applyPresets = (d) => {
    setPresets(d.presets || [])
    setCustomPresets(d.custom || [])
  }

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
          {/* Переносы сохраняются и видны в задаче: markdown сам по себе склеил
              бы строки в абзац, поэтому в окне задачи включён мягкий перенос */}
          <textarea
            className={`${field} h-32 resize-none`}
            placeholder="Описание — что сделать и зачем. Абзацы через пустую строку, перечисления списком"
            value={form.description}
            onChange={set('description')}
          />
          <div>
            <input className={field} placeholder="Критерии приёмки (опционально)" value={form.criteria} onChange={set('criteria')} />
            <div className="flex items-center gap-2 mt-1">
              {presets.length > 0 && (
                <div className="relative">
                  <button
                    type="button"
                    className="rounded-lg px-2 py-1 text-[11px] text-zinc-400 hover:text-zinc-200 border border-dashed border-zinc-600 hover:border-zinc-400 focus:outline-none"
                    onClick={() => setPresetsOpen(!presetsOpen)}
                  >
                    Заполнить критерий приёмки из пресета ▾
                  </button>
                  {presetsOpen && (
                    <>
                      {/* Прозрачная подложка: клик мимо закрывает список */}
                      <div className="fixed inset-0 z-10" onClick={() => setPresetsOpen(false)} />
                      <div className="absolute z-20 mt-1 w-72 max-h-48 overflow-y-auto bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl">
                        {presets.map((p) => (
                          <div key={p} className="flex items-center">
                            <button
                              type="button"
                              className="flex-1 text-left px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700"
                              onClick={() => {
                                setForm({ ...form, criteria: p })
                                setPresetsOpen(false)
                              }}
                            >
                              {p}
                            </button>
                            {customPresets.includes(p) && (
                              <button
                                type="button"
                                title="Удалить пресет"
                                className="px-2 text-zinc-500 hover:text-rose-400"
                                onClick={async () => {
                                  try {
                                    applyPresets(await api.deleteCriteriaPreset(p))
                                  } catch { /* список просто останется прежним */ }
                                }}
                              >
                                ×
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
              {form.criteria.trim() && !presets.includes(form.criteria.trim()) && (
                <button
                  type="button"
                  className="text-[11px] text-sky-400 hover:text-sky-300"
                  onClick={async () => {
                    try {
                      applyPresets(await api.saveCriteriaPreset(form.criteria.trim()))
                    } catch { /* не критично: пресет просто не сохранится */ }
                  }}
                >
                  Сохранить как пресет
                </button>
              )}
            </div>
          </div>
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
