// Визуальная дифференциация колонок и правила перетаскивания.
//
// Жизненный цикл задаёт пайплайн проекта (приходит в /api/board → config.pipeline):
// какие статусы включены, в каком порядке и каким цветом окрашены. Здесь — только
// палитры: Tailwind собирает классы статически, поэтому имена вроде `bg-${color}-400`
// не сработали бы и каждый цвет описан целиком.
//
// card/cardHover — превью-карточки: бледные оттенки в цвет колонки, с прозрачностью.
// У ховера **собственные hex**, а не тот же цвет под меньшей прозрачностью: подложки
// близки к фону колонки, и рост непрозрачности почти ничего не добавляет — наведение
// читалось бы по-разному в разных колонках. Осветление считается в CIE L*a*b* при
// сохранении оттенка, эталон силы отклика — `zinc`
// cardFocus — кольцо фокуса карточки. Карточка фокусируема (dnd-kit кладёт в неё
// tabIndex ради перетаскивания с клавиатуры), поэтому браузер рисует на ней свой
// дефолтный outline — толстый и белый. Своё кольцо тоньше и в цвет статуса
// modalHeader — шапка полноразмерной карточки: сплошной, почти нейтральный тёмный
// фон с едва узнаваемым оттенком статуса (тело модалки остаётся нейтральным)
export const COLOR_STYLE = {
  zinc: {
    dot: 'bg-zinc-400', border: 'border-zinc-700', header: 'text-zinc-300',
    card: 'bg-zinc-900 border-zinc-800', cardHover: 'hover:bg-zinc-800/70 hover:border-zinc-600',
    modalHeader: 'bg-[#18181b] border-[#3f3f46]', mdTint: 'md-tint-zinc',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-zinc-400/60',
  },
  amber: {
    dot: 'bg-amber-400', border: 'border-amber-700/60', header: 'text-amber-300',
    card: 'bg-[#211d0f]/60 border-[#6e5a1f]/40', cardHover: 'hover:bg-[#272416]/80 hover:border-[#947c40]/70',
    modalHeader: 'bg-[#211d0f] border-[#6e5a1f]', mdTint: 'md-tint-amber',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-amber-400/60',
  },
  sky: {
    dot: 'bg-sky-400', border: 'border-sky-700/60', header: 'text-sky-300',
    card: 'bg-[#131a1f]/60 border-[#23465c]/40', cardHover: 'hover:bg-[#1a2227]/80 hover:border-[#4f7188]/70',
    modalHeader: 'bg-[#131a1f] border-[#23465c]', mdTint: 'md-tint-sky',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-sky-400/60',
  },
  violet: {
    dot: 'bg-violet-400', border: 'border-violet-700/60', header: 'text-violet-300',
    card: 'bg-[#171420]/60 border-[#3d2f5c]/40', cardHover: 'hover:bg-[#1f1c29]/80 hover:border-[#6d5c8d]/70',
    modalHeader: 'bg-[#171420] border-[#3d2f5c]', mdTint: 'md-tint-violet',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-violet-400/60',
  },
  // Предрелизная заморозка состава: розово-сиреневый рядом с violet у заметок
  // релиза, но заметно теплее — и, главное, не зелёный. Зелёный на шаге перед
  // выпуском читается как «уже готово», хотя задача ещё не выпущена
  fuchsia: {
    dot: 'bg-fuchsia-400', border: 'border-fuchsia-700/60', header: 'text-fuchsia-300',
    card: 'bg-[#1d1220]/60 border-[#5c2f57]/40', cardHover: 'hover:bg-[#251a28]/80 hover:border-[#8f5c87]/70',
    modalHeader: 'bg-[#1d1220] border-[#5c2f57]', mdTint: 'md-tint-fuchsia',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-fuchsia-400/60',
  },
  orange: {
    dot: 'bg-orange-400', border: 'border-orange-700/60', header: 'text-orange-300',
    card: 'bg-[#1d150e]/60 border-[#57351d]/40', cardHover: 'hover:bg-[#251d18]/80 hover:border-[#876046]/70',
    modalHeader: 'bg-[#1d150e] border-[#57351d]', mdTint: 'md-tint-orange',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-orange-400/60',
  },
  emerald: {
    dot: 'bg-emerald-400', border: 'border-emerald-700/60', header: 'text-emerald-300',
    card: 'bg-[#111a14]/60 border-[#1f4d33]/40', cardHover: 'hover:bg-[#19221c]/80 hover:border-[#49775b]/70',
    modalHeader: 'bg-[#111a14] border-[#1f4d33]', mdTint: 'md-tint-emerald',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-emerald-400/60',
  },
  yellow: {
    dot: 'bg-yellow-400', border: 'border-yellow-700/60', header: 'text-yellow-300',
    card: 'bg-[#1f1d0f]/60 border-[#5c541f]/40', cardHover: 'hover:bg-[#252416]/80 hover:border-[#847943]/70',
    modalHeader: 'bg-[#1f1d0f] border-[#5c541f]', mdTint: 'md-tint-yellow',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-yellow-400/60',
  },
  cyan: {
    dot: 'bg-cyan-400', border: 'border-cyan-700/60', header: 'text-cyan-300',
    card: 'bg-[#0f1c1f]/60 border-[#1f4d57]/40', cardHover: 'hover:bg-[#162326]/80 hover:border-[#4a7681]/70',
    modalHeader: 'bg-[#0f1c1f] border-[#1f4d57]', mdTint: 'md-tint-cyan',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-cyan-400/60',
  },
  teal: {
    dot: 'bg-teal-400', border: 'border-teal-700/60', header: 'text-teal-300',
    card: 'bg-[#0f1c19]/60 border-[#1f4d43]/40', cardHover: 'hover:bg-[#162420]/80 hover:border-[#49776c]/70',
    modalHeader: 'bg-[#0f1c19] border-[#1f4d43]', mdTint: 'md-tint-teal',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-teal-400/60',
  },
  rose: {
    dot: 'bg-rose-400', border: 'border-rose-700/60', header: 'text-rose-300',
    card: 'bg-[#1f1216]/60 border-[#5c2333]/40', cardHover: 'hover:bg-[#281b1e]/80 hover:border-[#8f5160]/70',
    modalHeader: 'bg-[#1f1216] border-[#5c2333]', mdTint: 'md-tint-rose',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-rose-400/60',
  },
  stone: {
    dot: 'bg-stone-500', border: 'border-stone-700/60', header: 'text-stone-400',
    card: 'bg-[#1c1917]/60 border-[#44403c]/40', cardHover: 'hover:bg-[#23201e]/80 hover:border-[#706b67]/70',
    modalHeader: 'bg-[#1c1917] border-[#44403c]', mdTint: 'md-tint-stone',
    cardFocus: 'focus-visible:ring-1 focus-visible:ring-stone-400/60',
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

// Статус по его подписи. В файле задачи история переводов записана подписями —
// их читает человек, — а цвет known только по ключу. Подпись, которой в
// пайплайне уже нет (статус выключили после того, как задача через него
// прошла), остаётся текстом без цвета: врать оттенком чужого статуса хуже,
// чем не красить вовсе
export function statusStyleByLabel(label) {
  const norm = String(label || '').trim().toLowerCase()
  const meta = pipeline.find((s) => String(s.label || '').trim().toLowerCase() === norm)
  return meta ? COLOR_STYLE[meta.color] || COLOR_STYLE.zinc : null
}

// Порядок колонок по умолчанию — порядок пайплайна (пользователь может
// переставить колонки сам, его выбор хранится в localStorage)
export function defaultColumnOrder() {
  return pipelineKeys()
}

// Правила перетаскивания мышью. Ограничения здесь мягкие и нужны только против
// случайного дропа: агент двигает задачи скриптом, где запретов нет вовсе.
// Сортировка внутри колонки разрешена всегда; dndFullBoard регулирует
// только кросс-колоночные перемещения.
export function isDropAllowed(from, to, dndFullBoard, pickStatus, createStatus) {
  if (!from || !to) return false
  if (from === to) return true
  if (dndFullBoard) return true
  const pair = new Set([from, to])
  return pair.has(createStatus) && pair.has(pickStatus)
}
