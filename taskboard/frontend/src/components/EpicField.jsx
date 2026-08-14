import { useEffect, useState } from 'react'
import { api } from '../api'
import { INLINE_FIELD } from '../fields'

// Поле эпика с подсказками из реестра: ключ выбирают из списка, а не
// вспоминают. Компонент общий у формы создания и окна задачи — эпик там
// задают одинаково, и вторая форма ввода расходилась бы с первой при первой же
// правке.
//
// Имя эпика хранится только в реестре (`tasks/epics.md`), поэтому незнакомый
// ключ спрашивает имя: ссылка на безымянный эпик бесполезна. Поле имени
// показывает сам компонент — родителю знать, когда оно нужно, незачем.
//
// Наружу отдаётся и текст поля, и разобранный ключ: после выбора из списка в
// поле лежит «ключ · название» (так оно читается само), а на бэкенд уходит
// только ключ.
export default function EpicField({
  value, name = '', onChange, onNameChange, inputClassName = INLINE_FIELD,
  placeholder = 'Эпик — Jira-ключ (опционально)', autoFocus = false,
}) {
  const [epics, setEpics] = useState([])
  const [focus, setFocus] = useState(false)
  // Ключ, выбранный из списка: по тексту «ключ · название» его уже не найти
  const [picked, setPicked] = useState(null)

  useEffect(() => {
    api.epics().then((d) => setEpics(d.items || [])).catch(() => { /* нет реестра */ })
  }, [])

  const label = (e) => (e.name ? `${e.key} · ${e.name}` : e.key)
  const key = (value || '').trim()
  const known = picked
    ? epics.find((e) => e.key === picked)
    : epics.find((e) => e.key.toLowerCase() === key.toLowerCase())
  const needsName = !!key && !known
  const needle = (picked || key).toLowerCase()
  const suggestions = epics.filter((e) =>
    e.key.toLowerCase().includes(needle) || (e.name || '').toLowerCase().includes(needle))

  return (
    // min-w-0 у обоих полей: у инпута есть интринсическая ширина, и без неё
    // появление второго поля схлопывает первое до полусантиметра
    <div className="flex gap-3">
      <div className="relative flex-1 min-w-0">
        <input
          className={inputClassName}
          placeholder={placeholder}
          value={value}
          autoFocus={autoFocus}
          onChange={(e) => { setPicked(null); onChange(e.target.value, '') }}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
        />
        {focus && !known && suggestions.length > 0 && (
          <div className="absolute z-20 mt-1 w-full max-h-40 overflow-y-auto bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl">
            {suggestions.map((e) => (
              <button
                key={e.key}
                type="button"
                className="w-full text-left px-3 py-1.5 text-xs hover:bg-zinc-700"
                // mousedown, а не click: blur поля успевает закрыть список
                onMouseDown={() => {
                  setPicked(e.key)
                  onChange(label(e), e.key)
                  setFocus(false)
                }}
              >
                <span className="font-mono text-zinc-300">{e.key}</span>
                {e.name && <span className="text-zinc-500"> · {e.name}</span>}
              </button>
            ))}
          </div>
        )}
      </div>
      {needsName && (
        <input
          className={`${inputClassName} flex-1 min-w-0`}
          placeholder="Название нового эпика"
          value={name}
          onChange={(e) => onNameChange?.(e.target.value)}
        />
      )}
    </div>
  )
}
