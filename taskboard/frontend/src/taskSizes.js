// Размеры задачи: подписи и стили метки.
//
// Дубль каталога из backend/config.py (`TASK_SIZES`) — как палитры статусов и
// типов: классы Tailwind собираются статически, поэтому `text-${color}` не
// сработал бы. Согласованность списков проверяет тест test_task_size.py.
//
// Размер — оценка объёма («браться ли за это сейчас»), а не вид работы: за
// «что это за работа» отвечает тип. Метка нарочно монохромная — цветов на
// карточке уже хватает (тип, простой, долг, списание), а размер читается
// буквами. Различает их яркость: чем крупнее работа, тем заметнее метка.
export const TASK_SIZES = {
  S: {
    label: 'S', hint: 'мелкая правка, один заход',
    mark: 'text-zinc-500', badge: 'border-zinc-700/60 text-zinc-400',
  },
  M: {
    label: 'M', hint: 'обычная задача на сессию',
    mark: 'text-zinc-400', badge: 'border-zinc-600/60 text-zinc-300',
  },
  L: {
    label: 'L', hint: 'несколько сессий, лучше с планом',
    mark: 'text-zinc-400', badge: 'border-zinc-500/60 text-zinc-200',
  },
  XL: {
    label: 'XL', hint: 'стоит разбить на задачи',
    mark: 'text-zinc-200', badge: 'border-zinc-400/60 text-zinc-100',
  },
}

// Регистр приводим здесь: доска присылает канон, а окно задачи читает
// frontmatter, который правят руками — `size: l` это тот же размер
export function taskSize(key) {
  return TASK_SIZES[String(key || '').trim().toUpperCase()] || null
}

export const SIZE_KEYS = Object.keys(TASK_SIZES)
