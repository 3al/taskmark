import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { mdComponents } from '../markdown'
import CopyButton from './CopyButton'

const logDateFormat = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit',
})

function formatLogDate(mtime) {
  return logDateFormat.format(new Date(mtime * 1000))
}

function isBrainstormLog(name) {
  return /-brainstorm(?:-team)?-log\.md$/i.test(name)
}

// Панель просмотра логов tasks/logs/ (read-only)
export default function LogsPanel({ onClose }) {
  const [files, setFiles] = useState([])
  const [current, setCurrent] = useState(null)
  const [content, setContent] = useState(null)
  const [message, setMessage] = useState('')

  useEffect(() => {
    api.logs()
      .then((data) => setFiles([...data.files].sort((a, b) => b.mtime - a.mtime)))
      .catch(() => setFiles([]))
  }, [])

  const open = async (name) => {
    setCurrent(name)
    setContent(null)
    setMessage('Загрузка…')
    try {
      const data = await api.log(name)
      setContent({ text: data.content, kind: data.kind })
      setMessage('')
    } catch (e) {
      setMessage(`Ошибка: ${e.message}`)
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
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-6xl h-[85vh]
          flex shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="w-72 shrink-0 border-r border-zinc-800 flex flex-col min-h-0">
          <div className="px-4 py-3 border-b border-zinc-800">
            <div className="font-semibold text-sm">Логи</div>
            <div className="mt-0.5 text-[11px] text-zinc-400">свежие сверху</div>
          </div>
          <div className="overflow-y-auto">
            {files.map((f) => {
              const brainstorm = isBrainstormLog(f.name)
              const active = current === f.name
              return (
                <button
                  key={f.name}
                  onClick={() => open(f.name)}
                  title={f.name}
                  className={`w-full overflow-hidden border-l-2 text-left px-4 py-2.5
                    hover:bg-zinc-800 ${active ? 'bg-zinc-800' : ''}
                    ${brainstorm ? 'border-violet-400 bg-violet-500/10' : 'border-transparent'}`}
                >
                  <span className={`block truncate text-xs
                    ${brainstorm ? 'font-medium text-violet-200' : active ? 'text-sky-300' : 'text-zinc-300'}`}>
                    {f.name}
                  </span>
                  <span className="mt-0.5 flex items-center gap-2 text-[11px] tabular-nums">
                    <span className={active ? 'text-sky-300/70' : 'text-zinc-400'}>
                      {formatLogDate(f.mtime)}
                    </span>
                    {brainstorm && (
                      <span className="rounded bg-violet-500/35 px-1 text-violet-100
                        ring-1 ring-inset ring-violet-400/50">
                        брейншторм
                      </span>
                    )}
                  </span>
                </button>
              )
            })}
            {!files.length && <div className="px-4 py-3 text-xs text-zinc-400">Нет файлов</div>}
          </div>
        </div>

        <div className="flex-1 flex flex-col min-w-0 min-h-0">
          <div className="px-4 py-3 border-b border-zinc-800 flex items-center">
            <span className="text-sm text-zinc-400 truncate">{current || 'Выберите файл'}</span>
            {current && content && (
              <CopyButton className="ml-auto" text={content.text} title="Копировать содержимое лога" />
            )}
            <button onClick={onClose} className={`${current ? '' : 'ml-auto'} text-zinc-400 hover:text-zinc-200 text-xl px-2`}>×</button>
          </div>
          {message && (
            <div className={`flex-1 px-5 py-4 text-sm ${message.startsWith('Ошибка') ? 'text-rose-400' : 'text-zinc-400'}`}>
              {message}
            </div>
          )}
          {!message && content?.kind === 'markdown' && (
            <div className="flex-1 overflow-auto px-6 py-4 md-body md-tint-zinc text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {content.text}
              </ReactMarkdown>
            </div>
          )}
          {!message && content && content.kind !== 'markdown' && (
            <pre className="flex-1 overflow-auto px-5 py-4 text-[13px] leading-relaxed
              text-zinc-200 whitespace-pre-wrap font-mono">
              {content.text}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}
