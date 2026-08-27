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
import { render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

// Recharts sizes itself from its parent, and every element in jsdom measures 0x0 —
// the charts would render nothing and the assertions below would prove nothing. Hand
// the figure a fixed box instead of waiting on a measurement that never arrives.
// (The ResizeObserver stub itself lives in src/__tests__/setup.js.)
vi.mock('recharts', async () => {
  const actual = await vi.importActual('recharts')
  const { cloneElement } = await vi.importActual('react')
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => cloneElement(children, { width: 600, height: 240 }),
  }
})

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
  total: 12, pool_total: 12, new_count: 7, ongoing_count: 4, archived_count: 1,
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
  added_over_time: [
    { period: '2026-06-01', label: 'Jun 2026', count: 4 },
    { period: '2026-07-01', label: 'Jul 2026', count: 0 },
    { period: '2026-08-01', label: 'Aug 2026', count: 8 },
  ],
  trend_granularity: 'month',
  score_bands: Array.from({ length: 10 }, (_, i) => ({
    band: `${i * 10}-${i === 9 ? 100 : i * 10 + 9}`, floor: i * 10,
    count: i === 7 ? 5 : i === 5 ? 3 : 0,
  })),
  unscored_in_view: 4,
  avg_score: 68.2,
  top_colleges: [{ name: 'NIT Trichy', count: 6 }, { name: 'IIT Madras', count: 3 }],
  top_roles: [{ name: 'Backend Engineer', count: 7 }],
}

/** The params the component sent on its most recent metrics request. */
const lastMetricsParams = () => {
  const calls = api.get.mock.calls.filter(c => String(c[0]).includes('metrics'))
  return calls.length ? (calls[calls.length - 1][1]?.params ?? {}) : null
}

const SKILLS = {
  people: 12, people_with_skills: 10, no_skills_on_record: 2,
  distinct_skills: 18, active_roles: 3, demanded_skills: 4, fully_unmet: 1,
  scanned: 12, truncated: false,
  demand: [
    { skill: 'Rust', roles: 2, role_titles: ['Platform Engineer'], candidates: 0,
      related: 0, related_examples: [], related_cluster: '', coverage: 0 },
    { skill: 'Kubernetes', roles: 1, role_titles: ['SRE'], candidates: 0,
      related: 4, related_examples: ['Docker'], related_cluster: 'container orchestration',
      coverage: 0 },
    { skill: 'React', roles: 1, role_titles: ['Frontend Engineer'], candidates: 3,
      related: 0, related_examples: [], related_cluster: '', coverage: 30 },
    { skill: 'Python', roles: 3, role_titles: ['Backend Engineer'], candidates: 9,
      related: 0, related_examples: [], related_cluster: '', coverage: 90 },
  ],
  top_skills: [{ skill: 'Python', count: 9 }, { skill: 'Figma', count: 5 }],
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
  Promise.resolve(
    url.includes('talent-pool/skills') ? { data: SKILLS }
      : url.includes('metrics') ? { data: METRICS }
        : listResponse(results, extra))

/** The params the component sent on its most recent skills request. */
const lastSkillsParams = () => {
  const calls = api.get.mock.calls.filter(c => String(c[0]).includes('talent-pool/skills'))
  return calls.length ? (calls[calls.length - 1][1]?.params ?? {}) : null
}

const goToSkills = async () => {
  const user = (await import('@testing-library/user-event')).default
  await user.click(screen.getByRole('tab', { name: /Skills/ }))
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation(routeGet())
})

const goToCandidates = async () => {
  const user = (await import('@testing-library/user-event')).default
  await user.click(screen.getByRole('tab', { name: /Candidates/ }))
}

