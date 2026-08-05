import React from 'react'

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
 *  NOMINAL — sources and outcomes are just names. Reordering them changes nothing, so
 *  every bar wears the SAME hue (#0e4fa0, 7.55:1 on surface). Colouring those bars by
 *  their value would spend the identity channel re-encoding what bar length already
 *  shows.
 *
 * Every bar is direct-labelled with its count, so nothing depends on colour alone.
 */

// Ordinal ramp: light → dark across the funnel. Derived from the app's navy.
export const STAGE_RAMP = {
  new: '#7aa7d9',
  ongoing: '#2b6cb0',
  archived: '#0a3b7d',
}

// Single hue for nominal bars.
const NOMINAL_HUE = '#0e4fa0'

export function StatTile({ label, value, hint, tone }) {
  return (
    <div className={`tp-tile${tone ? ` tp-tile--${tone}` : ''}`}>
      <div className="tp-tile__label">{label}</div>
      <div className="tp-tile__value">{value}</div>
      {hint && <div className="tp-tile__hint">{hint}</div>}
    </div>
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
