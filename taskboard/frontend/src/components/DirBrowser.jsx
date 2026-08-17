import { useEffect, useState } from 'react'
import { api } from '../api'

// Обзор папок: список подпапок, «наверх» и диски Windows. Своё дерево, а не
// системный диалог: абсолютного пути браузер не отдаёт ни при выборе папки, ни
// при перетаскивании, поэтому файловую систему читает сервер, а здесь — только
// показ присланного.
//
// Компонент без своего окна: он вставляется в чужое (добавление проекта), и
// оверлей с кнопками принадлежит хозяину.
export default function DirBrowser({ path, onPath, onError }) {
  const [view, setView] = useState(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await api.browseDirs(path)
        if (cancelled) return
        setView(next)
        // Отказ приходит телом со своим `path`: показываем причину, но список
        // остаётся живым — «наверх» и диски работают и из недоступной папки
        onError(next.ok ? null : next.error)
      } catch (e) {
        if (!cancelled) onError(e.message)
      }
    }
    load()
    return () => { cancelled = true }
  }, [path])

  const row = 'w-full text-left px-3 py-1.5 text-sm hover:bg-zinc-800 flex items-center gap-2'

  return (
    <div className="border border-zinc-800 rounded-xl overflow-hidden flex flex-col min-h-0">
      {/* Диски — только Windows: там «наверх» упирается в корень диска, и
          соседний диск иначе недостижим. На остальных системах список пуст */}
      {view?.drives?.length > 0 && (
        <div className="px-3 py-2 border-b border-zinc-800 flex flex-wrap gap-1 shrink-0">
          {view.drives.map((drive) => (
            <button
              key={drive}
              onClick={() => onPath(drive)}
              className="px-2 py-1 text-xs font-mono rounded-lg border border-zinc-700
                text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
            >
              {drive}
            </button>
          ))}
        </div>
      )}

      <div className="overflow-y-auto divide-y divide-zinc-800/60">
        {view?.parent && (
          <button className={`${row} text-zinc-400`} onClick={() => onPath(view.parent)}>
            <span className="shrink-0">↑</span> наверх
          </button>
        )}
        {view?.entries?.map((entry) => (
          <button key={entry.path} className={row} onClick={() => onPath(entry.path)}>
            <span className="shrink-0 text-zinc-600">📁</span>
            <span className="truncate text-zinc-300">{entry.name}</span>
            {/* Папка с задачами внутри — то, что человек и ищет: без метки он
                открывает наугад каждую вторую */}
            {entry.project && (
              <span className="shrink-0 text-[11px] text-emerald-400/80">проект</span>
            )}
          </button>
        ))}
        {view?.ok && view.entries.length === 0 && (
          <div className="px-3 py-2 text-sm text-zinc-500">Вложенных папок нет</div>
        )}
        {view === null && <div className="px-3 py-2 text-sm text-zinc-500">Загружаю…</div>}
      </div>
    </div>
  )
}
