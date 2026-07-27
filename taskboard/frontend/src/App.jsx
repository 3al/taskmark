import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DndContext, DragOverlay, PointerSensor, pointerWithin, rectIntersection, useDroppable, useSensor, useSensors } from '@dnd-kit/core'
import { api, subscribeChanges } from './api'
import { isDropAllowed, defaultColumnOrder } from './statuses'
import Header from './components/Header'
import Column from './components/Column'
import TaskModal from './components/TaskModal'
import NewTaskModal from './components/NewTaskModal'
import LogsPanel from './components/LogsPanel'
import SettingsModal from './components/SettingsModal'
import ScaffoldModal from './components/ScaffoldModal'

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
  const [showNewTask, setShowNewTask] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showScaffold, setShowScaffold] = useState(false)
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

  // Живые обновления от watcher'а (агент двигает задачи — доска перечитывается)
  useEffect(() => subscribeChanges(() => refresh()), [refresh])

  const switchProject = async (name) => {
    if (!name) return
    await api.activateProject(name)
    refresh()
  }

  // Тумблер DnD: сохраняем в глобальный конфиг (переживает перезагрузку)
  const toggleDnd = async () => {
    const next = !dndFullBoard
    setDndFullBoard(next)
    try {
      await api.saveConfig({ dnd_full_board: next })
    } catch (e) {
      setError(e.message)
    }
  }

  // --- DnD ---

  // Статус очереди из конфига проекта (может быть переименован)
  const queuedStatus = board?.config?.queued_status || 'queued'

  // Колонки в порядке: сохранённый пользователем → дефолтный поток → новые разделы в хвост
  const orderedColumns = useMemo(() => {
    const cols = board?.columns || []
    const saved = columnOrder || defaultColumnOrder(queuedStatus)
    const byStatus = new Map(cols.map((c) => [c.status, c]))
    const ordered = saved.filter((s) => byStatus.has(s)).map((s) => byStatus.get(s))
    const rest = cols.filter((c) => !saved.includes(c.status))
    return [...ordered, ...rest]
  }, [board, columnOrder, queuedStatus])

  const findColumn = (status) => board?.columns.find((c) => c.status === status)

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

  const isAllowed = (from, to) => isDropAllowed(from, to, dndFullBoard, queuedStatus)

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
    try {
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
    outdated_script: { part: 'create_script', label: 'Обновить' },
    no_status_script: { part: 'status_script', label: 'Создать' },
    outdated_status_script: { part: 'status_script', label: 'Обновить' },
    outdated_skills: { part: 'skills', label: 'Обновить' },
    outdated_commands: { part: 'commands', label: 'Обновить' },
  }
  const fixDegraded = async (code) => {
    const fix = DEGRADED_FIX[code]
    if (!fix) return
    try {
      await api.scaffold({ skills: false, commands: false, rules: false, parts: [fix.part] })
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

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
        dndFullBoard={dndFullBoard}
        hasCustomOrder={!!columnOrder}
        onSwitchProject={switchProject}
        onNewTask={() => setShowNewTask(true)}
        onShowLogs={() => setShowLogs(true)}
        onToggleDnd={toggleDnd}
        onRefresh={refresh}
        onResetColumns={() => saveColumnOrder(null)}
        onOpenSettings={() => setShowSettings(true)}
      />

      {error && (
        <div className="px-4 py-2 bg-rose-950/60 border-b border-rose-800 text-sm text-rose-300">
          {error}
          <button className="ml-3 underline" onClick={() => setError(null)}>скрыть</button>
        </div>
      )}

      {report && !report.ok && !['missing', 'no_board'].includes(report.structure) && (
        <div className="px-4 py-3 bg-rose-950/60 border-b border-rose-800 text-sm text-rose-200 space-y-1">
          <div className="font-semibold">Критические проблемы структуры tasks/:</div>
          {report.critical.map((c, i) => <div key={i}>• {c}</div>)}
        </div>
      )}

      {report?.ok && (report.degraded.length > 0 || report.warnings.length > 0) && (
        <div className="px-4 py-2 bg-amber-950/50 border-b border-amber-800/60 text-xs text-amber-200/90">
          {report.degraded.map((d, i) => (
            <span key={`d${i}`} className="mr-4 inline-flex items-center gap-2">
              • {d.message}
              {DEGRADED_FIX[d.code] && (
                <button
                  className="px-2 py-0.5 rounded bg-amber-700/50 hover:bg-amber-600/60 text-amber-100 transition"
                  onClick={() => fixDegraded(d.code)}
                >
                  {DEGRADED_FIX[d.code].label}
                </button>
              )}
            </span>
          ))}
          {report.warnings.slice(0, 5).map((w, i) => (
            <span key={`w${i}`} className="mr-4">• {w}</span>
          ))}
          {report.warnings.length > 5 && <span>и ещё {report.warnings.length - 5}…</span>}
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
              board.md со всеми разделами · create_task.py · epics.md · опционально скиллы и правила
            </div>
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
              {orderedColumns.map((col) => (
                <Column
                  key={col.title}
                  column={col}
                  onOpenTask={setOpenTask}
                  activeFrom={activeDrag?.fromStatus || null}
                  dndFullBoard={dndFullBoard}
                  queuedStatus={queuedStatus}
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

      {openTask && <TaskModal taskId={openTask} onClose={() => setOpenTask(null)} />}
      {showNewTask && (
        <NewTaskModal
          backlogSections={
            (board?.columns.find((c) => c.status === 'backlog')?.groups || [])
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
        />
      )}
      {showScaffold && (
        <ScaffoldModal
          tasksDir={health?.project?.tasks_dir}
          onClose={() => setShowScaffold(false)}
          onDone={() => { setShowScaffold(false); refresh() }}
        />
      )}
    </div>
  )
}
