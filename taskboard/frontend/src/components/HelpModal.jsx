import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { rehypeHighlight } from '../highlight'
import { mdComponents } from '../markdown'
import { back, visit } from '../helpTrail'
import { HEADINGS_WITH_LINE, SearchField, SearchHits, hitTarget, useSearch } from './SearchHits'

// Ссылка на соседний раздел внутри документации: docs/help/02-board.md.
// Пишем их файлами, а не спецсхемой, чтобы те же тексты оставались
// кликабельными на GitHub — id раздела вынимаем из имени файла
const SECTION_LINK = /^(?:\.\/)?(?:\d+-)?([a-z0-9-]+)\.md(?:#.*)?$/i

function sectionOf(href) {
  const m = SECTION_LINK.exec(href || '')
  return m ? m[1] : null
}

// Окно помощи: слева разделы, справа рендер markdown.
// Текст не дублируется в коде — сервер отдаёт те же файлы docs/help,
// на которые ссылается README, поэтому расходиться нечему.
export default function HelpModal({ section, onClose }) {
  const [items, setItems] = useState([])
  const [current, setCurrent] = useState(section || null)
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState(null)
  const [query, setQuery] = useState('')
  // Место, к которому прокрутить после рендера: {section, line}
  const [target, setTarget] = useState(null)
  const bodyRef = useRef(null)
  // История переходов живёт, пока окно открыто: при новом открытии начинаем с
  // чистого листа, как новая вкладка браузера
  const [trail, setTrail] = useState([])
  // Высота, на которую вернуть раздел после загрузки при шаге «назад»
  const restoreRef = useRef(null)

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
    // Переход по ссылке из середины длинного раздела — новый текст читают
    // с начала, а не с той высоты, где кликнули. Кроме шага «назад»: там
    // высота вернётся, когда раздел загрузится
    if (bodyRef.current && restoreRef.current == null) bodyRef.current.scrollTop = 0
    api.helpSection(current).then(setDoc).catch((e) => setError(e.message))
  }, [current])

  const { found, terms, phrase } = useSearch(query, api.helpSearch, setError)
  useEffect(() => {
    if (!query.trim()) setTarget(null)
  }, [query])

  // Плагин пересобирается только со словами: новый на каждый рендер заставлял бы
  // react-markdown перерисовывать весь раздел
  const rehypePlugins = useMemo(
    () => (terms.length ? [rehypeHighlight(terms, { wholeWord: true, inCode: true, phrase })] : []),
    [phrase, terms.join(' ')], // eslint-disable-line react-hooks/exhaustive-deps
  )

  useEffect(() => {
    if (!target || !doc || doc.id !== target.section || !bodyRef.current) return
    const el = hitTarget(bodyRef.current, target.line)
    el?.scrollIntoView({ block: el.tagName === 'MARK' ? 'center' : 'start' })
  }, [target, doc, rehypePlugins])

  // Высота восстанавливается после рендера загруженного раздела: до него
  // прокручивать нечего — на месте текста стоит «Загрузка…»
  useEffect(() => {
    const restore = restoreRef.current
    if (restore == null || !doc || doc.id !== current || !bodyRef.current) return
    bodyRef.current.scrollTop = restore
    restoreRef.current = null
  }, [doc, current])

  // Единственный путь перехода — ссылка в тексте, меню и найденное место:
  // каждый переход в другой раздел запоминает, откуда ушли
  const go = (section, place = null) => {
    setTrail((t) => visit(t, { section: current, scroll: bodyRef.current?.scrollTop || 0 }, section))
    restoreRef.current = null
    setTarget(place)
    setCurrent(section)
  }

  const goBack = () => {
    const { place, trail: rest } = back(trail)
    if (!place) return
    restoreRef.current = place.scroll
    setTarget(null)
    setTrail(rest)
    setCurrent(place.section)
  }

  const titleOf = (id) => items.find((i) => i.id === id)?.title || id

  const pick = (group, hit) => go(group.key, { section: group.key, line: hit.line })

  const groups = found && found.items.map((item) => ({
    key: item.id,
    title: item.title,
    hits: item.hits.map((h) => ({ ...h, key: String(h.line) })),
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
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-5xl h-[85vh]
          flex flex-col shadow-2xl overflow-hidden"
      >
        <div className="flex items-center gap-3 px-5 py-3 border-b border-zinc-800 bg-zinc-900/80">
          {trail.length > 0 && (
            <button
              onClick={goBack}
              className="-ml-2 px-2 py-0.5 rounded-md text-lg leading-none text-zinc-400
                hover:text-zinc-100 hover:bg-zinc-800 transition"
              title={`Назад: ${titleOf(trail[trail.length - 1].section)}`}
            >
              ←
            </button>
          )}
          <div className="text-lg font-semibold text-zinc-300">Помощь</div>
          <div className="text-xs text-zinc-500">как работать с доской, задачами и пайплайнами</div>
          <button
            onClick={onClose}
            className="ml-auto text-zinc-400 hover:text-zinc-200 text-xl leading-none px-2"
            title="Закрыть (Esc)"
          >
            ×
          </button>
        </div>

        <div className="flex-1 flex min-h-0">
          <nav className={`${query.trim() ? 'w-80' : 'w-56'} shrink-0 border-r border-zinc-800
            flex flex-col pt-2`}>
            {/* Поле стоит на месте, прокручивается только список под ним */}
            <SearchField value={query} onChange={setQuery} placeholder="Поиск по справке" />
            <div className="flex-1 min-h-0 overflow-y-auto pb-2">
              {query.trim() && (
                <SearchHits
                  groups={groups}
                  terms={terms}
                phrase={phrase}
                  loading
                  activeKey={target && `${target.section}:${target.line}`}
                  onPick={pick}
                />
              )}
              {!query.trim() && !items.length && !error && (
                <div className="px-3 py-2 text-sm text-zinc-500">Загрузка…</div>
              )}
              {!query.trim() && items.map((item) => (
                <button
                  key={item.id}
                  onClick={() => go(item.id)}
                  className={`w-full text-left px-3 py-2 text-sm transition border-l-2
                    ${item.id === current
                      ? 'border-sky-500 text-sky-300 bg-zinc-800/60'
                      : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'}`}
                >
                  {item.title}
                </button>
              ))}
            </div>
          </nav>

          <div ref={bodyRef} className="flex-1 overflow-y-auto px-6 py-4 md-body md-tint-zinc text-sm">
            {error && <div className="text-rose-400">{error}</div>}
            {!doc && !error && <div className="text-zinc-400">Загрузка…</div>}
            {doc && (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={rehypePlugins}
                components={{
                  // Таблицы в справке широкие — прокручиваются в своей обёртке,
                  // как и в окне задачи
                  ...mdComponents,
                  ...HEADINGS_WITH_LINE,
                  // Ссылка на соседний раздел переключает окно, а не уводит
                  // из приложения на несуществующий по этому адресу файл
                  a: ({ href, children, ...props }) => {
                    const target = sectionOf(href)
                    if (!target) return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>
                    return (
                      <a
                        href={href}
                        onClick={(e) => { e.preventDefault(); go(target) }}
                        {...props}
                      >
                        {children}
                      </a>
                    )
                  },
                }}
              >
                {doc.content}
              </ReactMarkdown>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
