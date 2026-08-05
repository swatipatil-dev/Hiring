import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  FileSearch, Upload, X, ChevronDown, ChevronRight, ChevronLeft, CheckCircle2,
  AlertCircle, Download, RefreshCw, Trash2, UserPlus, Search, SlidersHorizontal, Users,
  Pencil, PhoneCall, Paperclip, Save,
} from 'lucide-react'
import api from '../../api/client'
import MoveToATSModal from './MoveToATSModal'
import CandidateForm from './CandidateForm'
import { OverviewPanel, AnalyticsPanel, SettingsPanel } from './TalentPoolPanels'
import './ResumeMatcher.css'

const ACCEPT = '.pdf,.doc,.docx'
const ALLOWED = ['pdf', 'doc', 'docx']
// Profiles also take images — they display but cannot be ATS-scored, since matching
// reads text and no OCR runs on this path.
const ATTACH_ACCEPT = '.pdf,.doc,.docx,.png,.jpg,.jpeg,.webp'

// Same bands the ATS sheet uses, on the workspace's warm status palette.
const scoreColor = (s) =>
  s >= 80 ? 'var(--rm-strong)' : s >= 60 ? 'var(--rm-good)' : s >= 40 ? 'var(--rm-fair)' : 'var(--rm-weak)'

// Segments lit on a 10-step gauge. A zero score lights none.
const segments = (score) => (score > 0 ? Math.max(1, Math.round(score / 10)) : 0)

