/**
 * Cross-browser fullscreen, for the proctored exam.
 *
 * What this can and cannot do, because the difference matters to how it is used:
 *
 * It CANNOT stop a candidate reaching another tab. Browsers guarantee that Escape and
 * F11 leave fullscreen and that guarantee cannot be suppressed — a page that could trap
 * you in fullscreen would be a phishing tool. Alt+Tab is untouched either way.
 *
 * What it CAN do is make leaving *visible and expensive*: the exam notices, hides the
 * questions, and records a strike. That is the whole of the integrity story here; the
 * caller should not describe it to a candidate as anything stronger.
 *
 * The vendor-prefixed branches are for Safari, which still ships `webkit`-prefixed names
 * only. iOS Safari on iPhone has no element fullscreen at all, which is why
 * `isSupported()` exists and why callers must degrade rather than block.
 */

/** Is element fullscreen available and permitted here? False on iOS Safari, and in an
 *  iframe without `allow="fullscreen"`. */
export function isSupported() {
  if (typeof document === 'undefined') return false
  const el = document.documentElement
  const hasApi = !!(el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen)
  // fullscreenEnabled is false when a containing iframe withholds the permission, in
  // which case the method exists but every call rejects.
  const permitted = document.fullscreenEnabled ?? document.webkitFullscreenEnabled ?? true
  return hasApi && permitted !== false
}

/** Are we in fullscreen right now? */
export function isFullscreen() {
  if (typeof document === 'undefined') return false
  return !!(document.fullscreenElement
    || document.webkitFullscreenElement
    || document.msFullscreenElement)
}

/**
 * Ask for fullscreen. Resolves true on success, false on refusal — never throws.
 *
 * MUST be called synchronously from a user gesture (a click). Browsers reject the
 * request otherwise, which is why the exam enters fullscreen from the Start button on
 * the instructions page rather than from an effect when the exam route mounts: by then
 * the gesture is gone.
 */
export async function requestFullscreen(target) {
  if (!isSupported()) return false
  const el = target || document.documentElement
  const fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen
  if (!fn) return false
  try {
    // `navigationUI: hide` is a hint; browsers that do not know it ignore the argument.
    await fn.call(el, { navigationUI: 'hide' })
    return isFullscreen()
  } catch {
    return false
  }
}

/** Leave fullscreen. Safe to call when not in it. */
export async function exitFullscreen() {
  if (!isFullscreen()) return
  const fn = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen
  if (!fn) return
  try {
    await fn.call(document)
  } catch {
    /* Already leaving, or the document went away. Nothing to recover. */
  }
}

/**
 * Subscribe to fullscreen enter/exit. Returns an unsubscribe function.
 *
 * All three event names are registered because a browser fires only the one it knows,
 * and there is no reliable feature test for which — listening to all three costs
 * nothing and missing the right one costs the whole feature.
 */
export function onFullscreenChange(handler) {
  const events = ['fullscreenchange', 'webkitfullscreenchange', 'MSFullscreenChange']
  const wrapped = () => handler(isFullscreen())
  events.forEach(e => document.addEventListener(e, wrapped))
  return () => events.forEach(e => document.removeEventListener(e, wrapped))
}
