import { useState } from 'react'
import { UserCircle } from 'lucide-react'
import api from '../../../api/client'
import { useAuth } from '../../../context/AuthContext'
import { Card, CardHeader, Field, Notice, GRID_AUTOFIT } from './ProfileFields'

const fmtDate = (iso) => {
  if (!iso) return null
  const d = new Date(iso)
  return isNaN(d) ? null : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/**
 * Account details, editable in place.
 *
 * Inline rather than a modal: only three fields are editable and they already
 * live on this card, so a dialog would hide the values being changed. The page
 * also has no modal today, and index.css's .modal carries no focus trap, no
 * Escape handler and no scroll lock.
 */
export default function AccountDetailsCard({ me, onUpdated }) {
  const { setUserFromServer } = useAuth()
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState({ first_name: '', last_name: '', phone: '' })

  const profile = me?.profile

  const startEdit = () => {
    setForm({
      first_name: me.first_name || '',
      last_name: me.last_name || '',
      phone: me.phone || '',
    })
    setError('')
    setEditing(true)
  }

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const { data } = await api.put('/auth/me', {
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        phone: form.phone.trim(),
      })
      // Only the three keys that changed. setUserFromServer merges, so the
      // JWT-derived branding (logo, colours, slug) on the cached user survives.
      setUserFromServer?.({
        first_name: data.first_name,
        last_name: data.last_name,
        phone: data.phone,
      })
      onUpdated?.(data)
      setEditing(false)
    } catch (err) {
      const status = err.response?.status
      setError(
        err.response?.data?.detail
        || (status === 422 ? 'Please check the values and try again.' : 'Could not save your changes.')
      )
    }
    setSaving(false)
  }

  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }))

  return (
    <Card>
      <CardHeader
        icon={UserCircle}
        title="Account details"
        action={editing ? (
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </button>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        ) : (
          <button className="btn btn-secondary btn-sm" onClick={startEdit}>Edit</button>
        )}
      />

      <div style={GRID_AUTOFIT}>
        {editing ? (
          <>
            <label className="mp-edit-field">
              <span>First name</span>
              <input className="input" value={form.first_name} onChange={set('first_name')} autoFocus />
            </label>
            <label className="mp-edit-field">
              <span>Last name</span>
              <input className="input" value={form.last_name} onChange={set('last_name')} />
            </label>
            <label className="mp-edit-field">
              <span>Phone</span>
              <input className="input" value={form.phone} onChange={set('phone')}
                     placeholder="+91 98765 43210" inputMode="tel" />
            </label>
          </>
        ) : (
          <>
            <Field label="First name" value={me.first_name} />
            <Field label="Last name" value={me.last_name} />
            <Field label="Phone" value={me.phone} />
          </>
        )}

        {/* Read-only either way, so the card keeps its shape in edit mode. */}
        <Field label="Gender" value={me.gender} />
        <Field label="Date of birth" value={fmtDate(me.date_of_birth)} />
        <Field label="Member since" value={fmtDate(me.created_at)} />
        <Field label="Last updated" value={fmtDate(me.updated_at)} />
        {profile && <Field label="Employee ID" value={profile.employee_id} mono />}
        {profile && <Field label="Department" value={profile.department_name} />}
        {profile?.kind === 'interviewer' && (
          <Field label="Years of experience" value={profile.years_experience} />
        )}
        {profile?.kind === 'interviewer' && (
          <Field label="Expertise" value={
            profile.expertise_areas?.length
              ? <span style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                  {profile.expertise_areas.map(a => <span key={a} className="badge badge-neutral">{a}</span>)}
                </span>
              : null
          } />
        )}
        <Field label="User ID" value={me.id} mono />
      </div>

      {editing && (
        <p style={{ marginTop: 14, fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
          Your email address and role are set by your administrator and cannot be changed here.
        </p>
      )}

      <Notice kind="error">{error}</Notice>
    </Card>
  )
}
