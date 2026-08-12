import { useState, useMemo } from 'react'
import { X, Sparkles, CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react'
import api from '../../api/client'

const TEAL = '#4ecdb5'
const TEAL_LIGHT = '#e9f9f4'
const TEAL_MID = '#c3ede6'

// A low-confidence extraction starts unchecked so the candidate opts in deliberately.
const HIGH_CONF = 0.7

/**
 * Review/confirm screen for resume auto-fill. The candidate edits and toggles
 * each extracted item; nothing is written until they click "Add to profile".
 */
export default function ResumeImportReview({ data, onClose, onApplied }) {
  const [skills, setSkills] = useState(
    () => (data.skills || []).map(s => ({ value: s, include: true }))
  )
  const [work, setWork] = useState(
    () => (data.work_experience || []).map(w => ({
      title: w.title || '', company: w.company || '',
      include: (w.confidence ?? 0) >= HIGH_CONF,
    }))
  )
  const [education, setEducation] = useState(
    () => (data.education || []).map(e => ({
      degree: e.degree || '', institution: '',
      include: (e.confidence ?? 0) >= HIGH_CONF,
    }))
  )
  const [certs, setCerts] = useState(
    () => (data.certifications || []).map(c => ({ value: c, include: true }))
  )
  // Projects are the least reliable extraction, so they default to OFF —
  // the candidate opts in deliberately after checking each title.
  const [projects, setProjects] = useState(
    () => (data.projects || []).map(p => ({
      value: p.title || p.name || '',
      description: p.description || '',
      technologies_used: p.technologies_used || p.tech_stack || '',
      role: p.role || '',
      project_type: p.project_type || 'Personal',
      start_date: p.start_date || null,
      end_date: p.end_date || null,
      ongoing: p.ongoing || false,
      project_url: p.project_url || null,
      repository_url: p.repository_url || null,
      include: false
    }))
  )
  const expYears = data.total_experience_years
  const [includeExp, setIncludeExp] = useState(!!expYears)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const selectedCount = useMemo(() => (
    skills.filter(s => s.include).length +
    work.filter(w => w.include).length +
    education.filter(e => e.include).length +
    certs.filter(c => c.include).length +
    projects.filter(p => p.include).length
  ), [skills, work, education, certs, projects])

  const nothingExtracted = (
    !skills.length && !work.length && !education.length && !certs.length && !projects.length
  )

  const handleApply = async () => {
    setSaving(true); setError('')
    const payload = {
      skills: skills.filter(s => s.include && s.value.trim()).map(s => s.value.trim()),
      work_experience: work.filter(w => w.include && (w.title.trim() || w.company.trim()))
        .map(w => ({ title: w.title.trim(), company: w.company.trim() })),
      education: education.filter(e => e.include && (e.degree.trim() || e.institution.trim()))
        .map(e => ({ degree: e.degree.trim(), institution: e.institution.trim() })),
      certifications: certs.filter(c => c.include && c.value.trim()).map(c => c.value.trim()),
      projects: projects.filter(p => p.include && p.value.trim()).map(p => ({
        name: p.value.trim(),
        title: p.value.trim(),
        description: p.description || null,
        technologies_used: p.technologies_used || null,
        role: p.role || null,
        project_type: p.project_type || 'Personal',
        start_date: p.start_date || null,
        end_date: p.end_date || null,
        ongoing: p.ongoing || false,
        project_url: p.project_url || null,
        repository_url: p.repository_url || null,
      })),
      total_experience_years: includeExp ? expYears : null,
    }
    try {
      const r = await api.post('/candidate/profile/import/apply', payload)
      onApplied(r.data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Could not add to profile. Please try again.')
      setSaving(false)
    }
  }

  return (
    <div className="rir-overlay" onClick={onClose}>
      <div className="rir-modal" onClick={e => e.stopPropagation()}>
        <button className="rir-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        <div style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <Sparkles size={18} style={{ color: TEAL }} />
            <h2 style={{ fontSize: '1.15rem', fontWeight: 800, margin: 0 }}>Review what we found</h2>
          </div>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-tertiary)', margin: '0 0 4px' }}>
            Uncheck anything wrong and edit the rest. Only checked items are added — your existing
            profile data is never overwritten.
          </p>
          {data.confidence_score < HIGH_CONF && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#9a3412',
              background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 8, padding: '8px 12px', margin: '8px 0',
            }}>
              <AlertTriangle size={13} style={{ flexShrink: 0 }} />
              This resume was hard to read, so some items may be off — please review carefully.
            </div>
          )}

          {nothingExtracted ? (
            <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>
              We couldn't pull structured details from this resume. Try a different file or fill your
              profile manually.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18, marginTop: 14 }}>
              {/* Skills */}
              {skills.length > 0 && (
                <Group
                  title="Skills"
                  count={skills.filter(s => s.include).length}
                  action={
                    <button
                      type="button"
                      style={{
                        background: 'none', border: 'none', padding: 0,
                        fontSize: '0.73rem', fontWeight: 600, color: TEAL,
                        cursor: 'pointer', textTransform: 'none', letterSpacing: 'normal'
                      }}
                      onClick={() => {
                        const allSelected = skills.every(s => s.include)
                        setSkills(prev => prev.map(x => ({ ...x, include: !allSelected })))
                      }}
                    >
                      {skills.every(s => s.include) ? 'Deselect all' : 'Select all'}
                    </button>
                  }
                >
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {skills.map((s, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => setSkills(prev => prev.map((x, j) => j === i ? { ...x, include: !x.include } : x))}
                        className={s.include ? 'rir-chip rir-chip-on' : 'rir-chip'}
                      >
                        {s.include && <CheckCircle2 size={12} />} {s.value}
                      </button>
                    ))}
                  </div>
                </Group>
              )}

              {/* Total experience */}
              {expYears ? (
                <Group title="Total experience">
                  <label className="rir-row">
                    <input type="checkbox" checked={includeExp} onChange={e => setIncludeExp(e.target.checked)} />
                    <span style={{ fontSize: '0.88rem' }}>Set total experience to <strong>{expYears} years</strong> <span style={{ color: 'var(--text-tertiary)' }}>(only if empty)</span></span>
                  </label>
                </Group>
              ) : null}

              {/* Work experience */}
              {work.length > 0 && (
                <Group
                  title="Work experience"
                  count={work.filter(w => w.include).length}
                  action={
                    <button
                      type="button"
                      style={{
                        background: 'none', border: 'none', padding: 0,
                        fontSize: '0.73rem', fontWeight: 600, color: TEAL,
                        cursor: 'pointer', textTransform: 'none', letterSpacing: 'normal'
                      }}
                      onClick={() => {
                        const allSelected = work.every(w => w.include)
                        setWork(prev => prev.map(x => ({ ...x, include: !allSelected })))
                      }}
                    >
                      {work.every(w => w.include) ? 'Deselect all' : 'Select all'}
                    </button>
                  }
                >
                  {work.map((w, i) => (
                    <div key={i} className="rir-row">
                      <input type="checkbox" checked={w.include}
                        onChange={e => setWork(prev => prev.map((x, j) => j === i ? { ...x, include: e.target.checked } : x))} />
                      <input className="rir-input" placeholder="Job title" value={w.title}
                        onChange={e => setWork(prev => prev.map((x, j) => j === i ? { ...x, title: e.target.value } : x))} />
                      <input className="rir-input" placeholder="Company" value={w.company}
                        onChange={e => setWork(prev => prev.map((x, j) => j === i ? { ...x, company: e.target.value } : x))} />
                    </div>
                  ))}
                </Group>
              )}

              {/* Education */}
              {education.length > 0 && (
                <Group
                  title="Education"
                  count={education.filter(e => e.include).length}
                  action={
                    <button
                      type="button"
                      style={{
                        background: 'none', border: 'none', padding: 0,
                        fontSize: '0.73rem', fontWeight: 600, color: TEAL,
                        cursor: 'pointer', textTransform: 'none', letterSpacing: 'normal'
                      }}
                      onClick={() => {
                        const allSelected = education.every(e => e.include)
                        setEducation(prev => prev.map(x => ({ ...x, include: !allSelected })))
                      }}
                    >
                      {education.every(e => e.include) ? 'Deselect all' : 'Select all'}
                    </button>
                  }
                >
                  {education.map((e, i) => (
                    <div key={i} className="rir-row">
                      <input type="checkbox" checked={e.include}
                        onChange={ev => setEducation(prev => prev.map((x, j) => j === i ? { ...x, include: ev.target.checked } : x))} />
                      <input className="rir-input" placeholder="Degree" value={e.degree}
                        onChange={ev => setEducation(prev => prev.map((x, j) => j === i ? { ...x, degree: ev.target.value } : x))} />
                      <input className="rir-input" placeholder="Institution" value={e.institution}
                        onChange={ev => setEducation(prev => prev.map((x, j) => j === i ? { ...x, institution: ev.target.value } : x))} />
                    </div>
                  ))}
                </Group>
              )}

              {/* Certifications */}
              {certs.length > 0 && (
                <Group
                  title="Certifications"
                  count={certs.filter(c => c.include).length}
                  action={
                    <button
                      type="button"
                      style={{
                        background: 'none', border: 'none', padding: 0,
                        fontSize: '0.73rem', fontWeight: 600, color: TEAL,
                        cursor: 'pointer', textTransform: 'none', letterSpacing: 'normal'
                      }}
                      onClick={() => {
                        const allSelected = certs.every(c => c.include)
                        setCerts(prev => prev.map(x => ({ ...x, include: !allSelected })))
                      }}
                    >
                      {certs.every(c => c.include) ? 'Deselect all' : 'Select all'}
                    </button>
                  }
                >
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {certs.map((c, i) => (
                      <button key={i} type="button"
                        onClick={() => setCerts(prev => prev.map((x, j) => j === i ? { ...x, include: !x.include } : x))}
                        className={c.include ? 'rir-chip rir-chip-on' : 'rir-chip'}>
                        {c.include && <CheckCircle2 size={12} />} {c.value}
                      </button>
                    ))}
                  </div>
                </Group>
              )}

              {/* Projects */}
              {projects.length > 0 && (
                <Group
                  title="Projects"
                  count={projects.filter(p => p.include).length}
                  action={
                    <button
                      type="button"
                      style={{
                        background: 'none', border: 'none', padding: 0,
                        fontSize: '0.73rem', fontWeight: 600, color: TEAL,
                        cursor: 'pointer', textTransform: 'none', letterSpacing: 'normal'
                      }}
                      onClick={() => {
                        const allSelected = projects.every(p => p.include)
                        setProjects(prev => prev.map(x => ({ ...x, include: !allSelected })))
                      }}
                    >
                      {projects.every(p => p.include) ? 'Deselect all' : 'Select all'}
                    </button>
                  }
                >
                  {projects.map((p, i) => (
                    <div key={i} className="rir-row">
                      <input type="checkbox" checked={p.include}
                        onChange={e => setProjects(prev => prev.map((x, j) => j === i ? { ...x, include: e.target.checked } : x))} />
                      <input className="rir-input" placeholder="Project name" value={p.value}
                        onChange={e => setProjects(prev => prev.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} />
                    </div>
                  ))}
                </Group>
              )}
            </div>
          )}

          {error && <div style={{ color: '#dc2626', fontSize: '0.82rem', marginTop: 12 }}>{error}</div>}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 22 }}>
            <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
            <button className="btn btn-primary" onClick={handleApply} disabled={saving || nothingExtracted || selectedCount === 0}>
              {saving ? <><Loader2 size={14} className="rir-spin" /> Adding…</> : `Add ${selectedCount} to profile`}
            </button>
          </div>
        </div>
      </div>

      <style>{`
        .rir-overlay {
          position: fixed; inset: 0; z-index: 1100;
          background: rgba(15,23,42,0.45); backdrop-filter: blur(5px);
          display: grid; place-items: center; padding: 20px;
        }
        .rir-modal {
          background: var(--bg-card, #fff); border: 1px solid var(--border-subtle);
          border-radius: 18px; width: 100%; max-width: 600px;
          max-height: 88vh; overflow-y: auto; position: relative;
          box-shadow: 0 24px 60px rgba(0,0,0,0.22);
        }
        .rir-close {
          position: absolute; top: 16px; right: 16px; width: 32px; height: 32px;
          border-radius: 8px; border: 1px solid var(--border-subtle);
          background: var(--surface-input); color: var(--text-tertiary);
          cursor: pointer; display: grid; place-items: center;
        }
        .rir-close:hover { color: var(--text-primary); background: var(--bg-hover); }
        .rir-chip {
          display: inline-flex; align-items: center; gap: 4px;
          border: 1px solid var(--border-medium); background: var(--surface-input);
          color: var(--text-tertiary); border-radius: 20px; padding: 4px 12px;
          font-size: 0.8rem; font-weight: 600; cursor: pointer; opacity: 0.6;
        }
        .rir-chip-on {
          background: ${TEAL_LIGHT}; border-color: ${TEAL_MID}; color: #0f766e; opacity: 1;
        }
        .rir-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .rir-input {
          flex: 1; min-width: 0; padding: 7px 10px; border-radius: 8px;
          border: 1px solid var(--border-subtle); background: var(--surface-input);
          font-size: 0.84rem; font-family: var(--font-body);
        }
        .rir-input:focus { outline: none; border-color: ${TEAL}; }
        .rir-spin { animation: rirspin 0.8s linear infinite; }
        @keyframes rirspin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}

function Group({ title, count, children, action }) {
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
        color: 'var(--text-tertiary)', marginBottom: 8,
      }}>
        <span>{title}{typeof count === 'number' ? ` · ${count} selected` : ''}</span>
        {action}
      </div>
      {children}
    </div>
  )
}
