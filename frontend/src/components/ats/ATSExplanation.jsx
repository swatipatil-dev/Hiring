import { useEffect, useState } from 'react'
import api from '../../api/client'

/**
 * What a resume score rests on, in the resume's own words.
 *
 * Fetches `loadUrl` (the ATS-tab explanation, or a Talent Pool match computed on demand)
 * and shows: mandatory requirements with how each was evidenced and a quote; nice-to-haves;
 * experience and education with UNKNOWN kept distinct from NOT MET; how the factors combine,
 * including any left out for lack of evidence; and when, and against what, it was scored.
 */

const LEVEL = {
  demonstrated: { label: 'Shown in work', fg: '#166534', bg: '#dcfce7',
    hint: 'Appears in an experience or project entry.' },
  listed: { label: 'Listed only', fg: '#1e40af', bg: '#dbeafe',
    hint: 'Appears in a skills list, not in any experience or project entry.' },
  related: { label: 'Related skill', fg: '#92400e', bg: '#fef3c7',
    hint: 'Not found, but a skill from the same family is.' },
  not_found: { label: 'Not found on resume', fg: '#4b5563', bg: '#f3f4f6',
    hint: 'Not on the resume. That can be a gap, or just not written down.' },
}

const S = {
  section: { marginTop: 16 },
  h: { fontSize: '0.8rem', fontWeight: 800, color: '#111827', margin: '0 0 8px' },
  row: { display: 'grid', gridTemplateColumns: 'minmax(0, 11rem) minmax(0, 1fr)', gap: '4px 14px', padding: '8px 0', borderTop: '1px solid #f3f4f6' },
  skill: { fontSize: '0.86rem', fontWeight: 700, color: '#111827', display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' },
  quote: { display: 'block', fontSize: '0.8rem', color: '#374151', lineHeight: 1.5, background: '#f9fafb', borderLeft: '3px solid #d1d5db', padding: '4px 8px', margin: '0 0 4px' },
  muted: { fontSize: '0.8rem', color: '#6b7280' },
}

function Chip({ level, extra }) {
  const l = LEVEL[level] || LEVEL.not_found
  return (
    <span title={l.hint} style={{ fontSize: '0.7rem', fontWeight: 700, padding: '2px 8px', borderRadius: 6, color: l.fg, background: l.bg }}>
      {l.label}{extra ? `: ${extra}` : ''}
    </span>
  )
}

function Quotes({ excerpts }) {
  const list = (excerpts || []).filter(Boolean)
  if (!list.length) return null
  return list.map((e, i) => (
    <q key={i} style={S.quote} title={e.section ? `From the ${e.section} section` : undefined}>{e.excerpt}</q>
  ))
}

function Checklist({ items }) {
  return (
    <div>
      {items.map((c) => (
        <div key={c.skill} style={S.row}>
          <div style={S.skill}>
            {c.skill}
            <Chip level={c.level} extra={c.level === 'related' ? c.related_to : null} />
          </div>
          <div>
            <Quotes excerpts={c.excerpts} />
            {c.level === 'not_found' && <span style={S.muted}>Ask about it rather than assume it is missing.</span>}
          </div>
        </div>
      ))}
    </div>
  )
}

function Status({ kind, children }) {
  const c = { met: ['#166534', '#dcfce7'], below: ['#9a3412', '#ffedd5'], unknown: ['#4b5563', '#f3f4f6'],
    not_required: ['#4b5563', '#f3f4f6'] }[kind] || ['#4b5563', '#f3f4f6']
  return <span style={{ fontSize: '0.72rem', fontWeight: 700, padding: '2px 8px', borderRadius: 6, color: c[0], background: c[1] }}>{children}</span>
}

function Experience({ exp }) {
  if (!exp) return null
  const years = exp.years_found
  const status = exp.relevant_status
  return (
    <div style={S.section}>
      <h4 style={S.h}>Experience</h4>
      <div style={S.row}>
        <div style={S.skill}>Years of experience</div>
        <div>
          {status === 'unknown' && <Status kind="unknown">Not stated on the resume: verify</Status>}
          {status === 'met' && <Status kind="met">{`About ${years} years, ${exp.required_years} required`}</Status>}
          {status === 'below' && <Status kind="below">{`About ${years} years, ${exp.required_years} required`}</Status>}
          {status === 'not_required' && <Status kind="not_required">No minimum for this job</Status>}
          {exp.years_method === 'profile' && <span style={{ ...S.muted, marginLeft: 8 }}>From the candidate's profile</span>}
          <div style={{ marginTop: 4 }}><Quotes excerpts={exp.years_evidence} /></div>
        </div>
      </div>
      <div style={S.row}>
        <div style={S.skill}>Internships</div>
        <div>
          {exp.internship_count ? <span style={{ fontSize: '0.84rem' }}>{exp.internship_count} found</span> : <span style={S.muted}>None found</span>}
          <div style={{ marginTop: 4 }}><Quotes excerpts={exp.internship_evidence} /></div>
        </div>
      </div>
      <div style={S.row}>
        <div style={S.skill}>GitHub, open source, hackathons</div>
        <div>
          {!exp.has_github && !exp.has_oss && !exp.has_hackathon && <span style={S.muted}>None found</span>}
          <Quotes excerpts={[exp.github_evidence, exp.oss_evidence, exp.hackathon_evidence]} />
        </div>
      </div>
    </div>
  )
}

function Education({ edu }) {
  if (!edu) return null
  const deg = { met: <Status kind="met">Meets the required level</Status>,
    below: <Status kind="below">Below the required level</Status>,
    unknown: <Status kind="unknown">Not stated on the resume: verify</Status> }[edu.degree_status]
  const tier = { tier_1: 'Tier-1 institute', tier_2: 'Tier-2 institute', not_listed: 'Institute not in the tier list',
    unknown: null }[edu.tier_status] || null
  return (
    <div style={S.section}>
      <h4 style={S.h}>Education</h4>
      <div style={S.row}>
        <div style={S.skill}>Degree</div>
        <div>{deg}<div style={{ marginTop: 4 }}><Quotes excerpts={[edu.degree_evidence]} /></div></div>
      </div>
      <div style={S.row}>
        <div style={S.skill}>Institute</div>
        <div>{tier ? <span style={{ fontSize: '0.84rem' }}>{tier}</span> : <Status kind="unknown">Not stated: not counted</Status>}</div>
      </div>
      <div style={S.row}>
        <div style={S.skill}>CGPA</div>
        <div>{edu.cgpa_status === 'found'
          ? <span style={{ fontSize: '0.84rem' }}>{edu.cgpa} of {edu.cgpa_max} points</span>
          : <Status kind="unknown">Not stated: not counted</Status>}</div>
      </div>
    </div>
  )
}

function Factors({ rows, coverage }) {
  if (!rows?.length) return null
  return (
    <div style={S.section}>
      <h4 style={S.h}>How the score adds up</h4>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
        <thead>
          <tr style={{ color: '#6b7280', textAlign: 'left' }}>
            <th style={{ padding: '4px 6px' }}>Factor</th><th style={{ padding: '4px 6px' }}>Score</th>
            <th style={{ padding: '4px 6px' }}>Weight</th><th style={{ padding: '4px 6px' }}>Adds</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} style={{ borderTop: '1px solid #f3f4f6', color: r.status === 'scored' ? '#111827' : '#9ca3af' }}>
              <td style={{ padding: '5px 6px' }}>{r.label}</td>
              <td style={{ padding: '5px 6px' }}>{r.status === 'scored' ? Math.round(r.score) : 'Nothing to go on: left out'}</td>
              <td style={{ padding: '5px 6px' }}>
                {Math.round(r.weight * 100)}%
                {r.status === 'scored' && Math.abs(r.effective_weight - r.weight) > 0.001 && ` (counts as ${Math.round(r.effective_weight * 100)}%)`}
              </td>
              <td style={{ padding: '5px 6px' }}>{r.status === 'scored' ? r.contribution.toFixed(1) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {coverage != null && coverage < 1 && (
        <p style={{ ...S.muted, margin: '6px 0 0' }}>
          {Math.round(coverage * 100)}% of the scoring weight rests on evidence found in the resume; the rest was
          left out and the remaining factors re-weighted.
        </p>
      )}
    </div>
  )
}

export default function ATSExplanation({ loadUrl, compact = false, onRescore }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setData(null); setError(null)
    api.get(loadUrl)
      .then((r) => { if (!cancelled) setData(r.data) })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || 'Could not load the explanation.') })
    return () => { cancelled = true }
  }, [loadUrl])

  if (error) return <div style={{ ...S.muted, color: '#b91c1c' }}>{error}</div>
  if (!data) return <div style={S.muted}>Loading the evidence…</div>

  const ex = data.explanation
  const needsReview = data.parse?.status === 'needs_review'
  const prov = data.provenance || {}
  return (
    <div onClick={(e) => e.stopPropagation()} style={{ fontSize: '0.86rem', color: '#111827' }}>
      {needsReview && (
        <div style={{ padding: '10px 12px', borderRadius: 8, background: '#fffbeb', border: '1px solid #fde68a', marginBottom: 12 }}>
          <b style={{ color: '#92400e' }}>Not ranked: read the resume yourself.</b>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18, color: '#78350f', fontSize: '0.82rem' }}>
            {(data.parse.issue_text || []).map((t, i) => <li key={i}>{t}</li>)}
          </ul>
        </div>
      )}

      {!ex ? (
        <div style={{ ...S.muted, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          Scored before explanations were recorded, so there is no evidence to show.
          {onRescore && <button type="button" className="btn btn-ghost btn-sm" onClick={onRescore}>Rescore to see it</button>}
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '1rem', fontWeight: 800 }}>
              Meets {ex.mandatory?.met ?? 0} of {ex.mandatory?.total ?? 0} mandatory
            </span>
            <span style={S.muted}>Missing ones lower the score; they never reject anyone automatically.</span>
          </div>
          {ex.mandatory?.items?.length > 0 && <Checklist items={ex.mandatory.items} />}

          <div style={S.section}>
            <h4 style={S.h}>
              Nice to have
              {ex.preferred?.source === 'inferred' && <span style={{ ...S.muted, fontWeight: 400 }}> (read from the job description text)</span>}
            </h4>
            {ex.preferred?.items?.length ? <Checklist items={ex.preferred.items} /> : <div style={S.muted}>None specified for this job.</div>}
          </div>

          {!compact && <Experience exp={ex.experience} />}
          {!compact && <Education edu={ex.education} />}
          <Factors rows={ex.factors} coverage={ex.evidence_coverage} />
        </>
      )}

      <div style={{ ...S.muted, marginTop: 12, fontSize: '0.74rem' }}>
        {[
          prov.scored_at && `Scored ${new Date(prov.scored_at + (prov.scored_at.endsWith('Z') ? '' : 'Z')).toLocaleString()}`,
          prov.scoring_rules_version && `rules ${prov.scoring_rules_version}${prov.current_rules_version && prov.current_rules_version !== prov.scoring_rules_version ? ` (current ${prov.current_rules_version})` : ''}`,
          prov.jd_version_no && `job description v${prov.jd_version_no}${prov.current_jd_version_no && prov.current_jd_version_no !== prov.jd_version_no ? ` (current v${prov.current_jd_version_no})` : ''}`,
          data.scoring_source && `from the candidate's ${data.scoring_source === 'profile' ? 'profile' : 'resume'}`,
          prov.model_version?.semantic && `relevance model: ${prov.model_version.semantic}`,
        ].filter(Boolean).join(' · ')}
      </div>
    </div>
  )
}
