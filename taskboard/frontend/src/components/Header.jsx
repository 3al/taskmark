import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import AddProjectModal from './AddProjectModal'
import CopyButton from './CopyButton'
import { useListKeys } from '../listKeys'
import { SIZE_KEYS, TASK_SIZES } from '../taskSizes'

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

  const pick = (project) => { onSwitch(project.name); setOpen(false) }

  // Стрелки, Enter и Esc — общие для всех списков интерфейса. Нажатие приходит
  // от кнопки-переключателя: фокус после клика остаётся на ней
  const list = useListKeys({ items: projects, open, onPick: pick, onClose: () => setOpen(false) })

  return (
    <div ref={ref} className="relative" onKeyDown={list.onKeyDown}>
      <button
        onClick={() => setOpen(!open)}
        className="bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-sm w-full
          flex items-center gap-2 hover:border-zinc-500 focus:outline-none focus:border-sky-500"
        title="Переключить проект"
      >
        <span className="truncate">{active || '— проект не выбран —'}</span>
        <span className={`ml-auto text-zinc-400 text-xs transition-transform ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 min-w-48 w-full bg-zinc-900 border border-zinc-700
          rounded-lg shadow-2xl shadow-black/60 py-1 z-40 max-h-72 overflow-y-auto">
          {!projects.length && <div className="px-3 py-1.5 text-sm text-zinc-400 italic">пусто</div>}
          {projects.map((p, i) => (
            <button
              key={p.name}
              onClick={() => pick(p)}
              onMouseEnter={() => list.setActive(i)}
              // Подсветка одна на мышь и клавиатуру; активный проект отличается
              // цветом текста, а не фоном, — иначе две подсветки спорят
              className={`w-full text-left px-3 py-1.5 text-sm transition
                ${p.name === active ? 'text-sky-300' : 'text-zinc-300'}
                ${i === list.active ? 'bg-zinc-800' : ''}`}
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
  onOpenHelp, onOpenUpdate, updateAvailable = false,
  query, onQuery, matches, stalledOnly, onStalledOnly, stalledCount = 0,
  sizes = [], onSizes,
}) {
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState(null)
  // Подтверждение удаления: кнопка ✕ рядом с проектом легко нажать случайно
  const [confirmingRemove, setConfirmingRemove] = useState(false)

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

  // whitespace-nowrap: подпись кнопки не должна ломаться на две строки — так
  // кнопка становится выше соседних, и шапка едет целиком
  const btn = 'shrink-0 whitespace-nowrap px-3 py-1.5 text-sm rounded-lg border border-zinc-700 hover:border-zinc-500 hover:bg-zinc-800 transition'
  // Поле шапки (поиск). Ширина задана **базой + ростом**, а не фиксированным
  // `w-`, и это не косметика:
  //
  // перенос строки браузер решает по базовым ширинам, **до** раздачи свободного
  // места. Поэтому ни отступы, ни `flex-shrink` перенос не отменяют — сжатие
  // случается уже внутри готовой строки. Отменяет его только меньшая база:
  // строка «считает» поле по 160px, а на экране оно дорастает до 208 за счёт
  // свободного места, которого иначе просто не хватало на кнопки.
  //
  // Константа осталась отдельной: следующее поле шапки обязано получить ту же
  // базу — разные ширины соседних полей читаются как разная важность
  const headerField = 'basis-40 grow max-w-52 min-w-0'
  const activeProject = projects.find((p) => p.name === active)

  return (
    // Шапка переносится, а не сжимается: сжатие вместо переноса деформирует
    // кнопки — текст в них ломается, «+ Задача» уходит за край. Поэтому у
    // каждой группы стоит shrink-0: перенос целой группой читается, сплющенная
    // кнопка — нет.
    //
    // Отсюда же правило для нового контрола: **всё, что можно показать в окне,
    // в строку шапки не ставят.** Форма пути проекта стоила ~430px и ломала
    // строку одним своим появлением — теперь она в окне, а в шапке кнопка
    <header className="flex flex-wrap items-start gap-2 px-4 py-3 border-b border-zinc-800 bg-zinc-900/60">
      {/* Знак: [x] — синтаксис отмеченной задачи в markdown, тем же моноширинным,
          каким она написана в файле. Дальше название обычным шрифтом интерфейса */}
      <div className="shrink-0 font-bold text-lg tracking-tight flex items-baseline gap-1.5">
        {/* Сдвиг вверх: скобки моноширинного шрифта уходят ниже базовой линии
            заметно глубже букв, и по базовой линии знак кажется съехавшим */}
        <span className="font-mono text-zinc-400 text-base leading-none relative -top-[2px]">[<span className="text-sky-400">x</span>]</span>
        <span>task<span className="text-sky-400">mark</span></span>
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
                className="px-1.5 py-1.5 text-xs rounded-lg text-zinc-400 hover:text-zinc-300 transition"
                title="Отмена"
              >
                ✕
              </button>
            </span>
          ) : (
            <button
              onClick={() => setConfirmingRemove(true)}
              className="shrink-0 px-2 py-1.5 text-sm rounded-lg border border-zinc-700 text-zinc-400
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
              className="text-[11px] leading-tight text-zinc-400 truncate"
              title={activeProject.tasks_dir}
            >
              {projectDir(activeProject.tasks_dir)}
            </div>
          </div>
        )}
      </div>

      {/* Форма добавления живёт в окне, а не в строке шапки: поле пути с
          кнопками — это ~430px, из-за которых строка переносилась (правило
          выше). В шапке остаётся одна кнопка, в окне полю есть где стоять */}
      <button className={btn} onClick={() => setAdding(true)} title="Добавить проект по пути к его корню">
        + проект
      </button>

      {adding && (
        <AddProjectModal
          startPath={projectDir(activeProject?.tasks_dir || '')}
          onAdded={onRefresh}
          onClose={() => setAdding(false)}
        />
      )}

      {error && <span className="text-xs text-rose-400 self-center">{error}</span>}

      {/* Живой фильтр: без кнопки «искать» — доска сужается по мере ввода */}
      {/* Поиск прижат вправо: свободное место строки собирается перед ним */}
      <div className={`ml-auto flex items-center relative ${headerField}`}>
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Escape' && onQuery('')}
          placeholder="Поиск по задачам…"
          title="Ищет по номеру, заголовку и содержанию задач (Esc — сбросить)"
          className="w-full bg-zinc-800 border border-zinc-700 rounded-lg pl-2.5 pr-16 py-1.5 text-sm
            focus:outline-none focus:border-sky-500 placeholder:text-zinc-500"
        />
        {query && (
          <span className="absolute right-2 flex items-center gap-1.5">
            <span className={`text-[11px] ${matches ? 'text-zinc-400' : 'text-rose-400'}`}>
              {matches ?? 0}
            </span>
            <button
              onClick={() => onQuery('')}
              className="text-zinc-400 hover:text-zinc-200 text-sm leading-none"
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
        className={`shrink-0 whitespace-nowrap px-3 py-1.5 text-sm rounded-lg border transition
          ${stalledOnly
            ? 'border-amber-600 bg-amber-950/50 text-amber-200'
            : 'border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:bg-zinc-800'}`}
      >
        ⛔ стоят
        {stalledCount > 0 && <span className="ml-1.5 text-xs text-zinc-400">{stalledCount}</span>}
      </button>

      {/* Отбор по размеру: чипы, а не строка поиска. Размер живёт во
          frontmatter, а поиск по тексту его намеренно не видит — иначе запрос
          вроде `todo` выдавал бы всю доску. Несколько чипов складываются между
          собой по «или»: «покажи S и M» — это вопрос «что успею сегодня» */}
      <div className="flex items-center gap-1 shrink-0">
        {SIZE_KEYS.map((key) => {
          const on = sizes.includes(key)
          return (
            <button
              key={key}
              onClick={() => onSizes(on ? sizes.filter((s) => s !== key)
                                        : [...sizes, key])}
              title={`Только задачи размера ${key}: ${TASK_SIZES[key].hint}`}
              className={`px-2 py-1.5 text-xs rounded-lg border transition tabular-nums
                ${on
                  ? 'border-sky-600 bg-sky-950/50 text-sky-200'
                  : 'border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:bg-zinc-800'}`}
            >
              {key}
            </button>
          )
        })}
      </div>

      <div className="flex items-center gap-2 shrink-0">
        {hasCustomOrder && (
          <button className={btn} onClick={onResetColumns} title="Сбросить порядок колонок на порядок по умолчанию">
            ↺ колонки
          </button>
        )}
        {hasLogs && <button className={btn} onClick={onShowLogs}>Логи</button>}
        <button className={btn} onClick={() => onOpenHelp()} title="Помощь: как работать с доской">?</button>
        {/* Точка показывается только когда есть что ставить: постоянного
            значка «обновись» в рабочем инструменте быть не должно */}
        <button
          className={`${btn} relative`}
          onClick={onOpenUpdate}
          title={updateAvailable ? 'Доступна новая версия' : 'Обновление и версия'}
        >
          ↑
          {updateAvailable && (
            <span className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-sky-400" />
          )}
        </button>
        <button className={btn} onClick={onOpenSettings} title="Настройки">⚙</button>
        {canCreate && (
          <button
            className="shrink-0 whitespace-nowrap px-3 py-1.5 text-sm rounded-lg bg-sky-600
              hover:bg-sky-500 font-medium transition"
            onClick={onNewTask}
          >
            + Задача
          </button>
        )}
      </div>
    </header>
  )
}
