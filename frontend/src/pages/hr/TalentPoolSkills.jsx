import React from 'react'
import { Briefcase, ChevronRight, Sparkles } from 'lucide-react'
import { BarList, StatTile } from './TalentPoolCharts'
import { ViewScope, nf } from './TalentPoolPanels'

/**
 * Skills: can we staff the roles we have open?
 *
 * On the form. Each row carries two measures — how many roles demand the skill (1–5)
 * and how many people have it (0–hundreds). Those scales are nowhere near each other,
 * so drawing both as bars, or on two axes, would make a skill one role needs look
 * bigger than one twelve roles need. Bar length encodes ONE measure — people — and
 * demand rides alongside as a numeric badge.
 *
 * On the colour. Coverage reuses the ATS score ramp (--rm-weak / --rm-fair / --rm-good)
 * so "weak" means the same thing here as it does on a candidate's score, and no new
 * hue enters the app. It is never load-bearing: every row states its count in words as
 * well, so the table reads correctly in greyscale and under any colour deficiency.
 */

// Ordered worst-first, matching how the server sorts the rows.
const COVERAGE = [
  { at: 0, tone: 'weak', word: 'nobody' },
  { at: 1, tone: 'fair', word: 'thin' },
  { at: 3, tone: 'good', word: 'covered' },
]

const coverageOf = (n) => COVERAGE.filter(c => n >= c.at).pop() || COVERAGE[0]

/** One demanded skill, measured against the people in view. */
function DemandRow({ row, max, onPick }) {
  const cover = coverageOf(row.candidates)
  const pct = max > 0 ? (row.candidates / max) * 100 : 0
  const clickable = row.candidates > 0

  const body = (
    <>
      <span className="tp-skill__name" title={row.skill}>{row.skill}</span>

      <span className="tp-skill__roles"
            title={row.role_titles.join(', ') || 'Required by an open role'}>
        <Briefcase size={12} />
        {row.roles} {row.roles === 1 ? 'role' : 'roles'}
      </span>

      <span className="tp-skill__track">
        {/* A zero-length bar is still a row: the label to its right carries the value. */}
        <span className={`tp-skill__fill tp-skill__fill--${cover.tone}`}
              style={{ width: `${Math.max(pct, row.candidates > 0 ? 3 : 0)}%` }} />
      </span>

      <span className={`tp-skill__count tp-skill__count--${cover.tone}`}>
        {row.candidates === 0
          ? 'nobody'
          : `${nf.format(row.candidates)} ${row.candidates === 1 ? 'person' : 'people'}`}
      </span>
    </>
  )

  return (
    <li className={`tp-skill${clickable ? ' tp-skill--link' : ''}`}>
      {clickable ? (
        <button type="button" className="tp-skill__row" onClick={() => onPick(row.skill)}
                title={`Show the ${row.candidates} in the pool with ${row.skill}`}>
          {body}
          <ChevronRight size={14} className="tp-skill__go" />
        </button>
      ) : (
        <div className="tp-skill__row">{body}</div>
      )}

      {/* Secondary by design. The groupings behind this are hand-curated and some are
          loose, so the cluster is named — the reader judges it rather than trusting it. */}
      {row.related > 0 && (
        <p className="tp-skill__related">
          <Sparkles size={12} />
          {nf.format(row.related)} {row.related === 1 ? 'person knows' : 'people know'}{' '}
          {row.related_examples.join(', ')}
          {row.related_cluster ? ` — same ${row.related_cluster} group` : ' — related'}
        </p>
      )}
    </li>
  )
}

export function SkillsPanel({ skills, loading, filtered, onClear, onPickSkill }) {
  if (loading && !skills) return <div className="tp-loading">Loading skills…</div>
  if (!skills) return null

  const {
    people = 0, people_with_skills: withSkills = 0,
    no_skills_on_record: noSkills = 0, distinct_skills: distinct = 0,
    active_roles: roles = 0, demanded_skills: demanded = 0,
    fully_unmet: unmet = 0, truncated = false, demand = [], top_skills: top = [],
  } = skills

  const max = Math.max(...demand.map(r => r.candidates), 1)

  return (
    <div className="tp-panel">
      <ViewScope total={people} poolTotal={people} filtered={filtered} onClear={onClear} />

      {/* Demand comes from open roles, so with none posted every number below is
          undefined rather than zero. Say which it is. */}
      {roles === 0 ? (
        <section className="tp-card tp-card--empty">
          <h3 className="tp-card__title">No open roles to measure against</h3>
          <p className="tp-card__sub">
            This tab compares what your active job postings ask for against what the
            people in your pool can do. Publish a role and its required skills appear
            here, scored against everyone.
          </p>
        </section>
      ) : (
        <>
          <div className="tp-tiles">
            <StatTile label="Demanded, held by nobody" value={nf.format(unmet)}
                      hint={`of ${nf.format(demanded)} skills your open roles ask for`} />
            <StatTile label="Open roles" value={nf.format(roles)} />
            <StatTile label="Skills in this group" value={nf.format(distinct)}
                      hint={`across ${nf.format(withSkills)} ${withSkills === 1 ? 'person' : 'people'}`} />
            <StatTile label="No skills on record" value={nf.format(noSkills)}
                      hint={noSkills > 0 ? 'Resume not parsed yet — not counted anywhere below'
                                         : 'Everyone here has skills recorded'} />
          </div>

          <section className="tp-card">
            <h3 className="tp-card__title">Skill by skill</h3>
            <p className="tp-card__sub">
              Every skill your open roles ask for, against the people in view. Worst
              covered first — the ones you cannot staff are the ones worth reading.
            </p>

            {demand.length === 0 ? (
              <p className="tp-empty">Your open roles do not list any required skills.</p>
            ) : (
              <ol className="tp-skills" aria-label="Skills your open roles need">
                {demand.map(row => (
                  <DemandRow key={row.skill} row={row} max={max} onPick={onPickSkill} />
                ))}
              </ol>
            )}

            {noSkills > 0 && (
              <p className="tp-note">
                {nf.format(noSkills)} {noSkills === 1 ? 'person has' : 'people have'} no
                skills on record and {noSkills === 1 ? 'is' : 'are'} counted in none of
                these rows. Nothing was read from their resume — that is not the same as
                lacking the skill.
              </p>
            )}
            {truncated && (
              <p className="tp-note">
                Counted the first {nf.format(people)} people in this view, so these are
                lower bounds. Narrow the filter for exact numbers.
              </p>
            )}
          </section>

          <section className="tp-card">
            <h3 className="tp-card__title">What this group actually knows</h3>
            <p className="tp-card__sub">
              The most common skills among these people, whether or not a role asks for
              them — the only place a strength you are not currently hiring for shows up.
            </p>
            <BarList
              items={top.map(t => ({ label: t.skill, count: t.count }))}
              empty="No skills recorded for anyone in this group."
            />
          </section>
        </>
      )}
    </div>
  )
}
