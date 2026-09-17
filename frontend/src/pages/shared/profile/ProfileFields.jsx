/**
 * The two primitives every profile card is built from.
 *
 * Shared rather than redefined per file: four components render these, and a
 * second copy is how the cards start drifting apart visually.
 */

/** A labelled value. Renders an em dash rather than a blank cell when empty. */
export function Field({ label, value, mono }) {
  const empty = value === null || value === undefined || value === ''
  return (
    <div>
      <div style={{ fontSize: '0.7rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 4 }}>
        {label}
      </div>
      <div style={{
        fontSize: '0.88rem',
        color: empty ? 'var(--text-tertiary)' : 'var(--text-primary)',
        fontFamily: mono ? 'var(--font-mono)' : 'inherit',
        wordBreak: 'break-word',
      }}>
        {empty ? '—' : value}
      </div>
    </div>
  )
}

export function Card({ children, style }) {
  return (
    <div style={{
      background: 'var(--surface-card)', border: '1px solid var(--border-subtle)',
      borderRadius: 14, padding: 20, marginBottom: 18, ...style,
    }}>{children}</div>
  )
}

/** The header row shared by the account and security cards. */
export function CardHeader({ icon: Icon, title, action }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {Icon && <Icon size={16} style={{ color: 'var(--accent-primary)' }} />}
        <span style={{ fontWeight: 700, fontSize: '0.92rem', color: 'var(--text-primary)' }}>{title}</span>
      </div>
      {action}
    </div>
  )
}

/** Inline success / error line. Colour carries meaning, so it also names itself. */
export function Notice({ kind = 'error', children }) {
  if (!children) return null
  const ok = kind === 'success'
  return (
    <div role="status" style={{
      marginTop: 12, padding: '9px 12px', borderRadius: 8, fontSize: '0.82rem', lineHeight: 1.45,
      background: ok ? 'rgba(45,138,110,0.08)' : 'rgba(199,62,62,0.07)',
      color: ok ? 'var(--status-success)' : 'var(--status-error, #c73e3e)',
      border: `1px solid ${ok ? 'rgba(45,138,110,0.25)' : 'rgba(199,62,62,0.22)'}`,
    }}>{children}</div>
  )
}

export const GRID_AUTOFIT = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 18 }
