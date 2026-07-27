import { useEffect, useState } from 'react'
import { api } from '../api'

// Раскраска unified diff: цвет строки определяется её первым символом.
// Внешние diff-библиотеки намеренно не подключаются — dist/ коммитится
function DiffView({ text }) {
  if (!text) {
    return <div className="px-3 py-2 text-xs text-zinc-500">Файлы совпадают</div>
  }
  const lineClass = (line) => {
    if (line.startsWith('+++') || line.startsWith('---')) return 'text-zinc-500'
    if (line.startsWith('@@')) return 'text-sky-300 bg-sky-950/40'
    if (line.startsWith('+')) return 'text-emerald-200 bg-emerald-950/40'
    if (line.startsWith('-')) return 'text-rose-200 bg-rose-950/40'
    return 'text-zinc-400'
  }
  // min-w-max на обёртке: строки становятся шириной с самую длинную,
  // поэтому подсветка не обрывается на границе видимой области при скролле
  return (
    <pre className="text-[11px] leading-relaxed font-mono overflow-x-auto max-h-96 overflow-y-auto
      bg-zinc-950/60 rounded-lg border border-zinc-800">
      <div className="min-w-max">
        {text.split('\n').map((line, i) => (
          <div key={i} className={`px-3 whitespace-pre ${lineClass(line)}`}>{line || ' '}</div>
        ))}
      </div>
    </pre>
  )
}

// Модалка подробностей: что именно разошлось с шаблонами и что изменится
export default function AgenticStaleModal({ onClose, onUpdated }) {
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  // Развёрнутые диффы: ключ "part/name" → { diff, added, removed }
  const [diffs, setDiffs] = useState({})

  const key = (item) => `${item.part}/${item.name}`

  const load = async () => {
    try {
      const result = await api.agenticStale()
      setItems(result.items)
      if (result.items.length === 0) onUpdated()
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { load() }, [])

  const toggleDiff = async (item) => {
    const k = key(item)
    if (diffs[k]) {
      setDiffs(({ [k]: _drop, ...rest }) => rest)
      return
    }
    setBusy(k)
    try {
      const result = await api.agenticDiff(item.part, item.name)
      setDiffs((prev) => ({ ...prev, [k]: result }))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const update = async (item) => {
    setBusy(key(item))
    try {
      await api.scaffold({ parts: [item.part], names: [item.name] })
      setDiffs(({ [key(item)]: _drop, ...rest }) => rest)
      await load()
      onUpdated()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const updateAll = async () => {
    setBusy('all')
    try {
      const parts = [...new Set(items.map((i) => i.part))]
      await api.scaffold({ parts })
      setDiffs({})
      await load()
      onUpdated()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const PART_LABEL = { skills: 'Скилл', commands: 'Команда' }
  // Причину расхождения мы не знаем: либо шаблон обновился, либо файл правили
  // в проекте. Различить можно только храня базовую версию — её сейчас нет,
  // поэтому формулировки нейтральные и не приписывают правки пользователю
  const STATE_LABEL = {
    modified: { text: 'отличается от шаблона', cls: 'text-amber-300' },
    missing: { text: 'не развёрнут', cls: 'text-rose-300' },
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-7xl shadow-2xl
        max-h-[90vh] flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-800 flex items-center justify-between">
          <div className="text-lg font-semibold">Агентское окружение устарело</div>
          {items?.length > 0 && (
            <button
              onClick={updateAll}
              disabled={busy === 'all'}
              className="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500 disabled:opacity-50"
            >
              {busy === 'all' ? 'Обновляю…' : 'Обновить все'}
            </button>
          )}
        </div>

        <div className="px-5 py-4 space-y-3 overflow-y-auto">
          <div className="text-sm text-rose-200 bg-rose-950/40 border border-rose-800/60
            rounded-lg px-4 py-3">
            Обновление приводит файл к шаблонной версии. Файл мог разойтись как из-за
            обновления самого шаблона, так и из-за правок в проекте — во втором случае
            <strong> правки будут потеряны</strong>. Посмотрите diff, прежде чем обновлять.
          </div>
          {error && <div className="text-sm text-rose-400">{error}</div>}
          {items === null && <div className="text-sm text-zinc-500">Загружаю…</div>}
          {items?.length === 0 && (
            <div className="text-sm text-emerald-300">Всё актуально</div>
          )}

          {items?.map((item) => {
            const k = key(item)
            const state = STATE_LABEL[item.state] || STATE_LABEL.modified
            const diff = diffs[k]
            return (
              <div key={k} className="border border-zinc-800 rounded-xl">
                <div className="flex items-center gap-3 px-3 py-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm">
                      <span className="text-zinc-500 text-xs mr-2">{PART_LABEL[item.part]}</span>
                      <span className="font-medium">{item.name}</span>
                    </div>
                    <div className={`text-[11px] ${state.cls}`}>
                      {state.text}
                      {diff && ` · +${diff.added} / −${diff.removed}`}
                    </div>
                  </div>
                  <button
                    onClick={() => toggleDiff(item)}
                    disabled={busy === k}
                    className="px-2.5 py-1 text-xs rounded-lg bg-zinc-800 hover:bg-zinc-700
                      disabled:opacity-50"
                  >
                    {diff ? 'Скрыть' : 'Diff'}
                  </button>
                  <button
                    onClick={() => update(item)}
                    disabled={busy === k}
                    className="px-2.5 py-1 text-xs rounded-lg bg-sky-600 hover:bg-sky-500
                      disabled:opacity-50"
                  >
                    {busy === k ? '…' : 'Обновить'}
                  </button>
                </div>
                {diff && (
                  <div className="px-3 pb-3">
                    <div className="text-[11px] text-zinc-500 mb-1 font-mono">{item.path}</div>
                    <DiffView text={diff.diff} />
                    <div className="text-[11px] text-zinc-600 mt-1">
                      «+» — появится после обновления, «−» — исчезнет
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div className="px-5 py-4 border-t border-zinc-800 flex justify-end">
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
            Закрыть
          </button>
        </div>
      </div>
    </div>
  )
}
