import React from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

/**
 * Chart primitives for the Talent Pool dashboard.
 *
 * Two colour jobs, and they are not interchangeable:
 *
 *  ORDINAL — New → Ongoing → Archived is a funnel. Swapping the order would change
 *  the meaning, so the stages take one hue in monotone lightness steps and the reader
 *  sees the sequence in the colour itself. The ramp is validated: monotone lightness,
 *  adjacent ΔL ≥ 0.06, single hue (6° spread), light end 2.39:1 on the ivory surface.
 *
 *  NOMINAL — sources, outcomes, colleges and roles are just names. Reordering them
 *  changes nothing, so every bar wears the SAME hue (#0e4fa0, 7.55:1 on surface).
 *  Colouring those bars by their value would spend the identity channel re-encoding
 *  what bar length already shows.
 *
 * The two Recharts figures below carry ONE series each, so neither needs a legend or
 * a second hue — the card title names the series, and length carries the value. Score
 * bands are bins, not categories: they are positions on an axis, so painting them
 * separately would invent an identity the data does not have.
 *
 * Every bar is direct-labelled with its count, so nothing depends on colour alone.
 */

// Ordinal ramp: light → dark across the funnel. Derived from the app's navy.
export const STAGE_RAMP = {
  new: '#7aa7d9',
  ongoing: '#2b6cb0',
  archived: '#0a3b7d',
}

// Single hue for nominal bars, and for both Recharts figures.
const NOMINAL_HUE = '#0e4fa0'

// Recessive axes and grid: they orient the reader and then get out of the way.
const AXIS = { stroke: '#98a2b3', fontSize: 11, tickLine: false, axisLine: false }
const GRID = '#e6e9ef'

/** Chrome shared by both Recharts figures, so they read as one system. */
const tooltipStyle = {
  contentStyle: {
    borderRadius: 10,
    border: '1px solid #e6e9ef',
    boxShadow: '0 6px 18px rgba(16,24,40,0.08)',
    fontSize: '0.78rem',
    padding: '8px 10px',
  },
  labelStyle: { fontWeight: 600, marginBottom: 2, color: '#1d2939' },
  // Values wear ink, never the series colour — the mark beside them carries identity.
  itemStyle: { color: '#475467' },
}

/**
 * A tile is a button only when it has somewhere to go.
 *
 * Without `onClick` it renders exactly as before — a plain div that reads as a label and
 * does not move on hover. That is deliberate: index.css:408 sets the house rule that a card
 * which lifts must navigate, and an average has no list to drill into, so `Avg match score`
 * stays inert rather than being given a destination for the sake of consistency.
 */
export function StatTile({ label, value, hint, onClick, actionHint }) {
  const body = (
    <>
      <div className="tp-tile__label">{label}</div>
      <div className="tp-tile__value">{value}</div>
      {hint && <div className="tp-tile__hint">{hint}</div>}
    </>
  )

  if (!onClick) return <div className="tp-tile">{body}</div>

  return (
    <button type="button" className="tp-tile tp-tile--action" onClick={onClick}
            aria-label={`${label}: ${value}. ${actionHint || 'View these people'}`}>
      {body}
    </button>
  )
}

/**
 * Horizontal bars with direct labels.
 *
 * Horizontal because the categories have long names — "Registered on portal" does not
 * fit under a column without turning the axis label sideways.
 */
export function BarList({ items, ordinal = false, empty = 'Nothing to show yet.' }) {
  const rows = (items || []).filter(Boolean)
  if (rows.length === 0) return <p className="tp-empty">{empty}</p>

  // Scale to the largest bar rather than the total: this compares magnitudes, and
  // scaling to the total would flatten every bar into invisibility when one dominates.
  const max = Math.max(...rows.map(r => r.count), 1)

  return (
    <dl className="tp-bars">
      {rows.map((r) => {
        const pct = (r.count / max) * 100
        const color = ordinal ? (STAGE_RAMP[r.key] || NOMINAL_HUE) : NOMINAL_HUE
        return (
          <div className="tp-bar" key={r.key || r.label}>
            <dt className="tp-bar__label" title={r.label}>{r.label}</dt>
            <dd className="tp-bar__track">
              <span
                className="tp-bar__fill"
                style={{ width: `${Math.max(pct, r.count > 0 ? 2 : 0)}%`, background: color }}
                title={`${r.label}: ${r.count}`}
              />
            </dd>
            <dd className="tp-bar__value">{r.count}</dd>
          </div>
        )
      })}
    </dl>
  )
}

/**
 * Additions over time.
 *
 * Area rather than bars: this is one quantity moving continuously, and the filled
 * region reads as accumulated intake in a way separated columns do not. The server
 * zero-fills the empty periods, so a hiring freeze shows as a trough rather than
 * being closed up into a straight line between the two weeks either side of it.
 */
export function TrendArea({ points, granularity = 'month', empty = 'Nothing added in this window.' }) {
  const data = points || []
  // A single point cannot draw a line. Say so rather than rendering one lonely dot on
  // an axis that implies a trend nobody can see.
  if (data.length === 0 || data.every(p => p.count === 0)) {
    return <p className="tp-empty">{empty}</p>
  }

  const unit = granularity === 'week' ? 'week' : 'month'
  // A year of weekly buckets is ~52 labels in ~500px. Thin them rather than letting
  // Recharts drop labels at whatever interval the width happens to allow.
  const interval = data.length > 26 ? Math.ceil(data.length / 12) - 1 : 0

  return (
    <div className="tp-figure">
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <defs>
            <linearGradient id="tpTrendFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={NOMINAL_HUE} stopOpacity={0.22} />
              <stop offset="100%" stopColor={NOMINAL_HUE} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="label" interval={interval} {...AXIS} />
          <YAxis allowDecimals={false} width={44} {...AXIS} />
          <Tooltip
            {...tooltipStyle}
            cursor={{ stroke: '#98a2b3', strokeWidth: 1 }}
            formatter={(v) => [`${v} ${v === 1 ? 'person' : 'people'}`, `Added this ${unit}`]}
          />
          <Area
            type="monotone" dataKey="count" name="Added"
            stroke={NOMINAL_HUE} strokeWidth={2}
            fill="url(#tpTrendFill)"
            dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: '#fff' }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

/**
 * Match-score distribution across ten 10-point bands.
 *
 * The bands are bins on a scale, so they are one series in one hue — length is the
 * only thing that varies, because it is the only thing that means anything. People
 * with no score at all are NOT a band: "never ranked" is not "scored zero", and
 * folding them into 0–9 would invent a cohort of terrible candidates out of people
 * nobody has ranked. They are reported beside the chart instead.
 */
export function ScoreHistogram({ bands, empty = 'Nobody here has been ranked yet.' }) {
  const data = bands || []
  if (data.length === 0 || data.every(b => b.count === 0)) {
    return <p className="tp-empty">{empty}</p>
  }

  return (
    <div className="tp-figure">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }} barCategoryGap={2}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="band" {...AXIS} />
          <YAxis allowDecimals={false} width={44} {...AXIS} />
          <Tooltip
            {...tooltipStyle}
            cursor={{ fill: 'rgba(14,79,160,0.06)' }}
            formatter={(v) => [`${v} ${v === 1 ? 'person' : 'people'}`, 'Scored in this band']}
            labelFormatter={(b) => `Match score ${b}`}
          />
          {/* 4px rounded top on the data end, square on the baseline it is anchored to. */}
          <Bar dataKey="count" name="People" radius={[4, 4, 0, 0]} maxBarSize={46}>
            {data.map((b) => <Cell key={b.floor} fill={NOMINAL_HUE} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
