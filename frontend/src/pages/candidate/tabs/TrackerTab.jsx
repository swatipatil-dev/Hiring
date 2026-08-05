import { Briefcase } from 'lucide-react'
import api from '../../../api/client'
import StageTimeline from '../../../components/StageTimeline'
import { STATUS_BADGES, STAGE_LABELS } from '../utils'

const STAGE_MAP = {
  'applied': 0,
  'ats_screening': 1, 'screening': 1,
  'hr_review': 2,
  'shortlisted': 3,
  'test_sent': 4, 'test_in_progress': 4, 'test_submitted': 4, 'test_completed': 4,
  'interview': 5,
  'interview_completed': 6,
  'hired': 7, 'offer_extended': 7, 'offer_sent': 7, 'onboarding': 7,
}

export default function TrackerTab({
  apps,
  selectedApp,
  setSelectedApp,
  appDetails,
  setAppDetails,
  trackerLoading,
  setTrackerLoading,
  refreshApps,
  reportApiError,
}) {
  const loadAppDetails = (appId) => {
    setAppDetails(null)
    setTrackerLoading(true)
    api.get(`/candidate/applications/${appId}`)
      .then(r => {
        setAppDetails(r.data)
        const freshStage = r.data?.current_stage
        if (freshStage) {
          setSelectedApp(prev => prev ? { ...prev, stage: freshStage, status: freshStage } : prev)
        }
      })
      .catch((err) => { setAppDetails(null); reportApiError('Load application progress')(err) })
      .finally(() => setTrackerLoading(false))
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: 20, padding: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: '1rem' }}>Select Job Application</h3>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => { refreshApps(); if (selectedApp) loadAppDetails(selectedApp.id) }}
          >
            ↻ Refresh
          </button>
        </div>
        {apps.length === 0 ? (
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>No applications yet. Browse jobs and apply!</div>
        ) : (
          <select
            className="select"
            value={selectedApp?.id || ''}
            onChange={e => {
              const app = apps.find(a => a.id === e.target.value)
              if (app) {
                setSelectedApp(app)
                loadAppDetails(app.id)
              } else {
                setSelectedApp(null)
                setAppDetails(null)
              }
            }}
          >
            <option value="">— Select a job to track progress —</option>
            {apps.map(a => (
              <option key={a.id} value={a.id}>{a.job}  ·  Applied {a.applied}</option>
            ))}
          </select>
        )}
      </div>

      {!selectedApp && apps.length > 0 && (
        <div className="card" style={{ padding: 48, textAlign: 'center', color: 'var(--text-tertiary)' }}>
          <Briefcase size={40} style={{ marginBottom: 12, opacity: 0.25 }} />
          <div>Select a job above to view your hiring stage progress.</div>
        </div>
      )}

      {trackerLoading && (
        <div className="card" style={{ padding: 48, textAlign: 'center', color: 'var(--text-tertiary)' }}>
          Loading progress…
        </div>
      )}

      {!trackerLoading && selectedApp && appDetails === null && (
        <div className="card" style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>
          Could not load progress data. Please hit Refresh above.
        </div>
      )}

      {!trackerLoading && selectedApp && appDetails !== null && (() => {
        const rawStage = (
          appDetails?.current_stage ||
          selectedApp?.stage ||
          'applied'
        ).toLowerCase().replace(/\s+/g, '_')

        const isRejected = rawStage === 'rejected' || rawStage === 'offer_rejected'

        let stageIdx = 0
        if (isRejected) {
          const lastGood = [...(appDetails?.stage_history || [])].find(
            h => !['rejected', 'offer_rejected'].includes((h.to_stage || '').toLowerCase())
          )
          stageIdx = lastGood ? (STAGE_MAP[(lastGood.to_stage || '').toLowerCase()] ?? 0) : 0
        } else {
          stageIdx = STAGE_MAP[rawStage] ?? 0
        }

        const ts = {}
        if (appDetails?.stage_history) {
          const chronological = [...appDetails.stage_history].reverse()
          chronological.forEach(h => {
            const idx = STAGE_MAP[(h.to_stage || '').toLowerCase().replace(/\s+/g, '_')]
            if (idx !== undefined && ts[idx] === undefined) {
              const dateStr = h.changed_at || h.created_at
              if (dateStr) ts[idx] = new Date(dateStr).toLocaleString()
            }
          })
        }

        return (
          <div>
            <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
              <Briefcase size={18} color="var(--accent-primary)" />
              <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>{selectedApp.job}</span>
              <span className={`badge ${STATUS_BADGES[selectedApp.status] || 'badge-neutral'}`}>
                {STAGE_LABELS[selectedApp.status] || selectedApp.status}
              </span>
            </div>
            <StageTimeline
              currentStage={stageIdx}
              timestamps={ts}
              isRejected={isRejected}
            />
          </div>
        )
      })()}
    </div>
  )
}
