import { useState, useEffect, useRef } from 'react'
import {
  User, MapPin, Briefcase, GraduationCap, Star, Award,
  FolderOpen, Settings, FileText, Link2, Camera, Upload,
  Plus, Pencil, Trash2, Check, X, ChevronDown, ChevronUp, ChevronLeft, ChevronRight,
  Globe, Linkedin, Github, Twitter, ExternalLink, BadgeCheck, Sparkles, Loader2,
} from 'lucide-react'
import api from '../../api/client'
import ResumeImportReview from './ResumeImportReview'

// ── Constants ─────────────────────────────────────────────────────────────────

const SECTIONS = [
  { id: 'personal',     label: 'Personal Info',             icon: User },
  { id: 'documents',    label: 'Documents & Links',          icon: FileText },
  { id: 'location',     label: 'Location',                   icon: MapPin },
  { id: 'experience',   label: 'Work Experience & Summary',  icon: Briefcase },
  { id: 'education',    label: 'Education',                  icon: GraduationCap },
  { id: 'skills',       label: 'Skills',                     icon: Star },
  { id: 'certifications', label: 'Certifications',          icon: Award },
  { id: 'projects',     label: 'Projects',                   icon: FolderOpen },
  { id: 'preferences',  label: 'Job Preferences',            icon: Settings },
]

const PROFICIENCY_COLORS = {
  Beginner:     '#6b7280',
  Intermediate: '#3b82f6',
  Advanced:     '#8b5cf6',
  Expert:       '#f59e0b',
}

const SKILL_CATEGORIES = ['Technical', 'Soft Skills', 'Tools & Software', 'Domain Knowledge']

// ── Profile Strength banner ───────────────────────────────────────────────────

const TEAL = '#4ecdb5'
const TEAL_LIGHT = '#e9f9f4'
const TEAL_MID = '#c3ede6'

function levelFor(score) {
  if (score >= 95) return { name: 'Expert',       color: '#7c3aed', bg: '#f5f1ff', border: '#ddd2ff' }
  if (score >= 75) return { name: 'Advanced',     color: '#2563eb', bg: '#eef4ff', border: '#cddcff' }
  if (score >= 40) return { name: 'Intermediate', color: '#059669', bg: '#ecfdf5', border: '#c7f0da' }
  return              { name: 'Beginner',         color: '#d97706', bg: '#fff7ed', border: '#fed7aa' }
}

function ProfileStrengthBanner({ completion }) {
  if (!completion) return null
  const { score, missing } = completion
  const [open, setOpen] = useState(false)
  const [animScore, setAnimScore] = useState(0)
  const lvl = levelFor(score)

  useEffect(() => {
    const id = requestAnimationFrame(() => setAnimScore(score))
    return () => cancelAnimationFrame(id)
  }, [score])

  const R = 22
  const C = 2 * Math.PI * R
  const offset = C - (animScore / 100) * C

  return (
    <div style={{
      background: '#fff', border: '1px solid var(--border-subtle)',
      borderRadius: 14, padding: '16px 20px', marginBottom: 18,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
        {/* Ring */}
        <div style={{ width: 52, height: 52, position: 'relative', flexShrink: 0 }}>
          <svg width="52" height="52" viewBox="0 0 52 52">
            <circle cx="26" cy="26" r={R} fill="none" stroke="#f0fdf9" strokeWidth="5" />
            <circle
              cx="26" cy="26" r={R} fill="none" stroke={TEAL} strokeWidth="5"
              strokeLinecap="round" strokeDasharray={C} strokeDashoffset={offset}
              transform="rotate(-90 26 26)"
              style={{ transition: 'stroke-dashoffset 0.9s cubic-bezier(0.22,1,0.36,1)' }}
            />
          </svg>
          <div style={{
            position: 'absolute', inset: 0, display: 'grid', placeItems: 'center',
            fontSize: 11, fontWeight: 700, color: TEAL,
          }}>{score}%</div>
        </div>

        {/* Middle */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--text-primary)' }}>Profile Strength</span>
            <span style={{
              background: lvl.bg, color: lvl.color, border: `1px solid ${lvl.border}`,
              borderRadius: 20, padding: '2px 10px', fontSize: 11.5, fontWeight: 600,
              letterSpacing: 0.2,
            }}>{lvl.name}</span>
            {missing?.length > 0 && (
              <button
                onClick={() => setOpen(o => !o)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-tertiary)', marginLeft: 'auto',
                  display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
                }}
              >
                {open ? 'Hide' : 'What\'s missing'} {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              flex: 1, background: '#f3f4f6', borderRadius: 999, height: 6, overflow: 'hidden',
            }}>
              <div style={{
                width: `${animScore}%`, height: '100%', borderRadius: 999,
                background: `linear-gradient(90deg, ${TEAL}, #10b981)`,
                transition: 'width 0.9s cubic-bezier(0.22,1,0.36,1)',
              }} />
            </div>
            <span style={{ fontSize: 13, fontWeight: 700, color: TEAL, flexShrink: 0 }}>
              {score} / 100
            </span>
          </div>

          <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--text-secondary)' }}>
            Complete your profile to get better matches.
          </div>
        </div>
      </div>

      {open && missing?.length > 0 && (
        <div style={{
          marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border-subtle)',
          display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 6,
        }}>
          {missing.map((item, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--text-secondary)' }}>
              <span style={{
                width: 14, height: 14, borderRadius: '50%',
                border: `1.5px solid ${lvl.color}`, flexShrink: 0,
              }} />
              {item}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function SectionCard({ title, icon: Icon, children, action, noPadding }) {
  return (
    <div style={{
      background: '#fff', border: '1px solid var(--border-subtle)',
      borderRadius: 14, marginBottom: 18, overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 20px', borderBottom: '1px solid var(--border-subtle)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {Icon && <Icon size={16} strokeWidth={1.8} style={{ color: TEAL }} />}
          <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-primary)' }}>{title}</span>
        </div>
        {action}
      </div>
      <div style={{ padding: noPadding ? 0 : '20px' }}>{children}</div>
    </div>
  )
}

// ── Inline form helpers ───────────────────────────────────────────────────────

function Field({ label, required, error, children }) {
  return (
    <div className="input-group" style={{ marginBottom: 0 }}>
      <label style={{ fontSize: '0.8rem', fontWeight: 500, marginBottom: 4, display: 'block' }}>
        {label}
        {required && <span style={{ color: '#ef4444', marginLeft: 4 }}>*</span>}
      </label>
      {children}
      {error && <div style={{ color: '#ef4444', fontSize: 12, marginTop: 4 }}>{error}</div>}
    </div>
  )
}

function FormGrid({ children, cols = 2 }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 14 }}>
      {children}
    </div>
  )
}

function SaveRow({ onSave, onCancel, saving }) {
  return (
    <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
      <button className="btn btn-primary btn-sm" onClick={onSave} disabled={saving}>
        <Check size={14} /> {saving ? 'Saving…' : 'Save'}
      </button>
      <button className="btn btn-secondary btn-sm" onClick={onCancel} disabled={saving}>
        <X size={14} /> Cancel
      </button>
    </div>
  )
}

// ── Tag input (languages, preferred roles, etc.) ──────────────────────────────

function TagInput({ value = [], onChange, placeholder = 'Type and press Enter' }) {
  const [draft, setDraft] = useState('')

  const add = () => {
    const v = draft.trim()
    if (v && !value.includes(v)) onChange([...value, v])
    setDraft('')
  }

  return (
    <div style={{
      border: '1px solid var(--border-subtle)', borderRadius: 8,
      padding: '6px 10px', display: 'flex', flexWrap: 'wrap', gap: 6,
      background: 'var(--surface-input)', minHeight: 40,
    }}>
      {value.map(tag => (
        <span key={tag} style={{
          background: 'var(--accent-primary)22', color: 'var(--accent-primary)',
          border: '1px solid var(--accent-primary)44', borderRadius: 20,
          padding: '2px 10px', fontSize: '0.78rem', display: 'flex', alignItems: 'center', gap: 4,
        }}>
          {tag}
          <button onClick={() => onChange(value.filter(t => t !== tag))}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', padding: 0, lineHeight: 1, fontSize: '1rem' }}>×</button>
        </span>
      ))}
      <input
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } if (e.key === ',' ) { e.preventDefault(); add() } }}
        placeholder={value.length === 0 ? placeholder : ''}
        style={{ border: 'none', background: 'none', outline: 'none', flex: 1, minWidth: 120, fontSize: '0.85rem', color: 'var(--text-primary)' }}
      />
    </div>
  )
}

// ── List section (experience, education, certs, projects) ─────────────────────

function ListCard({ item, title, subtitle, meta, onEdit, onDelete }) {
  const [expand, setExpand] = useState(false)
  return (
    <div style={{
      border: '1px solid var(--border-subtle)', borderRadius: 10,
      marginBottom: 12, overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 16px', cursor: 'pointer', background: expand ? 'var(--surface-hover)' : 'transparent',
      }} onClick={() => setExpand(e => !e)}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: '0.9rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</div>
          {subtitle && <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 2 }}>{subtitle}</div>}
          {meta && <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: 2 }}>{meta}</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 8 }}>
          <button className="btn btn-secondary btn-sm" style={{ padding: '4px 10px', display: 'flex', alignItems: 'center', gap: 4 }}
            onClick={e => { e.stopPropagation(); onEdit() }}>
            <Pencil size={12} /> Edit
          </button>
          <button className="btn btn-secondary btn-sm" style={{ padding: '4px 10px', color: '#ef4444', borderColor: '#fca5a5', display: 'flex', alignItems: 'center', gap: 4 }}
            onClick={e => { e.stopPropagation(); onDelete() }}>
            <Trash2 size={12} /> Delete
          </button>
          {expand ? <ChevronUp size={14} style={{ color: 'var(--text-tertiary)' }} /> : <ChevronDown size={14} style={{ color: 'var(--text-tertiary)' }} />}
        </div>
      </div>
      {expand && item.description && (
        <div style={{ padding: '0 16px 12px', fontSize: '0.83rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          {item.description}
        </div>
      )}
    </div>
  )
}

