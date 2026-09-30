// "How did you hear about this job?" — sent with an application as its source.
// Keys match CANDIDATE_SELECTABLE in backend/src/services/candidate_source.py.
const OPTIONS = [
  ['direct', 'Company website / career page'],
  ['job_portal', 'Job portal (LinkedIn, Naukri, Indeed…)'],
  ['referral', 'Referred by an employee'],
  ['agency', 'Recruitment agency'],
  ['other', 'Other'],
]
const KNOWN = new Set(OPTIONS.map(([k]) => k))

/** Pre-fill from a shared link: ?source=referral&ref=Ravi, or ?utm_source=linkedin. */
export function initialSource(search = typeof window !== 'undefined' ? window.location.search : '') {
  const q = new URLSearchParams(search)
  const raw = (q.get('source') || '').toLowerCase()
  const utm = q.get('utm_source') || ''
  if (KNOWN.has(raw)) {
    return { source: raw, source_detail: q.get('source_detail') || utm || '', referrer_name: q.get('ref') || '', referrer_email: '' }
  }
  if (utm) return { source: 'job_portal', source_detail: utm, referrer_name: '', referrer_email: '' }
  return { source: 'direct', source_detail: '', referrer_name: '', referrer_email: '' }
}

/** The fields the apply endpoints accept; blanks are omitted. */
export function sourcePayload(v) {
  const out = { source: v.source }
  if (v.source_detail?.trim()) out.source_detail = v.source_detail.trim()
  if (v.source === 'referral') {
    if (v.referrer_name?.trim()) out.referrer_name = v.referrer_name.trim()
    if (v.referrer_email?.trim()) out.referrer_email = v.referrer_email.trim()
  }
  return out
}

export default function SourceFields({ value, onChange }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value })
  const detailHint = { job_portal: 'Which portal?', agency: 'Agency name', other: 'Where?' }[value.source]
  return (
    <div className="input-group">
      <label htmlFor="apply-source">How did you hear about this job?</label>
      <select id="apply-source" className="select" value={value.source} onChange={set('source')}>
        {OPTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      {detailHint && (
        <input className="input" style={{ marginTop: 8 }} placeholder={detailHint} aria-label={detailHint}
          value={value.source_detail} onChange={set('source_detail')} maxLength={255} />
      )}
      {value.source === 'referral' && (
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <input className="input" placeholder="Who referred you?" aria-label="Referrer name"
            value={value.referrer_name} onChange={set('referrer_name')} maxLength={255} />
          <input className="input" type="email" placeholder="Their work email (optional)" aria-label="Referrer email"
            value={value.referrer_email} onChange={set('referrer_email')} maxLength={255} />
        </div>
      )}
    </div>
  )
}