describe('Talent Pool dashboard', () => {
  it('opens on the Dashboard with the tiles and the pipeline', async () => {
    render(<ResumeMatcher />)
    expect(await screen.findByText('Total in pool')).toBeInTheDocument()
    expect(screen.getByText('Pipeline')).toBeInTheDocument()
    expect(screen.getByText('New prospects')).toBeInTheDocument()
    // Every bar is direct-labelled, so nothing depends on colour alone. Scoped to the
    // card, because "Ongoing" is also an option in the stage filter now that the filter
    // bar is shared with this tab.
    const pipeline = screen.getByText('Pipeline').closest('section')
    expect(within(pipeline).getByText('Ongoing')).toBeInTheDocument()
  })

  it('carries the old Analytics breakdowns on the same tab', async () => {
    // These used to be one click away on their own tab, which meant the same counts
    // lived in two places and could disagree.
    render(<ResumeMatcher />)
    expect(await screen.findByText('Where they came from')).toBeInTheDocument()
    expect(screen.getByText('LinkedIn')).toBeInTheDocument()
    expect(screen.getByText('What happened in the ATS')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Analytics' })).not.toBeInTheDocument()
  })

  it('renders the trend, the histogram and the top-N lists', async () => {
    render(<ResumeMatcher />)
    expect(await screen.findByText('Additions over time')).toBeInTheDocument()
    expect(screen.getByText('Match score distribution')).toBeInTheDocument()
    expect(screen.getByText('Top colleges')).toBeInTheDocument()
    expect(screen.getByText('Top roles')).toBeInTheDocument()
    expect(screen.getByText('IIT Madras')).toBeInTheDocument()
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument()
  })

  it('keeps the unranked out of the score bands and says so', async () => {
    // "Never ranked" is not "scored zero" — folding them into the 0-9 bar would invent
    // a cohort of terrible candidates out of people nobody has ranked.
    render(<ResumeMatcher />)
    expect(await screen.findByText(/4 people are not ranked at all and appear in no band/))
      .toBeInTheDocument()
  })

  it('explains stage vs outcome on Settings', async () => {
    const user = (await import('@testing-library/user-event')).default
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('tab', { name: /Settings/ }))
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
    expect(screen.queryByText(/people are not ranked yet$/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Rank all/ })).not.toBeInTheDocument()
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

describe('Talent Pool dashboard filtering', () => {
  const userEvent = async () => (await import('@testing-library/user-event')).default

  it('says how much of the pool is in view, and does not cry filter when none is on', async () => {
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    // The count is emphasised, so the sentence spans elements — read the whole line.
    expect(screen.getByRole('status')).toHaveTextContent('All 12 people')
    expect(screen.queryByText('Clear filters')).not.toBeInTheDocument()
  })

  it('sends the filter to the metrics endpoint, not just the list', async () => {
    const user = await userEvent()
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')

    await user.click(screen.getByRole('button', { name: /In process/ }))

    // Debounced by 300ms, same as the list.
    await waitFor(() => expect(lastMetricsParams()).toMatchObject({ outcome: 'in_process' }),
                  { timeout: 2000 })
  })

  it('never sends sort to the metrics endpoint', async () => {
    // Ordering a list cannot change an aggregate, and the server rejects the param —
    // sending it would 422 the whole dashboard on a harmless click.
    const user = await userEvent()
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('button', { name: /Never applied/ }))
    await waitFor(() => expect(lastMetricsParams()).toMatchObject({ outcome: 'never_applied' }),
                  { timeout: 2000 })
    expect(lastMetricsParams()).not.toHaveProperty('sort')
  })

  it('reports the slice against the whole pool once a filter is on', async () => {
    api.get.mockImplementation((url) => Promise.resolve(
      url.includes('metrics') ? { data: { ...METRICS, total: 3, pool_total: 12 } }
                              : listResponse([RECORD])))
    const user = await userEvent()
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('button', { name: /Hired/ }))

    expect(await screen.findByText(/of 12 people/, {}, { timeout: 2000 })).toBeInTheDocument()
    expect(screen.getByText('People in view')).toBeInTheDocument()
  })

  it('shows one empty state, not nine, when nothing matches', async () => {
    const user = await userEvent()
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')

    // Filter INTO the empty slice — starting there would never show the transition.
    api.get.mockImplementation((url) => Promise.resolve(
      url.includes('metrics')
        ? { data: { ...METRICS, total: 0, pool_total: 12, outcomes: [], sources: [],
                    top_colleges: [], top_roles: [],
                    score_bands: METRICS.score_bands.map(b => ({ ...b, count: 0 })),
                    added_over_time: METRICS.added_over_time.map(p => ({ ...p, count: 0 })) } }
        : listResponse([])))
    await user.click(screen.getByRole('button', { name: /Hired/ }))

    expect(await screen.findByText('Nobody matches these filters', {}, { timeout: 2000 }))
      .toBeInTheDocument()
    // The charts are unmounted rather than each drawing its own empty axis.
    expect(screen.queryByText('Additions over time')).not.toBeInTheDocument()
  })

  it('clears back to the whole pool', async () => {
    const user = await userEvent()
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('button', { name: /Hired/ }))
    await waitFor(() => expect(lastMetricsParams()).toMatchObject({ outcome: 'hired' }),
                  { timeout: 2000 })

    await user.click(screen.getAllByRole('button', { name: /Clear filters/ })[0])
    await waitFor(() => expect(lastMetricsParams()).toEqual({}), { timeout: 2000 })
  })

  it('carries the filter across to the Candidates list', async () => {
    const user = await userEvent()
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('button', { name: /In process/ }))
    await waitFor(() => expect(lastMetricsParams()).toMatchObject({ outcome: 'in_process' }),
                  { timeout: 2000 })

    await user.click(screen.getByRole('tab', { name: /Candidates/ }))
    await waitFor(() => {
      const listCalls = api.get.mock.calls.filter(c => !String(c[0]).includes('metrics'))
      expect(listCalls[listCalls.length - 1][1].params).toMatchObject({ outcome: 'in_process' })
    }, { timeout: 2000 })
  })
})

