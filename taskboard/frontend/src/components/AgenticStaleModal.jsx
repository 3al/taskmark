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

const PART_LABEL = {
  skills: 'Скилл',
  commands: 'Команда',
  rules: 'Правила',
  vault: 'Волт',
  create_script: 'Скрипт',
  status_script: 'Скрипт',
  template: 'Шаблон задачи',
}

// Состояние расхождения называет его причину, а не только факт. Причину даёт
// слепок — копия того, что инструмент сам развернул в проект: без него
// «отстал от шаблона» и «правили в проекте» неразличимы
const STATE_LABEL = {
  outdated: {
    text: 'отстал от шаблона',
    hint: 'правок в проекте нет — обновление ничего не потеряет',
    cls: 'text-amber-300',
  },
  conflict: {
    text: 'правки в проекте, шаблон обновился',
    hint: 'слияние сохранит и то и другое',
    cls: 'text-rose-300',
  },
  unknown: {
    text: 'происхождение неизвестно',
    hint: 'слепка нет — что здесь ваше, а что отставание, сказать нельзя',
    cls: 'text-amber-300',
  },
  missing: { text: 'не развёрнут', hint: '', cls: 'text-rose-300' },
}

// Ниже этого совпадения слияние состоится, но конфликтов будет много
const NOISY_RATIO = 0.7

// Основа слияния, подобранная по истории шаблонов, — не факт происхождения:
// файл мог быть развёрнут до появления слепка, а мог и вовсе не разворачиваться
// из шаблона. Поэтому называем версию и совпадение, а выводы оставляем человеку.
// Процент показывается и тогда, когда основа не годится: невидимая планка, молча
// убирающая кнопку у одного элемента и оставляющая у соседнего, выглядит произволом
const baseHint = (item) => {
  if (item.base_origin !== 'history') return ''
  const version = item.base_version ? ` ${item.base_version}` : ''
  if (item.base_exact) return ` · совпадает с шаблоном${version}`
  // Округляем вниз: «100%» у неточного совпадения читается как «файл тот же»
  const percent = item.base_ratio == null ? null : Math.floor(item.base_ratio * 100)
  if (!item.base_usable) {
    return percent == null
      ? ' · слияние невозможно: подходящей версии шаблона не нашлось'
      : ` · слияние невозможно: ближайшая версия шаблона${version} совпадает на ${percent}%`
  }
  const noisy = item.base_ratio < NOISY_RATIO ? ' — конфликтов будет много' : ''
  return ` · основа слияния: шаблон${version}, совпадение ${percent}%${noisy}`
}

// Расхождения, которые чинятся одной кнопкой без выбора: правок пользователя
// в них нет, терять нечего
const SAFE_STATES = ['outdated', 'missing']

