import { Bell, Search, Moon } from 'lucide-react'
import { useAuth } from '../context/AuthContext'

export default function Header({ title, subtitle, onBellClick, unreadCount = 0 }) {
  const { user } = useAuth()
  return (
    <header className="top-header">
      <div className="header-left">
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </div>
      <div className="header-right">
        <div className="header-search">
          <Search size={16} />
          <input type="text" placeholder="Search anything..." className="input" />
        </div>
        <button className="header-icon-btn" onClick={onBellClick}>
          <Bell size={18} color={unreadCount > 0 ? '#2b7cba' : undefined} />
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
        .header-search {
          display: flex;
          align-items: center;
          gap: 8px;
          background: var(--surface-input);
          border: 1px solid var(--border-subtle);
          border-radius: 10px;
          padding: 0 12px;
          width: 220px;
          color: var(--text-tertiary);
          transition: border-color 0.2s;
        }
        .header-search:focus-within {
          border-color: var(--accent-primary);
        }
        .header-search .input {
          border: none;
          background: none;
          padding: 8px 0;
          font-size: 0.83rem;
          box-shadow: none;
        }
        .header-search .input:focus {
          box-shadow: none;
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
