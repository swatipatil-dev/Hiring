import { useState, useCallback } from 'react'

const STORAGE_KEY = 'hireflow_my_learning'

// Tracks a candidate's learning state per course id, persisted in localStorage.
// Two buckets: `enrolled` (started) and `completed` (finished). A course lives
// in at most one bucket. Backend persistence can replace this later without
// changing the component API.
function readFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { enrolled: [], completed: [] }
    const parsed = JSON.parse(raw)
    return {
      enrolled: Array.isArray(parsed.enrolled) ? parsed.enrolled : [],
      completed: Array.isArray(parsed.completed) ? parsed.completed : [],
    }
  } catch {
    return { enrolled: [], completed: [] }
  }
}

function writeToStorage(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // silent fail — localStorage unavailable in some private browsing contexts
  }
}

export default function useMyLearning() {
  const [state, setState] = useState(readFromStorage)

  const update = useCallback((mutator) => {
    setState(prev => {
      const sets = { enrolled: new Set(prev.enrolled), completed: new Set(prev.completed) }
      mutator(sets)
      const out = { enrolled: [...sets.enrolled], completed: [...sets.completed] }
      writeToStorage(out)
      return out
    })
  }, [])

  const enroll = useCallback((id) => update(({ enrolled, completed }) => {
    const k = String(id); completed.delete(k); enrolled.add(k)
  }), [update])

  const complete = useCallback((id) => update(({ enrolled, completed }) => {
    const k = String(id); enrolled.delete(k); completed.add(k)
  }), [update])

  const reset = useCallback((id) => update(({ enrolled, completed }) => {
    const k = String(id); enrolled.delete(k); completed.delete(k)
  }), [update])

  const statusOf = useCallback((id) => {
    const k = String(id)
    if (state.completed.includes(k)) return 'completed'
    if (state.enrolled.includes(k)) return 'enrolled'
    return 'none'
  }, [state])

  return {
    statusOf,
    enroll,
    complete,
    reset,
    enrolledCount: state.enrolled.length,
    completedCount: state.completed.length,
    completedIds: state.completed,
  }
}
