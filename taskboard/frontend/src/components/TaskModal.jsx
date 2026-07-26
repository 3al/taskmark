import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api } from '../api'
import { statusStyle } from '../statuses'
import CopyButton from './CopyButton'

// Модалка с полным содержимым задачи (рендер markdown)
export default function TaskModal({ taskId, onClose }) {
  const [task, setTask] = useState(null)
  const [error, setError] = useState(null)
  // Оттенок шапки модалки в цвет статуса задачи (применится после загрузки)
  const style = statusStyle(task?.meta?.status)

  useEffect(() => {
    setTask(null)
    setError(null)
    api.task(taskId).then(setTask).catch((e) => setError(e.message))
  }, [taskId])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`flex items-start gap-3 px-5 py-4 border-b ${style.modalHeader}`}>
          <div className="min-w-0">
            <div className="text-xs font-mono text-zinc-500">{taskId}</div>
            <div className="text-xl font-semibold text-zinc-300 leading-snug">
              {task?.meta?.title || '…'}
            </div>
            {task?.meta && (
              <div className="text-xs text-zinc-500 mt-1">
                статус: {task.meta.status || '—'} · создана: {task.meta.created || '—'}
                {task.meta.patch && task.meta.patch !== '~' ? ` · ${task.meta.patch}` : ''}
              </div>
            )}
          </div>
          {task && (
            <CopyButton
              className="ml-auto"
              text={`${task.meta?.title || taskId}\n\n${task.body || ''}`}
              title="Копировать содержимое задачи"
            />
          )}
          <button
            onClick={onClose}
            className={`${task ? '' : 'ml-auto'} text-zinc-500 hover:text-zinc-200 text-xl leading-none px-2`}
            title="Закрыть (Esc)"
          >
            ×
          </button>
        </div>

        <div className={`overflow-y-auto px-5 py-4 md-body text-sm ${style.mdTint}`}>
          {error && <div className="text-rose-400">{error}</div>}
          {!task && !error && <div className="text-zinc-500">Загрузка…</div>}
          {task && <ReactMarkdown>{task.body}</ReactMarkdown>}
        </div>
      </div>
    </div>
  )
}
