// Wire @monaco-editor/react to the LOCALLY bundled monaco-editor instead of the
// default jsDelivr CDN, so the code editor works fully offline (important for an
// exam app). Importing this module once (for its side effects) is enough.
//
// For our languages (python, java, cpp, javascript, sql) syntax highlighting is
// Monarch-based and needs no language service worker — the generic editor worker
// is sufficient. We therefore wire only the editor worker and skip the heavier
// per-language workers (ts/json/css/html), keeping the bundle lean.
// All setup is wrapped defensively: a worker/loader failure must DEGRADE (fall back
// to @monaco-editor/react's default CDN loader, or the textarea fallback upstream),
// never throw at import time and blank the page.
import * as monaco from 'monaco-editor'
import { loader } from '@monaco-editor/react'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'

try {
  self.MonacoEnvironment = {
    getWorker() {
      return new EditorWorker()
    },
  }
  loader.config({ monaco })
} catch (e) {
  // eslint-disable-next-line no-console
  console.error('[monacoSetup] local Monaco wiring failed; using default loader', e)
}
