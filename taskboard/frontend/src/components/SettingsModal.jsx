import { useEffect, useState } from 'react'
import { api } from '../api'
import PipelineEditor from './PipelineEditor'

// Вкладки окна настроек. Реестр, а не россыпь JSX: добавить группу — дописать
// строку, и она встанет и в список слева, и в переключение содержимого.
// scope — уровень влияния: 'project' пишется в <проект>/tasks/.taskboard.json,
// 'global' — в ~/.taskboard/config.json и действует во всех проектах.
// Он же решает спор «куда положить»: настройка живёт там, где её слой
const TABS = [
  { key: 'tool', title: 'Общие', scope: 'global' },
  { key: 'agentic', title: 'Агенты', scope: 'project' },
  { key: 'board', title: 'Вид доски', scope: 'global' },
  { key: 'lifecycle', title: 'Жизненный цикл', scope: 'project' },
  { key: 'release', title: 'Выпуск', scope: 'project' },
]

// Выбранная вкладка живёт в localStorage, как порядок колонок: это привычка
// пользователя, а не свойство проекта, и гонять её через конфиг незачем
const TAB_KEY = 'taskboard:settingsTab'

const SCOPE_NOTE = {
  project: 'Настройки этого проекта · tasks/.taskboard.json',
  global: 'Общие для всех проектов · ~/.taskboard/config.json',
}

