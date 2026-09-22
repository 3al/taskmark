import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { rehypeHighlight } from '../highlight'
import { mdComponents } from '../markdown'
import { SearchField, SearchHits } from './SearchHits'

// Ссылка на соседний раздел внутри документации: docs/help/02-board.md.
// Пишем их файлами, а не спецсхемой, чтобы те же тексты оставались
// кликабельными на GitHub — id раздела вынимаем из имени файла
const SECTION_LINK = /^(?:\.\/)?(?:\d+-)?([a-z0-9-]+)\.md(?:#.*)?$/i

function sectionOf(href) {
  const m = SECTION_LINK.exec(href || '')
  return m ? m[1] : null
}

// Заголовки помечаются номером своей строки в файле: поиск отвечает местом
// «раздел + строка подзаголовка», и по этой метке окно прокручивает к нему
const withLine = (Tag) => ({ node, ...props }) => (
  <Tag data-line={node?.position?.start?.line} {...props} />
)
const HEADINGS = Object.fromEntries(
  ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].map((tag) => [tag, withLine(tag)]),
)

// Первое подсвеченное слово под подзаголовком — до следующего заголовка.
// Слова могли найтись только в блоке кода, где подсветки нет, — тогда сам заголовок
function hitTarget(body, line) {
  const heading = body.querySelector(`[data-line="${line}"]`)
  if (!heading) return null
  const headings = [...body.querySelectorAll('[data-line]')]
  const nextHeading = headings[headings.indexOf(heading) + 1]
  const mark = [...body.querySelectorAll('mark.search-hit')].find((m) =>
    heading.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING
    && !(nextHeading && nextHeading.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING))
  return mark || heading
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
  // {terms, items} последнего ответа поиска; null — поиска нет или ответ ещё не пришёл
  const [found, setFound] = useState(null)
  // Место, к которому прокрутить после рендера: {section, line}
  const [target, setTarget] = useState(null)
  const bodyRef = useRef(null)

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
    // с начала, а не с той высоты, где кликнули
    if (bodyRef.current) bodyRef.current.scrollTop = 0
    api.helpSection(current).then(setDoc).catch((e) => setError(e.message))
  }, [current])

  // Ввод опережает сеть: запрос уходит после паузы, иначе каждая буква — запрос
  useEffect(() => {
    const needle = query.trim()
    if (!needle) {
      setFound(null)
      setTarget(null)
      return
    }
    let alive = true
    const timer = setTimeout(() => {
      api.helpSearch(needle)
        .then((r) => alive && setFound({ terms: r.terms, items: r.items }))
        .catch((e) => alive && setError(e.message))
    }, 200)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [query])

  const terms = query.trim() ? found?.terms || [] : []
  // Плагин пересобирается только со словами: новый на каждый рендер заставлял бы
  // react-markdown перерисовывать весь раздел
  const rehypePlugins = useMemo(
    () => (terms.length ? [rehypeHighlight(terms, { wholeWord: true, inCode: true })] : []),
    [terms.join(' ')], // eslint-disable-line react-hooks/exhaustive-deps
  )

  useEffect(() => {
    if (!target || !doc || doc.id !== target.section || !bodyRef.current) return
    const el = hitTarget(bodyRef.current, target.line)
    el?.scrollIntoView({ block: el.tagName === 'MARK' ? 'center' : 'start' })
  }, [target, doc, rehypePlugins])

  const pick = (group, hit) => {
    setTarget({ section: group.key, line: hit.line })
    setCurrent(group.key)
  }

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
                  onClick={() => { setTarget(null); setCurrent(item.id) }}
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
                  ...HEADINGS,
                  // Ссылка на соседний раздел переключает окно, а не уводит
                  // из приложения на несуществующий по этому адресу файл
                  a: ({ href, children, ...props }) => {
                    const target = sectionOf(href)
                    if (!target) return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>
                    return (
                      <a
                        href={href}
                        onClick={(e) => { e.preventDefault(); setTarget(null); setCurrent(target) }}
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
