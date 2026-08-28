import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import CopyButton from './CopyButton'

// Панель просмотра логов tasks/logs/ (read-only)
export default function LogsPanel({ onClose }) {
  const [files, setFiles] = useState([])
  const [current, setCurrent] = useState(null)
  const [content, setContent] = useState('')

  useEffect(() => {
    api.logs().then((data) => setFiles(data.files)).catch(() => setFiles([]))
  }, [])

  const open = async (name) => {
    setCurrent(name)
    setContent('Загрузка…')
    try {
      const data = await api.log(name)
      setContent(data.content)
    } catch (e) {
      setContent(`Ошибка: ${e.message}`)
    }
  }

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
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-4xl max-h-[85vh] flex shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="w-64 shrink-0 border-r border-zinc-800 flex flex-col">
          <div className="px-4 py-3 border-b border-zinc-800 font-semibold text-sm">Логи</div>
          <div className="overflow-y-auto">
            {files.map((f) => (
              <button
                key={f.name}
                onClick={() => open(f.name)}
                className={`w-full text-left px-4 py-2 text-xs truncate hover:bg-zinc-800
                  ${current === f.name ? 'bg-zinc-800 text-sky-300' : 'text-zinc-300'}`}
              >
                {f.name}
              </button>
            ))}
            {!files.length && <div className="px-4 py-3 text-xs text-zinc-400">Нет файлов</div>}
          </div>
        </div>

        <div className="flex-1 flex flex-col min-w-0">
          <div className="px-4 py-3 border-b border-zinc-800 flex items-center">
            <span className="text-sm text-zinc-400 truncate">{current || 'Выберите файл'}</span>
            {current && content && !content.startsWith('Ошибка') && content !== 'Загрузка…' && (
              <CopyButton className="ml-auto" text={content} title="Копировать содержимое лога" />
            )}
            <button onClick={onClose} className={`${current ? '' : 'ml-auto'} text-zinc-400 hover:text-zinc-200 text-xl px-2`}>×</button>
          </div>
          <pre className="flex-1 overflow-auto px-4 py-3 text-xs text-zinc-300 whitespace-pre-wrap font-mono">
            {content}
          </pre>
        </div>
      </div>
    </div>
  )
}
