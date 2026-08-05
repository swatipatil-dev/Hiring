import { useState, useEffect } from 'react'

const FACTORS = [
  { key: 'skills', label: 'Skills Match', weight: 35, color: '#d4a853' },
  { key: 'semantic', label: 'Semantic Similarity', weight: 35, color: '#60a5fa' },
  { key: 'experience', label: 'Experience', weight: 20, color: '#4ade80' },
  { key: 'keywords', label: 'Keywords', weight: 10, color: '#a78bfa' },
]

function RadialScore({ score, size = 120, strokeWidth = 8 }) {
  const r = (size - strokeWidth) / 2
  const circ = 2 * Math.PI * r
  const offset = circ - (score / 100) * circ

  return (
    <svg width={size} height={size} className="radial-score">
      <circle cx={size/2} cy={size/2} r={r} fill="none"
        stroke="var(--border-subtle)" strokeWidth={strokeWidth} />
      <circle cx={size/2} cy={size/2} r={r} fill="none"
        stroke="var(--accent-primary)" strokeWidth={strokeWidth}
        strokeDasharray={circ} strokeDashoffset={offset}
        strokeLinecap="round"
        transform={`rotate(-90 ${size/2} ${size/2})`}
        style={{ transition: 'stroke-dashoffset 1s var(--ease-out-expo)' }} />
      <text x={size/2} y={size/2 - 6} textAnchor="middle" fill="var(--text-primary)"
        fontFamily="var(--font-display)" fontSize="1.8rem" fontWeight="700">
        {score}
      </text>
      <text x={size/2} y={size/2 + 14} textAnchor="middle" fill="var(--text-tertiary)"
        fontFamily="var(--font-mono)" fontSize="0.6rem" letterSpacing="0.1em">
        OVERALL
      </text>
    </svg>
  )
}

function FactorBar({ factor, value, animDelay }) {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const t = setTimeout(() => setWidth(value), 100 + animDelay)
    return () => clearTimeout(t)
  }, [value, animDelay])

  return (
    <div className="factor-row">
      <div className="factor-header">
        <span className="factor-label">{factor.label}</span>
        <span className="factor-meta">
          <span className="mono" style={{ color: factor.color }}>{value}%</span>
          <span className="factor-weight">w:{factor.weight}%</span>
        </span>
      </div>
      <div className="factor-track">
        <div className="factor-fill" style={{
          width: `${width}%`,
          background: `linear-gradient(90deg, ${factor.color}33, ${factor.color})`,
          transition: `width 0.8s var(--ease-out-expo) ${animDelay}ms`
        }}>
          <div className="factor-glow" style={{ background: factor.color }} />
        </div>
      </div>
    </div>
  )
}

export default function ATSScorePanel({ scores = { skills: 82, semantic: 74, experience: 90, keywords: 65 }, overall = 78 }) {
  return (
    <div className="ats-panel card">
      <div className="ats-header">
        <h3>ATS Score Breakdown</h3>
        <span className="badge badge-amber">4-Factor Algorithm</span>
      </div>
      <div className="ats-body">
        <div className="ats-radial">
          <RadialScore score={overall} />
          <div className="ats-verdict">
            {overall >= 80 ? 'Strong Match' : overall >= 60 ? 'Good Match' : 'Needs Review'}
          </div>
        </div>
        <div className="ats-factors">
          {FACTORS.map((f, i) => (
            <FactorBar key={f.key} factor={f} value={scores[f.key]} animDelay={i * 120} />
          ))}
        </div>
      </div>

      <style>{`
        .ats-panel { padding: 24px; }
        .ats-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 24px;
        }
        .ats-body {
          display: flex;
          gap: 32px;
          align-items: center;
        }
        .ats-radial {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 8px;
          flex-shrink: 0;
        }
        .ats-verdict {
          font-family: var(--font-mono);
          font-size: 0.75rem;
          color: var(--accent-primary);
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }
        .ats-factors { flex: 1; display: flex; flex-direction: column; gap: 14px; }
        .factor-row {}
        .factor-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 6px;
        }
        .factor-label {
          font-size: 0.85rem;
          color: var(--text-secondary);
          font-weight: 500;
        }
        .factor-meta { display: flex; align-items: center; gap: 10px; }
        .factor-weight {
          font-family: var(--font-mono);
          font-size: 0.65rem;
          color: var(--text-tertiary);
          background: var(--bg-hover);
          padding: 2px 6px;
          border-radius: 4px;
        }
        .factor-track {
          height: 6px;
          background: var(--bg-hover);
          border-radius: 3px;
          overflow: hidden;
          position: relative;
        }
        .factor-fill {
          height: 100%;
          border-radius: 3px;
          position: relative;
        }
        .factor-glow {
          position: absolute;
          right: 0;
          top: -2px;
          width: 10px;
          height: 10px;
          border-radius: 50%;
          filter: blur(4px);
          opacity: 0.6;
        }
        @media (max-width: 640px) {
          .ats-body { flex-direction: column; }
        }
      `}</style>
    </div>
  )
}
