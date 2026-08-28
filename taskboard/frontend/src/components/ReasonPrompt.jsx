import { useEffect, useRef, useState } from 'react'
import { INLINE_FIELD } from '../fields'

// Ввод причины одной строкой: пауза, отмена — любое действие, которое человек
// потом будет читать в файле задачи. Компонент общий намеренно: причина везде
// вводится одинаково, и расхождение форм ввода само по себе сбивает с толку.
//
// Причина хранится в одной строке frontmatter, поэтому перенос схлопывается
// прямо на вводе — иначе он разорвал бы шапку файла.
export default function ReasonPrompt({
  label, placeholder = '', value = '', submitLabel = 'Сохранить', busy = false,
  inputClassName = INLINE_FIELD, buttonClassName = '', onSubmit, onCancel,
}) {
  const [text, setText] = useState(value)
  const ref = useRef(null)

  useEffect(() => { ref.current?.focus() }, [])

  const submit = () => {
    const reason = text.replace(/\s+/g, ' ').trim()
    if (!reason || busy) return
    onSubmit(reason)
  }

  return (
    <div className="flex items-center gap-2">
      {label && <span className="text-xs text-zinc-400 shrink-0">{label}</span>}
      <input
        ref={ref}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        disabled={busy}
        className={`flex-1 min-w-0 disabled:opacity-60 ${inputClassName}`}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); submit() }
          // Esc гасим здесь: иначе слушатель окна закроет всю модалку
          if (e.key === 'Escape') { e.stopPropagation(); onCancel() }
        }}
      />
      <button
        onClick={submit}
        disabled={busy || !text.trim()}
        className={buttonClassName || `shrink-0 px-2 py-1 text-xs rounded-lg border
          border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:bg-zinc-800
          disabled:opacity-40 transition`}
      >
        {submitLabel}
      </button>
      <button
        onClick={onCancel}
        className="shrink-0 px-1.5 py-1 text-xs text-zinc-400 hover:text-zinc-200 transition"
        title="Отменить (Esc)"
      >
        ✕
      </button>
    </div>
  )
}