describe('Talent Pool skills tab', () => {
  it('leads with what nobody can cover, worst first', async () => {
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()

    expect(await screen.findByText('Skill by skill')).toBeInTheDocument()
    expect(screen.getByText('Demanded, held by nobody')).toBeInTheDocument()

    // Server order is preserved — worst-covered first is the working order, and
    // re-sorting client-side would silently disagree with it. Scoped to the gap table:
    // Python is also in the top-skills list below it.
    const table = screen.getByRole('list', { name: 'Skills your open roles need' })
    const names = within(table).getAllByTitle(/^(Rust|Kubernetes|React|Python)$/)
      .map(n => n.textContent)
    expect(names).toEqual(['Rust', 'Kubernetes', 'React', 'Python'])
  })

  it('says "nobody" in words, never colour alone', async () => {
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()
    // Two rows have nobody; the bar colour is decoration on top of the words.
    expect((await screen.findAllByText('nobody')).length).toBe(2)
    expect(screen.getByText('3 people')).toBeInTheDocument()
  })

  it('names the grouping behind a related match rather than asserting equivalence', async () => {
    // The clusters are hand-curated and some are loose, so the reader has to be able
    // to judge "related" for themselves.
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()
    expect(await screen.findByText(/4 people know Docker — same container orchestration group/))
      .toBeInTheDocument()
  })

  it('does not blame people whose resume was never read', async () => {
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()
    expect(await screen.findByText(/counted in none of these rows/)).toBeInTheDocument()
  })

  it('shows strengths no open role asks for', async () => {
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()
    expect(await screen.findByText('What this group actually knows')).toBeInTheDocument()
    expect(screen.getByText('Figma')).toBeInTheDocument()
  })

  it('drills a skill through to the people who have it', async () => {
    const user = (await import('@testing-library/user-event')).default
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()

    await user.click(await screen.findByTitle(/Show the 3 in the pool with React/))

    await waitFor(() => {
      const listCalls = api.get.mock.calls.filter(
        c => !String(c[0]).includes('metrics') && !String(c[0]).includes('skills'))
      expect(listCalls[listCalls.length - 1][1].params).toMatchObject({ skill: 'React' })
    }, { timeout: 2000 })
    // The filter has no control of its own, so it needs a chip or the short list is
    // inexplicable.
    expect(screen.getByLabelText('Remove the React filter')).toBeInTheDocument()
  })

  it('will not offer a drill-down that would land on nobody', async () => {
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()
    expect(screen.queryByTitle(/Show the 0 in the pool/)).not.toBeInTheDocument()
  })

  it('passes the shared filter to the skills endpoint', async () => {
    const user = (await import('@testing-library/user-event')).default
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await user.click(screen.getByRole('button', { name: /In process/ }))
    await goToSkills()

    await waitFor(() => expect(lastSkillsParams()).toMatchObject({ outcome: 'in_process' }),
                  { timeout: 2000 })
    expect(lastSkillsParams()).not.toHaveProperty('sort')
  })

  it('explains itself when there is nothing to measure against', async () => {
    api.get.mockImplementation((url) => Promise.resolve(
      url.includes('talent-pool/skills')
        ? { data: { ...SKILLS, active_roles: 0, demand: [], demanded_skills: 0 } }
        : url.includes('metrics') ? { data: METRICS } : listResponse([RECORD])))
    render(<ResumeMatcher />)
    await screen.findByText('Total in pool')
    await goToSkills()
    expect(await screen.findByText('No open roles to measure against')).toBeInTheDocument()
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
