import React from 'react'
import { RefreshCw, Users, Upload, Gauge, X, FilterX } from 'lucide-react'
import { StatTile, BarList, TrendArea, ScoreHistogram } from './TalentPoolCharts'

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

export const nf = new Intl.NumberFormat()

/**
 * How much of the pool the reader is currently looking at.
 *
 * The single most important line on the screen. Every number below it is filtered,
 * and a filtered dashboard that looks identical to an unfiltered one is how people
 * come to trust a figure that describes 40 of their 4,000 candidates.
 */
export function ViewScope({ total, poolTotal, filtered, onClear }) {
  return (
    <div className={`tp-scope${filtered ? ' tp-scope--on' : ''}`} role="status">
      <span className="tp-scope__count">
        {filtered
          ? <><strong>{nf.format(total)}</strong> of {nf.format(poolTotal)} people</>
          : <>All <strong>{nf.format(poolTotal)}</strong> people</>}
      </span>
      <span className="tp-scope__note">
        {filtered
          ? 'Every chart below describes this slice.'
          : 'Filter above to narrow every chart and the Candidates list together.'}
      </span>
      {filtered && (
        <button type="button" className="rm-act tp-scope__clear" onClick={onClear}>
          <FilterX size={14} /> Clear filters
        </button>
      )}
    </div>
  )
}

/**
 * The dashboard — every number and chart for the people currently in view.
 *
 * One tab rather than the Overview/Analytics split it replaces: the same counts were
 * reachable from two places, which meant two screens to check and two chances to read
 * a stale one.
 *
 * Reading order is deliberate. What is broken (unranked backlog) → how big is this
 * → how it moves and how strong it is → where people sit and where they came from →
 * who they are. Each row answers the question the row above it raises.
 */
