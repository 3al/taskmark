import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { roleSegments, segmentSource } from '../brainstormLog'
import { highlight, rehypeHighlight } from '../highlight'
import { mdComponents } from '../markdown'
import CopyButton from './CopyButton'
import { HEADINGS_WITH_LINE, SearchField, SearchHits, hitTarget, useSearch } from './SearchHits'

const MD_COMPONENTS = { ...mdComponents, ...HEADINGS_WITH_LINE }

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

// Цвета ролей протокола брейншторма: полоса слева и лёгкий фон. Цвета не
// пересекаются с фиолетовой меткой «брейншторм» и зелёной подсветкой поиска
const ROLE_STYLE = {
  architect: 'border-l-2 border-sky-400/70 bg-sky-500/5 pl-4 pr-2 py-1 my-3 rounded-r',
  pragmatist: 'border-l-2 border-amber-400/70 bg-amber-500/5 pl-4 pr-2 py-1 my-3 rounded-r',
  judge: 'border-l-2 border-rose-400/70 bg-rose-500/5 pl-4 pr-2 py-1 my-3 rounded-r',
}

// Панель просмотра логов tasks/logs/ (read-only)
export default function LogsPanel({ onClose }) {
  const [files, setFiles] = useState([])
  const [current, setCurrent] = useState(null)
  const [content, setContent] = useState(null)
  const [message, setMessage] = useState('')
  const [query, setQuery] = useState('')
  // Место, к которому прокрутить после загрузки файла: {name, line}
  const [target, setTarget] = useState(null)
  const bodyRef = useRef(null)
  const { found, terms, phrase } = useSearch(query, api.logsSearch, (e) => setMessage(`Ошибка: ${e}`))

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
    if (!query.trim()) setTarget(null)
  }, [query])

  const termsKey = `${phrase}:${terms.join(' ')}`
  // Markdown — кусками: у протокола брейншторма ответ каждой роли в своём
  // цвете, остальное одним куском. Номера строк кусков совпадают с файлом
  const mdParts = useMemo(() => {
    if (content?.kind !== 'markdown') return []
    return isBrainstormLog(current) ? roleSegments(content.text) : [{ role: null, start: 1, text: content.text }]
  }, [content, current])
  // Плагин и разбивка на строки пересобираются только со словами и файлом:
  // лог бывает в сотни килобайт, и лишняя перерисовка на каждый рендер заметна
  const rehypePlugins = useMemo(
    () => (terms.length ? [rehypeHighlight(terms, { wholeWord: true, inCode: true, phrase })] : []),
    [termsKey], // eslint-disable-line react-hooks/exhaustive-deps
  )
  // Консольный текст — строками с номером слева. Номер рисует окно, в файле
  // его нет: он не выделяется мышью и не попадает в копируемый текст. Метка
  // [data-line] у строки — место для перехода из поиска
  const textBody = useMemo(() => {
    if (!content || content.kind === 'markdown') return null
    const lines = content.text.split(/\r?\n/)
    // Перевод строки в конце файла — не пустая последняя строка
    if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
    const gutter = `calc(${String(lines.length).length}ch + 0.75rem)`
    return lines.map((line, k) => (
      <div key={k} data-line={k + 1} className="flex">
        <span aria-hidden="true" style={{ width: gutter }}
          className="shrink-0 pr-3 text-right text-zinc-500 select-none">{k + 1}</span>
        <span className="flex-1 min-w-0 whitespace-pre-wrap break-words">
          {(terms.length ? highlight(line, terms, { wholeWord: true, phrase }) : line) || ' '}
        </span>
      </div>
    ))
  }, [content, termsKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!target || !content || current !== target.name || !bodyRef.current) return
    const el = hitTarget(bodyRef.current, target.line)
    el?.scrollIntoView({ block: el.tagName === 'MARK' ? 'center' : 'start' })
  }, [target, content, current, rehypePlugins, textBody])

  const pick = (group, hit) => {
    setTarget({ name: group.key, line: hit.line })
    if (current !== group.key) open(group.key)
  }

  const groups = found && found.items.map((item) => ({
    key: item.name,
    title: item.name,
    more: item.more,
    hits: item.hits.map((h) => ({
      ...h,
      key: String(h.line),
      heading: item.kind === 'markdown' ? h.heading : `строка ${h.line}`,
    })),
  }))

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    // Клик мимо окна его не закрывает: промах мышью стирал бы поиск с
    // результатами. Закрывают × и Esc
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-6xl h-[85vh]
          flex shadow-2xl overflow-hidden"
      >
        <div className={`${query.trim() ? 'w-80' : 'w-72'} shrink-0 border-r border-zinc-800 flex flex-col min-h-0`}>
          <div className="px-4 py-3 border-b border-zinc-800">
            <div className="font-semibold text-sm">Логи</div>
            <div className="mt-0.5 text-[11px] text-zinc-400">свежие сверху</div>
          </div>
          {/* Поле стоит на месте, прокручивается только список под ним */}
          <div className="pt-2">
            <SearchField value={query} onChange={setQuery} placeholder="Поиск по логам" />
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto">
            {query.trim() && (
              <SearchHits
                groups={groups}
                terms={terms}
                phrase={phrase}
                loading
                activeKey={target && `${target.name}:${target.line}`}
                onPick={pick}
              />
            )}
            {!query.trim() && files.map((f) => {
              const brainstorm = isBrainstormLog(f.name)
              const active = current === f.name
              return (
                <button
                  key={f.name}
                  onClick={() => { setTarget(null); open(f.name) }}
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
            {!query.trim() && !files.length && <div className="px-4 py-3 text-xs text-zinc-400">Нет файлов</div>}
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
            <div ref={bodyRef} className="flex-1 overflow-auto px-6 py-4 md-body md-tint-zinc text-sm">
              {mdParts.map((part) => (
                <div key={part.start} className={ROLE_STYLE[part.role]}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins}
                    components={MD_COMPONENTS}>
                    {segmentSource(part)}
                  </ReactMarkdown>
                </div>
              ))}
            </div>
          )}
          {!message && content && content.kind !== 'markdown' && (
            <div ref={bodyRef} className="log-text flex-1 overflow-auto px-3 py-4 text-[13px] leading-relaxed
              text-zinc-200 font-mono">
              {textBody}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
