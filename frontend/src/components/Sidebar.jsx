import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { clearSlug, getStoredSlug } from '../utils/tenant'
import {
  LayoutDashboard, Users, Building2, BarChart3, Briefcase, KanbanSquare,
  Calendar, Gift, ScanSearch, ClipboardList, MessageSquare,
  Search, FileText, Clock, LogOut, Shield, UserCircle, UserPlus, Crown, Mail,
  LifeBuoy, BookOpen, SlidersHorizontal, History, Menu, X,
} from 'lucide-react'

const NAV = {
  superadmin: [
    { to: '/superadmin', icon: Crown, label: 'All Companies' },
    { to: '/superadmin/departments', icon: Building2, label: 'Create Department' },
    { to: '/superadmin/support', icon: LifeBuoy, label: 'Support Tickets' },
  ],
  admin: [
    { to: '/admin', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/admin/users', icon: Users, label: 'Users' },
    { to: '/admin/departments', icon: Building2, label: 'Departments' },
    { to: '/admin/analytics', icon: BarChart3, label: 'Analytics' },
    { to: '/admin/email', icon: Mail, label: 'Email Settings' },
    { to: '/admin/support', icon: LifeBuoy, label: 'Help & Support' },
  ],
  hr: [
    { to: '/hr', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/hr/pipeline', icon: KanbanSquare, label: 'Pipeline' },
    { to: '/hr/requirements', icon: UserPlus, label: 'Requirements' },
    { to: '/hr/jobs', icon: Briefcase, label: 'Job Postings' },
    { to: '/hr/exam-drafts', icon: SlidersHorizontal, label: 'Exam Drafts' },
    { to: '/hr/ats', icon: ScanSearch, label: 'ATS Scores' },
    { to: '/hr/talent-pool', icon: Users, label: 'Talent Pool' },
    { to: '/hr/test-results', icon: ClipboardList, label: 'Test Results' },
    { to: '/hr/interviews', icon: Calendar, label: 'Interviews' },
    { to: '/hr/feedback', icon: MessageSquare, label: 'Feedback' },
    { to: '/hr/offers', icon: Gift, label: 'Offers' },
    { to: '/hr/notifications', icon: Mail, label: 'Email Notifications' },
  ],
  interviewer: [
    { to: '/interviewer', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/interviewer/request-candidates', icon: UserPlus, label: 'Request Candidates' },
    { to: '/interviewer/create-test', icon: ScanSearch, label: 'Create Test' },
    { to: '/interviewer/edit-exam', icon: SlidersHorizontal, label: 'Edit Exam/Questions' },
    { to: '/interviewer/interviews', icon: ClipboardList, label: 'My Interviews' },
    { to: '/interviewer/feedback-history', icon: History, label: 'Feedback History' },
  ],
  candidate: [
    { to: '/candidate', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/candidate/jobs', icon: Search, label: 'Browse Jobs' },
    { to: '/candidate/applications', icon: FileText, label: 'My Applications' },
    { to: '/candidate/interviews', icon: ClipboardList, label: 'My Interviews' },
    { to: '/candidate/status', icon: Clock, label: 'Stage Tracker' },
    { to: '/candidate/courses', icon: BookOpen, label: 'Courses' },
    { to: '/candidate/profile', icon: UserCircle, label: 'Profile' },
  ],
}

const ROLE_ICONS = { superadmin: Crown, admin: Shield, hr: Briefcase, interviewer: ClipboardList, candidate: UserCircle }
const ROLE_COLORS = { superadmin: '#f59e0b', admin: '#e87c6c', hr: '#4ecdb5', interviewer: '#6ba3d6', candidate: '#c17f3e' }

const INNOMAX_COMPANY_ID = 'b88497df-ffa7-4a7f-b441-9f9ef365d6df'

// Below this width the rail becomes an off-canvas drawer. Matches the index.css
// breakpoint where .main-content drops its sidebar margin.
const DRAWER_QUERY = '(max-width: 1024px)'

export default function Sidebar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const role = user?.role?.toLowerCase() || 'candidate'
  const items = NAV[role] || NAV.candidate
  const RoleIcon = ROLE_ICONS[role] || UserCircle

  // ── Mobile drawer ──
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const toggleRef = useRef(null)
  const closeRef = useRef(null)
  const closeMenu = () => { setOpen(false); toggleRef.current?.focus() }

  // Picking a page closes the drawer.
  useEffect(() => { setOpen(false) }, [location.pathname])

  // Growing past the breakpoint turns the drawer back into the rail.
  useEffect(() => {
    const mq = window.matchMedia(DRAWER_QUERY)
    const onChange = e => { if (!e.matches) setOpen(false) }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // While open: Escape closes, the page behind doesn't scroll, focus starts on ✕.
  useEffect(() => {
    if (!open) return
    const onKey = e => { if (e.key === 'Escape') closeMenu() }
    document.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [open])

  const handleLogout = () => {
    // The slug rides in the JWT and is mirrored onto the cached user, so it is
    // authoritative here and needs no storage lookup. getStoredSlug is the fallback for
    // a session that pre-dates that claim.
    const companySlug = user?.company_slug || getStoredSlug()
    const isHireFlowUser = !user?.company_id || user.company_id === INNOMAX_COMPANY_ID
    logout()
    clearSlug()
    if (!isHireFlowUser && companySlug) {
      navigate(`/${encodeURIComponent(companySlug)}/jobs`)
    } else {
      navigate('/login')
    }
  }

  // The API returns first_name / last_name — never a `name` field. Reading user.name
  // left every user in every role showing a "?" avatar and the label "User".
  // Fall back to the email local-part so there is always something human on screen.
  const displayName = [user?.first_name, user?.last_name].filter(Boolean).join(' ')
    || user?.email?.split('@')[0]
    || 'User'
  const avatarInitial = (user?.first_name?.[0] || user?.email?.[0] || '?').toUpperCase()

  const isCandidate = role === 'candidate'
  // Platform org (Innomax) → navy + innomax.ai logo. Detect by slug so it works for
  // superadmin/admin/candidates who belong to the innomax company (which has a color),
  // not just company-less users. Real client companies keep their own brand color.
  const isInnomax = !user?.company_id
    || user?.company_slug === 'innomax'
    || user?.company_id === INNOMAX_COMPANY_ID
  // Optional-chained: `user` is read from localStorage and can be null while a token
  // still exists (cleared site data, half-finished logout) — a bare user.company_color
  // threw and blanked the whole shell.
  const sidebarBg = isInnomax ? '#1a1f2e' : user?.company_color
  const navActive = isInnomax ? '#3d8ed6' : 'rgba(255,255,255,0.95)'
  const navActiveBg = isInnomax ? 'rgba(61,142,214,0.14)' : 'rgba(255,255,255,0.15)'
  const avatarBg = isInnomax ? 'rgba(61,142,214,0.16)' : 'rgba(255,255,255,0.15)'
  const avatarColor = isInnomax ? '#3d8ed6' : '#ffffff'

  return (
    <>
    <button ref={toggleRef} type="button" className="nav-toggle"
      aria-label="Open menu" aria-controls="app-sidebar" aria-expanded={open}
      onClick={() => setOpen(true)}>
      <Menu size={20} />
    </button>
    <div className={`sidebar-backdrop${open ? ' open' : ''}`} onClick={closeMenu} aria-hidden="true" />
    <aside
      id="app-sidebar"
      aria-label="Main menu"
      className={`sidebar ${isCandidate ? 'sidebar-candidate' : ''}${open ? ' open' : ''}`}
      style={{
        '--sidebar-bg': sidebarBg,
        '--nav-active': navActive,
        '--nav-active-bg': navActiveBg,
        '--avatar-bg': avatarBg,
        '--avatar-color': avatarColor,
      }}
    >
      {/* Logo area.
          - Company has its own logo  → show it.
          - Platform owner (Innomax)  → show the platform logo asset.
          - Tenant with no logo        → neutral initials placeholder, never the
            platform logo, so one company never sees another's brand. */}
      <button ref={closeRef} type="button" className="sidebar-close" aria-label="Close menu" onClick={closeMenu}>
        <X size={18} />
      </button>

      <div className="sidebar-logo">
        {user?.company_logo ? (
          <img
            src={user.company_logo}
            alt={user?.company_name || 'Company'}
            className="sidebar-logo-img"
          />
        ) : isInnomax ? (
          <img src="/logo.png?v=3" alt="Innomax Skills" className="sidebar-logo-img" />
        ) : (
          <div className="sidebar-logo-placeholder" title={user?.company_name || ''}>
            {(user?.company_name?.trim()?.[0] || '?').toUpperCase()}
          </div>
        )}
      </div>

      {/* Decorative line (hidden for candidate per redesign) */}
      {!isCandidate && (
        <div className="sidebar-deco">
          <svg viewBox="0 0 200 12" className="deco-svg">
            <line x1="0" y1="6" x2="60" y2="6" stroke="var(--accent-primary)" strokeWidth="1" />
            <polygon points="60,2 68,6 60,10" fill="var(--accent-primary)" />
            <circle cx="100" cy="6" r="3" fill="none" stroke="var(--accent-primary)" strokeWidth="1" />
            <polygon points="132,2 140,6 132,10" fill="var(--accent-primary)" transform="scale(-1,1) translate(-272,0)" />
            <line x1="140" y1="6" x2="200" y2="6" stroke="var(--accent-primary)" strokeWidth="1" />
          </svg>
        </div>
      )}

      {/* Role chip / badge */}
      {isCandidate ? (
        <div className="sidebar-role-chip">
          <span className="role-dot" />
          <span>CANDIDATE</span>
        </div>
      ) : (
        <div className="sidebar-role" style={{ '--role-color': ROLE_COLORS[role] }}>
          <RoleIcon size={14} />
          <span>{role.toUpperCase()}</span>
        </div>
      )}

      {/* Navigation */}
      <nav className="sidebar-nav">
        {items.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === `/${role}`}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}

      </nav>

      {/* User + Logout */}
      <div className="sidebar-footer">
        {/* A NavLink, not an onClick div — that buys keyboard focus, Enter to
            activate, middle-click to open in a tab and the focus ring for free. */}
        <NavLink
          to="/profile"
          className={({ isActive }) => `sidebar-user${isActive ? ' active' : ''}`}
          aria-label="View my profile"
        >
          <div className="user-avatar">{avatarInitial}</div>
          <div className="user-info">
            <div className="user-name" title={displayName}>{displayName}</div>
            <div className="user-email" title={user?.email || ''}>{user?.email || ''}</div>
          </div>
        </NavLink>
        <button className="nav-item logout-btn" onClick={handleLogout}>
          <LogOut size={18} />
          <span>Sign Out</span>
        </button>
      </div>

      <style>{`
        .sidebar {
          position: fixed;
          top: 0;
          left: 0;
          width: var(--sidebar-width);
          height: 100vh;
          background: var(--sidebar-bg, #1a1f2e);
          display: flex;
          flex-direction: column;
          z-index: 100;
          overflow-y: auto;
        }
        .sidebar-logo {
          padding: 20px 20px 16px;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .sidebar-logo-img {
          height: 130px;
          width: auto;
          max-width: 100%;
          object-fit: contain;
          filter: brightness(1.1);
        }
        .sidebar-logo-placeholder {
          height: 96px;
          width: 96px;
          border-radius: 24px;
          display: grid;
          place-items: center;
          font-size: 2.6rem;
          font-weight: 800;
          color: #ffffff;
          background: rgba(255,255,255,0.16);
          border: 1px solid rgba(255,255,255,0.28);
          letter-spacing: -0.02em;
          user-select: none;
        }
        .sidebar-deco {
          padding: 0 24px;
          margin-bottom: 16px;
        }
        .deco-svg {
          width: 100%;
          height: 12px;
          opacity: 0.25;
        }
        .deco-svg line { stroke: #3d8ed6; }
        .deco-svg polygon { fill: #3d8ed6; }
        .deco-svg circle { stroke: #3d8ed6; }
        .sidebar-role {
          display: flex;
          align-items: center;
          gap: 8px;
          margin: 0 16px 18px;
          padding: 7px 12px;
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 8px;
          color: var(--role-color);
          font-family: var(--font-body);
          font-size: 0.68rem;
          font-weight: 700;
          letter-spacing: 0.1em;
          text-transform: uppercase;
        }
        .sidebar-nav {
          flex: 1;
          padding: 0 12px;
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .nav-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 14px;
          border-radius: 10px;
          color: #8b95a8;
          text-decoration: none;
          font-size: 0.87rem;
          font-weight: 500;
          transition: all 0.2s ease;
          border: none;
          background: none;
          cursor: pointer;
          width: 100%;
          text-align: left;
          font-family: var(--font-body);
          position: relative;
        }
        .nav-item:hover {
          color: #d4dae6;
          background: rgba(255,255,255,0.05);
        }
        .nav-item.active {
          color: var(--nav-active, #4ecdb5);
          background: var(--nav-active-bg, rgba(61,142,214,0.14));
          font-weight: 600;
        }
        .nav-item.active::before {
          content: '';
          position: absolute;
          left: 0;
          top: 50%;
          transform: translateY(-50%);
          width: 3px;
          height: 22px;
          background: var(--nav-active, #3d8ed6);
          border-radius: 0 4px 4px 0;
        }
        .sidebar-footer {
          padding: 14px 12px;
          border-top: 1px solid rgba(255,255,255,0.06);
          margin-top: 8px;
        }
        .sidebar-user {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 12px;
          margin-bottom: 6px;
          /* Added when this became a link to /profile. display:flex must stay —
             it is what lets .user-info's flex:1 drive the truncation below. */
          border-radius: 10px;
          text-decoration: none;
          color: inherit;
          cursor: pointer;
          font-family: var(--font-body);
          transition: background 0.2s ease;
          -webkit-tap-highlight-color: transparent;
        }
        .sidebar-user:hover {
          background: rgba(255,255,255,0.05);
        }
        .sidebar-user:hover .user-name {
          color: #ffffff;
        }
        .sidebar-user.active {
          background: var(--nav-active-bg, rgba(61,142,214,0.14));
        }
        .sidebar-user.active .user-name {
          color: var(--nav-active, #4ecdb5);
        }
        .sidebar-user:focus { outline: none; }
        .sidebar-user:focus-visible {
          outline: 2px solid var(--nav-active, #3d8ed6);
          outline-offset: 2px;
        }
        .user-avatar {
          width: 34px;
          height: 34px;
          flex-shrink: 0;
          border-radius: 10px;
          background: var(--avatar-bg, rgba(78,205,181,0.12));
          color: var(--avatar-color, #4ecdb5);
          display: grid;
          place-items: center;
          font-weight: 700;
          font-size: 0.85rem;
          font-family: var(--font-display);
        }
        /* min-width:0 lets the flex child shrink so the ellipsis below can engage —
           without it a long email pushes past the 260px rail instead of truncating. */
        .user-info {
          min-width: 0;
          flex: 1;
        }
        .user-name {
          font-size: 0.85rem;
          font-weight: 600;
          color: #d4dae6;
          line-height: 1.2;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .user-email {
          font-size: 0.66rem;
          color: #6b7589;
          font-family: var(--font-mono);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .logout-btn {
          color: #e87c6c !important;
          opacity: 0.7;
        }
        .logout-btn:hover {
          opacity: 1;
          background: rgba(232, 124, 108, 0.08) !important;
        }

        /* ── Candidate: inherits brand colour from CSS variable ─────── */
        .sidebar-candidate {
          background: var(--sidebar-bg, #1a1f2e);
        }
        .sidebar-candidate .sidebar-role-chip {
          display: flex;
          align-items: center;
          gap: 8px;
          margin: 0 16px 18px;
          padding: 7px 12px;
          background: rgba(255,255,255,0.05);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 8px;
          color: #c17f3e;
          font-size: 0.68rem;
          font-weight: 700;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          width: auto;
        }
        .sidebar-candidate .sidebar-role-chip .role-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #c17f3e;
          flex-shrink: 0;
        }
        .sidebar-candidate .nav-item.active { background: var(--nav-active-bg, rgba(78,205,181,0.08)); color: var(--nav-active, #4ecdb5); }
        .sidebar-candidate .nav-item.active::before { display: block; }

        .nav-item:focus-visible {
          outline: 2px solid var(--nav-active, #3d8ed6);
          outline-offset: -2px;
        }

        /* ── Mobile / tablet: off-canvas drawer ─────────────────────── */
        .nav-toggle, .sidebar-close, .sidebar-backdrop { display: none; }
        @media (max-width: 1024px) {
          .nav-toggle {
            display: grid;
            place-items: center;
            position: fixed;
            top: calc((var(--header-height) - 40px) / 2);
            left: 12px;
            width: 40px;
            height: 40px;
            z-index: 95;
            background: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            color: var(--text-primary);
            cursor: pointer;
          }
          .nav-toggle:focus-visible { outline: 2px solid var(--accent-primary); outline-offset: 2px; }
          .sidebar {
            width: min(280px, 86vw);
            transform: translateX(-100%);
            /* visibility keeps the closed drawer out of the tab order and away from
               screen readers; it flips only after the slide-out finishes. */
            visibility: hidden;
            transition: transform 0.26s cubic-bezier(0.16, 1, 0.3, 1), visibility 0s linear 0.26s;
          }
          .sidebar.open {
            transform: none;
            visibility: visible;
            transition: transform 0.26s cubic-bezier(0.16, 1, 0.3, 1);
            box-shadow: 0 0 48px rgba(0, 0, 0, 0.35);
          }
          .sidebar-close {
            display: grid;
            place-items: center;
            position: absolute;
            top: 12px;
            right: 12px;
            width: 36px;
            height: 36px;
            border: none;
            border-radius: 10px;
            background: rgba(255,255,255,0.08);
            color: #d4dae6;
            cursor: pointer;
            z-index: 1;
          }
          .sidebar-close:focus-visible { outline: 2px solid var(--nav-active, #3d8ed6); outline-offset: 2px; }
          .sidebar-backdrop {
            display: block;
            position: fixed;
            inset: 0;
            z-index: 99;
            background: rgba(15, 20, 30, 0.45);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.26s ease;
          }
          .sidebar-backdrop.open { opacity: 1; pointer-events: auto; }
          /* The 130px desktop logo would push half the menu below the fold. */
          .sidebar-logo { padding: 16px 56px 12px; }
          .sidebar-logo-img { height: 84px; }
          .sidebar-logo-placeholder { height: 64px; width: 64px; font-size: 1.8rem; border-radius: 18px; }
        }
        @media (prefers-reduced-motion: reduce) {
          .sidebar, .sidebar.open, .sidebar-backdrop { transition: none; }
        }
      `}</style>
    </aside>
    </>
  )
}
