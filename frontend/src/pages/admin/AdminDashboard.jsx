import { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Legend, AreaChart, Area, CartesianGrid } from 'recharts'
import { Users, Building2, Briefcase, TrendingUp, Plus, Edit3, Trash2, Search, Camera, Upload, X, Mail, CheckCircle2, AlertCircle, Send } from 'lucide-react'
import api from '../../api/client'
import Header from '../../components/Header'
import SupportPanel from './SupportPanel'
import MailboxManager from './MailboxManager'
import MailRoutingPanel from './MailRoutingPanel'

const CHART_COLORS = ['#2b7cba', '#3b82f6', '#22c55e', '#8b5cf6', '#ef4444', '#06b6d4']

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: 'var(--surface-modal)', border: '1px solid var(--border-medium)',
      borderRadius: 8, padding: '10px 14px', fontSize: '0.85rem'
    }}>
      <div style={{ color: 'var(--text-tertiary)', marginBottom: 4, fontFamily: 'var(--font-mono)', fontSize: '0.75rem' }}>{label}</div>
      <div style={{ color: 'var(--accent-primary)', fontWeight: 600 }}>{payload[0].value}</div>
    </div>
  )
}

const TAB_MAP = { users: 'users', departments: 'departments', analytics: 'analytics', email: 'email', support: 'support' }

// Role categories for the Users cards. 'all' is a UI-only key — the API takes
// no `role` param for it. The rest match the `users.role` strings exactly.
//
// `tone`/`tint`/`line` are the same colours the role badges use in the table below
// (.badge-red / -amber / -blue / -green in index.css), so a role reads the same
// wherever it appears on this page rather than picking up a second identity here.
const ROLE_TABS = [
  { key: 'all',         label: 'All Users',    tone: '#2b7cba', tint: '#eef4fb', line: '#c0d8ed' },
  { key: 'admin',       label: 'Admin',        tone: '#c44536', tint: '#fef2f0', line: '#f5c6c0' },
  { key: 'hr',          label: 'HR',           tone: '#92610a', tint: '#fef7ec', line: '#fde4b1' },
  { key: 'interviewer', label: 'Interviewer',  tone: '#2d5f8a', tint: '#eef4fb', line: '#c0d8ed' },
  { key: 'candidate',   label: 'Candidate',    tone: '#1a6b5a', tint: '#eef6f3', line: '#c8e6dc' },
]
const ROLE_KEYS = ROLE_TABS.map(t => t.key)
const PAGE_SIZE = 25

/**
 * One role card in the Users filter row.
 *
 * A button, not a div: this filters the table below it, so it has to be reachable and
 * operable from the keyboard like the tab bar it replaced. `aria-pressed` carries the
 * selected state, because the tinted background and heavier border are decoration that
 * a screen reader never sees.
 */
function RoleCard({ role, count, selected, onSelect }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      // Without this the accessible name is the raw text content — "8HR" — because the
      // numeral is rendered first. Spelling it out gives a screen reader the same two
      // facts a sighted reader gets from the layout, in a sensible order.
      aria-label={count == null
        ? `${role.label}, loading count`
        : `${role.label}, ${count} ${count === 1 ? 'user' : 'users'}`}
      onClick={() => onSelect(role.key)}
      style={{
        flex: '1 1 150px', minWidth: 140, textAlign: 'left', cursor: 'pointer',
        padding: '14px 16px', borderRadius: 12,
        background: selected ? role.tint : 'var(--surface-card)',
        border: `1px solid ${selected ? role.line : 'var(--border-subtle)'}`,
        // The selected card is lifted by a ring rather than a colour swap alone, so the
        // choice survives greyscale and colour-blindness.
        boxShadow: selected ? `inset 0 0 0 1px ${role.line}` : 'none',
        transition: 'background 0.15s, border-color 0.15s',
      }}
    >
      <div style={{
        fontSize: '1.6rem', fontWeight: 800, lineHeight: 1.1,
        fontFamily: 'var(--font-mono)', color: role.tone,
      }}>
        {count == null ? '—' : count}
      </div>
      <div style={{
        fontSize: '0.8rem', fontWeight: 700, marginTop: 4,
        color: selected ? role.tone : 'var(--text-primary)',
      }}>
        {role.label}
      </div>
    </button>
  )
}

