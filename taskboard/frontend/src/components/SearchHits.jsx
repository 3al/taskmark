import { useEffect, useState } from 'react'
import { highlight } from '../highlight'

// Поиск с паузой: запрос уходит, когда ввод замер на 200 мс, иначе каждая
// буква — запрос. Ответ сервера — {terms, phrase, items}; пустой запрос — null.
// `terms` и `phrase` — чем подсвечивать: сервер сам решил, нашлась ли фраза
// целиком или места подобраны по словам
export function useSearch(query, fetcher, onError) {
  const [found, setFound] = useState(null)
  useEffect(() => {
    const needle = query.trim()
    if (!needle) {
      setFound(null)
      return
    }
    let alive = true
    const timer = setTimeout(() => {
      fetcher(needle)
        .then((r) => alive && setFound({ terms: r.terms, phrase: !!r.phrase, items: r.items }))
        .catch((e) => alive && onError?.(e.message))
    }, 200)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [query]) // eslint-disable-line react-hooks/exhaustive-deps
  const terms = query.trim() ? found?.terms || [] : []
  const phrase = !!(query.trim() && found?.phrase)
  return { found: query.trim() ? found : null, terms, phrase }
}

// Заголовки markdown помечаются номером своей строки в файле: поиск отвечает
// местом «файл + строка подзаголовка», и по этой метке окно прокручивает к нему
const withLine = (Tag) => ({ node, ...props }) => (
  <Tag data-line={node?.position?.start?.line} {...props} />
)
export const HEADINGS_WITH_LINE = Object.fromEntries(
  ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].map((tag) => [tag, withLine(tag)]),
)

// Куда прокрутить к месту `line`: первое подсвеченное слово от метки
// [data-line] (заголовок markdown или строка текста) до следующей метки.
// Слова могли найтись только в блоке кода, где подсветки нет, — тогда сама метка
export function hitTarget(body, line) {
  const anchor = body.querySelector(`[data-line="${line}"]`)
  if (!anchor) return null
  const anchors = [...body.querySelectorAll('[data-line]')]
  const next = anchors[anchors.indexOf(anchor) + 1]
  const mark = [...body.querySelectorAll('mark.search-hit')].find((m) =>
    anchor.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING
    && !(next && next.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING))
  return mark || anchor
}

// Поле поиска для боковой колонки окна со списком слева и текстом справа.
// Esc с текстом очищает поле и дальше не идёт: окно закрывает только Esc в
// пустом поле — иначе стереть запрос было бы нечем, кроме мыши
export function SearchField({ value, onChange, placeholder }) {
  return (
    <div className="relative px-2 pb-2">
      <input
        autoFocus
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && value) {
            e.stopPropagation()
            onChange('')
          }
        }}
        placeholder={placeholder}
        className="w-full bg-zinc-800 border border-zinc-700 rounded-lg pl-2.5 pr-7 py-1.5 text-sm
          text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-sky-600"
      />
      {value && (
        <button
          onClick={() => onChange('')}
          className="absolute right-3.5 top-1.5 text-zinc-500 hover:text-zinc-200 leading-none px-1"
          title="Очистить (Esc)"
        >
          ×
        </button>
      )}
    </div>
  )
}

// Найденные места, сгруппированные по источнику (раздел справки, файл лога):
// groups = [{key, title, hits: [{key, heading, excerpt}], more}]. `more` — сколько
// мест источника не показано. `terms` и `phrase` — чем подсвечивать фрагмент
// (см. `useSearch`); `onPick(group, hit)` — переход к месту
export function SearchHits({ groups, terms, phrase, loading, activeKey, onPick }) {
  if (!groups) {
    return <div className="px-3 py-2 text-sm text-zinc-500">{loading ? 'Ищу…' : ''}</div>
  }
  if (!groups.length) {
    return <div className="px-3 py-2 text-sm text-zinc-500">Ничего не найдено</div>
  }
  return groups.map((group) => (
    <div key={group.key} className="pb-2">
      <div className="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-zinc-400 truncate"
        title={group.title}>
        {group.title}
      </div>
      {group.hits.map((hit) => (
        <button
          key={hit.key}
          onClick={() => onPick(group, hit)}
          className={`w-full text-left px-3 py-1.5 border-l-2 transition
            ${activeKey === `${group.key}:${hit.key}`
              ? 'border-sky-500 bg-zinc-800/60'
              : 'border-transparent hover:bg-zinc-800/40'}`}
        >
          {hit.heading && (
            <div className="text-sm text-zinc-200">{highlight(hit.heading, terms, { wholeWord: true, phrase })}</div>
          )}
          <div className="text-xs text-zinc-400 line-clamp-3">
            {highlight(hit.excerpt, terms, { wholeWord: true, phrase })}
          </div>
        </button>
      ))}
      {group.more > 0 && (
        <div className="px-3 py-1 text-xs text-zinc-400">и ещё {group.more} — уточните запрос</div>
      )}
    </div>
  ))
}
