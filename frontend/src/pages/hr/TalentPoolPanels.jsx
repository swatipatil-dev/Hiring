import React from 'react'
import { RefreshCw, Users, Upload, Gauge, X } from 'lucide-react'
import { StatTile, BarList } from './TalentPoolCharts'

/**
 * The pool's central promise is a match score per person, and imported people arrive
 * without one. This is the prompt to make good on it.
 *
 * Scoring runs in bounded batches inside the request (no queue infrastructure exists),
 * so progress is reported from the real remaining count rather than a spinner — a run
 * over a large backlog takes minutes and silence would read as broken.
 */
export function ScoringCard({ metrics, scoring, progress, onScore, onStop }) {
  if (!metrics) return null
  const { unscored = 0, unscoreable = 0 } = metrics
  if (unscored === 0 && unscoreable === 0) return null

  return (
    <section className="tp-card">
      <h3 className="tp-card__title">
        {unscored > 0
          ? `${unscored} ${unscored === 1 ? 'person is' : 'people are'} not ranked yet`
          : 'Everyone who can be ranked, is'}
      </h3>
      {unscored > 0 && (
        <p className="tp-card__sub">
          They came in without being scored against your open roles, so they have no
          match and sit at the bottom of the list.
        </p>
      )}

      {unscored > 0 && (
        <div className="tp-card__actions">
          {scoring ? (
            <>
              <span className="tp-progress" role="status">
                <RefreshCw size={14} className="spin" /> {progress}
              </span>
              <button className="rm-act" onClick={onStop}>
                <X size={14} /> Stop
              </button>
            </>
          ) : (
            <button className="btn btn-primary btn-sm" onClick={onScore}>
              <Gauge size={15} /> Rank {unscored === 1 ? 'them' : `all ${unscored}`}
            </button>
          )}
        </div>
      )}

      {unscoreable > 0 && (
        <p className="tp-note">
          {unscoreable} {unscoreable === 1 ? 'person has' : 'people have'} no resume or
          profile text to score, so {unscoreable === 1 ? 'they will' : 'they will'} stay
          unranked until {unscoreable === 1 ? 'they add one' : 'they add one'}.
        </p>
      )}
    </section>
  )
}

const OUTCOME_LABELS = {
  never_applied: 'Never applied',
  in_process: 'In process',
  offer: 'Offer out',
  hired: 'Hired',
  declined: 'Declined our offer',
  rejected: 'Rejected',
}

const pretty = (s) => OUTCOME_LABELS[s] || (s || '').replace(/_/g, ' ')

