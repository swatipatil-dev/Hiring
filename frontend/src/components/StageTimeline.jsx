import { CheckCircle2, Circle, Clock, XCircle } from 'lucide-react'

const STAGES = [
  'Applied',
  'ATS Screening',
  'HR Review',
  'Shortlisted',
  'Test',
  'Interview',
  'Interview Completed',
  'Hired',
]

const STAGE_COLORS = {
  completed: '#4ade80',
  current:   '#d4a853',
  pending:   '#5c5955',
  rejected:  '#ef4444',
}

export default function StageTimeline({ currentStage = 0, stages = STAGES, timestamps = {}, isRejected = false }) {
  return (
    <div className="timeline-container card">
      <div className="timeline-header">
        <h3>Hiring Stage Progress</h3>
        {isRejected ? (
          <span style={{ padding: '3px 10px', borderRadius: 6, background: '#fef2f2', color: '#ef4444', fontSize: '0.75rem', fontWeight: 700, border: '1px solid #fecaca' }}>
            Not Selected
          </span>
        ) : (
          <span className="badge badge-amber">{currentStage + 1} of {stages.length}</span>
        )}
      </div>

      {isRejected && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 10, background: '#fef2f2', border: '1px solid #fecaca', marginBottom: 20, fontSize: '0.84rem', color: '#dc2626' }}>
          <XCircle size={16} />
          Your application was not selected at this stage. Thank you for applying.
        </div>
      )}

      <div className="timeline">
        {stages.map((stage, i) => {
          const status = i < currentStage ? 'completed' : i === currentStage ? 'current' : 'pending'
          const color = isRejected && i === currentStage ? STAGE_COLORS.rejected : STAGE_COLORS[status]
          return (
            <div key={i} className={`timeline-item ${status}`}>
              <div className="timeline-marker" style={{ '--stage-color': color }}>
                {isRejected && i === currentStage ? <XCircle size={18} /> :
                 status === 'completed'            ? <CheckCircle2 size={18} /> :
                 status === 'current'              ? <Clock size={18} /> :
                 <Circle size={18} />}
              </div>
              <div className="timeline-content">
                <div className="timeline-label">{stage}</div>
                {timestamps[i] && (
                  <div className="timeline-time">{timestamps[i]}</div>
                )}
              </div>
              {i < stages.length - 1 && (
                <div className="timeline-connector" style={{
                  background: i < currentStage ? STAGE_COLORS.completed : 'var(--border-subtle)'
                }} />
              )}
            </div>
          )
        })}
      </div>

      <style>{`
        .timeline-container { padding: 24px; }
        .timeline-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 24px;
        }
        .timeline {
          position: relative;
          display: flex;
          flex-direction: column;
          gap: 0;
        }
        .timeline-item {
          display: flex;
          align-items: flex-start;
          gap: 16px;
          position: relative;
          padding-bottom: 24px;
        }
        .timeline-item:last-child { padding-bottom: 0; }
        .timeline-marker {
          color: var(--stage-color);
          flex-shrink: 0;
          position: relative;
          z-index: 1;
        }
        .timeline-item.current .timeline-marker {
          animation: pulse-glow 2s ease infinite;
        }
        @keyframes pulse-glow {
          0%, 100% { filter: drop-shadow(0 0 4px var(--accent-glow)); }
          50% { filter: drop-shadow(0 0 12px var(--accent-glow-strong)); }
        }
        .timeline-content { flex: 1; }
        .timeline-label {
          font-size: 0.9rem;
          font-weight: 500;
        }
        .timeline-item.completed .timeline-label { color: var(--text-primary); }
        .timeline-item.current .timeline-label { color: var(--accent-primary); font-weight: 600; }
        .timeline-item.pending .timeline-label { color: var(--text-tertiary); }
        .timeline-time {
          font-family: var(--font-mono);
          font-size: 0.7rem;
          color: var(--text-tertiary);
          margin-top: 2px;
        }
        .timeline-connector {
          position: absolute;
          left: 8px;
          top: 22px;
          width: 2px;
          height: calc(100% - 14px);
          border-radius: 1px;
        }
      `}</style>
    </div>
  )
}
