import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DndContext, DragOverlay, PointerSensor, pointerWithin, rectIntersection, useDroppable, useSensor, useSensors } from '@dnd-kit/core'
import { api, subscribeChanges } from './api'
import { isDropAllowed, defaultColumnOrder, setPipeline, statusStyle } from './statuses'
import { taskCopyText } from './taskText'
import Header from './components/Header'
import Column, { CollapsedColumn } from './components/Column'
import TaskModal from './components/TaskModal'
import EpicModal from './components/EpicModal'
import NewTaskModal from './components/NewTaskModal'
import LogsPanel from './components/LogsPanel'
import SettingsModal from './components/SettingsModal'
import ScaffoldModal from './components/ScaffoldModal'
import AgenticStaleModal from './components/AgenticStaleModal'
import BoardRepairModal from './components/BoardRepairModal'
import ReasonPrompt from './components/ReasonPrompt'
import HelpModal from './components/HelpModal'
import UpdateModal from './components/UpdateModal'
import ContextMenu from './components/ContextMenu'

// Колонки таскаем по указателю (pointerWithin), карточки — по пересечению
// прямоугольников: для карточки важна площадь перекрытия, иначе вставка между
// соседями ловилась бы пиксель в пиксель.
//
// **Свёрнутые колонки — исключение, и без него они почти недостижимы.**
// `rectIntersection` сравнивает площадь пересечения с оверлеем карточки (288px
// шириной), а полоса — 40px: стоящая между развёрнутыми колонками, она всегда
// проигрывает соседям, которых тот же оверлей накрывает сильнее. Заметно это
// было по краю доски — там соседа нет, и полоса «вдруг» начинала ловиться.
// Поэтому цель-полосу решает указатель: он либо над ней, либо нет.
function makeCollisionDetection(collapsed) {
  return (args) => {
    if (args.active?.data?.current?.type === 'columnHeader') return pointerWithin(args)
    if (collapsed.size) {
      const strip = pointerWithin(args).filter(
        (c) => typeof c.id === 'string' && c.id.startsWith('col:')
          && collapsed.has(c.id.slice(4)),
      )
      if (strip.length) return strip
    }
    return rectIntersection(args)
  }
}

// Дроп-зона после последней колонки: перенос колонки в самый конец
function ColumnEndZone({ active }) {
  const { setNodeRef, isOver } = useDroppable({ id: 'col-end', data: { type: 'columnEnd' } })
  return (
    <div ref={setNodeRef} className="relative w-5 shrink-0">
      {isOver && active && (
        <div className="absolute left-1 top-2 bottom-2 w-0.5 bg-sky-400 rounded-full
          shadow-[0_0_8px_rgba(56,189,248,0.9)] pointer-events-none" />
      )}
    </div>
  )
}

