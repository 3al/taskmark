// Типы задач: подписи и палитра меток.
//
// Дубль каталога из backend/config.py (`TASK_TYPES`) — как и палитры статусов,
// классы Tailwind собираются статически, поэтому `bg-${color}-500` не сработал
// бы и каждый цвет описан целиком. Согласованность списков проверяет тест
// test_task_type.py: разъехавшийся тип — метка, которая молча не рисуется.
//
// Тип — константа поставки, а не настройка проекта: «что это за работа» не
// зависит от жизненного цикла. letter — буква кружка на превью доски, места
// там ровно на один знак; буквы не повторяются.
export const TASK_TYPES = {
  feature: {
    label: 'Новый функционал', letter: 'Н',
    dot: 'bg-sky-500/20 text-sky-300 ring-sky-500/40',
    badge: 'border-sky-700/60 text-sky-300',
  },
  bug: {
    label: 'Баг', letter: 'Б',
    dot: 'bg-rose-500/20 text-rose-300 ring-rose-500/40',
    badge: 'border-rose-700/60 text-rose-300',
  },
  refactor: {
    label: 'Рефакторинг', letter: 'Р',
    dot: 'bg-violet-500/20 text-violet-300 ring-violet-500/40',
    badge: 'border-violet-700/60 text-violet-300',
  },
  cleanup: {
    label: 'Уборка', letter: 'У',
    dot: 'bg-emerald-500/20 text-emerald-300 ring-emerald-500/40',
    badge: 'border-emerald-700/60 text-emerald-300',
  },
  discussion: {
    label: 'Обсуждение', letter: 'О',
    dot: 'bg-amber-500/20 text-amber-300 ring-amber-500/40',
    badge: 'border-amber-700/60 text-amber-300',
  },
  design: {
    label: 'Дизайн', letter: 'Д',
    dot: 'bg-fuchsia-500/20 text-fuchsia-300 ring-fuchsia-500/40',
    badge: 'border-fuchsia-700/60 text-fuchsia-300',
  },
  review: {
    label: 'Код-ревью', letter: 'К',
    dot: 'bg-lime-500/20 text-lime-300 ring-lime-500/40',
    badge: 'border-lime-700/60 text-lime-300',
  },
}

// Задача без типа (заведена до его появления) и задача с чужим значением метки
// не получают: пустой кружок на превью хуже отсутствующего
export function taskType(key) {
  return TASK_TYPES[key] || null
}

export const TYPE_KEYS = Object.keys(TASK_TYPES)
