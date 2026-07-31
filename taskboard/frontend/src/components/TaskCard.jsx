import { useDraggable, useDroppable } from '@dnd-kit/core'
import { statusStyle } from '../statuses'
import { highlight } from '../highlight'

// Карточка задачи: draggable + droppable (дроп на карточку = вставка на её позицию)
// Полоска простоя у левого края карточки. Работа у неё периферийная — «в этой
// колонке что-то стоит», — поэтому причина кодируется цветом: блокировка
// красная, пауза жёлтая, а когда есть и то и другое, полоска делится пополам.
// Второй полоски у правого края не будет: там сразу зазор до соседней колонки,
// и вертикальная линия читалась бы как её граница.
//
// Не рамка (border-l), а отдельный элемент: у статуса свой hover:border-*,
// который красит рамку целиком и на наведении съедал бы полоску.
const STALL_STRIPE = {
  blocked: 'bg-rose-500/70',
  paused: 'bg-amber-400/70',
  both: 'bg-gradient-to-b from-rose-500/70 from-50% to-amber-400/70 to-50%',
  // Блокеры завершены: пометка ещё стоит, но держать нечему — приглушаем
  stale: 'bg-zinc-500/60',
}

function stripeKind(task) {
  if (task.stall_stale) return 'stale'
  const blocked = task.blocked_by?.length > 0
  if (blocked && task.paused) return 'both'
  if (blocked) return 'blocked'
  return task.paused ? 'paused' : null
}

export default function TaskCard({ task, status, onOpen, indicatorAllowed = true,
                                   query, match }) {
  const style = statusStyle(status)
  const stripe = stripeKind(task)
  const dragId = `task:${task.id}`
  const { attributes, listeners, setNodeRef: setDragRef, isDragging } = useDraggable({
    id: dragId,
    data: { taskId: task.id, fromStatus: status, task },
  })
  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `card:${status}:${task.id}`,
    data: { type: 'card', status, taskId: task.id },
  })

  return (
    <div ref={setDropRef} className="relative">
      {isOver && indicatorAllowed && (
        <div className="absolute -top-[5px] left-1 right-1 h-0.5 bg-sky-400 rounded-full
          shadow-[0_0_8px_rgba(56,189,248,0.9)] z-10 pointer-events-none" />
      )}
      <div
        ref={setDragRef}
        {...listeners}
        {...attributes}
        onClick={() => onOpen(task.id)}
        className={`${style.card} relative overflow-hidden border rounded-lg px-3.5 py-2.5
          cursor-grab ${style.cardHover} transition select-none touch-none
          outline-none ${style.cardFocus}
          ${isDragging ? 'opacity-40' : ''} ${task.struck ? 'opacity-45 border-dashed' : ''}`}
      >
        {stripe && (
          <span aria-hidden="true"
                className={`absolute left-0 top-0 bottom-0 w-0.5 ${STALL_STRIPE[stripe]}`} />
        )}
        {/* Верх карточки — состояние: номер и почему задача стоит. Взгляд идёт
            по колонке сверху вниз, и «чего она ждёт» должно читаться до
            заголовка. У блокировки номер важнее значка — сразу видно, чего
            ждём; у паузы номера нет, а слово «пауза» рядом со значком ничего
            не добавляет */}
        <div className="flex items-center gap-1.5 text-xs font-mono text-zinc-500">
          <span className="shrink-0">{task.id}</span>
          {task.struck && <span className="text-zinc-600 normal-case shrink-0">superseded</span>}
          {/* Значок — эмодзи: его цвет рисует шрифт, и text-* на него не
              действует. Чтобы приглушённая пометка выглядела приглушённой
              целиком, обесцвечиваем фильтром */}
          {task.blocked_by?.length > 0 && (
            <span className={`shrink-0 normal-case ${task.stall_stale
              ? 'text-zinc-500 grayscale opacity-80' : 'text-rose-400/90'}`}
                  title={task.stall_stale
                    ? `Блокеры завершены, пометку можно снять: ${task.blocked_by.join(', ')}`
                    : `Ждёт: ${task.blocked_by.join(', ')}`}>
              ⛔{task.blocked_by[0]}
              {/* «+N» жмётся к номеру блокера (узкий пробел, приглушённый цвет):
                  обычный пробел ставил его ровно посередине между блокировкой и
                  значком паузы, и было непонятно, к чему он относится */}
              {task.blocked_by.length > 1 && (
                <span className="text-rose-400/60">{' '}+{task.blocked_by.length - 1}</span>
              )}
            </span>
          )}
          {/* Пауза — в правый угол строки: место освободилось после переезда
              эпика, а у значка без номера постоянное место читается лучше, чем
              позиция, зависящая от того, есть ли рядом блокировка */}
          {task.paused && (
            <span className={`ml-auto shrink-0 normal-case ${task.stall_stale
              ? 'text-zinc-500 grayscale opacity-80' : 'text-amber-300/90'}`}
                  title={task.stall_stale
                    ? `Пометку можно снять: ${task.paused}`
                    : `Пауза: ${task.paused}`}>
              ⏸
            </span>
          )}
        </div>
        {/* Заголовок превью чуть мягче интерфейсных надписей: карточек на доске
            десятки, полная яркость превращает колонку в стену текста */}
        <div className="text-base text-zinc-300/90 leading-snug mt-0.5 line-clamp-2" title={task.title}>
          {highlight(task.title, query)}
        </div>
        {/* Нашлось в теле задачи — показываем, где именно: иначе непонятно,
            почему карточка попала в выдачу */}
        {match && !match.in_title && match.excerpt && (
          <div className="text-[11px] text-zinc-500 mt-1 line-clamp-2 leading-snug"
               title={match.excerpt}>
            {highlight(match.excerpt, query)}
          </div>
        )}
        {/* Низ карточки — справка: кто и когда трогал, к какому эпику относится.
            Эпик здесь, а не наверху: он самый широкий элемент строки и при этом
            самый редко нужный — наверху он вытеснял пометки простоя */}
        {(task.meta || task.epic) && (
          <div className="flex items-center gap-2 mt-1 text-xs text-zinc-500">
            {task.meta && <span className="truncate">{task.meta}</span>}
            {task.epic && (
              <span className="ml-auto min-w-0 shrink-0 truncate px-1.5 py-px rounded
                border border-zinc-700 text-[10px] font-mono text-zinc-400"
                    title={`Эпик ${task.epic}`}>
                {task.epic}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
