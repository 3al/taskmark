import { useState } from 'react'
import { useDraggable, useDroppable } from '@dnd-kit/core'
import { statusStyle } from '../statuses'
import { taskType } from '../taskTypes'
import { taskSize } from '../taskSizes'
import { highlight } from '../highlight'

// Карточка задачи: draggable + droppable (дроп на карточку = вставка на её позицию)
// Полоска простоя у левого края карточки. Работа у неё периферийная — «в этой
// колонке что-то стоит», — поэтому причина кодируется цветом: блокировка
// красная, пауза жёлтая, а когда есть и то и другое, полоска делится пополам.
// Второй полоски у правого края не будет: там сразу зазор до соседней колонки,
// и вертикальная линия читалась бы как её граница.
//
// Не рамка (border-l), а отдельный элемент: у статуса свой hover:border-*,
// который красит рамку целиком и на наведении съедал бы полоску.
const STALL_STRIPE = {
  blocked: 'bg-rose-500/70',
  paused: 'bg-amber-400/70',
  both: 'bg-gradient-to-b from-rose-500/70 from-50% to-amber-400/70 to-50%',
  // Блокеры завершены: пометка ещё стоит, но держать нечему — приглушаем
  stale: 'bg-zinc-500/60',
}

// Правый угол верхней строки — три значка подряд (тип, пауза, крестик), и
// разъезжаться по размеру и базовой линии им нельзя: значок паузы и буква в
// кружке сами по себе разной высоты. Поэтому у всех один бокс с центрированием —
// различаются только заливкой и кеглем глифа внутри.
// Размер в em: строка номера масштабируется настройкой превью (--card-meta-size)
const SLOT = `inline-flex items-center justify-center shrink-0
  w-[1.4em] h-[1.4em] leading-none`

// Кегль глифов паузы и крестика: символы рисуются мельче своего кегля, и при
// одном размере с буквой типа выглядели бы меньше её
const GLYPH = { fontSize: '0.95em' }

// Кружок метки типа. Только у типа: пауза в кружке сливалась с типами, у
// которых тот же жёлтый (обсуждение).
// display переменной, а не условием: метку типа можно выключить в настройках
// (TASK-122), и флаг ради одного элемента незачем тащить пропсами
const MARK = `${SLOT} rounded-full ring-1`

// Возраст в статусе словами. Число дней приходит с бэкенда — там же решено,
// показывать ли его вообще: порог залежалости один на доску
function agePhrase(days) {
  const last = days % 10
  const teen = days % 100 >= 11 && days % 100 <= 14
  const word = !teen && last === 1 ? 'день' : !teen && last >= 2 && last <= 4 ? 'дня' : 'дней'
  return `${days} ${word} здесь`
}

// Свежесть правки: карточку, файл которой правили только что, видно боковым
// зрением — мягкое кольцо по контуру, без значка и без места в строках превью.
// Порог решает бэкенд: поле приходит только у свежих карточек
const FRESH_RING = 'ring-1 ring-sky-400/70 shadow-[0_0_16px_rgba(56,189,248,0.3)]'

// Сколько минут назад правили — словами, в подсказку номера задачи. Ровно ноль
// значит «в эту минуту», и «0 минут назад» о нём сказало бы хуже, чем «только что»
function freshPhrase(minutes) {
  if (!minutes) return 'правили только что'
  const last = minutes % 10
  const teen = minutes % 100 >= 11 && minutes % 100 <= 14
  const word = !teen && last === 1 ? 'минуту'
    : !teen && last >= 2 && last <= 4 ? 'минуты' : 'минут'
  return `правили ${minutes} ${word} назад`
}

// Прогресс плана работы. Делениями, пока пунктов немного: по ним «3 из 5»
// читается без цифр, а цифрам на превью места нет. Дальше деления сливаются
// в рябь шириной в пиксель, и полоска становится сплошной с долей заливки —
// точное число всё равно называет подсказка.
const PROGRESS_SEGMENTS_MAX = 8
const PROGRESS_BAR = 'w-14 h-[3px] shrink-0'
// Закрытый план — своё состояние, а не «почти закрытый»: зелёным его видно, не
// пересчитывая деления. Цвет тот же, что у терминальных статусов доски
const PROGRESS_DONE = 'bg-sky-500/70'
const PROGRESS_FULL = 'bg-emerald-500/80'
const PROGRESS_REST = 'bg-zinc-700'

