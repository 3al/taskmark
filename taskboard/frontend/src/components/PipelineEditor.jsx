import { useState } from 'react'
import { COLOR_STYLE } from '../statuses'

// Роли действий: подписи бейджей рядом со статусом-целью
const ACTION_LABEL = {
  create: 'создание',
  pick: 'очередь',
  start: 'в работу',
  return: 'возврат',
  release_draft: 'черновик релиза',
  release_lock: 'в релиз',
}

// Поля формы действий. Обязательные заполнены всегда, необязательные нужны
// только тем, кто выпускает версии, — им доступен пустой вариант
const ACTION_FIELDS = [
  { name: 'create', title: 'Новая задача' },
  { name: 'start', title: 'Взять в работу' },
  { name: 'return', title: 'Вернуть после замечаний' },
  { name: 'pick', title: 'Брать работу из', empty: 'авто (перед «в работу»)' },
  { name: 'release_draft', title: 'Готовить заметки релиза', empty: 'не используется' },
  { name: 'release_lock', title: 'Отобрано в релиз', empty: 'не используется' },
]

// Редактор жизненного цикла: порядок статусов и цели действий скиллов.
// Порядок задаёт маршрут (что идёт за чем), а не запреты: прыжки вперёд и
// возвраты назад законны в любом случае.
export default function PipelineEditor({ pipeline, actions, catalog, sources, onChange, onOpenHelp }) {
  const keys = pipeline.map((s) => s.key)
  const available = catalog.filter((c) => !keys.includes(c.key))

  // Чем заполнен маршрут — видно в поле выбора. Ручная правка сбрасывает его:
  // после неё в форме уже не тот маршрут, что у источника (и заодно источник
  // можно выбрать повторно, чтобы вернуться к нему)
  const [picked, setPicked] = useState('')

  const emit = (nextPipeline, nextActions = actions, nextStatuses) => {
    setPicked('')
    onChange({ pipeline: nextPipeline, actions: nextActions, statuses: nextStatuses })
  }

  // Готовый маршрут подставляется в форму, а не сохраняется: дальше его можно
  // поправить, а применится он обычным «Сохранить» — с переносом задач из
  // выключаемых статусов, как при ручной правке
  const applySource = (value) => {
    const source = sources?.[Number(value)]
    if (!source) return
    emit(source.pipeline, source.actions, source.statuses || {})
    setPicked(value)
  }

  const move = (index, delta) => {
    const next = [...pipeline]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    emit(next)
  }

  const remove = (index) => {
    const dropped = pipeline[index].key
    const next = pipeline.filter((_, i) => i !== index)
    // Действие не может указывать на выключенный статус — сбрасываем на авто
    const cleaned = Object.fromEntries(
      Object.entries(actions).filter(([, value]) => value !== dropped))
    emit(next, cleaned)
  }

  // Статус из каталога встаёт на своё каноническое место, а не в конец:
  // local_testing между разработкой и ревью, cancelled — последним.
  // Порядок потом всё равно можно поправить стрелками
  const add = (key) => {
    const meta = catalog.find((c) => c.key === key)
    if (!meta) return
    const order = catalog.map((c) => c.key)
    const at = pipeline.findIndex((s) => {
      const i = order.indexOf(s.key)
      return i > order.indexOf(key)  // кастомные статусы (-1) не сдвигаем
    })
    const next = [...pipeline]
    next.splice(at === -1 ? next.length : at, 0, meta)
    emit(next)
  }

  const setAction = (name, value) =>
    emit(pipeline, { ...actions, [name]: value || undefined })

  const label = 'block text-xs text-zinc-500 mb-1'
  const field = 'w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:border-sky-500'

  const presets = (sources || []).filter((s) => s.kind === 'preset')
  const projects = (sources || []).filter((s) => s.kind === 'project')

  return (
    <div className="space-y-3">
      {sources?.length > 0 && (
        <div>
          <span className={label}>Взять готовый маршрут</span>
          <select className={field} value={picked}
                  onChange={(e) => applySource(e.target.value)}>
            <option value="">Заполнить из…</option>
            {presets.length > 0 && (
              <optgroup label="Пресеты">
                {presets.map((s) => (
                  <option key={s.name} value={sources.indexOf(s)}>
                    {s.name} — {s.hint}
                  </option>
                ))}
              </optgroup>
            )}
            {projects.length > 0 && (
              <optgroup label="Другие проекты">
                {projects.map((s) => (
                  <option key={s.name} value={sources.indexOf(s)}>
                    {s.name} ({s.pipeline.map((p) => p.label).join(' → ')})
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          <div className="text-[11px] text-zinc-600 mt-1">
            Подставит статусы и действия в форму — сохранение по кнопке ниже
          </div>
        </div>
      )}

      <div>
        <span className={label}>Статусы по порядку — он задаёт ожидаемый маршрут задачи</span>
        <div className="space-y-1">
          {pipeline.map((status, i) => {
            const style = COLOR_STYLE[status.color] || COLOR_STYLE.zinc
            const roles = Object.entries(actions)
              .filter(([, value]) => value === status.key)
              .map(([name]) => ACTION_LABEL[name])
              .filter(Boolean)
            return (
              <div key={status.key}
                   className="flex items-center gap-2 bg-zinc-800/50 border border-zinc-700/60 rounded-lg px-2 py-1.5">
                <span className={`w-2 h-2 rounded-full shrink-0 ${style.dot}`} />
                <span className="text-sm truncate">{status.label}</span>
                <span className="text-[11px] text-zinc-600 truncate">{status.key}</span>
                {status.offramp && (
                  <span className="text-[10px] text-zinc-500 border border-zinc-700 rounded px-1">
                    вне маршрута
                  </span>
                )}
                {roles.map((role) => (
                  <span key={role}
                        className="text-[10px] text-sky-300 border border-sky-800/60 rounded px-1">
                    {role}
                  </span>
                ))}
                <div className="ml-auto flex items-center gap-0.5 shrink-0">
                  <button onClick={() => move(i, -1)} disabled={i === 0}
                          title="Выше"
                          className="px-1.5 text-zinc-500 hover:text-zinc-200 disabled:opacity-30">↑</button>
                  <button onClick={() => move(i, 1)} disabled={i === pipeline.length - 1}
                          title="Ниже"
                          className="px-1.5 text-zinc-500 hover:text-zinc-200 disabled:opacity-30">↓</button>
                  <button onClick={() => remove(i)} title="Убрать из пайплайна"
                          className="px-1.5 text-zinc-500 hover:text-rose-400">✕</button>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {available.length > 0 && (
        <div>
          <span className={label}>Добавить статус</span>
          <select className={field} value="" onChange={(e) => add(e.target.value)}>
            <option value="">Выберите статус…</option>
            {available.map((c) => (
              <option key={c.key} value={c.key}>{c.label} ({c.key})</option>
            ))}
          </select>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        {ACTION_FIELDS.map(({ name, title, empty }) => (
          <div key={name}>
            <span className={label}>{title}</span>
            <select className={field} value={actions[name] || ''}
                    onChange={(e) => setAction(name, e.target.value)}>
              {empty && <option value="">{empty}</option>}
              {pipeline.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </div>
        ))}
      </div>

      <div className="text-[11px] text-zinc-600 space-y-1">
        <div>
          Порядок — ожидаемый маршрут задачи, а не запрет: шаг через статус и возврат
          назад законны. Скиллы берут цели отсюда, поэтому выключенный статус исчезает
          и из их вариантов.
        </div>
        <div>
          Выключение статуса с задачами спросит, куда их перенести.
          {onOpenHelp && (
            <button className="ml-1 underline hover:text-zinc-400"
                    onClick={() => onOpenHelp('lifecycle')}>
              Подробнее о жизненном цикле
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
