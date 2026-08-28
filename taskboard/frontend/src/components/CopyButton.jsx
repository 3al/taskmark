import { useState } from 'react'

// Размеры кнопки: обычная стоит сама по себе, маленькая — в строке рядом с
// текстом (номер задачи), и там иконка должна быть ростом со шрифт, иначе она
// перетягивает строку на себя
const SIZES = {
  md: { box: 'w-5 h-5', icon: 'w-3.5 h-3.5' },
  sm: { box: 'w-4 h-4', icon: 'w-3 h-3' },
}

// Маленькая кнопка-иконка «копировать в буфер» с галочкой-подтверждением
export default function CopyButton({ text, title = 'Скопировать', size = 'md',
                                     className = '' }) {
  const [copied, setCopied] = useState(false)
  const { box, icon } = SIZES[size] || SIZES.md

  const copy = async (e) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch { /* буфер недоступен */ }
  }

  return (
    <button
      onClick={copy}
      title={copied ? 'Скопировано!' : title}
      className={`inline-flex items-center justify-center ${box} shrink-0 rounded transition
        ${copied ? 'text-emerald-400' : 'text-zinc-400 hover:text-zinc-300 hover:bg-zinc-700/60'}
        ${className}`}
    >
      {copied ? (
        <svg viewBox="0 0 16 16" className={icon} fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M2.5 8.5l3.5 3.5 7-8" />
        </svg>
      ) : (
        <svg viewBox="0 0 16 16" className={icon} fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="5.5" y="5.5" width="8" height="9" rx="1.5" />
          <path d="M10.5 5.5v-2A1.5 1.5 0 0 0 9 2H4a1.5 1.5 0 0 0-1.5 1.5V10A1.5 1.5 0 0 0 4 11.5h1.5" />
        </svg>
      )}
    </button>
  )
}
