import { useEffect, useState } from 'react'
import { api } from './api'

const EMPTY = { items: [], results: {} }

export function useBrainstorm(taskId, project) {
  const [state, setState] = useState({ ...EMPTY, project })
  useEffect(() => {
    setState({ ...EMPTY, project })
    if (!project) return
    let stopped = false
    let busy = false
    let request = null
    const load = async () => {
      setState((prev) => prev.items.some((s) => s.expiresAt <= Date.now())
        ? { ...prev, items: prev.items.filter((s) => s.expiresAt > Date.now()) } : prev)
      if (busy) return
      busy = true
      request = new AbortController()
      const timeout = setTimeout(() => request?.abort(), 3000)
      try {
        const result = await (taskId
          ? api.brainstorm(taskId, project, request.signal)
          : api.brainstorms(project, request.signal))
        if (!stopped) setState((prev) => {
          const items = (result.items || []).map((s) => ({
            ...s, expiresAt: Date.now() + s.expires_in * 1000,
          }))
          const results = result.results || {}
          // Не перерисовываем всю доску на каждом пустом ответе.
          if (!items.length && !prev.items.length
              && JSON.stringify(results) === JSON.stringify(prev.results)) return prev
          return { project, items, results }
        })
      } catch { /* при потере сервера активность истекает по полученному сроку */ }
      finally { clearTimeout(timeout); busy = false }
    }
    load()
    const timer = setInterval(load, 2000)
    return () => { stopped = true; clearInterval(timer); request?.abort() }
  }, [taskId, project])
  return state.project === project ? state : EMPTY
}
