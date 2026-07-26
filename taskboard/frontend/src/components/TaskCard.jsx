import { useDraggable, useDroppable } from '@dnd-kit/core'
import { statusStyle } from '../statuses'

// Карточка задачи: draggable + droppable (дроп на карточку = вставка на её позицию)
export default function TaskCard({ task, status, onOpen, indicatorAllowed = true }) {
  const style = statusStyle(status)
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
        className={`${style.card} border rounded-lg px-3.5 py-2.5 cursor-grab
          ${style.cardHover} transition select-none touch-none
          ${isDragging ? 'opacity-40' : ''} ${task.struck ? 'opacity-45 border-dashed' : ''}`}
      >
        <div className="text-xs font-mono text-zinc-500">
          {task.id}
          {task.struck && <span className="ml-2 text-zinc-600 normal-case">superseded</span>}
        </div>
        <div className="text-base text-zinc-300 leading-snug mt-0.5 line-clamp-2" title={task.title}>
          {task.title}
        </div>
        {task.meta && <div className="text-xs text-zinc-500 mt-1 truncate">{task.meta}</div>}
      </div>
    </div>
  )
}