// Модалка подробностей: что именно разошлось с шаблонами, почему и что делать
export default function AgenticStaleModal({ onClose, onUpdated }) {
  const [items, setItems] = useState(null)
  const [canMerge, setCanMerge] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  // Развёрнутые диффы: ключ "part/name" → { diff, template_diff, local_diff, … }
  const [diffs, setDiffs] = useState({})
  // Итоги разрешённых расхождений: ключ → { action, conflicts, backup }
  const [outcomes, setOutcomes] = useState({})

  const key = (item) => `${item.part}/${item.name}`

  const load = async () => {
    try {
      const result = await api.agenticStale()
      setItems(result.items)
      setCanMerge(result.can_merge !== false)
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

  const resolve = async (item, action) => {
    const k = key(item)
    setBusy(k)
    setError(null)
    try {
      const result = await api.agenticResolve(item.part, item.name, action)
      setOutcomes((prev) => ({ ...prev, [k]: result }))
      setDiffs(({ [k]: _drop, ...rest }) => rest)
      await load()
      onUpdated()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const safeItems = (items || []).filter((i) => SAFE_STATES.includes(i.state))
  const mergeableItems = (items || []).filter((i) => i.mergeable)

  // Массовое действие — тот же поэлементный запрос в цикле: бэкап, слепок и
  // подсчёт конфликтов должны быть у каждого элемента свои
  const resolveMany = async (list, action, tag) => {
    setBusy(tag)
    setError(null)
    try {
      for (const item of list) {
        const result = await api.agenticResolve(item.part, item.name, action)
        setOutcomes((prev) => ({ ...prev, [key(item)]: result }))
      }
      setDiffs({})
      await load()
      onUpdated()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const Button = ({ onClick, disabled, tone = 'zinc', title, children }) => (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`px-2.5 py-1 text-xs rounded-lg disabled:opacity-50 ${
        tone === 'sky' ? 'bg-sky-600 hover:bg-sky-500' : 'bg-zinc-800 hover:bg-zinc-700'
      }`}
    >
      {children}
    </button>
  )

  const actions = (item) => {
    const k = key(item)
    const disabled = busy === k
    if (SAFE_STATES.includes(item.state)) {
      return (
        <Button onClick={() => resolve(item, 'template')} disabled={disabled} tone="sky">
          {disabled ? '…' : item.state === 'missing' ? 'Развернуть' : 'Обновить'}
        </Button>
      )
    }
    return (
      <>
        {item.mergeable && (
          <Button
            onClick={() => resolve(item, 'merge')}
            disabled={disabled || !canMerge}
            tone="sky"
            title={canMerge ? '' : 'Слияние выполняет git — он не найден'}
          >
            {disabled ? '…' : 'Слить'}
          </Button>
        )}
        <Button onClick={() => resolve(item, 'template')} disabled={disabled}>
          Взять шаблон
        </Button>
        <Button
          onClick={() => resolve(item, 'keep')}
          disabled={disabled}
          title="Файл не меняется, элемент перестаёт светиться до следующей правки шаблона"
        >
          Оставить свою
        </Button>
      </>
    )
  }

  const conflicted = Object.values(outcomes)
    .filter((r) => r?.ok && r.conflicts)
    .map((r) => r.name)

  const outcomeLine = (result) => {
    if (!result?.ok) return null
    const parts = []
    if (result.action === 'merge') {
      parts.push(result.conflicts
        ? `Слито, конфликтов: ${result.conflicts} — разрешите маркеры <<<<<<< в файле`
        : 'Слито без конфликтов')
    } else if (result.action === 'keep') {
      parts.push('Оставлена ваша версия')
    } else {
      parts.push('Приведено к шаблону')
    }
    if (result.backup) parts.push(`прежняя версия: ${result.backup}`)
    return parts.join(' · ')
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-7xl shadow-2xl
        max-h-[90vh] flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-800 flex items-center justify-between">
          <div className="text-lg font-semibold">Расхождения агентского окружения</div>
          <div className="flex items-center gap-2">
            {mergeableItems.length > 0 && (
              <button
                onClick={() => resolveMany(mergeableItems, 'merge', 'merge-all')}
                disabled={busy === 'merge-all' || !canMerge}
                title="Правки останутся, новое из шаблона приедет; пересечения станут маркерами в файлах"
                className="px-3 py-1.5 text-xs rounded-lg bg-zinc-800 hover:bg-zinc-700
                  disabled:opacity-50"
              >
                {busy === 'merge-all' ? 'Сливаю…' : `Слить все (${mergeableItems.length})`}
              </button>
            )}
            {safeItems.length > 0 && (
              <button
                onClick={() => resolveMany(safeItems, 'template', 'safe-all')}
                disabled={busy === 'safe-all'}
                className="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500
                  disabled:opacity-50"
              >
                {busy === 'safe-all' ? 'Обновляю…' : `Обновить безопасные (${safeItems.length})`}
              </button>
            )}
          </div>
        </div>

        <div className="px-5 py-4 space-y-3 overflow-y-auto">
          <div className="text-sm text-zinc-300 bg-zinc-800/60 border border-zinc-700
            rounded-lg px-4 py-3">
            Элементы, где правок в проекте нет, обновляются одной кнопкой. Там, где
            ваши правки встретились с новым шаблоном, выбор за вами: слить, взять
            шаблон или оставить свою версию. Прежнее содержимое перед перезаписью
            сохраняется в <code className="text-zinc-400">tasks/.taskboard/backup/</code>.
          </div>
          {!canMerge && (
            <div className="text-xs text-amber-300">
              git не найден — слияние недоступно, остаются «взять шаблон» и «оставить свою».
            </div>
          )}
          {error && <div className="text-sm text-rose-400">{error}</div>}
          {items === null && <div className="text-sm text-zinc-500">Загружаю…</div>}
          {items?.length === 0 && (
            <div className="text-sm text-emerald-300">Всё актуально</div>
          )}

          {items?.map((item) => {
            const k = key(item)
            const state = STATE_LABEL[item.state] || STATE_LABEL.unknown
            const diff = diffs[k]
            return (
              <div key={k} className="border border-zinc-800 rounded-xl">
                <div className="flex items-center gap-2 px-3 py-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm">
                      <span className="text-zinc-500 text-xs mr-2">{PART_LABEL[item.part]}</span>
                      <span className="font-medium">{item.name}</span>
                    </div>
                    <div className={`text-[11px] ${state.cls}`}>
                      {state.text}
                      {diff && ` · +${diff.added} / −${diff.removed}`}
                      {state.hint && <span className="text-zinc-500"> · {state.hint}</span>}
                      <span className="text-zinc-500">{baseHint(item)}</span>
                    </div>
                  </div>
                  <Button onClick={() => toggleDiff(item)} disabled={busy === k}>
                    {diff ? 'Скрыть' : 'Diff'}
                  </Button>
                  {actions(item)}
                </div>
                {diff && (
                  <div className="px-3 pb-3 space-y-2">
                    <div className="text-[11px] text-zinc-500 font-mono">{item.path}</div>
                    {item.state === 'conflict' ? (
                      <>
                        <div className="text-[11px] text-zinc-400">Что нового в шаблоне</div>
                        <DiffView text={diff.template_diff} />
                        <div className="text-[11px] text-zinc-400">Что своего в проекте</div>
                        <DiffView text={diff.local_diff} />
                      </>
                    ) : (
                      <>
                        <DiffView text={diff.diff} />
                        <div className="text-[11px] text-zinc-600">
                          «+» — появится после обновления, «−» — исчезнет
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}

          {/* Итоги: после массового слияния конфликты — единственное, что
              требует от человека действия, поэтому они выделены и посчитаны */}
          {conflicted.length > 0 && (
            <div className="text-sm text-rose-200 bg-rose-950/40 border border-rose-800/60
              rounded-lg px-4 py-3">
              Маркеры конфликтов остались в файлах ({conflicted.length}) — разрешите их
              вручную: {conflicted.join(', ')}
            </div>
          )}
          {Object.entries(outcomes).map(([k, result]) => (
            outcomeLine(result) && (
              <div
                key={k}
                className={`text-[11px] ${result.conflicts ? 'text-rose-300' : 'text-emerald-300'}`}
              >
                {k}: {outcomeLine(result)}
              </div>
            )
          ))}
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
