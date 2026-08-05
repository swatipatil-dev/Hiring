/**
 * Tests for JobsTab — filter chips, sort, bookmark toggle, empty states.
 * Uses the real useSavedJobs hook (localStorage) so bookmark interaction is end-to-end.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import JobsTab from '../pages/candidate/tabs/JobsTab'

const MOCK_JOBS = [
  {
    id: '1',
    title: 'Frontend Developer',
    type: 'Full-time',
    location: 'Remote',
    department: 'Engineering',
    skills: ['React', 'TypeScript'],
    posted: '2024-03-10',
  },
  {
    id: '2',
    title: 'Backend Engineer',
    type: 'Part-time',
    location: 'New York',
    department: 'Engineering',
    skills: ['Python', 'Django'],
    posted: '2024-03-05',
  },
  {
    id: '3',
    title: 'UI Designer',
    type: 'Contract',
    location: 'Remote',
    department: 'Design',
    skills: ['Figma', 'CSS'],
    posted: '2024-03-01',
  },
]

function renderTab(jobs = MOCK_JOBS) {
  const onViewDetails = vi.fn()
  const onQuickApply = vi.fn()
  render(<JobsTab jobs={jobs} onViewDetails={onViewDetails} onQuickApply={onQuickApply} />)
  return { onViewDetails, onQuickApply }
}

describe('JobsTab', () => {
  const user = userEvent.setup()

  beforeEach(() => {
    localStorage.clear()
  })

  // ── rendering ──────────────────────────────────────────────────────────────

  it('renders all jobs by default', () => {
    renderTab()
    expect(screen.getByText('Frontend Developer')).toBeInTheDocument()
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument()
    expect(screen.getByText('UI Designer')).toBeInTheDocument()
  })

  it('renders filter chips: All, Full-time, Part-time, Contract, Remote, Saved', () => {
    renderTab()
    expect(screen.getByRole('button', { name: /^All$/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Full-time/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Part-time/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Contract/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Remote/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Saved/ })).toBeInTheDocument()
  })

  it('renders sort row with Newest button', () => {
    renderTab()
    expect(screen.getByRole('button', { name: 'Newest' })).toBeInTheDocument()
  })

  it('does not render Best Match sort when no job has matchPct', () => {
    renderTab()
    expect(screen.queryByRole('button', { name: 'Best Match' })).not.toBeInTheDocument()
  })

  it('renders Best Match sort when at least one job has matchPct', () => {
    const jobsWithMatch = [{ ...MOCK_JOBS[0], matchPct: 80 }, ...MOCK_JOBS.slice(1)]
    renderTab(jobsWithMatch)
    expect(screen.getByRole('button', { name: 'Best Match' })).toBeInTheDocument()
  })

  // ── text search ────────────────────────────────────────────────────────────

  it('text search filters jobs by title', async () => {
    renderTab()
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Frontend')
    expect(screen.getByText('Frontend Developer')).toBeInTheDocument()
    expect(screen.queryByText('Backend Engineer')).not.toBeInTheDocument()
    expect(screen.queryByText('UI Designer')).not.toBeInTheDocument()
  })

  it('text search filters jobs by skill', async () => {
    renderTab()
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Python')
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument()
    expect(screen.queryByText('Frontend Developer')).not.toBeInTheDocument()
  })

  it('text search filters jobs by department', async () => {
    renderTab()
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Design')
    expect(screen.getByText('UI Designer')).toBeInTheDocument()
    expect(screen.queryByText('Frontend Developer')).not.toBeInTheDocument()
  })

  // ── chip filters ───────────────────────────────────────────────────────────

  it('Full-time chip shows only Full-time jobs', async () => {
    renderTab()
    await user.click(screen.getByRole('button', { name: /Full-time/ }))
    expect(screen.getByText('Frontend Developer')).toBeInTheDocument()
    expect(screen.queryByText('Backend Engineer')).not.toBeInTheDocument()
    expect(screen.queryByText('UI Designer')).not.toBeInTheDocument()
  })

  it('Part-time chip shows only Part-time jobs', async () => {
    renderTab()
    await user.click(screen.getByRole('button', { name: /Part-time/ }))
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument()
    expect(screen.queryByText('Frontend Developer')).not.toBeInTheDocument()
  })

  it('Contract chip shows only Contract jobs', async () => {
    renderTab()
    await user.click(screen.getByRole('button', { name: /Contract/ }))
    expect(screen.getByText('UI Designer')).toBeInTheDocument()
    expect(screen.queryByText('Backend Engineer')).not.toBeInTheDocument()
  })

  it('Remote chip shows only jobs with Remote location', async () => {
    renderTab()
    await user.click(screen.getByRole('button', { name: /^Remote$/ }))
    expect(screen.getByText('Frontend Developer')).toBeInTheDocument()
    expect(screen.getByText('UI Designer')).toBeInTheDocument()
    expect(screen.queryByText('Backend Engineer')).not.toBeInTheDocument()
  })

  it('All chip resets filter to show all jobs', async () => {
    renderTab()
    await user.click(screen.getByRole('button', { name: /Full-time/ }))
    await user.click(screen.getByRole('button', { name: /^All$/ }))
    expect(screen.getByText('Frontend Developer')).toBeInTheDocument()
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument()
    expect(screen.getByText('UI Designer')).toBeInTheDocument()
  })

  it('text search and chip filter work together', async () => {
    renderTab()
    await user.click(screen.getByRole('button', { name: /^Remote$/ }))
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'Designer')
    expect(screen.getByText('UI Designer')).toBeInTheDocument()
    expect(screen.queryByText('Frontend Developer')).not.toBeInTheDocument()
  })

  // ── bookmark / saved ───────────────────────────────────────────────────────

  it('each job card has a bookmark button', () => {
    renderTab()
    const bookmarkBtns = screen.getAllByRole('button', { name: /save job/i })
    expect(bookmarkBtns).toHaveLength(3)
  })

  it('clicking bookmark saves a job and updates aria-label to Remove from saved', async () => {
    renderTab()
    const [firstBtn] = screen.getAllByRole('button', { name: /save job/i })
    await user.click(firstBtn)
    expect(screen.getByRole('button', { name: /remove from saved/i })).toBeInTheDocument()
  })

  it('Saved chip count updates after bookmarking a job', async () => {
    renderTab()
    expect(screen.getByRole('button', { name: /^Saved$/ })).toBeInTheDocument()

    const [firstBtn] = screen.getAllByRole('button', { name: /save job/i })
    await user.click(firstBtn)

    expect(screen.getByRole('button', { name: /Saved \(1\)/ })).toBeInTheDocument()
  })

  it('Saved chip shows only bookmarked jobs', async () => {
    renderTab()
    const saveButtons = screen.getAllByRole('button', { name: /save job/i })
    await user.click(saveButtons[0])

    await user.click(screen.getByRole('button', { name: /Saved \(1\)/ }))

    expect(screen.getByText('Frontend Developer')).toBeInTheDocument()
    expect(screen.queryByText('Backend Engineer')).not.toBeInTheDocument()
    expect(screen.queryByText('UI Designer')).not.toBeInTheDocument()
  })

  it('unbookmarking a job removes it from Saved filter view', async () => {
    renderTab()
    const [firstSave] = screen.getAllByRole('button', { name: /save job/i })
    await user.click(firstSave)

    await user.click(screen.getByRole('button', { name: /Saved \(1\)/ }))
    expect(screen.getByText('Frontend Developer')).toBeInTheDocument()

    const removeBtn = screen.getByRole('button', { name: /remove from saved/i })
    await user.click(removeBtn)

    expect(screen.queryByText('Frontend Developer')).not.toBeInTheDocument()
  })

  // ── empty states ───────────────────────────────────────────────────────────

  it('shows No saved jobs message when Saved chip is active with no bookmarks', async () => {
    renderTab()
    await user.click(screen.getByRole('button', { name: /^Saved$/ }))
    expect(screen.getByText(/no saved jobs yet/i)).toBeInTheDocument()
  })

  it('shows no jobs match message when a chip filter has no results', async () => {
    const singleJob = [{ ...MOCK_JOBS[0] }]
    renderTab(singleJob)
    await user.click(screen.getByRole('button', { name: /Part-time/ }))
    expect(screen.getByText(/no jobs match your current filters/i)).toBeInTheDocument()
  })

  it('shows empty state when search yields no results', async () => {
    renderTab()
    await user.type(screen.getByPlaceholderText(/search jobs/i), 'zzznomatch')
    expect(screen.getByText(/no jobs match your current filters/i)).toBeInTheDocument()
  })

  // ── callbacks ──────────────────────────────────────────────────────────────

  it('View Details button calls onViewDetails with the correct job', async () => {
    const { onViewDetails } = renderTab()
    const viewBtns = screen.getAllByRole('button', { name: /view details/i })
    await user.click(viewBtns[0])
    expect(onViewDetails).toHaveBeenCalledWith(expect.objectContaining({ title: 'Frontend Developer' }))
  })

  it('Quick Apply button calls onQuickApply with the correct job', async () => {
    const { onQuickApply } = renderTab()
    const applyBtns = screen.getAllByRole('button', { name: /quick apply/i })
    await user.click(applyBtns[0])
    expect(onQuickApply).toHaveBeenCalledWith(expect.objectContaining({ title: 'Frontend Developer' }))
  })
})
