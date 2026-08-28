import { useEffect, useState } from 'react'
import { api } from '../api'
import { COLOR_STYLE } from '../statuses'

// Окно эпика: что вообще входит в эпик и где оно едет.
//
// Эпик — единственная связь задачи с другими задачами, и до этого окна увидеть
// её было негде: в файле лежит ключ, имя — в реестре, состав приходилось
// собирать грепом. Порядок строк — **маршрут пайплайна**, а не номер задачи:
// список читают как «сколько ещё осталось», поэтому цвет статуса здесь тот же,
// что у колонки доски, — глаз переносит смысл с доски без обучения.
//
// Строк может быть много, а окно должно оставаться окном, поэтому список
// прокручивается внутри себя, а шапка с ключом и именем стоит на месте.
export default function EpicModal({ epicKey, onOpenTask, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setData(null)
    setError(null)
    api.epicTasks(epicKey)
      .then(setData)
      // Старый сервер эндпоинта не знает: это не поломка проекта, а разошедшиеся
      // версии — так и говорим, вместо пустого окна без объяснения
      .catch(() => setError('Сервер не отдаёт состав эпика — перезапустите его'))
  }, [epicKey])

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
            <div className="font-mono text-sm text-zinc-300">{epicKey}</div>
            {/* Имя живёт только в реестре: нет записи — так и пишем, потому что
                молчание тут неотличимо от эпика без имени */}
            <div className="mt-0.5 text-sm text-zinc-400">
              {data ? (data.name || 'нет записи в реестре эпиков') : '…'}
            </div>
          </div>
          <button onClick={onClose} title="Закрыть (Esc)"
                  className="text-zinc-400 hover:text-zinc-200 transition">✕</button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto p-2">
          {error && <div className="px-3 py-6 text-sm text-rose-400">{error}</div>}
          {!error && data && tasks.length === 0 && (
            <div className="px-3 py-6 text-sm text-zinc-400">
              В этом эпике пока нет задач.
            </div>
          )}
          {tasks.map((task) => {
            // Статус, выключенный из пайплайна, приходит без цвета: красить его
            // чужим оттенком значит соврать о том, где задача едет
            const style = COLOR_STYLE[task.color]
            return (
              <button
                key={task.id}
                type="button"
                onClick={() => onOpenTask(task.id)}
                className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left
                  transition ${style ? `${style.card} ${style.cardHover}`
                                     : 'border-zinc-800 bg-zinc-900 hover:bg-zinc-800/70'}`}>
                <span className="shrink-0 font-mono text-xs text-zinc-400">{task.id}</span>
                <span className="min-w-0 flex-1 truncate text-sm text-zinc-300">
                  {task.title}
                </span>
                <span className={`shrink-0 text-xs ${style ? style.header : 'text-zinc-400'}`}>
                  {task.label}
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
