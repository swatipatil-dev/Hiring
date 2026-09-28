import { useEffect, useState } from 'react'
import api from '../api/client'

/**
 * Lets a candidate tell the hiring team, in their own words, anything they should know
 * about a submitted test: why it ended early, a power cut, a flag they expect to see.
 * Reviewers read it next to the proctoring record. Appendable, up to five notes.
 */
export default function ExplanationForm({ interviewId, tone = 'light' }) {
  const [items, setItems] = useState([])
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [sent, setSent] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.get(`/candidate/interviews/${interviewId}/explanations`)
      .then((r) => { if (!cancelled) setItems(r.data || []) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [interviewId])

  const send = async () => {
    const body = text.trim()
    if (!body) return
    setSaving(true); setError(null)
    try {
      const r = await api.post(`/candidate/interviews/${interviewId}/explanation`, { text: body })
      setItems((xs) => [...xs, r.data])
      setText('')
      setSent(true)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Your note could not be sent. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const muted = tone === 'light' ? '#64748b' : '#94a3b8'
  const full = items.length >= 5
  return (
    <div style={{ textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <label htmlFor={`explain-${interviewId}`} style={{ fontSize: 13, fontWeight: 700, color: '#0f172a' }}>
        Anything the hiring team should know?
      </label>
      <p style={{ margin: 0, fontSize: 12, color: muted, lineHeight: 1.5 }}>
        For example, a power cut or someone walking into the room. Your note is shown to the
        people reviewing your test, next to anything that was flagged.
      </p>
      {items.map((e) => (
        <p key={e.id} style={{ margin: 0, padding: '8px 10px', borderRadius: 8, background: '#f1f5f9', fontSize: 12.5, color: '#334155', lineHeight: 1.5 }}>
          {e.text}
        </p>
      ))}
      {!full && (
        <>
          <textarea
            id={`explain-${interviewId}`}
            value={text}
            onChange={(e) => { setText(e.target.value); setSent(false) }}
            rows={3}
            maxLength={2000}
            style={{ fontFamily: 'inherit', fontSize: 13, padding: '8px 10px', borderRadius: 8, border: '1px solid #cbd5e1', resize: 'vertical' }}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button type="button" onClick={send} disabled={saving || !text.trim()}
              style={{ padding: '8px 16px', borderRadius: 8, border: 'none', background: '#0f766e', color: '#fff', fontSize: 13, fontWeight: 700, cursor: saving || !text.trim() ? 'not-allowed' : 'pointer', opacity: saving || !text.trim() ? 0.5 : 1 }}>
              {saving ? 'Sending…' : 'Send to the hiring team'}
            </button>
            {sent && <span role="status" style={{ fontSize: 12, color: '#15803d' }}>Sent. The reviewers will see it.</span>}
          </div>
        </>
      )}
      {error && <p role="alert" style={{ margin: 0, fontSize: 12, color: '#b91c1c' }}>{error}</p>}
    </div>
  )
}