/** Overview — the numbers HR leads with, then the funnel. */
export function OverviewPanel({ metrics, loading, onOpenStage, scoring, progress,
                                onScore, onStop }) {
  if (loading && !metrics) return <div className="tp-loading">Loading metrics…</div>
  if (!metrics) return null

  const contact = metrics.avg_days_since_contact
  return (
    <div className="tp-panel">
      {/* Above the tiles: an unranked backlog undermines every number below it. */}
      <ScoringCard metrics={metrics} scoring={scoring} progress={progress}
                   onScore={onScore} onStop={onStop} />
      {/* The three stage counts live in the pipeline chart below and nowhere else.
          Repeating them as tiles would spend half the screen showing the same three
          numbers twice. These tiles carry what the chart cannot. */}
      <div className="tp-tiles">
        <StatTile label="Total in pool" value={metrics.total} />
        <StatTile
          label="Avg days since contact"
          value={contact == null ? '—' : contact}
          hint={contact == null ? 'Nobody contacted yet' : 'Across everyone you have spoken to'}
        />
        <StatTile label="Never contacted" value={metrics.never_contacted}
                  hint="No outreach recorded" />
        <StatTile label="Added in last 30 days" value={metrics.added_last_30_days} />
        <StatTile label="With a resume on file" value={metrics.with_resume}
                  hint={`of ${metrics.total}`} />
      </div>

      <section className="tp-card">
        <h3 className="tp-card__title">Pipeline</h3>
        <p className="tp-card__sub">
          Your own triage lane. Move people through it by hand — it is deliberately
          separate from what happened to them in the ATS.
        </p>
        <BarList
          ordinal
          items={(metrics.pipeline || []).map(p => ({
            key: p.stage, label: p.label, count: p.count,
          }))}
          empty="Nobody in the pool yet."
        />
        {onOpenStage && metrics.total > 0 && (
          <div className="tp-card__actions">
            {(metrics.pipeline || []).map(p => (
              <button key={p.stage} className="rm-act" onClick={() => onOpenStage(p.stage)}>
                See {p.label.toLowerCase()}
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

/** Analytics — where people come from, and what became of them. */
export function AnalyticsPanel({ metrics, loading }) {
  if (loading && !metrics) return <div className="tp-loading">Loading analytics…</div>
  if (!metrics) return null

  return (
    <div className="tp-panel">
      <section className="tp-card">
        <h3 className="tp-card__title">Where they came from</h3>
        <p className="tp-card__sub">
          Sourcing channel where you recorded one, otherwise how the record reached the
          pool — so every person is counted exactly once.
        </p>
        <BarList
          items={(metrics.sources || []).map(s => ({ label: s.name, count: s.count }))}
          empty="No sources recorded yet."
        />
      </section>

      <section className="tp-card">
        <h3 className="tp-card__title">What happened in the ATS</h3>
        <p className="tp-card__sub">
          Read live from the hiring pipeline. Each person counts once, under the best
          thing that ever happened to them here.
        </p>
        <BarList
          items={(metrics.outcomes || []).map(o => ({ label: pretty(o.name), count: o.count }))}
          empty="Nobody in the pool has applied to a role yet."
        />
      </section>
    </div>
  )
}

/** Settings — the knobs that actually exist, and nothing invented. */
export function SettingsPanel({ metrics, pending, importing, onImport, onRefresh, busy,
                                scoring, progress, onScore, onStop }) {
  return (
    <div className="tp-panel">
      <ScoringCard metrics={metrics} scoring={scoring} progress={progress}
                   onScore={onScore} onStop={onStop} />
      <section className="tp-card">
        <h3 className="tp-card__title">Bring people in</h3>
        <p className="tp-card__sub">
          New applicants and registrations land here on their own. This sweeps up
          everyone who was already in the database before that started.
        </p>
        <div className="tp-card__actions">
          <button className="btn btn-primary btn-sm" onClick={onImport}
                  disabled={importing || pending === 0}>
            <Users size={15} />
            {importing ? 'Importing…'
              : pending > 0 ? `Import ${pending} people` : 'Everyone is already in'}
          </button>
          <button className="rm-act" onClick={onRefresh} disabled={busy}>
            <RefreshCw size={14} className={busy ? 'spin' : undefined} /> Refresh metrics
          </button>
        </div>
        {pending > 0 && (
          <p className="tp-note">
            Imported in batches of 50. Most arrive unscored — use Re-match on a profile,
            or re-run the import, to rank them against your open roles.
          </p>
        )}
      </section>

      <section className="tp-card">
        <h3 className="tp-card__title">How stages work</h3>
        <p className="tp-card__sub">
          Two things are tracked separately, on purpose.
        </p>
        <dl className="tp-defs">
          <dt>Stage</dt>
          <dd>
            New, Ongoing and Archived. Yours to set. Nothing changes it automatically,
            so a candidate stays where you put them.
          </dd>
          <dt>Outcome</dt>
          <dd>
            Hired, Rejected, In process and so on. Read live from the ATS and not
            editable here — it is a record of what actually happened.
          </dd>
        </dl>
        <p className="tp-note">
          This is why someone rejected for one role can still sit in Ongoing: the
          rejection is history, the stage is your intent.
        </p>
      </section>

      <section className="tp-card">
        <h3 className="tp-card__title">Files</h3>
        <p className="tp-card__sub">
          Profiles accept PDF, DOC, DOCX, PNG and JPG up to 10&nbsp;MB.
        </p>
        <p className="tp-note">
          <Upload size={13} /> The first document you attach becomes the resume used for
          matching. Images are stored and shown but cannot be scored — matching reads
          text, and no OCR runs on this path.
        </p>
        {metrics && (
          <p className="tp-note">
            {metrics.with_resume} of {metrics.total} profiles have a resume on file.
          </p>
        )}
      </section>
    </div>
  )
}
