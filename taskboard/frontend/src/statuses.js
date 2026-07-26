// Визуальная дифференциация статусов колонок
// card/cardHover — превью-карточки: бледные оттенки в цвет колонки, с прозрачностью
// modalHeader — шапка полноразмерной карточки: сплошной, почти нейтральный тёмный
// фон с едва узнаваемым оттенком статуса (тело модалки остаётся нейтральным)
export const STATUS_STYLE = {
  backlog: {
    label: 'Backlog', dot: 'bg-zinc-400', border: 'border-zinc-700', header: 'text-zinc-300',
    card: 'bg-zinc-900 border-zinc-800', cardHover: 'hover:bg-zinc-800/70 hover:border-zinc-600',
    modalHeader: 'bg-[#18181b] border-[#3f3f46]', mdTint: 'md-tint-zinc',
  },
  queued: {
    label: 'Очередь', dot: 'bg-amber-400', border: 'border-amber-700/60', header: 'text-amber-300',
    card: 'bg-[#211d0f]/60 border-[#6e5a1f]/40', cardHover: 'hover:bg-[#211d0f]/80 hover:border-[#6e5a1f]/70',
    modalHeader: 'bg-[#211d0f] border-[#6e5a1f]', mdTint: 'md-tint-amber',
  },
  development: {
    label: 'Development', dot: 'bg-sky-400', border: 'border-sky-700/60', header: 'text-sky-300',
    card: 'bg-[#131a1f]/60 border-[#23465c]/40', cardHover: 'hover:bg-[#131a1f]/80 hover:border-[#23465c]/70',
    modalHeader: 'bg-[#131a1f] border-[#23465c]', mdTint: 'md-tint-sky',
  },
  review: {
    label: 'Review', dot: 'bg-violet-400', border: 'border-violet-700/60', header: 'text-violet-300',
    card: 'bg-[#171420]/60 border-[#3d2f5c]/40', cardHover: 'hover:bg-[#171420]/80 hover:border-[#3d2f5c]/70',
    modalHeader: 'bg-[#171420] border-[#3d2f5c]', mdTint: 'md-tint-violet',
  },
  testing: {
    label: 'Testing', dot: 'bg-orange-400', border: 'border-orange-700/60', header: 'text-orange-300',
    card: 'bg-[#1d150e]/60 border-[#57351d]/40', cardHover: 'hover:bg-[#1d150e]/80 hover:border-[#57351d]/70',
    modalHeader: 'bg-[#1d150e] border-[#57351d]', mdTint: 'md-tint-orange',
  },
  completed: {
    label: 'Completed', dot: 'bg-emerald-400', border: 'border-emerald-700/60', header: 'text-emerald-300',
    card: 'bg-[#111a14]/60 border-[#1f4d33]/40', cardHover: 'hover:bg-[#111a14]/80 hover:border-[#1f4d33]/70',
    modalHeader: 'bg-[#111a14] border-[#1f4d33]', mdTint: 'md-tint-emerald',
  },
}

export function statusStyle(status) {
  return STATUS_STYLE[status] || STATUS_STYLE.backlog
}

// Логичный порядок движения статусов (дефолт, пока пользователь не перетащил)
export function defaultColumnOrder(queuedStatus = 'queued') {
  return ['backlog', queuedStatus, 'development', 'review', 'testing', 'completed']
}

// Правила перетаскивания: по умолчанию Backlog↔Queue и reorder в очереди,
// с тумблером dnd_full_board — всё. Статус очереди приходит из конфига.
export function isDropAllowed(from, to, dndFullBoard, queuedStatus = 'queued') {
  if (!from || !to) return false
  if (from === to) return dndFullBoard || to === queuedStatus
  if (dndFullBoard) return true
  const pair = new Set([from, to])
  return pair.has('backlog') && pair.has(queuedStatus)
}
