import { useState } from 'react'
import { api } from '../api'

// Модалка развёртывания структуры tasks/ и агентского окружения.
// Состав поставки задают выбранные среды: по раскладке на диске их не угадать —
// папки может не быть просто потому, что проект ещё не открывали в этой среде.
// Ответ запоминается в конфиге проекта, дальше по нему проверяется полнота.
export default function ScaffoldModal({ tasksDir, harnesses, onClose, onDone, onShowDiff }) {
  const [options, setOptions] = useState({
    claude: harnesses?.choice?.claude ?? harnesses?.detected?.claude ?? true,
    opencode: harnesses?.choice?.opencode ?? harnesses?.detected?.opencode ?? true,
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
      setResult(await api.scaffold({
        harnesses: { claude: options.claude, opencode: options.opencode },
        vault: options.vault,
      }))
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
          {result ? 'Окружение развёрнуто' : 'Настройка окружения проекта'}
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
              {result.diverged?.length > 0 && (
                <div className="border border-amber-800/60 bg-amber-950/30 rounded-lg px-3 py-2">
                  <div className="text-amber-200 mb-1">
                    Отличаются от шаблона — оставлены как есть:
                  </div>
                  {result.diverged.map((f, i) => (
                    <div key={i} className="text-amber-300/90 font-mono text-xs">≠ {f}</div>
                  ))}
                  <div className="text-[11px] text-zinc-400 mt-2">
                    В них могут быть ваши правки, поэтому развёртывание их не трогает.
                    Посмотрите diff и решите по каждому файлу отдельно.
                  </div>
                  {onShowDiff && (
                    <button
                      onClick={onShowDiff}
                      className="mt-2 px-3 py-1.5 text-xs rounded-lg bg-zinc-800 hover:bg-zinc-700
                        border border-zinc-700"
                    >
                      Посмотреть отличия
                    </button>
                  )}
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
                Всегда создаётся: board.md со всеми разделами, create_task.py,
                set_status.py, epics.md, .gitignore, папка логов.
              </div>
              <div className="text-xs text-zinc-500 font-mono break-all">{tasksDir}</div>

              <div className="border-t border-zinc-800 pt-3 space-y-3">
                <div className="text-sm text-zinc-300">
                  В каких средах вы работаете с этим проектом?
                  <div className="text-[11px] text-zinc-500 mt-1">
                    Отсутствие папки ни о чём не говорит — проект мог просто ни разу
                    не открываться в этой среде. Ответ запомнится: дальше taskboard
                    следит, чтобы окружение выбранных сред было полным и актуальным.
                  </div>
                </div>

                <label className={row}>
                  <input
                    type="checkbox"
                    checked={options.claude}
                    onChange={(e) => set('claude', e.target.checked)}
                    className={`mt-0.5 ${checkbox}`}
                  />
                  <span>
                    Claude Code
                    <div className={hint}>.claude/skills/ — new-task, start-task, fix-task, finalize-task, brainstorm, brainstorm-team · секция правил в CLAUDE.md</div>
                  </span>
                </label>

                <label className={row}>
                  <input
                    type="checkbox"
                    checked={options.opencode}
                    onChange={(e) => set('opencode', e.target.checked)}
                    className={`mt-0.5 ${checkbox}`}
                  />
                  <span>
                    opencode
                    <div className={hint}>.opencode/commands/ — обёртки вызова скиллов · секция правил в AGENTS.md</div>
                  </span>
                </label>

                <div className="text-[11px] text-zinc-500 pl-6">
                  Скиллы разворачиваются один раз: opencode читает и .claude/skills,
                  поэтому вторая копия нужна только проекту без Claude Code.
                </div>

                <label className={`${row} ${!options.claude && !options.opencode ? 'opacity-40 pointer-events-none' : ''}`}>
                  <input
                    type="checkbox"
                    checked={options.vault}
                    disabled={!options.claude && !options.opencode}
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
                Существующие файлы не перезаписываются: развёртывание только создаёт
                недостающее. Скиллы и скрипты с вашими правками останутся как есть —
                их отличия покажем отдельно, обновить можно будет по одному, посмотрев diff.
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
