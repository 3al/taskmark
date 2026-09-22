import { highlight } from '../highlight'

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
// groups = [{key, title, hits: [{key, heading, excerpt}]}]. `terms` — слова
// запроса для подсветки во фрагменте; `onPick(group, hit)` — переход к месту
export function SearchHits({ groups, terms, loading, activeKey, onPick }) {
  if (!groups) {
    return <div className="px-3 py-2 text-sm text-zinc-500">{loading ? 'Ищу…' : ''}</div>
  }
  if (!groups.length) {
    return <div className="px-3 py-2 text-sm text-zinc-500">Ничего не найдено</div>
  }
  return groups.map((group) => (
    <div key={group.key} className="pb-2">
      <div className="px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-zinc-400">
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
            <div className="text-sm text-zinc-200">{highlight(hit.heading, terms, { wholeWord: true })}</div>
          )}
          <div className="text-xs text-zinc-400 line-clamp-3">
            {highlight(hit.excerpt, terms, { wholeWord: true })}
          </div>
        </button>
      ))}
    </div>
  ))
}
