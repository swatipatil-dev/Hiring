/**
 * HR must be able to write MCQs by hand and bulk-import them from CSV.
 *
 * These two features only ever existed in InterviewerDashboard.jsx's private fork of the
 * wizard, so HR — which renders the shared CreateTestWizard — had no way to add a question
 * that the AI generator had not written. That was an accident of the fork, not a policy:
 * both roles post to the same /interviews/{id}/manual-questions[/csv] endpoints with the
 * same permissions.
 *
 * These tests render the SHARED component with role="hr" specifically so the features
 * cannot quietly slide back into the interviewer-only copy.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

// generator_router is mounted at /interviews, not /api/v1, so the wizard builds its own
// axios instance for it (see the qApi comment in CreateTestWizard.jsx). Intercept the
// instance at axios.create so the assertions can read the URLs it is given.
// vi.hoisted, because vi.mock is lifted above every const in the module.
const qApi = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
}))
vi.mock('axios', () => ({
  default: { create: () => qApi },
}))

import api from '../api/client'
import CreateTestWizard from '../pages/shared/CreateTestWizard'

const DRAFT_ID = 42

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.get.mockResolvedValue({ data: [] })
  api.post.mockResolvedValue({ data: { id: DRAFT_ID } })
  qApi.get.mockResolvedValue({ data: { questions: [] } })
})

/** Render as HR, name the default skill, and land on Step 4. */
async function openStep4(user) {
  render(<CreateTestWizard role="hr" />)
  await user.click(screen.getByRole('button', { name: '2. Skills & Questions' }))
  await user.type(screen.getByPlaceholderText('e.g. React, Node.js'), 'React')
  await user.click(screen.getByRole('button', { name: '4. Generate Questions' }))
}