export function DashboardPanel({ metrics, loading, filtered, onClear, onOpenStage,
                                 onOpenNeverContacted, onOpenRecent,
                                 scoring, progress, onScore, onStop }) {
  if (loading && !metrics) return <div className="tp-loading">Loading dashboard…</div>
  if (!metrics) return null

  const {
    total = 0, pool_total: poolTotal = 0, avg_score: avgScore,
    avg_days_since_contact: contact, unscored_in_view: unscoredHere = 0,
    trend_granularity: granularity = 'month',
  } = metrics

  // A filter that matches nobody is a normal thing to do — it is how you find out a
  // cohort is empty. Every card would otherwise render its own empty state and the
  // screen would say "nothing to show" nine times.
  const empty = total === 0

  return (
    <div className="tp-panel">
      <ViewScope total={total} poolTotal={poolTotal} filtered={filtered} onClear={onClear} />

      {/* Above everything: an unranked backlog undermines every number below it. */}
      <ScoringCard metrics={metrics} scoring={scoring} progress={progress}
                   onScore={onScore} onStop={onStop} />

      {empty ? (
        <section className="tp-card tp-card--empty">
          <h3 className="tp-card__title">Nobody matches these filters</h3>
          <p className="tp-card__sub">
            The pool has {nf.format(poolTotal)} {poolTotal === 1 ? 'person' : 'people'} in
            it. Widen a filter, or clear them all, to see the numbers again.
          </p>
          {onClear && (
            <div className="tp-card__actions">
              <button className="btn btn-primary btn-sm" onClick={onClear}>
                <FilterX size={15} /> Clear filters
              </button>
            </div>
          )}
        </section>
      ) : (
        <>
          {/* The three stage counts live in the pipeline chart below and nowhere else.
              Repeating them as tiles would spend half the screen showing the same three
              numbers twice. These tiles carry what the charts cannot. */}
          <div className="tp-tiles">
            <StatTile label={filtered ? 'People in view' : 'Total in pool'}
                      value={nf.format(total)}
                      hint={filtered ? `of ${nf.format(poolTotal)} in the pool` : undefined} />
            <StatTile label="Avg match score"
                      value={avgScore == null ? '—' : avgScore}
                      hint={unscoredHere > 0
                        ? `${nf.format(unscoredHere)} not ranked yet`
                        : 'Across everyone ranked'} />
            <StatTile label="Avg days since contact"
                      value={contact == null ? '—' : contact}
                      hint={contact == null ? 'Nobody contacted yet' : 'Across everyone you have spoken to'} />
            <StatTile label="Never contacted" value={nf.format(metrics.never_contacted)}
                      hint="No outreach recorded"
                      onClick={metrics.never_contacted > 0 ? onOpenNeverContacted : undefined}
                      actionHint="List them, least recently contacted first" />
            <StatTile label="Added in last 30 days"
                      value={nf.format(metrics.added_last_30_days)}
                      onClick={metrics.added_last_30_days > 0 ? onOpenRecent : undefined}
                      actionHint="List the people added in the last 30 days" />
          </div>

          <div className="tp-grid">
            <section className="tp-card">
              <h3 className="tp-card__title">Additions over time</h3>
              <p className="tp-card__sub">
                People entering the pool per {granularity}. Quiet periods are real
                zeroes, not gaps closed up — a hiring freeze should look like one.
              </p>
              <TrendArea points={metrics.added_over_time} granularity={granularity}
                         empty="Nobody was added in this window." />
            </section>

            <section className="tp-card">
              <h3 className="tp-card__title">Match score distribution</h3>
              <p className="tp-card__sub">
                How this group scored against your open roles, in 10-point bands.
              </p>
              <ScoreHistogram bands={metrics.score_bands}
                              empty="Nobody in this group has been ranked yet." />
              <p className="tp-note">
                {unscoredHere > 0
                  ? `${nf.format(unscoredHere)} ${unscoredHere === 1 ? 'person is' : 'people are'} not ranked at all and appear in no band — not ranked is not the same as scoring zero.`
                  : `Everyone here is ranked. ${nf.format(metrics.with_resume)} of ${nf.format(total)} have a resume on file.`}
              </p>
            </section>
          </div>

          <div className="tp-grid tp-grid--3">
            <section className="tp-card">
              <h3 className="tp-card__title">Pipeline</h3>
              <p className="tp-card__sub">
                Your own triage lane. Move people through it by hand — it is
                deliberately separate from what happened to them in the ATS.
              </p>
              <BarList
                ordinal
                items={(metrics.pipeline || []).map(p => ({
                  key: p.stage, label: p.label, count: p.count,
                }))}
                empty="Nobody in the pool yet."
              />
              {onOpenStage && total > 0 && (
                <div className="tp-card__actions">
                  {(metrics.pipeline || []).map(p => (
                    <button key={p.stage} className="rm-act"
                            onClick={() => onOpenStage(p.stage)}>
                      See {p.label.toLowerCase()}
                    </button>
                  ))}
                </div>
              )}
            </section>

            <section className="tp-card">
              <h3 className="tp-card__title">Where they came from</h3>
              <p className="tp-card__sub">
                Sourcing channel where you recorded one, otherwise how the record
                reached the pool — so every person is counted exactly once.
              </p>
              <BarList
                items={(metrics.sources || []).map(s => ({ label: s.name, count: s.count }))}
                empty="No sources recorded yet."
              />
            </section>

            <section className="tp-card">
              <h3 className="tp-card__title">What happened in the ATS</h3>
              <p className="tp-card__sub">
                Read live from the hiring pipeline. Each person counts once, under the
                best thing that ever happened to them here.
              </p>
              <BarList
                items={(metrics.outcomes || []).map(o => ({ label: pretty(o.name), count: o.count }))}
                empty="Nobody in this group has applied to a role yet."
              />
            </section>
          </div>

          <div className="tp-grid">
            <section className="tp-card">
              <h3 className="tp-card__title">Top colleges</h3>
              <p className="tp-card__sub">
                The ten most common in this group. Grouped on the text as typed, so
                spellings that differ stay separate rows.
              </p>
              <BarList
                items={(metrics.top_colleges || []).map(c => ({ label: c.name, count: c.count }))}
                empty="No colleges recorded for this group."
              />
            </section>

            <section className="tp-card">
              <h3 className="tp-card__title">Top roles</h3>
              <p className="tp-card__sub">
                What these people do now, or last did — parsed from their resume where
                one was read.
              </p>
              <BarList
                items={(metrics.top_roles || []).map(r => ({ label: r.name, count: r.count }))}
                empty="No roles recorded for this group."
              />
            </section>
          </div>
        </>
      )}
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
