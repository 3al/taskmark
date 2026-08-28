import { useEffect, useState } from 'react'
import { api } from '../api'
import { INLINE_FIELD } from '../fields'
import { useListKeys } from '../listKeys'

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
  // Список закрыт по Esc. Отдельно от фокуса: пока это был один флаг, закрытие
  // списка выглядело как уход из поля — человек стирал написанное, набирал
  // заново и подсказок больше не видел
  const [dismissed, setDismissed] = useState(false)

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
  // Пустой ввод — пустой список: подсказки появляются с началом набора, как в
  // поле блокирующей задачи. Правило одно на все поля с подсказками, иначе
  // соседние поля ведут себя по-разному без видимой причины.
  // Выбранный эпик список тоже закрывает: он уже не подсказка, а выбор
  const suggestions = needle && !known
    ? epics.filter((e) =>
        e.key.toLowerCase().includes(needle) || (e.name || '').toLowerCase().includes(needle))
    : []

  const open = focus && !dismissed && suggestions.length > 0

  const pick = (epic) => {
    setPicked(epic.key)
    onChange(label(epic), epic.key)
    setFocus(false)
  }

  // Стрелки, Enter и Esc — общие для всех списков интерфейса
  const list = useListKeys({
    items: suggestions, open, resetKey: needle,
    onPick: pick, onClose: () => setDismissed(true),
  })

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
          // Правка текста — это новый поиск: закрытый список открываем обратно
          onChange={(e) => { setPicked(null); setDismissed(false); onChange(e.target.value, '') }}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          onKeyDown={list.onKeyDown}
        />
        {open && (
          <div className="absolute z-20 mt-1 w-full max-h-40 overflow-y-auto bg-zinc-800 border border-zinc-700 rounded-lg shadow-xl">
            {suggestions.map((e, i) => (
              <button
                key={e.key}
                type="button"
                className={`w-full text-left px-3 py-1.5 text-xs
                  ${i === list.active ? 'bg-zinc-700' : 'hover:bg-zinc-700'}`}
                // mousedown, а не click: blur поля успевает закрыть список
                onMouseDown={() => pick(e)}
                onMouseEnter={() => list.setActive(i)}
              >
                <span className="font-mono text-zinc-300">{e.key}</span>
                {e.name && <span className="text-zinc-400"> · {e.name}</span>}
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