describe('CreateTestWizard — manual MCQ entry (HR)', () => {
  it('offers Add Manually and CSV on an MCQ pool row', async () => {
    const user = userEvent.setup()
    await openStep4(user)

    expect(screen.getByRole('button', { name: /Add Manually/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^CSV$/i })).toBeInTheDocument()
  })

  it('keeps the pool row wrapping so the last buttons are never clipped away', async () => {
    // The buttons existing is not the same as HR being able to click them. The row is a flex
    // line of nowrap buttons inside a body with overflow-x:hidden, so without wrapping the
    // trailing Add Manually / CSV simply run off the right edge, with no scrollbar to reach
    // them — which is exactly how HR lost the feature while the markup was still present.
    const user = userEvent.setup()
    await openStep4(user)

    const buttonGroup = screen.getByRole('button', { name: /Add Manually/i }).parentElement
    const poolRow = buttonGroup.parentElement
    expect(buttonGroup.style.flexWrap).toBe('wrap')
    expect(poolRow.style.flexWrap).toBe('wrap')
  })

  it('posts a hand-written question to the draft and keeps the modal open for the next one', async () => {
    const user = userEvent.setup()
    qApi.post.mockResolvedValue({
      data: { questions: [{ id: 'q1', question_text: 'What is JSX?', status: 'generated' }] },
    })
    await openStep4(user)

    await user.click(screen.getByRole('button', { name: /Add Manually/i }))
    expect(await screen.findByText('Add MCQ manually')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Enter the question'), 'What is JSX?')
    for (const [L, text] of [['A', 'A syntax extension'], ['B', 'A database'], ['C', 'A router'], ['D', 'A bundler']]) {
      await user.type(screen.getByPlaceholderText(`Option ${L}`), text)
    }
    // B is the correct answer — the radios are in option order.
    await user.click(screen.getAllByRole('radio')[1])
    await user.click(screen.getByRole('button', { name: /Add to Pool/i }))

    await waitFor(() => expect(qApi.post).toHaveBeenCalled())
    const [url, body] = qApi.post.mock.calls[0]
    expect(url).toBe(`/interviews/${DRAFT_ID}/manual-questions`)
    expect(body).toMatchObject({
      skill_name: 'React',
      question_type: 'multiple_choice',
      questions: [{
        question_text: 'What is JSX?',
        options: ['A syntax extension', 'A database', 'A router', 'A bundler'],
        correct_answer: 'B',
      }],
    })

    // The modal stays open, the form is cleared, and the running count is shown, so a
    // batch of questions can be typed without reopening it once per question.
    expect(await screen.findByText(/1 added/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Enter the question')).toHaveValue('')
  })

  it('reports a de-duplicated question instead of silently doing nothing', async () => {
    const user = userEvent.setup()
    // The backend de-dupes by normalized text and returns 200 with an empty list, not an
    // error — so an empty response is the only duplicate signal the UI gets.
    qApi.post.mockResolvedValue({ data: { questions: [], message: 'Duplicate question skipped' } })
    const alerted = vi.spyOn(window, 'alert').mockImplementation(() => {})
    await openStep4(user)

    await user.click(screen.getByRole('button', { name: /Add Manually/i }))
    await user.type(screen.getByPlaceholderText('Enter the question'), 'What is JSX?')
    for (const L of ['A', 'B', 'C', 'D']) {
      await user.type(screen.getByPlaceholderText(`Option ${L}`), `opt ${L}`)
    }
    await user.click(screen.getByRole('button', { name: /Add to Pool/i }))

    await waitFor(() => expect(alerted).toHaveBeenCalledWith('Duplicate question skipped'))
    expect(screen.queryByText(/1 added/)).not.toBeInTheDocument()
  })

  it('refuses to open without a skill name', async () => {
    const user = userEvent.setup()
    const alerted = vi.spyOn(window, 'alert').mockImplementation(() => {})
    render(<CreateTestWizard role="hr" />)
    await user.click(screen.getByRole('button', { name: '4. Generate Questions' }))

    // With no skill named there is no pool row at all, which is the guard working from
    // the other direction — Step 4 tells you to go back to Step 2.
    expect(screen.getByText(/No skills configured/i)).toBeInTheDocument()
    expect(alerted).not.toHaveBeenCalled()
  })
})

describe('CreateTestWizard — CSV import (HR)', () => {
  it('uploads the chosen file as multipart against the draft', async () => {
    const user = userEvent.setup()
    qApi.post.mockResolvedValue({
      data: { questions: [{ id: 'q1' }, { id: 'q2' }], message: 'Imported 2 question(s)' },
    })
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    await openStep4(user)

    const file = new File(
      ['question,option_a,option_b,option_c,option_d,correct_option\n"2+2?","3","4","5","6","B"'],
      'mcq.csv',
      { type: 'text/csv' },
    )
    // The input is hidden and driven by the CSV button's ref click, so upload directly to it.
    const input = document.querySelector('input[type="file"]')
    expect(input).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /^CSV$/i }))
    await user.upload(input, file)

    await waitFor(() => expect(qApi.post).toHaveBeenCalled())
    const [url, body, config] = qApi.post.mock.calls[0]
    expect(url).toBe(`/interviews/${DRAFT_ID}/manual-questions/csv`)
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('skill_name')).toBe('React')
    expect(body.get('question_type')).toBe('multiple_choice')
    expect(body.get('file')).toBe(file)
    expect(config.headers['Content-Type']).toBe('multipart/form-data')
  })

  it('offers a CSV template whose header matches what the importer parses', async () => {
    const user = userEvent.setup()
    // jsdom has no object URLs and no real downloads; capture the Blob instead.
    let captured = null
    const createObjectURL = vi.fn((blob) => { captured = blob; return 'blob:mock' })
    globalThis.URL.createObjectURL = createObjectURL
    globalThis.URL.revokeObjectURL = vi.fn()
    await openStep4(user)

    await user.click(screen.getByRole('button', { name: /Add Manually/i }))
    await user.click(await screen.findByRole('button', { name: /Download CSV template/i }))

    expect(createObjectURL).toHaveBeenCalled()
    const text = await captured.text()
    expect(text.split('\r\n')[0]).toBe(
      '"question","option_a","option_b","option_c","option_d","correct_option"',
    )
    // correct_option is a letter, not the option text — the backend maps it via _letter_for.
    expect(text).toContain('"B"')
  })
})
