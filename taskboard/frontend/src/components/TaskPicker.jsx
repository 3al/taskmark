import { useEffect, useState } from 'react'
import { api } from '../api'
import { INLINE_FIELD } from '../fields'
import { useListKeys } from '../listKeys'

// Поле выбора задачи проекта: ввод фильтрует список, выбор подставляет номер.
// Список свой и тёмный, а не нативный datalist — браузер рисует его белым, и
// он выбивается из темы.
//
// Компонент общий: номер задачи вводят и при создании (blocked_by), и в
// открытой карточке (простановка блокировки). Две копии одной подсказки
// разъезжались бы по поведению — фильтру, оформлению, выбору с клавиатуры.
//
// **Выбирается ровно одна существующая задача.** Свободный ввод здесь — это
// ссылка в никуда: пользователь видит её результат не сразу, а предупреждением
// валидатора где-то потом. Поэтому наверх уходит не текст, а найденная задача
// (или null), и место вызова решает, что делать с «не выбрано».
export default function TaskPicker({
  value, onChange, placeholder = 'TASK-NNN', exclude = [], autoFocus = false,
  className = '', inputClassName = INLINE_FIELD, blockerFor = '', onEnter, onEscape,
}) {
  const [tasks, setTasks] = useState([])
  // Два разных состояния, и путать их нельзя: `focus` — курсор действительно в
  // поле (только события фокуса), `dismissed` — список закрыт по выбору или
  // Esc. Пока это был один флаг, закрытие списка выглядело как уход из поля:
  // человек стирал написанное, набирал заново — и подсказок больше не видел
  const [focus, setFocus] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    // Кем можно заблокировать — решает бэкенд: он знает и статусы задач, и
    // граф зависимостей, в котором прячутся циклы
    api.tasksList(blockerFor).then((d) => setTasks(d.items || []))
      .catch(() => { /* старый сервер */ })
  }, [blockerFor])

  const text = (value || '').trim()
  const needle = text.toLowerCase()
  const exact = tasks.find((t) => t.id.toLowerCase() === needle) || null

  // Точное совпадение — уже выбор: список подсказок закрываем, чтобы он не
  // висел поверх соседних полей
  const suggestions = needle && !exact
    ? tasks.filter((t) =>
        !exclude.includes(t.id) &&
        (t.id.toLowerCase().includes(needle) || (t.title || '').toLowerCase().includes(needle)))
    : []

  const open = focus && !dismissed && suggestions.length > 0

  // Выбранная задача уходит наверх вместе с текстом: место вызова не обязано
  // само сверять введённое со списком
  const emit = (next) => {
    // Правка текста — это новый поиск: закрытый список открываем обратно
    setDismissed(false)
    const found = tasks.find((t) => t.id.toLowerCase() === next.trim().toLowerCase()) || null
    onChange(next, found)
  }

  const pick = (task) => {
    onChange(task.id, task)
    setDismissed(true)
  }

  // Стрелки, Enter и Esc — общие для всех списков интерфейса
  const list = useListKeys({
    items: suggestions, open, resetKey: needle,
    onPick: pick, onClose: () => setDismissed(true),
  })

  const onKeyDown = (e) => {
    // Список забрал нажатие себе — дальше разбирать нечего
    if (list.onKeyDown(e)) return
    if (e.key === 'Enter' && onEnter) { e.preventDefault(); onEnter() }
    // Esc снимает по одному слою: список подсказок забирает его первым (в
    // хуке), закрытое поле отдаёт форме, в которой стоит. Дальше событие идёт
    // наружу само — иначе поле молча съедало бы Esc и окно переставало закрываться
    if (e.key === 'Escape' && onEscape) { e.stopPropagation(); onEscape() }
  }

  return (
    <div className={`relative ${className}`}>
      <input
        className={inputClassName}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => emit(e.target.value)}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        onKeyDown={onKeyDown}
      />
      {open && (
        <div className="absolute z-20 mt-1 w-full max-h-40 overflow-y-auto bg-zinc-800
          border border-zinc-700 rounded-lg shadow-xl">
          {suggestions.map((t, i) => (
            <button
              key={t.id}
              type="button"
              className={`w-full text-left px-3 py-1.5 text-xs
                ${i === list.active ? 'bg-zinc-700' : 'hover:bg-zinc-700'}`}
              // mouseDown, а не click: blur поля успел бы закрыть список раньше
              onMouseDown={() => pick(t)}
              onMouseEnter={() => list.setActive(i)}
            >
              <span className="font-mono text-zinc-300">{t.id}</span>
              {t.title && <span className="text-zinc-500"> · {t.title}</span>}
              {/* Статус кандидата: по нему видно, стоит ли вообще ждать */}
              {t.label && <span className="text-zinc-600"> · {t.label}</span>}
            </button>
          ))}
        </div>
      )}
      {/* Не совпало ни с одной задачей — говорим сразу, а не предупреждением
          валидатора после сохранения. Во время набора молчим: там ещё пол-номера.
          Подпись лежит вне потока: появившись, она не должна двигать соседние
          кнопки — строка ввода прыгала бы под руками */}
      {!focus && text && !exact && (
        <div className="absolute left-0 top-full mt-0.5 text-[11px] text-rose-400/80">
          Такой задачи нет — выберите из списка
        </div>
      )}
    </div>
  )
}
