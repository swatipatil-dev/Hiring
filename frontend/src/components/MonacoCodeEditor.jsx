// Monaco editor wrapper, loaded LAZILY (React.lazy) so the heavy monaco-editor
// import never runs at OnlineTest module-eval time. If Monaco fails to load, the
// surrounding <ErrorBoundary> in OnlineTest falls back to a plain <textarea>.
import '../lib/monacoSetup'
import Editor from '@monaco-editor/react'

// "Editorial Exam Hall" warm-charcoal theme — matches the OnlineTest editor pane
// (warm near-black canvas, teal + amber accents) instead of cold VS Code grey.
function defineWarmTheme(monaco) {
  monaco.editor.defineTheme('hireflow-warm', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: '', foreground: 'e8e2d9', background: '211b17' },
      { token: 'comment', foreground: '8a7f74', fontStyle: 'italic' },
      { token: 'keyword', foreground: '4ecdb5' },
      { token: 'keyword.control', foreground: '4ecdb5' },
      { token: 'string', foreground: 'e0a05a' },
      { token: 'number', foreground: 'd9a36b' },
      { token: 'type', foreground: '7fd1c0' },
      { token: 'function', foreground: 'f0e6d8' },
      { token: 'variable', foreground: 'e8e2d9' },
      { token: 'operator', foreground: 'c9bfb2' },
    ],
    colors: {
      'editor.background': '#211b17',
      'editor.foreground': '#e8e2d9',
      'editorLineNumber.foreground': '#5e554c',
      'editorLineNumber.activeForeground': '#9a8f82',
      'editor.lineHighlightBackground': '#2a221d',
      'editor.selectionBackground': '#3a4f49',
      'editor.inactiveSelectionBackground': '#2f3b37',
      'editorCursor.foreground': '#4ecdb5',
      'editorIndentGuide.background': '#322a24',
      'editorIndentGuide.activeBackground': '#473d35',
      'editorWhitespace.foreground': '#3a312b',
      'editorGutter.background': '#211b17',
      'editorWidget.background': '#2a221d',
      'editorWidget.border': '#3a312b',
      'editorSuggestWidget.background': '#2a221d',
      'editorSuggestWidget.selectedBackground': '#3a4f49',
      'scrollbarSlider.background': '#3a312b88',
      'scrollbarSlider.hoverBackground': '#473d35',
    },
  })
}

export default function MonacoCodeEditor({ language, value, onChange }) {
  return (
    <Editor
      height="100%"
      language={language}
      theme="hireflow-warm"
      beforeMount={defineWarmTheme}
      value={value}
      onChange={(v) => onChange(v ?? '')}
      loading={<div style={{ color: '#8a7f74', fontFamily: "'DM Sans', sans-serif", fontSize: 13, padding: 16 }}>Loading editor…</div>}
      options={{
        minimap: { enabled: false }, fontSize: 13.5, tabSize: 4, insertSpaces: true,
        lineHeight: 21, automaticLayout: true, scrollBeyondLastLine: false, smoothScrolling: true,
        fontFamily: "'JetBrains Mono', monospace", fontLigatures: true, fixedOverflowWidgets: true,
        renderWhitespace: 'selection', padding: { top: 12, bottom: 12 }, roundedSelection: true,
        cursorBlinking: 'smooth', guides: { indentation: true },
      }}
    />
  )
}
