import { useEffect, useState } from 'react'
import { api } from '../api'

// Модалка настроек: редактирование глобального конфига ~/.taskboard/config.json
export default function SettingsModal({ onClose, onSaved }) {
  const [config, setConfig] = useState(null)
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
  }, [])

  const set = (key, value) => setConfig({ ...config, [key]: value })

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.saveConfig({
        port: Number(config.port) || 8765,
        dnd_full_board: !!config.dnd_full_board,
        board_file: config.board_file,
        create_script: config.create_script,
        status_script: config.status_script,
        logs_dir: config.logs_dir,
        queue_section: config.queue_section,
        queued_status: config.queued_status,
      })
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
      <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-md shadow-2xl">
        <div className="px-5 py-4 border-b border-zinc-800 text-lg font-semibold">
          {serverAction === 'restart' ? 'Перезапуск сервера' :
           serverAction === 'stop' ? 'Сервер остановлен' :
           migrations ? 'Настройки сохранены' : 'Настройки'}
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
                DnD по всей доске (не только Backlog↔Queue)
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

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <span className={label}>Название раздела очереди</span>
                  <input className={field} value={config.queue_section} onChange={(e) => set('queue_section', e.target.value)} />
                </div>
                <div>
                  <span className={label}>Статус очереди (frontmatter)</span>
                  <input className={field} value={config.queued_status} onChange={(e) => set('queued_status', e.target.value)} />
                </div>
              </div>

              <div className="text-[11px] text-zinc-600">
                Глобальный конфиг: ~/.taskboard/config.json · переопределения проекта:
                &lt;проект&gt;/taskboard/config.json
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
            onClick={save}
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
