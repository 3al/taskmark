import { useCallback, useEffect, useRef, useState } from 'react'

import { playNotice } from '../sound'

// Всплывашки службы уведомлений: верхний угол доски, стопка, исчезание по таймеру.
//
// Уведомление говорит о том, что случилось **сейчас**, и работе не мешает:
// контейнер сквозной для мыши (`pointer-events-none`), кликается только сама
// карточка. Иначе полоса в углу перехватывала бы перетаскивание задач.

// Сколько живёт уведомление без вмешательства, когда настройка не пришла.
// Кому не хватило, тот наводит мышь: вся стопка приостанавливается, чтобы
// соседние уведомления не исчезали, пока человек читает одно из них.
// Ноль в настройке означает «не гасить само» — уведомление ждёт крестика
const DEFAULT_LIFETIME_MS = 6000
// Длительность ухода: столько карточка тает и складывается, прежде чем
// исчезнуть. Не мгновение: уходящая карточка освобождает место соседям, и
// рывок в углу экрана заметнее самого уведомления
const LEAVE_MS = 520

// Уровень — единственное, чем уведомления отличаются на вид. Решений по нему
// не принимают: это тон сообщения, а не его важность в очереди.
//
// Цвет несут полоска во всю высоту, еле заметная подложка и заголовок. Заливать
// цветом шапку целиком пробовали — на приглушённой доске такая карточка читается
// чужеродной наклейкой, а не сообщением инструмента. Классы записаны целиком,
// а не собираются из кусков: Tailwind вычитает их из исходника, и имя,
// склеенное в рантайме, в сборку не попадает
const LEVELS = {
  info: { stripe: 'bg-sky-400', tint: 'bg-sky-500/[0.06]', ring: 'ring-sky-500/25',
          title: 'text-sky-300', dot: 'bg-sky-400' },
  success: { stripe: 'bg-emerald-400', tint: 'bg-emerald-500/[0.06]', ring: 'ring-emerald-500/25',
             title: 'text-emerald-300', dot: 'bg-emerald-400' },
  warning: { stripe: 'bg-amber-400', tint: 'bg-amber-500/[0.06]', ring: 'ring-amber-500/25',
             title: 'text-amber-300', dot: 'bg-amber-400' },
  error: { stripe: 'bg-rose-500', tint: 'bg-rose-500/[0.07]', ring: 'ring-rose-500/30',
           title: 'text-rose-300', dot: 'bg-rose-500' },
}

