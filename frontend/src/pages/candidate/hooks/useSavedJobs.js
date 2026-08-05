import { useState, useCallback } from 'react'

const STORAGE_KEY = 'hireflow_saved_jobs'

function readFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return new Set()
    return new Set(JSON.parse(raw))
  } catch {
    return new Set()
  }
}

function writeToStorage(set) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]))
  } catch {
    // silent fail — localStorage unavailable in some private browsing contexts
  }
}

export default function useSavedJobs() {
  const [savedIds, setSavedIds] = useState(() => readFromStorage())

  const toggle = useCallback((id) => {
    setSavedIds(prev => {
      const next = new Set(prev)
      const key = String(id)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      writeToStorage(next)
      return next
    })
  }, [])

  const isSaved = useCallback((id) => savedIds.has(String(id)), [savedIds])

  return { isSaved, toggle, savedCount: savedIds.size, savedIds }
}