function DepartmentCard({ dept, stat, accent, logo, initials, onUploadLogo, onRemoveLogo, onEdit, onDelete }) {
  const fileRef = useRef(null)
  const [hovering, setHovering] = useState(false)
  const [dragOver, setDragOver] = useState(false)

  // Hands the raw File to the parent, which uploads it. Previously this read the
  // file into a base64 data URL and stashed it in localStorage, so the logo only
  // existed in whichever browser uploaded it.
  const processFile = (file) => {
    if (!file || !file.type.startsWith('image/')) return
    onUploadLogo(dept.id, file)
  }

  const handleFileChange = (e) => processFile(e.target.files[0])

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    processFile(e.dataTransfer.files[0])
  }

  return (
    <div style={{
      borderRadius: 16,
      overflow: 'hidden',
      background: 'var(--surface-card, var(--bg-secondary))',
      border: `1px solid ${accent}22`,
      boxShadow: `0 4px 24px ${accent}0a`,
      transition: 'transform 0.18s ease, box-shadow 0.18s ease',
    }}
    onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = `0 8px 32px ${accent}18` }}
    onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = `0 4px 24px ${accent}0a` }}
    >
      {/* Top accent strip */}
      <div style={{ height: 4, background: `linear-gradient(90deg, ${accent}, ${accent}88)` }} />

      {/* Logo + header */}
      <div style={{ padding: '20px 20px 16px', display: 'flex', alignItems: 'center', gap: 16 }}>
        {/* Logo upload zone */}
        <div
          style={{ position: 'relative', flexShrink: 0, cursor: 'pointer' }}
          onMouseEnter={() => setHovering(true)}
          onMouseLeave={() => setHovering(false)}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
        >
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />

          {/* Logo circle */}
          <div style={{
            width: 72, height: 72, borderRadius: 16,
            overflow: 'hidden', position: 'relative',
            border: dragOver ? `2px dashed ${accent}` : `2px solid ${accent}30`,
            background: logo ? 'transparent' : `linear-gradient(135deg, ${accent}20, ${accent}08)`,
            transition: 'border-color 0.15s, box-shadow 0.15s',
            boxShadow: hovering ? `0 0 0 3px ${accent}22` : 'none',
          }}>
            {logo ? (
              <img src={logo} alt={dept.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              <div style={{
                width: '100%', height: '100%',
                display: 'grid', placeItems: 'center',
                fontSize: '1.4rem', fontWeight: 800,
                color: accent,
                fontFamily: 'var(--font-display)',
                letterSpacing: '-0.03em',
              }}>
                {initials}
              </div>
            )}

            {/* Hover overlay */}
            <div style={{
              position: 'absolute', inset: 0,
              background: 'rgba(0,0,0,0.55)',
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2,
              opacity: hovering || dragOver ? 1 : 0,
              transition: 'opacity 0.15s',
            }}>
              {dragOver ? (
                <Upload size={20} style={{ color: '#fff' }} />
              ) : (
                <Camera size={18} style={{ color: '#fff' }} />
              )}
              <span style={{ fontSize: '0.55rem', color: 'rgba(255,255,255,0.85)', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                {dragOver ? 'Drop' : 'Upload'}
              </span>
            </div>
          </div>

          {/* Remove button */}
          {logo && (
            <button
              onClick={(e) => { e.stopPropagation(); onRemoveLogo(dept.id) }}
              style={{
                position: 'absolute', top: -6, right: -6,
                width: 20, height: 20, borderRadius: '50%',
                background: '#ef4444', border: '2px solid var(--bg-secondary)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', padding: 0,
                opacity: hovering ? 1 : 0,
                transition: 'opacity 0.15s',
              }}
            >
              <X size={10} style={{ color: '#fff' }} />
            </button>
          )}
        </div>

        {/* Name + description */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontWeight: 800, fontSize: '1.05rem',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-display)',
            letterSpacing: '-0.025em',
            lineHeight: 1.2,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {dept.name}
          </div>
          {dept.description && (
            <div style={{
              fontSize: '0.78rem', color: 'var(--text-tertiary)',
              marginTop: 4, lineHeight: 1.4,
              display: '-webkit-box', WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical', overflow: 'hidden',
            }}>
              {dept.description}
            </div>
          )}
          {!dept.description && (
            <div style={{ fontSize: '0.72rem', color: `${accent}80`, marginTop: 4, fontFamily: 'var(--font-mono)' }}>
              Click logo to upload image
            </div>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1, margin: '0 16px', marginBottom: 16 }}>
        {[
          { label: 'Employees', value: stat.employees || 0, color: accent },
          { label: 'Jobs', value: stat.jobs || 0, color: '#22c55e' },
          { label: 'Candidates', value: stat.candidates || 0, color: '#8b5cf6' },
        ].map(({ label, value, color }) => (
          <div key={label} style={{
            textAlign: 'center', padding: '10px 4px',
            background: 'var(--bg-tertiary)',
            borderRadius: 10,
          }}>
            <div style={{ fontSize: '1.25rem', fontWeight: 800, color, fontFamily: 'var(--font-mono)', lineHeight: 1 }}>{value}</div>
            <div style={{ fontSize: '0.62rem', fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginTop: 3 }}>{label}</div>
          </div>
        ))}
      </div>

    </div>
  )
}

const EMPTY_USER = { email: '', password: '', first_name: '', last_name: '', phone: '', role: 'hr', department_id: '', employee_id: '', expertise_areas: '', years_experience: '' }
const EMPTY_DEPT = { name: '', description: '' }

export default function AdminDashboard() {
  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [users, setUsers] = useState([])
  const [departments, setDepartments] = useState([])
  const [search, setSearch] = useState('')
  // Role filtering, paging and per-role counts all run server-side — the table
  // renders exactly the page the API returned.
  const [userTotal, setUserTotal] = useState(0)
  const [userPage, setUserPage] = useState(0)
  const [roleCounts, setRoleCounts] = useState(null)
  const [analytics, setAnalytics] = useState(null)
  const [showUserModal, setShowUserModal] = useState(false)
  const [showDeptModal, setShowDeptModal] = useState(false)
  const [userForm, setUserForm] = useState(EMPTY_USER)
  const [editingUserId, setEditingUserId] = useState(null)   // null = add mode
  const [deptForm, setDeptForm] = useState(EMPTY_DEPT)
  const [submitting, setSubmitting] = useState(false)
  const [deptStats, setDeptStats] = useState([])
  const [hrComparison, setHrComparison] = useState([])
  const [genderData, setGenderData] = useState([])
  const [ageData, setAgeData] = useState([])
  // Logos now come back on the department row itself (dept.logo_url) — they used to
  // be base64 blobs in this browser's localStorage, invisible to other admins.
  const [logoError, setLogoError] = useState('')

  // ── Per-company email (SMTP) configuration ─────────────────────────────
  const [emailConfig, setEmailConfig] = useState(null)          // server snapshot
  const [emailForm, setEmailForm] = useState({                  // editable form
    smtp_host: 'smtp.gmail.com',
    smtp_port: 587,
    smtp_user: '',
    smtp_pass: '',          // blank = keep existing
    smtp_from_email: '',
  })
  const [emailSaving, setEmailSaving]   = useState(false)
  const [emailMessage, setEmailMessage] = useState(null)        // {type:'success'|'error', text}
  const [testTo, setTestTo]             = useState('')
  const [testing, setTesting]           = useState(false)
  const [testResult, setTestResult]     = useState(null)

  const loadEmailConfig = useCallback(() => {
    api.get('/admin/email-config').then(r => {
      setEmailConfig(r.data)
      setEmailForm(f => ({
        ...f,
        smtp_host:       r.data.smtp_host || 'smtp.gmail.com',
        smtp_port:       r.data.smtp_port || 587,
        smtp_user:       r.data.smtp_user || '',
        smtp_from_email: r.data.smtp_from_email || '',
        smtp_pass:       '',
      }))
    }).catch(() => {})
  }, [])

  const handleSaveEmailConfig = async (e) => {
    e.preventDefault()
    setEmailMessage(null)
    setEmailSaving(true)
    try {
      const payload = {
        smtp_host: emailForm.smtp_host || 'smtp.gmail.com',
        smtp_port: Number(emailForm.smtp_port) || 587,
        smtp_user: emailForm.smtp_user,
        smtp_from_email: emailForm.smtp_from_email || emailForm.smtp_user,
      }
      if (emailForm.smtp_pass) payload.smtp_pass = emailForm.smtp_pass
      await api.put('/admin/email-config', payload)
      setEmailMessage({ type: 'success', text: 'Email configuration saved.' })
      loadEmailConfig()
    } catch (err) {
      setEmailMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to save configuration' })
    }
    setEmailSaving(false)
  }

  const handleSendTestEmail = async () => {
    if (!testTo) { setTestResult({ sent: false, error: 'Enter a recipient email' }); return }
    setTesting(true)
    setTestResult(null)
    try {
      const r = await api.post('/admin/email/test', {
        to: testTo,
        subject: 'HireFlow SMTP Test',
        body: '<p>If you can read this, your company SMTP is configured correctly.</p>',
      })
      setTestResult(r.data)
    } catch (err) {
      setTestResult({ sent: false, error: err.response?.data?.detail || 'Failed to send test' })
    }
    setTesting(false)
  }

  const handleClearEmailConfig = async () => {
    if (!confirm('Clear the company email config? Emails will fall back to the global default.')) return
    try {
      await api.delete('/admin/email-config')
      setEmailMessage({ type: 'success', text: 'Email configuration cleared.' })
      loadEmailConfig()
    } catch (err) {
      setEmailMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to clear configuration' })
    }
  }

  const saveLogo = useCallback(async (deptId, file) => {
    setLogoError('')
    const fd = new FormData()
    fd.append('file', file)
    try {
      // The Content-Type override is required: api's default is application/json,
      // which would suppress the multipart boundary and the upload would fail.
      await api.post(`/admin/departments/${deptId}/logo`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      fetchDepartments()
    } catch (err) {
      setLogoError(err.response?.data?.detail || 'Failed to upload logo')
    }
  }, [])

  const removeLogo = useCallback(async (deptId) => {
    setLogoError('')
    try {
      await api.delete(`/admin/departments/${deptId}/logo`)
      fetchDepartments()
    } catch (err) {
      setLogoError(err.response?.data?.detail || 'Failed to remove logo')
    }
  }, [])

  const segment = location.pathname.split('/').pop()
  const tab = TAB_MAP[segment] || (segment === 'admin' ? 'overview' : 'overview')

  const setTab = (t) => navigate(t === 'overview' ? '/admin' : `/admin/${t}`)

  // The selected role category lives in the URL (?role=hr) rather than in state,
  // matching how the top-level tabs work above — so it survives a reload and can
  // be bookmarked or shared.
  const roleFilter = ROLE_KEYS.includes(searchParams.get('role')) ? searchParams.get('role') : 'all'
  const setRoleFilter = (r) => {
    const next = new URLSearchParams(searchParams)
    if (r === 'all') next.delete('role')
    else next.set('role', r)
    setSearchParams(next, { replace: true })
  }

  // Debounce the search box so typing doesn't fire a request per keystroke.
  const [debouncedSearch, setDebouncedSearch] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(t)
  }, [search])

  // Changing the category or the search term invalidates the current page —
  // without this, switching to a 3-user category while on page 4 shows nothing.
  useEffect(() => { setUserPage(0) }, [roleFilter, debouncedSearch])

  useEffect(() => {
    api.get('/admin/departments').then(r => setDepartments(r.data)).catch(() => {})
    api.get('/admin/department-stats').then(r => setDeptStats(r.data)).catch(() => {})
    api.get('/admin/analytics/overview').then(r => setAnalytics(r.data)).catch(() => {})
    api.get('/admin/analytics/hr-comparison').then(r => setHrComparison(r.data)).catch(() => {})
    api.get('/admin/analytics/gender-distribution').then(r => setGenderData(r.data)).catch(() => {})
    api.get('/admin/analytics/age-distribution').then(r => setAgeData(r.data)).catch(() => {})
    loadEmailConfig()
  }, [loadEmailConfig])

  // Role, text search and paging are all query params — the server already
  // excludes superadmin, so there is nothing left to filter client-side.
  const fetchUsers = useCallback(() => {
    api.get('/admin/users', {
      params: {
        role: roleFilter === 'all' ? undefined : roleFilter,
        search: debouncedSearch || undefined,
        skip: userPage * PAGE_SIZE,
        limit: PAGE_SIZE,
      },
    }).then(r => {
      setUsers(r.data?.users || [])
      setUserTotal(r.data?.total || 0)
    }).catch(() => {})
  }, [roleFilter, debouncedSearch, userPage])

  const fetchCounts = useCallback(() => {
    api.get('/admin/users/counts').then(r => setRoleCounts(r.data)).catch(() => {})
  }, [])

  useEffect(() => { fetchUsers() }, [fetchUsers])
  useEffect(() => { fetchCounts() }, [fetchCounts])
  const fetchDepartments = () => {
    api.get('/admin/departments').then(r => setDepartments(r.data)).catch(() => {})
    api.get('/admin/department-stats').then(r => setDeptStats(r.data)).catch(() => {})
  }

  const openAddUser = () => {
    setEditingUserId(null)
    setUserForm(EMPTY_USER)
    setShowUserModal(true)
  }

  const openEditUser = (u) => {
    setEditingUserId(u.id)
    setUserForm({
      email: u.email || '',
      password: '',                       // blank = keep current
      first_name: u.first_name || '',
      last_name: u.last_name || '',
      phone: u.phone || '',
      role: u.role,
      department_id: u.department_id || '',
      employee_id: u.employee_id || '',
      expertise_areas: Array.isArray(u.expertise_areas) ? u.expertise_areas.join(', ') : (u.expertise_areas || ''),
      years_experience: u.years_experience ?? '',
    })
    setShowUserModal(true)
  }

  const closeUserModal = () => {
    setShowUserModal(false)
    setUserForm(EMPTY_USER)
    setEditingUserId(null)
  }

  const handleSaveUser = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    try {
      if (editingUserId) {
        // Edit mode — only send supplied fields; empty ones are left unchanged.
        const payload = {
          first_name: userForm.first_name,
          last_name: userForm.last_name,
          email: userForm.email,
          phone: userForm.phone || null,
        }
        if (userForm.password) payload.password = userForm.password
        if (userForm.department_id) payload.department_id = userForm.department_id
        if (userForm.employee_id) payload.employee_id = userForm.employee_id
        if (userForm.role === 'interviewer') {
          if (userForm.expertise_areas) payload.expertise_areas = userForm.expertise_areas.split(',').map(s => s.trim())
          if (userForm.years_experience) payload.years_experience = parseInt(userForm.years_experience)
        }
        await api.put(`/admin/users/${editingUserId}`, payload)
      } else {
        const endpoint = userForm.role === 'hr' ? '/admin/users/hr' : '/admin/users/interviewer'
        const payload = {
          email: userForm.email,
          password: userForm.password,
          first_name: userForm.first_name,
          last_name: userForm.last_name,
          phone: userForm.phone || null,
          department_id: userForm.department_id,
          employee_id: userForm.employee_id,
        }
        if (userForm.role === 'interviewer') {
          payload.expertise_areas = userForm.expertise_areas ? userForm.expertise_areas.split(',').map(s => s.trim()) : []
          payload.years_experience = userForm.years_experience ? parseInt(userForm.years_experience) : null
        }
        await api.post(endpoint, payload)
      }
      fetchUsers()
      fetchCounts()
      closeUserModal()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to save user')
    }
    setSubmitting(false)
  }

  const handleAddDept = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    try {
      await api.post('/admin/departments', deptForm)
      fetchDepartments()
      setShowDeptModal(false)
      setDeptForm(EMPTY_DEPT)
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to create department')
    }
    setSubmitting(false)
  }

  const handleToggleUser = async (userId, currentActive) => {
    if (!confirm(`${currentActive ? 'Deactivate' : 'Activate'} this user?`)) return
    try {
      if (currentActive) {
        await api.delete(`/admin/users/${userId}`)
      } else {
        await api.put(`/admin/users/${userId}/status?is_active=true`)
      }
      fetchUsers()
      fetchCounts()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to update user')
    }
  }

  const handleDeleteDept = async (deptId) => {
    if (!confirm('Delete this department?')) return
    try {
      await api.delete(`/admin/departments/${deptId}`)
      fetchDepartments()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to delete department')
    }
  }

  const handleEditDept = async (dept) => {
    const newName = prompt('Department name:', dept.name)
    if (!newName || newName === dept.name) return
    const newDesc = prompt('Description:', dept.description || '')
    try {
      await api.put(`/admin/departments/${dept.id}?name=${encodeURIComponent(newName)}&description=${encodeURIComponent(newDesc || '')}`)
      fetchDepartments()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to update department')
    }
  }

  // Paging maths for the Users footer. `userTotal` is the server's count for the
  // active role + search, so these stay right no matter which category is open.
  const userRangeStart = userTotal === 0 ? 0 : userPage * PAGE_SIZE + 1
  const userRangeEnd = Math.min((userPage + 1) * PAGE_SIZE, userTotal)
  const hasNextUserPage = (userPage + 1) * PAGE_SIZE < userTotal

  // Overview's "Users by Role" chart reads the counts endpoint, not `users` —
  // `users` now holds one filtered page, which would make the chart show only
  // whichever category was last opened.
  const roleChartData = ROLE_TABS
    .filter(t => t.key !== 'all')
    .map(t => ({ name: t.label, value: roleCounts?.[t.key] ?? 0 }))
    .filter(d => d.value > 0)

  return (
    <>
      <Header title="Admin Console" subtitle="System overview & management" />

{tab === 'overview' && (
        <>
          <div className="stats-grid">
            {[
              { label: 'Total Users', value: analytics?.total_users ?? roleCounts?.all ?? 0, icon: Users, cls: 'amber' },
              { label: 'Departments', value: departments.length, icon: Building2, cls: 'blue' },
              { label: 'Active Jobs', value: analytics?.active_jobs ?? 0, icon: Briefcase, cls: 'green' },
              { label: 'Total Candidates', value: analytics?.total_candidates ?? 0, icon: TrendingUp, cls: 'purple' },
            ].map((s, i) => (
              <div key={i} className={`stat-card ${s.cls}`} style={{ animationDelay: `${i * 80}ms` }}>
                <div className="stat-label">{s.label}</div>
                <div className="stat-value">{s.value.toLocaleString()}</div>
              </div>
            ))}
          </div>

          <div className="grid-3 mb-24">
            <div className="card">
              <h3 style={{ marginBottom: 20 }}>Candidates by Stage</h3>
              {(analytics?.candidates_by_stage?.length > 0) ? (
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={analytics.candidates_by_stage} barSize={32}>
                    <XAxis dataKey="stage" tick={{ fill: '#9ca3af', fontSize: 11, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} />
                    <YAxis hide />
                    <Tooltip content={<CustomTooltip />} />
                    <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                      {analytics.candidates_by_stage.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} fillOpacity={0.8} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ height: 240, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)' }}>No candidate data yet</div>
              )}
            </div>

            <div className="card">
              <h3 style={{ marginBottom: 20 }}>Users by Role</h3>
              {roleChartData.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <Pie
                        data={roleChartData}
                        cx="50%" cy="50%" innerRadius={55} outerRadius={90}
                        dataKey="value" stroke="var(--bg-primary)" strokeWidth={3} paddingAngle={3}>
                        {roleChartData.map((_, i) => (
                          <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip content={<CustomTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex gap-12" style={{ justifyContent: 'center', flexWrap: 'wrap', marginTop: 8 }}>
                    {roleChartData.map((d, i) => (
                      <div key={d.name} className="flex-center gap-8" style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                        <div style={{ width: 8, height: 8, borderRadius: 2, background: CHART_COLORS[i % CHART_COLORS.length] }} />
                        {d.name} ({d.value})
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div style={{ height: 240, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)' }}>No user data yet</div>
              )}
            </div>

            <div className="card">
              <h3 style={{ marginBottom: 20 }}>Jobs by Department</h3>
              {deptStats.some(d => d.jobs > 0) ? (
                <>
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <Pie
                        data={deptStats.filter(d => d.jobs > 0).map(d => ({ name: d.name, value: d.jobs }))}
                        cx="50%" cy="50%" innerRadius={55} outerRadius={90}
                        dataKey="value" stroke="var(--bg-primary)" strokeWidth={3} paddingAngle={3}>
                        {deptStats.filter(d => d.jobs > 0).map((_, i) => (
                          <Cell key={i} fill={CHART_COLORS[(i + 2) % CHART_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip content={<CustomTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex gap-12" style={{ justifyContent: 'center', flexWrap: 'wrap', marginTop: 8 }}>
                    {deptStats.filter(d => d.jobs > 0).map((d, i) => (
                      <div key={d.name} className="flex-center gap-8" style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                        <div style={{ width: 8, height: 8, borderRadius: 2, background: CHART_COLORS[(i + 2) % CHART_COLORS.length] }} />
                        {d.name} ({d.jobs})
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div style={{ height: 240, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)' }}>No jobs data yet</div>
              )}
            </div>
          </div>

          {/* Gender + Age Charts Side by Side */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 24 }}>

            {/* Gender Distribution Pie Chart */}
            <div className="card" style={{ position: 'relative', overflow: 'hidden' }}>
              <div style={{
                position: 'absolute', top: -60, right: -60, width: 160, height: 160,
                borderRadius: '50%', background: 'radial-gradient(circle, rgba(43,124,186,0.06) 0%, transparent 70%)',
                pointerEvents: 'none'
              }} />

              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20, position: 'relative' }}>
                <div style={{
                  width: 36, height: 36, borderRadius: 9,
                  background: 'linear-gradient(135deg, rgba(43,124,186,0.14), rgba(43,124,186,0.04))',
                  display: 'grid', placeItems: 'center', border: '1px solid rgba(43,124,186,0.15)'
                }}>
                  <Users size={16} style={{ color: '#2b7cba' }} />
                </div>
                <div>
                  <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                    Gender Distribution
                  </h3>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                    {genderData.reduce((sum, d) => sum + d.count, 0)} candidates
                  </span>
                </div>
              </div>

              {genderData.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie
                        data={genderData}
                        cx="50%" cy="50%"
                        innerRadius={58} outerRadius={90}
                        dataKey="count" nameKey="gender"
                        stroke="var(--bg-primary)" strokeWidth={4}
                        paddingAngle={4}
                        animationBegin={200}
                        animationDuration={800}
                      >
                        {genderData.map((entry, i) => {
                          const colors = { 'Male': '#3b82f6', 'Female': '#2b7cba', 'Other': '#8b5cf6', 'Not_specified': '#6b7280' }
                          return <Cell key={i} fill={colors[entry.gender] || CHART_COLORS[i % CHART_COLORS.length]} />
                        })}
                      </Pie>
                      <Tooltip content={<CustomTooltip />} />
                    </PieChart>
                  </ResponsiveContainer>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
                    {genderData.map((entry, i) => {
                      const colors = { 'Male': '#3b82f6', 'Female': '#2b7cba', 'Other': '#8b5cf6', 'Not_specified': '#6b7280' }
                      const icons = { 'Male': '♂', 'Female': '♀', 'Other': '⚥', 'Not_specified': '—' }
                      const color = colors[entry.gender] || CHART_COLORS[i % CHART_COLORS.length]
                      const total = genderData.reduce((sum, d) => sum + d.count, 0)
                      const pct = total > 0 ? ((entry.count / total) * 100).toFixed(1) : 0
                      return (
                        <div key={entry.gender} style={{
                          display: 'flex', alignItems: 'center', gap: 12,
                          padding: '10px 14px', borderRadius: 10,
                          background: `linear-gradient(135deg, ${color}08 0%, ${color}03 100%)`,
                          border: `1px solid ${color}18`
                        }}>
                          <div style={{
                            width: 34, height: 34, borderRadius: 8,
                            background: `linear-gradient(135deg, ${color}20, ${color}08)`,
                            display: 'grid', placeItems: 'center',
                            fontSize: '1rem', fontWeight: 700, color
                          }}>
                            {icons[entry.gender] || '●'}
                          </div>
                          <div style={{ flex: 1 }}>
                            <div style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-primary)' }}>{entry.gender}</div>
                            <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                              {entry.count} candidate{entry.count !== 1 ? 's' : ''}
                            </div>
                          </div>
                          <div style={{ fontSize: '1.15rem', fontWeight: 800, color, fontFamily: 'var(--font-mono)' }}>{pct}%</div>
                        </div>
                      )
                    })}
                  </div>
                </>
              ) : (
                <div style={{ height: 280, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)', fontSize: '0.85rem', border: '2px dashed var(--border-subtle)', borderRadius: 14 }}>
                  No candidate gender data yet
                </div>
              )}
            </div>

            {/* Age Range Line Chart */}
            <div className="card" style={{ position: 'relative', overflow: 'hidden' }}>
              <div style={{
                position: 'absolute', inset: 0, pointerEvents: 'none',
                background: 'radial-gradient(ellipse at 10% 80%, rgba(6,182,212,0.04) 0%, transparent 50%), radial-gradient(ellipse at 90% 20%, rgba(139,92,246,0.04) 0%, transparent 50%)'
              }} />
              <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', opacity: 0.025 }}>
                <defs><pattern id="grid-dots" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="0.8" fill="currentColor" /></pattern></defs>
                <rect width="100%" height="100%" fill="url(#grid-dots)" />
              </svg>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, position: 'relative' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{
                    width: 36, height: 36, borderRadius: 9,
                    background: 'linear-gradient(135deg, #06b6d4 0%, #8b5cf6 100%)',
                    display: 'grid', placeItems: 'center',
                    boxShadow: '0 3px 12px rgba(6,182,212,0.25)'
                  }}>
                    <TrendingUp size={16} style={{ color: '#fff' }} />
                  </div>
                  <div>
                    <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700, letterSpacing: '-0.025em' }}>
                      Age Distribution
                    </h3>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                      {ageData.reduce((s, d) => s + d.count, 0)} candidates
                    </span>
                  </div>
                </div>
                {ageData.some(d => d.count > 0) && (() => {
                  const peak = ageData.reduce((max, d) => d.count > max.count ? d : max, { count: 0 })
                  return (
                    <div style={{
                      padding: '4px 10px', borderRadius: 16,
                      background: 'linear-gradient(135deg, rgba(6,182,212,0.1), rgba(139,92,246,0.1))',
                      border: '1px solid rgba(6,182,212,0.2)',
                      fontSize: '0.68rem', fontWeight: 600, fontFamily: 'var(--font-mono)', color: '#06b6d4'
                    }}>
                      Peak: {peak.range}
                    </div>
                  )
                })()}
              </div>

              {ageData.some(d => d.count > 0) ? (
                <div style={{ position: 'relative' }}>
                  <ResponsiveContainer width="100%" height={260}>
                    <AreaChart data={ageData} margin={{ top: 10, right: 16, left: -10, bottom: 0 }}>
                      <defs>
                        <linearGradient id="ageGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#06b6d4" stopOpacity={0.3} />
                          <stop offset="50%" stopColor="#8b5cf6" stopOpacity={0.15} />
                          <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.02} />
                        </linearGradient>
                        <linearGradient id="ageStroke" x1="0" y1="0" x2="1" y2="0">
                          <stop offset="0%" stopColor="#06b6d4" />
                          <stop offset="100%" stopColor="#8b5cf6" />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 6" stroke="var(--border-subtle)" strokeOpacity={0.5} vertical={false} />
                      <XAxis dataKey="range" tick={{ fill: '#9ca3af', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={{ stroke: 'var(--border-subtle)' }} tickLine={false} dy={6} />
                      <YAxis tick={{ fill: '#9ca3af', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} allowDecimals={false} dx={-4} />
                      <Tooltip content={({ active, payload, label }) => {
                        if (!active || !payload?.length) return null
                        return (
                          <div style={{
                            background: 'var(--surface-modal)', border: '1px solid var(--border-medium)',
                            borderRadius: 10, padding: '10px 14px', boxShadow: '0 8px 30px rgba(0,0,0,0.12)'
                          }}>
                            <div style={{ color: 'var(--text-tertiary)', fontSize: '0.68rem', fontFamily: 'var(--font-mono)', letterSpacing: '0.05em', textTransform: 'uppercase', marginBottom: 4 }}>Age Range</div>
                            <div style={{ fontSize: '1.1rem', fontWeight: 800, background: 'linear-gradient(135deg, #06b6d4, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', fontFamily: 'var(--font-mono)' }}>{label}</div>
                            <div style={{ marginTop: 4, fontSize: '0.78rem', color: 'var(--text-secondary)', fontWeight: 500 }}>{payload[0].value} candidate{payload[0].value !== 1 ? 's' : ''}</div>
                          </div>
                        )
                      }} />
                      <Area type="monotone" dataKey="count" stroke="url(#ageStroke)" strokeWidth={3} fill="url(#ageGradient)"
                        dot={{ r: 4, fill: '#fff', stroke: '#06b6d4', strokeWidth: 2.5 }}
                        activeDot={{ r: 6, fill: '#06b6d4', stroke: '#fff', strokeWidth: 3 }}
                        animationDuration={1200} animationEasing="ease-out"
                      />
                    </AreaChart>
                  </ResponsiveContainer>

                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 12, justifyContent: 'center' }}>
                    {ageData.filter(d => d.count > 0).map((d, i) => {
                      const total = ageData.reduce((s, x) => s + x.count, 0)
                      const pct = total > 0 ? ((d.count / total) * 100).toFixed(0) : 0
                      return (
                        <div key={d.range} style={{
                          padding: '4px 10px', borderRadius: 6, background: 'var(--bg-hover)',
                          border: '1px solid var(--border-subtle)', fontSize: '0.68rem',
                          fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', fontWeight: 500,
                          display: 'flex', alignItems: 'center', gap: 5
                        }}>
                          <span style={{ width: 6, height: 6, borderRadius: '50%', background: `hsl(${180 + i * 25}, 70%, 55%)` }} />
                          {d.range}: {d.count} ({pct}%)
                        </div>
                      )
                    })}
                  </div>
                </div>
              ) : (
                <div style={{ height: 280, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)', fontSize: '0.85rem', border: '2px dashed var(--border-subtle)', borderRadius: 14 }}>
                  No candidate age data yet
                </div>
              )}
            </div>

          </div>

          {analytics?.recent_applications?.length > 0 && (
            <div className="card">
              <h3 style={{ marginBottom: 16 }}>Recent Applications</h3>
              <div className="table-wrapper">
                <table>
                  <thead><tr><th>Candidate</th><th>Job</th><th>Stage</th><th>ATS Score</th></tr></thead>
                  <tbody>
                    {analytics.recent_applications.map((a, i) => (
                      <tr key={i}>
                        <td style={{ fontWeight: 500 }}>{a.name}</td>
                        <td>{a.job_title}</td>
                        <td><span className="badge badge-blue">{a.stage}</span></td>
                        <td className="mono">{a.ats_score != null ? `${a.ats_score}/100` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

        </>
      )}

      {tab === 'analytics' && (
        <>
          {/* HR Hiring Performance */}
          {hrComparison.length > 0 && (
            <div className="card" style={{ position: 'relative', overflow: 'hidden', marginBottom: 24 }}>
              {/* Background texture */}
              <div style={{
                position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.4,
                background: `
                  radial-gradient(ellipse at 0% 0%, rgba(43,124,186,0.06) 0%, transparent 40%),
                  radial-gradient(ellipse at 100% 100%, rgba(34,197,94,0.05) 0%, transparent 40%)
                `
              }} />
              <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', opacity: 0.02 }}>
                <defs>
                  <pattern id="hr-grid" width="24" height="24" patternUnits="userSpaceOnUse">
                    <path d="M24 0L0 0 0 24" fill="none" stroke="currentColor" strokeWidth="0.5" />
                  </pattern>
                </defs>
                <rect width="100%" height="100%" fill="url(#hr-grid)" />
              </svg>

              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 28, position: 'relative' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                  <div style={{
                    width: 44, height: 44, borderRadius: 12,
                    background: 'linear-gradient(135deg, #2b7cba 0%, #22c55e 100%)',
                    display: 'grid', placeItems: 'center',
                    boxShadow: '0 4px 16px rgba(43,124,186,0.3)'
                  }}>
                    <Briefcase size={20} style={{ color: '#fff' }} />
                  </div>
                  <div>
                    <h3 style={{ margin: 0, fontSize: '1.15rem', fontWeight: 800, letterSpacing: '-0.03em' }}>
                      HR Hiring Performance
                    </h3>
                    <span style={{ fontSize: '0.73rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', letterSpacing: '0.02em' }}>
                      {hrComparison.length} HR personnel tracked
                    </span>
                  </div>
                </div>
                <div style={{
                  padding: '5px 12px', borderRadius: 16,
                  background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)',
                  fontSize: '0.7rem', fontWeight: 600, fontFamily: 'var(--font-mono)', color: '#22c55e'
                }}>
                  {hrComparison.reduce((s, h) => s + h.offers, 0)} total offers
                </div>
              </div>

              {/* Stacked bar comparison chart */}
              <ResponsiveContainer width="100%" height={320}>
                <BarChart data={hrComparison} margin={{ top: 10, right: 20, left: -10, bottom: 0 }} barGap={6}>
                  <defs>
                    <linearGradient id="barJobs" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#2b7cba" stopOpacity={0.9} />
                      <stop offset="100%" stopColor="#2b7cba" stopOpacity={0.6} />
                    </linearGradient>
                    <linearGradient id="barCandidates" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.9} />
                      <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.6} />
                    </linearGradient>
                    <linearGradient id="barInterviews" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.9} />
                      <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.6} />
                    </linearGradient>
                    <linearGradient id="barOffers" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#22c55e" stopOpacity={0.9} />
                      <stop offset="100%" stopColor="#22c55e" stopOpacity={0.6} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="4 8" stroke="var(--border-subtle)" strokeOpacity={0.4} vertical={false} />
                  <XAxis
                    dataKey="name" tick={{ fill: '#9ca3af', fontSize: 11, fontFamily: 'JetBrains Mono' }}
                    axisLine={{ stroke: 'var(--border-subtle)' }} tickLine={false} dy={8}
                  />
                  <YAxis tick={{ fill: '#9ca3af', fontSize: 10, fontFamily: 'JetBrains Mono' }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    content={({ active, payload, label }) => {
                      if (!active || !payload?.length) return null
                      const data = payload[0]?.payload
                      return (
                        <div style={{
                          background: 'var(--surface-modal)', border: '1px solid var(--border-medium)',
                          borderRadius: 14, padding: '16px 20px', minWidth: 200,
                          boxShadow: '0 16px 48px rgba(0,0,0,0.18)', backdropFilter: 'blur(16px)'
                        }}>
                          <div style={{
                            fontWeight: 700, fontSize: '0.95rem', color: 'var(--text-primary)',
                            marginBottom: 10, paddingBottom: 8, borderBottom: '1px solid var(--border-subtle)'
                          }}>{label}</div>
                          {[
                            { label: 'Jobs Created', value: data?.jobs, color: '#2b7cba', icon: '◆' },
                            { label: 'Candidates', value: data?.candidates, color: '#3b82f6', icon: '●' },
                            { label: 'Interviews', value: data?.interviews, color: '#8b5cf6', icon: '▲' },
                            { label: 'Offers Made', value: data?.offers, color: '#22c55e', icon: '★' },
                          ].map(item => (
                            <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0' }}>
                              <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                                <span style={{ color: item.color, fontSize: '0.6rem' }}>{item.icon}</span>
                                {item.label}
                              </span>
                              <span style={{ fontWeight: 700, fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: item.color }}>{item.value}</span>
                            </div>
                          ))}
                          {data?.avg_ats > 0 && (
                            <div style={{
                              marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border-subtle)',
                              display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem'
                            }}>
                              <span style={{ color: 'var(--text-tertiary)' }}>Avg ATS Score</span>
                              <span style={{ fontWeight: 700, fontFamily: 'var(--font-mono)', color: data.avg_ats >= 70 ? '#22c55e' : '#2b7cba' }}>
                                {data.avg_ats}/100
                              </span>
                            </div>
                          )}
                        </div>
                      )
                    }}
                  />
                  <Bar dataKey="jobs" fill="url(#barJobs)" radius={[4, 4, 0, 0]} barSize={20} />
                  <Bar dataKey="candidates" fill="url(#barCandidates)" radius={[4, 4, 0, 0]} barSize={20} />
                  <Bar dataKey="interviews" fill="url(#barInterviews)" radius={[4, 4, 0, 0]} barSize={20} />
                  <Bar dataKey="offers" fill="url(#barOffers)" radius={[4, 4, 0, 0]} barSize={20} />
                </BarChart>
              </ResponsiveContainer>

              {/* Legend */}
              <div style={{ display: 'flex', gap: 20, justifyContent: 'center', marginTop: 16, flexWrap: 'wrap' }}>
                {[
                  { label: 'Jobs Created', color: '#2b7cba', icon: '◆' },
                  { label: 'Candidates', color: '#3b82f6', icon: '●' },
                  { label: 'Interviews', color: '#8b5cf6', icon: '▲' },
                  { label: 'Offers Made', color: '#22c55e', icon: '★' },
                ].map(item => (
                  <div key={item.label} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    fontSize: '0.75rem', color: 'var(--text-secondary)', fontWeight: 500
                  }}>
                    <span style={{
                      width: 10, height: 10, borderRadius: 3, background: item.color, flexShrink: 0,
                      boxShadow: `0 2px 6px ${item.color}40`
                    }} />
                    {item.label}
                  </div>
                ))}
              </div>

              {/* HR Scorecards */}
              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(hrComparison.length, 4)}, 1fr)`, gap: 12, marginTop: 24 }}>
                {hrComparison.map((hr, i) => {
                  const conversionRate = hr.candidates > 0 ? ((hr.offers / hr.candidates) * 100).toFixed(1) : 0
                  const accentColors = ['#2b7cba', '#3b82f6', '#8b5cf6', '#22c55e', '#06b6d4', '#ef4444']
                  const accent = accentColors[i % accentColors.length]
                  return (
                    <div key={hr.name} style={{
                      padding: '18px 16px', borderRadius: 14, position: 'relative', overflow: 'hidden',
                      background: `linear-gradient(135deg, ${accent}06, ${accent}02)`,
                      border: `1px solid ${accent}18`,
                    }}>
                      {/* Top accent bar */}
                      <div style={{
                        position: 'absolute', top: 0, left: 0, right: 0, height: 3,
                        background: `linear-gradient(90deg, ${accent}, ${accent}60)`
                      }} />

                      <div style={{ fontWeight: 700, fontSize: '0.88rem', color: 'var(--text-primary)', marginBottom: 4 }}>
                        {hr.name}
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px', marginTop: 12 }}>
                        {[
                          { label: 'Jobs', value: hr.jobs },
                          { label: 'Candidates', value: hr.candidates },
                          { label: 'Interviews', value: hr.interviews },
                          { label: 'Offers', value: hr.offers },
                        ].map(stat => (
                          <div key={stat.label}>
                            <div style={{ fontSize: '0.65rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                              {stat.label}
                            </div>
                            <div style={{ fontSize: '1.15rem', fontWeight: 800, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>
                              {stat.value}
                            </div>
                          </div>
                        ))}
                      </div>

                      {/* Conversion funnel bar */}
                      <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${accent}15` }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                          <span style={{ fontSize: '0.65rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                            Hire Rate
                          </span>
                          <span style={{ fontSize: '0.82rem', fontWeight: 800, color: accent, fontFamily: 'var(--font-mono)' }}>
                            {conversionRate}%
                          </span>
                        </div>
                        <div style={{ height: 6, borderRadius: 3, background: `${accent}12`, overflow: 'hidden' }}>
                          <div style={{
                            height: '100%', borderRadius: 3, width: `${Math.min(conversionRate, 100)}%`,
                            background: `linear-gradient(90deg, ${accent}, ${accent}cc)`,
                            transition: 'width 1s ease-out'
                          }} />
                        </div>
                        {hr.avg_ats > 0 && (
                          <div style={{
                            marginTop: 8, display: 'flex', justifyContent: 'space-between',
                            fontSize: '0.65rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)'
                          }}>
                            <span>AVG ATS</span>
                            <span style={{ fontWeight: 700, color: hr.avg_ats >= 70 ? '#22c55e' : '#2b7cba' }}>{hr.avg_ats}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* ═══ 13-Stage Recruitment Pipeline ═══ */}
          <div className="card" style={{ position: 'relative', overflow: 'hidden' }}>
            {/* Background */}
            <div style={{
              position: 'absolute', inset: 0, pointerEvents: 'none',
              background: 'radial-gradient(ellipse at 20% 0%, rgba(43,124,186,0.04) 0%, transparent 50%), radial-gradient(ellipse at 80% 100%, rgba(77,201,168,0.04) 0%, transparent 50%)'
            }} />

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 32, position: 'relative' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <div style={{
                  width: 44, height: 44, borderRadius: 12,
                  background: 'linear-gradient(135deg, #0f1b2d 0%, #2b7cba 100%)',
                  display: 'grid', placeItems: 'center',
                  boxShadow: '0 4px 16px rgba(43,124,186,0.3)'
                }}>
                  <TrendingUp size={20} style={{ color: '#fff' }} />
                </div>
                <div>
                  <h3 style={{ margin: 0, fontSize: '1.15rem', fontWeight: 800, letterSpacing: '-0.03em' }}>
                    AI Recruitment Pipeline
                  </h3>
                  <span style={{ fontSize: '0.73rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                    13-stage end-to-end hiring workflow
                  </span>
                </div>
              </div>
              <div style={{
                padding: '5px 12px', borderRadius: 16,
                background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)',
                fontSize: '0.7rem', fontWeight: 600, fontFamily: 'var(--font-mono)', color: '#22c55e'
              }}>
                13/13 stages active
              </div>
            </div>

            {/* Pipeline visualization */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 0, position: 'relative' }}>
              {[
                { stage: 1, title: 'Job Creation & Posting', desc: 'Admin/HR create and publish job openings', icon: '📋', color: '#2b7cba', api: 'POST /hr/jobs' },
                { stage: 2, title: 'Career Page & Application', desc: 'Candidates browse jobs and submit resumes', icon: '🌐', color: '#3b82f6', api: 'POST /candidate/jobs/{id}/apply' },
                { stage: 3, title: 'ATS Resume Scoring', desc: 'AI scores resumes: skills, semantic, experience, keywords', icon: '🤖', color: '#8b5cf6', api: 'POST /ats/score' },
                { stage: 4, title: 'Candidate Shortlisting', desc: 'Top N candidates selected by ATS rank', icon: '📊', color: '#6366f1', api: 'POST /hr/jobs/{id}/select-candidates' },
                { stage: 5, title: 'AI Test Creation', desc: 'Gemini generates MCQ, coding & descriptive tests', icon: '🧪', color: '#06b6d4', api: 'POST /interviews/{id}/generate-questions' },
                { stage: 6, title: 'Test Invitation', desc: 'Shortlisted candidates receive test credentials', icon: '✉️', color: '#0891b2', api: 'POST /hr/jobs/{id}/send-test-invite' },
                { stage: 7, title: 'Candidate Test', desc: 'Candidates login and complete AI-generated assessment', icon: '👨‍💻', color: '#14b8a6', api: 'POST /candidate/interviews/{id}/submit' },
                { stage: 8, title: 'Test Result Ranking', desc: 'Auto-grading with combined ATS + test scores', icon: '🏆', color: '#10b981', api: 'GET /hr/test-results' },
                { stage: 9, title: 'HR Screening Round', desc: 'Structured call: salary, notice period, culture fit', icon: '📞', color: '#22c55e', api: 'POST /hr/interviews' },
                { stage: 10, title: 'Technical Interview', desc: 'Deep-dive technical assessment by interviewer', icon: '💻', color: '#84cc16', api: 'GET /interviewer/interviews' },
                { stage: 11, title: 'Interview Feedback', desc: 'Structured scoring: technical, problem-solving, communication', icon: '📝', color: '#eab308', api: 'POST /interviewer/{id}/feedback' },
                { stage: 12, title: 'Offer Letter', desc: 'Salary, benefits, terms prepared and sent', icon: '💼', color: '#f59e0b', api: 'POST /hr/offers' },
                { stage: 13, title: 'Candidate Hired', desc: 'Offer accepted, onboarding initiated', icon: '🎉', color: '#ef4444', api: 'POST /offers/{id}/respond' },
              ].map((s, i, arr) => (
                <div key={s.stage} style={{ display: 'flex', alignItems: 'stretch', gap: 0 }}>
                  {/* Timeline column */}
                  <div style={{ width: 56, display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
                    {/* Node */}
                    <div style={{
                      width: 36, height: 36, borderRadius: '50%',
                      background: `linear-gradient(135deg, ${s.color}, ${s.color}cc)`,
                      display: 'grid', placeItems: 'center',
                      fontSize: '0.85rem', fontWeight: 800, color: '#fff',
                      fontFamily: 'var(--font-mono)',
                      boxShadow: `0 3px 12px ${s.color}40`,
                      flexShrink: 0, position: 'relative', zIndex: 2
                    }}>
                      {s.stage}
                    </div>
                    {/* Connector line */}
                    {i < arr.length - 1 && (
                      <div style={{
                        width: 2, flex: 1, minHeight: 12,
                        background: `linear-gradient(180deg, ${s.color}60, ${arr[i + 1].color}60)`
                      }} />
                    )}
                  </div>

                  {/* Content card */}
                  <div style={{
                    flex: 1, padding: '12px 18px', marginBottom: i < arr.length - 1 ? 4 : 0,
                    borderRadius: 10, position: 'relative',
                    background: `linear-gradient(135deg, ${s.color}06, transparent)`,
                    border: `1px solid ${s.color}12`,
                    transition: 'all 0.2s ease',
                    cursor: 'default'
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = `linear-gradient(135deg, ${s.color}10, ${s.color}04)`; e.currentTarget.style.borderColor = `${s.color}25` }}
                  onMouseLeave={e => { e.currentTarget.style.background = `linear-gradient(135deg, ${s.color}06, transparent)`; e.currentTarget.style.borderColor = `${s.color}12` }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={{ fontSize: '1.1rem' }}>{s.icon}</span>
                      <div style={{ fontWeight: 700, fontSize: '0.88rem', color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
                        {s.title}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {/* Footer summary */}
            <div style={{
              display: 'flex', gap: 16, marginTop: 28, padding: '16px 0 0',
              borderTop: '1px solid var(--border-subtle)', justifyContent: 'center', flexWrap: 'wrap'
            }}>
              {[
                { label: 'AI Components', value: '4', desc: 'ATS, Questions, Grading, Ranking' },
                { label: 'User Roles', value: '4', desc: 'Admin, HR, Interviewer, Candidate' },
                { label: 'Automation', value: '8', desc: 'Parsing, Scoring, Emails, Grading...' },
              ].map(item => (
                <div key={item.label} style={{
                  textAlign: 'center', padding: '10px 20px', borderRadius: 10,
                  background: 'var(--bg-hover)', border: '1px solid var(--border-subtle)', minWidth: 160
                }}>
                  <div style={{ fontSize: '1.5rem', fontWeight: 800, color: '#2b7cba', fontFamily: 'var(--font-mono)' }}>{item.value}</div>
                  <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--text-primary)', marginTop: 2 }}>{item.label}</div>
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>{item.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {tab === 'users' && (
        <div className="card">
          {/* Pick a role to filter the table below. Counts come from
              GET /admin/users/counts, so they describe the whole company rather than
              the current page. */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}
               role="group" aria-label="Filter users by role">
            {ROLE_TABS.map(t => (
              <RoleCard
                key={t.key}
                role={t}
                // null, not 0, until the counts land: a flash of zero reads as an empty
                // system rather than as "still loading".
                count={roleCounts ? (roleCounts[t.key] ?? 0) : null}
                selected={roleFilter === t.key}
                onSelect={setRoleFilter}
              />
            ))}
          </div>
          <div className="flex-between mb-16">
            <div className="flex-center gap-8" style={{ background: 'var(--surface-input)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: '0 12px', width: 260 }}>
              <Search size={16} style={{ color: 'var(--text-tertiary)' }} />
              <input className="input" style={{ border: 'none', background: 'none', padding: '8px 0' }}
                placeholder="Search users..." value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <button className="btn btn-primary btn-sm" onClick={openAddUser}><Plus size={14} /> Add User</button>
          </div>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id}>
                    <td style={{ fontWeight: 500 }}>{u.name || `${u.first_name || ''} ${u.last_name || ''}`.trim()}</td>
                    <td className="mono" style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>{u.email}</td>
                    <td><span className={`badge badge-${u.role === 'admin' ? 'red' : u.role === 'hr' ? 'amber' : u.role === 'interviewer' ? 'blue' : 'green'}`}>{u.role}</span></td>
                    <td><span className={`badge ${u.is_active ? 'badge-green' : 'badge-neutral'}`}>{u.is_active ? 'Active' : 'Inactive'}</span></td>
                    <td>
                      <div className="flex gap-8">
                        {(u.role === 'hr' || u.role === 'interviewer') && (
                          <button className="btn btn-ghost btn-sm"
                            onClick={() => openEditUser(u)}
                            title="Edit details / reset password">
                            <Edit3 size={14} /> Edit
                          </button>
                        )}
                        <button className="btn btn-ghost btn-sm"
                          onClick={() => handleToggleUser(u.id, u.is_active)}
                          title={u.is_active ? 'Deactivate' : 'Activate'}>
                          {u.is_active ? <Trash2 size={14} /> : <Edit3 size={14} />}
                          {u.is_active ? 'Deactivate' : 'Activate'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {users.length === 0 && (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>
              {debouncedSearch
                ? `No ${roleFilter === 'all' ? 'users' : roleFilter + 's'} match "${debouncedSearch}".`
                : roleFilter === 'all' ? 'No users yet.' : `No ${roleFilter} accounts yet.`}
            </div>
          )}

          {userTotal > 0 && (
            <div className="flex-between" style={{ marginTop: 16 }}>
              <div style={{ color: 'var(--text-tertiary)', fontSize: '0.8rem' }}>
                Showing {userRangeStart}–{userRangeEnd} of {userTotal}
              </div>
              <div className="flex gap-8">
                <button className="btn btn-ghost btn-sm" disabled={userPage === 0}
                  onClick={() => setUserPage(p => Math.max(0, p - 1))}>Previous</button>
                <button className="btn btn-ghost btn-sm" disabled={!hasNextUserPage}
                  onClick={() => setUserPage(p => p + 1)}>Next</button>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'departments' && (
        <>
          {/* Donut Charts Row */}
          {deptStats.length > 0 && (
            <div className="grid-3 mb-24">
              {/* Employees by Department */}
              <div className="card">
                <h3 style={{ marginBottom: 16, fontSize: '0.95rem' }}>Employees by Dept</h3>
                {deptStats.some(d => d.employees > 0) ? (
                  <>
                    <ResponsiveContainer width="100%" height={200}>
                      <PieChart>
                        <Pie
                          data={deptStats.filter(d => d.employees > 0).map(d => ({ name: d.name, value: d.employees }))}
                          cx="50%" cy="50%" innerRadius={50} outerRadius={80}
                          dataKey="value" stroke="#fff" strokeWidth={3} paddingAngle={3}>
                          {deptStats.filter(d => d.employees > 0).map((_, i) => (
                            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip content={<CustomTooltip />} />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="flex gap-8" style={{ justifyContent: 'center', flexWrap: 'wrap', marginTop: 6 }}>
                      {deptStats.filter(d => d.employees > 0).map((d, i) => (
                        <div key={d.name} className="flex-center gap-8" style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>
                          <div style={{ width: 7, height: 7, borderRadius: 2, background: CHART_COLORS[i % CHART_COLORS.length] }} />
                          {d.name} ({d.employees})
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <div style={{ height: 200, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>No employees assigned yet</div>
                )}
              </div>

              {/* Jobs by Department */}
              <div className="card">
                <h3 style={{ marginBottom: 16, fontSize: '0.95rem' }}>Jobs by Dept</h3>
                {deptStats.some(d => d.jobs > 0) ? (
                  <>
                    <ResponsiveContainer width="100%" height={200}>
                      <PieChart>
                        <Pie
                          data={deptStats.filter(d => d.jobs > 0).map(d => ({ name: d.name, value: d.jobs }))}
                          cx="50%" cy="50%" innerRadius={50} outerRadius={80}
                          dataKey="value" stroke="#fff" strokeWidth={3} paddingAngle={3}>
                          {deptStats.filter(d => d.jobs > 0).map((_, i) => (
                            <Cell key={i} fill={CHART_COLORS[(i + 2) % CHART_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip content={<CustomTooltip />} />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="flex gap-8" style={{ justifyContent: 'center', flexWrap: 'wrap', marginTop: 6 }}>
                      {deptStats.filter(d => d.jobs > 0).map((d, i) => (
                        <div key={d.name} className="flex-center gap-8" style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>
                          <div style={{ width: 7, height: 7, borderRadius: 2, background: CHART_COLORS[(i + 2) % CHART_COLORS.length] }} />
                          {d.name} ({d.jobs})
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <div style={{ height: 200, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>No jobs created yet</div>
                )}
              </div>

              {/* Candidates by Department */}
              <div className="card">
                <h3 style={{ marginBottom: 16, fontSize: '0.95rem' }}>Candidates by Dept</h3>
                {deptStats.some(d => d.candidates > 0) ? (
                  <>
                    <ResponsiveContainer width="100%" height={200}>
                      <PieChart>
                        <Pie
                          data={deptStats.filter(d => d.candidates > 0).map(d => ({ name: d.name, value: d.candidates }))}
                          cx="50%" cy="50%" innerRadius={50} outerRadius={80}
                          dataKey="value" stroke="#fff" strokeWidth={3} paddingAngle={3}>
                          {deptStats.filter(d => d.candidates > 0).map((_, i) => (
                            <Cell key={i} fill={CHART_COLORS[(i + 4) % CHART_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip content={<CustomTooltip />} />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="flex gap-8" style={{ justifyContent: 'center', flexWrap: 'wrap', marginTop: 6 }}>
                      {deptStats.filter(d => d.candidates > 0).map((d, i) => (
                        <div key={d.name} className="flex-center gap-8" style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>
                          <div style={{ width: 7, height: 7, borderRadius: 2, background: CHART_COLORS[(i + 4) % CHART_COLORS.length] }} />
                          {d.name} ({d.candidates})
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <div style={{ height: 200, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>No candidates yet</div>
                )}
              </div>
            </div>
          )}

          {/* Department List */}
          <div className="card">
            <div className="flex-between mb-16">
              <h3>Departments ({departments.length})</h3>
            </div>
            {logoError && (
              <div style={{ padding: '10px 14px', marginBottom: 14, borderRadius: 8, background: '#fef2f2', border: '1px solid #fca5a5', color: '#dc2626', fontSize: '0.85rem' }}>
                {logoError}
              </div>
            )}
            {departments.length === 0 ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>No departments found</div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
                {departments.map((d, i) => {
                  const stat = deptStats.find(s => s.name === d.name) || {}
                  const accent = CHART_COLORS[i % CHART_COLORS.length]
                  const logo = d.logo_url
                  const initials = d.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
                  return (
                    <DepartmentCard
                      key={d.id}
                      dept={d}
                      stat={stat}
                      accent={accent}
                      logo={logo}
                      initials={initials}
                      onUploadLogo={saveLogo}
                      onRemoveLogo={removeLogo}
                      onEdit={handleEditDept}
                      onDelete={handleDeleteDept}
                    />
                  )
                })}
              </div>
            )}
          </div>
        </>
      )}

      {showUserModal && (
        <div className="modal-overlay" onClick={closeUserModal}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>{editingUserId ? 'Edit User' : 'Add User'}</h2>
            <form className="auth-form" onSubmit={handleSaveUser}>
              <div className="input-group">
                <label>Role</label>
                <select className="select" value={userForm.role} disabled={!!editingUserId} onChange={e => setUserForm({ ...userForm, role: e.target.value })}>
                  <option value="hr">HR</option>
                  <option value="interviewer">Interviewer</option>
                </select>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>First Name</label>
                  <input className="input" required value={userForm.first_name} onChange={e => setUserForm({ ...userForm, first_name: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Last Name</label>
                  <input className="input" required value={userForm.last_name} onChange={e => setUserForm({ ...userForm, last_name: e.target.value })} />
                </div>
              </div>
              <div className="input-group">
                <label>Email</label>
                <input className="input" type="email" required value={userForm.email} onChange={e => setUserForm({ ...userForm, email: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Password {editingUserId && <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(leave blank to keep current)</span>}</label>
                <input className="input" type="password" required={!editingUserId} placeholder={editingUserId ? '••••••••••••' : ''} value={userForm.password} onChange={e => setUserForm({ ...userForm, password: e.target.value })} />
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Phone</label>
                  <input className="input" value={userForm.phone} onChange={e => setUserForm({ ...userForm, phone: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Employee ID</label>
                  <input className="input" required={!editingUserId} placeholder={editingUserId ? '(unchanged if blank)' : ''} value={userForm.employee_id} onChange={e => setUserForm({ ...userForm, employee_id: e.target.value })} />
                </div>
              </div>
              <div className="input-group">
                <label>Department</label>
                <select className="select" required={!editingUserId} value={userForm.department_id} onChange={e => setUserForm({ ...userForm, department_id: e.target.value })}>
                  <option value="">{editingUserId ? 'Keep current department' : 'Select department...'}</option>
                  {departments.map(d => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              {userForm.role === 'interviewer' && (
                <>
                  <div className="input-group">
                    <label>Expertise Areas (comma-separated)</label>
                    <input className="input" placeholder="React, Node.js, System Design" value={userForm.expertise_areas} onChange={e => setUserForm({ ...userForm, expertise_areas: e.target.value })} />
                  </div>
                  <div className="input-group">
                    <label>Years of Experience</label>
                    <input className="input" type="number" value={userForm.years_experience} onChange={e => setUserForm({ ...userForm, years_experience: e.target.value })} />
                  </div>
                </>
              )}
              <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
                <button type="button" className="btn btn-secondary" onClick={closeUserModal}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={submitting}>{submitting ? 'Saving...' : (editingUserId ? 'Save Changes' : 'Create User')}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {tab === 'email' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr)', gap: 20, maxWidth: 760 }}>
          {/* Status banner */}
          <div className="card" style={{
            display: 'flex', alignItems: 'center', gap: 14,
            padding: '14px 18px',
            background: emailConfig?.configured
              ? 'linear-gradient(135deg, rgba(34,197,94,0.10), rgba(34,197,94,0.02))'
              : 'linear-gradient(135deg, rgba(245,158,11,0.10), rgba(245,158,11,0.02))',
            borderLeft: `3px solid ${emailConfig?.configured ? '#22c55e' : '#f59e0b'}`,
          }}>
            {emailConfig?.configured
              ? <CheckCircle2 size={20} style={{ color: '#22c55e' }} />
              : <AlertCircle size={20} style={{ color: '#f59e0b' }} />}
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>
                {emailConfig?.configured ? 'Company SMTP is configured' : 'No company SMTP configured'}
              </div>
              <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', marginTop: 2 }}>
                {emailConfig?.configured
                  ? `All emails are sent from ${emailConfig.smtp_from_email || emailConfig.smtp_user}`
                  : 'Until configured, emails are sent from the platform default address.'}
              </div>
            </div>
          </div>

          {/* Save form */}
          <form onSubmit={handleSaveEmailConfig} className="card" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <Mail size={18} style={{ color: 'var(--accent-primary)' }} />
              <h3 style={{ margin: 0, fontSize: '1rem' }}>SMTP Credentials</h3>
            </div>
            <p style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)', marginBottom: 20 }}>
              Use a Gmail account with an{' '}
              <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer"
                 style={{ color: 'var(--accent-primary)' }}>App Password</a>
              {' '}(not your regular password). Emails to candidates will come from this address.
            </p>

            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>
              <div className="input-group">
                <label>SMTP Host</label>
                <input className="input" required
                  value={emailForm.smtp_host}
                  onChange={e => setEmailForm({ ...emailForm, smtp_host: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Port</label>
                <input className="input" type="number" required
                  value={emailForm.smtp_port}
                  onChange={e => setEmailForm({ ...emailForm, smtp_port: e.target.value })} />
              </div>
            </div>

            <div className="input-group">
              <label>From Email (SMTP user)</label>
              <input className="input" type="email" required placeholder="hr@yourcompany.com"
                value={emailForm.smtp_user}
                onChange={e => setEmailForm({ ...emailForm, smtp_user: e.target.value })} />
            </div>

            <div className="input-group">
              <label>App Password {emailConfig?.has_password && <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(leave blank to keep existing)</span>}</label>
              <input className="input" type="password" placeholder={emailConfig?.has_password ? '••••••••••••••••' : 'xxxx xxxx xxxx xxxx'}
                value={emailForm.smtp_pass}
                onChange={e => setEmailForm({ ...emailForm, smtp_pass: e.target.value })} />
            </div>

            <div className="input-group">
              <label>Display From Email <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(optional — defaults to SMTP user)</span></label>
              <input className="input" type="email"
                value={emailForm.smtp_from_email}
                onChange={e => setEmailForm({ ...emailForm, smtp_from_email: e.target.value })} />
            </div>

            {emailMessage && (
              <div style={{
                marginTop: 8, padding: '10px 14px', borderRadius: 8, fontSize: '0.85rem',
                background: emailMessage.type === 'success' ? 'rgba(34,197,94,0.10)' : 'rgba(239,68,68,0.10)',
                color: emailMessage.type === 'success' ? '#16a34a' : '#dc2626',
              }}>
                {emailMessage.text}
              </div>
            )}

            <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
              {emailConfig?.configured && (
                <button type="button" className="btn btn-secondary" onClick={handleClearEmailConfig}>
                  Clear
                </button>
              )}
              <button type="submit" className="btn btn-primary" disabled={emailSaving}>
                {emailSaving ? 'Saving…' : 'Save Configuration'}
              </button>
            </div>
          </form>

          {/* Test email */}
          <div className="card" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <Send size={18} style={{ color: 'var(--accent-primary)' }} />
              <h3 style={{ margin: 0, fontSize: '1rem' }}>Send Test Email</h3>
            </div>
            <p style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)', marginBottom: 16 }}>
              Send a test message using {emailConfig?.configured ? 'this company\'s' : 'the global'} SMTP configuration.
            </p>
            <div style={{ display: 'flex', gap: 10 }}>
              <input className="input" type="email" placeholder="recipient@example.com" style={{ flex: 1 }}
                value={testTo} onChange={e => setTestTo(e.target.value)} />
              <button type="button" className="btn btn-primary" onClick={handleSendTestEmail} disabled={testing}>
                {testing ? 'Sending…' : 'Send Test'}
              </button>
            </div>
            {testResult && (
              <div style={{
                marginTop: 14, padding: 14, borderRadius: 10, fontSize: '0.85rem',
                background: testResult.sent ? 'rgba(34,197,94,0.10)' : 'rgba(239,68,68,0.10)',
                color: testResult.sent ? '#15803d' : '#b91c1c',
              }}>
                {testResult.sent ? (
                  <>
                    <b>Success:</b> Email delivered via {testResult.config?.smtp_host}
                    {testResult.using_company_smtp ? ' (company SMTP)' : ' (global default)'}.
                  </>
                ) : (
                  <>
                    <b>Failed{testResult.error_type ? ` (${testResult.error_type})` : ''}:</b> {testResult.error}
                  </>
                )}
              </div>
            )}
          </div>

          {/* Named sender mailboxes (HR picks these per pipeline step) */}
          <MailboxManager />

          {/* Read-only: which sender each step resolves to, and queued vs inline */}
          <MailRoutingPanel />
        </div>
      )}


      {tab === 'support' && (
        <SupportPanel />
      )}

    </>
  )
}
