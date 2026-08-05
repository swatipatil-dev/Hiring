"""
Communication scoring service.

Measures *delivery* (not content correctness) of a candidate's spoken answer to a
`communication`-type question. Given the uploaded recording (WAV) and the browser
transcript, it computes four delivery metrics and combines them into a 0-100 score:

  - Speech pace        (words-per-minute vs an ideal band)         weight 30%
  - Filler / clarity   (filler-word ratio)                          weight 30%
  - Acoustic confidence(audible & steady volume from the waveform)  weight 25%
  - Pauses / fluency   (speaking-vs-silence ratio via VAD)          weight 15%

Audio is read directly from a 16 kHz mono WAV with `soundfile` (no ffmpeg needed).
VAD reuses the WebRTC approach from `proctoring/core/audio_proctor.py`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Filler words/phrases penalised for clarity. Order matters: multi-word phrases first.
FILLER_PATTERNS = [
    r"\byou know\b", r"\bkind of\b", r"\bsort of\b", r"\bi mean\b",
    r"\bum+\b", r"\buh+\b", r"\ber+\b", r"\bah+\b", r"\bhmm+\b",
    r"\blike\b", r"\bbasically\b", r"\bactually\b", r"\bliterally\b",
]

# Ideal speaking pace band (words per minute).
IDEAL_WPM_LOW = 110
IDEAL_WPM_HIGH = 150

# Weights for the composite score (sum to 1.0).
WEIGHTS = {
    "pace": 0.30,
    "fillers": 0.30,
    "acoustic": 0.25,
    "fluency": 0.15,
}

# VAD config (mirrors proctoring AudioConfig defaults).
VAD_AGGRESSIVENESS = 2
VAD_SAMPLE_RATE = 16000
VAD_FRAME_MS = 30


@dataclass
class CommunicationScore:
    communication_score: float = 0.0          # 0-100 composite

    # Raw measurements
    duration_seconds: float = 0.0
    word_count: int = 0
    words_per_minute: float = 0.0
    filler_count: int = 0
    filler_ratio: float = 0.0
    pause_count: int = 0
    speaking_ratio: float = 0.0
    acoustic_confidence: float = 0.0

    # Sub-scores (0-100)
    pace_score: float = 0.0
    filler_score: float = 0.0
    acoustic_score: float = 0.0
    fluency_score: float = 0.0

    feedback: List[str] = field(default_factory=list)

    def to_breakdown(self) -> Dict:
        return asdict(self)


def _count_words(transcript: str) -> int:
    return len([w for w in re.findall(r"[A-Za-z']+", transcript or "")])


def _count_fillers(transcript: str) -> int:
    text = (transcript or "").lower()
    total = 0
    for pat in FILLER_PATTERNS:
        total += len(re.findall(pat, text))
    return total


def _score_pace(wpm: float) -> float:
    """100 inside the ideal band, tapering to 0 far outside it."""
    if wpm <= 0:
        return 0.0
    if IDEAL_WPM_LOW <= wpm <= IDEAL_WPM_HIGH:
        return 100.0
    if wpm < IDEAL_WPM_LOW:
        # too slow: lose ~1.25 pts per wpm below the band, floor at 0
        return max(0.0, 100.0 - (IDEAL_WPM_LOW - wpm) * 1.25)
    # too fast: lose ~1.25 pts per wpm above the band
    return max(0.0, 100.0 - (wpm - IDEAL_WPM_HIGH) * 1.25)


def _score_fillers(filler_ratio: float) -> float:
    """0% fillers -> 100; ratio scaled so ~12%+ fillers -> 0."""
    return max(0.0, min(100.0, 100.0 - filler_ratio * 800.0))


def _load_wav(audio_path: str) -> tuple[Optional[np.ndarray], int]:
    """Read a WAV file to a mono float32 array in [-1, 1]. Returns (samples, sample_rate)."""
    try:
        import soundfile as sf
        data, sr = sf.read(audio_path, dtype="float32", always_2d=False)
    except Exception as e:
        logger.warning("Could not read audio %s: %s", audio_path, e)
        return None, 0
    if data is None or len(data) == 0:
        return None, sr
    if data.ndim > 1:  # stereo -> mono
        data = data.mean(axis=1)
    return data.astype(np.float32), sr


def _score_acoustic(samples: Optional[np.ndarray]) -> tuple[float, float]:
    """
    Acoustic confidence from waveform: rewards an audible, steady volume.
    Returns (acoustic_confidence_0_1, acoustic_score_0_100).
    """
    if samples is None or len(samples) == 0:
        return 0.0, 0.0

    rms = float(np.sqrt(np.mean(samples ** 2)))
    if rms <= 1e-4:
        return 0.0, 0.0

    # Loudness: RMS ~0.05+ is comfortably audible -> full marks.
    loudness = min(1.0, rms / 0.05)

    # Steadiness: low variation in per-window energy among *speaking* windows.
    win = max(1, len(samples) // 50)
    energies = [
        float(np.sqrt(np.mean(samples[i:i + win] ** 2)))
        for i in range(0, len(samples) - win, win)
    ]
    speaking = [e for e in energies if e > rms * 0.3]
    if len(speaking) >= 2:
        mean_e = float(np.mean(speaking))
        cv = float(np.std(speaking) / mean_e) if mean_e > 0 else 1.0
        steadiness = max(0.0, 1.0 - min(cv, 1.0))
    else:
        steadiness = 0.5

    confidence = 0.6 * loudness + 0.4 * steadiness
    return round(confidence, 3), round(confidence * 100.0, 1)


def _vad_metrics(samples: Optional[np.ndarray], sr: int) -> tuple[float, int, float]:
    """
    Speaking-vs-silence analysis via WebRTC VAD.
    Returns (speaking_ratio_0_1, pause_count, fluency_score_0_100).
    Falls back to an energy-based estimate if webrtcvad is unavailable.
    """
    if samples is None or len(samples) == 0:
        return 0.0, 0, 0.0

    # WebRTC VAD needs 16 kHz 16-bit PCM; resample crudely if needed.
    if sr != VAD_SAMPLE_RATE and sr > 0:
        ratio = VAD_SAMPLE_RATE / sr
        idx = (np.arange(int(len(samples) * ratio)) / ratio).astype(int)
        idx = idx[idx < len(samples)]
        work = samples[idx]
    else:
        work = samples

    pcm16 = np.clip(work * 32768.0, -32768, 32767).astype(np.int16)
    frame_len = int(VAD_SAMPLE_RATE * VAD_FRAME_MS / 1000)

    flags: List[bool] = []
    try:
        import webrtcvad
        vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        for i in range(0, len(pcm16) - frame_len, frame_len):
            frame = pcm16[i:i + frame_len].tobytes()
            try:
                flags.append(vad.is_speech(frame, VAD_SAMPLE_RATE))
            except Exception:
                flags.append(False)
    except ImportError:
        logger.info("webrtcvad unavailable — using energy-based fluency estimate")
        rms = float(np.sqrt(np.mean(work ** 2))) or 1e-6
        for i in range(0, len(work) - frame_len, frame_len):
            seg = work[i:i + frame_len]
            flags.append(float(np.sqrt(np.mean(seg ** 2))) > rms * 0.5)

    if not flags:
        return 0.0, 0, 0.0

    speaking_ratio = sum(flags) / len(flags)

    # Count "long" pauses: runs of >=10 consecutive silent frames (~300 ms).
    pause_count = 0
    run = 0
    for f in flags:
        if not f:
            run += 1
        else:
            if run >= 10:
                pause_count += 1
            run = 0
    if run >= 10:
        pause_count += 1

    # Fluency: continuous speech with few long pauses scores high.
    fluency_score = max(0.0, min(100.0, speaking_ratio * 100.0 - pause_count * 6.0))
    return round(speaking_ratio, 3), pause_count, round(fluency_score, 1)


def _build_feedback(s: CommunicationScore) -> List[str]:
    fb: List[str] = []
    if s.words_per_minute == 0:
        fb.append("No speech detected in the recording.")
        return fb
    if s.words_per_minute < IDEAL_WPM_LOW:
        fb.append(f"Pace was a little slow ({s.words_per_minute:.0f} wpm); aim for {IDEAL_WPM_LOW}-{IDEAL_WPM_HIGH} wpm.")
    elif s.words_per_minute > IDEAL_WPM_HIGH:
        fb.append(f"Pace was fast ({s.words_per_minute:.0f} wpm); slow down toward {IDEAL_WPM_LOW}-{IDEAL_WPM_HIGH} wpm.")
    else:
        fb.append(f"Good speaking pace ({s.words_per_minute:.0f} wpm).")

    if s.filler_count == 0:
        fb.append("No filler words — clear delivery.")
    elif s.filler_ratio > 0.06:
        fb.append(f"Frequent filler words ({s.filler_count}); reduce 'um', 'uh', 'like'.")
    else:
        fb.append(f"A few filler words ({s.filler_count}).")

    if s.acoustic_score >= 70:
        fb.append("Voice was clear and steady.")
    elif s.acoustic_score >= 40:
        fb.append("Voice volume was a bit uneven or quiet.")
    else:
        fb.append("Voice was quiet or inconsistent; speak up closer to the mic.")

    if s.pause_count >= 3:
        fb.append(f"Several long pauses ({s.pause_count}) interrupted the flow.")
    return fb


def score_communication(
    audio_path: Optional[str],
    transcript: str,
    duration_seconds: float,
) -> CommunicationScore:
    """
    Score the delivery of a spoken answer. Robust to missing audio / empty
    transcript — always returns a valid (possibly low) score, never raises.
    """
    s = CommunicationScore()
    s.duration_seconds = max(0.0, float(duration_seconds or 0.0))
    s.word_count = _count_words(transcript)

    # Pace
    minutes = s.duration_seconds / 60.0
    s.words_per_minute = round(s.word_count / minutes, 1) if minutes > 0 else 0.0
    s.pace_score = round(_score_pace(s.words_per_minute), 1)

    # Fillers / clarity
    s.filler_count = _count_fillers(transcript)
    s.filler_ratio = round(s.filler_count / s.word_count, 4) if s.word_count else 0.0
    s.filler_score = round(_score_fillers(s.filler_ratio), 1)

    # Acoustic + VAD (require audio)
    samples, sr = _load_wav(audio_path) if audio_path else (None, 0)
    s.acoustic_confidence, s.acoustic_score = _score_acoustic(samples)
    s.speaking_ratio, s.pause_count, s.fluency_score = _vad_metrics(samples, sr)

    # If there are no words at all, delivery scores collapse to 0.
    if s.word_count == 0:
        s.pace_score = 0.0
        s.filler_score = 0.0

    composite = (
        WEIGHTS["pace"] * s.pace_score
        + WEIGHTS["fillers"] * s.filler_score
        + WEIGHTS["acoustic"] * s.acoustic_score
        + WEIGHTS["fluency"] * s.fluency_score
    )
    s.communication_score = round(composite, 1)
    s.feedback = _build_feedback(s)
    return s
