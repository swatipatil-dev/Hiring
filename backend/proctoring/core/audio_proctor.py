"""
Module 3 — Audio AI Pipeline: VAD, multi-speaker detection, Whisper transcription.
Thread-safe design with circular buffer. Raw audio never persisted.
"""
import time
import logging
import threading
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AudioAnalysisResult:
    # VAD
    is_speaking: bool = False
    speech_duration_ms: int = 0
    # Multi-speaker
    voice_count_estimate: int = 0
    multi_speaker_confidence: float = 0.0
    # A voice that isn't the enrolled candidate's. Distinct from voice_count_estimate,
    # which is per-window: if only the intruder speaks in a window, the count is 1 while
    # this is True. Scoring and flagging key off this, never off the count.
    foreign_speech: bool = False
    speaker_enrolled: bool = False
    speaker_distances: List[float] = field(default_factory=list)
    # Level. dBFS — relative to digital full scale, NOT acoustic dB SPL: an uncalibrated
    # browser mic at an unknown distance can't measure sound pressure. Useful for
    # comparing a candidate against their own room over time, not across candidates.
    rms: float = 0.0
    spl_dbfs: float = -120.0
    peak_dbfs: float = -120.0
    noise_floor_dbfs: float = -120.0
    # Transcription
    transcript: str = ""
    flagged_keywords: List[str] = field(default_factory=list)
    language: str = ""
    # Meta
    mic_muted: bool = False
    flags: List[str] = field(default_factory=list)
    processing_time_ms: float = 0.0


