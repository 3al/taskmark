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

// Свёрнутая колонка: узкая полоса вместо списка задач.
//
// Это **отдельный компонент, а не спрятанная стилями колонка**, и в этом весь
// смысл: сворачивают обычно терминальные статусы, где со временем копятся сотни
// задач. `display: none` оставил бы в DOM все узлы, обработчики и регистрации
// dnd-kit — то есть ровно ту стоимость, ради которой колонку и сворачивали.
// Здесь карточки просто не монтируются.
//
// Целью дропа полоса остаётся: у неё та же дроп-зона `col:<статус>`, что у
// развёрнутой колонки, — иначе сворачивание было бы односторонним, задачу в
// такую колонку не положить.
export function CollapsedColumn({ column, count, activeFrom, dndFullBoard,
                                  pickStatus, createStatus, columnIndicator, reason, onExpand }) {
  const style = statusStyle(column.status)
  const { setNodeRef, isOver } = useDroppable({
    id: `col:${column.status}`,
    data: { type: 'column', status: column.status, title: column.title },
  })
  // Свёрнутая колонка переставляется так же, как развёрнутая: сворачивание —
  // это вид, а не потеря возможностей. Ручкой служит сама полоса — шапки, за
  // которую тянут развёрнутую, у неё нет.
  //
  // Клик по полосе при этом остаётся: drag начинается только после сдвига на
  // 6px (activationConstraint сенсора), поэтому «нажал и отпустил» разворачивает
  const {
    attributes, listeners, setNodeRef: setColDragRef, isDragging: isColDragging,
  } = useDraggable({
    id: `col-drag:${column.status}`,
    data: { type: 'columnHeader', status: column.status, title: column.title },
  })
  const canDrop = isDropAllowed(activeFrom, column.status, dndFullBoard, pickStatus, createStatus)
  const highlight = isOver && canDrop
  // Свёрнутая вручную и свёрнутая настройкой полосы неотличимы на глаз, поэтому
  // причину называет подсказка: без неё колонка, оставшаяся полосой после
  // выключения настройки, читается как сбой
  const why = reason === 'manual' ? 'свёрнута вручную' : 'свёрнута по настройке'

  return (
    // Фон и рамка задаются одним выражением, а не двумя классами поверх друг
    // друга: у полосы есть собственный фон (в отличие от тела развёрнутой
    // колонки), и второй `bg-*` не победил бы — порядок решает сгенерированный
    // CSS, а не строка className.
    // Подсветка цели заметнее, чем у обычной колонки, намеренно: площадь
    // маленькая, и «сюда можно отпустить» нужно прочитать боковым зрением
    <div ref={setNodeRef}
         className={`relative flex w-10 shrink-0 h-full min-h-0 rounded-xl border transition
      ${highlight
        ? 'bg-sky-500/15 border-sky-500/70 shadow-[0_0_12px_rgba(56,189,248,0.35)]'
        : `bg-zinc-900/40 ${style.border} ${activeFrom && canDrop ? 'border-dashed' : ''}`}
      ${isColDragging ? 'opacity-40' : ''}`}>
      {columnIndicator && (
        <div className={`absolute top-2 bottom-2 w-0.5 bg-sky-400 rounded-full z-10 pointer-events-none
          shadow-[0_0_8px_rgba(56,189,248,0.9)]
          ${columnIndicator === 'left' ? '-left-[9px]' : '-right-[9px]'}`} />
      )}
      <button
        ref={setColDragRef}
        {...listeners}
        {...attributes}
        type="button"
        onClick={onExpand}
        title={`${column.title} — ${why} · развернуть · перетащить, чтобы переставить`}
        className="flex w-full flex-col items-center gap-2 py-2.5 rounded-xl
          cursor-grab touch-none select-none hover:bg-zinc-800/50 transition">
        <span className={`w-2 h-2 shrink-0 rounded-full ${highlight ? 'bg-sky-400' : style.dot}`} />
        <span className={`shrink-0 text-sm ${highlight ? 'text-sky-300' : 'text-zinc-500'}`}>
          {count}
        </span>
        {/* Подпись лежит на боку, как корешок книги: слово остаётся словом и
            занимает по высоте столько же, сколько занимало бы по ширине */}
        <span className={`min-h-0 flex-1 text-base font-semibold ${style.header} truncate`}
              style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}>
          {column.title}
        </span>
      </button>
    </div>
  )
}

// Колонка статуса с группами (подразделы ###)
export default function Column({ column, onOpenTask, activeFrom, dndFullBoard, columnIndicator,
                                 pickStatus, createStatus, query, matches, filtered = false,
                                 onDelete, onOpenEpic, onCollapse, onTaskContextMenu }) {
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
      {/* Шапка делает два дела, и разделять их нечем: перетаскивание переставляет
          колонку, короткий клик сворачивает. Отдельной кнопки нет намеренно —
          свернуть кнопкой, а развернуть кликом по полосе было несимметрично:
          обратное действие искали там же, где прямое.
          Одно с другим не спорит: drag начинается только после сдвига на 6px
          (activationConstraint сенсора), поэтому «нажал и отпустил» — это клик */}
      <div
        ref={setColDragRef}
        {...listeners}
        {...attributes}
        onClick={onCollapse}
        className={`flex items-center gap-2 px-3 py-2.5 border-b ${style.border}
          cursor-grab touch-none select-none hover:bg-zinc-800/50 rounded-t-xl transition`}
        title="Свернуть колонку · перетащить, чтобы переставить"
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
                onDelete={onDelete}
                onOpenEpic={onOpenEpic}
                onContextMenu={onTaskContextMenu}
              />
            ))}
            {/* Под любым фильтром хвостовая зона врала бы: «после последней»
                указывало бы на последнюю показанную, а не на последнюю в разделе */}
            {!filtered && (
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
            {filtered ? 'нет совпадений' : 'пусто'}
          </div>
        )}
      </div>
    </div>
  )
}
