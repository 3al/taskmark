import { useEffect, useState } from 'react'
import { api } from '../api'
import DirBrowser from './DirBrowser'

// Добавление проекта целиком в окне, а не строкой шапки. Причина не в красоте:
// шапка не сжимается, а переносится целыми группами (см. правило в Header.jsx),
// и поле пути с кнопками — это ~430px, из-за которых строка ломалась. В окне им
// есть где стоять, а в шапке остаётся одна кнопка.
//
// Путь можно и ввести, и выбрать: скопированный из проводника вставляется
// быстрее любого обхода дерева, поэтому поле не заменено обзором, а стоит над
// ним и показывает выбранное.
export default function AddProjectModal({ startPath = '', onAdded, onClose }) {
  const [path, setPath] = useState(startPath)
  // Папка, открытая в обзоре: меняется кликом по дереву и кнопкой «сюда»,
  // но не каждой буквой в поле — иначе дерево прыгало бы на полпути к пути
  const [dir, setDir] = useState(startPath)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && !busy && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, busy])

  const add = async () => {
    if (!path.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await api.registerProject(path.trim())
      onAdded()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const button = 'shrink-0 whitespace-nowrap px-3 py-1.5 text-xs rounded-lg border ' +
    'border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:bg-zinc-800 ' +
    'disabled:opacity-40 transition'

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-2xl shadow-2xl
        max-h-[85vh] flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-800">
          <div className="text-lg font-semibold">Добавить проект</div>
          <div className="text-[11px] text-zinc-500">
            Путь к <strong>корню</strong> проекта — папка <code>tasks/</code> берётся
            внутри него, создавать её заранее не нужно
          </div>
        </div>

        <div className="px-5 py-4 flex flex-col gap-3 min-h-0">
          <div className="flex items-center gap-2">
            <input
              autoFocus
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="D:\мой-проект"
              className="flex-1 min-w-0 bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5
                text-sm focus:outline-none focus:border-zinc-500"
              onKeyDown={(e) => e.key === 'Enter' && add()}
            />
            {/* Введённый путь можно открыть в обзоре: набрал начало — дальше мышью */}
            <button className={button} onClick={() => setDir(path.trim())}
                    title="Открыть этот путь в списке ниже">
              Открыть
            </button>
            <button className={button} onClick={add} disabled={busy || !path.trim()}>
              {busy ? 'Добавляю…' : 'Добавить'}
            </button>
          </div>

          <DirBrowser path={dir} onPath={(next) => { setDir(next); setPath(next) }}
                      onError={setError} />
        </div>

        <div className="px-5 py-3 border-t border-zinc-800 flex items-center gap-3">
          {error && <span className="text-xs text-rose-400 truncate">{error}</span>}
          <button className={`${button} ml-auto`} onClick={onClose} disabled={busy}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  )
}
