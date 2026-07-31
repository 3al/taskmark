import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import CopyButton from './CopyButton'

// Кастомный дропдаун проектов: нативный select не темизируется (список рендерит ОС)
function ProjectSelect({ projects, active, onSwitch }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-sm w-full
          flex items-center gap-2 hover:border-zinc-500 focus:outline-none focus:border-sky-500"
        title="Переключить проект"
      >
        <span className="truncate">{active || '— проект не выбран —'}</span>
        <span className={`ml-auto text-zinc-500 text-xs transition-transform ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 min-w-48 w-full bg-zinc-900 border border-zinc-700
          rounded-lg shadow-2xl shadow-black/60 py-1 z-40 max-h-72 overflow-y-auto">
          {!projects.length && <div className="px-3 py-1.5 text-sm text-zinc-500 italic">пусто</div>}
          {projects.map((p) => (
            <button
              key={p.name}
              onClick={() => { onSwitch(p.name); setOpen(false) }}
              className={`w-full text-left px-3 py-1.5 text-sm transition
                ${p.name === active ? 'text-sky-300 bg-zinc-800/70' : 'text-zinc-300 hover:bg-zinc-800'}`}
            >
              {p.name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Папка проекта = родитель tasks/ (если путь заканчивается на tasks)
function projectDir(tasksDir) {
  return tasksDir.replace(/[\\/]tasks[\\/]?$/i, '') || tasksDir
}

// Шапка: переключатель проектов, кнопки действий
export default function Header({
  projects, active, canCreate, hasLogs, hasCustomOrder,
  onSwitchProject, onNewTask, onShowLogs, onRefresh, onResetColumns, onOpenSettings,
  onOpenHelp, query, onQuery, matches, stalledOnly, onStalledOnly, stalledCount = 0,
}) {
  const [adding, setAdding] = useState(false)
  const [path, setPath] = useState('')
  const [error, setError] = useState(null)
  // Подтверждение удаления: кнопка ✕ рядом с проектом легко нажать случайно
  const [confirmingRemove, setConfirmingRemove] = useState(false)

  const addProject = async () => {
    if (!path.trim()) return
    setError(null)
    try {
      await api.registerProject(path.trim())
      setPath('')
      setAdding(false)
      onRefresh()
    } catch (e) {
      setError(e.message)
    }
  }

  // Забыть проект: вычищает из реестра (файлы не трогаются),
  // сервер переключается на следующий в списке
  const removeProject = async () => {
    if (!active) return
    setError(null)
    setConfirmingRemove(false)
    try {
      await api.removeProject(active)
      onRefresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const btn = 'px-3 py-1.5 text-sm rounded-lg border border-zinc-700 hover:border-zinc-500 hover:bg-zinc-800 transition'
  const activeProject = projects.find((p) => p.name === active)

  return (
    <header className="flex items-start gap-3 px-4 py-3 border-b border-zinc-800 bg-zinc-900/60">
      <div className="font-bold text-lg tracking-tight">
        task<span className="text-sky-400">board</span>
      </div>

      <div className="flex flex-col gap-1 w-48 shrink-0">
        <div className="flex items-center gap-1">
          <div className="flex-1 min-w-0">
            <ProjectSelect projects={projects} active={active} onSwitch={onSwitchProject} />
          </div>
          {active && (confirmingRemove ? (
            <span className="flex items-center gap-1 shrink-0">
              <button
                onClick={removeProject}
                className="px-2 py-1.5 text-xs rounded-lg border border-rose-800 bg-rose-950/50
                  text-rose-200 hover:bg-rose-900/60 transition"
                title={`Подтвердить: забыть проект «${active}» (файлы не удаляются)`}
              >
                Забыть?
              </button>
              <button
                onClick={() => setConfirmingRemove(false)}
                className="px-1.5 py-1.5 text-xs rounded-lg text-zinc-500 hover:text-zinc-300 transition"
                title="Отмена"
              >
                ✕
              </button>
            </span>
          ) : (
            <button
              onClick={() => setConfirmingRemove(true)}
              className="shrink-0 px-2 py-1.5 text-sm rounded-lg border border-zinc-700 text-zinc-500
                hover:text-rose-300 hover:border-rose-800 hover:bg-rose-950/40 transition"
              title={`Забыть проект «${active}» (файлы не удаляются)`}
            >
              ✕
            </button>
          ))}
        </div>
        {activeProject?.tasks_dir && (
          <div className="flex items-center gap-1 pl-3">
            <CopyButton text={projectDir(activeProject.tasks_dir)} title="Копировать путь проекта" />
            <div
              className="text-[11px] leading-tight text-zinc-500 truncate"
              title={activeProject.tasks_dir}
            >
              {projectDir(activeProject.tasks_dir)}
            </div>
          </div>
        )}
      </div>

      <button className={btn} onClick={() => setAdding(!adding)} title="Добавить проект по пути к его корню">
        + проект
      </button>

      {adding && (
        <span className="flex items-center gap-2">
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="D:\мой-проект"
            title="Путь к корню проекта (папка tasks берётся внутри него)"
            className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-sm w-72 focus:outline-none focus:border-sky-500"
            onKeyDown={(e) => e.key === 'Enter' && addProject()}
          />
          <button className={btn} onClick={addProject}>OK</button>
          {error && <span className="text-xs text-rose-400">{error}</span>}
        </span>
      )}

      {!adding && error && <span className="text-xs text-rose-400 self-center">{error}</span>}

      {/* Живой фильтр: без кнопки «искать» — доска сужается по мере ввода */}
      <div className="ml-auto flex items-center relative">
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && onQuery('')}
          placeholder="Поиск по задачам…"
          title="Ищет по номеру, заголовку и содержанию задач (Esc — сбросить)"
          className="bg-zinc-800 border border-zinc-700 rounded-lg pl-2.5 pr-16 py-1.5 text-sm w-56
            focus:outline-none focus:border-sky-500 placeholder:text-zinc-600"
        />
        {query && (
          <span className="absolute right-2 flex items-center gap-1.5">
            <span className={`text-[11px] ${matches ? 'text-zinc-500' : 'text-rose-400'}`}>
              {matches ?? 0}
            </span>
            <button
              onClick={() => onQuery('')}
              className="text-zinc-500 hover:text-zinc-200 text-sm leading-none"
              title="Сбросить поиск (Esc)"
            >
              ✕
            </button>
          </span>
        )}
      </div>

      {/* Фильтр простоя: остановленные задачи разом, каждая в своей колонке.
          Складывается с поиском — оба фильтра сужают доску */}
      <button
        onClick={() => onStalledOnly(!stalledOnly)}
        title="Показать только задачи, которые стоят: ждут другую задачу или на паузе"
        className={`px-3 py-1.5 text-sm rounded-lg border transition
          ${stalledOnly
            ? 'border-amber-600 bg-amber-950/50 text-amber-200'
            : 'border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:bg-zinc-800'}`}
      >
        ⛔ стоят
        {stalledCount > 0 && <span className="ml-1.5 text-xs text-zinc-500">{stalledCount}</span>}
      </button>

      <div className="flex items-center gap-2">
        {hasCustomOrder && (
          <button className={btn} onClick={onResetColumns} title="Сбросить порядок колонок на порядок по умолчанию">
            ↺ колонки
          </button>
        )}
        {hasLogs && <button className={btn} onClick={onShowLogs}>Логи</button>}
        <button className={btn} onClick={() => onOpenHelp()} title="Помощь: как работать с доской">?</button>
        <button className={btn} onClick={onOpenSettings} title="Настройки">⚙</button>
        {canCreate && (
          <button
            className="px-3 py-1.5 text-sm rounded-lg bg-sky-600 hover:bg-sky-500 font-medium transition"
            onClick={onNewTask}
          >
            + Задача
          </button>
        )}
      </div>
    </header>
  )
}
