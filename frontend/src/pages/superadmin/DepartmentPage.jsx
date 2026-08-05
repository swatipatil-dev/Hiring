import { useState, useEffect } from 'react'
import { Building2, Plus, Trash2, X, BarChart3, Layers } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import api from '../../api/client'

const FONTS = `@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap');`

const STYLES = `
  ${FONTS}
  .dp-root { font-family: 'Syne', sans-serif; padding: 32px 32px 60px; min-height: 100vh; background: #f4f6fb; position: relative; }
  .dp-dotgrid { position: fixed; inset: 0; background-image: radial-gradient(circle, rgba(0,0,0,0.045) 1px, transparent 1px); background-size: 28px 28px; pointer-events: none; z-index: 0; }
  .dp-glow { position: fixed; top: -200px; right: -100px; width: 600px; height: 600px; background: radial-gradient(circle, rgba(245,158,11,0.07) 0%, transparent 70%); pointer-events: none; z-index: 0; }
  .dp-content { position: relative; z-index: 1; }

  .dp-title { font-size: 1.45rem; font-weight: 800; color: #111827; letter-spacing: -0.02em; margin: 0 0 4px; }
  .dp-sub { font-family: 'DM Mono', monospace; font-size: 0.7rem; color: #9ca3af; letter-spacing: 0.08em; text-transform: uppercase; }
  .dp-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 28px; padding-bottom: 24px; border-bottom: 1px solid #e5e7eb; }
  .dp-header-left { display: flex; align-items: center; gap: 14px; }
  .dp-icon-wrap { width: 46px; height: 46px; border-radius: 14px; background: linear-gradient(135deg, #f59e0b, #d97706); display: grid; place-items: center; box-shadow: 0 0 20px rgba(245,158,11,0.25); }

  .dp-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 28px; }
  .dp-stat { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 18px 22px; position: relative; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
  .dp-stat::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: var(--accent); }
  .dp-stat-num { font-size: 2rem; font-weight: 800; color: #111827; line-height: 1; letter-spacing: -0.03em; }
  .dp-stat-lbl { font-family: 'DM Mono', monospace; font-size: 0.65rem; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.1em; margin-top: 5px; }

  .dp-grid { display: grid; grid-template-columns: 380px 1fr; gap: 24px; align-items: start; }

  .dp-card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 16px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
  .dp-card-bar { height: 3px; width: 100%; }
  .dp-card-body { padding: 24px; }
  .dp-card-title { font-size: 0.88rem; font-weight: 700; color: #111827; margin: 0 0 18px; display: flex; align-items: center; gap: 8px; }

  .dp-label { display: block; font-family: 'DM Mono', monospace; font-size: 0.63rem; font-weight: 500; color: #6b7280; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 6px; }
  .dp-input { width: 100%; padding: 10px 14px; border-radius: 9px; border: 1px solid #e5e7eb; background: #f9fafb; color: #111827; font-family: 'Syne', sans-serif; font-size: 0.875rem; outline: none; box-sizing: border-box; transition: border-color 0.2s, background 0.2s, box-shadow 0.2s; }
  .dp-input:focus { border-color: #f59e0b; background: #fff; box-shadow: 0 0 0 3px rgba(245,158,11,0.1); }
  .dp-input::placeholder { color: #c4c9d4; }
  .dp-field { margin-bottom: 14px; }

  .dp-select { width: 100%; padding: 10px 14px; border-radius: 9px; border: 1px solid #e5e7eb; background: #f9fafb; color: #111827; font-family: 'Syne', sans-serif; font-size: 0.875rem; outline: none; box-sizing: border-box; cursor: pointer; transition: border-color 0.2s; }
  .dp-select:focus { border-color: #f59e0b; }
  .dp-select option { background: #ffffff; color: #111827; }

  .dp-btn { width: 100%; padding: 11px; border-radius: 9px; border: none; background: linear-gradient(135deg, #f59e0b, #d97706); color: #fff; font-family: 'Syne', sans-serif; font-weight: 700; font-size: 0.85rem; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 7px; box-shadow: 0 4px 16px rgba(245,158,11,0.25); transition: all 0.2s; margin-top: 4px; }
  .dp-btn:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 6px 20px rgba(245,158,11,0.35); }
  .dp-btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .dp-error { margin-top: 10px; padding: 9px 12px; border-radius: 8px; background: #fef2f2; border: 1px solid #fecaca; color: #dc2626; font-family: 'DM Mono', monospace; font-size: 0.73rem; }

  .dp-dept-list { margin-top: 20px; border-top: 1px solid #f3f4f6; padding-top: 16px; }
  .dp-dept-list-title { font-family: 'DM Mono', monospace; font-size: 0.62rem; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 10px; }
  .dp-dept-item { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-radius: 9px; background: #f9fafb; border: 1px solid #f3f4f6; margin-bottom: 6px; transition: border-color 0.15s; }
  .dp-dept-item:hover { border-color: #e5e7eb; }
  .dp-dept-initial { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; font-family: 'DM Mono', monospace; font-size: 0.68rem; font-weight: 700; flex-shrink: 0; }
  .dp-dept-name { flex: 1; font-size: 0.84rem; font-weight: 600; color: #1f2937; }
  .dp-dept-desc { font-size: 0.7rem; color: #9ca3af; font-family: 'DM Mono', monospace; }
  .dp-dept-del { padding: 5px; border-radius: 6px; border: 1px solid #fecaca; background: #fef2f2; cursor: pointer; color: #dc2626; display: grid; place-items: center; flex-shrink: 0; transition: all 0.15s; }
  .dp-dept-del:hover { background: #fee2e2; border-color: #fca5a5; }
  .dp-empty { padding: 28px; text-align: center; color: #c4c9d4; font-family: 'DM Mono', monospace; font-size: 0.72rem; }

  .dp-chart-card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 16px; overflow: hidden; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
  .dp-chart-head { padding: 18px 22px 14px; border-bottom: 1px solid #f3f4f6; display: flex; align-items: center; gap: 10px; }
  .dp-chart-title { font-size: 0.85rem; font-weight: 700; color: #111827; }
  .dp-chart-body { padding: 20px 22px 16px; }

  .dp-co-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .dp-co-card { background: #f9fafb; border: 1px solid #f3f4f6; border-radius: 12px; padding: 14px 16px; transition: border-color 0.15s; }
  .dp-co-card:hover { border-color: #e5e7eb; }
  .dp-co-name { font-size: 0.85rem; font-weight: 700; color: #111827; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
  .dp-co-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .dp-co-count { font-family: 'DM Mono', monospace; font-size: 0.68rem; color: #9ca3af; margin-bottom: 10px; }
  .dp-co-tags { display: flex; flex-wrap: wrap; gap: 5px; }
  .dp-co-tag { padding: 3px 9px; border-radius: 20px; font-family: 'DM Mono', monospace; font-size: 0.63rem; background: #f3f4f6; border: 1px solid #e5e7eb; color: #6b7280; }

  .dp-loading { padding: 48px; text-align: center; font-family: 'DM Mono', monospace; font-size: 0.72rem; color: #c4c9d4; letter-spacing: 0.1em; text-transform: uppercase; }
`

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: 10, padding: '10px 14px', boxShadow: '0 4px 16px rgba(0,0,0,0.1)' }}>
      <div style={{ fontFamily: 'Syne', fontWeight: 700, color: '#111827', fontSize: '0.85rem' }}>{payload[0].payload.fullName}</div>
      <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '0.72rem', color: payload[0].payload.color, marginTop: 3 }}>
        {payload[0].value} department{payload[0].value !== 1 ? 's' : ''}
      </div>
    </div>
  )
}