// Видно ли страницу прямо сейчас. Свёрнутое окно и уход на другую вкладку —
// то же `hidden`, и различать их незачем: в обоих случаях человек уведомления
// не видит
function usePageVisible() {
  const [visible, setVisible] = useState(() => !document.hidden)
  useEffect(() => {
    const onChange = () => setVisible(!document.hidden)
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return visible
}

function Notice({ notice, activeProject, lifetime, volume, visible, paused,
                 closingAll, onClose }) {
  // Вид, который ждёт закрытия, таймера не получает вовсе: «ход за вами» —
  // призыв к действию, а не сообщение о «сейчас», и адресат его как раз тот,
  // кого у экрана нет. Ноль тут значит ровно то же, что ноль в настройке
  const life = notice.sticky ? 0 : lifetime
  const [leaving, setLeaving] = useState(false)
  const left = useRef(life)
  const card = useRef(null)

  // Звучит только то уведомление, чей источник об этом просили: признак
  // считает бэкенд, показу остаётся проиграть. Эффект без зависимостей —
  // сигнал принадлежит появлению карточки, а не её перерисовкам
  useEffect(() => {
    if (notice.sound) playNotice(volume)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const close = useCallback(() => {
    // Высоту фиксируем **до** ухода: складывать карточку в ноль можно только
    // от конкретного значения, а `auto` не анимируется. Иначе соседи
    // подпрыгивают на её место рывком, в момент размонтирования
    const el = card.current
    if (el) el.style.height = `${el.offsetHeight}px`
    setLeaving(true)
    setTimeout(() => onClose(notice.id), LEAVE_MS)
  }, [notice.id, onClose])

  useEffect(() => {
    if (!leaving) return
    // Следующий кадр: иначе браузер не увидит перехода между зафиксированной
    // высотой и нулём и схлопнет карточку мгновенно
    const frame = requestAnimationFrame(() => {
      const el = card.current
      if (el) { el.style.height = '0px'; el.style.marginBottom = '0px' }
    })
    return () => cancelAnimationFrame(frame)
  }, [leaving])

  // «Закрыть все» уводит карточки тем же путём, что и крестик: разом стёртая
  // стопка мигает, а исчезновение должно читаться как исчезновение
  useEffect(() => {
    if (closingAll && !leaving) close()
  }, [closingAll, leaving, close])

  useEffect(() => {
    // Ноль — «не гасить само»: таймера просто нет, уведомление ждёт крестика.
    // Уходящая карточка досчитывать тоже не должна.
    // **Невидимая доска не считает.** Уведомление адресовано человеку,
    // который отошёл от экрана: сгорев в свёрнутом окне, оно не показалось бы
    // вовсе — а истории у службы нет, и вернуться к нему неоткуда
    if (!life || paused || leaving || !visible) return
    const started = Date.now()
    const timer = setTimeout(close, left.current)
    return () => {
      clearTimeout(timer)
      // Списываем прожитое: наведение мыши **приостанавливает** отсчёт, а не
      // сбрасывает его — иначе отвлёкшийся человек получал бы полный срок
      // заново при каждом движении мыши
      left.current = Math.max(0, left.current - (Date.now() - started))
    }
  }, [paused, leaving, visible, life, close])

  const level = LEVELS[notice.level] || LEVELS.info
  // Имя проекта показываем только у чужого: сервер один на весь реестр, и
  // задача из чата приезжает в проект, который прямо сейчас не открыт
  const foreign = notice.project && notice.project !== activeProject
  // Кто зовёт. Своим цветом, а не общим серым: уведомление от агента приходит
  // от имени конкретной модели, и это первое, что человек хочет знать, увидев
  // всплывашку, — у системных поводов автора нет вовсе
  const meta = notice.agent || foreign || notice.ts
  // Время **события**, а не показа: между ними свёрнутое окно и переподключение
  // SSE, и стопка, увиденная после разворачивания доски, иначе не отвечает на
  // вопрос «когда это было». Старое событие времени не везёт — тогда его просто
  // нет, а не пусто на его месте
  const at = notice.ts
    ? new Date(notice.ts * 1000).toLocaleTimeString('ru-RU', { hour12: false })
    : ''

  // Всплывашка не должна читаться как ещё одна карточка задачи, но и наклейкой
  // поверх доски тоже: отличают её корпус темнее любой колонки, цветная полоска
  // во всю высоту и крупная тень — а не заливка и размер
  return (
    <div
      ref={card}
      className={`pointer-events-auto mb-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden
        rounded-lg bg-zinc-950/95 ring-1 ${level.ring} shadow-xl shadow-black/60 backdrop-blur-sm
        ${leaving ? 'notice-leaving' : 'animate-notice-in'}`}
    >
      <div className={`flex items-stretch ${level.tint}`}>
        <span className={`w-0.5 shrink-0 ${level.stripe}`} />
        <div className="min-w-0 flex-1 px-3 py-2">
          <div className={`text-[13px] font-medium ${level.title}`}>{notice.title}</div>
          {notice.text && (
            <div className="mt-0.5 text-xs text-zinc-300 break-words">{notice.text}</div>
          )}
          {meta && (
            <div className="mt-1 text-[11px] text-zinc-400">
              {/* Части строки собираются списком: у карточки может не быть ни
                  автора, ни чужого проекта, а разделитель между отсутствующими
                  оставлял бы висящую точку */}
              {[
                notice.agent && <span className="text-violet-300">{notice.agent}</span>,
                foreign && `Проект: ${notice.project}`,
                at,
              ].filter(Boolean).map((part, index) => (
                <span key={index}>{index > 0 && ' · '}{part}</span>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={close}
          title="Закрыть"
          className="shrink-0 self-start px-2 py-1.5 text-zinc-500 hover:text-zinc-200"
        >
          ✕
        </button>
      </div>
      {/* Полоска отсчёта: она же и ответ на вопрос «пауза вообще работает?».
          Ширину ведёт CSS-анимация той же длительности, что и таймер, а
          наведение на стопку останавливает её вместе с остальными — человек
          видит, что отсчёт замер и продолжился с места, а не начался заново */}
      {!!life && (
        <div
          className={`h-0.5 ${level.dot} opacity-40 notice-bar`}
          style={{ animationDuration: `${life}ms`,
                   animationPlayState: paused || !visible ? 'paused' : 'running' }}
        />
      )}
    </div>
  )
}

export default function Notices({ items, activeProject, seconds, volume, onClose }) {
  // Видимость спрашиваем один раз на стопку, а не в каждой карточке: слушатель
  // один, и все карточки замирают и оживают одновременно
  const visible = usePageVisible()
  const [closingAll, setClosingAll] = useState(false)
  // Пауза принадлежит всей стопке: пока человек читает одно уведомление,
  // соседние не должны исчезать. Таймеры остаются внутри карточек и каждый
  // сохраняет собственный остаток времени.
  const [paused, setPaused] = useState(false)
  // Стопка опустела — снимаем режим: следующая пачка не должна уйти, не успев
  // показаться
  useEffect(() => { if (!items.length) setClosingAll(false) }, [items.length])
  if (!items.length) return null
  // Секунды настройки — в миллисекунды таймера; настройка не доехала (старая
  // сборка, доска без проекта) — живём умолчанием, а не вечно
  const lifetime = seconds == null ? DEFAULT_LIFETIME_MS : Number(seconds) * 1000
  // Правый верхний угол области доски, а не низ окна: нижний край окна
  // прячется под панелью задач системы, а перекрыть её страница не может —
  // она живёт внутри окна. Стопка растёт вниз: прежние карточки остаются на
  // месте, новая встаёт под ними, и читаемое не прыгает из-под курсора
  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className="pointer-events-none absolute top-3 right-3 z-[60] flex flex-col items-end"
    >
      {/* Ждущие закрытия уведомления копятся: одно движение должно убирать всю
          стопку, иначе крестики нажимают по очереди. Одному уведомлению кнопка
          не нужна — у него есть свой крестик */}
      {items.length > 1 && (
        <button
          onClick={() => setClosingAll(true)}
          className="pointer-events-auto mb-2 rounded px-2 py-0.5 text-[11px] text-zinc-400
            bg-zinc-900/95 ring-1 ring-zinc-700/60 backdrop-blur-sm
            hover:text-zinc-200 hover:ring-zinc-600"
        >
          Закрыть все
        </button>
      )}
      {items.map((notice) => (
        <Notice key={notice.id} notice={notice} activeProject={activeProject}
                lifetime={lifetime} volume={volume} visible={visible} paused={paused}
                closingAll={closingAll}
                onClose={onClose} />
      ))}
    </div>
  )
}
