/**
 * Tests for useSavedJobs hook — localStorage persistence, toggle, isSaved, savedCount.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import useSavedJobs from '../pages/candidate/hooks/useSavedJobs'

const STORAGE_KEY = 'hireflow_saved_jobs'

describe('useSavedJobs', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('starts with an empty saved set when localStorage has no data', () => {
    const { result } = renderHook(() => useSavedJobs())
    expect(result.current.savedCount).toBe(0)
    expect(result.current.isSaved('job-1')).toBe(false)
  })

  it('toggle() adds a job id and persists it to localStorage', () => {
    const { result } = renderHook(() => useSavedJobs())

    act(() => { result.current.toggle('job-1') })

    expect(result.current.isSaved('job-1')).toBe(true)
    expect(result.current.savedCount).toBe(1)

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY))
    expect(stored).toContain('job-1')
  })

  it('toggle() removes a job id that is already saved', () => {
    const { result } = renderHook(() => useSavedJobs())

    act(() => { result.current.toggle('job-1') })
    expect(result.current.isSaved('job-1')).toBe(true)

    act(() => { result.current.toggle('job-1') })
    expect(result.current.isSaved('job-1')).toBe(false)
    expect(result.current.savedCount).toBe(0)

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY))
    expect(stored).not.toContain('job-1')
  })

  it('isSaved returns false for an id that was never saved', () => {
    const { result } = renderHook(() => useSavedJobs())
    act(() => { result.current.toggle('job-1') })
    expect(result.current.isSaved('job-99')).toBe(false)
  })

  it('savedCount increments and decrements correctly across multiple ids', () => {
    const { result } = renderHook(() => useSavedJobs())

    act(() => { result.current.toggle('a') })
    act(() => { result.current.toggle('b') })
    act(() => { result.current.toggle('c') })
    expect(result.current.savedCount).toBe(3)

    act(() => { result.current.toggle('b') })
    expect(result.current.savedCount).toBe(2)
  })

  it('hydrates saved ids from localStorage on mount', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['job-10', 'job-20']))

    const { result } = renderHook(() => useSavedJobs())

    expect(result.current.savedCount).toBe(2)
    expect(result.current.isSaved('job-10')).toBe(true)
    expect(result.current.isSaved('job-20')).toBe(true)
    expect(result.current.isSaved('job-30')).toBe(false)
  })

  it('coerces numeric ids to strings so numeric and string ids match', () => {
    const { result } = renderHook(() => useSavedJobs())

    act(() => { result.current.toggle(42) })

    expect(result.current.isSaved(42)).toBe(true)
    expect(result.current.isSaved('42')).toBe(true)
  })

  it('handles corrupt localStorage data gracefully — starts empty', () => {
    localStorage.setItem(STORAGE_KEY, 'not valid json }{')

    const { result } = renderHook(() => useSavedJobs())

    expect(result.current.savedCount).toBe(0)
    expect(result.current.isSaved('any')).toBe(false)
  })

  it('savedIds set is exposed and reflects current state', () => {
    const { result } = renderHook(() => useSavedJobs())

    act(() => { result.current.toggle('x') })

    expect(result.current.savedIds.has('x')).toBe(true)
  })
})