// Модалка настроек. Свойства инструмента (порт, вид карточки) живут в
// глобальном ~/.taskboard/config.json, настройки проекта (жизненный цикл,
// среды, скрипт выпуска) — в <проект>/tasks/.taskboard.json
export default function SettingsModal({ onClose, onSaved, onOpenHelp, initialTab }) {
  // Ключ мог остаться от вкладки, которой больше нет, — тогда открываем первую.
  // `initialTab` важнее сохранённой: окно открыли ради конкретной настройки, и
  // высадить человека на прошлой вкладке значит заставить искать самому
  const [tab, setTab] = useState(() => {
    if (TABS.some((t) => t.key === initialTab)) return initialTab
    const saved = localStorage.getItem(TAB_KEY)
    return TABS.some((t) => t.key === saved) ? saved : TABS[0].key
  })

  const openTab = (key) => {
    setTab(key)
    localStorage.setItem(TAB_KEY, key)
  }
  const [config, setConfig] = useState(null)
  const [pipeline, setPipelineState] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [sources, setSources] = useState([])
  // Переопределения подписей приезжают вместе с готовым маршрутом; null —
  // пользователь их не менял, и трогать сохранённые не нужно
  const [statusesOverride, setStatusesOverride] = useState(null)
  // Требования этапов правятся тем же редактором; null — не трогали
  const [requiresOverride, setRequiresOverride] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // Выполненные миграции после сохранения (переименования в проекте)
  const [migrations, setMigrations] = useState(null)
  // Действие с сервером: 'restart' | 'stop' | null (после вызова эндпоинта)
  const [serverAction, setServerAction] = useState(null)

  // Ждём, пока сервер поднимется после перезапуска, затем перезагружаем страницу
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && !busy && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, busy])

  const restartServer = async () => {
    setServerAction('restart')
    try { await api.restartServer() } catch { /* сервер уже умер — это норма */ }
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 1000))
      try {
        await api.health()
        location.reload()
        return
      } catch { /* ещё не поднялся */ }
    }
    setError('Сервер не поднялся после перезапуска')
    setServerAction(null)
  }

  const stopServer = async () => {
    setServerAction('stop')
    try { await api.stopServer() } catch { /* сервер уже умер — это норма */ }
    // Проверяем, что сервер действительно умер (были случаи «мягкой» остановки)
    for (let i = 0; i < 8; i++) {
      await new Promise((r) => setTimeout(r, 700))
      try {
        await api.health()
      } catch {
        return // не отвечает — умер, остаётся заглушка
      }
    }
    setError('Сервер не остановился — завершите процесс вручную: lsof -ti:8765 | xargs kill')
    setServerAction(null)
  }

  useEffect(() => {
    api.getConfig().then(setConfig).catch((e) => setError(e.message))
    api.pipeline()
      .then((data) => {
        setPipelineState({ pipeline: data.pipeline, actions: data.actions })
        setCatalog(data.catalog || [])
      })
      .catch(() => { /* нет активного проекта — редактор просто не покажем */ })
    api.pipelineSources()
      .then((data) => setSources(data.items || []))
      .catch(() => { /* без источников редактор работает как раньше */ })
  }, [])

  const set = (key, value) => setConfig({ ...config, [key]: value })

  // Выключение статуса с задачами: куда их девать — решает пользователь
  const [removals, setRemovals] = useState(null)
  const [moves, setMoves] = useState({})
  // Включённое требование действует задним числом: показываем цену до сохранения
  const [gated, setGated] = useState(null)

  const updates = () => ({
    port: Number(config.port) || 8765,
    dnd_full_board: !!config.dnd_full_board,
    delete_tasks: !!config.delete_tasks,
    release_script: (config.release_script || '').trim(),
    // Размеры превью: пустое поле — «не меняли», иначе бэкенд получит ноль
    ...Object.fromEntries(['card_title_size', 'card_title_lines', 'card_meta_size']
      .filter((k) => config[k] !== '' && config[k] != null)
      .map((k) => [k, Number(config[k])])),
    // Переключатель, а не число: пустого значения у него не бывает
    card_show_type: config.card_show_type !== false,
    // Не выбраны — не подменяем «не спрашивали» на «обе среды не нужны»
    ...(config.harnesses ? { harnesses: config.harnesses } : {}),
    vault: !!config.vault,
    ...(pipeline ? {
      pipeline: pipeline.pipeline.map((s) => s.key),
      actions: pipeline.actions,
    } : {}),
    ...(statusesOverride !== null ? { statuses: statusesOverride } : {}),
    // Требования — ключ верхнего уровня, а не внутри `statuses`: вложенное
    // затирается применением источника пайплайна
    ...(requiresOverride !== null ? { requires: requiresOverride } : {}),
  })

  // Сначала спрашиваем бэкенд, не осиротеют ли задачи выключаемых статусов
  const check = async () => {
    setBusy(true)
    setError(null)
    try {
      const preview = await api.previewConfig(updates())
      if (preview.removals?.length) {
        setRemovals(preview.removals)
        // Ключ — заголовок раздела: у осиротевшего раздела статуса может не быть
        setMoves(Object.fromEntries(preview.removals.map((r) => [r.section, r.suggested])))
        return
      }
      // Требование действует задним числом: живые задачи, прошедшие этап раньше,
      // упрутся на следующем шаге вперёд. Это то, ради чего механизм заводился,
      // но человек должен увидеть цену до нажатия, а не узнать от агента через день
      if (preview.gated?.length) {
        setGated(preview.gated)
        return
      }
      await save()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const save = async (withMoves = undefined) => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.saveConfig(updates(), withMoves)
      setRemovals(null)
      onSaved(result.config)
      // Переименования запускают миграции в проекте — показываем что произошло
      if (result.migrations?.length) {
        setMigrations(result.migrations)
      } else {
        onClose()
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const field = 'w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-500'
  const label = 'block text-xs text-zinc-500 mb-1'

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      {/* Высота формы фиксирована, а не подстраивается под вкладку: иначе окно
          прыгает при каждом переключении. Короткие состояния (перезапуск,
          миграции, вопрос про задачи) растягивать незачем — им max-h */}
      {/* Ширина рассчитана на самую тесную форму — требования этапа: там в строку
          идут проверка, её параметр, формулировка и служебное имя */}
      <div className={`bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-4xl overflow-hidden flex flex-col shadow-2xl ${
        serverAction || removals || migrations ? 'max-h-[90vh]' : 'h-[min(90vh,640px)]'
      }`}>
        <div className="px-5 py-4 border-b border-zinc-800 text-lg font-semibold">
          {serverAction === 'restart' ? 'Перезапуск сервера' :
           serverAction === 'stop' ? 'Сервер остановлен' :
           migrations ? 'Настройки сохранены' :
           removals ? 'Куда перенести задачи?' :
           gated ? 'Кого коснётся требование' : 'Настройки'}
        </div>

        {serverAction === 'restart' ? (
          <div className="px-5 py-8 text-sm text-zinc-300 text-center">
            Сервер перезапускается, страница обновится автоматически…
          </div>
        ) : serverAction === 'stop' ? (
          <div className="px-5 py-8 space-y-2 text-center">
            <div className="text-sm text-zinc-300">Сервер остановлен. Эту вкладку можно закрыть.</div>
            <div className="text-xs text-zinc-600">Запуск: py taskboard.py</div>
          </div>
        ) : removals ? (
          <>
            <div className="px-5 py-4 space-y-3">
              <div className="text-sm text-zinc-300">
                Эти статусы выключаются, но в их разделах остались задачи.
                Выберите, куда их перенести — доска и frontmatter поедут вместе.
              </div>
              {removals.map((r) => (
                <div key={r.section} className="space-y-1">
                  <span className="block text-xs text-zinc-500">
                    {r.label} — задач: {r.count}
                  </span>
                  <select
                    className={field}
                    value={moves[r.section] || ''}
                    onChange={(e) => setMoves({ ...moves, [r.section]: e.target.value })}
                  >
                    {(pipeline?.pipeline || []).map((s) => (
                      <option key={s.key} value={s.key}>{s.label}</option>
                    ))}
                  </select>
                </div>
              ))}
              {error && <div className="text-sm text-rose-400">{error}</div>}
            </div>
            <div className="px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
              <button
                onClick={() => setRemovals(null)}
                className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200"
              >
                Отмена
              </button>
              <button
                onClick={() => save(moves)}
                disabled={busy}
                className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 rounded-lg disabled:opacity-50"
              >
                {busy ? 'Переношу…' : 'Перенести и сохранить'}
              </button>
            </div>
          </>
        ) : gated ? (
          <>
            {/* Цена включения требования: оно действует задним числом, и задачи,
                прошедшие этап раньше, упрутся на следующем шаге вперёд. Это не
                ошибка настройки, а её смысл — но узнавать об этом человек должен
                здесь, а не от агента через день */}
            <div className="px-5 py-4 space-y-3">
              <div className="text-sm text-zinc-300">
                Требование распространяется и на задачи, которые этот этап уже
                прошли. Вот кого оно коснётся:
              </div>
              <div className="space-y-1 max-h-64 overflow-y-auto">
                {gated.map((t) => (
                  <div key={t.id} className="text-xs flex gap-2">
                    <span className={`shrink-0 ${t.when === 'now' ? 'text-amber-300' : 'text-zinc-400'}`}>
                      {t.id}
                    </span>
                    <span className="text-zinc-400 truncate">{t.title}</span>
                    {/* Когда именно требование их коснётся: у прошедших этап
                        значок долга появится сразу, у стоящих на нём — при уходе.
                        Иначе предупреждение и доска показывают разные числа */}
                    <span className="ml-auto shrink-0 text-[10px] text-zinc-500 border border-zinc-700 rounded px-1">
                      {t.when === 'now' ? 'долг сразу' : 'при уходе с этапа'}
                    </span>
                    <span className="text-zinc-500 shrink-0">
                      {t.requirements.join('; ')}
                    </span>
                  </div>
                ))}
              </div>
              {/* Экран должен отвечать на «и что мне теперь делать»: без этого
                  список задач читается угрозой, и включать требование не хочется */}
              <div className="text-xs text-zinc-400 space-y-1 border-t border-zinc-800 pt-3">
                <div>
                  На вашу работу это не влияет: карточки переносятся как раньше.
                  Значок `⚠` на карточке появится у тех, кто этап уже прошёл;
                  у остальных — когда они с него уйдут.
                </div>
                <div>
                  Взявшись за такую задачу, агент либо выполнит требование, либо
                  отметит, что к этой задаче оно не относится, — и в обоих случаях
                  запишет это в задачу.
                </div>
                <div>Передумаете — снимите требование, и долг исчезнет: он не хранится, а считается.</div>
              </div>
              {error && <div className="text-sm text-rose-400">{error}</div>}
            </div>
            <div className="px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
              <button onClick={() => setGated(null)}
                      className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
                Отмена
              </button>
              <button onClick={() => { setGated(null); save() }} disabled={busy}
                      className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 rounded-lg disabled:opacity-50">
                {busy ? 'Сохраняю…' : 'Включить требование'}
              </button>
            </div>
          </>
        ) : migrations ? (
          <>
            <div className="px-5 py-4 space-y-2">
              <div className="text-sm text-zinc-300">Выполненные миграции в проекте:</div>
              {migrations.map((m, i) => (
                <div key={i} className="text-sm text-emerald-300">✓ {m}</div>
              ))}
            </div>
            <div className="px-5 py-4 border-t border-zinc-800 flex justify-end">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 rounded-lg"
              >
                OK
              </button>
            </div>
          </>
        ) : (
          <div className="flex min-h-0 flex-1">
            {/* Список вкладок слева: с ростом числа групп окно не удлиняется */}
            <nav className="w-44 shrink-0 border-r border-zinc-800 p-2 space-y-1 overflow-y-auto">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  onClick={() => openTab(t.key)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm ${
                    tab === t.key
                      ? 'bg-zinc-800 text-zinc-100'
                      : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
                  }`}
                >
                  {t.title}
                  <span className="block text-[10px] text-zinc-600">
                    {t.scope === 'project' ? 'проект' : 'глобально'}
                  </span>
                </button>
              ))}
            </nav>

            <div className="flex min-w-0 flex-1 flex-col">
        <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1">
          {!config && !error && <div className="text-sm text-zinc-500">Загрузка…</div>}

          {config && (
            <>
              {/* Уровень влияния — словами, а не только подписью во вкладке:
                  «где это сохранится» пользователь спрашивает именно тут */}
              <div className="text-[11px] text-zinc-600">
                {SCOPE_NOTE[TABS.find((t) => t.key === tab)?.scope]}
              </div>

              {tab === 'board' && (
              <>
              {/* Превью задачи. Границы приходят с бэкенда (card_limits) — он же
                  их и проверяет: числа, вписанные сюда руками, разъехались бы
                  с проверкой при первой правке диапазона */}
              <div>
                <span className={label}>Превью задачи на доске</span>
                <div className="grid grid-cols-3 gap-3">
                  {[['card_title_size', 'Заголовок, px'],
                    ['card_title_lines', 'Строк заголовка'],
                    ['card_meta_size', 'Метаданные, px']].map(([key, title]) => {
                    const [low, high] = config.card_limits?.[key] || []
                    const value = config[key]
                    const bad = value !== '' && (Number(value) < low || Number(value) > high)
                    return (
                      <div key={key}>
                        <span className={label}>{title}</span>
                        <input
                          className={`${field} ${bad ? 'border-rose-500' : ''}`}
                          type="number"
                          min={low}
                          max={high}
                          value={value ?? ''}
                          onChange={(e) => set(key, e.target.value)}
                        />
                        <span className="block text-[11px] text-zinc-600 mt-1">
                          от {low} до {high}
                        </span>
                      </div>
                    )
                  })}
                </div>
                {/* Метка типа — не размер, но живёт там же: это про то, что
                    видно на превью. По умолчанию включена */}
                <label className="flex items-start gap-2 text-sm cursor-pointer select-none mt-3">
                  <input
                    type="checkbox"
                    checked={config.card_show_type !== false}
                    onChange={(e) => set('card_show_type', e.target.checked)}
                    className="mt-0.5 accent-sky-500"
                  />
                  <span>
                    Метка типа задачи
                    <div className="text-[11px] text-zinc-500">
                      кружок с буквой в правом верхнем углу превью; в открытой
                      задаче тип виден всегда
                    </div>
                  </span>
                </label>
              </div>
              </>
              )}

              {/* Состав агентского окружения проверяется по выбранным средам:
                  выключенная среда молчит, включённая — требует полного набора */}
              {tab === 'agentic' && (
              <div>
                <span className={label}>Среды агентов</span>
                <div className="space-y-2">
                  {[['claude', 'Claude Code', '.claude/skills · CLAUDE.md'],
                    ['opencode', 'opencode', '.opencode/commands · AGENTS.md']].map(
                    ([key, title, where]) => (
                      <label key={key} className="flex items-start gap-2 text-sm cursor-pointer select-none">
                        <input
                          type="checkbox"
                          checked={!!config.harnesses?.[key]}
                          onChange={(e) => set('harnesses', {
                            ...(config.harnesses || {}), [key]: e.target.checked,
                          })}
                          className="mt-0.5 accent-sky-500"
                        />
                        <span>
                          {title}
                          <div className="text-[11px] text-zinc-500">{where}</div>
                        </span>
                      </label>
                    ))}
                </div>
                {/* Волт — часть окружения, а не среда: спрашивается тем же
                    диалогом, но передумать можно только здесь */}
                <label className="flex items-start gap-2 text-sm cursor-pointer select-none mt-2">
                  <input
                    type="checkbox"
                    checked={!!config.vault}
                    onChange={(e) => set('vault', e.target.checked)}
                    className="mt-0.5 accent-sky-500"
                  />
                  <span>
                    Knowledge Vault
                    <div className="text-[11px] text-zinc-500">
                      vault/ — внешняя память проекта: скилл write-vault, шаблоны заметок,
                      блоки про волт в скиллах и правилах
                    </div>
                  </span>
                </label>
                <div className="text-[11px] text-zinc-600 mt-1">
                  Недостающее развернётся кнопками на баннере. Скиллы лежат в одном
                  месте: opencode читает и .claude/skills
                  {onOpenHelp && (
                    <button className="ml-1 underline hover:text-zinc-400"
                            onClick={() => onOpenHelp('agentic')}>подробнее</button>
                  )}
                </div>
              </div>
              )}

              {tab === 'lifecycle' && (
              <>
              {/* Перетаскивание — правило движения задач, поэтому живёт рядом
                  с маршрутом, а не среди свойств внешнего вида */}
              <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={!!config.dnd_full_board}
                  onChange={(e) => set('dnd_full_board', e.target.checked)}
                  className="accent-sky-500"
                />
                DnD по всей доске (иначе мышью — только приём задач ↔ очередь)
              </label>

              {/* Удаление необратимо и трогает файлы пользователя, поэтому
                  выключено по умолчанию: крестика на карточках просто нет */}
              <label className="flex items-start gap-2 text-sm cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={!!config.delete_tasks}
                  onChange={(e) => set('delete_tasks', e.target.checked)}
                  className="mt-0.5 accent-sky-500"
                />
                <span>
                  Удаление задач крестиком
                  <div className="text-[11px] text-zinc-500">
                    крестик в углу превью убирает задачу с доски и удаляет её файл;
                    спрашивает подтверждение
                  </div>
                </span>
              </label>

              {pipeline && (
                <div className="border-t border-zinc-800 pt-4">
                  <div className="text-sm font-medium mb-2">Жизненный цикл задачи</div>
                  <PipelineEditor
                    pipeline={pipeline.pipeline}
                    actions={pipeline.actions}
                    catalog={catalog}
                    sources={sources}
                    requires={requiresOverride ?? config.requires ?? {}}
                    predicates={config.predicates}
                    onOpenHelp={onOpenHelp}
                    onChange={({ pipeline: next, actions: nextActions, statuses, requires }) => {
                      setPipelineState({ pipeline: next, actions: nextActions })
                      if (statuses !== undefined) setStatusesOverride(statuses)
                      if (requires !== undefined) setRequiresOverride(requires)
                    }}
                  />
                </div>
              )}
              </>
              )}

              {/* Скрипт выпуска пишет пользователь, и его может не быть вовсе —
                  поэтому отдельная вкладка, а не поле среди свойств проекта */}
              {tab === 'release' && (
              <div>
                <span className={label}>Скрипт выпуска версии</span>
                <input
                  className={field}
                  placeholder="не настроен"
                  value={config.release_script || ''}
                  onChange={(e) => set('release_script', e.target.value)}
                />
                <div className="text-[11px] text-zinc-600 mt-1">
                  Путь к вашему скрипту в проекте, например <code>tools/release.py</code>.
                  Пусто — подготовка выпуска доводится до changelog, а выпускаете вы сами.
                  {onOpenHelp && (
                    <button
                      className="ml-1 underline hover:text-zinc-400"
                      onClick={() => onOpenHelp('release')}
                    >
                      подробнее
                    </button>
                  )}
                </div>
              </div>
              )}

              {tab === 'tool' && (
              <>
              <div>
                <span className={label}>Порт (применится после перезапуска сервера)</span>
                <input
                  className={field}
                  type="number"
                  value={config.port}
                  onChange={(e) => set('port', e.target.value)}
                />
              </div>

              <div className="border-t border-zinc-800 pt-4">
                <span className={label}>Сервер</span>
                <div className="flex gap-2">
                  <button
                    onClick={restartServer}
                    className="px-3 py-2 text-sm bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded-lg"
                  >
                    Перезапустить
                  </button>
                  <button
                    onClick={stopServer}
                    className="px-3 py-2 text-sm bg-zinc-800 hover:bg-rose-900/40 border border-zinc-700 hover:border-rose-800 rounded-lg"
                  >
                    Остановить
                  </button>
                </div>
                <div className="text-[11px] text-zinc-600 mt-1">
                  Перезапуск применяет смену порта и перечитывает конфиги
                </div>
              </div>
              </>
              )}
            </>
          )}

          {error && <div className="text-sm text-rose-400">{error}</div>}
        </div>

        {/* Кнопки сохранения общие для всех вкладок: конфиг уходит целиком,
            и «сохранил на одной вкладке, потерял на другой» невозможно */}
        <div className="px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
            Отмена
          </button>
          <button
            onClick={check}
            disabled={busy || !config}
            className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 disabled:opacity-50 rounded-lg"
          >
            {busy ? 'Сохраняю…' : 'Сохранить'}
          </button>
        </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
