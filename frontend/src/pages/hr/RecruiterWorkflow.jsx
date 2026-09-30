import { Fragment, useCallback, useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Plus, RefreshCw } from 'lucide-react'
import api from '../../api/client'
import MergeDialog from '../../components/workflow/MergeDialog'
import ConfirmDialog from '../../components/workflow/ConfirmDialog'

const TABS = [
  { key: 'pending', label: 'Pending actions' },
  { key: 'stages', label: 'Stages & owners' },
  { key: 'duplicates', label: 'Duplicates' },
  { key: 'sources', label: 'Sources' },
  { key: 'bulk', label: 'Bulk history' },
]

const fmt = (iso) => iso ? new Date(iso.endsWith('Z') ? iso : iso + 'Z').toLocaleString(undefined,
  { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'
const detail = (e, fallback) => {
  const d = e?.response?.data?.detail
  return typeof d === 'string' ? d : d?.message || fallback
}
const STATUS_STYLE = {
  overdue: { background: '#ef444418', color: '#dc2626', label: 'Overdue' },
  due_soon: { background: '#f59e0b18', color: '#b45309', label: 'Due soon' },
  on_track: { background: '#22c55e18', color: '#15803d', label: 'On track' },
}
const th = { textAlign: 'left', padding: '10px 14px', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-tertiary)' }
const td = { padding: '10px 14px', fontSize: '0.85rem', borderTop: '1px solid var(--border-subtle)' }

export default function RecruiterWorkflow({ onOpenCandidate }) {
  const [tab, setTab] = useState('pending')
  return (
    <div>
      <div className="flex-between mb-16">
        <div>
          <h3 style={{ fontSize: '1.2rem' }}>Recruiter Workflow</h3>
          <span className="text-sm text-muted">Deadlines, owners, duplicates, sources and the bulk-action audit trail</span>
        </div>
      </div>
      <div className="tabs" role="tablist" aria-label="Workflow sections">
        {TABS.map(t => (
          <button key={t.key} role="tab" aria-selected={tab === t.key}
            className={`tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      {tab === 'pending' && <PendingActions onOpenCandidate={onOpenCandidate} />}
      {tab === 'stages' && <StageSettings />}
      {tab === 'duplicates' && <Duplicates />}
      {tab === 'sources' && <Sources />}
      {tab === 'bulk' && <BulkHistory />}
    </div>
  )
}

// ─── pending actions ─────────────────────────────────────────────────────────

function PendingActions({ onOpenCandidate }) {
  const [scope, setScope] = useState('mine')
  const [status, setStatus] = useState('due')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setData(null); setError('')
    api.get('/hr/workflow/pending-actions', { params: { scope, status } })
      .then(r => setData(r.data)).catch(e => setError(detail(e, 'Could not load pending actions')))
  }, [scope, status])
  useEffect(() => { load() }, [load])

  return (
    <div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="select" aria-label="Whose actions" value={scope} onChange={e => setScope(e.target.value)} style={{ flex: '0 1 180px' }}>
          <option value="mine">Assigned to me</option>
          <option value="all">Everyone on my jobs</option>
        </select>
        <select className="select" aria-label="Deadline status" value={status} onChange={e => setStatus(e.target.value)} style={{ flex: '0 1 200px' }}>
          <option value="due">Overdue + due soon</option>
          <option value="overdue">Overdue only</option>
          <option value="all">All with a deadline</option>
        </select>
        <button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw size={14} /> Refresh</button>
        {data && (
          <span className="text-sm text-muted">
            {data.counts.overdue} overdue · {data.counts.due_soon} due soon
          </span>
        )}
      </div>
      {error && <div className="card" role="alert" style={{ color: '#dc2626' }}>{error}</div>}
      {!data && !error && <div className="card text-muted">Loading…</div>}
      {data && data.items.length === 0 && (
        <div className="card empty-state" style={{ padding: 40, textAlign: 'center' }}>
          <p>Nothing waiting.</p>
          <p className="text-sm text-muted">Deadlines come from Stages &amp; owners — a stage without a deadline never appears here.</p>
        </div>
      )}
      {data && data.items.length > 0 && (
        <div className="card table-wrapper" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Candidate</th><th style={th}>Job</th><th style={th}>Stage</th><th style={th}>Owner</th><th style={th}>Due</th><th style={th}>Status</th></tr></thead>
            <tbody>
              {data.items.map(i => {
                const st = STATUS_STYLE[i.status]
                return (
                  <tr key={i.candidate_id} style={{ cursor: onOpenCandidate ? 'pointer' : 'default' }}
                    onClick={() => onOpenCandidate?.(i.candidate_id)}>
                    <td style={td}><strong>{i.candidate_name}</strong></td>
                    <td style={td}>{i.job_title}</td>
                    <td style={td}>{i.stage_label}</td>
                    <td style={td}>{i.owner_name || <span className="text-muted">Unassigned</span>}
                      {i.owner_source === 'job' && <span className="text-muted" style={{ fontSize: '0.72rem' }}> (job owner)</span>}
                    </td>
                    <td style={td}>{fmt(i.due_at)}</td>
                    <td style={td}>
                      <span style={{ ...st, padding: '3px 10px', borderRadius: 20, fontSize: '0.75rem', fontWeight: 600 }}>
                        {st.label}{i.status === 'overdue' ? ` · ${Math.round(i.hours_overdue)}h` : ''}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── stages & owners ─────────────────────────────────────────────────────────

function StageSettings() {
  const [stages, setStages] = useState(null)
  const [owners, setOwners] = useState([])
  const [msg, setMsg] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/hr/workflow/stages').then(r => setStages(r.data.stages)).catch(() => setStages([]))
    api.get('/hr/workflow/stage-owners').then(r => setOwners(r.data.owners || [])).catch(() => {})
  }, [])

  const update = (idx, patch) => setStages(s => s.map((x, i) => i === idx ? { ...x, ...patch } : x))
  const move = (idx, dir) => setStages(s => {
    const next = [...s]; const j = idx + dir
    if (j < 0 || j >= next.length) return s
    ;[next[idx], next[j]] = [next[j], next[idx]]
    return next
  })
  const addStage = () => {
    const label = window.prompt('Name of the new stage (e.g. "Hiring manager review")')
    if (!label) return
    const key = label.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 50)
    if (!/^[a-z]/.test(key)) { setMsg({ error: 'A stage name must start with a letter.' }); return }
    if (stages.some(s => s.key === key)) { setMsg({ error: `A stage "${key}" already exists.` }); return }
    const at = Math.max(0, stages.findIndex(s => s.is_terminal))
    setStages(s => [...s.slice(0, at), { key, label, owner_user_id: null, sla_days: null, is_active: true, is_builtin: false, is_terminal: false }, ...s.slice(at)])
  }
  const save = async () => {
    setSaving(true); setMsg(null)
    try {
      const r = await api.put('/hr/workflow/stages', {
        stages: stages.map(s => ({ key: s.key, label: s.label, owner_user_id: s.owner_user_id || null,
          sla_days: s.sla_days === '' || s.sla_days === null ? null : Number(s.sla_days), is_active: s.is_active })),
      })
      setStages(r.data.stages); setMsg({ ok: 'Saved.' })
    } catch (e) { setMsg({ error: detail(e, 'Could not save') }) }
    setSaving(false)
  }

  if (!stages) return <div className="card text-muted">Loading…</div>
  return (
    <div>
      <p className="text-sm text-muted" style={{ marginBottom: 12 }}>
        A deadline puts candidates who sit in a stage too long on their owner's pending list, and the
        reminder sweep emails the owner once a day. With no owner set, the job's owning HR is responsible.
      </p>
      <div className="card table-wrapper" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Order</th><th style={th}>Stage</th><th style={th}>Owner</th><th style={th}>Deadline (days)</th><th style={th}>Active</th></tr></thead>
          <tbody>
            {stages.map((s, idx) => (
              <tr key={s.key} style={{ opacity: s.is_active ? 1 : 0.55 }}>
                <td style={td}>
                  <button className="btn btn-ghost btn-sm" aria-label={`Move ${s.label} up`} onClick={() => move(idx, -1)}><ArrowUp size={13} /></button>
                  <button className="btn btn-ghost btn-sm" aria-label={`Move ${s.label} down`} onClick={() => move(idx, 1)}><ArrowDown size={13} /></button>
                </td>
                <td style={td}>
                  <input className="input" aria-label={`Label for ${s.key}`} value={s.label} onChange={e => update(idx, { label: e.target.value })} style={{ minWidth: 160 }} />
                  <div className="text-muted" style={{ fontSize: '0.7rem', marginTop: 3 }}>{s.key}{s.is_builtin ? '' : ' · custom'}{s.is_terminal ? ' · final' : ''}</div>
                </td>
                <td style={td}>
                  <select className="select" aria-label={`Owner for ${s.label}`} value={s.owner_user_id || ''} onChange={e => update(idx, { owner_user_id: e.target.value || null })} disabled={s.is_terminal}>
                    <option value="">Job owner (default)</option>
                    {owners.map(o => <option key={o.id} value={o.id}>{o.name} ({o.role})</option>)}
                  </select>
                </td>
                <td style={td}>
                  <input className="input" type="number" min="1" max="365" aria-label={`Deadline for ${s.label}`} placeholder="None"
                    value={s.sla_days ?? ''} disabled={s.is_terminal}
                    onChange={e => update(idx, { sla_days: e.target.value === '' ? null : e.target.value })} style={{ width: 90 }} />
                </td>
                <td style={td}>
                  <input type="checkbox" aria-label={`${s.label} active`} checked={s.is_active} disabled={s.is_terminal}
                    onChange={e => update(idx, { is_active: e.target.checked })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 14, alignItems: 'center' }}>
        <button className="btn btn-secondary btn-sm" onClick={addStage}><Plus size={14} /> Add stage</button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save stages'}</button>
        {msg?.ok && <span style={{ color: '#16a34a' }}>{msg.ok}</span>}
        {msg?.error && <span role="alert" style={{ color: '#dc2626' }}>{msg.error}</span>}
      </div>
    </div>
  )
}

// ─── duplicates ──────────────────────────────────────────────────────────────

function Duplicates() {
  const [groups, setGroups] = useState(null)
  const [crossJob, setCrossJob] = useState(false)
  const [merge, setMerge] = useState(null)
  const [merges, setMerges] = useState([])
  const [undo, setUndo] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    setGroups(null)
    api.get('/hr/workflow/duplicates', { params: { include_cross_job: crossJob } })
      .then(r => setGroups(r.data.groups)).catch(() => setGroups([]))
    api.get('/hr/workflow/merges').then(r => setMerges(r.data.merges || [])).catch(() => {})
  }, [crossJob])
  useEffect(() => { load() }, [load])

  const doUndo = async () => {
    setBusy(true); setMsg('')
    try { await api.post(`/hr/workflow/merges/${undo.id}/undo`); setUndo(null); load() }
    catch (e) { setMsg(detail(e, 'Could not undo')); setUndo(null) }
    setBusy(false)
  }

  return (
    <div>
      <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, fontSize: '0.85rem' }}>
        <input type="checkbox" checked={crossJob} onChange={e => setCrossJob(e.target.checked)} />
        Also show the same person across different jobs (context only — those can't be merged)
      </label>
      {msg && <div role="alert" style={{ color: '#dc2626', marginBottom: 10 }}>{msg}</div>}
      {!groups && <div className="card text-muted">Scanning…</div>}
      {groups?.length === 0 && <div className="card empty-state" style={{ padding: 40, textAlign: 'center' }}>No likely duplicates.</div>}
      {groups?.map((g, gi) => (
        <div key={gi} className="card" style={{ marginBottom: 12, padding: 16 }}>
          <div className="flex-between" style={{ marginBottom: 8 }}>
            <span className="text-sm">
              Matched on <strong>{g.reasons.join(', ')}</strong>
              {g.confidence === 'weak' && <span className="badge badge-amber" style={{ marginLeft: 8 }}>weak — check before merging</span>}
              {!g.same_job && <span className="badge badge-neutral" style={{ marginLeft: 8 }}>different jobs</span>}
            </span>
          </div>
          {g.candidates.map((c, ci) => (
            <div key={c.candidate_id} className="flex-between" style={{ padding: '6px 0', borderTop: '1px solid var(--border-subtle)', gap: 8, flexWrap: 'wrap' }}>
              <div>
                <strong>{c.name}</strong> <span className="text-muted">· {c.email}{c.phone ? ` · ${c.phone}` : ''}</span>
                <div className="text-muted" style={{ fontSize: '0.75rem' }}>{c.job_title} · {(c.current_stage || '').replace(/_/g, ' ')} · applied {fmt(c.applied_at)}</div>
              </div>
              {g.same_job && ci > 0 && (
                <button className="btn btn-secondary btn-sm" onClick={() => setMerge({ primaryId: g.candidates[0].candidate_id, mergedId: c.candidate_id })}>
                  Merge into first…
                </button>
              )}
            </div>
          ))}
        </div>
      ))}

      {merges.length > 0 && (
        <>
          <h4 style={{ margin: '22px 0 10px' }}>Merge history</h4>
          <div className="card table-wrapper" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={th}>When</th><th style={th}>Reason</th><th style={th}>Moved</th><th style={th}></th></tr></thead>
              <tbody>
                {merges.map(m => (
                  <tr key={m.id}>
                    <td style={td}>{fmt(m.created_at)}</td>
                    <td style={td}>{m.reason || <span className="text-muted">—</span>}</td>
                    <td style={td}>{(m.moved?.interviews || []).length} interview(s), {(m.moved?.notes || []).length} note(s)</td>
                    <td style={td}>
                      {m.undone_at ? <span className="text-muted">Undone {fmt(m.undone_at)}</span>
                        : <button className="btn btn-ghost btn-sm" onClick={() => setUndo(m)}>Undo</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {merge && <MergeDialog {...merge} onClose={() => setMerge(null)} onMerged={() => { setMerge(null); load() }} />}
      {undo && (
        <ConfirmDialog title="Undo this merge?" confirmLabel="Undo merge" busy={busy} onConfirm={doUndo} onCancel={() => setUndo(null)}>
          Both applications are restored as they were before the merge, and moved interviews and notes go back.
        </ConfirmDialog>
      )}
    </div>
  )
}

// ─── sources ─────────────────────────────────────────────────────────────────

function Sources() {
  const [data, setData] = useState(null)
  useEffect(() => { api.get('/hr/workflow/sources/report').then(r => setData(r.data)).catch(() => setData({ sources: [], total: 0 })) }, [])
  if (!data) return <div className="card text-muted">Loading…</div>
  if (!data.total) return <div className="card empty-state" style={{ padding: 40, textAlign: 'center' }}>No applications yet.</div>
  return (
    <div className="card table-wrapper" style={{ padding: 0, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr>
          <th style={th}>Source</th><th style={th}>Applications</th><th style={th}>Shortlisted</th><th style={th}>Interviewed</th>
          <th style={th}>Offered</th><th style={th}>Hired</th><th style={th}>Hire rate</th><th style={th}>Top channels</th>
        </tr></thead>
        <tbody>
          {data.sources.map(s => (
            <tr key={s.source}>
              <td style={td}><strong>{s.label}</strong></td>
              <td style={td}>{s.total} <span className="text-muted">({Math.round(s.total / data.total * 100)}%)</span></td>
              <td style={td}>{s.shortlisted} <span className="text-muted">({s.shortlist_rate}%)</span></td>
              <td style={td}>{s.interviewed}</td>
              <td style={td}>{s.offered}</td>
              <td style={td}>{s.hired}</td>
              <td style={td}>{s.hire_rate}%</td>
              <td style={td}>{s.details.slice(0, 3).map(d => `${d.name} (${d.count})`).join(', ') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─── bulk history ────────────────────────────────────────────────────────────

function BulkHistory() {
  const [ops, setOps] = useState(null)
  const [open, setOpen] = useState(null)
  useEffect(() => { api.get('/hr/workflow/bulk').then(r => setOps(r.data.operations)).catch(() => setOps([])) }, [])
  const show = async (id) => {
    if (open?.id === id) { setOpen(null); return }
    const r = await api.get(`/hr/workflow/bulk/${id}`).catch(() => null)
    setOpen(r?.data || null)
  }
  if (!ops) return <div className="card text-muted">Loading…</div>
  if (!ops.length) return <div className="card empty-state" style={{ padding: 40, textAlign: 'center' }}>No bulk actions yet. Select candidates on the Pipeline to start one.</div>
  return (
    <div className="card table-wrapper" style={{ padding: 0, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr><th style={th}>When</th><th style={th}>Action</th><th style={th}>By</th><th style={th}>Result</th><th style={th}></th></tr></thead>
        <tbody>
          {ops.map(o => (
            <Fragment key={o.id}>
              <tr>
                <td style={td}>{fmt(o.executed_at || o.created_at)}</td>
                <td style={td}>{o.action_label}{o.params?.target_stage ? ` → ${o.params.target_stage.replace(/_/g, ' ')}` : ''}</td>
                <td style={td}>{o.created_by_name}</td>
                <td style={td}>{o.status === 'executed' ? `${o.affected_count} of ${o.requested_count} updated` : o.status}</td>
                <td style={td}><button className="btn btn-ghost btn-sm" onClick={() => show(o.id)}>{open?.id === o.id ? 'Hide' : 'Details'}</button></td>
              </tr>
              {open?.id === o.id && (
                <tr><td style={td} colSpan={5}>
                  <ul style={{ margin: '0 0 0 18px', fontSize: '0.8rem' }}>
                    {(open.result || open.preview?.eligible || []).map(r => (
                      <li key={r.candidate_id}>{r.name || r.candidate_id}: {r.outcome === 'skipped' ? r.reason : (r.to_stage ? `${(r.from_stage || '').replace(/_/g, ' ')} → ${r.to_stage.replace(/_/g, ' ')}` : r.outcome)}</li>
                    ))}
                  </ul>
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}
