/**
 * Company slug normalisation for the onboarding UI.
 *
 * Mirrors `backend/src/tenancy/slug.py` so the URL previewed while typing is the one the
 * API will actually store. The backend re-slugifies everything it receives and owns the
 * reserved-name check — this is convenience, never validation.
 */

// Matches the backend's MAX_SLUG_LENGTH: a slug becomes a DNS label (acme.hireflow.io).
export const MAX_SLUG_LENGTH = 63

export function slugify(value) {
  if (!value) return ''
  return String(value)
    // Fold accents to base letters (NFD + strip combining marks) so "Café" gives
    // "cafe", not "caf". Must stay identical to the backend's unicodedata pass.
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')   // any run of non-alphanumerics becomes one hyphen
    .replace(/-{2,}/g, '-')
    .replace(/^-+|-+$/g, '')       // a DNS label may not start or end with a hyphen
    .slice(0, MAX_SLUG_LENGTH)
    .replace(/-+$/, '')            // truncation can re-expose a trailing hyphen
}

export default slugify
