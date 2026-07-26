import { useState } from 'react'
import { api } from '../api'

// Модалка развёртывания структуры tasks/ и агентского окружения
export default function ScaffoldModal({ tasksDir, onClose, onDone }) {
  const [options, setOptions] = useState({
    skills: true,
    commands: true,
    rules_agents: true,
    rules_claude: true,
    vault: false,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const set = (key, value) => setOptions({ ...options, [key]: value })

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      setResult(await api.scaffold(options))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const checkbox = 'accent-sky-500'
  const row = 'flex items-start gap-2 text-sm cursor-pointer select-none'
  const hint = 'text-[11px] text-zinc-500 pl-6'

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-md shadow-2xl max-h-[85vh] flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-800 text-lg font-semibold">
          {result ? 'Структура развёрнута' : 'Развернуть структуру'}
        </div>

        {result ? (
          <>
            <div className="px-5 py-4 space-y-3 overflow-y-auto text-sm">
              {result.created?.length > 0 && (
                <div>
                  <div className="text-zinc-400 mb-1">Создано:</div>
                  {result.created.map((f, i) => (
                    <div key={i} className="text-emerald-300">✓ {f}</div>
                  ))}
                </div>
              )}
              {result.replaced?.length > 0 && (
                <div>
                  <div className="text-zinc-400 mb-1">Обновлено до шаблонной версии:</div>
                  {result.replaced.map((f, i) => (
                    <div key={i} className="text-amber-300">↻ {f}</div>
                  ))}
                </div>
              )}
              {result.rules?.appended?.length > 0 && (
                <div className="text-emerald-300">
                  ✓ Секция правил дописана: {result.rules.appended.join(', ')}
                </div>
              )}
              {result.rules?.already_present?.length > 0 && (
                <div className="text-zinc-400">
                  · Секция правил уже была: {result.rules.already_present.join(', ')}
                </div>
              )}
              {result.skipped?.length > 0 && (
                <div>
                  <div className="text-zinc-400 mb-1">Пропущено (уже существует):</div>
                  {result.skipped.map((f, i) => (
                    <div key={i} className="text-zinc-500">· {f}</div>
                  ))}
                </div>
              )}
            </div>
            <div className="px-5 py-4 border-t border-zinc-800 flex justify-end">
              <button
                onClick={onDone}
                className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 rounded-lg"
              >
                Готово
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="px-5 py-4 space-y-3 overflow-y-auto">
              <div className="text-sm text-zinc-300">
                Всегда создаётся: board.md со всеми разделами, create_task.py, epics.md,
                .gitignore, папка логов.
              </div>
              <div className="text-xs text-zinc-500 font-mono break-all">{tasksDir}</div>

              <div className="border-t border-zinc-800 pt-3 space-y-3">
                <label className={row}>
                  <input
                    type="checkbox"
                    checked={options.skills}
                    onChange={(e) => set('skills', e.target.checked)}
                    className={`mt-0.5 ${checkbox}`}
                  />
                  <span>
                    Скиллы Claude Code
                    <div className={hint}>.claude/skills/ — new-task, start-task, fix-task, finalize-task, brainstorm, brainstorm-team</div>
                  </span>
                </label>

                <label className={row}>
                  <input
                    type="checkbox"
                    checked={options.commands}
                    onChange={(e) => set('commands', e.target.checked)}
                    className={`mt-0.5 ${checkbox}`}
                  />
                  <span>
                    Команды opencode
                    <div className={hint}>.opencode/commands/ — обёртки вызова скиллов</div>
                  </span>
                </label>

                <label className={row}>
                  <input
                    type="checkbox"
                    checked={options.rules_agents}
                    onChange={(e) => set('rules_agents', e.target.checked)}
                    className={`mt-0.5 ${checkbox}`}
                  />
                  <span>
                    Секция правил в AGENTS.md
                    <div className={hint}>TASK MANAGEMENT (создаст файл при отсутствии, не дублирует)</div>
                  </span>
                </label>

                <label className={row}>
                  <input
                    type="checkbox"
                    checked={options.rules_claude}
                    onChange={(e) => set('rules_claude', e.target.checked)}
                    className={`mt-0.5 ${checkbox}`}
                  />
                  <span>
                    Секция правил в CLAUDE.md
                    <div className={hint}>TASK MANAGEMENT (создаст файл при отсутствии, не дублирует)</div>
                  </span>
                </label>

                <label className={`${row} ${!options.skills ? 'opacity-40 pointer-events-none' : ''}`}>
                  <input
                    type="checkbox"
                    checked={options.vault}
                    disabled={!options.skills}
                    onChange={(e) => set('vault', e.target.checked)}
                    className={`mt-0.5 ${checkbox}`}
                  />
                  <span>
                    Поддержка волта знаний
                    <div className={hint}>оставить в скиллах блоки про Knowledge Vault (obsidian-волт в проекте)</div>
                  </span>
                </label>
              </div>

              <div className="text-[11px] text-zinc-600">
                Существующие файлы не перезаписываются. Исключение — create_task.py:
                устаревшая версия обновляется до актуальной шаблонной.
              </div>

              {error && <div className="text-sm text-rose-400">{error}</div>}
            </div>

            <div className="px-5 py-4 border-t border-zinc-800 flex justify-end gap-2">
              <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
                Отмена
              </button>
              <button
                onClick={run}
                disabled={busy}
                className="px-4 py-2 text-sm font-medium bg-sky-600 hover:bg-sky-500 disabled:opacity-50 rounded-lg"
              >
                {busy ? 'Разворачиваю…' : 'Развернуть'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
