/**
 * Bulk resume upload packs the queue into chunks before sending.
 *
 * Two server limits make one big request the wrong shape, and a chunk has to respect
 * both at once:
 *
 *   * Apache's LimitRequestBody rejects an oversized body with 413 BEFORE FastAPI runs,
 *     so the API cannot say what went wrong. Twenty 5 MB PDFs is 100 MB and used to
 *     fail as a bare "Could not match resumes."
 *   * The API processes at most RESUME_MATCH_MAX_FILES (20) per request and silently
 *     discards the rest.
 *
 * A count-only split would still 413 on large files; a byte-only split could exceed the
 * API's file cap on small ones. These tests pin both bounds, and the invariant that no
 * file is ever dropped — losing a resume silently is the failure mode that matters.
 */
import { describe, it, expect } from 'vitest'

import { chunkFiles } from '../pages/hr/ResumeMatcher'

const MB = 1024 * 1024
const CHUNK_FILES = 8
const CHUNK_BYTES = 16 * MB

/** A stand-in for File — chunkFiles only reads `name` and `size`. */
const f = (name, sizeMB) => ({ name, size: Math.round(sizeMB * MB) })

const flat = (chunks) => chunks.flat()
const bytesOf = (chunk) => chunk.reduce((n, x) => n + x.size, 0)

describe('chunkFiles', () => {
  it('returns nothing for an empty queue', () => {
    expect(chunkFiles([])).toEqual([])
  })

  it('keeps a small batch in one request', () => {
    const files = [f('a.pdf', 0.3), f('b.pdf', 0.4), f('c.pdf', 0.2)]
    const chunks = chunkFiles(files)
    expect(chunks).toHaveLength(1)
    expect(chunks[0]).toEqual(files)
  })

  it('splits on the file-count bound', () => {
    // 20 small resumes: bytes are irrelevant here, the count is what splits them.
    const files = Array.from({ length: 20 }, (_, i) => f(`r${i}.pdf`, 0.2))
    const chunks = chunkFiles(files)
    expect(chunks).toHaveLength(3)          // 8 + 8 + 4
    expect(chunks.map(c => c.length)).toEqual([8, 8, 4])
  })

  it('splits on the byte bound before reaching the count bound', () => {
    // 6 x 4 MB = 24 MB, over the 16 MB budget but only 6 files.
    const files = Array.from({ length: 6 }, (_, i) => f(`big${i}.pdf`, 4))
    const chunks = chunkFiles(files)
    expect(chunks.length).toBeGreaterThan(1)
    for (const c of chunks) expect(bytesOf(c)).toBeLessThanOrEqual(CHUNK_BYTES)
  })

  it('never exceeds either bound, for a mixed batch', () => {
    const files = [
      f('tiny.pdf', 0.1), f('big1.pdf', 4.5), f('big2.pdf', 4.5), f('mid.docx', 2),
      f('big3.pdf', 4.5), f('tiny2.pdf', 0.1), f('big4.pdf', 4.9), f('mid2.pdf', 1.5),
      f('tiny3.pdf', 0.2), f('big5.pdf', 4.2), f('mid3.pdf', 3), f('tiny4.pdf', 0.3),
    ]
    for (const c of chunkFiles(files)) {
      expect(c.length).toBeLessThanOrEqual(CHUNK_FILES)
      expect(bytesOf(c)).toBeLessThanOrEqual(CHUNK_BYTES)
    }
  })

  it('loses no file and preserves order', () => {
    const files = Array.from({ length: 47 }, (_, i) => f(`r${i}.pdf`, (i % 5) + 0.5))
    expect(flat(chunkFiles(files))).toEqual(files)
  })

  it('emits no empty chunk', () => {
    const files = Array.from({ length: 25 }, (_, i) => f(`r${i}.pdf`, 3))
    for (const c of chunkFiles(files)) expect(c.length).toBeGreaterThan(0)
  })

  it('keeps a single oversized file in its own chunk rather than dropping it', () => {
    // Guards the loop's `current.length &&` condition: without it a file bigger than the
    // whole budget would open a chunk, immediately split, and vanish. In practice the
    // MAX_FILE_BYTES check rejects it earlier with its name — but silently losing a
    // resume must not be reachable from here.
    const files = [f('huge.pdf', 40), f('small.pdf', 0.5)]
    const chunks = chunkFiles(files)
    expect(flat(chunks)).toHaveLength(2)
    expect(chunks[0]).toEqual([files[0]])
  })

  it('stays within the API per-request cap of 20 files', () => {
    const files = Array.from({ length: 100 }, (_, i) => f(`r${i}.pdf`, 0.05))
    for (const c of chunkFiles(files)) expect(c.length).toBeLessThanOrEqual(20)
  })
})
