// Визуальная дифференциация колонок и правила перетаскивания.
//
// Жизненный цикл задаёт пайплайн проекта (приходит в /api/board → config.pipeline):
// какие статусы включены, в каком порядке и каким цветом окрашены. Здесь — только
// палитры: Tailwind собирает классы статически, поэтому имена вроде `bg-${color}-400`
// не сработали бы и каждый цвет описан целиком.
//
// card/cardHover — превью-карточки: бледные оттенки в цвет колонки, с прозрачностью
// modalHeader — шапка полноразмерной карточки: сплошной, почти нейтральный тёмный
// фон с едва узнаваемым оттенком статуса (тело модалки остаётся нейтральным)
export const COLOR_STYLE = {
  zinc: {
    dot: 'bg-zinc-400', border: 'border-zinc-700', header: 'text-zinc-300',
    card: 'bg-zinc-900 border-zinc-800', cardHover: 'hover:bg-zinc-800/70 hover:border-zinc-600',
    modalHeader: 'bg-[#18181b] border-[#3f3f46]', mdTint: 'md-tint-zinc',
  },
  amber: {
    dot: 'bg-amber-400', border: 'border-amber-700/60', header: 'text-amber-300',
    card: 'bg-[#211d0f]/60 border-[#6e5a1f]/40', cardHover: 'hover:bg-[#211d0f]/80 hover:border-[#6e5a1f]/70',
    modalHeader: 'bg-[#211d0f] border-[#6e5a1f]', mdTint: 'md-tint-amber',
  },
  sky: {
    dot: 'bg-sky-400', border: 'border-sky-700/60', header: 'text-sky-300',
    card: 'bg-[#131a1f]/60 border-[#23465c]/40', cardHover: 'hover:bg-[#131a1f]/80 hover:border-[#23465c]/70',
    modalHeader: 'bg-[#131a1f] border-[#23465c]', mdTint: 'md-tint-sky',
  },
  violet: {
    dot: 'bg-violet-400', border: 'border-violet-700/60', header: 'text-violet-300',
    card: 'bg-[#171420]/60 border-[#3d2f5c]/40', cardHover: 'hover:bg-[#171420]/80 hover:border-[#3d2f5c]/70',
    modalHeader: 'bg-[#171420] border-[#3d2f5c]', mdTint: 'md-tint-violet',
  },
  orange: {
    dot: 'bg-orange-400', border: 'border-orange-700/60', header: 'text-orange-300',
    card: 'bg-[#1d150e]/60 border-[#57351d]/40', cardHover: 'hover:bg-[#1d150e]/80 hover:border-[#57351d]/70',
    modalHeader: 'bg-[#1d150e] border-[#57351d]', mdTint: 'md-tint-orange',
  },
  emerald: {
    dot: 'bg-emerald-400', border: 'border-emerald-700/60', header: 'text-emerald-300',
    card: 'bg-[#111a14]/60 border-[#1f4d33]/40', cardHover: 'hover:bg-[#111a14]/80 hover:border-[#1f4d33]/70',
    modalHeader: 'bg-[#111a14] border-[#1f4d33]', mdTint: 'md-tint-emerald',
  },
  yellow: {
    dot: 'bg-yellow-400', border: 'border-yellow-700/60', header: 'text-yellow-300',
    card: 'bg-[#1f1d0f]/60 border-[#5c541f]/40', cardHover: 'hover:bg-[#1f1d0f]/80 hover:border-[#5c541f]/70',
    modalHeader: 'bg-[#1f1d0f] border-[#5c541f]', mdTint: 'md-tint-yellow',
  },
  cyan: {
    dot: 'bg-cyan-400', border: 'border-cyan-700/60', header: 'text-cyan-300',
    card: 'bg-[#0f1c1f]/60 border-[#1f4d57]/40', cardHover: 'hover:bg-[#0f1c1f]/80 hover:border-[#1f4d57]/70',
    modalHeader: 'bg-[#0f1c1f] border-[#1f4d57]', mdTint: 'md-tint-cyan',
  },
  teal: {
    dot: 'bg-teal-400', border: 'border-teal-700/60', header: 'text-teal-300',
    card: 'bg-[#0f1c19]/60 border-[#1f4d43]/40', cardHover: 'hover:bg-[#0f1c19]/80 hover:border-[#1f4d43]/70',
    modalHeader: 'bg-[#0f1c19] border-[#1f4d43]', mdTint: 'md-tint-teal',
  },
  rose: {
    dot: 'bg-rose-400', border: 'border-rose-700/60', header: 'text-rose-300',
    card: 'bg-[#1f1216]/60 border-[#5c2333]/40', cardHover: 'hover:bg-[#1f1216]/80 hover:border-[#5c2333]/70',
    modalHeader: 'bg-[#1f1216] border-[#5c2333]', mdTint: 'md-tint-rose',
  },
  stone: {
    dot: 'bg-stone-500', border: 'border-stone-700/60', header: 'text-stone-400',
    card: 'bg-[#1c1917]/60 border-[#44403c]/40', cardHover: 'hover:bg-[#1c1917]/80 hover:border-[#44403c]/70',
    modalHeader: 'bg-[#1c1917] border-[#44403c]', mdTint: 'md-tint-stone',
  },
}

// Пайплайн активного проекта. Живёт в модуле, а не в пропсах: цвет статуса нужен
// карточкам и модалке в глубине дерева, а меняется он только при смене проекта
let pipeline = []

export function setPipeline(statuses) {
  pipeline = Array.isArray(statuses) ? statuses : []
}

export function pipelineKeys() {
  return pipeline.map((s) => s.key)
}

export function statusMeta(status) {
  return pipeline.find((s) => s.key === status) || null
}

export function statusLabel(status) {
  return statusMeta(status)?.label || status
}

export function statusStyle(status) {
  const color = statusMeta(status)?.color
  return COLOR_STYLE[color] || COLOR_STYLE.zinc
}

// Порядок колонок по умолчанию — порядок пайплайна (пользователь может
// переставить колонки сам, его выбор хранится в localStorage)
export function defaultColumnOrder() {
  return pipelineKeys()
}

// Правила перетаскивания мышью. Ограничения здесь мягкие и нужны только против
// случайного дропа: агент двигает задачи скриптом, где запретов нет вовсе.
// По умолчанию — перестановка внутри очереди и обмен с разделом приёма задач,
// с тумблером dnd_full_board — любое перемещение.
export function isDropAllowed(from, to, dndFullBoard, pickStatus, createStatus) {
  if (!from || !to) return false
  if (from === to) return dndFullBoard || to === pickStatus
  if (dndFullBoard) return true
  const pair = new Set([from, to])
  return pair.has(createStatus) && pair.has(pickStatus)
}
