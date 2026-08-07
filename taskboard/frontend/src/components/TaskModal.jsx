import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { statusLabel, statusStyle } from '../statuses'
import { TASK_TYPES, taskType } from '../taskTypes'
import { highlight, rehypeHighlight } from '../highlight'
import { mdComponents, rehypeNoteMeta } from '../markdown'
import { INLINE_FIELD } from '../fields'
import CopyButton from './CopyButton'
import MarkdownEditor from './MarkdownEditor'
import ReasonPrompt from './ReasonPrompt'
import TaskPicker from './TaskPicker'

// Типографика заголовка — одна и та же в просмотре и в правке: любой разнобой
// в размере, начертании или межстрочном интервале превращается в скачок высоты
// шапки при входе в редактирование
const TITLE_TEXT = 'text-xl font-semibold text-zinc-300 leading-snug break-words'

// Кнопки правки простоя — в оформлении самого окна: заливка акцентным синим
// спорит с цветом статуса в шапке и тянет на себя внимание сильнее задачи
const STALL_BUTTON =
  'shrink-0 px-2 py-1 text-xs rounded-lg border border-zinc-700 text-zinc-300 ' +
  'hover:border-zinc-500 hover:bg-zinc-800 disabled:opacity-40 ' +
  'disabled:hover:border-zinc-700 disabled:hover:bg-transparent transition'

// Кнопки правки названия — inline сразу за последним символом, но нулевой
// ширины и высоты: в поток они не попадают, поэтому текст переносится ровно
// так же, как без них, а выглядят приклеенными к концу строки — в том числе
// перенесённой. `visible` и `z-10` нужны в режиме правки: держатель едет
// внутри невидимого двойника текста, а сверху лежит поле ввода
function TitleActions({ children }) {
  return (
    <span className="visible relative z-10 inline-block w-0 h-0 align-baseline">
      <span className="absolute left-1.5 top-[-0.32em] -translate-y-1/2 flex items-center gap-1">
        {children}
      </span>
    </span>
  )
}

