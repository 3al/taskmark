import { useDraggable, useDroppable } from '@dnd-kit/core'
import TaskCard from './TaskCard'
import { statusStyle, isDropAllowed } from '../statuses'

// Дроп-зона в конце подраздела: вставка после последней карточки
// (в пустом подразделе — по имени группы, т.к. якорной задачи нет)
function GroupTail({ status, groupIndex, afterTaskId, groupTitle, allowed }) {
  const { setNodeRef, isOver } = useDroppable({
    id: `tail:${status}:${groupIndex}`,
    data: { type: 'tail', status, afterTaskId, groupTitle },
  })
  return (
    <div ref={setNodeRef} className="relative h-3 -my-1">
      {isOver && allowed && (
        <div className="absolute top-1/2 -translate-y-1/2 left-1 right-1 h-0.5 bg-sky-400 rounded-full
          shadow-[0_0_8px_rgba(56,189,248,0.9)] pointer-events-none" />
      )}
    </div>
  )
}

// Колонка статуса с группами (подразделы ###)
export default function Column({ column, onOpenTask, activeFrom, dndFullBoard, columnIndicator,
                                 pickStatus, createStatus, query, matches }) {
  const style = statusStyle(column.status)
  const { setNodeRef, isOver } = useDroppable({
    id: `col:${column.status}`,
    data: { type: 'column', status: column.status, title: column.title },
  })
  // Перетаскивание самой колонки (за шапку) — порядок хранится на фронте
  const {
    attributes, listeners, setNodeRef: setColDragRef, isDragging: isColDragging,
  } = useDraggable({
    id: `col-drag:${column.status}`,
    data: { type: 'columnHeader', status: column.status, title: column.title },
  })

  const count = column.groups.reduce((n, g) => n + g.tasks.length, 0)
  const canDrop = isDropAllowed(activeFrom, column.status, dndFullBoard, pickStatus, createStatus)
  // Подсветка тела колонки только когда дроп разрешён и курсор не над карточкой
  const highlight = isOver && canDrop

  return (
    <div className={`relative flex flex-col w-72 shrink-0 h-full min-h-0 rounded-xl border ${style.border} bg-zinc-900/40
      ${isColDragging ? 'opacity-40' : ''}`}>
      {columnIndicator && (
        <div className={`absolute top-2 bottom-2 w-0.5 bg-sky-400 rounded-full z-10 pointer-events-none
          shadow-[0_0_8px_rgba(56,189,248,0.9)]
          ${columnIndicator === 'left' ? '-left-[9px]' : '-right-[9px]'}`} />
      )}
      <div
        ref={setColDragRef}
        {...listeners}
        {...attributes}
        className={`flex items-center gap-2 px-3 py-2.5 border-b ${style.border}
          cursor-grab touch-none select-none hover:bg-zinc-800/50 rounded-t-xl transition`}
        title="Перетащить колонку"
      >
        <span className={`w-2 h-2 rounded-full ${style.dot}`} />
        <span className={`text-base font-semibold ${style.header}`}>{column.title}</span>
        <span className="ml-auto text-sm text-zinc-500">{count}</span>
      </div>

      <div
        ref={setNodeRef}
        className={`flex-1 min-h-0 overflow-y-auto p-2 space-y-4 rounded-b-xl transition
          ${highlight ? 'bg-zinc-800/40' : ''}`}
      >
        {column.groups.map((group, gi) => (
          <div key={gi} className="space-y-1.5">
            {group.title && (
              <div className="text-xs uppercase tracking-wide text-zinc-500 px-1 pt-1">
                {group.title}
              </div>
            )}
            {group.tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                status={column.status}
                onOpen={onOpenTask}
                indicatorAllowed={canDrop}
                query={query}
                match={matches?.get(task.id)}
              />
            ))}
            {/* Под фильтром хвостовая зона врала бы: «после последней» указывало
                бы на последнюю найденную, а не на последнюю в разделе */}
            {!matches && (
              <GroupTail
                status={column.status}
                groupIndex={gi}
                afterTaskId={group.tasks.length ? group.tasks[group.tasks.length - 1].id : null}
                groupTitle={group.title}
                allowed={canDrop}
              />
            )}
            {!group.tasks.length && (
              <div className="text-xs text-zinc-600 italic px-1">пусто</div>
            )}
          </div>
        ))}
        {!column.groups.length && (
          <div className="text-xs text-zinc-600 italic px-1">
            {matches ? 'нет совпадений' : 'пусто'}
          </div>
        )}
      </div>
    </div>
  )
}