const fmtDate = (iso) => {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** The signature: score numeral over a bottom-up segmented gauge. Stacked
 *  down the list it becomes one scannable column of match strength. */
function Spine({ score }) {
  if (score == null) {
    return (
      <div className="rm-spine rm-spine--empty">
        <AlertCircle size={18} />
      </div>
    )
  }
  const lit = segments(score)
  return (
    <div className="rm-spine" style={{ color: scoreColor(score) }}>
      <div className="rm-spine__score">{Math.round(score)}</div>
      <div className="rm-spine__of">/100</div>
      <div className="rm-spine__gauge">
        {Array.from({ length: 10 }, (_, i) => (
          <span
            key={i}
            className={`rm-spine__seg${i < lit ? ' rm-spine__seg--on' : ''}`}
            style={i < lit ? { animationDelay: `${140 + i * 40}ms` } : undefined}
          />
        ))}
      </div>
    </div>
  )
}

/** One ATS factor, using the spine's segment vocabulary laid flat. */
function Factor({ label, value }) {
  if (value == null) return null
  const lit = segments(value)
  return (
    <div className="rm-factor" style={{ color: scoreColor(value) }}>
      <span className="rm-factor__label">{label}</span>
      <span className="rm-factor__track">
        {Array.from({ length: 10 }, (_, i) => (
          <i key={i} className={`rm-factor__seg${i < lit ? ' rm-factor__seg--on' : ''}`} />
        ))}
      </span>
      <span className="rm-factor__val">{Math.round(value)}</span>
    </div>
  )
}

function SkillSet({ label, skills, tone }) {
  if (!skills || skills.length === 0) return null
  return (
    <div className="rm-skillset">
      <span className="rm-skillset__label">{label}</span>
      {skills.slice(0, 10).map((s, i) => (
        <span key={i} className={`rm-skill rm-skill--${tone}`}>{s}</span>
      ))}
      {skills.length > 10 && (
        <span className="rm-skill__more">+{skills.length - 10}</span>
      )}
    </div>
  )
}

/** A record field in the mono meta line (college, year, role, contact). */
function Fact({ children, title }) {
  if (!children) return null
  return <span title={title}>{children}</span>
}

/** Every role this person has been put forward for, and how far each one got. */
function History({ applications }) {
  const [open, setOpen] = useState(applications.length <= 2)
  if (applications.length === 0) return null

  const latest = applications[0]
  return (
    <div className="rm-hist">
      <button className="rm-toggle" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {applications.length} application{applications.length === 1 ? '' : 's'}
        {!open && latest.job_title ? ` · latest: ${latest.job_title} — ${stageLabel(latest.stage)}` : ''}
      </button>
      {open && (
        <div className="rm-hist__rows">
          {applications.map(a => (
            <div key={a.candidate_id} className="rm-hist__row">
              <span className="rm-hist__job" title={a.job_title || 'This job posting was removed'}>
                {a.job_title || 'Removed job'}
              </span>
              <span className={`rm-stage rm-stage--${stageTone(a.stage)}`}>
                {stageLabel(a.stage)}
              </span>
              <span className="rm-hist__when">{fmtDate(a.applied_at) || '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const fmtSize = (b) =>
  !b ? null : b >= 1024 * 1024 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`

/** Files on a profile. Images show as attachments but never drive matching. */
function Attachments({ result, onView, onDownload, onReplace, onRemove, busy }) {
  const files = result.attachments || []
  if (files.length === 0) return null
  return (
    <div className="tp-filelist">
      <span className="rm-skillset__label">Files</span>
      <ul>
        {files.map(f => (
          <li className="tp-filerow" key={f.id}>
            <button className="tp-file__name" onClick={() => onView(result, f)}
                    title={`Open ${f.filename}`}>
              <Paperclip size={12} /> {f.filename}
            </button>
            <span className="tp-file__meta">
              {[fmtDate(f.uploaded_at), fmtSize(f.size_bytes)].filter(Boolean).join(' · ')}
              {!f.is_scoreable && (
                <span className="tp-file__tag" title="Images can be stored and viewed but not ATS-scored">
                  not scored
                </span>
              )}
            </span>
            <span className="tp-file__acts">
              <button className="tp-file__act" onClick={() => onDownload(result, f)}
                      title="Download">
                <Download size={12} />
              </button>
              <button className="tp-file__act" onClick={() => onReplace(result, f)}
                      disabled={busy} title="Replace this file">
                <RefreshCw size={12} />
              </button>
              <button className="tp-file__act tp-file__act--danger" disabled={busy}
                      onClick={() => onRemove(result, f)} title={`Remove ${f.filename}`}>
                <X size={12} />
              </button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Notes edited where they are read, rather than in a dialog two clicks away. */
function InlineNotes({ result, onSave, busy }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(result.notes || '')

  useEffect(() => { setDraft(result.notes || '') }, [result.notes])

  if (!editing) {
    return (
      <div className="tp-notes">
        <div className="tp-notes__head">
          <span className="rm-skillset__label">Notes</span>
          <button className="tp-inline__edit" onClick={() => setEditing(true)}>
            {result.notes ? 'Edit' : 'Add notes'}
          </button>
        </div>
        {result.notes
          ? <p title={result.notes}>{result.notes}</p>
          : <p className="tp-notes--empty">No notes yet. Interview feedback, what they
              are after, when to follow up.</p>}
      </div>
    )
  }

  return (
    <div className="tp-notes">
      <span className="rm-skillset__label">Notes</span>
      <textarea className="textarea" rows={4} value={draft} autoFocus
                onChange={e => setDraft(e.target.value)}
                placeholder="Interview feedback, what they are looking for, when to follow up…" />
      <div className="tp-inline__actions">
        <button className="rm-act rm-act--primary" disabled={busy}
                onClick={async () => { await onSave(result, { notes: draft }); setEditing(false) }}>
          <Save size={13} /> Save notes
        </button>
        {/* Cancel restores what was there, so an accidental edit costs nothing. */}
        <button className="rm-act" disabled={busy}
                onClick={() => { setDraft(result.notes || ''); setEditing(false) }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

/** Skills edited in place — the most-corrected field, since parsing often misses. */
function InlineSkills({ result, onSave, busy }) {
  const skills = result.extracted_skills || []
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(skills.join(', '))

  useEffect(() => { setDraft((result.extracted_skills || []).join(', ')) }, [result.extracted_skills])

  if (!editing) {
    return (
      <div className="tp-inline">
        <SkillSet label="Skills" skills={skills} tone="have" />
        {skills.length === 0 && <span className="rm-skillset__label">Skills</span>}
        <button className="tp-inline__edit" onClick={() => setEditing(true)}>
          {skills.length ? 'Edit' : 'Add skills'}
        </button>
      </div>
    )
  }

  return (
    <div className="tp-inline tp-inline--editing">
      <span className="rm-skillset__label">Skills</span>
      <input className="input" value={draft} autoFocus
             onChange={e => setDraft(e.target.value)}
             placeholder="python, fastapi, mysql" />
      <div className="tp-inline__actions">
        <button className="rm-act rm-act--primary" disabled={busy}
                onClick={async () => {
                  await onSave(result, {
                    skills: draft.split(',').map(s => s.trim()).filter(Boolean),
                  })
                  setEditing(false)
                }}>
          <Save size={13} /> Save skills
        </button>
        <button className="rm-act" disabled={busy}
                onClick={() => { setDraft(skills.join(', ')); setEditing(false) }}>
          Cancel
        </button>
      </div>
      <span className="tp-inline__hint">Separate with commas.</span>
    </div>
  )
}

/** Contact date — "today" is one click, but a call logged late needs a real date. */
function ContactControl({ result, onSave, busy }) {
  const [open, setOpen] = useState(false)
  const iso = result.last_contact_at ? String(result.last_contact_at).slice(0, 10) : ''
  const [draft, setDraft] = useState(iso)

  useEffect(() => { setDraft(iso) }, [iso])

  if (!open) {
    return (
      <div className="tp-inline">
        <span className="rm-skillset__label">Last contact</span>
        <span className="tp-inline__value">
          {result.days_since_contact == null
            ? 'Never contacted'
            : `${fmtDate(result.last_contact_at)} · ${result.days_since_contact} day${result.days_since_contact === 1 ? '' : 's'} ago`}
        </span>
        <button className="tp-inline__edit" onClick={() => setOpen(true)}>Set date</button>
      </div>
    )
  }

  return (
    <div className="tp-inline tp-inline--editing">
      <span className="rm-skillset__label">Last contact</span>
      <input className="input" type="date" value={draft} max={new Date().toISOString().slice(0, 10)}
             onChange={e => setDraft(e.target.value)} />
      <div className="tp-inline__actions">
        <button className="rm-act rm-act--primary" disabled={busy}
                onClick={async () => {
                  await onSave(result, { last_contact_at: draft ? `${draft}T12:00:00` : null })
                  setOpen(false)
                }}>
          <Save size={13} /> Save date
        </button>
        <button className="rm-act" disabled={busy} onClick={() => { setDraft(iso); setOpen(false) }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function Record({ result, index, activeJobCount, onMove, onRematch, onDelete, busy,
                  onStage, onEdit, onContact, onUpload, onRemoveFile, onPatch,
                  onViewFile, onReplaceFile }) {
  const [showFactors, setShowFactors] = useState(false)
  const [showAlts, setShowAlts] = useState(false)
  const best = result.best_match
  const registered = result.source === 'registered'
  const uploaded = fmtDate(result.uploaded_at)
  const applications = result.applications || []
  // A rejected or declined person is the whole reason this screen exists — they read
  // as available, never as a failure.
  const available = result.outcome === 'rejected' || result.outcome === 'declined'

  return (
    <article className="rm-record" style={{ animationDelay: `${Math.min(index, 12) * 45}ms` }}>
      <Spine score={best ? best.ats_score : null} />

      <div className="rm-record__body">
        <div className="rm-head">
          <span className="rm-name" title={result.filename}>
            {result.full_name || result.filename}
          </span>
          <span className={`rm-pill rm-pill--${registered ? 'portal' : 'upload'}`}>
            {registered ? 'Registered' : result.source === 'applied' ? 'Added by HR' : 'Uploaded'}
          </span>
          <span className={`tp-stagepill tp-stagepill--${result.stage || 'new'}`}>
            {stageLabelOf(result.stage)}
          </span>
          {available && <span className="rm-pill rm-pill--available">Available</span>}
          {result.outcome === 'hired' && <span className="rm-pill rm-pill--moved">Hired</span>}
          {result.outcome === 'in_process' && (
            <span className="rm-pill rm-pill--live">In process</span>
          )}
          {result.outcome === 'offer' && <span className="rm-pill rm-pill--live">Offer out</span>}
        </div>

        <div className="rm-facts">
          <Fact title="College">{result.college}</Fact>
          <Fact title="Year of passing">{result.year_of_passing}</Fact>
          <Fact title="Job role">{result.job_role}</Fact>
          {typeof result.extracted_experience === 'number' && (
            <Fact title="Experience">
              {result.extracted_experience} yr{result.extracted_experience === 1 ? '' : 's'}
            </Fact>
          )}
          <Fact title="Email">{result.email}</Fact>
          <Fact title="Phone">{result.phone}</Fact>
          <Fact title="Sourcing channel">{result.source_channel}</Fact>
          {uploaded && (
            <Fact title={registered ? 'Registered' : 'Uploaded'}>
              {registered ? 'Joined' : 'Added'} {uploaded}
            </Fact>
          )}
          <Fact title="Last contact">
            {result.days_since_contact == null
              ? 'Never contacted'
              : result.days_since_contact === 0
                ? 'Contacted today'
                : `Contacted ${result.days_since_contact}d ago`}
          </Fact>
        </div>

        {result.error && (
          <div className="rm-alert rm-alert--err">
            <AlertCircle size={15} /> {result.error}
          </div>
        )}

        {!best && !result.error && (
          <div className="rm-verdict" style={{ borderLeftColor: 'var(--border-medium)' }}>
            <div className="rm-verdict__eyebrow" style={{ color: 'var(--text-tertiary)' }}>
              {registered && !result.has_resume && result.extracted_skills.length === 0
                ? 'Nothing to score'
                : 'No match yet'}
            </div>
            <div className="rm-verdict__meta" style={{ marginTop: 2 }}>
              {registered && !result.has_resume && result.extracted_skills.length === 0
                ? "Registered on the portal but hasn't added a resume or filled in their profile yet."
                : activeJobCount === 0
                  ? "There are no open roles to rank against. Publish a role, then re-match."
                  : "Not ranked against your current roles yet. Re-match to score them."}
            </div>
          </div>
        )}

        {best && (
          <>
            <div className="rm-verdict">
              <div className="rm-verdict__eyebrow">Best fit</div>
              <div className="rm-verdict__job">{best.job_title}</div>
              <div className="rm-verdict__meta">
                {best.department || 'General'}{best.rating ? ` · ${best.rating}` : ''}
              </div>
            </div>

            {best.skills_match_score != null && (
              <div>
                <button className="rm-toggle" onClick={() => setShowFactors(s => !s)}
                        aria-expanded={showFactors}>
                  {showFactors ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  How this scored
                </button>
                {showFactors && (
                  <div className="rm-factors">
                    <Factor label="Skills" value={best.skills_match_score} />
                    <Factor label="Semantic" value={best.semantic_score} />
                    <Factor label="Experience" value={best.experience_score} />
                    <Factor label="Keywords" value={best.keyword_score} />
                    <Factor label="Education" value={best.education_score} />
                  </div>
                )}
              </div>
            )}

            <SkillSet label="Has" skills={best.matched_skills} tone="have" />
            <SkillSet label="Gaps" skills={best.missing_skills} tone="gap" />

            {result.alternates?.length > 0 && (
              <div className="rm-alts">
                <button className="rm-toggle" onClick={() => setShowAlts(o => !o)}
                        aria-expanded={showAlts}>
                  {showAlts ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {result.alternates.length} other role{result.alternates.length === 1 ? '' : 's'} considered
                </button>
                {showAlts && (
                  <div style={{ marginTop: 8 }}>
                    {result.alternates.map((alt, i) => (
                      <div key={i} className="rm-alt">
                        <span className="rm-alt__title">
                          {alt.job_title}
                          <span className="rm-alt__dept">{alt.department || 'General'}</span>
                        </span>
                        <span className="rm-alt__score" style={{ color: scoreColor(alt.ats_score) }}>
                          {Math.round(alt.ats_score)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        <InlineSkills result={result} onSave={onPatch} busy={busy} />
        <ContactControl result={result} onSave={onPatch} busy={busy} />
        <InlineNotes result={result} onSave={onPatch} busy={busy} />

        <Attachments result={result} onView={onViewFile} onDownload={onDownloadAttachment}
                     onReplace={onReplaceFile} onRemove={onRemoveFile} busy={busy} />

        {/* Outside the best-match block on purpose: someone rejected a year ago may
            have no current score, and their history is exactly what HR came to see. */}
        <History applications={applications} />
      </div>

      {result.id && (
        <div className="rm-actions">
          {/* One-click stage move — the everyday action, so it leads. */}
          <select className="tp-stagesel" value={result.stage || 'new'} disabled={busy}
                  aria-label={`Stage for ${result.full_name || 'this person'}`}
                  onChange={e => onStage(result, e.target.value)}>
            {STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>

          <button className="rm-act" onClick={() => onEdit(result)} disabled={busy}>
            <Pencil size={14} /> Edit
          </button>
          <button className="rm-act" onClick={() => onContact(result)} disabled={busy}>
            <PhoneCall size={14} /> Log contact
          </button>
          <button className="rm-act" onClick={() => onUpload(result)} disabled={busy}>
            <Paperclip size={14} /> Attach
          </button>

          {/* Rendered even once promoted: a strong resume can belong on more than one
              open role, and most people here are already in the pipeline for the job
              they applied to themselves. */}
          <button className="rm-act rm-act--primary" onClick={() => onMove(result)}
                  disabled={busy || activeJobCount === 0}>
            <UserPlus size={14} />
            {available ? 'Resurface onto a role'
              : applications.length > 0 ? 'Move to another job' : 'Move to ATS'}
          </button>
          {result.has_resume && (
            <button className="rm-act" onClick={() => onDownload(result)}>
              <Download size={14} /> Resume
            </button>
          )}
          <button className="rm-act" onClick={() => onRematch(result)} disabled={busy}>
            <RefreshCw size={14} className={busy ? 'spin' : undefined} /> Re-match
          </button>
          <button className="rm-act rm-act--danger" onClick={() => onDelete(result)} disabled={busy}>
            <Trash2 size={14} /> Remove
          </button>
        </div>
      )}
    </article>
  )
}

// Downloads go through the API client so the auth header is attached — a plain
// <a href> would hit the endpoint unauthenticated and 403.
/**
 * Open a file in a new tab instead of forcing a download.
 *
 * Fetched through the API client so the auth header is attached, then handed to the
 * browser as a blob URL with its real content type — that is what lets Chrome render
 * a PDF or an image inline rather than dumping it in the downloads folder.
 */
async function onViewAttachment(result, file) {
  try {
    const r = await api.get(`/hr/resume-pool/${result.id}/attachments/${file.id}`,
                            { responseType: 'blob' })
    const url = window.URL.createObjectURL(
      new Blob([r.data], { type: file.content_type || 'application/octet-stream' })
    )
    const win = window.open(url, '_blank', 'noopener')
    if (!win) {
      alert('Your browser blocked the pop-up. Allow pop-ups for this site to view files.')
    }
    // Long enough for the new tab to take ownership of the blob.
    setTimeout(() => window.URL.revokeObjectURL(url), 60000)
  } catch {
    alert('Could not open this file.')
  }
}

async function onDownloadAttachment(result, file) {
  try {
    const r = await api.get(`/hr/resume-pool/${result.id}/attachments/${file.id}`,
                            { responseType: 'blob' })
    const url = window.URL.createObjectURL(new Blob([r.data]))
    const a = document.createElement('a')
    a.href = url
    a.download = file.filename || 'file'
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.URL.revokeObjectURL(url)
  } catch {
    alert('Could not download this file.')
  }
}

async function onDownload(result) {
  try {
    const r = await api.get(`/hr/resume-pool/${result.id}/resume`, { responseType: 'blob' })
    const url = window.URL.createObjectURL(new Blob([r.data]))
    const a = document.createElement('a')
    a.href = url
    a.download = result.filename || 'resume'
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.URL.revokeObjectURL(url)
  } catch {
    alert('Could not download this resume.')
  }
}

const EMPTY_FILTERS = {
  search: '', college: '', year_of_passing: '', job_role: '', min_score: '',
  outcome: '', stage: '', source: '', sort: '', uploaded_from: '', uploaded_to: '',
}

const SORTS = [
  { value: '', label: 'Best match' },
  { value: 'recent', label: 'Most recent' },
  { value: 'contact', label: 'Least recently contacted' },
  { value: 'name', label: 'Name A–Z' },
]

const TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'candidates', label: 'Candidates' },
  { value: 'analytics', label: 'Analytics' },
  { value: 'settings', label: 'Settings' },
]

// HR's manual lane. Ordered as a funnel, and the colours match the pipeline chart so
// a stage looks the same wherever it appears.
const STAGES = [
  { value: 'new', label: 'New' },
  { value: 'ongoing', label: 'Ongoing' },
  { value: 'archived', label: 'Archived' },
]

const stageLabelOf = (s) => (STAGES.find(x => x.value === s) || STAGES[0]).label

// Records are tall — spine, facts, verdict, history — so a page of 25 is about two
// screens. The server caps `limit` at 200 regardless.
const PAGE_SIZE = 25

// What HR is browsing for. Order matches the server's precedence: the bucket a person
// falls into is the best thing that ever happened to them here.
const OUTCOME_TABS = [
  { value: '', label: 'Everyone' },
  { value: 'in_process', label: 'In process' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'hired', label: 'Hired' },
  { value: 'never_applied', label: 'Never applied' },
]

// Stage strings are bare VARCHARs written by a dozen code paths, so anything not
// listed here falls through to prettify() rather than rendering as `technical_interview`.
const STAGE_LABEL = {
  registered: 'Registered', applied: 'Applied', ats_screening: 'ATS screening',
  hr_review: 'HR review', screening: 'Screening', shortlisted: 'Shortlisted',
  test_submitted: 'Test submitted', test_completed: 'Test completed',
  interview: 'Interview', interview_scheduled: 'Interview scheduled',
  technical_interview: 'Technical interview', interview_completed: 'Interview done',
  offer: 'Offer', offer_sent: 'Offer sent', offer_rejected: 'Declined offer',
  hired: 'Hired', rejected: 'Rejected',
}

const prettify = (s) =>
  (s || '').replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase())

/**
 * Always give React a string.
 *
 * FastAPI returns `detail` as a plain string for HTTPException but as an ARRAY OF
 * OBJECTS for a 422 validation error. Putting that array straight into state and
 * rendering it throws "Objects are not valid as a React child", which takes down the
 * whole page behind the error boundary — an unreadable date filter should not be able
 * to do that.
 */
const errText = (e, fallback) => {
  const status = e?.response?.status
  const detail = e?.response?.data?.detail

  let msg = null
  if (typeof detail === 'string' && detail) msg = detail
  else if (Array.isArray(detail) && detail.length) {
    const parts = detail.map(d => (typeof d === 'string' ? d : d?.msg)).filter(Boolean)
    if (parts.length) msg = parts.join('; ')
  }

  if (!status) return `${fallback} The server did not respond — check that the API is running.`
  // The API deliberately hides 500 details from clients (main.py returns a bare
  // "Something went wrong"), so say where to find the real cause instead of
  // repeating a message nobody can act on.
  if (status >= 500) return `${fallback} The server hit an error (${status}) — the cause is in the backend log.`
  if (status === 401 || status === 403) return 'Your session has expired. Sign in again.'
  if (status === 404) return `${fallback} That endpoint is missing (404) — the API may be running older code.`
  return msg || `${fallback} (HTTP ${status})`
}

const stageLabel = (s) => STAGE_LABEL[s] || prettify(s)

// Which visual family a stage belongs to. Rejection is deliberately NOT an error
// colour — a rejected person is the whole point of a talent pool.
const stageTone = (s) =>
  s === 'hired' ? 'hired'
    : (s === 'offer' || s === 'offer_sent') ? 'offer'
      : (s === 'rejected' || s === 'offer_rejected') ? 'closed'
        : 'live'

export default function ResumeMatcher() {
  const inputRef = useRef()
  const attachRef = useRef()          // per-profile attachment picker
  const attachTarget = useRef(null)
  const dragDepth = useRef(0)
  const [tab, setTab] = useState('overview')
  const [metrics, setMetrics] = useState(null)
  const [metricsLoading, setMetricsLoading] = useState(true)
  const [editing, setEditing] = useState(null)   // entry being edited, or {} to add
  const [scoring, setScoring] = useState(false)
  const [scoreProgress, setScoreProgress] = useState('')
  const stopScoring = useRef(false)
  const [files, setFiles] = useState([])       // File[] queued for matching
  const [loading, setLoading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')

  const [entries, setEntries] = useState([])   // stored pool
  const [total, setTotal] = useState(0)
  const [activeJobCount, setActiveJobCount] = useState(0)
  const [activeJobs, setActiveJobs] = useState([])
  const [pending, setPending] = useState(0)     // people not yet in the pool
  const [importing, setImporting] = useState(false)
  const [listLoading, setListLoading] = useState(true)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [page, setPage] = useState(0)
  const [showMoreFilters, setShowMoreFilters] = useState(false)
  const [busyId, setBusyId] = useState(null)
  const [moveTarget, setMoveTarget] = useState(null)
  const [notice, setNotice] = useState('')

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const loadPool = useCallback(async (f = filters, p = page) => {
    setListLoading(true)
    try {
      const params = { skip: p * PAGE_SIZE, limit: PAGE_SIZE }
      Object.entries(f).forEach(([k, v]) => { if (v !== '' && v != null) params[k] = v })
      // Unscored people sink under every scored one, so the buckets that are mostly
      // unscored default to "most recent" — unless HR has picked a sort explicitly.
      if (!f.sort && (f.outcome === 'never_applied' || f.outcome === 'rejected')) {
        params.sort = 'recent'
      }
      const r = await api.get('/hr/resume-pool', { params })
      setEntries(r.data.results || [])
      setTotal(r.data.total || 0)
      setActiveJobCount(r.data.active_job_count || 0)
      setActiveJobs(r.data.active_jobs || [])
      setPending((r.data.registered_pending || 0) + (r.data.orphan_pending || 0))
      // Clear on success. Without this, one transient failure — the API restarting, a
      // token expiring on first paint — leaves a red banner sitting permanently over a
      // page that has since loaded perfectly well.
      setError('')
    } catch (e) {
      setError(errText(e, 'Could not load the talent pool.'))
    } finally {
      setListLoading(false)
    }
  }, [filters, page])

  // Re-query on filter or page change. Typing is debounced; paging is not, because a
  // 300ms lag on a Next click reads as a broken button.
  const prevFilters = useRef(filters)
  useEffect(() => {
    const filtersChanged = prevFilters.current !== filters
    prevFilters.current = filters
    const t = setTimeout(() => loadPool(filters, page), filtersChanged ? 300 : 0)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, page])

  // Any filter change invalidates the current page — page 4 of the old result set is
  // meaningless in the new one.
  const setFilter = (key, value) => {
    setFilters(f => ({ ...f, [key]: value }))
    setPage(0)
  }
  const clearFilters = () => { setFilters(EMPTY_FILTERS); setPage(0) }
  const hasFilters = Object.values(filters).some(v => v !== '')

  const addFiles = (fileList) => {
    const incoming = Array.from(fileList || []).filter(f => {
      const ext = f.name.split('.').pop()?.toLowerCase()
      return ALLOWED.includes(ext)
    })
    if (incoming.length === 0) return
    // De-dupe by name+size so re-dropping the same file doesn't double it.
    setFiles(prev => {
      const seen = new Set(prev.map(f => f.name + f.size))
      return [...prev, ...incoming.filter(f => !seen.has(f.name + f.size))]
    })
  }

  // Dragging files anywhere over the page is a valid drop. dragenter/dragleave
  // fire per child element, so depth-count instead of toggling on each event.
  const isFileDrag = (e) => Array.from(e.dataTransfer?.types || []).includes('Files')

  const onDragEnter = (e) => {
    if (!isFileDrag(e)) return
    dragDepth.current += 1
    setDragging(true)
  }
  const onDragLeave = (e) => {
    if (!isFileDrag(e)) return
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragging(false)
  }
  const onDrop = (e) => {
    e.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    addFiles(e.dataTransfer.files)
  }

  const removeFile = (idx) => setFiles(prev => prev.filter((_, i) => i !== idx))

  const runMatch = async () => {
    if (files.length === 0) return
    setLoading(true)
    setError('')
    setWarning('')
    setNotice('')
    try {
      const fd = new FormData()
      for (const f of files) fd.append('resumes', f)
      const r = await api.post('/hr/resume-match', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const failed = (r.data.results || []).filter(x => x.error)
      if (r.data.message) setWarning(r.data.message)
      if (r.data.warning) setWarning(r.data.warning)
      if (failed.length) {
        setError(`${failed.length} resume${failed.length > 1 ? 's' : ''} could not be read: ` +
                 failed.map(f => `${f.filename} (${f.error})`).join('; '))
      }
      const stored = (r.data.results || []).filter(x => x.id).length
      if (stored) setNotice(`${stored} resume${stored > 1 ? 's' : ''} saved to the pool.`)
      setFiles([])
      if (inputRef.current) inputRef.current.value = ''
      // Clear filters and return to page one so fresh uploads are guaranteed visible.
      setPage(0)
      if (hasFilters) setFilters(EMPTY_FILTERS)
      else await loadPool(EMPTY_FILTERS, 0)
    } catch (e) {
      setError(errText(e, 'Could not match resumes. Please try again.'))
    } finally {
      setLoading(false)
    }
  }

  // Imports in batches — scoring every person against every open role in one request
  // would time out. The button stays live while any remain.
  const runImport = async () => {
    setImporting(true)
    setError('')
    setNotice('')
    try {
      const r = await api.post('/hr/resume-pool/import-registered', { limit: 50 })
      setNotice(r.data.message)
      await loadPool(filters, page)
    } catch (e) {
      setError(errText(e, 'Could not import people into the pool.'))
    } finally {
      setImporting(false)
    }
  }

  const loadMetrics = useCallback(async () => {
    setMetricsLoading(true)
    try {
      const r = await api.get('/hr/talent-pool/metrics')
      setMetrics(r.data)
    } catch (e) {
      setError(errText(e, 'Could not load the dashboard metrics.'))
    } finally {
      setMetricsLoading(false)
    }
  }, [])

  useEffect(() => { loadMetrics() }, [loadMetrics])

  /** Replace one row in place and refresh the counts the change affected. */
  const applyUpdate = (data) => {
    setEntries(prev => prev.map(e => (e.id === data.id ? data : e)))
    loadMetrics()
  }

  const handleStage = async (entry, stage) => {
    if (stage === entry.stage) return
    setBusyId(entry.id)
    setError('')
    try {
      const r = await api.patch(`/hr/resume-pool/${entry.id}/stage`, { stage })
      applyUpdate(r.data)
      setNotice(`${r.data.full_name || 'Candidate'} moved to ${stageLabelOf(stage)}.`)
    } catch (e) {
      setError(errText(e, 'Could not change the stage.'))
    } finally {
      setBusyId(null)
    }
  }

  const handleContact = async (entry) => {
    setBusyId(entry.id)
    setError('')
    try {
      const r = await api.patch(`/hr/resume-pool/${entry.id}`, { mark_contacted: true })
      applyUpdate(r.data)
      setNotice(`Logged contact with ${r.data.full_name || 'this candidate'}.`)
    } catch (e) {
      setError(errText(e, 'Could not log the contact.'))
    } finally {
      setBusyId(null)
    }
  }

  /**
   * Rank everyone who has never been scored, a few at a time.
   *
   * Batched because each person costs one model pass per open role and this runs inside
   * the request — there is no queue to hand it to. The loop trusts `remaining`, which
   * the server computes from scoreable rows only, and bails if a batch ranks nobody so
   * a persistent failure cannot spin forever.
   */
  const runScoring = async () => {
    setScoring(true)
    stopScoring.current = false
    setError('')
    setNotice('')

    let done = 0
    try {
      const total = metrics?.unscored || 0
      setScoreProgress(`Ranking… 0 of ${total}`)

      for (;;) {
        if (stopScoring.current) break
        const r = await api.post('/hr/talent-pool/score-backlog', { limit: 3 })
        const { scored, remaining } = r.data
        done += scored
        setScoreProgress(`Ranked ${done} of ${total}`)

        // No progress means every remaining row is failing, not that we are finished.
        if (remaining === 0 || scored === 0) break
      }

      setNotice(stopScoring.current
        ? `Stopped after ranking ${done}. The rest keep their place.`
        : `Ranked ${done} ${done === 1 ? 'person' : 'people'}.`)
      await Promise.all([loadPool(filters, page), loadMetrics()])
    } catch (e) {
      setError(errText(e, 'Could not rank these candidates.'))
      loadMetrics()
    } finally {
      setScoring(false)
      setScoreProgress('')
    }
  }

  /** Save a few fields on one profile — the engine behind every inline editor. */
  const handlePatch = async (entry, patch) => {
    setBusyId(entry.id)
    setError('')
    try {
      const r = await api.patch(`/hr/resume-pool/${entry.id}`, patch)
      applyUpdate(r.data)
      setNotice(`Saved ${r.data.full_name || 'profile'}.`)
      return true
    } catch (e) {
      setError(errText(e, 'Could not save that change.'))
      return false
    } finally {
      setBusyId(null)
    }
  }

  const pickAttachments = (entry) => {
    attachTarget.current = { entry, replacing: null }
    attachRef.current?.click()
  }

  const pickReplacement = (entry, file) => {
    attachTarget.current = { entry, replacing: file }
    attachRef.current?.click()
  }

  const uploadAttachments = async (fileList) => {
    const target = attachTarget.current
    const chosen = Array.from(fileList || [])
    if (!target?.entry || chosen.length === 0) return
    const { entry, replacing } = target

    setBusyId(entry.id)
    setError('')
    setWarning('')
    try {
      const fd = new FormData()
      let r
      if (replacing) {
        fd.append('file', chosen[0])
        r = await api.put(`/hr/resume-pool/${entry.id}/attachments/${replacing.id}`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      } else {
        chosen.forEach(f => fd.append('files', f))
        r = await api.post(`/hr/resume-pool/${entry.id}/attachments`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        })
      }
      applyUpdate(r.data.entry)
      setNotice(r.data.message)
      // Anything the server refused is named, so a dropped file is never silent.
      if (r.data.rejected?.length) setWarning(r.data.rejected.join('; '))
    } catch (e) {
      setError(errText(e, 'Could not attach those files.'))
    } finally {
      setBusyId(null)
      attachTarget.current = null
      if (attachRef.current) attachRef.current.value = ''
    }
  }

  const handleRemoveFile = async (entry, file) => {
    if (!window.confirm(`Remove ${file.filename} from this profile?`)) return
    setBusyId(entry.id)
    setError('')
    try {
      await api.delete(`/hr/resume-pool/${entry.id}/attachments/${file.id}`)
      await loadPool(filters, page)
      loadMetrics()
    } catch (e) {
      setError(errText(e, 'Could not remove that file.'))
    } finally {
      setBusyId(null)
    }
  }

  const handleSaved = async (data) => {
    const wasAdding = !editing?.id
    setEditing(null)
    setNotice(wasAdding
      ? `${data.full_name} added to the pool.`
      : `${data.full_name || 'Profile'} updated.`)
    if (wasAdding) {
      setTab('candidates')
      setPage(0)
      await loadPool(filters, 0)
    } else {
      applyUpdate(data)
    }
    loadMetrics()
  }

  const handleRematch = async (entry) => {
    setBusyId(entry.id)
    setError('')
    try {
      const r = await api.post(`/hr/resume-pool/${entry.id}/rematch`)
      setEntries(prev => prev.map(e => (e.id === entry.id ? r.data : e)))
      setNotice(`Re-matched against ${activeJobCount} open role${activeJobCount === 1 ? '' : 's'}.`)
    } catch (e) {
      setError(errText(e, 'Could not re-match this resume.'))
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (entry) => {
    // Only files the matcher itself stored are deleted with the row. A registered
    // candidate's resume belongs to their profile and their past applications.
    const who = entry.full_name || entry.filename
    const warning = entry.source === 'upload'
      ? `Remove ${who} from the talent pool? The resume file you uploaded is deleted too.`
      : `Remove ${who} from the talent pool? Their account, profile and applications are `
        + `untouched — and they'll reappear here if they apply again, since this is a `
        + `de-duplication tool rather than a blocklist.`
    if (!window.confirm(warning)) return
    setBusyId(entry.id)
    setError('')
    try {
      await api.delete(`/hr/resume-pool/${entry.id}`)
      setEntries(prev => prev.filter(e => e.id !== entry.id))
      setTotal(t => Math.max(0, t - 1))
      // Removing the only row on a page would otherwise strand HR on an empty one.
      if (entries.length === 1 && page > 0) setPage(p => p - 1)
    } catch (e) {
      setError(errText(e, 'Could not remove this resume.'))
    } finally {
      setBusyId(null)
    }
  }

  const handleMoved = async (data) => {
    setMoveTarget(null)
    setNotice(data.message || 'Candidate added to the ATS.')
    // Reload rather than patching locally: the move created a new application, and
    // both the history list and the outcome bucket are derived server-side.
    await loadPool(filters, page)
  }

  const pickFiles = () => inputRef.current?.click()
  const showIntakePanel = !listLoading && entries.length === 0 && !hasFilters

  return (
    <div
      className="rm-page"
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={(e) => { if (isFileDrag(e)) e.preventDefault() }}
      onDrop={onDrop}
    >
      <input
        ref={inputRef} type="file" multiple accept={ACCEPT} style={{ display: 'none' }}
        onChange={(e) => addFiles(e.target.files)}
      />
      {/* Separate picker: these files attach to one profile rather than joining the
          match queue, so they must not share the queue's input. */}
      <input
        ref={attachRef} type="file" multiple accept={ATTACH_ACCEPT} style={{ display: 'none' }}
        onChange={(e) => uploadAttachments(e.target.files)}
      />

      {/* ── Masthead ───────────────────────────────────────────── */}
      <header className="rm-masthead">
        <div>
          <h1 className="rm-masthead__title">Talent Pool</h1>
          <p className="rm-masthead__sub">
            Everyone this company has seen — people who applied, people you rejected, and
            resumes you uploaded. Search them, see how far each one got, and put anyone
            back in front of an open role.
          </p>
        </div>
        <div className="rm-masthead__actions">
          {pending > 0 && (
            <button className="btn btn-secondary btn-sm" onClick={runImport} disabled={importing}>
              <Users size={15} />
              {importing ? 'Importing…' : `Import ${pending} more`}
            </button>
          )}
          <button className="btn btn-secondary btn-sm" onClick={() => setEditing({})}>
            <UserPlus size={15} /> Add candidate
          </button>
          <button className="btn btn-primary btn-sm" onClick={pickFiles}>
            <Upload size={15} /> Add resumes
          </button>
        </div>
      </header>

      <nav className="tp-tabs" role="tablist" aria-label="Talent pool sections">
        {TABS.map(t => (
          <button
            key={t.value}
            role="tab"
            aria-selected={tab === t.value}
            className={`tp-tab${tab === t.value ? ' tp-tab--on' : ''}`}
            onClick={() => setTab(t.value)}>
            {t.label}
            {t.value === 'candidates' && total > 0 && (
              <span className="tp-tab__count">{total}</span>
            )}
          </button>
        ))}
      </nav>

      {tab === 'candidates' && (
        <div className="rm-ledgerline">
          <span>
            {listLoading
              ? 'Loading people'
              : <>
                  <b>{total}</b> {total === 1 ? 'person' : 'people'} · ranked against{' '}
                  <b>{activeJobCount}</b> open role{activeJobCount === 1 ? '' : 's'}
                </>}
          </span>
          <span>Drop files anywhere on this page</span>
        </div>
      )}

      {/* ── Queued files ───────────────────────────────────────── */}
      {files.length > 0 && (
        <div className="rm-queue" style={{ marginTop: 16 }}>
          <div className="rm-queue__head">
            <span className="rm-queue__count">
              {files.length} resume{files.length > 1 ? 's' : ''} ready
            </span>
            <button className="btn btn-primary btn-sm" onClick={runMatch} disabled={loading}>
              {loading ? 'Matching…' : 'Match to jobs'}
            </button>
          </div>
          <div className="rm-chips">
            {files.map((f, i) => (
              <span key={i} className="rm-chip">
                <span className="rm-chip__name">{f.name}</span>
                <button onClick={() => removeFile(i)} disabled={loading}
                        aria-label={`Remove ${f.name}`}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── Messages ───────────────────────────────────────────── */}
      {notice && (
        <div className="rm-alert rm-alert--ok" style={{ marginTop: 16 }}>
          <CheckCircle2 size={15} /> {notice}
        </div>
      )}
      {warning && (
        <div className="rm-alert rm-alert--warn" style={{ marginTop: 16 }}>
          <AlertCircle size={15} /> {warning}
        </div>
      )}
      {error && (
        <div className="rm-alert rm-alert--err" style={{ marginTop: 16 }}>
          <AlertCircle size={15} /> {error}
        </div>
      )}

      {tab === 'overview' && (
        <OverviewPanel
          metrics={metrics}
          loading={metricsLoading}
          onOpenStage={(stage) => { setTab('candidates'); setFilter('stage', stage) }}
          scoring={scoring} progress={scoreProgress}
          onScore={runScoring} onStop={() => { stopScoring.current = true }}
        />
      )}
      {tab === 'analytics' && <AnalyticsPanel metrics={metrics} loading={metricsLoading} />}
      {tab === 'settings' && (
        <SettingsPanel
          metrics={metrics} pending={pending} importing={importing}
          onImport={runImport} onRefresh={loadMetrics} busy={metricsLoading}
          scoring={scoring} progress={scoreProgress}
          onScore={runScoring} onStop={() => { stopScoring.current = true }}
        />
      )}

      {/* ── Filters ────────────────────────────────────────────── */}
      {tab === 'candidates' && !showIntakePanel && (
        <div className="rm-filters" style={{ marginTop: 20 }}>
          {/* Outcome is the primary axis of this screen, so it gets a rail rather than
              being buried in a select alongside the incidental filters. */}
          <div className="rm-outcomes" role="group" aria-label="Filter by outcome">
            {OUTCOME_TABS.map(t => (
              <button
                key={t.value || 'all'}
                className={`rm-outcome${filters.outcome === t.value ? ' rm-outcome--on' : ''}`}
                aria-pressed={filters.outcome === t.value}
                onClick={() => setFilter('outcome', t.value)}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="rm-filters__row">
            <div className="rm-search">
              <Search size={15} />
              <input className="input"
                     placeholder="Search name, email, college, role or skills"
                     value={filters.search} onChange={e => setFilter('search', e.target.value)} />
            </div>
            <select className="select" style={{ flex: '0 1 145px' }} aria-label="Stage"
                    value={filters.stage} onChange={e => setFilter('stage', e.target.value)}>
              <option value="">All stages</option>
              {STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <select className="select" style={{ flex: '0 1 165px' }}
                    value={filters.source} onChange={e => setFilter('source', e.target.value)}>
              <option value="">All sources</option>
              <option value="upload">Uploaded by HR</option>
              <option value="registered">Registered on portal</option>
              <option value="applied">Added by HR</option>
            </select>
            <select className="select" style={{ flex: '0 1 150px' }} aria-label="Sort by"
                    value={filters.sort} onChange={e => setFilter('sort', e.target.value)}>
              {SORTS.map(s => (
                <option key={s.value || 'best'} value={s.value}>Sort: {s.label}</option>
              ))}
            </select>
            <button className="rm-act" onClick={() => setShowMoreFilters(s => !s)}
                    aria-expanded={showMoreFilters}>
              <SlidersHorizontal size={14} /> More filters
            </button>
            {hasFilters && (
              <button className="btn btn-secondary btn-sm" onClick={clearFilters}>
                <X size={14} /> Clear
              </button>
            )}
          </div>

          {showMoreFilters && (
            <div className="rm-filters__more">
              <input className="input" style={{ flex: '0 1 170px' }} placeholder="College"
                     value={filters.college} onChange={e => setFilter('college', e.target.value)} />
              <input className="input" style={{ flex: '0 1 140px' }} placeholder="Year of passing" type="number"
                     value={filters.year_of_passing} onChange={e => setFilter('year_of_passing', e.target.value)} />
              <input className="input" style={{ flex: '0 1 150px' }} placeholder="Job role"
                     value={filters.job_role} onChange={e => setFilter('job_role', e.target.value)} />
              <input className="input" style={{ flex: '0 1 120px' }} placeholder="Min score" type="number" min="0" max="100"
                     value={filters.min_score} onChange={e => setFilter('min_score', e.target.value)} />
              <span className="rm-filters__label">Added</span>
              <input className="input" style={{ flex: '0 1 150px' }} type="date"
                     value={filters.uploaded_from} onChange={e => setFilter('uploaded_from', e.target.value)} />
              <span className="rm-filters__label">to</span>
              <input className="input" style={{ flex: '0 1 150px' }} type="date"
                     value={filters.uploaded_to} onChange={e => setFilter('uploaded_to', e.target.value)} />
            </div>
          )}
        </div>
      )}

      {/* ── Records ────────────────────────────────────────────── */}
      {/* Genuinely unmounted off-tab rather than hidden with display:none — hidden
          cards still build their DOM, and their stage selects would sit in the
          accessibility tree of a tab that isn't showing them. */}
      {tab === 'candidates' && (
      <div style={{ marginTop: 20 }}>
        {listLoading && entries.length === 0 && (
          <div className="rm-records">
            <div className="rm-skeleton" />
            <div className="rm-skeleton" />
            <div className="rm-skeleton" />
          </div>
        )}

        {!listLoading && entries.length === 0 && (
          showIntakePanel ? (
            <div className="rm-intake">
              <Upload size={26} style={{ color: 'var(--text-tertiary)', marginBottom: 10 }} />
              <div className="rm-intake__title">
                {activeJobCount === 0 ? 'No open roles to rank against' : 'The pool is empty'}
              </div>
              <div className="rm-intake__hint">
                {activeJobCount === 0
                  ? 'Publish a job posting first, then add resumes and re-match to rank them.'
                  : 'Drop resumes anywhere on this page, or choose files. PDF, DOC or DOCX.'}
              </div>
              <div className="rm-intake__actions">
                <button className="btn btn-primary btn-sm" onClick={pickFiles}>
                  <FileSearch size={15} /> Choose files
                </button>
                {pending > 0 && (
                  <button className="btn btn-secondary btn-sm" onClick={runImport} disabled={importing}>
                    <Users size={15} />
                    {importing ? 'Importing…' : `Import ${pending} people`}
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="rm-empty">
              <div className="rm-empty__title">Nobody matches these filters</div>
              <div className="rm-empty__body">
                Widen the search, or clear the filters to see the whole pool.
              </div>
              <button className="btn btn-secondary btn-sm" onClick={clearFilters}>
                <X size={14} /> Clear filters
              </button>
            </div>
          )
        )}

        {entries.length > 0 && (
          <div className="rm-records">
            {entries.map((res, i) => (
              <Record
                key={res.id}
                result={res}
                index={i}
                activeJobCount={activeJobCount}
                busy={busyId === res.id}
                onMove={setMoveTarget}
                onRematch={handleRematch}
                onDelete={handleDelete}
                onStage={handleStage}
                onEdit={setEditing}
                onContact={handleContact}
                onUpload={pickAttachments}
                onRemoveFile={handleRemoveFile}
                onPatch={handlePatch}
                onViewFile={onViewAttachment}
                onReplaceFile={pickReplacement}
              />
            ))}
          </div>
        )}

        {/* Without this the list silently stops at one page while the ledger line
            above reports the true total — rows past it would be unreachable. */}
        {total > PAGE_SIZE && (
          <div className="rm-pagefoot">
            <button className="rm-act" disabled={page === 0 || listLoading}
                    onClick={() => setPage(p => Math.max(0, p - 1))}>
              <ChevronLeft size={14} /> Previous
            </button>
            <span className="rm-pagefoot__pos">
              Page <b>{page + 1}</b> of <b>{pageCount}</b> · showing{' '}
              <b>{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)}</b> of <b>{total}</b>
            </span>
            <button className="rm-act" disabled={page + 1 >= pageCount || listLoading}
                    onClick={() => setPage(p => p + 1)}>
              Next <ChevronRight size={14} />
            </button>
          </div>
        )}
      </div>
      )}

      {/* ── Drop veil ──────────────────────────────────────────── */}
      {dragging && (
        <div className="rm-dropveil">
          <div className="rm-dropveil__frame">
            <Upload size={30} />
            <strong>Drop to add resumes</strong>
            <span>PDF · DOC · DOCX</span>
          </div>
        </div>
      )}

      {editing && (
        <CandidateForm
          entry={editing}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      )}

      {moveTarget && (
        <MoveToATSModal
          entry={moveTarget}
          activeJobs={activeJobs}
          onClose={() => setMoveTarget(null)}
          onMoved={handleMoved}
        />
      )}
    </div>
  )
}