// Заголовок редактируемой секции рисуем сами, а не отдаём в markdown: кнопка
// правки должна стоять сразу за последним словом («Описание ✎»), как у названия
// задачи, а внутрь тега, собранного react-markdown, её не положить. Стили те же —
// `.md-body h2/h3` из index.css
function SectionHeading({ heading, query, children }) {
  const level = heading.length - heading.replace(/^#+/, '').length
  const Tag = `h${Math.min(6, Math.max(1, level))}`
  const text = heading.replace(/^#+\s*/, '')
  return <Tag>{query ? highlight(text, query) : text}{children}</Tag>
}

// Тело задачи режется на блоки по редактируемым секциям: заголовки и ключи
// приходят с бэкенда — структуру файла знает он, здесь её остаётся применить.
// Граница секции — заголовок своего или более высокого уровня либо начало
// соседней редактируемой секции (зеркало task_parser.section_bounds): иначе
// «## Описание» обрывалось бы на первом же `### Что делаем`
// Копия текста, где содержимое блоков кода заменено пробелами: длина и
// переносы сохраняются, поэтому найденный по маске индекс годится для
// исходного текста. Зеркало `task_parser.mask_code_fences` — описание задачи
// сплошь и рядом показывает фрагмент доски, и строка `## Release Notes` внутри
// примера не должна обрывать секцию (TASK-120)
const FENCE = /^ {0,3}(`{3,}|~{3,})/

export function maskCodeFences(text) {
  let fence = null
  return (text || '').split('\n').map((line) => {
    const match = FENCE.exec(line)
    if (fence === null) {
      if (!match) return line
      fence = match[1][0]
      return ' '.repeat(line.length)
    }
    // Закрывает забор того же вида; незакрытый маскирует всё до конца текста —
    // ровно так же его понимает и markdown
    if (match && match[1][0] === fence && !line.split(fence).join('').trim()) fence = null
    return ' '.repeat(line.length)
  }).join('\n')
}

export function splitSections(body, sections) {
  const blocks = []
  const headings = (sections || []).map((s) => s.heading)
  let rest = body || ''
  for (const section of sections || []) {
    const masked = maskCodeFences(rest)
    const at = masked.indexOf(`${section.heading}\n`)
    if (at < 0) continue
    const before = rest.slice(0, at)
    if (before.trim()) blocks.push({ type: 'md', text: before })

    const from = at + section.heading.length + 1
    const tail = masked.slice(from)
    const level = section.heading.length - section.heading.replace(/^#+/, '').length
    const stops = []
    const higher = tail.match(new RegExp(`^#{1,${level}} `, 'm'))
    if (higher) stops.push(higher.index)
    for (const other of headings) {
      if (other === section.heading) continue
      const idx = tail.indexOf(`${other}\n`)
      if (idx >= 0) stops.push(idx)
    }
    const to = stops.length ? from + Math.min(...stops) : rest.length
    blocks.push({
      type: 'section',
      key: section.key,
      heading: section.heading,
      text: rest.slice(from, to).trim(),
      // Правим сырой текст файла, а не очищенный от комментариев рендер
      raw: section.text ?? '',
    })
    rest = rest.slice(to)
  }
  if (rest.trim()) blocks.push({ type: 'md', text: rest })
  return blocks
}

// Модалка с полным содержимым задачи (рендер markdown).
// query — активный поиск: совпадения подсвечиваются прямо в тексте задачи.
// onOpenTask — переход к другой задаче (номер блокера кликабелен),
// onChanged — простой поменялся: доске нужно перечитать карточки
export default function TaskModal({ taskId, query, onOpenTask, onChanged, onBack, backTo, onClose }) {
  const [task, setTask] = useState(null)
  const [error, setError] = useState(null)
  // HTML-комментарии в файле задачи — служебные пометки для агентов; человеку
  // на доске они видны как блоки текста и только мешают читать задачу.
  // Файл не трогаем: чистим то, что показываем и копируем
  const body = useMemo(
    () => (task?.body || '').replace(/<!--[\s\S]*?-->/g, '').replace(/\n{3,}/g, '\n\n').trim(),
    [task],
  )
  // Плагин пересобираем только при смене запроса: иначе react-markdown
  // перерисовывает всё дерево на каждый рендер модалки
  const rehypePlugins = useMemo(
    () => (query?.trim() ? [rehypeNoteMeta, rehypeHighlight(query)] : [rehypeNoteMeta]),
    [query],
  )
  // Оттенок шапки модалки в цвет статуса задачи (применится после загрузки)
  const style = statusStyle(task?.meta?.status)

  // Inline-редактирование названия. Требование к вёрстке: переключение
  // просмотр ⇄ правка не должно шевелить шапку. Поэтому поле не приносит в
  // поток ни отступов, ни рамки — рамка рисуется outline'ом (он вне потока),
  // а размер полю задаёт невидимый двойник текста: он переносится ровно так
  // же, как обычный заголовок, значит высота совпадает и на многострочных
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [saving, setSaving] = useState(false)
  const [titleError, setTitleError] = useState(null)

  const startEdit = () => {
    if (!task?.meta) return
    setEditTitle(task.meta.title || '')
    setTitleError(null)
    setEditing(true)
  }

  const cancelEdit = () => {
    setEditing(false)
    setTitleError(null)
  }

  const saveTitle = async () => {
    const trimmed = editTitle.trim()
    if (!trimmed || trimmed === task?.meta?.title) {
      cancelEdit()
      return
    }
    setSaving(true)
    try {
      // Сервер нормализует название (пробелы), поэтому берём его ответ
      const saved = await api.updateTask(taskId, { title: trimmed })
      setTask({ ...task, meta: { ...task.meta, title: saved?.title || trimmed } })
      setEditing(false)
      setTitleError(null)
    } catch (e) {
      // Правку не теряем: поле остаётся открытым с введённым текстом
      setTitleError(e.message || 'Не удалось переименовать задачу')
    } finally {
      setSaving(false)
    }
  }

  // Правка текста задачи: описание и критерии — отдельные секции, каждая со
  // своим карандашом. Правится **сырой markdown файла**, без автопреобразований:
  // в окне почти всегда открывают уже оформленное агентом описание, где перенос
  // внутри абзаца — часть оформления. Предпросмотр показывает результат разметки
  // до сохранения, чтобы поломка была видна сразу
  const blocks = useMemo(() => splitSections(body, task?.sections), [body, task])
  const [editSection, setEditSection] = useState(null)
  const [sectionText, setSectionText] = useState('')
  const [sectionPreview, setSectionPreview] = useState(false)
  const [sectionSaving, setSectionSaving] = useState(false)
  const [sectionError, setSectionError] = useState(null)

  const startSection = (block) => {
    setEditSection(block.key)
    setSectionText(block.raw)
    setSectionPreview(false)
    setSectionError(null)
  }

  const cancelSection = () => {
    setEditSection(null)
    setSectionError(null)
  }

  const saveSection = async () => {
    setSectionSaving(true)
    try {
      await api.updateTask(taskId, { [editSection]: sectionText })
      // Ответ описывает правку, а не задачу целиком: перечитываем, чтобы тело
      // и границы секций пересобрались из файла
      setTask(await api.task(taskId))
      setEditSection(null)
      setSectionError(null)
    } catch (e) {
      // Правку не теряем: поле остаётся открытым с введённым текстом
      setSectionError(e.message || 'Не удалось сохранить текст')
    } finally {
      setSectionSaving(false)
    }
  }

  // Тип задачи правится тут же: список открывается по клику на метке
  const [typePicker, setTypePicker] = useState(false)
  const [typeSaving, setTypeSaving] = useState(false)
  // Клик мимо списка ловит подложка под ним (см. разметку), а не слушатель на
  // окне: слушатель на mousedown успевал закрыть список до того, как до фона
  // модалки доходил click, — и фон, уже не видя открытого списка, закрывал
  // задачу целиком

  const pickType = async (key) => {
    setTypePicker(false)
    if (key === task?.meta?.type) return
    setTypeSaving(true)
    try {
      await api.updateTask(taskId, { type: key })
      // Ответ PATCH описывает правку, а не задачу целиком — перечитываем
      setTask(await api.task(taskId))
      onChanged?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setTypeSaving(false)
    }
  }

  // Простой задачи: что показываем и что правим. Форма ввода открывается по
  // кнопке — панель не должна занимать место, пока задача никого не ждёт
  const stall = task?.stall
  const [stallForm, setStallForm] = useState(null) // 'pause' | 'block'
  // Номер в поле и выбранная задача: блокировать можно только существующую,
  // поэтому решает не текст, а то, нашлась ли задача в проекте
  const [blockId, setBlockId] = useState('')
  const [blockTask, setBlockTask] = useState(null)
  const [stallBusy, setStallBusy] = useState(false)
  const [stallError, setStallError] = useState(null)

  const patchStall = async (updates) => {
    setStallBusy(true)
    setStallError(null)
    try {
      await api.updateTask(taskId, updates)
      // Ответ PATCH описывает правку, а не задачу целиком: перечитываем — так
      // подтянутся и заголовки блокеров, и обратные ссылки
      setTask(await api.task(taskId))
      setStallForm(null)
      setBlockId('')
      setBlockTask(null)
      onChanged?.()
    } catch (e) {
      setStallError(e.message)
    } finally {
      setStallBusy(false)
    }
  }

  // За раз добавляется одна задача: несколько номеров в одном поле подсказки
  // подобрать не помогут, а «мусор через пробел» превращался в блокеров-призраков
  const addBlocker = () => {
    if (!blockTask) return
    patchStall({ blocked_by: [...(stall?.blocked_by || []), blockTask.id] })
  }

  const dropBlocker = (id) =>
    patchStall({ blocked_by: (stall?.blocked_by || []).filter((b) => b !== id) })

  useEffect(() => {
    setTask(null)
    setError(null)
    setEditing(false)
    setTitleError(null)
    setStallForm(null)
    setStallError(null)
    setBlockId('')
    setTypePicker(false)
    api.task(taskId).then(setTask).catch((e) => setError(e.message))
  }, [taskId])

  // Esc забирает открытая правка: закрыть окно поверх набранного текста —
  // потерять его молча. Первый Esc сворачивает правку, второй закрывает окно.
  // Слушатель нужен и здесь, а не только в поле: в режиме предпросмотра
  // фокуса на textarea нет, и нажатие уходит прямо в окно
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      if (editSection) { cancelSection(); return }
      // Открытый список типов Esc тоже забирает: он перекрывает шапку,
      // и закрыть окно вместе с ним значило бы промахнуться мимо задачи
      if (typePicker) { setTypePicker(false); return }
      onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, editSection, typePicker])

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
      // Пока идёт правка, окно закрывается только явно: промах мышью мимо
      // карточки не должен уносить набранный текст. Открытый список типов
      // сюда не доходит — его клик забирает собственная подложка
      onClick={() => { if (!editSection) onClose() }}
    >
      {/* Ширина по содержимому: обычная задача остаётся привычных 48rem, а
          широкая таблица раздвигает окно до края экрана вместо того, чтобы
          уезжать за него вместе со всем текстом */}
      <div
        className="bg-zinc-900 border border-zinc-700 rounded-2xl w-fit min-w-[min(48rem,92vw)] max-w-[92vw] max-h-[85vh] flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`flex items-start gap-3 px-5 py-4 border-b ${style.modalHeader}`}>
          {/* Тот же предел, что и у текста в теле: длинное название — не повод
              растягивать окно, оно переносится по словам */}
          <div className="min-w-0 flex-1 max-w-[44rem]">
            {/* Рамка правки рисуется вплотную над строкой номера — на время
                правки уводим её на пару пикселей вверх. Это transform: в поток
                он не попадает, поэтому высота шапки не меняется */}
            <div className={`flex items-center gap-2 text-xs font-mono text-zinc-500
              transition-transform ${editing ? '-translate-y-0.5' : ''}`}>
              {/* Ушли по номеру блокера — должен быть путь обратно: иначе
                  переход в один конец, и открытую задачу приходится искать заново */}
              {onBack && (
                <button
                  onClick={onBack}
                  className="text-zinc-500 hover:text-zinc-200 transition"
                  title={backTo ? `Назад к ${backTo}` : 'Назад'}
                >
                  ←
                </button>
              )}
              {taskId}
              {/* Номер нужен отдельно от содержимого постоянно — им зовут
                  агента, его пишут в коммит. Кнопка тут же, ростом со шрифт
                  номера: выделять его мышью каждый раз незачем */}
              <CopyButton text={taskId} size="sm" className="-ml-1"
                          title="Скопировать номер задачи" />
            </div>
            {/* Кнопки лежат на нулевой ширине сразу за последним символом, а
                место под них справа держит распорка — в поток они не попадают
                ни в одном из режимов, поэтому текст переносится одинаково */}
            <div className="group flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="relative w-fit max-w-full">
                  {editing ? (
                    <>
                      {/* Двойник в потоке задаёт коробке размер, поле лежит
                          поверх него — оба переносят текст одинаково. Кнопки
                          едут внутри двойника: он и знает, где кончился текст */}
                      <span className={`${TITLE_TEXT} block whitespace-pre-wrap invisible`}>
                        {/* хвостовой zero-width: пустое поле не должно схлопываться в ноль */}
                        <span aria-hidden="true">{editTitle + '\u200b'}</span>
                        <TitleActions>
                          <button
                            onClick={saveTitle}
                            disabled={saving}
                            className="w-6 h-6 flex items-center justify-center text-base text-zinc-500 hover:text-emerald-400"
                            title="Сохранить (Enter)"
                          >✓</button>
                          <button
                            onClick={cancelEdit}
                            className="w-6 h-6 flex items-center justify-center text-base text-zinc-500 hover:text-rose-400"
                            title="Отменить (Esc)"
                          >✕</button>
                        </TitleActions>
                      </span>
                      <textarea
                        className={`${TITLE_TEXT} absolute inset-0 p-0 border-0 bg-transparent resize-none overflow-hidden
                          rounded-sm outline outline-1 outline-offset-2 outline-zinc-500 focus:outline-zinc-400`}
                        value={editTitle}
                        // Название — одна строка: перенос из вставки схлопываем,
                        // иначе он разорвёт и frontmatter, и строку доски
                        onChange={(e) => setEditTitle(e.target.value.replace(/\s*\n\s*/g, ' '))}
                        onFocus={(e) => e.target.setSelectionRange(e.target.value.length, e.target.value.length)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!saving) saveTitle() }
                          // Esc гасим здесь: иначе слушатель окна закроет всю модалку
                          if (e.key === 'Escape') { e.stopPropagation(); cancelEdit() }
                        }}
                        autoFocus
                      />
                    </>
                  ) : (
                    <div className={`${TITLE_TEXT} cursor-text`} onClick={startEdit}>
                      {task?.meta?.title ? highlight(task.meta.title, query) : '…'}
                      {task?.meta && (
                        <TitleActions>
                          <button
                            onClick={startEdit}
                            className="w-6 h-6 flex items-center justify-center text-base text-zinc-600 hover:text-zinc-300
                              opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                            title="Изменить название"
                          >✎</button>
                        </TitleActions>
                      )}
                    </div>
                  )}
                </div>
              </div>
              {/* распорка: кнопкам за концом длинной строки нужно место, иначе
                  они налезут на кнопку копирования */}
              <div className="shrink-0 w-14" aria-hidden="true" />
            </div>
            {task?.meta && (
              <div className="text-xs text-zinc-500 mt-1 flex items-center gap-2 flex-wrap">
                {/* На превью тип — кружок с буквой (места там на один знак),
                    здесь он подписан полностью: окно и открывают, чтобы понять,
                    что это за задача. Клик по метке меняет тип: список тут же,
                    цветными метками — выбирают глазами, а не по названию.
                    Задача без типа показывает пустую метку, иначе поставить его
                    было бы нечем */}
                <span className="relative">
                  <button
                    type="button"
                    onClick={() => setTypePicker((v) => !v)}
                    disabled={typeSaving}
                    title="Сменить тип задачи"
                    className={`px-1.5 py-px rounded border text-[10px] transition
                      hover:brightness-125 disabled:opacity-60
                      ${taskType(task.meta.type)?.badge
                        || 'border-dashed border-zinc-700 text-zinc-500'}`}>
                    {taskType(task.meta.type)?.label || 'без типа'}
                  </button>
                  {typePicker && (
                    <>
                    {/* Подложка на весь экран: любой клик мимо списка гасится
                        здесь и дальше не идёт — ни к фону модалки, ни к тексту
                        задачи. Она же закрывает список повторным кликом по метке */}
                    <span className="fixed inset-0 z-40"
                          onClick={(e) => { e.stopPropagation(); setTypePicker(false) }} />
                    <span className="absolute left-0 top-full mt-1 z-50 flex flex-col gap-1
                      rounded-lg border border-zinc-700 bg-zinc-900 p-1.5 shadow-xl">
                      {Object.entries(TASK_TYPES).map(([key, meta]) => (
                        <button
                          key={key}
                          type="button"
                          onClick={() => pickType(key)}
                          className={`px-1.5 py-px rounded border text-[10px] text-left
                            whitespace-nowrap transition hover:brightness-125
                            ${meta.badge} ${key === task.meta.type ? 'ring-1 ring-zinc-500' : ''}`}>
                          {meta.label}
                        </button>
                      ))}
                    </span>
                    </>
                  )}
                </span>
                <span>
                статус: {task.meta.status || '—'} · создана: {task.meta.created || '—'}
                {/* Во frontmatter лежит ключ, имя эпика приходит из реестра */}
                {task.meta.epic && task.meta.epic !== '~' && (
                  <> · эпик: <span className="text-zinc-400">{task.meta.epic}</span>
                    {task.epic_name ? ` — ${task.epic_name}` : ''}</>
                )}
                </span>
              </div>
            )}
            {titleError && <div className="text-xs text-rose-400 mt-1">{titleError}</div>}

            {/* Причина отмены: спрашивается один раз при переводе и остаётся
                в файле навсегда — из съезда не возвращаются */}
            {task?.meta?.cancel_reason && task.meta.cancel_reason !== '~' && (
              <div className="text-xs text-zinc-500 mt-1">
                отменена: <span className="text-zinc-400">{task.meta.cancel_reason}</span>
              </div>
            )}

            {/* Простой: почему задача стоит и как это снять. Блокировка живёт
                двумя концами, но правится одним вызовом — см. backend/stall.py */}
            {task && (
              <div className="mt-2 space-y-1">
                {stall?.blocked_by_tasks?.map((b) => (
                  <div key={b.id} className="flex items-center gap-2 text-xs">
                    {/* Блокер дошёл до конца маршрута — держать нечему.
                        Приглушаем и говорим прямо, но снимает человек:
                        пометка стоит в этой задаче, а решение за ним */}
                    <span className={`shrink-0 ${b.resolved || stall?.stale
                      ? 'text-zinc-500 grayscale opacity-80' : 'text-rose-400/90'}`}>
                      ⛔ ждёт
                    </span>
                    {/* Номер блокера — в цвет маркера на превью: на доске и в
                        карточке это одно и то же, разный цвет читался бы как
                        разные вещи. Блокировка красная, пауза жёлтая — иначе
                        два разных состояния сливаются в одно пятно */}
                    <button
                      onClick={() => onOpenTask?.(b.id)}
                      className={`font-mono shrink-0 ${b.resolved
                        ? 'text-zinc-400 hover:text-zinc-200'
                        : 'text-rose-400/90 hover:text-rose-300'}`}
                      title="Открыть блокирующую задачу"
                    >
                      {b.id}
                    </button>
                    {b.found ? (
                      <span className="text-zinc-500 truncate">
                        {b.title}
                        {b.status && <> · {statusLabel(b.status)}</>}
                      </span>
                    ) : (
                      <span className="text-rose-400/80 shrink-0">задача не найдена</span>
                    )}
                    {/* Держать нечему: блокер завершён или сама задача закрыта.
                        Отдельной строкой вне обрезки — у длинного заголовка
                        блокера пометку срезало многоточием */}
                    {(b.resolved || stall?.stale) && (
                      <span className="shrink-0 text-emerald-400/80">— можно снимать</span>
                    )}
                    <button
                      onClick={() => dropBlocker(b.id)}
                      disabled={stallBusy}
                      className="ml-1 text-zinc-600 hover:text-zinc-300 shrink-0 disabled:opacity-40"
                      title="Снять блокировку"
                    >
                      ✕
                    </button>
                  </div>
                ))}

                {stall?.paused && (
                  <div className="flex items-center gap-2 text-xs">
                    <span className={`shrink-0 ${stall.stale
                      ? 'text-zinc-500 grayscale opacity-80' : 'text-amber-300/80'}`}>
                      ⏸ пауза
                    </span>
                    <span className="text-zinc-400 truncate" title={stall.paused}>
                      {stall.paused}
                    </span>
                    {stall.stale && (
                      <span className="shrink-0 text-emerald-400/80">— можно снимать</span>
                    )}
                    <button
                      onClick={() => patchStall({ paused: '' })}
                      disabled={stallBusy}
                      className="text-zinc-600 hover:text-zinc-300 shrink-0 disabled:opacity-40"
                      title="Снять паузу"
                    >
                      ✕
                    </button>
                  </div>
                )}

                {/* Долг этапа: что задача прошла, не закрыв. Считается по её
                    положению и требованиям проекта, поэтому во frontmatter его
                    нет — там лежат только факты (`confirmed`, `waived`).
                    Гасит его агент: --confirm либо --waive с причиной */}
                {task?.debt?.length > 0 && (
                  <div className="flex items-start gap-2 text-xs">
                    <span className="shrink-0 text-amber-300/80">⚠ долг</span>
                    <span className="text-zinc-400 min-w-0">
                      {task.debt.map((d) => d.text).join('; ')}
                    </span>
                  </div>
                )}

                {/* Списанные требования: причина каждого — строкой в комментариях */}
                {task?.waived?.length > 0 && (
                  <div className="flex items-start gap-2 text-xs">
                    <span className="shrink-0 text-rose-400/80">⚠ списано</span>
                    <span className="text-zinc-400 min-w-0">
                      {task.waived.map((w) => w.text).join('; ')}
                    </span>
                  </div>
                )}

                {/* Кого держит эта задача — справочно: снимают блокировку с той
                    стороны, где она объявлена */}
                {stall?.blocks_tasks?.length > 0 && (
                  <div className="text-xs text-zinc-600">
                    держит: {stall.blocks_tasks.map((b) => b.id).join(', ')}
                  </div>
                )}

                {stallForm === 'pause' && (
                  <ReasonPrompt
                    label="Пауза:"
                    placeholder="ждём ответ контрагента"
                    value={stall?.paused || ''}
                    busy={stallBusy}
                    buttonClassName={STALL_BUTTON}
                    onSubmit={(reason) => patchStall({ paused: reason })}
                    onCancel={() => setStallForm(null)}
                  />
                )}

                {/* Форма блокировки повторяет форму паузы: подпись, поле той же
                    высоты, кнопка, отмена. Разная конструкция у двух соседних
                    полей читается как разный смысл, хотя действие одно */}
                {/* Место под подпись об ошибке держим всегда, пока форма открыта:
                    сама подпись лежит вне потока и иначе наезжает на границу шапки */}
                {stallForm === 'block' && (
                  <div className="flex items-center gap-2 pb-4">
                    <span className="text-xs text-zinc-500 shrink-0">Ждёт:</span>
                    {/* Кандидатов считает бэкенд: без завершённых, отменённых
                        и тех, кто сам ждёт эту задачу (иначе цикл) */}
                    <TaskPicker
                      className="flex-1 min-w-0"
                      inputClassName={INLINE_FIELD}
                      value={blockId}
                      onChange={(v, found) => { setBlockId(v); setBlockTask(found) }}
                      onEnter={addBlocker}
                      onEscape={() => { setStallForm(null); setBlockId(''); setBlockTask(null) }}
                      blockerFor={taskId}
                      placeholder="TASK-NNN"
                      autoFocus
                    />
                    <button
                      onClick={addBlocker}
                      disabled={stallBusy || !blockTask}
                      className={STALL_BUTTON}
                    >
                      Заблокировать
                    </button>
                    <button
                      onClick={() => { setStallForm(null); setBlockId(''); setBlockTask(null) }}
                      className="shrink-0 px-1.5 py-1 text-xs text-zinc-500 hover:text-zinc-200 transition"
                      title="Отменить (Esc)"
                    >
                      ✕
                    </button>
                  </div>
                )}

                {/* В терминальном статусе простой не ставится вовсе — кнопок
                    просто нет: подпись про недоступное действие только шумит */}
                {!stallForm && stall?.can_set && (
                  <div className="flex items-center gap-3 text-[11px] text-zinc-500">
                    <button className="hover:text-zinc-300 transition"
                            onClick={() => setStallForm('block')}>
                      + блокировка
                    </button>
                    <button className="hover:text-zinc-300 transition"
                            onClick={() => setStallForm('pause')}>
                      {stall?.paused ? 'изменить паузу' : '+ пауза'}
                    </button>
                  </div>
                )}

                {stallError && <div className="text-xs text-rose-400">{stallError}</div>}
              </div>
            )}
          </div>
          {task && (
            <CopyButton
              className="ml-auto"
              text={`${task.meta?.title || taskId}\n\n${body}`}
              title="Копировать содержимое задачи"
            />
          )}
          <button
            onClick={onClose}
            className={`${task ? '' : 'ml-auto'} text-zinc-500 hover:text-zinc-200 text-xl leading-none px-2`}
            title="Закрыть (Esc)"
          >
            ×
          </button>
        </div>

        <div className={`overflow-y-auto px-5 py-4 md-body text-sm ${style.mdTint}`}>
          {error && <div className="text-rose-400">{error}</div>}
          {!task && !error && <div className="text-zinc-500">Загрузка…</div>}
          {task && blocks.map((block, i) => (
            <div key={i} className="md-block group relative">
              {block.type === 'section' && editSection === block.key ? (
                <>
                  <SectionHeading heading={block.heading} />
                  {/* Ширина поля — та же колонка, в которой текст читается:
                      раздвигать под правку окно незачем, вырастает только высота */}
                  <MarkdownEditor
                    value={sectionText}
                    onChange={setSectionText}
                    onSave={saveSection}
                    onCancel={cancelSection}
                    saving={sectionSaving}
                    error={sectionError}
                    preview={sectionPreview}
                    onPreviewChange={setSectionPreview}
                    minRows={10}
                  />
                </>
              ) : (
                <>
                  {/* Карандаш стоит вплотную за словом заголовка — как у названия
                      задачи. Клик по самому тексту правку не открывает: описание
                      читают и копируют кусками, перехваченный клик мешал бы */}
                  {block.type === 'section' && (
                    <SectionHeading heading={block.heading} query={query}>
                      {!editSection && (
                        <TitleActions>
                          <button
                            onClick={() => startSection(block)}
                            className="w-6 h-6 flex items-center justify-center text-base text-zinc-600
                              hover:text-zinc-300 opacity-0 group-hover:opacity-100 focus:opacity-100
                              transition-opacity"
                            title="Изменить текст"
                          >✎</button>
                        </TitleActions>
                      )}
                    </SectionHeading>
                  )}
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins}
                                 components={mdComponents}>
                    {block.text}
                  </ReactMarkdown>
                </>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
