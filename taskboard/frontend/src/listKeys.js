import { useEffect, useState } from 'react'

// Клавиатура выпадающих списков: одно поведение на все списки интерфейса —
// подсказки задач и эпиков, выбор проекта в шапке, пресеты критериев.
//
// Копия обработчиков в каждом списке разъезжается молча: где-то Enter выбирает
// подсвеченное, где-то отправляет форму, где-то Esc закрывает окно вместо
// списка. Разбираться в этом приходится не разработчику, а человеку за доской.
//
// `onKeyDown` возвращает **было ли событие обработано**: место вызова решает,
// что делать с остальными нажатиями (у поля задачи Enter при закрытом списке
// отправляет форму, Esc — закрывает форму простоя).
//
//   const list = useListKeys({ items: suggestions, open, onPick: pick, onClose })
//   <input onKeyDown={list.onKeyDown} />
//   {items.map((item, i) => (
//     <button className={i === list.active ? '…' : '…'}
//             onMouseEnter={() => list.setActive(i)} />
//   ))}
export function useListKeys({ items = [], open = true, onPick, onClose, resetKey = '' }) {
  const [active, setActive] = useState(0)

  // Список сузился или открылся заново — подсветка возвращается на первую
  // строку: уехавший за край курсор выбрал бы не то, что видно
  useEffect(() => { setActive(0) }, [resetKey, open, items.length])

  const onKeyDown = (event) => {
    if (!open || !items.length) return false

    if (event.key === 'ArrowDown') {
      // preventDefault — иначе страница под списком уезжает вместе с подсветкой
      event.preventDefault()
      setActive((i) => (i + 1) % items.length)
      return true
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => (i - 1 + items.length) % items.length)
      return true
    }
    if (event.key === 'Enter') {
      // Enter при открытом списке выбирает подсвеченное, а не отправляет форму
      event.preventDefault()
      onPick?.(items[Math.min(active, items.length - 1)])
      return true
    }
    if (event.key === 'Escape') {
      // Esc снимает по одному слою: сначала список, потом окно под ним. Без
      // stopPropagation слушатель окна закрыл бы модалку целиком
      event.stopPropagation()
      onClose?.()
      return true
    }
    return false
  }

  return { active, setActive, onKeyDown }
}