class AudioProctor:
    """
    Real-time audio analysis with VAD, speaker detection, and on-demand transcription.
    All processing on a background thread. Raw audio is never stored.
    """

    def __init__(self, config=None):
        from ..config import AudioConfig
        self.cfg = config or AudioConfig()

        self._lock = threading.Lock()
        self._buffer = deque(maxlen=self.cfg.buffer_max_samples)
        self._speech_frames = 0
        self._total_frames = 0
        self._continuous_speech_ms = 0
        self._last_result = AudioAnalysisResult()
        self._running = False
        self._mic_active = True

        # Rolling level state: the noise floor is measured from windows where nobody is
        # speaking, which is what actually reveals a TV or a conversation in the room.
        self._quiet_levels = deque(maxlen=self.cfg.level_history_windows)
        self._consecutive_noisy = 0

        # Lazy-loaded heavy modules
        self._vad = None
        self._whisper_model = None

        from .diarizer import SpeakerDiarizer, DiarizationResult
        self._diarizer = SpeakerDiarizer(self.cfg)
        self._last_diarization = DiarizationResult()
        self._last_diarization_at = 0.0

        self._init_vad()
        logger.info("AudioProctor initialized (diarization engine=%s)", self._diarizer.engine)

    @property
    def diarization_engine(self) -> str:
        return self._diarizer.engine if self._diarizer else "none"

    @staticmethod
    def _to_dbfs(amplitude: float) -> float:
        """int16 amplitude -> dBFS. Digital silence floors at -120 rather than -inf."""
        if amplitude <= 0:
            return -120.0
        return float(max(-120.0, 20.0 * np.log10(amplitude / 32768.0)))

    def _init_vad(self):
        """Initialize WebRTC VAD."""
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self.cfg.vad_aggressiveness)
            logger.info("WebRTC VAD ready (aggressiveness=%d)", self.cfg.vad_aggressiveness)
        except ImportError:
            logger.warning("webrtcvad not installed — VAD disabled")

    def _load_whisper(self):
        """Lazy-load Whisper model."""
        if self._whisper_model is None:
            try:
                import whisper
                self._whisper_model = whisper.load_model(self.cfg.whisper_model)
                logger.info("Whisper model '%s' loaded", self.cfg.whisper_model)
            except ImportError:
                logger.warning("openai-whisper not installed — transcription disabled")

    def ingest_audio_chunk(self, pcm_bytes: bytes):
        """
        Receive a chunk of raw PCM audio (16-bit mono, 16kHz).
        Called from the WebSocket handler.
        """
        with self._lock:
            # Convert bytes to int16 samples
            samples = struct.unpack(f"<{len(pcm_bytes)//2}h", pcm_bytes)
            self._buffer.extend(samples)

    def analyze(self) -> AudioAnalysisResult:
        """
        Run analysis on the current audio buffer. Call periodically (e.g. every 500ms).
        """
        t0 = time.perf_counter()
        result = AudioAnalysisResult()

        with self._lock:
            buf_array = np.array(list(self._buffer), dtype=np.int16)

        if len(buf_array) == 0:
            result.mic_muted = True
            result.flags.append("MIC_MUTED")
            result.processing_time_ms = (time.perf_counter() - t0) * 1000
            self._last_result = result
            return result

        # --- Sub-System A: VAD ---
        result.is_speaking, result.speech_duration_ms = self._run_vad(buf_array)
        if result.is_speaking:
            self._continuous_speech_ms += result.speech_duration_ms
        else:
            self._continuous_speech_ms = 0

        # --- Sub-System B: Level (RMS / dBFS) ---
        self._measure_level(buf_array, result)

        # --- Sub-System C: Speaker diarization ---
        # Rate-limited: this runs per video frame, and embedding speech at frame rate
        # costs more than the vision modules put together. Between runs the last verdict
        # stands, which is also what makes the flag survive long enough to be persisted.
        now = time.time()
        if now - self._last_diarization_at >= self.cfg.diarization_interval_sec:
            self._last_diarization_at = now
            window = buf_array[-int(self.cfg.diarization_window_sec * self.cfg.sample_rate):]
            self._last_diarization = self._diarizer.analyze(self._speech_segments(window))
        diar = self._last_diarization

        result.speaker_enrolled = diar.enrolled
        result.speaker_distances = diar.distances
        result.multi_speaker_confidence = diar.confidence
        result.voice_count_estimate = diar.voice_count
        result.foreign_speech = diar.foreign_speech
        if diar.foreign_speech:
            result.flags.append("MULTIPLE_VOICES")

        # --- Sub-System C: Whisper Transcription (on-demand) ---
        trigger_ms = self.cfg.speech_trigger_seconds * 1000
        if self._continuous_speech_ms >= trigger_ms:
            transcript_result = self._transcribe(buf_array)
            result.transcript = transcript_result.get("text", "")
            result.language = transcript_result.get("language", "")
            result.flagged_keywords = self._check_keywords(result.transcript)
            if result.flagged_keywords:
                result.flags.append("FLAGGED_SPEECH")
            self._continuous_speech_ms = 0  # reset after transcription

        result.processing_time_ms = (time.perf_counter() - t0) * 1000
        self._last_result = result
        return result

    def _run_vad(self, audio: np.ndarray) -> tuple:
        """Run VAD on audio buffer. Returns (is_speaking, speech_duration_ms)."""
        if self._vad is None:
            return False, 0

        frame_size = int(self.cfg.sample_rate * self.cfg.frame_duration_ms / 1000)
        speech_frames = 0
        total_frames = 0

        # Process last 1 second of audio
        start = max(0, len(audio) - self.cfg.sample_rate)
        chunk = audio[start:]

        for i in range(0, len(chunk) - frame_size, frame_size):
            frame = chunk[i:i + frame_size]
            pcm = frame.tobytes()
            try:
                if self._vad.is_speech(pcm, self.cfg.sample_rate):
                    speech_frames += 1
            except Exception:
                pass
            total_frames += 1

        if total_frames == 0:
            return False, 0

        is_speaking = speech_frames / total_frames > 0.4
        duration = int(speech_frames * self.cfg.frame_duration_ms)
        return is_speaking, duration

    def _measure_level(self, audio: np.ndarray, result: AudioAnalysisResult):
        """RMS / peak / rolling noise floor, in dBFS, and the mute + background-noise flags."""
        samples = audio.astype(np.float64)
        result.rms = float(np.sqrt(np.mean(samples ** 2)))
        result.spl_dbfs = self._to_dbfs(result.rms)
        result.peak_dbfs = self._to_dbfs(float(np.max(np.abs(samples))) if samples.size else 0.0)

        # Only quiet windows update the floor: including speech would just measure the
        # candidate's voice and never reveal what is going on behind them.
        if not result.is_speaking:
            self._quiet_levels.append(result.spl_dbfs)
        result.noise_floor_dbfs = (float(np.mean(self._quiet_levels))
                                   if self._quiet_levels else result.spl_dbfs)

        if result.spl_dbfs <= self.cfg.silence_dbfs:
            result.mic_muted = True
            result.flags.append("MIC_MUTED")
            self._consecutive_noisy = 0
            return

        if not result.is_speaking and result.spl_dbfs >= self.cfg.noise_floor_dbfs:
            self._consecutive_noisy += 1
            if self._consecutive_noisy >= self.cfg.noise_confirm_windows:
                result.flags.append("BACKGROUND_NOISE")
        else:
            self._consecutive_noisy = 0

    def _speech_segments(self, audio: np.ndarray) -> List[np.ndarray]:
        """
        Split the buffer into contiguous speech spans using the same VAD as _run_vad.

        Diarization only ever sees speech: silence and room tone embed as noise and would
        drag the candidate's voice anchor towards their room rather than their voice.
        """
        if self._vad is None or audio.size == 0:
            return []

        frame_size = int(self.cfg.sample_rate * self.cfg.frame_duration_ms / 1000)
        min_len = int(self.cfg.min_speech_segment_sec * self.cfg.sample_rate)

        segments, current = [], []
        for i in range(0, len(audio) - frame_size, frame_size):
            frame = audio[i:i + frame_size]
            try:
                speech = self._vad.is_speech(frame.tobytes(), self.cfg.sample_rate)
            except Exception:
                speech = False
            if speech:
                current.append(frame)
            elif current:
                span = np.concatenate(current)
                if span.size >= min_len:
                    segments.append(span)
                current = []

        if current:
            span = np.concatenate(current)
            if span.size >= min_len:
                segments.append(span)
        return segments

    def _transcribe(self, audio: np.ndarray) -> dict:
        """Transcribe audio using Whisper. Audio is NOT stored."""
        self._load_whisper()
        if self._whisper_model is None:
            return {"text": "", "language": ""}
        try:
            float_audio = audio.astype(np.float32) / 32768.0
            result = self._whisper_model.transcribe(float_audio, fp16=False)
            return {"text": result.get("text", "").strip(), "language": result.get("language", "")}
        except Exception as e:
            logger.error("Whisper transcription failed: %s", e)
            return {"text": "", "language": ""}

    def _check_keywords(self, text: str) -> List[str]:
        """Check transcript for flagged keywords."""
        text_lower = text.lower()
        return [kw for kw in self.cfg.flagged_keywords if kw in text_lower]

    def reset(self):
        """Clear audio buffer."""
        with self._lock:
            self._buffer.clear()
            self._continuous_speech_ms = 0
            self._quiet_levels.clear()
            self._consecutive_noisy = 0
            self._diarizer.reset()
            from .diarizer import DiarizationResult
            self._last_diarization = DiarizationResult()
            self._last_diarization_at = 0.0
