import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DndContext, DragOverlay, PointerSensor, pointerWithin, rectIntersection, useDroppable, useSensor, useSensors } from '@dnd-kit/core'
import { api, subscribeChanges } from './api'
import { isDropAllowed, defaultColumnOrder, setPipeline } from './statuses'
import Header from './components/Header'
import Column from './components/Column'
import TaskModal from './components/TaskModal'
import NewTaskModal from './components/NewTaskModal'
import LogsPanel from './components/LogsPanel'
import SettingsModal from './components/SettingsModal'
import ScaffoldModal from './components/ScaffoldModal'
import AgenticStaleModal from './components/AgenticStaleModal'
import BoardRepairModal from './components/BoardRepairModal'
import HelpModal from './components/HelpModal'

// Колонки таскаем по указателю (pointerWithin), карточки — по пересечению прямоугольников
function collisionDetection(args) {
  if (args.active?.data?.current?.type === 'columnHeader') return pointerWithin(args)
  return rectIntersection(args)
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
  // Путь по задачам: из карточки уходят по номеру блокера, и вернуться нужно
  // туда, откуда пришли, а не искать исходную задачу на доске заново
  const [taskTrail, setTaskTrail] = useState([])
  const [showNewTask, setShowNewTask] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showScaffold, setShowScaffold] = useState(false)
  const [showAgentic, setShowAgentic] = useState(false)
  const [showRepair, setShowRepair] = useState(false)
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
  const [dndFullBoard, setDndFullBoard] = useState(false)
  const configLoaded = useRef(false)
  const [activeDrag, setActiveDrag] = useState(null)
  // Порядок колонок живёт только на фронте (localStorage), с файлом доски не синкается.
  // Ключ per-project: у каждого проекта свой порядок колонок
  const orderKey = (projectName) => `taskboard:columnOrder:${projectName || '_'}`
  const [columnOrder, setColumnOrder] = useState(null)
  // Цель вставки колонки: {status, side: 'before'|'after'|'end'}
  const [colDropTarget, setColDropTarget] = useState(null)
  const [error, setError] = useState(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))

  // Помощь открывается там, где возник вопрос: сразу на нужном разделе
  const openHelp = (section = null) => setHelpSection(section || 'start')

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

  // Живые обновления от watcher'а (агент двигает задачи — доска перечитывается)
  useEffect(() => subscribeChanges(() => refresh()), [refresh])

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

    const sectionTitle = findColumn(to)?.title || to
    const move = { taskId, sectionTitle, position, afterTaskId, group }

    // Заблокированную задачу берут в работу случайно — доска ведь не помнит,
    // чего она ждёт. Запрещать не за что (доска остаётся правдой пользователя),
    // поэтому переспрашиваем и называем причину
    const card = findTask(taskId)?.task
    if (card?.stalled && from !== to && isStartOfWork(to)) {
      setPendingMove({ ...move, task: card, toTitle: sectionTitle })
      return
    }
    applyMove(move)
  }

  // clearStall — задачу берут в работу, значит простой обычно уже неактуален:
  // оставленная пометка врала бы про то, чего задача не ждёт
  const applyMove = async ({ taskId, sectionTitle, position, afterTaskId, group },
                           clearStall = false) => {
    try {
      if (clearStall) await api.updateTask(taskId, { blocked_by: [], paused: '' })
      await api.moveTask(taskId, sectionTitle, position, afterTaskId, group)
      refresh()
    } catch (e) {
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
    outdated_template: { part: 'template', label: 'Обновить' },
    outdated_script: { part: 'create_script', label: 'Обновить' },
    no_status_script: { part: 'status_script', label: 'Создать' },
    outdated_status_script: { part: 'status_script', label: 'Обновить' },
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
        onOpenSettings={() => setShowSettings(true)}
        onOpenHelp={openHelp}
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
          {/* Рассинхрон доски и файлов чинится разом — но только после
              предпросмотра: правки идут по файлам задач */}
          {report.repairable > 0 && (
            <span className="inline-flex items-center gap-2">
              <button
                className="px-2 py-0.5 rounded bg-amber-700/50 hover:bg-amber-600/60 text-amber-100 transition"
                onClick={() => setShowRepair(true)}
              >
                Починить доску
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
              {visibleColumns.map((col) => (
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
                  columnIndicator={
                    activeDrag?.column && colDropTarget?.status === col.status
                      ? (colDropTarget.side === 'after' ? 'right' : 'left')
                      : null
                  }
                />
              ))}
              <ColumnEndZone active={!!activeDrag?.column} />
            </div>
            <DragOverlay dropAnimation={null}>
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
                    shadow-2xl shadow-black/70 scale-105 -rotate-1 text-sm font-semibold text-zinc-300">
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

      {/* Перенос остановленной задачи в работу: вопрос вместо запрета.
          Свой диалог, а не нативный confirm — он рисуется системой и выпадает
          из темы доски */}
      {pendingMove && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
             onClick={() => setPendingMove(null)}>
          <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-md shadow-2xl"
               onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-zinc-800 text-base font-semibold text-amber-200">
              Задача стоит
            </div>
            <div className="px-5 py-4 space-y-2 text-sm text-zinc-300/90">
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
                onClick={() => { const m = pendingMove; setPendingMove(null); applyMove(m) }}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-700 text-zinc-300
                  hover:border-zinc-500 hover:bg-zinc-800 transition"
              >
                Перенести
              </button>
              <button
                onClick={() => { const m = pendingMove; setPendingMove(null); applyMove(m, true) }}
                className="px-4 py-2 text-sm rounded-lg bg-amber-600 hover:bg-amber-500 font-medium"
                title="Снять блокировки и паузу, затем перенести"
              >
                Снять простой и перенести
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Открыли найденную задачу — совпадения подсвечены в её тексте */}
      {openTask && (
        <TaskModal
          taskId={openTask}
          query={query}
          onOpenTask={(id) => { setTaskTrail([...taskTrail, openTask]); setOpenTask(id) }}
          onChanged={refresh}
          backTo={taskTrail[taskTrail.length - 1]}
          onBack={taskTrail.length ? () => {
            setOpenTask(taskTrail[taskTrail.length - 1])
            setTaskTrail(taskTrail.slice(0, -1))
          } : null}
          onClose={() => { setOpenTask(null); setTaskTrail([]) }}
        />
      )}
      {showNewTask && (
        <NewTaskModal
          backlogSections={
            (board?.columns.find((c) => c.status === createStatus)?.groups || [])
              .filter((g) => g.title)
              .map((g) => g.title)
          }
          onClose={() => setShowNewTask(false)}
          onCreated={refresh}
        />
      )}
      {showLogs && <LogsPanel onClose={() => setShowLogs(false)} />}
      {showSettings && (
        <SettingsModal
          onClose={() => setShowSettings(false)}
          onSaved={(cfg) => { setDndFullBoard(!!cfg.dnd_full_board); refresh() }}
          onOpenHelp={openHelp}
        />
      )}
      {helpSection && (
        <HelpModal section={helpSection} onClose={() => setHelpSection(null)} />
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
