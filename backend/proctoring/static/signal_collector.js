/**
 * Behavioral Signal Collector — captures keystroke, mouse, clipboard, and focus events.
 * Sends data via WebSocket every 5 seconds as JSON payload.
 *
 * Usage:
 *   const collector = new SignalCollector(wsUrl);
 *   collector.start();
 *   // ... on exam end:
 *   collector.stop();
 */
class SignalCollector {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.running = false;
    this.sendInterval = null;

    this.data = {
      keystrokes: [],        // timestamps of keydown events
      mouse_positions: [],   // {x, y, t} sampled every 100ms
      copy_paste: [],        // timestamps
      tab_switches: [],      // timestamps of visibility changes
      question_times: [],    // durations per question (set externally)
    };

    this._lastMouseSample = 0;
    this._handlers = {};
  }

  start() {
    if (this.running) return;
    this.running = true;

    // Connect WebSocket
    this.ws = new WebSocket(this.wsUrl);
    this.ws.onopen = () => console.log('[SignalCollector] Connected');
    this.ws.onclose = () => console.log('[SignalCollector] Disconnected');

    // Keystroke tracking
    this._handlers.keydown = (e) => {
      this.data.keystrokes.push(Date.now());
    };
    document.addEventListener('keydown', this._handlers.keydown);

    // Mouse movement (sampled every 100ms)
    this._handlers.mousemove = (e) => {
      const now = Date.now();
      if (now - this._lastMouseSample < 100) return;
      this._lastMouseSample = now;
      this.data.mouse_positions.push({ x: e.clientX, y: e.clientY, t: now });
    };
    document.addEventListener('mousemove', this._handlers.mousemove);

    // Copy-paste events
    this._handlers.copy = () => this.data.copy_paste.push(Date.now());
    this._handlers.paste = () => this.data.copy_paste.push(Date.now());
    document.addEventListener('copy', this._handlers.copy);
    document.addEventListener('paste', this._handlers.paste);

    // Tab visibility
    this._handlers.visibility = () => {
      if (document.hidden) {
        this.data.tab_switches.push(Date.now());
      }
    };
    document.addEventListener('visibilitychange', this._handlers.visibility);

    // Focus/blur on exam window
    this._handlers.blur = () => {
      this.data.tab_switches.push(Date.now());
    };
    window.addEventListener('blur', this._handlers.blur);

    // Send data every 5 seconds
    this.sendInterval = setInterval(() => this._flush(), 5000);
  }

  stop() {
    this.running = false;
    clearInterval(this.sendInterval);

    document.removeEventListener('keydown', this._handlers.keydown);
    document.removeEventListener('mousemove', this._handlers.mousemove);
    document.removeEventListener('copy', this._handlers.copy);
    document.removeEventListener('paste', this._handlers.paste);
    document.removeEventListener('visibilitychange', this._handlers.visibility);
    window.removeEventListener('blur', this._handlers.blur);

    this._flush();  // send remaining
    if (this.ws) this.ws.close();
  }

  recordQuestionTime(durationMs) {
    this.data.question_times.push(durationMs);
  }

  _flush() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(this.data));
    // Reset after send
    this.data.keystrokes = [];
    this.data.mouse_positions = [];
    this.data.copy_paste = [];
    this.data.tab_switches = [];
    // question_times are kept (cumulative)
  }
}

// Export for module usage
if (typeof module !== 'undefined') module.exports = SignalCollector;
