import { useEffect, useLayoutEffect, useRef, useState } from 'react'

// Контекстное меню у курсора. Меню общее: что в нём — решает вызывающий,
// передавая `items`; здесь только показ, позиция и закрытие. Иначе каждое новое
// действие с карточки заводило бы своё меню со своими правилами закрытия.
//
// Элемент списка — либо действие `{ key, label, hint, dot, disabled, onSelect }`,
// либо заголовок группы `{ key, group }`: группа нужна, чтобы длинный
// однородный список (колонки переноса) читался, а не сливался с соседями.

// Отступ от края окна: меню, упёршееся в самый край, выглядит обрезанным
const EDGE = 8

export default function ContextMenu({ x, y, items, onClose }) {
  const ref = useRef(null)
  const [pos, setPos] = useState({ left: x, top: y })

  // Позицию правим после отрисовки: до неё размеры меню неизвестны, а у
  // правого края доски (там как раз последние колонки) оно уезжало за экран
  useLayoutEffect(() => {
    const box = ref.current?.getBoundingClientRect()
    if (!box) return
    const left = Math.max(EDGE, Math.min(x, window.innerWidth - box.width - EDGE))
    const top = Math.max(EDGE, Math.min(y, window.innerHeight - box.height - EDGE))
    setPos({ left, top })
  }, [x, y, items])

  // Закрытие: клавиша, клик мимо, прокрутка и смена размера окна. Прокрутка и
  // resize — потому что меню стоит в координатах экрана: доска уедет, а оно
  // останется висеть над чужой карточкой
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose() }
    }
    const onPointerDown = (e) => {
      if (!ref.current?.contains(e.target)) onClose()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('scroll', onClose, true)
    window.addEventListener('resize', onClose)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('scroll', onClose, true)
      window.removeEventListener('resize', onClose)
    }
  }, [onClose])

  return (
    <div
      ref={ref}
      role="menu"
      style={{ left: pos.left, top: pos.top }}
      // Правый клик по самому меню не должен открывать меню браузера поверх него
      onContextMenu={(e) => e.preventDefault()}
      // Меню плотное и мелкое: список колонок переноса длинный, а читают его
      // одним взглядом у курсора — воздух здесь только удлиняет дорогу мыши
      className="fixed z-50 min-w-[11rem] max-h-[70vh] overflow-y-auto py-0.5
        rounded-lg border border-zinc-700 bg-zinc-900 shadow-xl shadow-black/40 text-xs"
    >
      {items.map((item) => (item.group ? (
        <div key={item.key}
             className="px-2.5 pt-1.5 pb-0.5 text-[10px] uppercase tracking-wide text-zinc-500">
          {item.group}
        </div>
      ) : (
        <button
          key={item.key}
          type="button"
          role="menuitem"
          disabled={item.disabled}
          onClick={() => { onClose(); item.onSelect?.() }}
          className="w-full flex items-center gap-1.5 px-2.5 py-1 text-left transition
            text-zinc-200 hover:bg-zinc-800 disabled:text-zinc-600 disabled:hover:bg-transparent"
        >
          {item.dot && <span className={`w-1.5 h-1.5 shrink-0 rounded-full ${item.dot}`} />}
          <span className="truncate">{item.label}</span>
          {item.hint && <span className="ml-auto pl-2 text-[10px] text-zinc-500">{item.hint}</span>}
        </button>
      )))}
    </div>
  )
}
