import { useCallback, useEffect, useRef, useState } from 'react'

// Всплывашки службы уведомлений: верхний угол доски, стопка, исчезание по таймеру.
//
// Уведомление говорит о том, что случилось **сейчас**, и работе не мешает:
// контейнер сквозной для мыши (`pointer-events-none`), кликается только сама
// карточка. Иначе полоса в углу перехватывала бы перетаскивание задач.

// Сколько живёт уведомление без вмешательства, когда настройка не пришла.
// Кому не хватило, тот наводит мышь: наведение таймер приостанавливает.
// Ноль в настройке означает «не гасить само» — уведомление ждёт крестика
const DEFAULT_LIFETIME_MS = 6000
// Длительность ухода: столько карточка едет вправо, прежде чем исчезнуть
const LEAVE_MS = 200

// Уровень — единственное, чем уведомления отличаются на вид. Решений по нему
// не принимают: это тон сообщения, а не его важность в очереди
const LEVELS = {
  info: { ring: 'ring-sky-500/30', dot: 'bg-sky-400', title: 'text-sky-200' },
  success: { ring: 'ring-emerald-500/30', dot: 'bg-emerald-400', title: 'text-emerald-200' },
  warning: { ring: 'ring-amber-500/30', dot: 'bg-amber-400', title: 'text-amber-200' },
}

function Notice({ notice, activeProject, lifetime, onClose }) {
  const [leaving, setLeaving] = useState(false)
  // Пауза — **состояние**, а не снятый по месту таймер: таймер тогда живёт
  // в эффекте, и React сам снимает его при паузе и заводит заново при
  // возобновлении. Остаток времени переживает и то, и другое
  const [paused, setPaused] = useState(false)
  const left = useRef(lifetime)

  const close = useCallback(() => {
    setLeaving(true)
    setTimeout(() => onClose(notice.id), LEAVE_MS)
  }, [notice.id, onClose])

  useEffect(() => {
    // Ноль — «не гасить само»: таймера просто нет, уведомление ждёт крестика.
    // Уходящая карточка досчитывать тоже не должна
    if (!lifetime || paused || leaving) return
    const started = Date.now()
    const timer = setTimeout(close, left.current)
    return () => {
      clearTimeout(timer)
      // Списываем прожитое: наведение мыши **приостанавливает** отсчёт, а не
      // сбрасывает его — иначе отвлёкшийся человек получал бы полный срок
      // заново при каждом движении мыши
      left.current = Math.max(0, left.current - (Date.now() - started))
    }
  }, [paused, leaving, lifetime, close])

  const level = LEVELS[notice.level] || LEVELS.info
  // Имя проекта показываем только у чужого: сервер один на весь реестр, и
  // задача из чата приезжает в проект, который прямо сейчас не открыт
  const foreign = notice.project && notice.project !== activeProject

  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className={`pointer-events-auto w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg
        bg-zinc-900/95 ring-1 ${level.ring} shadow-lg shadow-black/40 backdrop-blur-sm
        ${leaving ? 'animate-notice-out' : 'animate-notice-in'}`}
    >
      <div className="flex items-start gap-2 px-3 py-2">
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${level.dot}`} />
        <div className="min-w-0 flex-1">
          <div className={`text-sm font-medium ${level.title}`}>{notice.title}</div>
          {notice.text && (
            <div className="mt-0.5 text-xs text-zinc-300 break-words">{notice.text}</div>
          )}
          {foreign && (
            <div className="mt-1 text-[11px] text-zinc-400">Проект: {notice.project}</div>
          )}
        </div>
        <button
          onClick={close}
          title="Закрыть"
          className="shrink-0 rounded px-1 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800"
        >
          ✕
        </button>
      </div>
      {/* Полоска отсчёта: она же и ответ на вопрос «пауза вообще работает?».
          Ширину ведёт CSS-анимация той же длительности, что и таймер, а
          наведение останавливает её тем же событием — человек видит, что
          отсчёт замер и продолжился с места, а не начался заново */}
      {!!lifetime && (
        <div
          className={`h-0.5 ${level.dot} opacity-40 notice-bar`}
          style={{ animationDuration: `${lifetime}ms`, animationPlayState: paused ? 'paused' : 'running' }}
        />
      )}
    </div>
  )
}

export default function Notices({ items, activeProject, seconds, onClose }) {
  if (!items.length) return null
  // Секунды настройки — в миллисекунды таймера; настройка не доехала (старая
  // сборка, доска без проекта) — живём умолчанием, а не вечно
  const lifetime = seconds == null ? DEFAULT_LIFETIME_MS : Number(seconds) * 1000
  // Правый верхний угол области доски, а не низ окна: нижний край окна
  // прячется под панелью задач системы, а перекрыть её страница не может —
  // она живёт внутри окна. Стопка растёт вниз: прежние карточки остаются на
  // месте, новая встаёт под ними, и читаемое не прыгает из-под курсора
  return (
    <div className="pointer-events-none absolute top-3 right-3 z-40 flex flex-col gap-2">
      {items.map((notice) => (
        <Notice key={notice.id} notice={notice} activeProject={activeProject}
                lifetime={lifetime} onClose={onClose} />
      ))}
    </div>
  )
}
