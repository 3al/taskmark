import { useEffect, useState } from 'react'
import { api } from '../api'
import PipelineEditor from './PipelineEditor'

// Модалка настроек. Свойства инструмента (порт, тема) живут в глобальном
// ~/.taskboard/config.json, настройки проекта (жизненный цикл, имена
// артефактов) — в <проект>/tasks/.taskboard.json; раскладывает их бэкенд
export default function SettingsModal({ onClose, onSaved, onOpenHelp }) {
  const [config, setConfig] = useState(null)
  const [pipeline, setPipelineState] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [sources, setSources] = useState([])
  // Переопределения подписей приезжают вместе с готовым маршрутом; null —
  // пользователь их не менял, и трогать сохранённые не нужно
  const [statusesOverride, setStatusesOverride] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // Выполненные миграции после сохранения (переименования в проекте)
  const [migrations, setMigrations] = useState(null)
  // Действие с сервером: 'restart' | 'stop' | null (после вызова эндпоинта)
  const [serverAction, setServerAction] = useState(null)

  // Ждём, пока сервер поднимется после перезапуска, затем перезагружаем страницу
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

  const updates = () => ({
    port: Number(config.port) || 8765,
    dnd_full_board: !!config.dnd_full_board,
    board_file: config.board_file,
    create_script: config.create_script,
    status_script: config.status_script,
    logs_dir: config.logs_dir,
    // Не выбраны — не подменяем «не спрашивали» на «обе среды не нужны»
    ...(config.harnesses ? { harnesses: config.harnesses } : {}),
    vault: !!config.vault,
    ...(pipeline ? {
      pipeline: pipeline.pipeline.map((s) => s.key),
      actions: pipeline.actions,
    } : {}),
    ...(statusesOverride !== null ? { statuses: statusesOverride } : {}),
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
      <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-xl max-h-[90vh] overflow-y-auto shadow-2xl">
        <div className="px-5 py-4 border-b border-zinc-800 text-lg font-semibold">
          {serverAction === 'restart' ? 'Перезапуск сервера' :
           serverAction === 'stop' ? 'Сервер остановлен' :
           migrations ? 'Настройки сохранены' :
           removals ? 'Куда перенести задачи?' : 'Настройки'}
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
          <>
        <div className="px-5 py-4 space-y-4">
          {!config && !error && <div className="text-sm text-zinc-500">Загрузка…</div>}

          {config && (
            <>
              <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={!!config.dnd_full_board}
                  onChange={(e) => set('dnd_full_board', e.target.checked)}
                  className="accent-sky-500"
                />
                DnD по всей доске (иначе мышью — только приём задач ↔ очередь)
              </label>

              <div>
                <span className={label}>Порт (применится после перезапуска сервера)</span>
                <input
                  className={field}
                  type="number"
                  value={config.port}
                  onChange={(e) => set('port', e.target.value)}
                />
              </div>

              <div>
                <span className={label}>Файл доски</span>
                <input className={field} value={config.board_file} onChange={(e) => set('board_file', e.target.value)} />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <span className={label}>Скрипт создания задач</span>
                  <input className={field} value={config.create_script} onChange={(e) => set('create_script', e.target.value)} />
                </div>
                <div>
                  <span className={label}>Скрипт смены статуса</span>
                  <input className={field} value={config.status_script} onChange={(e) => set('status_script', e.target.value)} />
                </div>
              </div>

              <div>
                <span className={label}>Папка логов</span>
                <input className={field} value={config.logs_dir} onChange={(e) => set('logs_dir', e.target.value)} />
              </div>

              {/* Состав агентского окружения проверяется по выбранным средам:
                  выключенная среда молчит, включённая — требует полного набора */}
              <div className="border-t border-zinc-800 pt-4">
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

              {pipeline && (
                <div className="border-t border-zinc-800 pt-4">
                  <div className="text-sm font-medium mb-2">Жизненный цикл задачи</div>
                  <PipelineEditor
                    pipeline={pipeline.pipeline}
                    actions={pipeline.actions}
                    catalog={catalog}
                    sources={sources}
                    onOpenHelp={onOpenHelp}
                    onChange={({ pipeline: next, actions: nextActions, statuses }) => {
                      setPipelineState({ pipeline: next, actions: nextActions })
                      if (statuses !== undefined) setStatusesOverride(statuses)
                    }}
                  />
                </div>
              )}

              <div className="text-[11px] text-zinc-600">
                Порт и тема — глобально (~/.taskboard/config.json), жизненный цикл и имена
                артефактов — в проекте (tasks/.taskboard.json, вне git)
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

          {error && <div className="text-sm text-rose-400">{error}</div>}
        </div>

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
          </>
        )}
      </div>
    </div>
  )
}
