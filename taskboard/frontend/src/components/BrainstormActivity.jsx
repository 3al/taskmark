import { useBrainstorm } from '../useBrainstorm'

const ROLES = [
  { key: 'architect', name: 'Архитектор', color: 'text-sky-300', dot: 'bg-sky-400' },
  { key: 'pragmatist', name: 'Прагматик', color: 'text-amber-300', dot: 'bg-amber-400' },
  { key: 'judge', color: 'text-rose-300', dot: 'bg-rose-400' },
]
const LEADER = { key: 'leader', name: 'Ведущий', color: 'text-violet-300', dot: 'bg-violet-400' }

export function activeRoles(phase) {
  if (phase === 'synthesis') return ['leader']
  if (phase === 'research' || phase === 'responses') return ['architect', 'pragmatist']
  if (phase === 'critique' || phase === 'final') return ['judge']
  return []
}

export default function BrainstormActivity({ taskId, project, result, onOpenLog }) {
  const { items } = useBrainstorm(taskId, project)

  if (!items.length) return result ? (
    <div className="px-5 py-3 border-b border-violet-500/20 bg-violet-500/5">
      <button type="button" onClick={() => onOpenLog?.(result)}
              title="Открыть последний протокол брейншторма"
              className="text-xs text-violet-300 hover:text-violet-200 transition">
        ≋ Результат
      </button>
    </div>
  ) : null
  return (
    <div className="space-y-2 px-5 py-3 border-b border-violet-500/20 bg-violet-500/5"
         role="status" aria-live="polite">
      {items.map((state) => (
        <div key={state.run}>
          <div className="flex items-center justify-between gap-3 text-xs mb-2">
            <span className="font-medium text-violet-300">{state.mode_label}</span>
            <span className="text-zinc-400">{state.phase_label}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            {(state.phase === 'synthesis' ? [...ROLES, LEADER] : ROLES).map((role, index) => {
              const active = activeRoles(state.phase).includes(role.key)
              const name = role.name || (state.mode === 'team' ? 'Оппонент' : 'Критик')
              return (
                <div key={role.key} className={`flex items-center gap-2 text-xs ${role.color}`}>
                  <span className={`brainstorm-orbit ${active ? 'brainstorm-orbit-active' : ''}`}
                        style={{ '--role-delay': `${index * 0.25}s` }} aria-hidden="true">
                    <span className={`${role.dot} brainstorm-dot`} />
                  </span>
                  <span className={active ? 'font-medium' : 'opacity-60'}>{name}</span>
                  {active && <span className="sr-only">работает</span>}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