export default function DepartmentPage() {
  const [companies, setCompanies] = useState([])
  const [allDepts, setAllDepts] = useState({})
  const [loadingAll, setLoadingAll] = useState(true)
  const [selectedId, setSelectedId] = useState('')
  const [form, setForm] = useState({ name: '', description: '' })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    // Load the platform's own org (Innomax) alongside the client companies, since
    // /superadmin/companies excludes the platform org. Innomax is listed first so
    // departments can be added to it here.
    Promise.all([
      api.get('/superadmin/own-company').then(r => r.data).catch(() => null),
      api.get('/superadmin/companies').then(r => r.data || []).catch(() => []),
    ])
      .then(async ([own, clients]) => {
        const map = {}
        const list = []
        if (own) {
          list.push({
            id: own.id,
            name: own.name,
            slug: own.slug,
            primary_color: own.primary_color,
          })
          map[own.id] = own.departments || []  // seeded inline — no extra request
        }
        list.push(...clients)
        setCompanies(list)
        if (list.length > 0) setSelectedId(list[0].id)

        // Fetch departments for the client companies (own already seeded above).
        const results = await Promise.all(
          clients.map(c =>
            api.get(`/superadmin/companies/${c.id}/departments`)
              .then(dr => ({ id: c.id, depts: dr.data || [] }))
              .catch(() => ({ id: c.id, depts: [] }))
          )
        )
        results.forEach(r => { map[r.id] = r.depts })
        setAllDepts(map)
      })
      .finally(() => setLoadingAll(false))
  }, [])

  const selectedCompany = companies.find(c => c.id === selectedId)
  const currentDepts = allDepts[selectedId] || []
  const totalDepts = Object.values(allDepts).reduce((s, d) => s + d.length, 0)
  const mostDepts = companies.reduce((best, c) => {
    const n = (allDepts[c.id] || []).length
    return n > best.n ? { name: c.name, n } : best
  }, { name: '—', n: 0 })

  const chartData = companies.map(c => ({
    name: c.name.length > 10 ? c.name.slice(0, 10) + '…' : c.name,
    fullName: c.name,
    departments: (allDepts[c.id] || []).length,
    color: c.primary_color || '#f59e0b',
  }))

  const handleAdd = async (e) => {
    e.preventDefault()
    if (!form.name.trim() || !selectedId) return
    setSubmitting(true)
    setError('')
    try {
      const r = await api.post(`/superadmin/companies/${selectedId}/departments`, {
        name: form.name.trim(),
        description: form.description.trim() || ''
      })
      setAllDepts(prev => ({
        ...prev,
        [selectedId]: [...(prev[selectedId] || []), r.data].sort((a, b) => a.name.localeCompare(b.name))
      }))
      setForm({ name: '', description: '' })
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create department')
    }
    setSubmitting(false)
  }

  const handleDelete = async (deptId) => {
    if (!selectedId) return
    await api.delete(`/superadmin/companies/${selectedId}/departments/${deptId}`).catch(() => {})
    setAllDepts(prev => ({
      ...prev,
      [selectedId]: (prev[selectedId] || []).filter(d => d.id !== deptId)
    }))
  }

  const accent = selectedCompany?.primary_color || '#f59e0b'

  return (
    <div className="dp-root">
      <style>{STYLES}</style>
      <div className="dp-dotgrid" />
      <div className="dp-glow" />

      <div className="dp-content">
        {/* Header */}
        <div className="dp-header">
          <div className="dp-header-left">
            <div className="dp-icon-wrap">
              <Building2 size={22} color="#fff" />
            </div>
            <div>
              <h1 className="dp-title">Department Management</h1>
              <div className="dp-sub">Create and manage departments per company</div>
            </div>
          </div>
        </div>

        {/* Stats */}
        <div className="dp-stats">
          <div className="dp-stat" style={{ '--accent': '#f59e0b' }}>
            <div className="dp-stat-num">{totalDepts}</div>
            <div className="dp-stat-lbl">Total Departments</div>
          </div>
          <div className="dp-stat" style={{ '--accent': '#60a5fa' }}>
            <div className="dp-stat-num">{companies.length}</div>
            <div className="dp-stat-lbl">Companies</div>
          </div>
          <div className="dp-stat" style={{ '--accent': '#00d48c' }}>
            <div className="dp-stat-num">{mostDepts.n}</div>
            <div className="dp-stat-lbl">Most (by {mostDepts.name})</div>
          </div>
        </div>

        {loadingAll ? (
          <div className="dp-loading">Loading department data…</div>
        ) : (
          <div className="dp-grid">

            {/* ── LEFT: Form ── */}
            <div>
              <div className="dp-card">
                <div className="dp-card-bar" style={{ background: `linear-gradient(90deg, ${accent}, ${accent}80)` }} />
                <div className="dp-card-body">
                  <div className="dp-card-title">
                    <Plus size={16} style={{ color: accent }} />
                    Add Department
                  </div>

                  <form onSubmit={handleAdd}>
                    <div className="dp-field">
                      <label className="dp-label">Company *</label>
                      <select
                        className="dp-select"
                        value={selectedId}
                        onChange={e => { setSelectedId(e.target.value); setError('') }}
                        style={{ borderColor: selectedId ? `${accent}40` : undefined }}
                      >
                        {companies.length === 0 && <option value="">No companies yet</option>}
                        {companies.map(c => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                    </div>

                    <div className="dp-field">
                      <label className="dp-label">Department Name *</label>
                      <input
                        className="dp-input"
                        placeholder="e.g. Engineering"
                        required
                        value={form.name}
                        onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
                      />
                    </div>

                    <div className="dp-field">
                      <label className="dp-label">Description</label>
                      <textarea
                        className="dp-input"
                        placeholder="Short description (optional)"
                        rows={3}
                        value={form.description}
                        onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
                        style={{ resize: 'vertical' }}
                      />
                    </div>

                    {error && <div className="dp-error">{error}</div>}

                    <button
                      type="submit"
                      className="dp-btn"
                      disabled={submitting || !selectedId || !form.name.trim()}
                      style={{ background: `linear-gradient(135deg, ${accent}, ${accent}cc)` }}
                    >
                      <Plus size={15} />
                      {submitting ? 'Creating…' : 'Create Department'}
                    </button>
                  </form>

                  {/* Department list for selected company */}
                  <div className="dp-dept-list">
                    <div className="dp-dept-list-title">
                      {currentDepts.length} Department{currentDepts.length !== 1 ? 's' : ''} in {selectedCompany?.name || '—'}
                    </div>
                    {currentDepts.length === 0 ? (
                      <div className="dp-empty">No departments yet — add one above</div>
                    ) : (
                      currentDepts.map(d => (
                        <div key={d.id} className="dp-dept-item">
                          <div
                            className="dp-dept-initial"
                            style={{ background: `${accent}18`, color: accent }}
                          >
                            {d.name.slice(0, 2).toUpperCase()}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div className="dp-dept-name">{d.name}</div>
                            {d.description && <div className="dp-dept-desc">{d.description}</div>}
                          </div>
                          <button className="dp-dept-del" onClick={() => handleDelete(d.id)}>
                            <Trash2 size={13} />
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* ── RIGHT: Analytics ── */}
            <div>
              {/* Bar chart */}
              <div className="dp-chart-card">
                <div className="dp-chart-head">
                  <BarChart3 size={16} style={{ color: '#f59e0b' }} />
                  <span className="dp-chart-title">Departments per Company</span>
                </div>
                <div className="dp-chart-body">
                  {chartData.length === 0 ? (
                    <div className="dp-empty">No data yet</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={chartData} barSize={32}>
                        <XAxis
                          dataKey="name"
                          tick={{ fill: '#9ca3af', fontSize: 11, fontFamily: 'DM Mono, monospace' }}
                          axisLine={false}
                          tickLine={false}
                        />
                        <YAxis
                          allowDecimals={false}
                          tick={{ fill: '#c4c9d4', fontSize: 11, fontFamily: 'DM Mono, monospace' }}
                          axisLine={false}
                          tickLine={false}
                          width={28}
                        />
                        <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(0,0,0,0.04)' }} />
                        <Bar dataKey="departments" radius={[6, 6, 0, 0]}>
                          {chartData.map((entry, i) => (
                            <Cell key={i} fill={entry.color} fillOpacity={selectedId === companies[i]?.id ? 1 : 0.55} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>

              {/* All companies overview */}
              <div className="dp-chart-card">
                <div className="dp-chart-head">
                  <Layers size={16} style={{ color: '#60a5fa' }} />
                  <span className="dp-chart-title">All Companies Overview</span>
                </div>
                <div className="dp-chart-body">
                  {companies.length === 0 ? (
                    <div className="dp-empty">No companies yet</div>
                  ) : (
                    <div className="dp-co-grid">
                      {companies.map(c => {
                        const depts = allDepts[c.id] || []
                        const color = c.primary_color || '#f59e0b'
                        return (
                          <div
                            key={c.id}
                            className="dp-co-card"
                            style={{
                              borderColor: selectedId === c.id ? `${color}40` : undefined,
                              cursor: 'pointer',
                            }}
                            onClick={() => setSelectedId(c.id)}
                          >
                            <div className="dp-co-name">
                              <span className="dp-co-dot" style={{ background: color }} />
                              {c.name}
                            </div>
                            <div className="dp-co-count">
                              {depts.length} department{depts.length !== 1 ? 's' : ''}
                            </div>
                            <div className="dp-co-tags">
                              {depts.length === 0 ? (
                                <span className="dp-co-tag" style={{ color: '#c4c9d4' }}>none yet</span>
                              ) : (
                                depts.slice(0, 4).map(d => (
                                  <span key={d.id} className="dp-co-tag" style={{ borderColor: `${color}30`, color: `${color}cc` }}>
                                    {d.name}
                                  </span>
                                ))
                              )}
                              {depts.length > 4 && (
                                <span className="dp-co-tag">+{depts.length - 4} more</span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Onboard modal rendered via SuperAdminDashboard — not needed here */}
    </div>
  )
}
