/**
 * Step 1's "Link to Job Posting" must be able to show the job it has selected.
 *
 * The reported bug: clicking Create Test on a job that already had an unfinished draft filled
 * in Job Position and Job Title but left the dropdown reading "Select a job…". The prefill had
 * in fact worked — ctForm.job_id was set — but the dropdown only rendered
 * allJobs.filter(j => !j.has_test), and has_test is true for ANY template interview including
 * a DRAFT. With no matching <option>, a <select> falls back to displaying the first one.
 *
 * So the job was linked and invisible at the same time, and could not be re-picked either,
 * because it was not in the list.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

const qApi = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(),
  interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
}))
vi.mock('axios', () => ({ default: { create: () => qApi } }))

import api from '../api/client'
import CreateTestWizard from '../pages/shared/CreateTestWizard'

const FREE_JOB = {
  job_id: 'job-free', job_title: 'Data Engineer', department: 'Tech',
  status: 'active', job_category: 'technical', required_skills: [],
  has_test: false, test_id: null, test_status: null,
}
const DRAFT_JOB = {
  job_id: 'job-draft', job_title: 'Ai Developer', department: 'Technology & AI',
  status: 'active', job_category: 'technical', required_skills: [],
  has_test: true, test_id: 42, test_status: 'DRAFT',
}
const SKILLED_JOB = {
  job_id: 'job-skilled', job_title: 'Python Developer', department: 'Tech',
  status: 'active', job_category: 'technical', required_skills: ['Python programming', 'SQL'],
  has_test: false, test_id: null, test_status: null,
}
const READY_JOB = {
  job_id: 'job-ready', job_title: 'QA Engineer', department: 'Tech',
  status: 'active', job_category: 'technical', required_skills: [],
  has_test: true, test_id: 43, test_status: 'READY',
}

const DRAFT_PAYLOAD = {
  id: 42,
  title: 'Ai Developer Test',
  job_id: 'job-draft',
  job_position: 'Ai Developer',
  question_count: 12,
  interview_configuration: { interview_type: 'TECHNICAL', difficulty_level: 'MEDIUM', time_per_question_type: {} },
  skills_configuration: { skills: [{ name: 'React', difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: 'multiple_choice', count_per_set: 3 }] }] },
  security_settings: {},
  evaluation_rules: {},
  ai_generation_rules: {},
  interviewer_details: {},
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.get.mockImplementation((url) => {
    if (url === '/interviewer/all-jobs') return Promise.resolve({ data: [FREE_JOB, DRAFT_JOB, READY_JOB, SKILLED_JOB] })
    if (url === '/interviewer/tests/drafts') return Promise.resolve({ data: { drafts: [DRAFT_PAYLOAD] } })
    return Promise.resolve({ data: [] })
  })
  api.post.mockResolvedValue({ data: { id: 42 } })
  qApi.get.mockResolvedValue({ data: { questions: [] } })
})

// Step 1 has several selects (Job Level, Role Type, Interview Type…). The job one is the
// only that leads with the "Select a job..." placeholder.
const jobSelect = () => screen.getAllByRole('combobox')
  .find(s => s.querySelector('option')?.textContent === 'Select a job...')

const optionFor = (label) =>
  [...(jobSelect()?.querySelectorAll('option') || [])].find(o => o.textContent.includes(label))

describe('Step 1 job dropdown', () => {
  it('shows a job whose only test is a DRAFT, and marks it resumable', async () => {
    render(<CreateTestWizard role="hr" />)

    const opt = await waitFor(() => {
      const o = optionFor('Ai Developer')
      expect(o).toBeTruthy()
      return o
    })
    expect(opt).not.toBeDisabled()
    expect(opt.textContent).toContain('draft in progress')
  })

  it('shows a job with a finished test but disables it, rather than hiding it', async () => {
    render(<CreateTestWizard role="hr" />)

    const opt = await waitFor(() => {
      const o = optionFor('QA Engineer')
      expect(o).toBeTruthy()
      return o
    })
    // Visible and explained beats silently missing — the server would 409 on a second test.
    expect(opt).toBeDisabled()
    expect(opt.textContent).toContain('test already exists')
  })

  it('displays the preselected job even when it has a draft — the reported bug', async () => {
    render(<CreateTestWizard role="hr" preselectedJobId="job-draft" />)

    // Before the fix this was '' and the control read "Select a job...", while the form
    // underneath was already linked to the job.
    await waitFor(() => expect(jobSelect()).toHaveValue('job-draft'))
  })

  it('pulls the unfinished draft in when its job is selected', async () => {
    render(<CreateTestWizard role="hr" preselectedJobId="job-draft" />)

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/interviewer/tests/drafts'))
    // The wizard filling itself in has to be explained, or it reads as stale state.
    expect(await screen.findByText(/Resuming an unfinished draft/)).toBeInTheDocument()
    expect(screen.getByText(/12 questions already in the pool/)).toBeInTheDocument()
  })

  it('leaves a job with no test exactly as it was', async () => {
    render(<CreateTestWizard role="hr" preselectedJobId="job-free" />)

    await waitFor(() => expect(jobSelect()).toHaveValue('job-free'))
    const opt = optionFor('Data Engineer')
    expect(opt).not.toBeDisabled()
    expect(opt.textContent).not.toContain('draft in progress')
    expect(api.get).not.toHaveBeenCalledWith('/interviewer/tests/drafts')
    expect(screen.queryByText(/Resuming an unfinished draft/)).not.toBeInTheDocument()
  })

  it('treats an unrecognised test status as blocked, not resumable', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/interviewer/all-jobs') {
        return Promise.resolve({ data: [{ ...DRAFT_JOB, test_status: 'SOMETHING_NEW' }] })
      }
      return Promise.resolve({ data: [] })
    })
    render(<CreateTestWizard role="hr" />)

    const opt = await waitFor(() => {
      const o = optionFor('Ai Developer')
      expect(o).toBeTruthy()
      return o
    })
    // Fail closed: never open an edit path for a state we do not understand.
    expect(opt).toBeDisabled()
  })

  it('seeds the skills from the job posting, so Step 4 has something to generate', async () => {
    // HR reaches the wizard by clicking "Create Test" on a job card, which lands in the
    // job branch of applyJobSelection. That branch used to drop job.required_skills — only
    // the candidate-request branch (interviewer-only) read them — so HR arrived at Step 4
    // on "No skills configured" with no Generate and no Add Manually button to reach.
    const user = userEvent.setup()
    render(<CreateTestWizard role="hr" preselectedJobId="job-skilled" />)

    await waitFor(() => expect(jobSelect()).toHaveValue('job-skilled'))
    await user.click(screen.getByRole('button', { name: '4. Generate Questions' }))

    expect(screen.queryByText(/No skills configured/)).not.toBeInTheDocument()
    expect(screen.getByText('Python programming')).toBeInTheDocument()
    expect(screen.getByText('SQL')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Add Manually/i }).length).toBe(2)
  })

  it('leaves Step 2 alone when the job lists no skills, for HR to fill in by hand', async () => {
    const user = userEvent.setup()
    render(<CreateTestWizard role="hr" preselectedJobId="job-free" />)

    await waitFor(() => expect(jobSelect()).toHaveValue('job-free'))
    await user.click(screen.getByRole('button', { name: '4. Generate Questions' }))

    expect(screen.getByText(/No skills configured/)).toBeInTheDocument()
  })

  it('canonicalises a draft whose stored question type is a display label', async () => {
    // SkillQuestionType.type is an unvalidated str on the backend, and the drafts endpoint
    // returns it with a bare json.loads, so a row saved with "MCQ" where the key belongs
    // comes back verbatim. It then renders as "MCQ" anyway — TYPE_LABELS misses and the
    // label falls through to the raw value — so the row looks right while losing the
    // MCQ-only actions, its per-question minutes, and the question_type it generates with.
    const user = userEvent.setup()
    api.get.mockImplementation((url) => {
      if (url === '/interviewer/all-jobs') return Promise.resolve({ data: [DRAFT_JOB] })
      if (url === '/interviewer/tests/drafts') {
        return Promise.resolve({ data: { drafts: [{
          ...DRAFT_PAYLOAD,
          skills_configuration: { skills: [{
            name: 'PYTHON', difficulty: 'MEDIUM', marks_per_question: 1,
            question_types: [{ type: 'MCQ', count_per_set: 3 }],
          }] },
        }] } })
      }
      return Promise.resolve({ data: [] })
    })
    render(<CreateTestWizard role="hr" preselectedJobId="job-draft" />)

    // The banner is the wizard's own signal that the draft finished loading into ctForm.
    expect(await screen.findByText(/Resuming an unfinished draft/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '4. Generate Questions' }))

    expect(screen.getByText('PYTHON')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add Manually/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^CSV$/i })).toBeInTheDocument()
  })

  it('does not double-load when the parent already handed over a draft', async () => {
    // The Exam Drafts tab sets openDraft AND preselectedJobId; that path owns the loading.
    render(
      <CreateTestWizard role="hr" preselectedJobId="job-draft" openDraft={DRAFT_PAYLOAD} />
    )

    await waitFor(() => expect(jobSelect()).toHaveValue('job-draft'))
    expect(api.get).not.toHaveBeenCalledWith('/interviewer/tests/drafts')
  })
})
