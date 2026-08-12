/**
 * Smoke render for the Talent Pool screen.
 *
 * The page crashed at runtime behind the ErrorBoundary ("Something went wrong"), which
 * a passing `vite build` cannot catch — the build only type-checks nothing and bundles.
 * This renders the real component against a mocked API so any render-time throw shows
 * up as a failing test with a real stack instead of a blank error screen.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

import api from '../api/client'
import ResumeMatcher from '../pages/hr/ResumeMatcher'

const RECORD = {
  id: 'r1',
  filename: 'anjali.pdf',
  full_name: 'Anjali Rao',
  email: 'anjali@example.com',
  phone: null,
  college: 'NIT Trichy',
  year_of_passing: 2021,
  job_role: 'Backend Engineer',
  highest_degree: 'B.Tech',
  extracted_skills: ['python'],
  extracted_experience: 3,
  best_match: {
    job_id: 'j1', job_title: 'Backend Engineer', department: 'Engineering',
    ats_score: 72, rating: 'Very Good', matched_skills: ['python'],
    missing_skills: ['kafka'], skills_match_score: 70, semantic_score: 68,
    experience_score: 80, keyword_score: 60, education_score: 75,
  },
  alternates: [],
  uploaded_at: '2026-07-01T00:00:00',
  status: 'new',
  moved_candidate_id: null,
  source: 'registered',
  has_resume: true,
  source_user_id: 'u1',
  source_candidate_id: 'c1',
  source_stage: 'rejected',
  applications: [
    { candidate_id: 'c1', job_id: 'j1', job_title: 'Backend Engineer',
      stage: 'rejected', applied_at: '2026-06-01T00:00:00', ats_score: 72 },
  ],
  application_count: 1,
  outcome: 'rejected',
  last_activity_at: '2026-06-01T00:00:00',
}

const METRICS = {
  total: 12, new_count: 7, ongoing_count: 4, archived_count: 1,
  pipeline: [
    { stage: 'new', label: 'New prospects', count: 7 },
    { stage: 'ongoing', label: 'Ongoing', count: 4 },
    { stage: 'archived', label: 'Archived', count: 1 },
  ],
  sources: [{ name: 'LinkedIn', count: 5 }, { name: 'Referral', count: 3 }],
  outcomes: [{ name: 'rejected', count: 2 }, { name: 'hired', count: 1 }],
  avg_days_since_contact: 9.5, never_contacted: 4,
  added_last_30_days: 6, with_resume: 8,
  unscored: 0, unscoreable: 0,
}

const listResponse = (results, extra = {}) => ({
  data: {
    results, total: results.length, active_job_count: 2,
    registered_pending: 0, orphan_pending: 0,
    active_jobs: [{ job_id: 'j1', job_title: 'Backend Engineer', department: 'Engineering' }],
    ...extra,
  },
})

// The page loads the list and the metrics from different endpoints, so route the mock
// by URL rather than returning one shape for everything.
const routeGet = (results = [RECORD], extra = {}) => (url) =>
  Promise.resolve(url.includes('metrics')
    ? { data: METRICS }
    : listResponse(results, extra))

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation(routeGet())
})

const goToCandidates = async () => {
  const user = (await import('@testing-library/user-event')).default
  await user.click(screen.getByRole('tab', { name: /Candidates/ }))
}

describe('Talent Pool dashboard', () => {
  it('opens on Overview with the metric tiles and pipeline', async () => {
    render(<ResumeMatcher />)
    expect(await screen.findByText('Total in pool')).toBeInTheDocument()
    expect(screen.getByText('Pipeline')).toBeInTheDocument()
    expect(screen.getByText('New prospects')).toBeInTheDocument()
    // Every bar is direct-labelled, so nothing depends on colour alone.
    expect(screen.getByText('Ongoing')).toBeInTheDocument()
  })

  it('shows the source and outcome breakdowns on Analytics', async () => {
    const user = (await import('@testing-library/user-event')).default
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('tab', { name: 'Analytics' }))
    expect(await screen.findByText('Where they came from')).toBeInTheDocument()
    expect(screen.getByText('LinkedIn')).toBeInTheDocument()
    expect(screen.getByText('What happened in the ATS')).toBeInTheDocument()
  })

  it('explains stage vs outcome on Settings', async () => {
    const user = (await import('@testing-library/user-event')).default
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('tab', { name: 'Settings' }))
    expect(await screen.findByText('How stages work')).toBeInTheDocument()
  })

  it('offers Add candidate', async () => {
    render(<ResumeMatcher />)
    expect(await screen.findByText('Add candidate')).toBeInTheDocument()
  })

  it('prompts to rank people who have no score', async () => {
    api.get.mockImplementation((url) => Promise.resolve(
      url.includes('metrics') ? { data: { ...METRICS, unscored: 12, unscoreable: 3 } }
                              : listResponse([RECORD])))
    render(<ResumeMatcher />)
    expect(await screen.findByText('12 people are not ranked yet')).toBeInTheDocument()
    expect(screen.getByText('Rank all 12')).toBeInTheDocument()
    // The rows that can never be scored are accounted for, so the number that does not
    // move is explained rather than looking like a stall.
    expect(screen.getByText(/no resume or profile text/)).toBeInTheDocument()
  })

  it('does not prompt when everyone is ranked', async () => {
    render(<ResumeMatcher />)   // METRICS has unscored: 0
    await screen.findByText('Total in pool')
    expect(screen.queryByText(/not ranked yet/)).not.toBeInTheDocument()
  })

  it('stops looping when a batch ranks nobody', async () => {
    const user = (await import('@testing-library/user-event')).default
    api.get.mockImplementation((url) => Promise.resolve(
      url.includes('metrics') ? { data: { ...METRICS, unscored: 9, unscoreable: 0 } }
                              : listResponse([RECORD])))
    // remaining stays high but nothing is scored — a persistent failure. Without the
    // scored===0 guard this would spin forever.
    api.post.mockResolvedValue({
      data: { scored: 0, failed: 3, remaining: 9, unscoreable: 0,
              active_job_count: 2, message: 'none' },
    })

    render(<ResumeMatcher />)
    await screen.findByText('9 people are not ranked yet')
    await user.click(screen.getByText('Rank all 9'))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post.mock.calls.filter(c => String(c[0]).includes('score-backlog')))
      .toHaveLength(1)
  })
})

describe('Talent Pool candidates tab', () => {
  it('renders a record without crashing', async () => {
    render(<ResumeMatcher />)
    await goToCandidates()
    expect(await screen.findByText('Anjali Rao')).toBeInTheDocument()
    expect(screen.getByText('Talent Pool')).toBeInTheDocument()
  })

  it('shows the stage control, notes and files on a card', async () => {
    api.get.mockImplementation(routeGet([{
      ...RECORD, stage: 'ongoing', notes: 'Strong on system design',
      attachments: [{ id: 'a1', filename: 'cv.pdf', is_scoreable: true }],
    }]))
    render(<ResumeMatcher />)
    await goToCandidates()
    await screen.findByText('Anjali Rao')
    expect(screen.getByLabelText(/Stage for Anjali Rao/)).toHaveValue('ongoing')
    expect(screen.getByText('Strong on system design')).toBeInTheDocument()
    expect(screen.getByText('cv.pdf')).toBeInTheDocument()
    expect(screen.getByText('Log contact')).toBeInTheDocument()
    // Inline editors are present without opening the dialog.
    expect(screen.getByText('Set date')).toBeInTheDocument()
  })

  it('shows the outcome rail and a rejected person as available', async () => {
    render(<ResumeMatcher />)
    await goToCandidates()
    await screen.findByText('Anjali Rao')
    // "Rejected" appears twice by design: the outcome rail tab and the stage chip.
    expect(screen.getAllByText('Rejected').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Available')).toBeInTheDocument()  // record pill
    expect(screen.getByText('Resurface onto a role')).toBeInTheDocument()
  })

  it('renders the application history', async () => {
    render(<ResumeMatcher />)
    await goToCandidates()
    await screen.findByText('Anjali Rao')
    expect(screen.getByText('1 application')).toBeInTheDocument()
  })

  it('renders an unscored person with no resume', async () => {
    api.get.mockImplementation(routeGet([{
      ...RECORD, id: 'r2', full_name: 'Bare Person', best_match: null,
      has_resume: false, extracted_skills: [], applications: [],
      application_count: 0, outcome: 'never_applied', source_stage: null,
    }]))
    render(<ResumeMatcher />)
    await goToCandidates()
    expect(await screen.findByText('Bare Person')).toBeInTheDocument()
  })

  it('picks a whole region of colleges in one click', async () => {
    // The point of the filter: campus hiring is a set operation, so a region goes in
    // as one query the server ORs — not seven searches typed one at a time.
    const user = (await import('@testing-library/user-event')).default
    render(<ResumeMatcher />)
    await goToCandidates()
    await screen.findByText('Anjali Rao')

    await user.click(screen.getByText('More filters'))
    await user.click(screen.getByLabelText('Filter by college'))
    await user.type(screen.getByPlaceholderText('Search colleges'), 'Coimbatore')

    // The group label matches, so the whole region is offered — not only the one
    // college with the city in its name.
    expect(screen.getByText('PSG College of Technology')).toBeInTheDocument()
    await user.click(screen.getByText('Add all'))

    await waitFor(() => {
      const last = api.get.mock.calls.filter(c => !String(c[0]).includes('metrics')).pop()
      expect(last[1].params.college).toContain('psg college')
      expect(last[1].params.college).toContain('kpr institute')
      expect(last[1].params.college.split(',')).toHaveLength(7)
    })
  })

  it('keeps a college that is not on the list', async () => {
    const user = (await import('@testing-library/user-event')).default
    render(<ResumeMatcher />)
    await goToCandidates()
    await screen.findByText('Anjali Rao')

    await user.click(screen.getByText('More filters'))
    await user.click(screen.getByLabelText('Filter by college'))
    await user.type(screen.getByPlaceholderText('Search colleges'), 'Parul University')
    await user.click(screen.getByText(/Use “Parul University”/))

    await waitFor(() => {
      const last = api.get.mock.calls.filter(c => !String(c[0]).includes('metrics')).pop()
      expect(last[1].params.college).toBe('Parul University')
    })
  })

  it('paginates when there are more people than one page', async () => {
    const many = Array.from({ length: 25 }, (_, i) => ({ ...RECORD, id: `r${i}` }))
    api.get.mockImplementation((url) => Promise.resolve(
      url.includes('metrics') ? { data: METRICS }
                              : { data: { ...listResponse(many).data, total: 60 } }))
    render(<ResumeMatcher />)
    await goToCandidates()
    await screen.findAllByText('Anjali Rao')
    await waitFor(() => expect(screen.getByText(/Page/)).toBeInTheDocument())
    expect(screen.getByText('Next')).toBeInTheDocument()
  })
})
