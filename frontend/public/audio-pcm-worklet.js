/**
 * Mic capture worklet for proctoring.
 *
 * The browser hands us float32 at the AudioContext's own rate (48kHz on most machines).
 * The backend's AudioProctor expects 16-bit signed little-endian mono PCM at 16kHz —
 * `struct.unpack("<Nh", pcm_bytes)` — so the conversion has to happen somewhere. Doing it
 * here keeps it off the main thread, where the countdown, the exam UI and the video frame
 * pump are already competing.
 *
 * Posts ~250ms buffers; the main thread forwards them over the audio WebSocket.
 */
const TARGET_RATE = 16000
const CHUNK_MS = 250

class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super()
    // sampleRate is a global inside the worklet scope — the context's real rate.
    this._ratio = sampleRate / TARGET_RATE
    this._buffer = new Int16Array(Math.round((TARGET_RATE * CHUNK_MS) / 1000))
    this._written = 0
    // Fractional read position into the incoming block, carried across blocks so the
    // decimation doesn't drift or click at block boundaries.
    this._pos = 0
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || !input.length) return true
    const channel = input[0]
    if (!channel || !channel.length) return true

    // Linear decimation to 16kHz. Not a resampling filter — for speech-band analysis
    // (VAD, MFCC, level) the aliasing this leaves is well below what the detectors care
    // about, and a proper polyphase filter is not worth the CPU per session.
    while (this._pos < channel.length) {
      const i = Math.floor(this._pos)
      let s = channel[i]
      if (s > 1) s = 1
      else if (s < -1) s = -1
      this._buffer[this._written++] = s < 0 ? s * 0x8000 : s * 0x7fff

      if (this._written >= this._buffer.length) {
        // Copy: the port transfers ownership, and this._buffer keeps being written.
        this.port.postMessage(this._buffer.slice().buffer)
        this._written = 0
      }
      this._pos += this._ratio
    }
    this._pos -= channel.length

    return true
  }
}

registerProcessor('pcm-downsampler', PcmDownsampler)
