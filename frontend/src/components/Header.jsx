import { Bell } from 'lucide-react'

export default function Header({ title, subtitle, onBellClick, unreadCount = 0 }) {
  // NOTE: a non-functional "Search anything..." input used to sit here. It had no
  // value, no onChange and no handler, so it read as a broken control on every
  // dashboard. Removed rather than faked — restore it only alongside a real
  // global-search endpoint.
  return (
    <header className="top-header">
      <div className="header-left">
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </div>
      <div className="header-right">
        <button
          className="header-icon-btn"
          onClick={onBellClick}
          aria-label={unreadCount > 0 ? `Notifications, ${unreadCount} unread` : 'Notifications'}
        >
          <Bell size={18} color={unreadCount > 0 ? 'var(--accent-primary)' : undefined} />
          {unreadCount > 0 && <span className="notif-dot" />}
        </button>
      </div>

      <style>{`
        .top-header {
          position: fixed;
          top: 0;
          left: var(--sidebar-width);
          right: 0;
          height: var(--header-height);
          background: rgba(250, 249, 247, 0.88);
          backdrop-filter: blur(20px) saturate(1.2);
          border-bottom: 1px solid var(--border-subtle);
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 40px;
          z-index: 90;
        }
        .header-left h1 {
          font-size: 1.2rem;
          margin: 0;
          font-family: var(--font-display);
          font-weight: 700;
          color: var(--text-primary);
        }
        .header-left .subtitle {
          font-size: 0.78rem;
          color: var(--text-tertiary);
          margin-top: 2px;
          font-family: var(--font-body);
        }
        .header-right {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .header-icon-btn {
          position: relative;
          width: 36px;
          height: 36px;
          display: grid;
          place-items: center;
          background: var(--bg-elevated);
          border: 1px solid var(--border-subtle);
          border-radius: 10px;
          color: var(--text-secondary);
          cursor: pointer;
          transition: all 0.2s;
        }
        .header-icon-btn:hover {
          border-color: var(--border-accent);
          color: var(--accent-primary);
          background: var(--accent-light);
        }
        /* Below 1024px the sidebar is a drawer, so the header spans the full width and
           leaves room on the left for the menu button (Sidebar.jsx .nav-toggle). */
        @media (max-width: 1024px) {
          .top-header { left: 0; padding: 0 16px 0 64px; }
        }
        @media (max-width: 640px) {
          .header-left { min-width: 0; }
          .header-left h1 { font-size: 1.05rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .header-left .subtitle { display: none; }
        }
        .notif-dot {
          position: absolute;
          top: 6px;
          right: 6px;
          width: 7px;
          height: 7px;
          background: var(--status-danger);
          border-radius: 50%;
          border: 2px solid var(--bg-primary);
        }
      `}</style>
    </header>
  )
}