// ── Modal wrapper ─────────────────────────────────────────────────────────────

function Modal({ title, children, onClose }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
    }} onClick={onClose}>
      <div style={{
        background: 'var(--surface-card)', border: '1px solid var(--border-subtle)',
        borderRadius: 16, width: '100%', maxWidth: 560, maxHeight: '90vh',
        overflow: 'auto', boxShadow: '0 24px 60px rgba(0,0,0,0.4)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '16px 20px', borderBottom: '1px solid var(--border-subtle)',
        }}>
          <span style={{ fontWeight: 600 }}>{title}</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>
        <div style={{ padding: '20px' }}>{children}</div>
      </div>
    </div>
  )
}

// ── Msg banner ────────────────────────────────────────────────────────────────

function Msg({ text, type = 'success' }) {
  if (!text) return null
  return (
    <div style={{
      padding: '8px 14px', borderRadius: 8, fontSize: '0.83rem', fontWeight: 500, marginTop: 10,
      background: type === 'success' ? '#22c55e22' : '#ef444422',
      color: type === 'success' ? '#22c55e' : '#ef4444',
      border: `1px solid ${type === 'success' ? '#22c55e44' : '#ef444444'}`,
    }}>{text}</div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function CandidateProfile() {
  const [activeSection, setActiveSection] = useState('personal')
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState({ text: '', type: 'success' })

  const flash = (text, type = 'success') => {
    setMsg({ text, type })
    setTimeout(() => setMsg({ text: '', type: 'success' }), 3000)
  }

  const reload = () =>
    api.get('/candidate/profile').then(r => {
      setProfile(r.data)
      setLoading(false)
    }).catch(() => setLoading(false))

  useEffect(() => { reload() }, [])

  if (loading) return (
    <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-tertiary)' }}>
      Loading profile…
    </div>
  )

  if (!profile) return (
    <div style={{ textAlign: 'center', padding: 60, color: 'var(--text-tertiary)' }}>
      Failed to load profile. Please refresh.
    </div>
  )

  // Compute per-section completion
  const done = {
    personal:       !!(profile.first_name && profile.last_name && profile.phone),
    location:       !!(profile.current_city && String(profile.current_city).trim()),
    summary:        !!(profile.headline || profile.about),
    experience:     (profile.work_experiences || []).length > 0,
    education:      (profile.educations || []).length > 0,
    skills:         (profile.skills || []).length > 0,
    certifications: (profile.certifications || []).length > 0,
    projects:       (profile.projects || []).length > 0,
    preferences:    !!(profile.preferences?.preferred_job_roles?.length || profile.preferences?.preferred_job_type || profile.preferences?.work_mode_preference || profile.preferences?.expected_salary_min),
    documents:      !!(profile.resume_path || profile.linkedin_url),
  }

  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
      {/* Section nav */}
      <aside style={{
        width: 220, flexShrink: 0,
        background: '#fff', border: '1px solid var(--border-subtle)',
        borderRadius: 14, padding: '10px', position: 'sticky', top: 24,
      }}>
        <div style={{
          padding: '6px 10px 10px', fontSize: 11, fontWeight: 600,
          letterSpacing: 0.6, color: 'var(--text-tertiary)', textTransform: 'uppercase',
        }}>Sections</div>
        {SECTIONS.map(({ id, label }) => {
          const isActive = activeSection === id
          const isDone   = done[id]
          return (
            <button key={id} onClick={() => setActiveSection(id)} style={{
              display: 'flex', alignItems: 'center', gap: 10, width: '100%',
              padding: '8px 10px', background: isActive ? TEAL_LIGHT : 'transparent',
              border: 'none', borderRadius: 8, cursor: 'pointer', textAlign: 'left',
              color: isActive ? TEAL : 'var(--text-secondary)',
              fontWeight: isActive ? 600 : 500, fontSize: 13,
              marginBottom: 2, transition: 'background 0.15s ease, color 0.15s ease',
              fontFamily: 'var(--font-body)',
            }}
            onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = '#f5f6f8' }}
            onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
            >
              {/* Dot indicator */}
              <span style={{
                width: 16, height: 16, borderRadius: '50%', flexShrink: 0,
                display: 'grid', placeItems: 'center',
                background: isActive ? TEAL : (!isActive && isDone ? TEAL : 'transparent'),
                border: isActive ? `1.5px solid ${TEAL}`
                      : isDone   ? `1.5px solid ${TEAL}`
                                 : '1.5px solid var(--border-subtle)',
              }}>
                {isActive && <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#fff' }} />}
                {!isActive && isDone && (
                  <svg width="9" height="9" viewBox="0 0 10 10" fill="none">
                    <path d="M2 5.2 L4.2 7.4 L8 3" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
              {label}
            </button>
          )
        })}
      </aside>

      {/* Main */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <ProfileStrengthBanner completion={profile.completion} />
        <Msg text={msg.text} type={msg.type} />

        {activeSection === 'personal'      && <PersonalSection                  profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'location'      && <LocationSection                  profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'experience'    && <WorkExperienceAndSummarySection profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'education'     && <EducationSection                 profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'skills'        && <SkillsSection                    profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'certifications'&& <CertificationsSection             profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'projects'      && <ProjectsSection                  profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'preferences'   && <PreferencesSection               profile={profile} reload={reload} flash={flash} />}
        {activeSection === 'documents'     && <DocumentsSection                 profile={profile} reload={reload} flash={flash} />}

        {(() => {
          const idx = SECTIONS.findIndex(s => s.id === activeSection)
          const prev = idx > 0 ? SECTIONS[idx - 1] : null
          const next = idx < SECTIONS.length - 1 ? SECTIONS[idx + 1] : null
          const go = (id) => {
            setActiveSection(id)
            window.scrollTo({ top: 0, behavior: 'smooth' })
          }
          const btnBase = {
            display: 'inline-flex', alignItems: 'center', gap: 8,
            padding: '10px 16px', borderRadius: 10, fontSize: 13,
            fontWeight: 600, cursor: 'pointer',
            fontFamily: 'var(--font-body)',
            transition: 'background 0.15s ease, border-color 0.15s ease, color 0.15s ease',
          }
          return (
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              marginTop: 20, paddingTop: 18, borderTop: '1px solid var(--border-subtle)',
              gap: 12,
            }}>
              {prev ? (
                <button onClick={() => go(prev.id)} style={{
                  ...btnBase,
                  background: '#fff', border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = '#f5f6f8' }}
                onMouseLeave={e => { e.currentTarget.style.background = '#fff' }}
                >
                  <ChevronLeft size={16} />
                  <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', lineHeight: 1.1 }}>
                    <span style={{ fontSize: 10, fontWeight: 500, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Previous</span>
                    <span>{prev.label}</span>
                  </span>
                </button>
              ) : <span />}
              {next ? (
                <button onClick={() => go(next.id)} style={{
                  ...btnBase,
                  background: TEAL, border: `1px solid ${TEAL}`, color: '#fff',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = '#3fb89f' }}
                onMouseLeave={e => { e.currentTarget.style.background = TEAL }}
                >
                  <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', lineHeight: 1.1 }}>
                    <span style={{ fontSize: 10, fontWeight: 500, opacity: 0.85, textTransform: 'uppercase', letterSpacing: 0.5 }}>Next</span>
                    <span>{next.label}</span>
                  </span>
                  <ChevronRight size={16} />
                </button>
              ) : (
                <button onClick={async () => {
                  await reload()
                  flash('Profile saved successfully')
                  window.scrollTo({ top: 0, behavior: 'smooth' })
                }} style={{
                  ...btnBase,
                  background: TEAL, border: `1px solid ${TEAL}`, color: '#fff',
                  padding: '12px 24px',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = '#3fb89f' }}
                onMouseLeave={e => { e.currentTarget.style.background = TEAL }}
                >
                  <Check size={16} />
                  <span>Save</span>
                </button>
              )}
            </div>
          )
        })()}
      </div>

      <style>{`
        .btn-sm { padding: 6px 14px !important; font-size: 0.8rem !important; }
        .input-group label { color: var(--text-secondary); }
      `}</style>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// SECTIONS
// ═════════════════════════════════════════════════════════════════════════════

// ── Personal Info ─────────────────────────────────────────────────────────────

function PersonalSection({ profile, reload, flash }) {
  const [edit, setEdit] = useState(false)
  const [form, setForm] = useState({})
  const [errors, setErrors] = useState({})
  const [saving, setSaving] = useState(false)
  const photoRef = useRef()

  const startEdit = () => {
    setForm({
      first_name: profile.first_name || '',
      last_name: profile.last_name || '',
      phone: profile.phone || '',
      gender: profile.gender || '',
      date_of_birth: profile.date_of_birth || '',
      alternate_phone: profile.alternate_phone || '',
      nationality: profile.nationality || '',
      marital_status: profile.marital_status || '',
      languages: profile.languages || [],
    })
    setEdit(true)
  }

  const validate = () => {
    const e = {}
    if (!form.first_name?.trim()) e.first_name = 'First name is required'
    if (!form.last_name?.trim())  e.last_name  = 'Last name is required'
    if (!form.phone?.trim())      e.phone      = 'Phone number is required'
    return e
  }

  const save = async () => {
    const errs = validate()
    if (Object.keys(errs).length) {
      setErrors(errs)
      flash('Please fill the required fields', 'error')
      return
    }
    setErrors({})
    setSaving(true)
    try {
      await api.put('/candidate/profile/personal', form)
      await reload()
      setEdit(false)
      flash('Personal info saved')
    } catch (e) {
      flash(e.response?.data?.detail || 'Save failed', 'error')
    }
    setSaving(false)
  }

  const uploadPhoto = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    const fd = new FormData()
    fd.append('photo', file)
    try {
      await api.post('/candidate/profile/photo', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
      await reload()
      flash('Photo updated')
    } catch (e) {
      flash(e.response?.data?.detail || 'Upload failed', 'error')
    }
  }

  const f = v => form[v] ?? ''

  const initials = ((profile.first_name?.[0] || '') + (profile.last_name?.[0] || '')).toUpperCase() || 'U'
  const fullName = [profile.first_name, profile.last_name].filter(Boolean).join(' ') || 'Your Name'

  const fields = [
    ['First Name',     profile.first_name],
    ['Last Name',      profile.last_name],
    ['Email Address',  profile.email],
    ['Gender',         profile.gender],
    ['Date of Birth',  profile.date_of_birth],
    ['Phone Number',   profile.phone],
    ['Alternate Phone',profile.alternate_phone],
    ['Nationality',    profile.nationality],
    ['Marital Status', profile.marital_status],
  ]

  return (
    <SectionCard title="Personal Information" icon={User} noPadding
      action={!edit && (
        <button onClick={startEdit} style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          background: 'var(--bg-primary)', border: '1px solid var(--border-subtle)',
          borderRadius: 8, padding: '6px 13px', fontSize: 12.5, fontWeight: 500,
          color: 'var(--text-secondary)', cursor: 'pointer', fontFamily: 'var(--font-body)',
          transition: 'all 0.15s ease',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = TEAL_LIGHT; e.currentTarget.style.borderColor = TEAL_MID; e.currentTarget.style.color = TEAL }}
        onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg-primary)'; e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
        >
          <Pencil size={13} strokeWidth={1.8} /> Edit
        </button>
      )}
    >
      {/* Photo row */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 20,
        padding: '20px', borderBottom: edit ? 'none' : '1px solid var(--border-subtle)',
      }}>
        <div style={{
          width: 72, height: 72, borderRadius: '50%', flexShrink: 0, position: 'relative',
          background: `linear-gradient(135deg, ${TEAL}, #10b981)`,
          display: 'grid', placeItems: 'center',
          border: '3px solid #fff', boxShadow: `0 0 0 2px ${TEAL_MID}`,
          overflow: 'hidden',
        }}>
          {profile.profile_photo_path
            ? <img src={`/${profile.profile_photo_path}`} alt="Profile" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            : <span style={{ color: '#fff', fontWeight: 700, fontSize: 22, letterSpacing: 0.5 }}>{initials}</span>
          }
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.2 }}>{fullName}</div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>{profile.email || ''}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
            <button onClick={() => photoRef.current?.click()} style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              background: TEAL, color: '#fff', border: 'none',
              borderRadius: 8, padding: '7px 14px', fontSize: 12.5, fontWeight: 600,
              cursor: 'pointer', fontFamily: 'var(--font-body)',
              transition: 'filter 0.15s ease',
            }}
            onMouseEnter={e => { e.currentTarget.style.filter = 'brightness(0.95)' }}
            onMouseLeave={e => { e.currentTarget.style.filter = 'none' }}
            >
              <Upload size={13} strokeWidth={2} />
              {profile.profile_photo_path ? 'Change Photo' : 'Upload Photo'}
            </button>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>JPG or PNG, max 2MB</span>
          </div>
        </div>
        <input ref={photoRef} type="file" accept=".jpg,.jpeg,.png" style={{ display: 'none' }} onChange={uploadPhoto} />
      </div>

      {!edit ? (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
            {fields.map(([label, val], i) => {
              const isLeft = i % 2 === 0
              const isLastRow = i >= fields.length - 2 || (i === fields.length - 1)
              return (
                <div key={label} style={{
                  padding: '16px 20px',
                  borderRight: isLeft ? '1px solid var(--border-subtle)' : 'none',
                  borderBottom: isLastRow ? 'none' : '1px solid var(--border-subtle)',
                }}>
                  <div style={{
                    fontSize: 11, fontWeight: 500, color: 'var(--text-tertiary)',
                    textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6,
                  }}>{label}</div>
                  {val ? (
                    <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)' }}>{val}</div>
                  ) : (
                    <div style={{ fontSize: 13, fontStyle: 'italic', color: 'var(--text-tertiary)' }}>Not provided</div>
                  )}
                </div>
              )
            })}
          </div>
          {(profile.languages || []).length > 0 && (
            <div style={{ padding: '16px 20px', borderTop: '1px solid var(--border-subtle)' }}>
              <div style={{
                fontSize: 11, fontWeight: 500, color: 'var(--text-tertiary)',
                textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 8,
              }}>Languages</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {profile.languages.map(l => (
                  <span key={l} style={{
                    background: TEAL_LIGHT, color: TEAL, border: `1px solid ${TEAL_MID}`,
                    borderRadius: 20, padding: '3px 12px', fontSize: 12, fontWeight: 500,
                  }}>{l}</span>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <div style={{ padding: '20px' }}>
          <FormGrid>
            <Field label="First Name" required error={errors.first_name}>
              <input className="input" required value={f('first_name')} onChange={e => setForm({ ...form, first_name: e.target.value })} />
            </Field>
            <Field label="Last Name" required error={errors.last_name}>
              <input className="input" required value={f('last_name')} onChange={e => setForm({ ...form, last_name: e.target.value })} />
            </Field>
            <Field label="Phone" required error={errors.phone}>
              <input className="input" required value={f('phone')} onChange={e => setForm({ ...form, phone: e.target.value })} />
            </Field>
            <Field label="Alternate Phone">
              <input className="input" value={f('alternate_phone')} onChange={e => setForm({ ...form, alternate_phone: e.target.value })} />
            </Field>
            <Field label="Gender">
              <select className="select" value={f('gender')} onChange={e => setForm({ ...form, gender: e.target.value })}>
                <option value="">Select</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
                <option value="other">Other</option>
              </select>
            </Field>
            <Field label="Date of Birth">
              <input className="input" type="date" value={f('date_of_birth')}
                max={new Date(new Date().setFullYear(new Date().getFullYear() - 16)).toISOString().split('T')[0]}
                onChange={e => setForm({ ...form, date_of_birth: e.target.value })} />
            </Field>
            <Field label="Nationality">
              <input className="input" value={f('nationality')} onChange={e => setForm({ ...form, nationality: e.target.value })} />
            </Field>
            <Field label="Marital Status">
              <select className="select" value={f('marital_status')} onChange={e => setForm({ ...form, marital_status: e.target.value })}>
                <option value="">Select</option>
                <option>Single</option>
                <option>Married</option>
                <option>Prefer not to say</option>
              </select>
            </Field>
          </FormGrid>
          <div style={{ marginTop: 14 }}>
            <Field label="Languages (press Enter to add)">
              <TagInput value={form.languages || []} onChange={v => setForm({ ...form, languages: v })} placeholder="e.g. English, Hindi…" />
            </Field>
          </div>
          <SaveRow onSave={save} onCancel={() => setEdit(false)} saving={saving} />
        </div>
      )}
    </SectionCard>
  )
}

// ── Location ──────────────────────────────────────────────────────────────────

// Sub-heading inside the Location card
function AddressHeading({ children }) {
  return (
    <div style={{
      fontSize: '0.78rem', fontWeight: 700, color: 'var(--text-secondary)',
      textTransform: 'uppercase', letterSpacing: '0.06em', margin: '4px 0 12px',
    }}>{children}</div>
  )
}

function LocationSection({ profile, reload, flash }) {
  const [edit, setEdit] = useState(false)
  const [form, setForm] = useState({})
  const [errors, setErrors] = useState({})
  const [saving, setSaving] = useState(false)

  const startEdit = () => setForm({
    current_address_line: profile.current_address_line || '',
    current_city: profile.current_city || '',
    state: profile.state || '',
    country: profile.country || '',
    pincode: profile.pincode || '',
    permanent_address_line: profile.permanent_address_line || '',
    permanent_city: profile.permanent_city || '',
    permanent_state: profile.permanent_state || '',
    permanent_country: profile.permanent_country || '',
    permanent_pincode: profile.permanent_pincode || '',
    permanent_same_as_current: profile.permanent_same_as_current || false,
    willing_to_relocate: profile.willing_to_relocate || false,
  })

  const save = async () => {
    const errs = {}
    if (!form.current_city?.trim()) errs.current_city = 'Current city is required'
    if (Object.keys(errs).length) {
      setErrors(errs)
      flash('Please fill the required fields', 'error')
      return
    }
    setErrors({})
    setSaving(true)
    try {
      // If "same as current" is ticked, mirror the current address into permanent.
      const payload = { ...form }
      if (payload.permanent_same_as_current) {
        payload.permanent_address_line = payload.current_address_line
        payload.permanent_city = payload.current_city
        payload.permanent_state = payload.state
        payload.permanent_country = payload.country
        payload.permanent_pincode = payload.pincode
      }
      await api.put('/candidate/profile/location', payload)
      await reload()
      setEdit(false)
      flash('Location updated')
    } catch (e) {
      flash(e.response?.data?.detail || 'Save failed', 'error')
    }
    setSaving(false)
  }

  const f = v => form[v] ?? ''
  const same = !!form.permanent_same_as_current

  // ── Read-only view helpers ──
  const currentLines = [
    profile.current_address_line,
    [profile.current_city, profile.state].filter(Boolean).join(', '),
    [profile.country, profile.pincode].filter(Boolean).join(' '),
  ].filter(s => s && String(s).trim())
  const permLines = profile.permanent_same_as_current
    ? ['Same as current address']
    : [
        profile.permanent_address_line,
        [profile.permanent_city, profile.permanent_state].filter(Boolean).join(', '),
        [profile.permanent_country, profile.permanent_pincode].filter(Boolean).join(' '),
      ].filter(s => s && String(s).trim())

  const AddressBlock = ({ title, lines }) => (
    <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: 16 }}>
      <AddressHeading>{title}</AddressHeading>
      {lines.length > 0
        ? <div style={{ fontSize: '0.9rem', fontWeight: 500, lineHeight: 1.6 }}>
            {lines.map((l, i) => <div key={i}>{l}</div>)}
          </div>
        : <div style={{ color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>Not provided</div>}
    </div>
  )

  return (
    <SectionCard title="Location & Address" icon={MapPin}
      action={!edit && <button className="btn btn-secondary btn-sm" onClick={() => { startEdit(); setEdit(true) }}><Pencil size={13} /> Edit</button>}
    >
      {!edit ? (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <AddressBlock title="Current Address" lines={currentLines} />
            <AddressBlock title="Permanent Address" lines={permLines} />
          </div>
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginBottom: 2 }}>Ready to Relocate</div>
            <div style={{ fontSize: '0.9rem', fontWeight: 500 }}>{profile.willing_to_relocate ? 'Yes' : 'No'}</div>
          </div>
        </>
      ) : (
        <>
          {/* Current Address */}
          <AddressHeading>Current Address</AddressHeading>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Field label="Address Line"><input className="input" value={f('current_address_line')} onChange={e => setForm({ ...form, current_address_line: e.target.value })} placeholder="House / street / area" /></Field>
            <FormGrid>
              <Field label="City" required error={errors.current_city}><input className="input" required value={f('current_city')} onChange={e => setForm({ ...form, current_city: e.target.value })} /></Field>
              <Field label="State / Province"><input className="input" value={f('state')} onChange={e => setForm({ ...form, state: e.target.value })} /></Field>
              <Field label="Country"><input className="input" value={f('country')} onChange={e => setForm({ ...form, country: e.target.value })} /></Field>
              <Field label="Pincode / ZIP"><input className="input" value={f('pincode')} onChange={e => setForm({ ...form, pincode: e.target.value })} /></Field>
            </FormGrid>
          </div>

          {/* Permanent Address */}
          <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <AddressHeading>Permanent Address</AddressHeading>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.85rem', cursor: 'pointer' }}>
              <input type="checkbox" checked={same}
                onChange={e => setForm({ ...form, permanent_same_as_current: e.target.checked })}
                style={{ accentColor: 'var(--accent-primary)' }} />
              Same as current address
            </label>
          </div>
          {!same && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <Field label="Address Line"><input className="input" value={f('permanent_address_line')} onChange={e => setForm({ ...form, permanent_address_line: e.target.value })} placeholder="House / street / area" /></Field>
              <FormGrid>
                <Field label="City"><input className="input" value={f('permanent_city')} onChange={e => setForm({ ...form, permanent_city: e.target.value })} /></Field>
                <Field label="State / Province"><input className="input" value={f('permanent_state')} onChange={e => setForm({ ...form, permanent_state: e.target.value })} /></Field>
                <Field label="Country"><input className="input" value={f('permanent_country')} onChange={e => setForm({ ...form, permanent_country: e.target.value })} /></Field>
                <Field label="Pincode / ZIP"><input className="input" value={f('permanent_pincode')} onChange={e => setForm({ ...form, permanent_pincode: e.target.value })} /></Field>
              </FormGrid>
            </div>
          )}

          {/* Ready to relocate */}
          <div style={{ marginTop: 18, display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" id="relocate" checked={form.willing_to_relocate || false}
              onChange={e => setForm({ ...form, willing_to_relocate: e.target.checked })} style={{ accentColor: 'var(--accent-primary)' }} />
            <label htmlFor="relocate" style={{ fontSize: '0.88rem', cursor: 'pointer' }}>Ready to relocate</label>
          </div>
          <SaveRow onSave={save} onCancel={() => setEdit(false)} saving={saving} />
        </>
      )}
    </SectionCard>
  )
}

// ── Work Experience & Summary (Merged Section) ────────────────────────────────

const EMPTY_EXP = { job_title: '', company_name: '', industry: '', employment_type: '', location: '', start_date: '', end_date: '', currently_working: false, description: '', key_achievements: '' }

function WorkExperienceAndSummarySection({ profile, reload, flash }) {
  // Summary state
  const [editSummary, setEditSummary] = useState(false)
  const [summaryForm, setSummaryForm] = useState({})
  const [summaryErrors, setSummaryErrors] = useState({})
  const [savingSummary, setSavingSummary] = useState(false)

  // Experience modal state
  const [modal, setModal] = useState(null)  // null | { mode: 'add'|'edit', data, id? }
  const [savingExp, setSavingExp] = useState(false)

  const startEditSummary = () => setSummaryForm({
    headline: profile.headline || '',
    about: profile.about || '',
    total_experience_years: profile.total_experience_years ?? '',
    current_employment_status: profile.current_employment_status || '',
    notice_period: profile.notice_period || '',
    open_to_work: profile.open_to_work || false,
  })

  const saveSummary = async () => {
    if (!summaryForm.headline?.trim() && !summaryForm.about?.trim()) {
      setSummaryErrors({ summary: 'Provide a headline or an about summary.' })
      flash('Provide a headline or an about summary', 'error')
      return
    }
    setSummaryErrors({})
    setSavingSummary(true)
    try {
      const payload = { ...summaryForm, total_experience_years: summaryForm.total_experience_years !== '' ? Number(summaryForm.total_experience_years) : null }
      await api.put('/candidate/profile/summary', payload)
      await reload()
      setEditSummary(false)
      flash('Summary updated')
    } catch (e) {
      flash(e.response?.data?.detail || 'Save failed', 'error')
    }
    setSavingSummary(false)
  }

  const openAdd = () => setModal({ mode: 'add', data: { ...EMPTY_EXP } })
  const openEdit = (item) => setModal({ mode: 'edit', data: { ...item }, id: item.id })

  const saveExp = async () => {
    const d = modal.data
    if (!d.job_title?.trim() || !d.company_name?.trim()) {
      flash('Job Title and Company are required', 'error')
      return
    }
    setSavingExp(true)
    try {
      const { mode, data, id } = modal
      if (mode === 'add') await api.post('/candidate/profile/work-experience', data)
      else await api.put(`/candidate/profile/work-experience/${id}`, data)
      await reload()
      setModal(null)
      flash(mode === 'add' ? 'Experience added' : 'Experience updated')
    } catch (e) {
      flash(e.response?.data?.detail || 'Save failed', 'error')
    }
    setSavingExp(false)
  }

  const removeExp = async (id) => {
    if (!window.confirm('Delete this work experience?')) return
    try {
      await api.delete(`/candidate/profile/work-experience/${id}`)
      await reload()
      flash('Experience deleted')
    } catch { flash('Delete failed', 'error') }
  }

  const d = modal?.data
  const set = (k, v) => setModal(m => ({ ...m, data: { ...m.data, [k]: v } }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Card 1: Professional Summary */}
      <SectionCard title="Professional Overview & Summary" icon={Briefcase}
        action={!editSummary && <button className="btn btn-secondary btn-sm" onClick={() => { startEditSummary(); setEditSummary(true) }}><Pencil size={13} /> Edit Summary</button>}
      >
        {!editSummary ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {profile.open_to_work && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: '#22c55e22', color: '#22c55e', border: '1px solid #22c55e44', borderRadius: 20, padding: '3px 14px', fontSize: '0.8rem', fontWeight: 600, width: 'fit-content' }}>
                <BadgeCheck size={13} /> Open to Work
              </span>
            )}
            {profile.headline && <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>{profile.headline}</div>}
            {profile.about && <div style={{ fontSize: '0.88rem', color: 'var(--text-secondary)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{profile.about}</div>}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              {profile.total_experience_years != null && (
                <div><div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>Total Experience</div><div style={{ fontWeight: 600 }}>{profile.total_experience_years} yrs</div></div>
              )}
              {profile.current_employment_status && (
                <div><div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>Status</div><div style={{ fontWeight: 600 }}>{profile.current_employment_status}</div></div>
              )}
              {profile.notice_period && (
                <div><div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>Notice Period</div><div style={{ fontWeight: 600 }}>{profile.notice_period}</div></div>
              )}
            </div>
            {!profile.headline && !profile.about && (
              <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No summary added yet.</div>
            )}
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <input type="checkbox" id="otw" checked={summaryForm.open_to_work || false}
                onChange={e => setSummaryForm({ ...summaryForm, open_to_work: e.target.checked })} style={{ accentColor: '#22c55e' }} />
              <label htmlFor="otw" style={{ fontSize: '0.88rem', cursor: 'pointer' }}>I am open to work</label>
            </div>
            {summaryErrors.summary && (
              <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#b91c1c', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 12 }}>
                {summaryErrors.summary}
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <Field label="Headline (max 120 chars)" required>
                <input className="input" maxLength={120} value={summaryForm.headline || ''} onChange={e => setSummaryForm({ ...summaryForm, headline: e.target.value })} placeholder="e.g. Full Stack Developer at Infosys" />
              </Field>
              <Field label="About / Summary (max 2000 chars)" required>
                <textarea className="input" rows={5} maxLength={2000} value={summaryForm.about || ''} onChange={e => setSummaryForm({ ...summaryForm, about: e.target.value })} style={{ resize: 'vertical' }} placeholder="Brief professional bio…" />
              </Field>
              <FormGrid>
                <Field label="Total Experience (years)">
                  <input className="input" type="number" min={0} max={50} step={0.5} value={summaryForm.total_experience_years ?? ''} onChange={e => setSummaryForm({ ...summaryForm, total_experience_years: e.target.value })} />
                </Field>
                <Field label="Employment Status">
                  <select className="select" value={summaryForm.current_employment_status || ''} onChange={e => setSummaryForm({ ...summaryForm, current_employment_status: e.target.value })}>
                    <option value="">Select</option>
                    {['Employed', 'Unemployed', 'Student', 'Freelancing'].map(v => <option key={v}>{v}</option>)}
                  </select>
                </Field>
                <Field label="Notice Period">
                  <select className="select" value={summaryForm.notice_period || ''} onChange={e => setSummaryForm({ ...summaryForm, notice_period: e.target.value })}>
                    <option value="">Select</option>
                    {['Immediately', '15 days', '1 month', '2 months', '3 months', 'More than 3 months'].map(v => <option key={v}>{v}</option>)}
                  </select>
                </Field>
              </FormGrid>
            </div>
            <SaveRow onSave={saveSummary} onCancel={() => setEditSummary(false)} saving={savingSummary} />
          </>
        )}
      </SectionCard>

      {/* Card 2: Work Experience Entries */}
      <SectionCard title="Work History & Roles" icon={Briefcase}
        action={<button className="btn btn-primary btn-sm" onClick={openAdd}><Plus size={13} /> Add Experience</button>}
      >
        {(profile.work_experiences || []).length === 0
          ? <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No work experience added yet.</div>
          : profile.work_experiences.map(w => (
            <ListCard key={w.id} item={w}
              title={`${w.job_title} — ${w.company_name}`}
              subtitle={[w.employment_type, w.location].filter(Boolean).join(' · ')}
              meta={w.currently_working ? `${w.start_date || ''} – Present` : `${w.start_date || ''} – ${w.end_date || ''}`}
              onEdit={() => openEdit(w)}
              onDelete={() => removeExp(w.id)}
            />
          ))
        }
      </SectionCard>

      {modal && (
        <Modal title={modal.mode === 'add' ? 'Add Work Experience' : 'Edit Work Experience'} onClose={() => setModal(null)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <FormGrid>
              <Field label="Job Title" required><input className="input" required value={d.job_title} onChange={e => set('job_title', e.target.value)} /></Field>
              <Field label="Company" required><input className="input" required value={d.company_name} onChange={e => set('company_name', e.target.value)} /></Field>
              <Field label="Industry"><input className="input" value={d.industry || ''} onChange={e => set('industry', e.target.value)} /></Field>
              <Field label="Employment Type">
                <select className="select" value={d.employment_type || ''} onChange={e => set('employment_type', e.target.value)}>
                  <option value="">Select</option>
                  {['Full-time', 'Part-time', 'Contract', 'Internship', 'Freelance'].map(v => <option key={v}>{v}</option>)}
                </select>
              </Field>
              <Field label="Location"><input className="input" value={d.location || ''} onChange={e => set('location', e.target.value)} /></Field>
            </FormGrid>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" id="cw" checked={d.currently_working || false}
                onChange={e => set('currently_working', e.target.checked)} style={{ accentColor: 'var(--accent-primary)' }} />
              <label htmlFor="cw" style={{ fontSize: '0.88rem', cursor: 'pointer' }}>I currently work here</label>
            </div>
            <FormGrid>
              <Field label="Start Date (YYYY-MM)"><input className="input" type="month" value={d.start_date || ''} onChange={e => set('start_date', e.target.value)} /></Field>
              {!d.currently_working && <Field label="End Date (YYYY-MM)"><input className="input" type="month" value={d.end_date || ''} onChange={e => set('end_date', e.target.value)} /></Field>}
            </FormGrid>
            <Field label="Description (roles & responsibilities)">
              <textarea className="input" rows={3} maxLength={1000} value={d.description || ''} onChange={e => set('description', e.target.value)} style={{ resize: 'vertical' }} />
            </Field>
            <Field label="Key Achievements (optional)">
              <textarea className="input" rows={2} maxLength={500} value={d.key_achievements || ''} onChange={e => set('key_achievements', e.target.value)} style={{ resize: 'vertical' }} />
            </Field>
            <SaveRow onSave={saveExp} onCancel={() => setModal(null)} saving={savingExp} />
          </div>
        </Modal>
      )}
    </div>
  )
}

// ── Education ─────────────────────────────────────────────────────────────────

const EDUCATION_LEVELS = ['School', 'Intermediate', 'Diploma', "Bachelor's Degree", "Master's Degree", 'Doctorate / PhD']

const EMPTY_EDU = { institution_name: '', education_level: '', degree: '', field_of_study: '', university_or_board: '', start_year: '', end_year: '', currently_pursuing: false, grade: '', description: '' }

function EducationSection({ profile, reload, flash }) {
  const [modal, setModal] = useState(null)
  const [saving, setSaving] = useState(false)

  const openAdd = () => setModal({ mode: 'add', data: { ...EMPTY_EDU } })
  const openEdit = item => setModal({ mode: 'edit', data: { ...item }, id: item.id })

  const save = async () => {
    if (!modal.data.institution_name?.trim()) {
      flash('Institution Name is required', 'error')
      return
    }
    setSaving(true)
    try {
      const { mode, data, id } = modal
      const payload = {
        ...data,
        start_year: data.start_year ? Number(data.start_year) : null,
        end_year: data.end_year ? Number(data.end_year) : null,
      }
      if (mode === 'add') await api.post('/candidate/profile/education', payload)
      else await api.put(`/candidate/profile/education/${id}`, payload)
      await reload()
      setModal(null)
      flash(mode === 'add' ? 'Education added' : 'Education updated')
    } catch (e) { flash(e.response?.data?.detail || 'Save failed', 'error') }
    setSaving(false)
  }

  const remove = async (id) => {
    if (!window.confirm('Delete this education entry?')) return
    try { await api.delete(`/candidate/profile/education/${id}`); await reload(); flash('Education deleted') }
    catch { flash('Delete failed', 'error') }
  }

  const d = modal?.data
  const set = (k, v) => setModal(m => ({ ...m, data: { ...m.data, [k]: v } }))

  return (
    <>
      <SectionCard title="Education" icon={GraduationCap}
        action={<button className="btn btn-primary btn-sm" onClick={openAdd}><Plus size={13} /> Add</button>}
      >
        {(profile.educations || []).length === 0
          ? <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No education entries yet.</div>
          : profile.educations.map(e => (
            <ListCard key={e.id} item={e}
              title={e.institution_name}
              subtitle={[e.education_level, e.degree, e.field_of_study].filter(Boolean).join(' · ')}
              meta={e.currently_pursuing ? `${e.start_year || ''} – Present` : [e.start_year, e.end_year].filter(Boolean).join(' – ')}
              onEdit={() => openEdit(e)}
              onDelete={() => remove(e.id)}
            />
          ))
        }
      </SectionCard>

      {modal && (
        <Modal title={modal.mode === 'add' ? 'Add Education' : 'Edit Education'} onClose={() => setModal(null)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Field label="Education Level">
              <select className="select" value={d.education_level || ''} onChange={e => set('education_level', e.target.value)}>
                <option value="">Select</option>
                {EDUCATION_LEVELS.map(v => <option key={v}>{v}</option>)}
              </select>
            </Field>
            <Field label="Institution Name" required><input className="input" required value={d.institution_name} onChange={e => set('institution_name', e.target.value)} /></Field>
            <FormGrid>
              <Field label="Degree"><input className="input" value={d.degree || ''} onChange={e => set('degree', e.target.value)} placeholder="B.Tech, MBA, 12th…" /></Field>
              <Field label="Field of Study"><input className="input" value={d.field_of_study || ''} onChange={e => set('field_of_study', e.target.value)} /></Field>
              <Field label="University / Board"><input className="input" value={d.university_or_board || ''} onChange={e => set('university_or_board', e.target.value)} /></Field>
              <Field label="Grade / CGPA / %"><input className="input" value={d.grade || ''} onChange={e => set('grade', e.target.value)} /></Field>
              <Field label="Start Year"><input className="input" type="number" min={1960} max={new Date().getFullYear()} value={d.start_year || ''} onChange={e => set('start_year', e.target.value)} /></Field>
              {!d.currently_pursuing && <Field label="End Year"><input className="input" type="number" min={1960} max={new Date().getFullYear() + 6} value={d.end_year || ''} onChange={e => set('end_year', e.target.value)} /></Field>}
            </FormGrid>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" id="cp" checked={d.currently_pursuing || false}
                onChange={e => set('currently_pursuing', e.target.checked)} style={{ accentColor: 'var(--accent-primary)' }} />
              <label htmlFor="cp" style={{ fontSize: '0.88rem', cursor: 'pointer' }}>Currently pursuing</label>
            </div>
            <Field label="Description (optional)">
              <textarea className="input" rows={2} value={d.description || ''} onChange={e => set('description', e.target.value)} style={{ resize: 'vertical' }} />
            </Field>
            <SaveRow onSave={save} onCancel={() => setModal(null)} saving={saving} />
          </div>
        </Modal>
      )}
    </>
  )
}

// ── Skills ────────────────────────────────────────────────────────────────────

function SkillsSection({ profile, reload, flash }) {
  const [form, setForm] = useState({ skill_name: '', proficiency_level: '', years_of_experience: '', category: 'Technical' })
  const [saving, setSaving] = useState(false)
  const [showAdd, setShowAdd] = useState(false)

  const add = async () => {
    if (!form.skill_name.trim()) {
      flash('Skill name is required', 'error')
      return
    }
    setSaving(true)
    try {
      await api.post('/candidate/profile/skills', {
        ...form,
        years_of_experience: form.years_of_experience ? Number(form.years_of_experience) : null,
      })
      await reload()
      setForm({ skill_name: '', proficiency_level: '', years_of_experience: '', category: 'Technical' })
      setShowAdd(false)
      flash('Skill added')
    } catch (e) { flash(e.response?.data?.detail || 'Add failed', 'error') }
    setSaving(false)
  }

  const remove = async (id) => {
    try { await api.delete(`/candidate/profile/skills/${id}`); await reload(); flash('Skill removed') }
    catch { flash('Remove failed', 'error') }
  }

  const grouped = SKILL_CATEGORIES.reduce((acc, cat) => {
    acc[cat] = (profile.skills || []).filter(s => s.category === cat)
    return acc
  }, {})
  const ungrouped = (profile.skills || []).filter(s => !s.category || !SKILL_CATEGORIES.includes(s.category))

  return (
    <SectionCard title="Skills" icon={Star}
      action={<button className="btn btn-primary btn-sm" onClick={() => setShowAdd(s => !s)}><Plus size={13} /> Add Skill</button>}
    >
      {(profile.skills || []).length < 3 && (
        <div style={{
          background: '#fff7ed', border: '1px solid #fed7aa', color: '#9a3412',
          borderRadius: 8, padding: '8px 12px', marginBottom: 14,
          fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ fontSize: 14 }}>⚠</span>
          Add at least 3 skills for a complete profile ({(profile.skills || []).length}/3)
        </div>
      )}
      {showAdd && (
        <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
          <FormGrid>
            <Field label="Skill Name" required>
              <input className="input" required value={form.skill_name} onChange={e => setForm({ ...form, skill_name: e.target.value })}
                onKeyDown={e => e.key === 'Enter' && add()} placeholder="e.g. React, Python…" />
            </Field>
            <Field label="Category">
              <select className="select" value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>
                {SKILL_CATEGORIES.map(c => <option key={c}>{c}</option>)}
              </select>
            </Field>
            <Field label="Proficiency">
              <select className="select" value={form.proficiency_level} onChange={e => setForm({ ...form, proficiency_level: e.target.value })}>
                <option value="">Select</option>
                {['Beginner', 'Intermediate', 'Advanced', 'Expert'].map(v => <option key={v}>{v}</option>)}
              </select>
            </Field>
            <Field label="Years of Exp (optional)">
              <input className="input" type="number" min={0} max={50} step={0.5} value={form.years_of_experience} onChange={e => setForm({ ...form, years_of_experience: e.target.value })} />
            </Field>
          </FormGrid>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button className="btn btn-primary btn-sm" onClick={add} disabled={saving}><Check size={13} /> {saving ? 'Adding…' : 'Add'}</button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowAdd(false)}><X size={13} /> Cancel</button>
          </div>
        </div>
      )}

      {(profile.skills || []).length === 0 && !showAdd && (
        <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No skills added yet. Add at least 3 to boost your profile.</div>
      )}

      {[...SKILL_CATEGORIES, ungrouped.length > 0 ? 'Other' : null].filter(Boolean).map(cat => {
        const list = cat === 'Other' ? ungrouped : grouped[cat]
        if (!list || list.length === 0) return null
        return (
          <div key={cat} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>{cat}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {list.map(s => {
                const color = PROFICIENCY_COLORS[s.proficiency_level] || 'var(--text-tertiary)'
                return (
                  <span key={s.id} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    background: color + '18', border: `1px solid ${color}44`,
                    borderRadius: 20, padding: '5px 12px', fontSize: '0.82rem',
                  }}>
                    <span style={{ fontWeight: 600 }}>{s.skill_name}</span>
                    {s.proficiency_level && <span style={{ fontSize: '0.72rem', color, opacity: 0.85 }}>{s.proficiency_level}</span>}
                    {s.years_of_experience && <span style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>{s.years_of_experience}y</span>}
                    <button onClick={() => remove(s.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 0, lineHeight: 1, fontSize: '1rem', marginLeft: 2 }}>×</button>
                  </span>
                )
              })}
            </div>
          </div>
        )
      })}

      {(profile.skills || []).length > 0 && (
        <div style={{ marginTop: 4, fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
          {profile.skills.length}/30 skills added
        </div>
      )}
    </SectionCard>
  )
}

// ── Certifications ────────────────────────────────────────────────────────────

const EMPTY_CERT = { certificate_name: '', issuing_organization: '', issue_date: '', expiry_date: '', does_not_expire: false, credential_id: '', credential_url: '' }

function CertificationsSection({ profile, reload, flash }) {
  const [modal, setModal] = useState(null)
  const [saving, setSaving] = useState(false)

  const openAdd = () => setModal({ mode: 'add', data: { ...EMPTY_CERT } })
  const openEdit = item => setModal({ mode: 'edit', data: { ...item }, id: item.id })

  const save = async () => {
    if (!modal.data.certificate_name?.trim()) {
      flash('Certificate Name is required', 'error')
      return
    }
    setSaving(true)
    try {
      const { mode, data, id } = modal
      if (mode === 'add') await api.post('/candidate/profile/certifications', data)
      else await api.put(`/candidate/profile/certifications/${id}`, data)
      await reload()
      setModal(null)
      flash(mode === 'add' ? 'Certification added' : 'Certification updated')
    } catch (e) { flash(e.response?.data?.detail || 'Save failed', 'error') }
    setSaving(false)
  }

  const remove = async (id) => {
    if (!window.confirm('Delete this certification?')) return
    try { await api.delete(`/candidate/profile/certifications/${id}`); await reload(); flash('Certification deleted') }
    catch { flash('Delete failed', 'error') }
  }

  const d = modal?.data
  const set = (k, v) => setModal(m => ({ ...m, data: { ...m.data, [k]: v } }))

  return (
    <>
      <SectionCard title="Certifications & Licenses" icon={Award}
        action={<button className="btn btn-primary btn-sm" onClick={openAdd}><Plus size={13} /> Add</button>}
      >
        {(profile.certifications || []).length === 0
          ? <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No certifications added yet.</div>
          : profile.certifications.map(c => (
            <ListCard key={c.id} item={c}
              title={c.certificate_name}
              subtitle={c.issuing_organization}
              meta={c.does_not_expire ? `Issued ${c.issue_date || ''} · No Expiry` : [c.issue_date, c.expiry_date].filter(Boolean).join(' – ')}
              onEdit={() => openEdit(c)}
              onDelete={() => remove(c.id)}
            />
          ))
        }
      </SectionCard>

      {modal && (
        <Modal title={modal.mode === 'add' ? 'Add Certification' : 'Edit Certification'} onClose={() => setModal(null)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Field label="Certificate Name" required><input className="input" required value={d.certificate_name} onChange={e => set('certificate_name', e.target.value)} /></Field>
            <Field label="Issuing Organization"><input className="input" value={d.issuing_organization || ''} onChange={e => set('issuing_organization', e.target.value)} /></Field>
            <FormGrid>
              <Field label="Issue Date"><input className="input" type="month" value={d.issue_date || ''} onChange={e => set('issue_date', e.target.value)} /></Field>
              {!d.does_not_expire && <Field label="Expiry Date"><input className="input" type="month" value={d.expiry_date || ''} onChange={e => set('expiry_date', e.target.value)} /></Field>}
            </FormGrid>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" id="dne" checked={d.does_not_expire || false}
                onChange={e => set('does_not_expire', e.target.checked)} style={{ accentColor: 'var(--accent-primary)' }} />
              <label htmlFor="dne" style={{ fontSize: '0.88rem', cursor: 'pointer' }}>Does not expire</label>
            </div>
            <FormGrid>
              <Field label="Credential ID"><input className="input" value={d.credential_id || ''} onChange={e => set('credential_id', e.target.value)} /></Field>
              <Field label="Credential URL"><input className="input" type="url" value={d.credential_url || ''} onChange={e => set('credential_url', e.target.value)} placeholder="https://…" /></Field>
            </FormGrid>
            <SaveRow onSave={save} onCancel={() => setModal(null)} saving={saving} />
          </div>
        </Modal>
      )}
    </>
  )
}

// ── Projects ──────────────────────────────────────────────────────────────────

const EMPTY_PROJ = { project_title: '', project_type: '', description: '', technologies_used: [], role: '', start_date: '', end_date: '', ongoing: false, project_url: '', repository_url: '' }

function ProjectsSection({ profile, reload, flash }) {
  const [modal, setModal] = useState(null)
  const [saving, setSaving] = useState(false)

  const openAdd = () => setModal({ mode: 'add', data: { ...EMPTY_PROJ, technologies_used: [] } })
  const openEdit = item => setModal({ mode: 'edit', data: { ...item, technologies_used: item.technologies_used || [] }, id: item.id })

  const save = async () => {
    if (!modal.data.project_title?.trim()) {
      flash('Project Title is required', 'error')
      return
    }
    setSaving(true)
    try {
      const { mode, data, id } = modal
      if (mode === 'add') await api.post('/candidate/profile/projects', data)
      else await api.put(`/candidate/profile/projects/${id}`, data)
      await reload()
      setModal(null)
      flash(mode === 'add' ? 'Project added' : 'Project updated')
    } catch (e) { flash(e.response?.data?.detail || 'Save failed', 'error') }
    setSaving(false)
  }

  const remove = async (id) => {
    if (!window.confirm('Delete this project?')) return
    try { await api.delete(`/candidate/profile/projects/${id}`); await reload(); flash('Project deleted') }
    catch { flash('Delete failed', 'error') }
  }

  const d = modal?.data
  const set = (k, v) => setModal(m => ({ ...m, data: { ...m.data, [k]: v } }))

  return (
    <>
      <SectionCard title="Projects" icon={FolderOpen}
        action={<button className="btn btn-primary btn-sm" onClick={openAdd}><Plus size={13} /> Add</button>}
      >
        {(profile.projects || []).length === 0
          ? <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No projects added yet.</div>
          : profile.projects.map(p => (
            <ListCard key={p.id} item={p}
              title={p.project_title}
              subtitle={[p.project_type, p.role].filter(Boolean).join(' · ')}
              meta={(p.technologies_used || []).slice(0, 4).join(', ')}
              onEdit={() => openEdit(p)}
              onDelete={() => remove(p.id)}
            />
          ))
        }
      </SectionCard>

      {modal && (
        <Modal title={modal.mode === 'add' ? 'Add Project' : 'Edit Project'} onClose={() => setModal(null)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <FormGrid>
              <Field label="Project Title" required><input className="input" required value={d.project_title} onChange={e => set('project_title', e.target.value)} /></Field>
              <Field label="Project Type">
                <select className="select" value={d.project_type || ''} onChange={e => set('project_type', e.target.value)}>
                  <option value="">Select</option>
                  {['Personal', 'Academic', 'Professional', 'Open-source'].map(v => <option key={v}>{v}</option>)}
                </select>
              </Field>
              <Field label="Your Role"><input className="input" value={d.role || ''} onChange={e => set('role', e.target.value)} /></Field>
            </FormGrid>
            <Field label="Technologies Used (press Enter)">
              <TagInput value={d.technologies_used || []} onChange={v => set('technologies_used', v)} placeholder="e.g. React, Node.js…" />
            </Field>
            <Field label="Description">
              <textarea className="input" rows={3} maxLength={1000} value={d.description || ''} onChange={e => set('description', e.target.value)} style={{ resize: 'vertical' }} />
            </Field>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" id="og" checked={d.ongoing || false}
                onChange={e => set('ongoing', e.target.checked)} style={{ accentColor: 'var(--accent-primary)' }} />
              <label htmlFor="og" style={{ fontSize: '0.88rem', cursor: 'pointer' }}>Ongoing project</label>
            </div>
            <FormGrid>
              <Field label="Start Date"><input className="input" type="month" value={d.start_date || ''} onChange={e => set('start_date', e.target.value)} /></Field>
              {!d.ongoing && <Field label="End Date"><input className="input" type="month" value={d.end_date || ''} onChange={e => set('end_date', e.target.value)} /></Field>}
            </FormGrid>
            <FormGrid>
              <Field label="Live URL"><input className="input" type="url" value={d.project_url || ''} onChange={e => set('project_url', e.target.value)} placeholder="https://…" /></Field>
              <Field label="Repository URL"><input className="input" type="url" value={d.repository_url || ''} onChange={e => set('repository_url', e.target.value)} placeholder="https://github.com/…" /></Field>
            </FormGrid>
            <SaveRow onSave={save} onCancel={() => setModal(null)} saving={saving} />
          </div>
        </Modal>
      )}
    </>
  )
}

// ── Job Preferences ───────────────────────────────────────────────────────────

function PreferencesSection({ profile, reload, flash }) {
  const [edit, setEdit] = useState(false)
  const [form, setForm] = useState({})
  const [saving, setSaving] = useState(false)

  const p = profile.preferences || {}

  const startEdit = () => setForm({
    preferred_job_roles: p.preferred_job_roles || [],
    preferred_industries: p.preferred_industries || [],
    preferred_job_type: p.preferred_job_type || '',
    work_mode_preference: p.work_mode_preference || '',
    expected_salary_min: p.expected_salary_min ?? '',
    expected_salary_max: p.expected_salary_max ?? '',
    salary_currency: p.salary_currency || 'INR',
    salary_type: p.salary_type || 'Annual',
    preferred_locations: p.preferred_locations || [],
  })

  const save = async () => {
    const hasJobType  = !!form.preferred_job_type?.trim()
    const hasWorkMode = !!form.work_mode_preference?.trim()
    const hasSalary   = form.expected_salary_min !== '' && form.expected_salary_min != null
    if (!hasJobType && !hasWorkMode && !hasSalary) {
      flash('Provide at least one: Job Type, Work Mode, or Expected Salary (Min)', 'error')
      return
    }
    setSaving(true)
    try {
      await api.put('/candidate/profile/preferences', {
        ...form,
        expected_salary_min: form.expected_salary_min !== '' ? Number(form.expected_salary_min) : null,
        expected_salary_max: form.expected_salary_max !== '' ? Number(form.expected_salary_max) : null,
      })
      await reload()
      setEdit(false)
      flash('Preferences saved')
    } catch (e) { flash(e.response?.data?.detail || 'Save failed', 'error') }
    setSaving(false)
  }

  return (
    <SectionCard title="Job Preferences" icon={Settings}
      action={!edit && <button className="btn btn-secondary btn-sm" onClick={() => { startEdit(); setEdit(true) }}><Pencil size={13} /> Edit</button>}
    >
      {!edit ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          {[
            ['Job Type', p.preferred_job_type],
            ['Work Mode', p.work_mode_preference],
            ['Salary Type', p.salary_type],
            ['Currency', p.salary_currency],
            ['Salary Range', p.expected_salary_min || p.expected_salary_max ? `${p.salary_currency || ''} ${p.expected_salary_min || '?'} – ${p.expected_salary_max || '?'}` : null],
          ].map(([label, val]) => val ? (
            <div key={label}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginBottom: 2 }}>{label}</div>
              <div style={{ fontWeight: 500 }}>{val}</div>
            </div>
          ) : null)}
          {(p.preferred_job_roles || []).length > 0 && (
            <div style={{ gridColumn: '1/-1' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginBottom: 6 }}>Preferred Roles</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {p.preferred_job_roles.map(r => <span key={r} style={{ background: 'var(--surface-input)', border: '1px solid var(--border-subtle)', borderRadius: 20, padding: '3px 12px', fontSize: '0.8rem' }}>{r}</span>)}
              </div>
            </div>
          )}
          {(p.preferred_industries || []).length > 0 && (
            <div style={{ gridColumn: '1/-1' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginBottom: 6 }}>Industries</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {p.preferred_industries.map(i => <span key={i} style={{ background: 'var(--surface-input)', border: '1px solid var(--border-subtle)', borderRadius: 20, padding: '3px 12px', fontSize: '0.8rem' }}>{i}</span>)}
              </div>
            </div>
          )}
          {!p.preferred_job_type && !p.work_mode_preference && (
            <div style={{ gridColumn: '1/-1', color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No preferences set yet.</div>
          )}
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <FormGrid>
              <Field label="Preferred Job Type">
                <select className="select" value={form.preferred_job_type || ''} onChange={e => setForm({ ...form, preferred_job_type: e.target.value })}>
                  <option value="">Select</option>
                  {['Full-time', 'Part-time', 'Contract', 'Internship', 'Freelance'].map(v => <option key={v}>{v}</option>)}
                </select>
              </Field>
              <Field label="Work Mode">
                <select className="select" value={form.work_mode_preference || ''} onChange={e => setForm({ ...form, work_mode_preference: e.target.value })}>
                  <option value="">Select</option>
                  {['On-site', 'Remote', 'Hybrid', 'Any'].map(v => <option key={v}>{v}</option>)}
                </select>
              </Field>
              <Field label="Currency">
                <select className="select" value={form.salary_currency || 'INR'} onChange={e => setForm({ ...form, salary_currency: e.target.value })}>
                  {['INR', 'USD', 'EUR', 'GBP', 'AED', 'SGD'].map(v => <option key={v}>{v}</option>)}
                </select>
              </Field>
              <Field label="Salary Type">
                <select className="select" value={form.salary_type || 'Annual'} onChange={e => setForm({ ...form, salary_type: e.target.value })}>
                  <option>Annual</option><option>Monthly</option>
                </select>
              </Field>
              <Field label="Expected Salary (Min)">
                <input className="input" type="number" min={0} value={form.expected_salary_min ?? ''} onChange={e => setForm({ ...form, expected_salary_min: e.target.value })} />
              </Field>
              <Field label="Expected Salary (Max)">
                <input className="input" type="number" min={0} value={form.expected_salary_max ?? ''} onChange={e => setForm({ ...form, expected_salary_max: e.target.value })} />
              </Field>
            </FormGrid>
            <Field label="Preferred Job Roles">
              <TagInput value={form.preferred_job_roles || []} onChange={v => setForm({ ...form, preferred_job_roles: v })} placeholder="e.g. Backend Developer…" />
            </Field>
            <Field label="Preferred Industries">
              <TagInput value={form.preferred_industries || []} onChange={v => setForm({ ...form, preferred_industries: v })} placeholder="e.g. FinTech, Healthcare…" />
            </Field>
            <Field label="Preferred Locations">
              <TagInput value={form.preferred_locations || []} onChange={v => setForm({ ...form, preferred_locations: v })} placeholder="e.g. Bangalore, Remote…" />
            </Field>
          </div>
          <SaveRow onSave={save} onCancel={() => setEdit(false)} saving={saving} />
        </>
      )}
    </SectionCard>
  )
}

// ── Documents & Links ─────────────────────────────────────────────────────────

function DocumentsSection({ profile, reload, flash }) {
  const [editLinks, setEditLinks] = useState(false)
  const [linksForm, setLinksForm] = useState({})
  const [saving, setSaving] = useState(false)
  const resumeRef = useRef()
  const clRef = useRef()

  // Resume auto-fill (Smart Profile Import)
  const autoFillRef = useRef()
  const [parsing, setParsing] = useState(false)
  const [reviewData, setReviewData] = useState(null)

  const handleAutoFill = async () => {
    const file = autoFillRef.current?.files[0]
    if (!file) return
    setParsing(true)
    const fd = new FormData()
    fd.append('resume', file)
    try {
      const r = await api.post('/candidate/profile/import/parse', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setReviewData(r.data)
    } catch (e) {
      flash(e.response?.data?.detail || 'Could not read that resume. Try a different file.', 'error')
    } finally {
      setParsing(false)
      if (autoFillRef.current) autoFillRef.current.value = ''  // allow re-picking the same file
    }
  }

  const handleImportApplied = async (result) => {
    setReviewData(null)
    await reload()
    const added = (result?.skills_added || 0) + (result?.work_experience_added || 0)
      + (result?.education_added || 0) + (result?.certifications_added || 0) + (result?.projects_added || 0)
    flash(added > 0 ? `Added ${added} item${added > 1 ? 's' : ''} to your profile` : 'Nothing new to add')
  }

  const lnk = profile.links || {}

  const startEditLinks = () => setLinksForm({
    linkedin_url: lnk.linkedin_url || '',
    github_url: lnk.github_url || '',
    portfolio_url: lnk.portfolio_url || '',
    twitter_url: lnk.twitter_url || '',
    behance_url: lnk.behance_url || '',
    dribbble_url: lnk.dribbble_url || '',
    other_url: lnk.other_url || '',
    other_url_label: lnk.other_url_label || '',
  })

  const saveLinks = async () => {
    setSaving(true)
    try {
      await api.put('/candidate/profile/links', linksForm)
      await reload()
      setEditLinks(false)
      flash('Links updated')
    } catch (e) { flash(e.response?.data?.detail || 'Save failed', 'error') }
    setSaving(false)
  }

  const uploadFile = async (ref, endpoint, fieldName) => {
    const file = ref.current?.files[0]
    if (!file) return
    const fd = new FormData()
    fd.append(fieldName, file)
    try {
      await api.post(endpoint, fd, { headers: { 'Content-Type': 'multipart/form-data' } })
      await reload()
      flash('File uploaded successfully')
    } catch (e) { flash(e.response?.data?.detail || 'Upload failed', 'error') }
  }

  const linkItems = [
    { key: 'linkedin_url', label: 'LinkedIn', icon: Linkedin },
    { key: 'github_url', label: 'GitHub', icon: Github },
    { key: 'portfolio_url', label: 'Portfolio', icon: Globe },
    { key: 'twitter_url', label: 'Twitter / X', icon: Twitter },
    { key: 'behance_url', label: 'Behance', icon: ExternalLink },
    { key: 'dribbble_url', label: 'Dribbble', icon: ExternalLink },
  ]

  return (
    <>
      {/* Documents */}
      <SectionCard title="Documents" icon={FileText}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Resume */}
          <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Resume / CV</div>
                  {!profile.resume_path && (
                    <span style={{
                      fontSize: '0.68rem',
                      fontWeight: 700,
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      color: '#ef4444',
                      background: '#fef2f2',
                      border: '1px solid #fecaca',
                      padding: '2px 8px',
                      borderRadius: 999,
                    }}>Required</span>
                  )}
                </div>
                {profile.resume_path
                  ? <div style={{ fontSize: '0.8rem', color: '#22c55e', marginTop: 4 }}>Uploaded · <a href={`/${profile.resume_path}`} target="_blank" rel="noreferrer" style={{ color: 'inherit' }}>Preview</a></div>
                  : <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginTop: 4 }}>PDF only, max 5 MB</div>
                }
              </div>
              <button className="btn btn-secondary btn-sm" onClick={() => resumeRef.current?.click()}>
                <Upload size={13} /> {profile.resume_path ? 'Replace' : 'Upload'}
              </button>
            </div>
            <input ref={resumeRef} type="file" accept=".pdf" style={{ display: 'none' }}
              onChange={() => uploadFile(resumeRef, '/candidate/profile/documents/resume', 'file')} />

            {/* Auto-fill profile from resume */}
            <div style={{
              marginTop: 14, paddingTop: 14, borderTop: '1px dashed var(--border-subtle)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <Sparkles size={16} style={{ color: TEAL, flexShrink: 0 }} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>Auto-fill from resume</div>
                  <div style={{ fontSize: '0.76rem', color: 'var(--text-tertiary)' }}>
                    We'll read your resume and pre-fill skills, experience &amp; education — you review before anything is saved.
                  </div>
                </div>
              </div>
              <button className="btn btn-sm" style={{ background: TEAL, color: '#fff', border: `1px solid ${TEAL}` }}
                onClick={() => autoFillRef.current?.click()} disabled={parsing}>
                {parsing ? <><Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> Reading…</> : <><Sparkles size={13} /> Auto-fill</>}
              </button>
            </div>
            <input ref={autoFillRef} type="file" accept=".pdf,.docx,.doc" style={{ display: 'none' }}
              onChange={handleAutoFill} />
          </div>

          {reviewData && (
            <ResumeImportReview
              data={reviewData}
              onClose={() => setReviewData(null)}
              onApplied={handleImportApplied}
            />
          )}

          {/* Cover Letter */}
          <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Cover Letter</div>
                {profile.cover_letter_path
                  ? <div style={{ fontSize: '0.8rem', color: '#22c55e', marginTop: 4 }}>Uploaded · <a href={`/${profile.cover_letter_path}`} target="_blank" rel="noreferrer" style={{ color: 'inherit' }}>Preview</a></div>
                  : <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginTop: 4 }}>PDF / DOC / DOCX, max 5 MB (optional)</div>
                }
              </div>
              <button className="btn btn-secondary btn-sm" onClick={() => clRef.current?.click()}>
                <Upload size={13} /> {profile.cover_letter_path ? 'Replace' : 'Upload'}
              </button>
            </div>
            <input ref={clRef} type="file" accept=".pdf,.doc,.docx" style={{ display: 'none' }}
              onChange={() => uploadFile(clRef, '/candidate/profile/documents/cover-letter', 'file')} />
          </div>
        </div>
      </SectionCard>

      {/* Social Links */}
      <SectionCard title="Social & Web Links" icon={Link2}
        action={!editLinks && <button className="btn btn-secondary btn-sm" onClick={() => { startEditLinks(); setEditLinks(true) }}><Pencil size={13} /> Edit</button>}
      >
        {!editLinks ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {linkItems.map(({ key, label, icon: Icon }) => lnk[key] ? (
              <a key={key} href={lnk[key]} target="_blank" rel="noreferrer" style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                border: '1px solid var(--border-subtle)', borderRadius: 8,
                color: 'var(--text-primary)', textDecoration: 'none', fontSize: '0.88rem',
                background: 'var(--surface-input)',
              }}>
                <Icon size={15} style={{ color: 'var(--accent-primary)', flexShrink: 0 }} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{lnk[key]}</span>
                <ExternalLink size={12} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
              </a>
            ) : null)}
            {lnk.other_url && (
              <a href={lnk.other_url} target="_blank" rel="noreferrer" style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                border: '1px solid var(--border-subtle)', borderRadius: 8,
                color: 'var(--text-primary)', textDecoration: 'none', fontSize: '0.88rem',
                background: 'var(--surface-input)',
              }}>
                <Globe size={15} style={{ color: 'var(--accent-primary)', flexShrink: 0 }} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{lnk.other_url_label ? `${lnk.other_url_label}: ` : ''}{lnk.other_url}</span>
              </a>
            )}
            {!Object.values(lnk).some(Boolean) && (
              <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No links added yet.</div>
            )}
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {linkItems.map(({ key, label }) => (
                <Field key={key} label={label}>
                  <input className="input" type="url" value={linksForm[key] || ''} onChange={e => setLinksForm({ ...linksForm, [key]: e.target.value })} placeholder="https://…" />
                </Field>
              ))}
              <FormGrid>
                <Field label="Custom Link Label">
                  <input className="input" value={linksForm.other_url_label || ''} onChange={e => setLinksForm({ ...linksForm, other_url_label: e.target.value })} placeholder="e.g. Personal Blog" />
                </Field>
                <Field label="Custom Link URL">
                  <input className="input" type="url" value={linksForm.other_url || ''} onChange={e => setLinksForm({ ...linksForm, other_url: e.target.value })} placeholder="https://…" />
                </Field>
              </FormGrid>
            </div>
            <SaveRow onSave={saveLinks} onCancel={() => setEditLinks(false)} saving={saving} />
          </>
        )}
      </SectionCard>
    </>
  )
}
