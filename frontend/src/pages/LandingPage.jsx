import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

function useCounter(target, duration = 1400, delay = 0) {
  const [value, setValue] = useState(0)
  useEffect(() => {
    const t = setTimeout(() => {
      const start = Date.now()
      const tick = () => {
        const p = Math.min((Date.now() - start) / duration, 1)
        const e = 1 - Math.pow(1 - p, 3)
        setValue(Math.round(e * target))
        if (p < 1) requestAnimationFrame(tick)
      }
      requestAnimationFrame(tick)
    }, delay)
    return () => clearTimeout(t)
  }, [target, duration, delay])
  return value
}

const SC = { applied: '#3b82f6', screening: '#8b5cf6', interview: '#3d8ee6', hired: '#34d399' }

const CANDS = [
  { name: 'Priya S.',  score: 94, stage: 'applied',    i: 'PS' },
  { name: 'Rahul M.', score: 88, stage: 'screening',   i: 'RM' },
  { name: 'Anita K.', score: 91, stage: 'interview',   i: 'AK' },
  { name: 'Dev R.',   score: 76, stage: 'applied',     i: 'DR' },
  { name: 'Sona T.',  score: 83, stage: 'screening',   i: 'ST' },
  { name: 'Kiran P.', score: 95, stage: 'hired',       i: 'KP' },
  { name: 'Arjun B.', score: 79, stage: 'interview',   i: 'AB' },
]

