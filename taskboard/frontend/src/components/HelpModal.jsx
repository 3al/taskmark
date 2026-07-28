import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'

// Окно помощи: слева разделы, справа рендер markdown.
// Текст не дублируется в коде — сервер отдаёт те же файлы docs/help,
// на которые ссылается README, поэтому расходиться нечему.
export default function HelpModal({ section, onClose }) {
  const [items, setItems] = useState([])
  const [current, setCurrent] = useState(section || null)
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.help()
      .then(({ items }) => {
        setItems(items)
        // Ссылка «подробнее» открывает свой раздел; без неё — первый по порядку
        setCurrent((c) => (c && items.some((i) => i.id === c) ? c : items[0]?.id || null))
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!current) return
    setDoc(null)
    api.helpSection(current).then(setDoc).catch((e) => setError(e.message))
  }, [current])

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
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-5xl h-[85vh]
          flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-3 border-b border-zinc-800 bg-zinc-900/80">
          <div className="text-lg font-semibold text-zinc-300">Помощь</div>
          <div className="text-xs text-zinc-600">как работать с доской, задачами и пайплайнами</div>
          <button
            onClick={onClose}
            className="ml-auto text-zinc-500 hover:text-zinc-200 text-xl leading-none px-2"
            title="Закрыть (Esc)"
          >
            ×
          </button>
        </div>

        <div className="flex-1 flex min-h-0">
          <nav className="w-56 shrink-0 border-r border-zinc-800 overflow-y-auto py-2">
            {!items.length && !error && (
              <div className="px-3 py-2 text-sm text-zinc-600">Загрузка…</div>
            )}
            {items.map((item) => (
              <button
                key={item.id}
                onClick={() => setCurrent(item.id)}
                className={`w-full text-left px-3 py-2 text-sm transition border-l-2
                  ${item.id === current
                    ? 'border-sky-500 text-sky-300 bg-zinc-800/60'
                    : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'}`}
              >
                {item.title}
              </button>
            ))}
          </nav>

          <div className="flex-1 overflow-y-auto px-6 py-4 md-body md-tint-zinc text-sm">
            {error && <div className="text-rose-400">{error}</div>}
            {!doc && !error && <div className="text-zinc-500">Загрузка…</div>}
            {doc && <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.content}</ReactMarkdown>}
          </div>
        </div>
      </div>
    </div>
  )
}
