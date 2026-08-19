import { useEffect, useState } from 'react'
import { api } from '../api'
import { INLINE_FIELD } from '../fields'
import { useListKeys } from '../listKeys'

// Исполнитель задачи: кто занимается ею на этапе проверки. Метка-кнопка в
// строке типа и размера, за ней — поле с подсказками.
//
// Список имён **открытый**: людей поставка не знает, их приносит работа.
// Поэтому здесь не выбор из закрытого каталога (как тип и размер), а ввод с
// подсказками: набранное имя запоминается на машине и дальше предлагается во
// всех проектах.
//
// Пустой ввод — пустой список: подсказки появляются с началом набора, как в
// поле эпика и блокирующей задачи. Совпавшее целиком имя список закрывает —
// это уже не подсказка, а выбор.
export default function AssigneeField({ value = '', busy = false, onPick }) {
  const [open, setOpen] = useState(false)
  const [names, setNames] = useState([])
  const [text, setText] = useState('')
  // Список закрыт по Esc — отдельно от самого поля: иначе первое же Esc
  // убирает форму, и человек не понимает, куда делось начатое имя
  const [dismissed, setDismissed] = useState(false)

  // Список спрашиваем при каждом открытии: имя могли завести в соседнем
  // проекте, а окно живёт ровно столько, сколько открыта задача
  useEffect(() => {
    if (!open) return
    api.assignees().then((d) => setNames(d.items || [])).catch(() => setNames([]))
  }, [open])

  const start = () => { setText(value || ''); setDismissed(false); setOpen(true) }
  const close = () => { setOpen(false); setDismissed(false) }

  const needle = text.trim().toLowerCase()
  const exact = names.some((n) => n.toLowerCase() === needle)
  const suggestions = needle && !exact
    ? names.filter((n) => n.toLowerCase().includes(needle))
    : []

  const save = (name) => {
    close()
    if ((name || '').trim() !== (value || '').trim()) onPick?.((name || '').trim())
  }

  const list = useListKeys({
    items: suggestions, open: open && !dismissed && suggestions.length > 0,
    resetKey: needle, onPick: save, onClose: () => setDismissed(true),
  })

  return (
    <span className="relative">
      <button
        type="button"
        onClick={() => (open ? close() : start())}
        disabled={busy}
        title="Кто занимается задачей на этом этапе"
        className={`px-1.5 py-px rounded border text-[10px] transition
          hover:brightness-125 disabled:opacity-60
          ${value
            ? 'border-zinc-600 bg-zinc-700/40 text-zinc-300'
            : 'border-dashed border-zinc-700 text-zinc-500'}`}>
        {value || 'без исполнителя'}
      </button>
      {open && (
        <>
        {/* Подложка на весь экран: клик мимо формы гасится здесь и дальше не
            идёт — ни к фону модалки, ни к тексту задачи */}
        <span className="fixed inset-0 z-40"
              onClick={(e) => { e.stopPropagation(); close() }} />
        <span className="absolute left-0 top-full mt-1 z-50 w-56 block
          rounded-lg border border-zinc-700 bg-zinc-900 p-1.5 shadow-xl">
          <input
            className={INLINE_FIELD}
            placeholder="Имя исполнителя"
            value={text}
            autoFocus
            onChange={(e) => { setDismissed(false); setText(e.target.value) }}
            onKeyDown={(e) => {
              if (list.onKeyDown(e)) return
              // Enter при закрытом списке сохраняет набранное: имя может быть
              // новым, и подсказки для него взяться неоткуда
              if (e.key === 'Enter') { e.preventDefault(); save(text) }
              if (e.key === 'Escape') { e.stopPropagation(); close() }
            }}
          />
          {suggestions.length > 0 && !dismissed && (
            <span className="mt-1 block max-h-40 overflow-y-auto">
              {suggestions.map((name, i) => (
                <button
                  key={name}
                  type="button"
                  className={`w-full text-left px-2 py-1 text-xs rounded
                    ${i === list.active ? 'bg-zinc-700' : 'hover:bg-zinc-700'}`}
                  // mousedown, а не click: blur поля успевает закрыть список
                  onMouseDown={() => save(name)}
                  onMouseEnter={() => list.setActive(i)}>
                  {name}
                </button>
              ))}
            </span>
          )}
          {/* Снять назначение: человек мог уйти с задачи, а задача остаться */}
          {value && (
            <button
              type="button"
              onClick={() => save('')}
              className="mt-1 w-full text-left px-2 py-1 text-[10px] rounded
                border border-dashed border-zinc-700 text-zinc-500 hover:text-zinc-300">
              снять назначение
            </button>
          )}
        </span>
        </>
      )}
    </span>
  )
}
