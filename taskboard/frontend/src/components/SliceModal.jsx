import { useEffect, useState } from 'react'
import { api } from '../api'
import SliceTaskList from './SliceTaskList'

// Подписи полей среза: окно одно на любое поле задачи, различается только то,
// как назвать значение в шапке и пустой состав
const FIELDS = {
  author: { caption: 'Автор', empty: 'У этого автора задач в проекте нет.' },
}

// Окно среза по полю задачи: все задачи с этим значением в порядке маршрута.
//
// Строк может быть много, а окно должно оставаться окном, поэтому список
// прокручивается внутри себя, а шапка со значением стоит на месте.
export default function SliceModal({ field, value, onOpenTask, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const meta = FIELDS[field] || { caption: field, empty: 'Задач с этим значением нет.' }

  useEffect(() => {
    setData(null)
    setError(null)
    api.taskSlice(field, value)
      .then(setData)
      // Старый сервер эндпоинта не знает: это разошедшиеся версии, а не
      // поломка проекта — так и говорим, вместо пустого окна
      .catch(() => setError('Сервер не отдаёт список задач — перезапустите его'))
  }, [field, value])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const tasks = data?.tasks || []

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto
                    bg-black/60 p-6" onClick={onClose}>
      <div className="w-full max-w-2xl rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3 border-b border-zinc-800 px-5 py-4">
          <div className="min-w-0 flex-1">
            <div className="text-xs text-zinc-500">{meta.caption}</div>
            <div className="mt-0.5 truncate text-sm text-zinc-300">{value}</div>
          </div>
          <button onClick={onClose} title="Закрыть (Esc)"
                  className="text-zinc-400 hover:text-zinc-200 transition">✕</button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto p-2">
          {error && <div className="px-3 py-6 text-sm text-rose-400">{error}</div>}
          {!error && data && tasks.length === 0 && (
            <div className="px-3 py-6 text-sm text-zinc-400">{meta.empty}</div>
          )}
          <SliceTaskList tasks={tasks} onOpenTask={onOpenTask} />
        </div>
      </div>
    </div>
  )
}
