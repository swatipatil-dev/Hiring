import { Component } from 'react'

/**
 * Catches render/lifecycle errors in its subtree and shows a fallback instead of
 * letting one crash blank the whole app (React unmounts the tree on an uncaught error).
 *
 * Props:
 *   - fallback: ReactNode rendered when an error is caught (e.g. a plain <textarea>
 *     in place of the Monaco editor). If omitted, a default error card is shown.
 *   - onError(error, info): optional callback (logging).
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Surface to the console so the real cause is visible even though we don't blank.
    console.error('[ErrorBoundary] caught:', error, info)
    this.props.onError?.(error, info)
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback !== undefined) return this.props.fallback
      return (
        <div style={S.wrap}>
          <h2 style={S.title}>Something went wrong</h2>
          <pre style={S.msg}>{String(this.state.error?.message || this.state.error)}</pre>
          <button style={S.btn} onClick={() => window.location.reload()}>Reload</button>
        </div>
      )
    }
    return this.props.children
  }
}

const S = {
  wrap: { minHeight: '60vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, padding: 24, fontFamily: "'Source Sans 3','Segoe UI',sans-serif" },
  title: { margin: 0, fontSize: 18, fontWeight: 800, color: '#b91c1c' },
  msg: { maxWidth: 720, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: "'JetBrains Mono',monospace", fontSize: 12.5, color: '#444', background: '#fff7f7', border: '1px solid #fecaca', borderRadius: 6, padding: '10px 12px' },
  btn: { padding: '8px 22px', borderRadius: 6, border: 'none', background: '#1a73e8', color: '#fff', fontSize: 13, fontWeight: 700, cursor: 'pointer' },
}
