/**
 * Which company is this page for?
 *
 * A company is identified in the URL by its **slug** — the public, brandable path in
 * `career.innomaxskills.com/<slug>`. A slug is an identifier, not a credential, so it is
 * safe in a URL, a log, a `Referer` header or a bookmark.
 *
 * It replaces `?key=sk_live_…`, which put `Company.api_key` — a real secret — into every
 * shared link, every access-log line and every browser history entry. Old links still
 * resolve during the deprecation window: `exchangeLegacyKey()` below trades the key for a
 * slug once, then rewrites the address bar so the secret stops travelling.
 *
 * One module so no component reaches into `localStorage` or `searchParams` on its own —
 * that is how the key ended up in six places.
 */
import api from '../api/client'

export const SLUG_KEY = 'hireflow_company_slug'
const LEGACY_KEY = 'hireflow_company_key'

function safeGet(k) {
  try { return localStorage.getItem(k) } catch { return null }
}

function safeSet(k, v) {
  try { localStorage.setItem(k, v) } catch { /* private mode / storage disabled */ }
}

function safeRemove(k) {
  try { localStorage.removeItem(k) } catch { /* ignore */ }
}

/**
 * Drop any `sk_live_` key a previous version of the app left behind.
 *
 * Deliberately does not try to resolve it into a slug: the callers that need a slug
 * synchronously (route guards, redirects) cannot await a fetch, and a stale key is more
 * likely dead than useful. Falling through to `/login` is a correct outcome.
 */
export function migrateLegacyKey() {
  safeRemove(LEGACY_KEY)
}

/** The stored slug, if this browser has been through a company portal before. */
export function getStoredSlug() {
  migrateLegacyKey()
  const slug = safeGet(SLUG_KEY)
  if (slug) return slug
  // A signed-in user carries their slug in the JWT, mirrored onto the cached user by
  // AuthContext. Authoritative, and free — no lookup, no storage.
  try {
    return JSON.parse(safeGet('hireflow_user') || 'null')?.company_slug || null
  } catch {
    return null
  }
}

export function storeSlug(slug) {
  if (!slug) return
  safeSet(SLUG_KEY, slug)
  migrateLegacyKey()
}

export function clearSlug() {
  safeRemove(SLUG_KEY)
  migrateLegacyKey()
}

/**
 * Resolve the company for a public page: route param first, then `?c=`, then whatever
 * this browser already knows.
 *
 * @param {object} params  from useParams() — `{ companySlug }` on the /<slug> routes
 * @param {URLSearchParams|string} search
 */
export function resolveSlug(params, search) {
  const qs = typeof search === 'string' ? new URLSearchParams(search) : search
  return params?.companySlug || qs?.get('c') || getStoredSlug() || ''
}

/**
 * Trade a legacy `?key=sk_live_…` for the company's slug and scrub it from the address bar.
 *
 * Returns the slug, or null when there was no key to exchange.
 *
 * `replaceState`, not `pushState`: pushing would leave the secret one Back-press away in
 * the session history. Note what this cannot undo — the first request already reached the
 * access log, and the URL is already in browser history, before any of this runs. What it
 * does fix is every `Referer` sent from here on, anything the user bookmarks or copies out
 * of the address bar, and the link they forward to someone else.
 */
export async function exchangeLegacyKey(search, pathBuilder) {
  const qs = typeof search === 'string' ? new URLSearchParams(search) : search
  const key = qs?.get('key')
  if (!key) return null
  try {
    const { data } = await api.post('/public/resolve-key', { key })
    if (!data?.slug) return null
    storeSlug(data.slug)
    try {
      window.history.replaceState(null, '', pathBuilder(data.slug))
    } catch { /* history blocked — the slug still resolved, page works */ }
    return data.slug
  } catch {
    // Dead or rotated key. Nothing to migrate; the caller falls back to its own error path.
    return null
  }
}
