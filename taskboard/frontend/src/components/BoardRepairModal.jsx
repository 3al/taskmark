import { useEffect, useState } from 'react'
import { api } from '../api'

// Группа правок одного вида: заголовок, пояснение «что произойдёт» и список.
// Пояснение важнее списка — правки идут по файлам пользователя, и он должен
// понимать правило, а не угадывать его по строкам
function Group({ title, hint, tone, items, render }) {
  if (!items?.length) return null
  return (
    <div className="border border-zinc-800 rounded-xl">
      <div className="px-3 py-2 border-b border-zinc-800">
        <div className={`text-sm font-medium ${tone}`}>{title} · {items.length}</div>
        <div className="text-[11px] text-zinc-500">{hint}</div>
      </div>
      <div className="divide-y divide-zinc-800/60">
        {items.map((item) => (
          <div key={item.id} className="px-3 py-1.5 text-xs text-zinc-300/90">{render(item)}</div>
        ))}
      </div>
    </div>
  )
}

// Предпросмотр починки: сначала список правок, применение — вторым нажатием
export default function BoardRepairModal({ onClose, onRepaired }) {
  const [plan, setPlan] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)

  const load = async () => {
    try {
      setPlan(await api.repairPlan())
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => { load() }, [])

  const total = plan
    ? plan.add.length + plan.status.length + plan.lost.length + (plan.relink?.length ?? 0)
    : 0

  const apply = async () => {
    setBusy(true)
    try {
      const result = await api.repairApply()
      setDone(result)
      await load()
      onRepaired()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-zinc-900 border border-zinc-700 rounded-2xl w-full max-w-3xl shadow-2xl
        max-h-[90vh] flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-800 flex items-center justify-between">
          <div className="text-lg font-semibold">Починка доски</div>
          {total > 0 && (
            <button
              onClick={apply}
              disabled={busy}
              className="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500 disabled:opacity-50"
            >
              {busy ? 'Чиню…' : `Починить (${total})`}
            </button>
          )}
        </div>

        <div className="px-5 py-4 space-y-3 overflow-y-auto">
          <div className="text-sm text-zinc-300/90 bg-zinc-950/50 border border-zinc-800
            rounded-lg px-4 py-3">
            Статус задачи хранится в двух местах — разделе доски и поле <code>status:</code>
            {' '}в файле. Починка сводит их вместе, считая <strong>доску источником правды</strong>.
            Ничего не удаляется.
          </div>
          {error && <div className="text-sm text-rose-400">{error}</div>}
          {plan === null && <div className="text-sm text-zinc-500">Загружаю…</div>}
          {done && (
            <div className="text-sm text-emerald-300">
              Готово: на доску возвращено {done.added}, статусов выправлено {done.restatused},
              {' '}записей без файла убрано {done.lost}
              {done.relinked > 0 && <>, ссылок исправлено {done.relinked}</>}
              {done.failed?.length > 0 && (
                <div className="text-rose-300 mt-1">Не удалось: {done.failed.join('; ')}</div>
              )}
            </div>
          )}
          {plan && total === 0 && !done && (
            <div className="text-sm text-emerald-300">Доска и файлы задач сходятся</div>
          )}

          <Group
            title="Вернуть на доску"
            hint="файл задачи есть, строки на доске нет — встанет в раздел своего статуса"
            tone="text-sky-300"
            items={plan?.add}
            render={(i) => (
              <>
                <span className="text-zinc-500">{i.id}</span> · {i.title}
                <span className="text-zinc-500"> → {i.section}</span>
                {i.restore && <span className="text-zinc-600"> (из потерянных)</span>}
              </>
            )}
          />

          <Group
            title="Выправить статус в файле"
            hint="строка на доске и frontmatter разошлись — прав раздел доски"
            tone="text-amber-300"
            items={plan?.status}
            render={(i) => (
              <>
                <span className="text-zinc-500">{i.id}</span> · {i.file}
                <span className="text-zinc-500"> : </span>
                <span className="text-rose-300/80">{i.from || 'пусто'}</span>
                <span className="text-zinc-500"> → </span>
                <span className="text-emerald-300/80">{i.to}</span>
              </>
            )}
          />

          <Group
            title="Исправить ссылку"
            hint="файл переименовали, а строка осталась со старым именем — ссылку перепишем, место не тронем"
            tone="text-violet-300"
            items={plan?.relink}
            render={(i) => (
              <>
                <span className="text-zinc-500">{i.id}</span>
                <span className="text-zinc-500"> : </span>
                <span className="text-rose-300/80">{i.from}</span>
                <span className="text-zinc-500"> → </span>
                <span className="text-emerald-300/80">{i.to}</span>
              </>
            )}
          />

          <Group
            title="Убрать с доски записи без файла"
            hint="файл не найден — строка уедет в технический раздел, из колонок исчезнет"
            tone="text-rose-300"
            items={plan?.lost}
            render={(i) => (
              <>
                <span className="text-zinc-500">{i.id}</span> · {i.file}
                <span className="text-zinc-500"> (из «{i.section}»)</span>
              </>
            )}
          />
        </div>

        <div className="px-5 py-4 border-t border-zinc-800 flex justify-end">
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
            Закрыть
          </button>
        </div>
      </div>
    </div>
  )
}
