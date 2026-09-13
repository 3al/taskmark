import { COLOR_STYLE } from '../statuses'

// Список задач среза — эпика, автора, исполнителя. Порядок строк задаёт бэкенд:
// **маршрут пайплайна**, а не номер задачи. Список читают как «где что едет»,
// поэтому цвет статуса здесь тот же, что у колонки доски, — глаз переносит
// смысл с доски без обучения.
export default function SliceTaskList({ tasks, onOpenTask }) {
  return tasks.map((task) => {
    // Статус, выключенный из пайплайна, приходит без цвета: красить его
    // чужим оттенком значит соврать о том, где задача едет
    const style = COLOR_STYLE[task.color]
    return (
      <button
        key={task.id}
        type="button"
        onClick={() => onOpenTask(task.id)}
        className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left
          transition ${style ? `${style.card} ${style.cardHover}`
                             : 'border-zinc-800 bg-zinc-900 hover:bg-zinc-800/70'}`}>
        <span className="shrink-0 font-mono text-xs text-zinc-400">{task.id}</span>
        <span className="min-w-0 flex-1 truncate text-sm text-zinc-300">
          {task.title}
        </span>
        <span className={`shrink-0 text-xs ${style ? style.header : 'text-zinc-400'}`}>
          {task.label}
        </span>
      </button>
    )
  })
}