function DashboardMockup() {
  const cols = [
    { key: 'applied',   label: 'Applied',   color: SC.applied   },
    { key: 'screening', label: 'Screening', color: SC.screening },
    { key: 'interview', label: 'Interview', color: SC.interview },
    { key: 'hired',     label: 'Hired',     color: SC.hired     },
  ]
  return (
    <div className="lp-mockup">
      <div className="lp-mock-chrome">
        <div className="lp-mock-dots">
          <span style={{ background: '#ff5f57' }} /><span style={{ background: '#febc2e' }} /><span style={{ background: '#28c840' }} />
        </div>
        <div className="lp-mock-url">hireflow.innomax.ai / hr / pipeline</div>
      </div>
      <div className="lp-mock-shell">
        <div className="lp-mock-sidebar">
          <div className="lp-mock-sidebar-logo">
            <div style={{ width: 22, height: 22, background: 'linear-gradient(135deg, #2563eb 0%, #3d8ee6 100%)', clipPath: 'polygon(50% 0%, 0% 100%, 100% 100%)', borderRadius: 2 }} />
          </div>
          {[0,1,2,3,4,5,6].map(i => <div key={i} className={`lp-mock-nav${i === 1 ? ' lp-mock-nav-active' : ''}`} />)}
        </div>
        <div className="lp-mock-main">
          <div className="lp-mock-stats-row">
            {[['24','Jobs','#3d8ee6'],['138','Candidates','#3b82f6'],['12','Interviews','#8b5cf6'],['5','Offers','#34d399']].map(([v,l,c]) => (
              <div key={l} className="lp-mock-stat-box">
                <div style={{ fontSize: 13, fontWeight: 800, color: c, fontFamily: 'JetBrains Mono,monospace', lineHeight: 1 }}>{v}</div>
                <div style={{ fontSize: 6, color: 'rgba(255,255,255,0.35)', textTransform: 'uppercase', letterSpacing: '0.1em', marginTop: 3 }}>{l}</div>
              </div>
            ))}
          </div>
          <div className="lp-mock-pipeline">
            {cols.map(col => {
              const items = CANDS.filter(c => c.stage === col.key).slice(0, 2)
              return (
                <div key={col.key} className="lp-mock-col">
                  <div className="lp-mock-col-hdr">
                    <span style={{ width: 5, height: 5, borderRadius: '50%', background: col.color, display: 'inline-block', flexShrink: 0 }} />
                    <span style={{ fontSize: 6.5, fontWeight: 700, color: 'rgba(255,255,255,0.6)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{col.label}</span>
                    <span style={{ marginLeft: 'auto', fontSize: 6.5, fontWeight: 700, color: col.color, background: col.color + '22', padding: '1px 5px', borderRadius: 8 }}>{items.length || 1}</span>
                  </div>
                  {(items.length ? items : [{ name: '— —', score: 80, i: '?' }]).map((c, ci) => (
                    <div key={ci} className="lp-mock-card">
                      <div style={{ width: 18, height: 18, borderRadius: 5, background: col.color + '30', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                        <span style={{ fontSize: 5.5, fontWeight: 800, color: col.color }}>{c.i}</span>
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 6.5, fontWeight: 600, color: 'rgba(255,255,255,0.82)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.name}</div>
                        <div style={{ fontSize: 5.5, color: '#3d8ee6', fontWeight: 700, marginTop: 1 }}>ATS {c.score}%</div>
                      </div>
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

const FEATURES = [
  { icon: '◎', title: 'AI Resume Scoring',      desc: '4-factor ATS engine scores skills match, semantic similarity, experience, and keyword density on every resume — automatically.', accent: '#3d8ee6' },
  { icon: '⬡', title: '12-Phase Pipeline',       desc: 'Every hiring stage tracked with an immutable audit trail. From first application to signed offer, nothing falls through the cracks.', accent: '#3b82f6' },
  { icon: '◈', title: 'Smart Assessments',       desc: 'MCQ tests and technical interviews created by your interviewers, assigned automatically, and scored before the next round.', accent: '#8b5cf6' },
  { icon: '◉', title: 'Role-Based Access',       desc: 'HR, Interviewers, Candidates, and Admins each see exactly what they need — and nothing they should not.', accent: '#06b6d4' },
  { icon: '◇', title: 'AI Question Generation', desc: 'Gemini-powered engine generates role-specific interview questions and assessments on demand, tailored to job requirements.', accent: '#6366f1' },
  { icon: '◆', title: 'Offer Management',        desc: 'Create, send, and track offer letters with reusable mail templates. One click to send — full audit trail of every response.', accent: '#34d399' },
]

const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;0,9..144,800;0,9..144,900;1,9..144,700;1,9..144,800;1,9..144,900&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

.lp {
  min-height: 100vh;
  background: #060d1a;
  color: #f0f4ff;
  font-family: 'DM Sans', sans-serif;
  overflow-x: hidden;
  position: relative;
}

/* ── Ambient orbs ── */
.lp-orb {
  position: fixed;
  border-radius: 50%;
  pointer-events: none;
  z-index: 0;
  filter: blur(90px);
}
.lp-orb-1 {
  width: 700px; height: 700px;
  background: radial-gradient(circle, rgba(37,99,235,0.22) 0%, transparent 65%);
  top: -200px; right: -100px;
  animation: lp-drift 14s ease-in-out infinite;
}
.lp-orb-2 {
  width: 500px; height: 500px;
  background: radial-gradient(circle, rgba(61,142,230,0.16) 0%, transparent 65%);
  bottom: 5%; left: -120px;
  animation: lp-drift 18s ease-in-out infinite reverse;
}
.lp-orb-3 {
  width: 350px; height: 350px;
  background: radial-gradient(circle, rgba(99,102,241,0.14) 0%, transparent 65%);
  top: 45%; left: 38%;
  animation: lp-drift 22s ease-in-out infinite 3s;
}
@keyframes lp-drift {
  0%,100% { transform: translate(0,0) scale(1); }
  33%      { transform: translate(35px,-25px) scale(1.06); }
  66%      { transform: translate(-25px,35px) scale(0.94); }
}

/* ── Grain ── */
.lp-grain {
  position: fixed; inset: -150%; width: 400%; height: 400%;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  opacity: 0.018; pointer-events: none; z-index: 1;
  animation: lp-grain 9s steps(1) infinite;
}
@keyframes lp-grain {
  0%,100%{transform:translate(0,0)} 10%{transform:translate(-2%,-3%)} 20%{transform:translate(3%,1%)}
  30%{transform:translate(-1%,4%)} 40%{transform:translate(4%,-2%)} 50%{transform:translate(-3%,3%)}
  60%{transform:translate(2%,-4%)} 70%{transform:translate(-4%,1%)} 80%{transform:translate(1%,3%)} 90%{transform:translate(-2%,-1%)}
}

/* ── Nav ── */
.lp-nav {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  backdrop-filter: blur(20px) saturate(1.4);
  background: rgba(6,13,26,0.88);
  border-bottom: 1px solid rgba(61,142,230,0.1);
}
.lp-nav-inner {
  max-width: 1400px; margin: 0 auto; padding: 0 32px;
  height: 64px; display: flex; align-items: center; gap: 32px;
}
.lp-nav-logo {
  display: flex; align-items: center; gap: 10px; margin-right: auto;
  text-decoration: none;
}
.lp-nav-logo-img {
  height: 40px; width: auto;
  background: rgba(255,255,255,0.06);
  border-radius: 10px;
  padding: 4px 8px;
  border: 1px solid rgba(61,142,230,0.12);
}
.lp-nav-links { display: flex; gap: 32px; }
.lp-nav-link {
  color: rgba(240,244,255,0.45); text-decoration: none;
  font-size: 0.875rem; font-weight: 500; transition: color 0.2s; letter-spacing: 0.01em;
}
.lp-nav-link:hover { color: #f0f4ff; }
.lp-nav-actions { display: flex; gap: 10px; align-items: center; }

/* ── Buttons ── */
.lp-btn-ghost {
  background: transparent; border: 1px solid rgba(255,255,255,0.14);
  color: rgba(240,244,255,0.65); padding: 8px 18px; border-radius: 8px;
  font-size: 0.85rem; font-weight: 500; cursor: pointer; font-family: inherit;
  transition: all 0.2s; letter-spacing: 0.01em;
}
.lp-btn-ghost:hover { border-color: rgba(61,142,230,0.4); color: #3d8ee6; background: rgba(61,142,230,0.06); }
.lp-btn-primary {
  background: linear-gradient(135deg, #1d4ed8 0%, #2570b8 100%);
  border: 1px solid rgba(61,142,230,0.3); color: #fff;
  padding: 8px 20px; border-radius: 8px; font-size: 0.85rem; font-weight: 600;
  cursor: pointer; font-family: inherit; transition: all 0.25s cubic-bezier(0.34,1.56,0.64,1);
}
.lp-btn-primary:hover { transform: translateY(-1px); box-shadow: 0 6px 24px rgba(37,99,235,0.45); filter: brightness(1.1); }
.lp-cta-primary {
  display: inline-flex; align-items: center; gap: 8px;
  background: linear-gradient(135deg, #1d4ed8 0%, #0891b2 100%);
  border: 1px solid rgba(61,142,230,0.3); color: #fff;
  padding: 14px 30px; border-radius: 10px; font-size: 0.95rem; font-weight: 600;
  cursor: pointer; font-family: inherit; transition: all 0.3s cubic-bezier(0.34,1.56,0.64,1);
  letter-spacing: 0.01em;
}
.lp-cta-primary:hover { transform: translateY(-2px); box-shadow: 0 10px 40px rgba(37,99,235,0.5); filter: brightness(1.1); }
.lp-cta-ghost {
  display: inline-flex; align-items: center;
  background: transparent; border: 1px solid rgba(255,255,255,0.14);
  color: rgba(240,244,255,0.6); padding: 14px 30px; border-radius: 10px;
  font-size: 0.95rem; font-weight: 500; cursor: pointer; font-family: inherit;
  transition: all 0.2s; letter-spacing: 0.01em;
}
.lp-cta-ghost:hover { border-color: rgba(61,142,230,0.35); color: #3d8ee6; background: rgba(61,142,230,0.05); }

/* ── Animations ── */
.lp-a1 { animation: lp-up 0.8s cubic-bezier(0.16,1,0.3,1) both 0.05s; }
.lp-a2 { animation: lp-up 0.85s cubic-bezier(0.16,1,0.3,1) both 0.2s; }
.lp-a3 { animation: lp-up 0.85s cubic-bezier(0.16,1,0.3,1) both 0.35s; }
.lp-a4 { animation: lp-up 0.85s cubic-bezier(0.16,1,0.3,1) both 0.5s; }
.lp-a5 { animation: lp-up 0.85s cubic-bezier(0.16,1,0.3,1) both 0.65s; }
.lp-a6 { animation: lp-fadein 1.1s cubic-bezier(0.16,1,0.3,1) both 0.45s; }
@keyframes lp-up    { from{opacity:0;transform:translateY(30px)} to{opacity:1;transform:translateY(0)} }
@keyframes lp-fadein{ from{opacity:0}                             to{opacity:1} }
@keyframes lp-float { 0%,100%{transform:translateY(0) rotate(-2.5deg)} 50%{transform:translateY(-14px) rotate(-2.5deg)} }
@keyframes lp-pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.45;transform:scale(0.8)} }

/* ── Hero ── */
.lp-hero {
  min-height: 100vh; display: flex; align-items: center;
  padding: 96px 32px 72px; max-width: 1400px; margin: 0 auto;
  gap: 64px; position: relative; z-index: 2;
}
.lp-hero-left  { flex: 1.1; min-width: 0; }
.lp-hero-right { flex: 1; min-width: 0; position: relative; display: flex; justify-content: center; align-items: center; }

.lp-badge {
  display: inline-flex; align-items: center; gap: 8px;
  background: rgba(37,99,235,0.1); border: 1px solid rgba(61,142,230,0.25);
  color: #3d8ee6; padding: 6px 16px; border-radius: 100px;
  font-size: 0.76rem; font-weight: 600; letter-spacing: 0.05em; margin-bottom: 28px;
}
.lp-badge-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: linear-gradient(135deg, #2563eb, #3d8ee6);
  flex-shrink: 0; animation: lp-pulse 2.2s ease-in-out infinite;
}

.lp-headline {
  font-family: 'Fraunces', Georgia, serif;
  font-size: clamp(3rem, 5.2vw, 5.4rem);
  font-weight: 900; line-height: 1.04; color: #f0f4ff;
  margin: 0 0 26px; letter-spacing: -0.025em;
}
.lp-headline-accent {
  font-style: italic;
  background: linear-gradient(135deg, #2563eb 0%, #3d8ee6 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}

.lp-subtext {
  font-size: 1.05rem; line-height: 1.75; color: rgba(240,244,255,0.5);
  margin: 0 0 38px; max-width: 490px; font-weight: 400;
}

.lp-ctas { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 52px; }

/* Stats strip */
.lp-stats-strip {
  display: flex; gap: 0;
  border: 1px solid rgba(61,142,230,0.1);
  border-radius: 12px; overflow: hidden;
  background: rgba(255,255,255,0.018);
  max-width: 440px;
}
.lp-stat-cell {
  flex: 1; padding: 18px 10px; text-align: center;
  border-right: 1px solid rgba(61,142,230,0.08);
}
.lp-stat-cell:last-child { border-right: none; }
.lp-stat-val {
  font-family: 'Fraunces', Georgia, serif; font-size: 1.9rem; font-weight: 900;
  background: linear-gradient(135deg, #3b82f6 0%, #3d8ee6 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  line-height: 1; margin-bottom: 5px; letter-spacing: -0.02em;
}
.lp-stat-lbl {
  font-size: 0.62rem; color: rgba(240,244,255,0.35);
  text-transform: uppercase; letter-spacing: 0.09em; font-weight: 600;
}

/* ── Dashboard mockup ── */
.lp-mockup-wrap { position: relative; }
.lp-mock-glow {
  position: absolute; width: 420px; height: 250px;
  background: radial-gradient(ellipse, rgba(37,99,235,0.32) 0%, transparent 70%);
  bottom: -40px; left: 50%; transform: translateX(-50%);
  filter: blur(36px); pointer-events: none; z-index: -1;
}
.lp-mockup {
  width: 100%; max-width: 500px; border-radius: 12px; overflow: hidden;
  border: 1px solid rgba(61,142,230,0.15);
  box-shadow: 0 40px 90px rgba(0,0,0,0.7), 0 0 0 1px rgba(61,142,230,0.05);
  transform: rotate(-2.5deg);
  animation: lp-float 7s ease-in-out infinite;
  background: #0b1428;
}
.lp-mock-chrome {
  background: #0f1e34; padding: 10px 14px;
  display: flex; align-items: center; gap: 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.lp-mock-dots { display: flex; gap: 5px; }
.lp-mock-dots span { width: 9px; height: 9px; border-radius: 50%; display: block; }
.lp-mock-url {
  flex: 1; background: rgba(255,255,255,0.06); border-radius: 4px;
  padding: 3px 10px; font-size: 8.5px; color: rgba(255,255,255,0.28);
  font-family: 'JetBrains Mono', monospace; text-align: center;
}
.lp-mock-shell { display: flex; height: 272px; }
.lp-mock-sidebar {
  width: 44px; background: #070f1f;
  border-right: 1px solid rgba(61,142,230,0.07);
  padding: 10px 7px; display: flex; flex-direction: column;
  gap: 7px; align-items: center;
}
.lp-mock-sidebar-logo {
  width: 26px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 8px;
}
.lp-mock-nav { width: 26px; height: 5px; background: rgba(255,255,255,0.09); border-radius: 3px; }
.lp-mock-nav-active { background: linear-gradient(90deg,#2563eb,#3d8ee6); box-shadow: 0 0 9px rgba(61,142,230,0.5); }
.lp-mock-main {
  flex: 1; padding: 11px; display: flex; flex-direction: column; gap: 10px;
  background: #0a1325; overflow: hidden;
}
.lp-mock-stats-row { display: flex; gap: 7px; }
.lp-mock-stat-box {
  flex: 1; background: rgba(255,255,255,0.038); border: 1px solid rgba(61,142,230,0.08);
  border-radius: 6px; padding: 7px 7px 6px; text-align: center;
}
.lp-mock-pipeline { display: flex; gap: 7px; flex: 1; overflow: hidden; }
.lp-mock-col { flex: 1; display: flex; flex-direction: column; gap: 5px; overflow: hidden; }
.lp-mock-col-hdr {
  display: flex; align-items: center; gap: 4px;
  padding: 4px 6px; background: rgba(255,255,255,0.03); border-radius: 5px; margin-bottom: 2px;
}
.lp-mock-card {
  display: flex; align-items: center; gap: 6px;
  background: rgba(255,255,255,0.038); border: 1px solid rgba(61,142,230,0.07);
  border-radius: 6px; padding: 6px 7px;
}

/* ── Divider ── */
.lp-divider {
  position: relative; z-index: 2; margin: 0;
  border: none; border-top: 1px solid rgba(61,142,230,0.06);
}

/* ── Sections ── */
.lp-section {
  position: relative; z-index: 2; padding: 100px 32px;
  border-top: 1px solid rgba(61,142,230,0.06);
}
.lp-section-inner { max-width: 1400px; margin: 0 auto; }
.lp-overline {
  font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.14em; color: #3d8ee6; margin-bottom: 16px;
}
.lp-s-title {
  font-family: 'Fraunces', Georgia, serif;
  font-size: clamp(2rem, 3.5vw, 3.1rem); font-weight: 800; color: #f0f4ff;
  margin: 0 0 14px; letter-spacing: -0.025em; line-height: 1.1;
}
.lp-s-sub {
  font-size: 1rem; color: rgba(240,244,255,0.42); margin: 0 0 60px; max-width: 460px; line-height: 1.7;
}

/* Features grid */
.lp-feat-grid {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 1px; background: rgba(61,142,230,0.08);
  border: 1px solid rgba(61,142,230,0.08); border-radius: 16px; overflow: hidden;
}
.lp-feat-card {
  background: rgba(8,18,36,0.97); padding: 36px 30px 32px;
  position: relative; transition: background 0.25s; cursor: default;
}
.lp-feat-card:hover { background: rgba(11,24,46,0.99); }
.lp-feat-icon { font-size: 1.5rem; margin-bottom: 16px; display: block; transition: transform 0.3s cubic-bezier(0.34,1.56,0.64,1); }
.lp-feat-card:hover .lp-feat-icon { transform: scale(1.15); }
.lp-feat-title {
  font-family: 'Fraunces', Georgia, serif; font-size: 1.05rem; font-weight: 700;
  color: #f0f4ff; margin: 0 0 10px; letter-spacing: -0.01em;
}
.lp-feat-desc { font-size: 0.85rem; line-height: 1.7; color: rgba(240,244,255,0.42); margin: 0; }
.lp-feat-bar {
  position: absolute; bottom: 0; left: 0; right: 0; height: 2px;
  opacity: 0; transition: opacity 0.3s;
}
.lp-feat-card:hover .lp-feat-bar { opacity: 1; }

/* How it works */
.lp-steps { display: flex; gap: 0; margin-top: 0; }
.lp-step { flex: 1; padding: 0 48px 0 0; position: relative; }
.lp-step:last-child { padding-right: 0; }
.lp-step-num {
  font-family: 'Fraunces', Georgia, serif; font-size: clamp(3rem, 5vw, 5rem);
  font-weight: 900; line-height: 1; margin-bottom: 22px;
  background: linear-gradient(135deg, #2563eb 0%, #3d8ee6 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  letter-spacing: -0.04em; opacity: 0.9;
}
.lp-step-title {
  font-family: 'Fraunces', Georgia, serif; font-size: 1.2rem; font-weight: 700;
  color: #f0f4ff; margin: 0 0 12px; letter-spacing: -0.01em;
}
.lp-step-desc { font-size: 0.875rem; line-height: 1.72; color: rgba(240,244,255,0.42); margin: 0; }
.lp-step-connector {
  position: absolute; top: 30px; right: 0; width: 36px; height: 1px;
  background: linear-gradient(to right, rgba(61,142,230,0.2), transparent);
}

/* Final CTA */
.lp-cta-band {
  position: relative; z-index: 2; padding: 110px 32px;
  background: linear-gradient(135deg, #071428 0%, #060d1a 55%, #051020 100%);
  border-top: 1px solid rgba(61,142,230,0.08); text-align: center;
  overflow: hidden;
}
.lp-cta-band::before {
  content: ''; position: absolute; top: -100px; left: 50%; transform: translateX(-50%);
  width: 600px; height: 400px;
  background: radial-gradient(ellipse, rgba(37,99,235,0.2) 0%, transparent 70%);
  pointer-events: none;
}
.lp-cta-band-inner { max-width: 580px; margin: 0 auto; position: relative; z-index: 1; }
.lp-cta-title {
  font-family: 'Fraunces', Georgia, serif;
  font-size: clamp(2.2rem, 4.5vw, 3.6rem); font-weight: 900;
  color: #f0f4ff; margin: 0 0 16px; letter-spacing: -0.025em; line-height: 1.08;
}
.lp-cta-sub { font-size: 1rem; color: rgba(240,244,255,0.42); margin: 0 0 40px; }
.lp-cta-btns { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }

/* Footer */
.lp-footer {
  position: relative; z-index: 2; padding: 28px 32px;
  border-top: 1px solid rgba(61,142,230,0.07);
  background: #040a14;
}
.lp-footer-inner {
  max-width: 1400px; margin: 0 auto;
  display: flex; justify-content: space-between; align-items: center;
}
.lp-footer-logo-img {
  height: 30px; width: auto;
  background: rgba(255,255,255,0.05);
  border-radius: 8px; padding: 3px 8px;
  border: 1px solid rgba(61,142,230,0.1);
}

/* Responsive */
@media (max-width: 960px) {
  .lp-hero { flex-direction: column; padding: 100px 28px 60px; min-height: auto; gap: 48px; }
  .lp-hero-right { width: 100%; }
  .lp-feat-grid { grid-template-columns: 1fr 1fr; }
  .lp-steps { flex-direction: column; gap: 40px; }
  .lp-step-connector { display: none; }
  .lp-section { padding: 72px 28px; }
  .lp-cta-band { padding: 80px 28px; }
  .lp-nav-links { display: none; }
  .lp-nav-inner { padding: 0 28px; }
  .lp-footer { padding: 24px 28px; }
}
@media (max-width: 600px) {
  .lp-feat-grid { grid-template-columns: 1fr; }
  .lp-stats-strip { display: grid; grid-template-columns: 1fr 1fr; }
  .lp-stat-cell:nth-child(2) { border-right: none; }
  .lp-stat-cell:nth-child(3),
  .lp-stat-cell:nth-child(4) { border-top: 1px solid rgba(61,142,230,0.08); }
  .lp-mockup { transform: rotate(0deg); }
  .lp-footer-inner { flex-direction: column; gap: 10px; text-align: center; }
}
`

export default function LandingPage() {
  const navigate = useNavigate()
  const c1 = useCounter(12,  1200, 200)
  const c2 = useCounter(4,   1000, 100)
  const c3 = useCounter(50,  1300, 600)
  const c4 = useCounter(98,  1400, 400)

  return (
    <>
      <style>{CSS}</style>
      <div className="lp">
        <div className="lp-grain" aria-hidden="true" />
        <div className="lp-orb lp-orb-1" aria-hidden="true" />
        <div className="lp-orb lp-orb-2" aria-hidden="true" />
        <div className="lp-orb lp-orb-3" aria-hidden="true" />

        {/* ── NAV ── */}
        <nav className="lp-nav">
          <div className="lp-nav-inner">
            <div className="lp-nav-logo">
              <img src="/logo.png?v=3" alt="Innomax Skills" className="lp-nav-logo-img" />
            </div>
            <nav className="lp-nav-links">
              <a href="#features" className="lp-nav-link">Features</a>
              <a href="#how"      className="lp-nav-link">How It Works</a>
            </nav>
            <div className="lp-nav-actions">
              <button className="lp-btn-ghost"   onClick={() => navigate('/login')}>Sign In</button>
              <button className="lp-btn-primary" onClick={() => navigate('/register')}>Get Started</button>
            </div>
          </div>
        </nav>

        {/* ── HERO ── */}
        <section className="lp-hero">
          <div className="lp-hero-left">
            <div className="lp-badge lp-a1">
              <span className="lp-badge-dot" />
              AI-Powered · 12-Phase Hiring Workflow
            </div>

            <h1 className="lp-headline lp-a2">
              Hire the right<br />
              people,{' '}
              <span className="lp-headline-accent">automatically.</span>
            </h1>

            <p className="lp-subtext lp-a3">
              HireFlow's AI scores every resume, tracks every candidate through a complete 12-phase
              pipeline, and moves the best talent forward — so your team focuses on people, not paperwork.
            </p>

            <div className="lp-ctas lp-a4">
              <button className="lp-cta-primary" onClick={() => navigate('/register')}>
                Start Free Trial
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
              </button>
              <button className="lp-cta-ghost" onClick={() => navigate('/login')}>
                Sign In
              </button>
            </div>

            <div className="lp-stats-strip lp-a5">
              {[
                { v: c1, s: '',  l: 'Hiring Phases'  },
                { v: c2, s: '',  l: 'User Roles'     },
                { v: c3, s: '+', l: 'Resume Signals' },
                { v: c4, s: '%', l: 'ATS Accuracy'   },
              ].map(({ v, s, l }) => (
                <div key={l} className="lp-stat-cell">
                  <div className="lp-stat-val">{v}{s}</div>
                  <div className="lp-stat-lbl">{l}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="lp-hero-right lp-a6">
            <div className="lp-mockup-wrap">
              <div className="lp-mock-glow" />
              <DashboardMockup />
            </div>
          </div>
        </section>

        {/* ── FEATURES ── */}
        <section className="lp-section" id="features">
          <div className="lp-section-inner">
            <div className="lp-overline">Capabilities</div>
            <h2 className="lp-s-title">Everything your hiring<br />team needs</h2>
            <p className="lp-s-sub">From AI screening to signed offer letters — the complete recruitment stack in one platform.</p>
            <div className="lp-feat-grid">
              {FEATURES.map(f => (
                <div key={f.title} className="lp-feat-card">
                  <span className="lp-feat-icon" style={{ color: f.accent }}>{f.icon}</span>
                  <div className="lp-feat-title">{f.title}</div>
                  <p className="lp-feat-desc">{f.desc}</p>
                  <div className="lp-feat-bar" style={{ background: `linear-gradient(to right, ${f.accent}, transparent)` }} />
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── HOW IT WORKS ── */}
        <section className="lp-section" id="how">
          <div className="lp-section-inner">
            <div className="lp-overline">Process</div>
            <h2 className="lp-s-title">How HireFlow works</h2>
            <p className="lp-s-sub">Three steps from open role to signed offer.</p>
            <div className="lp-steps">
              {[
                { n: '01', title: 'Post a Job',          desc: 'Create a posting with skills, experience, and requirements. The AI engine begins matching incoming applications the moment it goes live.' },
                { n: '02', title: 'AI Screens Everyone', desc: 'Every resume is scored across four factors. Set your threshold — the right candidates advance automatically, others receive a polished rejection email.' },
                { n: '03', title: 'Interview & Close',   desc: 'Schedule rounds, assign assessments, collect interviewer feedback, and extend offer letters — all from one dashboard without switching tools.' },
              ].map((s, i) => (
                <div key={s.n} className="lp-step">
                  <div className="lp-step-num">{s.n}</div>
                  <div className="lp-step-title">{s.title}</div>
                  <p className="lp-step-desc">{s.desc}</p>
                  {i < 2 && <div className="lp-step-connector" />}
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── CTA BAND ── */}
        <section className="lp-cta-band">
          <div className="lp-cta-band-inner">
            <h2 className="lp-cta-title">Start hiring smarter today</h2>
            <p className="lp-cta-sub">Join companies using HireFlow to find better talent, faster — with zero manual screening.</p>
            <div className="lp-cta-btns">
              <button className="lp-cta-primary" onClick={() => navigate('/register')}>
                Create Free Account
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
              </button>
              <button className="lp-cta-ghost" onClick={() => navigate('/login')}>Sign In</button>
            </div>
          </div>
        </section>

        {/* ── FOOTER ── */}
        <footer className="lp-footer">
          <div className="lp-footer-inner">
            <img src="/logo.png?v=3" alt="Innomax Skills" className="lp-footer-logo-img" />
            <p style={{ color: 'rgba(240,244,255,0.25)', fontSize: '0.76rem', letterSpacing: '0.02em' }}>
              © 2026 Innomax Skills · AI-Powered Recruitment
            </p>
          </div>
        </footer>
      </div>
    </>
  )
}