function ChecklistProgress({ done, total }) {
  const title = `План работы: ${done} из ${total}`
  const filled = done >= total ? PROGRESS_FULL : PROGRESS_DONE
  if (total <= PROGRESS_SEGMENTS_MAX) {
    return (
      <span className={`${PROGRESS_BAR} flex items-stretch gap-px`} title={title}>
        {Array.from({ length: total }, (_, i) => (
          <span key={i} className={`flex-1 rounded-[1px]
            ${i < done ? filled : PROGRESS_REST}`} />
        ))}
      </span>
    )
  }
  return (
    <span className={`${PROGRESS_BAR} block rounded-full overflow-hidden ${PROGRESS_REST}`}
          title={title}>
      <span className={`block h-full rounded-full ${filled}`}
            style={{ width: `${Math.round((done / total) * 100)}%` }} />
    </span>
  )
}

function stripeKind(task) {
  if (task.stall_stale) return 'stale'
  const blocked = task.blocked_by?.length > 0
  if (blocked && task.paused) return 'both'
  if (blocked) return 'blocked'
  return task.paused ? 'paused' : null
}

export default function TaskCard({ task, status, onOpen, indicatorAllowed = true,
                                   query, match, onDelete, onOpenEpic, onContextMenu }) {
  const style = statusStyle(status)
  const stripe = stripeKind(task)
  const type = taskType(task.type)
  const size = taskSize(task.size)
  // Удаление необратимо, поэтому крестик сначала превращается в вопрос — как
  // «Забыть проект» в шапке. Что именно заденет удаление, спрашиваем у бэкенда
  // в момент первого клика: держит ли задача другие, видно только по файлам
  const [confirming, setConfirming] = useState(null)

  const askDelete = async (e) => {
    e.stopPropagation()
    setConfirming(await onDelete.plan(task.id))
  }
  const dragId = `task:${task.id}`
  const { attributes, listeners, setNodeRef: setDragRef, isDragging } = useDraggable({
    id: dragId,
    data: { taskId: task.id, fromStatus: status, task },
  })
  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `card:${status}:${task.id}`,
    data: { type: 'card', status, taskId: task.id },
  })

  return (
    <div ref={setDropRef} className="relative">
      {isOver && indicatorAllowed && (
        <div className="absolute -top-[5px] left-1 right-1 h-0.5 bg-sky-400 rounded-full
          shadow-[0_0_8px_rgba(56,189,248,0.9)] z-10 pointer-events-none" />
      )}
      <div
        ref={setDragRef}
        {...listeners}
        {...attributes}
        onClick={() => onOpen(task.id)}
        // Карточка только зовёт меню и гасит браузерное: что в меню — знает
        // доска (правила переноса, состав колонок), а не превью задачи
        onContextMenu={onContextMenu
          ? (e) => { e.preventDefault(); onContextMenu(task, status, e) }
          : undefined}
        className={`${style.card} group/card relative overflow-hidden border rounded-lg px-3.5 py-2.5
          cursor-grab ${style.cardHover} transition select-none touch-none
          outline-none ${style.cardFocus}
          ${task.fresh_minutes != null ? FRESH_RING : ''}
          ${isDragging ? 'opacity-40' : ''} ${task.struck ? 'opacity-45 border-dashed' : ''}`}
      >
        {stripe && (
          <span aria-hidden="true"
                className={`absolute left-0 top-0 bottom-0 w-0.5 ${STALL_STRIPE[stripe]}`} />
        )}
        {/* Крестик живёт в самом углу и вне потока: в ряду значков он спорил
            с меткой типа и паузой — те рассказывают о задаче, а он делает с
            ней необратимое. Пока мышь не в углу, он почти прозрачен: место
            под него не резервируется, композицию строки он не трогает.
            Размер маленький намеренно: бокс должен уместиться в поле отступа
            карточки (px-3.5 / py-2.5), иначе крестик наезжает на значки строки */}
        {onDelete && (
          <button
            type="button"
            onClick={askDelete}
            onPointerDown={(e) => e.stopPropagation()}
            className="absolute top-0 right-0 z-10 w-4 h-4 flex items-center justify-center
              text-[10px] leading-none text-zinc-600 opacity-0 group-hover/card:opacity-40
              hover:!opacity-100 hover:text-rose-300 focus-visible:opacity-100 transition"
            title="Удалить задачу">
            ✕
          </button>
        )}
        {/* Верх карточки — состояние: номер и почему задача стоит. Взгляд идёт
            по колонке сверху вниз, и «чего она ждёт» должно читаться до
            заголовка. У блокировки номер важнее значка — сразу видно, чего
            ждём; у паузы номера нет, а слово «пауза» рядом со значком ничего
            не добавляет */}
        <div className="flex items-center gap-1.5 font-mono text-zinc-500"
             style={{ fontSize: 'var(--card-meta-size, 12px)' }}>
          {/* Кто и когда последним двигал задачу — в подсказке номера: строку
              она больше не занимает, но и не пропадает. Номер есть у каждой
              карточки, а нижней строки у молодых задач нет вовсе */}
          {/* Кольцо свежести само по себе молчит о том, насколько она свежа —
              подсказка отвечает здесь же, где живут исполнитель и дата */}
          <span className="shrink-0"
                title={[task.agent, task.moved,
                        task.fresh_minutes != null ? freshPhrase(task.fresh_minutes) : '']
                  .filter(Boolean).join(' · ') || undefined}>
            {task.id}
          </span>
          {task.struck && <span className="text-zinc-600 normal-case shrink-0">superseded</span>}
          {/* Значок — эмодзи: его цвет рисует шрифт, и text-* на него не
              действует. Чтобы приглушённая пометка выглядела приглушённой
              целиком, обесцвечиваем фильтром */}
          {task.blocked_by?.length > 0 && (
            <span className={`shrink-0 normal-case ${task.stall_stale
              ? 'text-zinc-500 grayscale opacity-80' : 'text-rose-400/90'}`}
                  title={task.stall_stale
                    ? `Блокеры завершены, пометку можно снять: ${task.blocked_by.join(', ')}`
                    : `Ждёт: ${task.blocked_by.join(', ')}`}>
              ⛔{task.blocked_by[0]}
              {/* «+N» жмётся к номеру блокера (узкий пробел, приглушённый цвет):
                  обычный пробел ставил его ровно посередине между блокировкой и
                  значком паузы, и было непонятно, к чему он относится */}
              {task.blocked_by.length > 1 && (
                <span className="text-rose-400/60">{' '}+{task.blocked_by.length - 1}</span>
              )}
            </span>
          )}
          {/* Долг этапа: задача прошла этап, не закрыв его требования. Ничего
              не запрещает — рука человека не гейтится, — но объясняет заранее,
              на чём упрётся агент при следующем движении вперёд */}
          {task.debt?.length > 0 && (
            <span className="shrink-0 normal-case text-amber-300/90"
                  title={`Долг этапа: ${task.debt.map((d) => d.text).join('; ')}`}>
              ⚠{task.debt.length > 1 && (
                <span className="text-amber-300/60">{task.debt.length}</span>
              )}
            </span>
          )}
          {/* Списанные требования: тот же треугольник, но красный — обход
              гейта должен быть виден рядом с долгом, а не вместо него.
              У закрытых задач бэкенд его не присылает: исторические решения
              не стоят визуального шума на доске */}
          {task.waived?.length > 0 && (
            <span className="shrink-0 normal-case text-rose-400/90"
                  title={`Списаны требования: ${task.waived.map((w) => w.text).join('; ')}`}>
              ⚠{task.waived.length > 1 && (
                <span className="text-rose-400/60">{task.waived.length}</span>
              )}
            </span>
          )}
          {/* Правый угол строки: тип и пауза. Место освободилось после переезда
              эпика, а у значков без номера постоянное место читается лучше, чем
              позиция, зависящая от того, есть ли рядом блокировка.
              Тип левее паузы: он у задачи всегда один и тот же, а пауза
              приходит и уходит — прыгающий кружок сложнее находить взглядом */}
          {(size || type || task.paused) && (
            <span className="ml-auto flex items-center gap-1.5 shrink-0">
              {/* Размер левее типа: он не у каждой задачи есть, а тип и пауза
                  занимают постоянные места — прыгающая метка ищется взглядом
                  дольше. Буквами, а не кружком: «XL» в один знак не влезает,
                  и цветом размер не кодируется вовсе (см. taskSizes.js) */}
              {size && (
                <span className={`shrink-0 normal-case tabular-nums ${size.mark}`}
                      title={`Размер: ${size.label} — ${size.hint}`}>
                  {size.label}
                </span>
              )}
              {/* Кружок, а не подпись: в строке места на один знак. Полное
                  название типа — в окне задачи и в подсказке */}
              {type && (
                <span className={`${MARK} ${type.dot}`}
                      style={{ fontSize: '0.85em',
                               display: 'var(--card-type-display, inline-flex)' }}
                      title={`Тип: ${type.label}`}>
                  {type.letter}
                </span>
              )}
              {/* Пауза — значок без заливки: в кружке её путали с типом
                  (у обсуждения тот же жёлтый) */}
              {task.paused && (
                <span className={`${SLOT} rounded-full ring-1 normal-case ${task.stall_stale
                  ? 'text-zinc-500 ring-zinc-600/60 grayscale opacity-80'
                  : 'text-amber-300 ring-amber-400/40'}`}
                      style={GLYPH}
                      title={task.stall_stale
                        ? `Пометку можно снять: ${task.paused}`
                        : `Пауза: ${task.paused}`}>
                  ⏸
                </span>
              )}
            </span>
          )}
        </div>
        {/* Подтверждение поверх карточки: удаление необратимо, и одного клика
            для него мало. Здесь же — что оно заденет: у задач, которые ждали
            эту, пометка будет снята */}
        {confirming && (
          <div className="mt-1.5 rounded-lg border border-rose-900/70 bg-rose-950/40 px-2 py-1.5
            text-[11px] text-rose-200"
               onClick={(e) => e.stopPropagation()}
               onPointerDown={(e) => e.stopPropagation()}>
            <div>Удалить задачу и её файл?</div>
            {confirming.blocks?.length > 0 && (
              <div className="text-rose-300/80 mt-0.5">
                снимет блокировку у {confirming.blocks.join(', ')}
              </div>
            )}
            <div className="flex items-center gap-2 mt-1.5">
              <button
                type="button"
                className="px-2 py-0.5 rounded border border-rose-800 bg-rose-950/60
                  hover:bg-rose-900/60 transition"
                onClick={() => { setConfirming(null); onDelete.remove(task.id) }}>
                Удалить
              </button>
              <button
                type="button"
                className="px-1.5 py-0.5 rounded text-zinc-400 hover:text-zinc-200 transition"
                onClick={() => setConfirming(null)}>
                Отмена
              </button>
            </div>
          </div>
        )}
        {/* Заголовок превью чуть мягче интерфейсных надписей: карточек на доске
            десятки, полная яркость превращает колонку в стену текста */}
        {/* Размеры превью настраиваются (TASK-097) и приезжают CSS-переменными
            с корня документа: тащить их пропсами через колонку в каждую
            карточку — лишний слой ради трёх чисел. Обрезка по строкам заодно
            перестаёт быть классом line-clamp-N: число строк тоже настройка */}
        <div className="text-zinc-300/90 leading-snug mt-0.5 overflow-hidden"
             style={{
               fontSize: 'var(--card-title-size, 14px)',
               display: '-webkit-box',
               WebkitBoxOrient: 'vertical',
               WebkitLineClamp: 'var(--card-title-lines, 3)',
             }}
             title={task.title}>
          {highlight(task.title, query)}
        </div>
        {/* Нашлось в теле задачи — показываем, где именно: иначе непонятно,
            почему карточка попала в выдачу */}
        {match && !match.in_title && match.excerpt && (
          <div className="text-[11px] text-zinc-500 mt-1 line-clamp-2 leading-snug"
               title={match.excerpt}>
            {highlight(match.excerpt, query)}
          </div>
        )}
        {/* Низ карточки — что залежалось и к какому эпику относится. Эпик
            здесь, а не наверху: он самый широкий элемент строки и при этом
            самый редко нужный — наверху он вытеснял пометки простоя.
            Возраст вместо прежнего «агент · дата»: имя модели на доске почти
            бесполезно (за контекстом идут в открытую карточку), а дата сама по
            себе не отвечает на вопрос «что стоит» — его считали в уме. Кто и
            когда двигал, остаётся в подсказке. Задача моложе порога поля
            не получает вовсе, и строки у неё нет */}
        {/* Три ячейки, а не поток: полоска прогресса должна стоять по центру
            карточки независимо от того, есть ли у задачи возраст и эпик. В
            потоке она уезжала бы вслед за соседями, и колонка превращалась
            в лесенку */}
        {(task.stale_days || task.epic || task.progress) && (
          <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 mt-1 text-zinc-500"
               style={{ fontSize: 'var(--card-meta-size, 12px)' }}>
            <span className="min-w-0 truncate">
              {task.stale_days > 0 && (
                <span title={[task.agent, task.moved].filter(Boolean).join(' · ')}>
                  {agePhrase(task.stale_days)}
                </span>
              )}
            </span>
            {/* Прогресс приходит с бэкенда и только у задач с планом: там же
                решено, показывать ли его вообще (настройка вида превью) */}
            <span className="flex justify-center">
              {task.progress?.total > 0 && (
                <ChecklistProgress done={task.progress.done} total={task.progress.total} />
              )}
            </span>
            <span className="min-w-0 flex justify-end">
              {task.epic && (
                // Клик открывает состав эпика, но карточку задачи при этом не
                // трогает: событие дальше не идёт, иначе поверх окна эпика тут же
                // открылась бы сама задача
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onOpenEpic?.(task.epic) }}
                  onPointerDown={(e) => e.stopPropagation()}
                  className="min-w-0 truncate px-1.5 py-px rounded
                    border border-zinc-700 text-[10px] font-mono text-zinc-400
                    transition hover:border-zinc-500 hover:text-zinc-200"
                  title={`Задачи эпика ${task.epic}`}>
                  {task.epic}
                </button>
              )}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