export default function App() {
  const [board, setBoard] = useState(null)
  const [health, setHealth] = useState(null)
  const [projects, setProjects] = useState({ active: null, projects: [] })
  const [openTask, setOpenTask] = useState(null)
  const [openEpic, setOpenEpic] = useState(null)
  // Путь по окнам: из карточки уходят по номеру блокера, из окна эпика — в его
  // задачу, и вернуться нужно туда, откуда пришли, а не искать исходное окно
  // заново. Поэтому в следе лежит не номер задачи, а вид целиком: задача и эпик
  // ходят по одному стеку, иначе «назад» из задачи в эпик потребовал бы второго
  const [taskTrail, setTaskTrail] = useState([])
  // Текущий вид — то, куда вернётся «назад» из следующего окна
  const currentView = () => (openTask ? { task: openTask } : { epic: openEpic })
  const viewLabel = (view) => (view?.task || view?.epic || '')
  const showView = (view) => {
    setOpenTask(view?.task || null)
    setOpenEpic(view?.epic || null)
  }
  const pushView = (view) => {
    setTaskTrail([...taskTrail, currentView()])
    showView(view)
  }
  const goBack = () => {
    showView(taskTrail[taskTrail.length - 1])
    setTaskTrail(taskTrail.slice(0, -1))
  }
  const closeViews = () => { setOpenTask(null); setOpenEpic(null); setTaskTrail([]) }
  const [showNewTask, setShowNewTask] = useState(false)
  // Копируемая задача: форма создания открывается предзаполненной её данными.
  // Копия начинает с бэклога, как любая новая задача, — место оригинала на
  // доске принадлежит его работе, а не замыслу
  const [copySource, setCopySource] = useState(null)
  const closeNewTask = () => { setShowNewTask(false); setCopySource(null) }
  const [showLogs, setShowLogs] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  // Вкладка, ради которой настройки открыли: из шестерёнки — никакая (запомнится
  // прошлая), из плашки «что нового» — та, про которую там и говорилось
  const [settingsTab, setSettingsTab] = useState(null)
  const openSettings = (tab = null) => { setSettingsTab(tab); setShowSettings(true) }
  const [showScaffold, setShowScaffold] = useState(false)
  const [showAgentic, setShowAgentic] = useState(false)
  const [showRepair, setShowRepair] = useState(false)
  const [showUpdate, setShowUpdate] = useState(false)
  // Точка у кнопки обновления. Читается из кэша сервера — сеть тут не задета
  const [updateAvailable, setUpdateAvailable] = useState(false)
  // Помощь: null — закрыта, иначе раздел, на котором её открыли
  const [helpSection, setHelpSection] = useState(null)
  // Живой поиск: строка ввода и результат с бэкенда (id → попадания).
  // Ищем на сервере, потому что содержание задач лежит в файлах, а не на доске
  const [query, setQuery] = useState('')
  const [found, setFound] = useState(null)
  // Фильтр «стоят»: остановленные задачи разом, каждая на своём этапе —
  // того, чего отдельный раздел доски не даёт
  const [stalledOnly, setStalledOnly] = useState(false)
  // Перенос остановленной задачи в работу: спрашиваем, а не запрещаем
  const [pendingMove, setPendingMove] = useState(null)
  // Перенос в съезд с маршрута: без причины отмены он не состоится
  const [pendingCancel, setPendingCancel] = useState(null)
  // Перенос, после которого задача останется с долгом этапа: предупреждаем,
  // но не запрещаем — гейт стоит только на пути агента
  const [pendingDebt, setPendingDebt] = useState(null)
  const [dndFullBoard, setDndFullBoard] = useState(false)
  // Открытое контекстное меню карточки: задача, её колонка и точка вызова
  const [menuFor, setMenuFor] = useState(null)
  const configLoaded = useRef(false)
  const [activeDrag, setActiveDrag] = useState(null)
  // Порядок колонок живёт только на фронте (localStorage), с файлом доски не синкается.
  // Ключ per-project: у каждого проекта свой порядок колонок
  const orderKey = (projectName) => `taskboard:columnOrder:${projectName || '_'}`
  const [columnOrder, setColumnOrder] = useState(null)
  // Свёрнутые колонки — рядом с порядком и по тому же принципу: это вид доски
  // у конкретного человека, а не свойство репозитория. Хранится **явное**
  // решение по колонке ('collapsed' | 'expanded'), потому что настройка
  // «скрывать пустые» задаёт лишь поведение по умолчанию: развернул пустую
  // руками — она должна остаться развёрнутой, а не схлопнуться на перерисовке
  const collapsedKey = (projectName) => `taskboard:collapsedColumns:${projectName || '_'}`
  const [collapsedState, setCollapsedState] = useState({})
  // Цель вставки колонки: {status, side: 'before'|'after'|'end'}
  const [colDropTarget, setColDropTarget] = useState(null)
  const [error, setError] = useState(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))

  // Помощь открывается там, где возник вопрос: сразу на нужном разделе
  const openHelp = (section = null) => setHelpSection(section || 'start')

  // Esc закрывает вопрос о переносе и ввод причины — как и любое окно доски
  useEffect(() => {
    if (!pendingMove && !pendingCancel && !pendingDebt) return
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      setPendingMove(null)
      setPendingCancel(null)
      setPendingDebt(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pendingMove, pendingCancel, pendingDebt])

  // Ввод опережает сеть: запрос уходит после паузы, иначе каждая буква — запрос
  useEffect(() => {
    const needle = query.trim()
    if (!needle) {
      setFound(null)
      return
    }
    const timer = setTimeout(() => {
      api.search(needle)
        .then((r) => setFound(new Map(r.items.map((i) => [i.id, i]))))
        .catch((e) => setError(e.message))
    }, 200)
    return () => clearTimeout(timer)
  }, [query, board])

  // Удаление задачи крестиком — только когда включено в настройках проекта.
  // Карточка сначала спрашивает план (кого задача держит), потом подтверждение
  const deleteTask = health?.config?.delete_tasks ? {
    plan: async (id) => {
      try {
        return await api.deleteTaskPlan(id)
      } catch (e) {
        setError(e.message)
        return { blocks: [] }
      }
    },
    remove: async (id) => {
      try {
        await api.deleteTask(id)
        if (openTask === id) setOpenTask(null)
        refresh()
      } catch (e) {
        setError(e.message)
      }
    },
  } : null

  // В файл уезжает только «свёрнута»: это решение долгоживущее — колонку убрали
  // с глаз, и она должна остаться убранной. «Развёрнута» живёт лишь до
  // перезагрузки, потому что разворачивают обычно чтобы **посмотреть**, а не
  // чтобы закрепить: сохранённое, оно потом молча спорит с настройкой
  // «сворачивать пустые», и колонка стоит развёрнутой без видимой причины
  const persistCollapsed = (state) => {
    const kept = Object.fromEntries(
      Object.entries(state).filter(([, v]) => v === 'collapsed'))
    localStorage.setItem(collapsedKey(projects.active), JSON.stringify(kept))
  }

  const setColumnCollapsed = (status, collapsed) => {
    const next = { ...collapsedState, [status]: collapsed ? 'collapsed' : 'expanded' }
    setCollapsedState(next)
    persistCollapsed(next)
  }

  // Снять решение по колонке, не заявляя обратного: дальше её судьбу снова
  // решает настройка «скрывать пустые»
  const forgetColumnCollapsed = (status) => {
    if (!(status in collapsedState)) return
    const next = { ...collapsedState }
    delete next[status]
    setCollapsedState(next)
    persistCollapsed(next)
  }

  // Включение настройки — свежее указание человека, и оно сильнее прежних
  // частных «развернул посмотреть». Иначе колонка, которую разворачивали час
  // назад, остаётся стоять пустой среди свёрнутых, и понять почему нельзя
  const hideEmpty = !!health?.config?.hide_empty_columns
  // `null` — «настройка ещё не известна»: конфиг приезжает с health уже после
  // первого рендера, и без этого различия каждая загрузка страницы читалась бы
  // как включение настройки человеком со всеми последствиями (TASK-173)
  const prevHideEmpty = useRef(null)
  useEffect(() => {
    if (!health) return
    const prev = prevHideEmpty.current
    prevHideEmpty.current = hideEmpty
    if (prev !== false || !hideEmpty) return
    // Запись в хранилище идёт здесь, а не из updater'а состояния: updater React
    // выполняет во время следующего рендера — то есть уже после того, как эффект
    // смены проекта прочитал localStorage. Свёрнутые колонки при этом оставались
    // на экране, но в хранилище уезжал пустой набор
    const kept = Object.fromEntries(
      Object.entries(collapsedState).filter(([, v]) => v === 'collapsed'))
    setCollapsedState(kept)
    persistCollapsed(kept)
  }, [hideEmpty, health])

  const saveColumnOrder = (order) => {
    setColumnOrder(order)
    const key = orderKey(projects.active)
    if (order) localStorage.setItem(key, JSON.stringify(order))
    else localStorage.removeItem(key)
  }

  // При смене активного проекта подгружаем его порядок колонок;
  // разовая миграция старого глобального ключа на первый встречный проект
  const prevProject = useRef(undefined)
  useEffect(() => {
    const active = projects.active
    if (active === prevProject.current) return
    prevProject.current = active
    let saved = null
    try { saved = JSON.parse(localStorage.getItem(orderKey(active))) } catch { /* нет ключа */ }
    if (!saved) {
      try {
        const legacy = JSON.parse(localStorage.getItem('taskboard:columnOrder'))
        if (legacy) {
          saved = legacy
          localStorage.setItem(orderKey(active), JSON.stringify(legacy))
        }
      } catch { /* нет legacy */ }
      localStorage.removeItem('taskboard:columnOrder')
    }
    setColumnOrder(saved || null)
    let folded = null
    try { folded = JSON.parse(localStorage.getItem(collapsedKey(active))) } catch { /* нет ключа */ }
    setCollapsedState(folded && typeof folded === 'object' ? folded : {})
  }, [projects.active])

  const refresh = useCallback(async () => {
    try {
      const [h, p] = await Promise.all([api.health(), api.projects()])
      setHealth(h)
      setProjects(p)
      // Тумблер DnD привязан к конфигу: подхватываем сохранённое значение один раз
      if (!configLoaded.current && h?.config) {
        setDndFullBoard(!!h.config.dnd_full_board)
        configLoaded.current = true
      }
      if (h?.report?.ok) {
        setBoard(await api.board())
      } else {
        setBoard(null)
      }
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Размеры превью — CSS-переменными на корне: карточек на доске десятки,
  // и менять их все через пропсы ради трёх чисел незачем (TASK-097)
  useEffect(() => {
    const style = board?.config?.card_style
    if (!style) return
    const root = document.documentElement
    root.style.setProperty('--card-title-size', `${style.card_title_size}px`)
    root.style.setProperty('--card-title-lines', String(style.card_title_lines))
    root.style.setProperty('--card-meta-size', `${style.card_meta_size}px`)
    // Видимость метки типа — тем же способом: показ/скрытие одного элемента
    // не стоит того, чтобы тащить флаг пропсами через колонку в каждую карточку
    root.style.setProperty('--card-type-display',
      style.card_show_type === false ? 'none' : 'inline-flex')
  }, [board?.config?.card_style])

  // Точка «есть новая версия» — из кэша сервера, один раз при открытии доски.
  // Проверка обновлений в путь загрузки доски не попадает: она идёт фоном
  // на сервере и только при согласии пользователя
  useEffect(() => {
    api.updateStatus()
      .then((s) => setUpdateAvailable(!!s.update_available && s.mode !== 'off'))
      .catch(() => {})
  }, [])

  // Первое открытие проекта: состав окружения зависит от сред, в которых с ним
  // работают, — а это знает только пользователь. Спрашиваем один раз на проект;
  // дальше выбор живёт в его конфиге, и вопрос больше не всплывает
  const harnessAsked = useRef(null)
  useEffect(() => {
    const report = health?.report
    if (!report || report.harnesses?.choice) return
    // Структуры нет вовсе — там свой экран с той же кнопкой, не перехватываем
    if (['missing', 'no_board'].includes(report.structure)) return
    if (harnessAsked.current === projects.active) return
    harnessAsked.current = projects.active
    setShowScaffold(true)
  }, [health, projects.active])

  // Живые обновления от watcher'а (агент двигает задачи — доска перечитывается).
  // Вторым каналом приезжает находка проверки обновлений: точка у кнопки
  // читается из кэша при загрузке и сама бы не зажглась (TASK-126)
  useEffect(() => subscribeChanges(
    () => refresh(),
    () => api.updateStatus()
      .then((s) => setUpdateAvailable(!!s.update_available && s.mode !== 'off'))
      .catch(() => { /* окно обновления покажет причину, доске это не мешает */ }),
  ), [refresh])

  const switchProject = async (name) => {
    if (!name) return
    await api.activateProject(name)
    refresh()
  }

  // --- DnD ---

  // Жизненный цикл проекта: порядок колонок, цвета и подписи статусов.
  // Кладём в модуль до отрисовки — цвет нужен карточкам в глубине дерева
  const pipeline = board?.config?.pipeline
  setPipeline(pipeline)

  // Откуда берут работу и куда попадают новые задачи — от них зависят правила DnD
  const pickStatus = board?.config?.actions?.pick || board?.config?.queued_status || 'queued'
  const createStatus = board?.config?.actions?.create || 'backlog'

  // Колонки в порядке: сохранённый пользователем → пайплайн → новые разделы в хвост
  const orderedColumns = useMemo(() => {
    const cols = board?.columns || []
    const saved = columnOrder || defaultColumnOrder()
    const byStatus = new Map(cols.map((c) => [c.status, c]))
    const ordered = saved.filter((s) => byStatus.has(s)).map((s) => byStatus.get(s))
    const rest = cols.filter((c) => !saved.includes(c.status))
    return [...ordered, ...rest]
  }, [board, columnOrder, pipeline])

  // Доска под фильтром: колонки остаются на местах (структура не должна
  // прыгать под руками), внутри — только подходящие задачи. Фильтры
  // складываются: «стоят» сужает найденное, а не заменяет поиск
  const filtered = !!found || stalledOnly
  const visibleColumns = useMemo(() => {
    if (!filtered) return orderedColumns
    const keep = (t) => (!found || found.has(t.id)) && (!stalledOnly || t.stalled)
    return orderedColumns.map((col) => ({
      ...col,
      groups: col.groups
        .map((g) => ({ ...g, tasks: g.tasks.filter(keep) }))
        .filter((g) => g.tasks.length),
    }))
  }, [orderedColumns, found, stalledOnly])

  // Какие колонки показывать полосой. Порядок разбора: явное решение человека
  // сильнее всего, затем настройка «скрывать пустые», иначе колонка развёрнута.
  //
  // Под фильтром колонка с совпадениями разворачивается **всегда**: найденное,
  // оставшееся внутри свёрнутой полосы, — это молчаливо потерянный результат
  // поиска, и человек считает, что задачи нет
  //
  // Хранится не набор статусов, а причина по каждому: свёрнутая вручную и
  // свёрнутая настройкой полосы выглядят одинаково, и различить их можно
  // только словами в подсказке
  const collapsed = useMemo(() => {
    // Пустоту считаем по **реальному** составу колонки, а не по отфильтрованному:
    // настройка описывает постоянное свойство («задач нет»), и фильтр не должен
    // на него влиять. Иначе поиск, будучи живым, схлопывал и разворачивал каркас
    // на каждой набранной букве — в обе стороны
    const total = new Map(orderedColumns.map(
      (c) => [c.status, c.groups.reduce((n, g) => n + g.tasks.length, 0)]))

    const out = new Map()
    for (const col of visibleColumns) {
      const decided = collapsedState[col.status]
      const byHand = decided === 'collapsed'
      if (!byHand && !(!decided && hideEmpty && !total.get(col.status))) continue
      // Единственное, ради чего фильтр вообще трогает каркас: найденное внутри
      // свёрнутой полосы человек не увидит и решит, что задачи нет
      const matches = col.groups.reduce((n, g) => n + g.tasks.length, 0)
      if (filtered && matches) continue
      out.set(col.status, byHand ? 'manual' : 'empty')
    }
    return out
  }, [visibleColumns, orderedColumns, collapsedState, hideEmpty, filtered])

  // Детектор пересобирается вместе с набором свёрнутых: он должен знать, какие
  // цели узкие, — иначе полоса снова начнёт проигрывать соседям по площади
  const collisionDetection = useMemo(
    () => makeCollisionDetection(collapsed), [collapsed])

  const findColumn = (status) => board?.columns.find((c) => c.status === status)

  // Задача и её колонка по номеру — нужны, чтобы назвать блокер в вопросе
  const findTask = (taskId) => {
    for (const col of board?.columns || []) {
      for (const g of col.groups) {
        for (const t of g.tasks) if (t.id === taskId) return { task: t, column: col }
      }
    }
    return null
  }

  // Сколько задач стоит — цифра рядом с фильтром
  const stalledCount = useMemo(() => {
    let n = 0
    for (const col of board?.columns || []) {
      for (const g of col.groups) n += g.tasks.filter((t) => t.stalled).length
    }
    return n
  }, [board])

  // Аномален ровно один переход — «взять в работу»: блокировка это и значит,
  // «не начинай, пока та не готова». Ждать внутри ревью, тестирования или
  // релиза законно (две задачи проверяются только вместе), назад по маршруту —
  // тем более, а в терминальные простой снимается сам
  const isStartOfWork = (status) => {
    const actions = board?.config?.actions || {}
    return !!status && (status === actions.start || status === actions.return)
  }

  const cardPosition = (status, taskId) => {
    const col = findColumn(status)
    if (!col) return null
    let i = 0
    for (const g of col.groups) {
      for (const t of g.tasks) {
        if (t.id === taskId) return i
        i++
      }
    }
    return null
  }

  const isAllowed = (from, to) => isDropAllowed(from, to, dndFullBoard, pickStatus, createStatus)

  const onDragStart = (event) => {
    setMenuFor(null)
    const data = event.active.data.current || {}
    if (data.type === 'columnHeader') {
      setActiveDrag({ column: { status: data.status, title: data.title } })
    } else if (data.task) {
      setActiveDrag({ task: data.task, fromStatus: data.fromStatus })
    }
  }

  // Отслеживаем цель вставки колонки: указатель в левой половине цели — «перед»,
  // в правой — «после»; в зазорах цель липкая (не сбрасываем)
  const onDragOver = (event) => {
    if (!activeDrag?.column) return
    const { over, delta, activatorEvent } = event
    if (!over) return
    const data = over.data.current || {}
    if (data.type === 'columnEnd') {
      setColDropTarget({ status: null, side: 'end' })
      return
    }
    const status = data.status
    if (!status || status === activeDrag.column.status) {
      setColDropTarget(null)
      return
    }
    const startX = activatorEvent?.clientX ?? 0
    const pointerX = startX + (delta?.x ?? 0)
    const rect = over.rect
    const side = rect && pointerX > rect.left + rect.width / 2 ? 'after' : 'before'
    setColDropTarget({ status, side })
  }

  const onDragEnd = async (event) => {
    setActiveDrag(null)
    const { active, over } = event

    // Перетаскивание колонки: порядок только на фронте
    if (active.data.current?.type === 'columnHeader') {
      const dragged = active.data.current.status
      const target = colDropTarget
      setColDropTarget(null)
      if (!target) return
      const current = orderedColumns.map((c) => c.status)
      if (!current.includes(dragged)) return
      const next = current.filter((s) => s !== dragged)
      if (target.side === 'end') {
        next.push(dragged)
      } else {
        const to = next.indexOf(target.status)
        if (to < 0) return
        next.splice(target.side === 'after' ? to + 1 : to, 0, dragged)
      }
      saveColumnOrder(next)
      return
    }

    if (!over) return
    const from = active.data.current?.fromStatus
    const taskId = active.data.current?.taskId
    const to = over.data.current?.status
    if (!taskId || !from || !to || !isAllowed(from, to)) return

    let position = null
    let afterTaskId = null
    let group = null
    const overType = over.data.current?.type
    if (overType === 'card') {
      position = cardPosition(to, over.data.current.taskId)
      // Карточка уйдёт из колонки раньше цели — скорректировать индекс
      if (from === to && position !== null && cardPosition(from, taskId) < position) {
        position -= 1
      }
    } else if (overType === 'tail') {
      // Точная вставка после конкретной задачи (конец подраздела);
      // пустой подраздел — по имени группы (якорной задачи нет)
      afterTaskId = over.data.current.afterTaskId
      if (!afterTaskId) group = over.data.current.groupTitle || null
      if (afterTaskId === taskId) return // дроп на собственный хвост — no-op
    } else if (overType === 'column') {
      // Дроп на пустое место колонки — явно в её конец
      position = (findColumn(to)?.groups || []).reduce((n, g) => n + g.tasks.length, 0)
    }

    startMove(taskId, from, to, { position, afterTaskId, group })
  }

  // Контекстное меню карточки: открывается правым кликом, состав собирается
  // здесь — доска знает и правила переноса, и колонки, а превью задачи не знает
  // ни того, ни другого
  const openTaskMenu = (task, status, e) => setMenuFor({ task, status, x: e.clientX, y: e.clientY })

  const copyToClipboard = async (text) => {
    try { await navigator.clipboard.writeText(text) } catch { /* буфер недоступен */ }
  }

  // Содержимое задачи на доске не лежит — тело файла приходит отдельным запросом.
  // Формула текста общая с окном задачи (`taskCopyText`): «скопировать
  // содержимое» обязано давать одно и то же, откуда бы его ни нажали
  const copyTaskText = async (taskId) => {
    try {
      await copyToClipboard(taskCopyText(await api.task(taskId), taskId))
    } catch (e) {
      setError(e.message)
    }
  }

  // Копия задачи: содержимое лежит в файле, а не на доске, — сначала грузим
  // задачу, потом открываем форму с её полями. Наследуется только написанное
  // человеком: название, описание, критерии, тип, эпик и простой
  const openCopy = async (taskId) => {
    try {
      const task = await api.task(taskId)
      const section = (key) => (task.sections || []).find((s) => s.key === key)?.text || ''
      const meta = task.meta || {}
      const value = (v) => (v && v !== '~' ? v : '')
      setCopySource({
        id: taskId,
        title: value(meta.title),
        description: section('description'),
        criteria: section('criteria'),
        task_type: value(meta.type),
        epic: value(meta.epic),
        blocked_by: task.stall?.blocked_by || [],
        paused: task.stall?.paused || '',
      })
      setShowNewTask(true)
    } catch (e) {
      setError(e.message)
    }
  }

  const menuItems = useMemo(() => {
    if (!menuFor) return []
    const { task, status } = menuFor
    // Правило переноса одно на все способы: настройка «перетаскивание по всей
    // доске» ограничивает и меню — иначе она обходилась бы правым кликом
    const targets = visibleColumns.filter(
      (col) => col.status !== status && isAllowed(status, col.status))
    const items = []
    if (targets.length) {
      items.push({ key: 'move', group: 'Перенести в' })
      for (const col of targets) {
        items.push({
          key: `move:${col.status}`,
          label: col.title,
          dot: statusStyle(col.status).dot,
          // В начало колонки: перенос из меню — про смену этапа, а не про место
          // в очереди; точное место по-прежнему выбирают мышью
          onSelect: () => startMove(task.id, status, col.status, { position: 0 }),
        })
      }
    }
    items.push({ key: 'copy', group: 'Копировать' })
    items.push({ key: 'copy-id', label: 'Номер задачи', hint: task.id,
                 onSelect: () => copyToClipboard(task.id) })
    items.push({ key: 'copy-body', label: 'Содержимое задачи',
                 onSelect: () => copyTaskText(task.id) })
    // Копия задачи — в той же группе: для человека это «скопировать» той же
    // рукой, что номер и текст, только результат кладётся не в буфер, а на
    // доску — потому и «целиком»
    items.push({ key: 'copy-task', label: 'Задачу целиком',
                 onSelect: () => openCopy(task.id) })
    return items
  }, [menuFor, visibleColumns, dndFullBoard, board])

  // Перенос задачи — общий путь для мыши и контекстного меню. Вопросы по дороге
  // (простой, долг этапа, причина отмены) одни и те же: способ переноса на них
  // не влияет, а два пути с разными вопросами разъехались бы молча
  const startMove = (taskId, from, to, where = {}) => {
    const sectionTitle = findColumn(to)?.title || to
    const move = {
      taskId, sectionTitle, toStatus: to,
      position: null, afterTaskId: null, group: null, ...where,
    }

    // Заблокированную задачу берут в работу случайно — доска ведь не помнит,
    // чего она ждёт. Запрещать не за что (доска остаётся правдой пользователя),
    // поэтому переспрашиваем и называем причину
    const card = findTask(taskId)?.task
    if (card?.stalled && from !== to && isStartOfWork(to)) {
      setPendingMove({ ...move, task: card, toTitle: sectionTitle })
      return
    }
    // Долг этапа руку не гейтит — но цену переноса человек должен видеть до
    // того, как задача уехала, а не узнавать её от агента через два этапа.
    // Спрашиваем бэкенд: считать долг на фронте значило бы завести третье
    // зеркало движка, которое разъедется с двумя первыми
    if (from !== to) {
      askDebtThenMove(move, sectionTitle, findColumn(from)?.title || from)
      return
    }
    applyMove(move)
  }

  // Перенос между колонками: сначала вопрос о будущем долге, потом движение.
  // Молчаливый провал вопроса не должен мешать переносу — доска остаётся
  // источником правды, а долг никуда не денется и всплывёт у агента
  const askDebtThenMove = async (move, sectionTitle, fromTitle) => {
    try {
      const result = await api.moveDebt(move.taskId, sectionTitle)
      if (result?.debt?.length) {
        // Долг делится по субъекту: `confirm` закрывает человек нажатием (он и
        // есть тот, чьё подтверждение требуется), остальное — работой
        const debt = result.debt
        setPendingDebt({
          ...move, toTitle: sectionTitle, fromTitle, debt,
          // Конец маршрута: долгом невыполненное там не станет — в терминальном
          // статусе он не считается. Значит и обещать «агент закроет позже»
          // нельзя: закрывать некому. Зато это последний момент, когда
          // требование ещё можно выполнить, и сказать надо именно это
          terminal: Boolean(result.terminal),
          confirmable: debt.filter((d) => d.confirmable),
          blocking: debt.filter((d) => !d.confirmable),
        })
        return
      }
    } catch { /* вопрос не удался — переносим как раньше */ }
    applyMove(move)
  }

  // Подтвердить своей рукой и перенести. Ошибка записи не отменяет перенос:
  // доска остаётся источником правды, а незакрытое требование всплывёт долгом
  const confirmThenMove = async (move) => {
    try {
      await api.confirmRequirements(move.taskId, move.confirmable.map((d) => d.id),
                                    move.toTitle)
    } catch (e) {
      setError(e.message)
    }
    applyMove(move)
  }

  // clearStall — задачу берут в работу, значит простой обычно уже неактуален:
  // оставленная пометка врала бы про то, чего задача не ждёт.
  // confirm — признак «пользователь подтвердил»: правило проверяет сервер, и
  // без признака он откажет (доска не единственный клиент)
  const applyMove = async (move, clearStall = false, confirm = false, reason = null) => {
    const { taskId, sectionTitle, position, afterTaskId, group } = move
    try {
      if (clearStall) await api.updateTask(taskId, { blocked_by: [], paused: '' })
      await api.moveTask(taskId, sectionTitle, position, afterTaskId, group, confirm, reason)
      // Сначала доска, потом решение о сворачивании — и только в таком порядке.
      // Снятое раньше, оно заставало колонку ещё пустой: настройка «сворачивать
      // пустые» тут же схлопывала её в полосу, и та разворачивалась обратно,
      // когда приезжала задача. Мигание на ровном месте.
      await refresh()
      // Задача уехала в свёрнутую колонку — разворачиваем: перенос без видимого
      // результата человек всё равно проверяет, разворачивая полосу сам.
      // Забываем решение, а не записываем «развёрнута»: колонка опустеет —
      // и снова свернётся по настройке, вместо того чтобы висеть пустой
      if (move.toStatus) forgetColumnCollapsed(move.toStatus)
    } catch (e) {
      // Доска могла не знать о простое (карточку изменили в другом окне) —
      // тогда сервер отказывает, и вопрос задаём по его причине
      if (e.code === 'stall_confirm') {
        setPendingMove({ ...move, task: findTask(taskId)?.task, toTitle: sectionTitle,
                         serverReason: e.message })
        return
      }
      // Съезд с маршрута требует причины: спрашиваем её и повторяем перенос.
      // Правило проверяет сервер, доска только собирает текст
      if (e.code === 'cancel_reason') {
        setPendingCancel({ ...move, toTitle: sectionTitle })
        return
      }
      setError(e.message)
    }
  }

  // Точечное восстановление/обновление части структуры (кнопка в баннере деградаций)
  const DEGRADED_FIX = {
    no_create_script: { part: 'create_script', label: 'Создать' },
    no_logs: { part: 'logs', label: 'Создать' },
    no_epics: { part: 'epics', label: 'Создать' },
    // Шаблон задачи — эталон структуры: по нему создаются новые задачи
    no_template: { part: 'template', label: 'Создать' },
    // Устаревшие файлы поставки ведут в то же окно расхождений, что и скиллы:
    // расхождение может оказаться вашей правкой, и «Обновить» вслепую её
    // потеряет. В окне видно причину, diff и выбор исхода
    outdated_template: { modal: 'agentic', label: 'Подробности', help: 'agentic' },
    outdated_script: { modal: 'agentic', label: 'Подробности', help: 'agentic' },
    no_status_script: { part: 'status_script', label: 'Создать' },
    outdated_status_script: { modal: 'agentic', label: 'Подробности', help: 'agentic' },
    // Скрипт не знает про объявленные требования этапов — чинится тем же
    // обновлением: пользователю всё равно, какую из двух кнопок нажать
    requires_unsupported: { part: 'status_script', label: 'Обновить' },
    // Снимок требований отстал от поставки. Чинится не разворачиванием файлов,
    // а дописыванием в конфиг проекта — поэтому свой путь, а не `part`
    requires_exceptions_stale: { action: 'requiresExceptions', label: 'Дописать',
                                 help: 'lifecycle' },
    // Требование придумал человек — дописать за него нельзя, применимо оно
    // к новому типу или нет, знает только он. Кнопка открывает настройки и
    // гасит повторный вопрос: настроит или решит, что относится, — его дело
    requires_types_unreviewed: { action: 'typesReviewed', label: 'Настроить',
                                 help: 'lifecycle' },
    // Правила — часть механизма, а не опция: без них агент не знает процесса
    no_rules: { part: 'rules', label: 'Развернуть', help: 'agentic' },
    // Отсутствие целой части чинится так же, как её устаревание: одной кнопкой
    no_skills: { part: 'skills', label: 'Развернуть', help: 'agentic' },
    no_commands: { part: 'commands', label: 'Развернуть', help: 'agentic' },
    // Состав поставки зависит от сред, а их знает только пользователь
    no_harness_choice: { modal: 'scaffold', label: 'Настроить', help: 'agentic' },
    // Агентское окружение — не «обновить всё вслепую»: сначала подробности,
    // где видно каждый элемент, его diff и точечное обновление
    outdated_skills: { modal: 'agentic', label: 'Подробности', help: 'agentic' },
    outdated_commands: { modal: 'agentic', label: 'Подробности', help: 'agentic' },
    outdated_rules: { modal: 'agentic', label: 'Подробности', help: 'agentic' },
    // Волт: скиллы уже ссылаются на vault/, поэтому его отсутствие — пробел,
    // а не «ещё не завели»
    no_vault: { part: 'vault', label: 'Развернуть', help: 'agentic' },
    outdated_vault: { modal: 'agentic', label: 'Подробности', help: 'agentic' },
  }
  const fixDegraded = async (code) => {
    const fix = DEGRADED_FIX[code]
    if (!fix) return
    if (fix.modal === 'agentic') {
      setShowAgentic(true)
      return
    }
    if (fix.modal === 'scaffold') {
      setShowScaffold(true)
      return
    }
    if (fix.action === 'requiresExceptions') {
      try {
        await api.applyRequiresExceptions()
        refresh()
      } catch (e) {
        setError(e.message)
      }
      return
    }
    if (fix.action === 'typesReviewed') {
      // Отметку ставим до открытия настроек: вопрос задан, ответ за человеком.
      // Не поставить её значит спрашивать снова каждую загрузку доски
      try {
        await api.markTypesReviewed()
      } catch (e) {
        setError(e.message)
      }
      openSettings('lifecycle')
      refresh()
      return
    }
    try {
      await api.scaffold({ skills: false, commands: false, rules: false, parts: [fix.part] })
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  // Порядок колонок отличается от пайплайна — показываем кнопку сброса.
  // Сравниваем с defaultColumnOrder, а не просто `!!columnOrder`: пустой
  // массив (явный null) и совпадающий с дефолтом порядок скрывают кнопку
  const hasCustomOrder = useMemo(() => {
    if (!columnOrder) return false
    const def = defaultColumnOrder()
    return columnOrder.length !== def.length || columnOrder.some((s, i) => s !== def[i])
  }, [columnOrder])

  // --- Рендер ---

  const report = health?.report
  const features = report?.features || {}

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Header
        projects={projects.projects}
        active={projects.active}
        canCreate={!!features.create_task}
        hasLogs={!!features.logs}
        hasCustomOrder={hasCustomOrder}
        onSwitchProject={switchProject}
        onNewTask={() => setShowNewTask(true)}
        onShowLogs={() => setShowLogs(true)}
        onRefresh={refresh}
        onResetColumns={() => saveColumnOrder(null)}
        onOpenSettings={() => openSettings()}
        onOpenHelp={openHelp}
        onOpenUpdate={() => setShowUpdate(true)}
        updateAvailable={updateAvailable}
        query={query}
        onQuery={setQuery}
        matches={found ? found.size : null}
        stalledOnly={stalledOnly}
        onStalledOnly={setStalledOnly}
        stalledCount={stalledCount}
      />

      {error && (
        <div className="px-4 py-2 bg-rose-950/60 border-b border-rose-800 text-sm text-rose-300">
          {error}
          <button className="ml-3 underline" onClick={() => setError(null)}>скрыть</button>
        </div>
      )}

      {report && !report.ok && !['missing', 'no_board'].includes(report.structure) && (
        <div className="px-4 py-3 bg-rose-950/60 border-b border-rose-800 text-sm text-rose-200 space-y-1">
          <div className="font-semibold flex items-center gap-2">
            Критические проблемы структуры tasks/:
            <button className="text-xs underline text-rose-300/80 hover:text-rose-200"
                    onClick={() => openHelp('validation')}>что это значит?</button>
          </div>
          {report.critical.map((c, i) => <div key={i}>• {c}</div>)}
        </div>
      )}

      {report?.ok && (report.degraded.length > 0 || report.warnings.length > 0) && (
        <div className="px-4 py-2 bg-amber-950/50 border-b border-amber-800/60 text-xs text-amber-200/90">
          {report.degraded.map((d, i) => (
            <span key={`d${i}`} className="mr-4 inline-flex items-center gap-2">
              • {d.message}
              {DEGRADED_FIX[d.code] && (
                <>
                  <button
                    className="px-2 py-0.5 rounded bg-amber-700/50 hover:bg-amber-600/60 text-amber-100 transition"
                    onClick={() => fixDegraded(d.code)}
                  >
                    {DEGRADED_FIX[d.code].label}
                  </button>
                  {/* Вопрос «а что это вообще?» возникает здесь — здесь и ответ */}
                  <button
                    className="text-amber-300/70 hover:text-amber-100 transition"
                    title="Что это значит"
                    onClick={() => openHelp(DEGRADED_FIX[d.code].help || 'validation')}
                  >
                    ?
                  </button>
                </>
              )}
            </span>
          ))}
          {report.warnings.slice(0, 5).map((w, i) => (
            <span key={`w${i}`} className="mr-4">• {w}</span>
          ))}
          {report.warnings.length > 5 && <span className="mr-4">и ещё {report.warnings.length - 5}…</span>}
          {/* Разъехавшиеся концы (доска ↔ файл, blocked_by ↔ blocks) чинятся
              разом — но только после предпросмотра: правки идут по файлам */}
          {report.repairable > 0 && (
            <span className="inline-flex items-center gap-2">
              <button
                className="px-2 py-0.5 rounded bg-amber-700/50 hover:bg-amber-600/60 text-amber-100 transition"
                onClick={() => setShowRepair(true)}
              >
                Починить данные
              </button>
              <button
                className="text-amber-300/70 hover:text-amber-100 transition"
                title="Что это значит"
                onClick={() => openHelp('validation')}
              >
                ?
              </button>
            </span>
          )}
        </div>
      )}

      {report?.ok && !features.queue_section && (
        <div className="px-4 py-2 bg-sky-950/50 border-b border-sky-800/60 text-sm text-sky-200 flex items-center gap-3">
          Раздел очереди (Queue) отсутствует в board.md
          <button
            className="px-3 py-1 text-xs rounded-lg bg-sky-600 hover:bg-sky-500"
            onClick={async () => { await api.ensureQueue(); refresh() }}
          >
            Создать раздел
          </button>
          <button className="text-xs underline text-sky-300/80 hover:text-sky-200"
                  onClick={() => openHelp('lifecycle')}>про очередь и статусы</button>
        </div>
      )}

      <main className="flex-1 overflow-x-auto overflow-y-hidden p-4">
        {['missing', 'no_board'].includes(report?.structure) ? (
          <div className="h-full flex flex-col items-center justify-center gap-3 text-center">
            <div className="text-zinc-300">
              {report.structure === 'missing'
                ? 'В этом проекте ещё нет структуры tasks/'
                : 'В папке tasks нет файла доски'}
            </div>
            <div className="text-xs text-zinc-500 font-mono">{health?.project?.tasks_dir}</div>
            <button
              className="mt-1 px-4 py-2 text-sm rounded-lg bg-sky-600 hover:bg-sky-500 font-medium"
              onClick={() => setShowScaffold(true)}
            >
              Развернуть структуру
            </button>
            <div className="text-xs text-zinc-600">
              board.md со всеми разделами · create_task.py · set_status.py · epics.md ·
              окружение выбранных сред агентов
            </div>
            <button className="text-xs underline text-zinc-500 hover:text-zinc-300"
                    onClick={() => openHelp('start')}>
              Что это создаст и зачем
            </button>
          </div>
        ) : board ? (
          <DndContext
            sensors={sensors}
            collisionDetection={collisionDetection}
            onDragStart={onDragStart}
            onDragOver={onDragOver}
            onDragEnd={onDragEnd}
            onDragCancel={() => { setActiveDrag(null); setColDropTarget(null) }}
          >
            <div className="flex gap-3 h-full items-stretch">
              {visibleColumns.map((col) => {
                const indicator = activeDrag?.column && colDropTarget?.status === col.status
                  ? (colDropTarget.side === 'after' ? 'right' : 'left')
                  : null
                // Свёрнутая колонка рисуется другим компонентом, а не скрытой
                // разметкой: её карточки не должны попадать в DOM вовсе
                return collapsed.has(col.status) ? (
                  <CollapsedColumn
                    key={col.title}
                    column={col}
                    count={col.groups.reduce((n, g) => n + g.tasks.length, 0)}
                    reason={collapsed.get(col.status)}
                    activeFrom={activeDrag?.fromStatus || null}
                    dndFullBoard={dndFullBoard}
                    pickStatus={pickStatus}
                    createStatus={createStatus}
                    columnIndicator={indicator}
                    onExpand={() => setColumnCollapsed(col.status, false)}
                  />
                ) : (
                  <Column
                    key={col.title}
                    column={col}
                    onOpenTask={setOpenTask}
                    activeFrom={activeDrag?.fromStatus || null}
                    dndFullBoard={dndFullBoard}
                    pickStatus={pickStatus}
                    createStatus={createStatus}
                    query={query}
                    matches={found}
                    filtered={filtered}
                    onDelete={deleteTask}
                    onOpenEpic={(key) => pushView({ epic: key })}
                    onCollapse={() => setColumnCollapsed(col.status, true)}
                    onTaskContextMenu={openTaskMenu}
                    columnIndicator={indicator}
                  />
                )
              })}
              <ColumnEndZone active={!!activeDrag?.column} />
            </div>
            {/* Размер обёртки dnd-kit берёт у ручки, за которую тянут. У свёрнутой
                колонки это полоса шириной 40px, и плашка с названием сжималась в
                квадратик. Колонку тащат за разное, а выглядеть это должно
                одинаково — поэтому для колонки размер задаёт содержимое */}
            <DragOverlay dropAnimation={null}
                         style={activeDrag?.column ? { width: 'auto', height: 'auto' } : undefined}>
              {activeDrag?.task && (
                <div className="w-72 cursor-grabbing">
                  <div className="bg-zinc-800 border border-sky-500/70 rounded-lg px-3 py-2
                    shadow-2xl shadow-black/70 scale-[1.04] rotate-1">
                    <div className="text-[11px] font-mono text-zinc-500">{activeDrag.task.id}</div>
                     <div className="text-base text-zinc-300 leading-snug mt-0.5 line-clamp-2">
                      {activeDrag.task.title}
                    </div>
                  </div>
                </div>
              )}
              {activeDrag?.column && (
                <div className="cursor-grabbing">
                  <div className="bg-zinc-800 border border-sky-500/70 rounded-lg px-4 py-2
                    shadow-2xl shadow-black/70 scale-105 -rotate-1 text-sm font-semibold
                    text-zinc-300 whitespace-nowrap">
                    {activeDrag.column.title}
                  </div>
                </div>
              )}
            </DragOverlay>
          </DndContext>
        ) : (
          <div className="h-full flex items-center justify-center text-zinc-500 text-sm">
            {health && !health.project
              ? 'Нет активного проекта — добавьте проект кнопкой «+ проект»'
              : 'Доска недоступна'}
          </div>
        )}
      </main>

      {/* Перенос, после которого останется долг этапа. Не запрет: рука человека
          не гейтится — но он видит цену до движения, а не узнаёт её от агента
          через два этапа. Списать долг отсюда нельзя намеренно: списание —
          инструмент агентского пути, и оно обязано оставлять строку в заметках */}
      {pendingDebt && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
             onClick={() => setPendingDebt(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-md shadow-2xl"
               onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-zinc-800 text-base font-semibold text-amber-200">
              {/* Номер в заголовке: диалог всплывает над доской, где карточек
                  десятки, и «какая именно задача» — первое, что нужно знать */}
              {pendingDebt.confirmable.length > 0
                ? `Задача ${pendingDebt.taskId}: подтвердите перед переносом`
                : pendingDebt.terminal
                  ? `Задача ${pendingDebt.taskId} закрывается с невыполненными требованиями`
                  : `Задача ${pendingDebt.taskId} уедет с долгом`}
            </div>
            <div className="px-5 py-4 space-y-3 text-sm text-zinc-300/90">
              {/* Формулировка требования — утверждение о выполненном («проверку
                  подтвердил человек»), поэтому обрамление даёт ей придаточное:
                  «этап пройден, если …». Идентификатора здесь нет — он нужен
                  тому, кто зовёт скрипт, а не тому, кто читает диалог */}
              {pendingDebt.confirmable.length > 0 && (
                <div className="space-y-1">
                  <div className="text-zinc-400">Вы подтверждаете:</div>
                  <ul className="space-y-1 text-zinc-200">
                    {pendingDebt.confirmable.map((d) => (
                      <li key={d.id}>
                        — {d.text}
                        {d.stage && <span className="text-zinc-500"> · этап «{d.stage}»</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {/* Остальное закрывается работой, а не нажатием: чеклист, секции,
                  поля. Здесь их можно только увидеть */}
              {pendingDebt.blocking.length > 0 && (
                <div className="space-y-1">
                  <div className="text-zinc-500">
                    {pendingDebt.terminal
                      ? 'Останутся невыполненными: это конец маршрута, спросить их будет негде'
                      : pendingDebt.confirmable.length > 0
                        ? 'Останется долгом, агент закроет позже:'
                        : 'Задача уедет с долгом, агент закроет позже:'}
                  </div>
                  <ul className="space-y-1 text-zinc-400">
                    {pendingDebt.blocking.map((d) => (
                      <li key={d.id}>
                        — {d.text}
                        {d.stage && <span className="text-zinc-600"> · этап «{d.stage}»</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
              <button onClick={() => setPendingDebt(null)}
                      className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
                Отмена
              </button>
              <button
                onClick={() => { const m = pendingDebt; setPendingDebt(null); applyMove(m) }}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-700 text-zinc-300
                  hover:border-zinc-500 hover:bg-zinc-800 transition"
              >
                {pendingDebt.confirmable.length > 0
                  ? 'Перенести без подтверждения'
                  : pendingDebt.terminal ? 'Закрыть без них' : 'Перенести с долгом'}
              </button>
              {pendingDebt.confirmable.length > 0 && (
                <button
                  onClick={() => { const m = pendingDebt; setPendingDebt(null); confirmThenMove(m) }}
                  className="px-4 py-2 text-sm rounded-lg border border-emerald-800
                    text-emerald-200 hover:bg-emerald-900/30 transition"
                >
                  Подтвердить и перенести
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Перенос остановленной задачи в работу: вопрос вместо запрета.
          Свой диалог, а не нативный confirm — он рисуется системой и выпадает
          из темы доски */}
      {menuFor && (
        <ContextMenu x={menuFor.x} y={menuFor.y} items={menuItems}
                     onClose={() => setMenuFor(null)} />
      )}
      {pendingMove && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
             onClick={() => setPendingMove(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-md shadow-2xl"
               onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-zinc-800 text-base font-semibold text-amber-200">
              Задача стоит
            </div>
            <div className="px-5 py-4 space-y-2 text-sm text-zinc-300/90">
              {/* Причина от сервера точнее нашей: доска могла устареть */}
              {pendingMove.serverReason ? (
                <div>{pendingMove.serverReason}</div>
              ) : (
              <div>
                <span className="font-mono text-zinc-400">{pendingMove.taskId}</span>
                {pendingMove.task?.blocked_by?.length > 0 && (
                  <> ждёт {pendingMove.task.blocked_by.map((id) => {
                    const col = findTask(id)?.column
                    return col ? `${id} (${col.title})` : id
                  }).join(', ')}</>
                )}
                {pendingMove.task?.paused && (
                  <>{pendingMove.task?.blocked_by?.length > 0 ? ' и' : ''} на паузе
                    : {pendingMove.task.paused}</>
                )}
              </div>
              )}
              <div className="text-zinc-500">
                Всё равно взять в работу — перенести в «{pendingMove.toTitle}»?
              </div>
            </div>
            <div className="px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
              <button onClick={() => setPendingMove(null)}
                      className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
                Отмена
              </button>
              <button
                onClick={() => { const m = pendingMove; setPendingMove(null); applyMove(m, false, true) }}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-700 text-zinc-300
                  hover:border-zinc-500 hover:bg-zinc-800 transition"
              >
                Перенести
              </button>
              <button
                onClick={() => { const m = pendingMove; setPendingMove(null); applyMove(m, true, true) }}
                className="px-4 py-2 text-sm rounded-lg border border-amber-800
                  bg-amber-950/40 text-amber-200 hover:bg-amber-900/50 transition"
                title="Снять блокировки и паузу, затем перенести"
              >
                Снять простой и перенести
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Отмена — съезд с маршрута: из неё не возвращаются, и «почему»
          остаётся в файле навсегда. Поэтому причина не опция, а условие
          переноса: без текста кнопка неактивна */}
      {pendingCancel && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
             onClick={() => setPendingCancel(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-md shadow-2xl"
               onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-zinc-800 text-base font-semibold text-zinc-300">
              Причина отмены
            </div>
            <div className="px-5 py-4 space-y-3 text-sm text-zinc-300/90">
              <div>
                <span className="font-mono text-zinc-400">{pendingCancel.taskId}</span>
                {' '}уезжает в «{pendingCancel.toTitle}». Из отмены не возвращаются —
                причина останется в файле задачи.
              </div>
              <ReasonPrompt
                label="Причина:"
                placeholder="дублирует TASK-010"
                submitLabel="Отменить задачу"
                onSubmit={(reason) => {
                  const m = pendingCancel
                  setPendingCancel(null)
                  applyMove(m, false, false, reason)
                }}
                onCancel={() => setPendingCancel(null)}
              />
            </div>
          </div>
        </div>
      )}

      {/* Открыли найденную задачу — совпадения подсвечены в её тексте */}
      {openTask && (
        <TaskModal
          taskId={openTask}
          query={query}
          onOpenTask={(id) => pushView({ task: id })}
          onOpenEpic={(key) => pushView({ epic: key })}
          onChanged={refresh}
          // Форма копии перекрыла бы окно задачи — уступаем ей место: копию
          // правят по своим полям, а не сверяют с оригиналом на просвет
          onCopy={(id) => { closeViews(); openCopy(id) }}
          backTo={viewLabel(taskTrail[taskTrail.length - 1])}
          onBack={taskTrail.length ? goBack : null}
          onClose={closeViews}
        />
      )}
      {openEpic && (
        <EpicModal
          epicKey={openEpic}
          onOpenTask={(id) => pushView({ task: id })}
          onClose={closeViews}
        />
      )}
      {showNewTask && (
        <NewTaskModal
          source={copySource}
          onClose={closeNewTask}
          onCreated={refresh}
        />
      )}
      {showLogs && <LogsPanel onClose={() => setShowLogs(false)} />}
      {showSettings && (
        <SettingsModal
          initialTab={settingsTab}
          onClose={() => { setShowSettings(false); setSettingsTab(null) }}
          onSaved={(cfg) => { setDndFullBoard(!!cfg.dnd_full_board); refresh() }}
          onOpenHelp={openHelp}
        />
      )}
      {helpSection && (
        <HelpModal section={helpSection} onClose={() => setHelpSection(null)} />
      )}
      {showUpdate && (
        <UpdateModal
          onOpenSettings={(tab) => { setShowUpdate(false); openSettings(tab) }}
          onClose={() => {
            setShowUpdate(false)
            // Окно могло проверить обновления или изменить режим — перечитываем
            api.updateStatus()
              .then((s) => setUpdateAvailable(!!s.update_available && s.mode !== 'off'))
              .catch(() => {})
          }}
        />
      )}
      {showAgentic && (
        <AgenticStaleModal
          onClose={() => setShowAgentic(false)}
          onUpdated={refresh}
        />
      )}
      {showRepair && (
        <BoardRepairModal
          onClose={() => setShowRepair(false)}
          onRepaired={refresh}
        />
      )}
      {showScaffold && (
        <ScaffoldModal
          tasksDir={health?.project?.tasks_dir}
          harnesses={report?.harnesses}
          onShowDiff={() => { setShowScaffold(false); refresh(); setShowAgentic(true) }}
          onClose={() => setShowScaffold(false)}
          onDone={() => { setShowScaffold(false); refresh() }}
        />
      )}
    </div>
  )
}
