"""
Speaker diarization — is the candidate alone, or is someone else speaking?

Replaces the energy-variance guess that preceded it, which split the buffer into 250ms
chunks and called any coefficient of variation above 0.5 "two speakers". One person's
normal speech dynamics clear that constantly, so the signal was noise.

The approach here is speaker *identity*, not loudness: embed each speech segment, anchor
the candidate's own voice from the first few seconds (the same trick FaceAnalyzer uses to
anchor a face), and treat a segment far from that anchor as somebody else.

Two engines, chosen at construction:

  resemblyzer  256-d neural speaker embeddings. Rides the torch already installed.
  dsp          MFCC + pitch statistics via scipy. No extra dependency; separates clearly
               different voices (e.g. a man and a woman) but struggles with similar ones.

The fallback is deliberate and matches the rest of the pipeline — MediaPipe -> Haar in
face_analyzer, SFace -> histogram in face matching. `engine` is reported through
pipeline.get_summary() so a missing dependency is visible rather than a silent downgrade.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_MAX_EMBED_SAMPLES = 2 * _SAMPLE_RATE  # embed at most 2s of any one segment


@dataclass
class DiarizationResult:
    voice_count: int = 0            # distinct speakers heard in this window
    foreign_speech: bool = False    # someone other than the enrolled candidate spoke
    confidence: float = 0.0         # 0-1, how far the foreign segment sat from the candidate
    enrolled: bool = False          # the candidate's voice anchor exists yet
    segment_count: int = 0          # speech segments this window (0 = nothing to judge)
    distances: List[float] = field(default_factory=list)  # per-segment cosine distance, for the report


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / (na * nb))


class SpeakerDiarizer:
    """Tracks one enrolled voice and reports when a different one appears."""

    def __init__(self, config=None):
        from ..config import AudioConfig
        self.cfg = config or AudioConfig()

        self._encoder = None
        self.engine = "dsp"
        self._enrollment_pool: List[np.ndarray] = []
        self._enrolled_seconds = 0.0
        self._centroid: Optional[np.ndarray] = None
        self._consecutive_foreign = 0

        self._init_encoder()

    @property
    def _threshold(self) -> float:
        """Match threshold for the engine actually in use — the two scales differ hugely."""
        return (self.cfg.speaker_match_threshold if self.engine == "resemblyzer"
                else self.cfg.speaker_match_threshold_dsp)

    def _init_encoder(self):
        try:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder(device="cpu", verbose=False)
            self.engine = "resemblyzer"
            logger.info("Speaker diarization: resemblyzer encoder ready")
        except Exception as e:
            # ImportError when the package is absent; anything else means the model failed
            # to load. Either way the DSP path still gives a usable answer.
            logger.warning("Speaker diarization falling back to DSP features: %s", e)

    # ── Embeddings ────────────────────────────────────────────────────────────

    def _embed(self, segment: np.ndarray) -> Optional[np.ndarray]:
        """Embed one int16 speech segment. Returns None if it can't be embedded."""
        if segment.size < int(self.cfg.min_speech_segment_sec * _SAMPLE_RATE):
            return None
        float_seg = segment.astype(np.float32) / 32768.0

        if self._encoder is not None:
            try:
                return np.asarray(self._encoder.embed_utterance(float_seg), dtype=np.float32)
            except Exception as e:
                logger.warning("resemblyzer embed failed, using DSP for this segment: %s", e)

        return self._dsp_embed(float_seg)

    def _dsp_embed(self, float_seg: np.ndarray) -> Optional[np.ndarray]:
        """
        Mel-cepstral + pitch summary of a segment, using scipy only.

        Mean and standard deviation of 13 MFCC-like coefficients plus the fundamental
        frequency. Crude next to a trained encoder, but the two things that most separate
        two people talking in one room — vocal tract shape (cepstrum) and pitch — are
        exactly what this captures.
        """
        try:
            from scipy.fftpack import dct
            from scipy.signal import get_window
        except ImportError:
            return None

        frame_len, hop = 400, 160          # 25ms frame, 10ms hop at 16kHz

        # Cap the analysed span. Timbre and pitch are stationary over a couple of
        # seconds, so embedding more audio buys nothing and costs linearly — and this
        # runs inside the per-frame proctoring loop.
        float_seg = float_seg[:_MAX_EMBED_SAMPLES]
        if float_seg.size < frame_len * 4:
            return None

        window = get_window("hann", frame_len, fftbins=True)
        # Strided view instead of a Python loop building a list of slices: same frames,
        # no per-frame allocation.
        frames = np.lib.stride_tricks.sliding_window_view(float_seg, frame_len)[::hop] * window
        spectra = np.abs(np.fft.rfft(frames, n=512, axis=1)) ** 2

        mel_fb = self._mel_filterbank(n_filters=26, n_fft=512, sample_rate=_SAMPLE_RATE)
        mel_energy = np.maximum(spectra @ mel_fb.T, 1e-10)
        mfcc = dct(np.log(mel_energy), type=2, axis=1, norm="ortho")[:, 1:14]

        feat = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0), [self._estimate_f0(float_seg)]])
        return feat.astype(np.float32)

    @staticmethod
    def _mel_filterbank(n_filters: int, n_fft: int, sample_rate: int) -> np.ndarray:
        def hz_to_mel(f):
            return 2595.0 * np.log10(1.0 + f / 700.0)

        def mel_to_hz(m):
            return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

        low, high = hz_to_mel(60.0), hz_to_mel(sample_rate / 2)
        points = mel_to_hz(np.linspace(low, high, n_filters + 2))
        bins = np.floor((n_fft + 1) * points / sample_rate).astype(int)

        fb = np.zeros((n_filters, n_fft // 2 + 1))
        for i in range(n_filters):
            left, centre, right = bins[i], bins[i + 1], bins[i + 2]
            if centre == left or right == centre:
                continue
            fb[i, left:centre] = (np.arange(left, centre) - left) / (centre - left)
            fb[i, centre:right] = (right - np.arange(centre, right)) / (right - centre)
        return fb

    @staticmethod
    def _estimate_f0(float_seg: np.ndarray) -> float:
        """
        Fundamental frequency by autocorrelation, normalised to roughly 0-1.

        Via FFT, and over at most 0.5s. `np.correlate(x, x, "full")` is O(n^2): on a 10s
        buffer that is ~25 billion operations, which took this call from milliseconds to
        minutes and stalled the whole proctoring loop, since analyze() runs per video
        frame. Pitch is stationary enough that half a second is plenty to measure it.
        """
        seg = float_seg[:_SAMPLE_RATE // 2]
        seg = seg - seg.mean()
        if seg.size < 512:
            return 0.0
        n = 1 << int(np.ceil(np.log2(seg.size * 2)))
        spec = np.fft.rfft(seg, n)
        corr = np.fft.irfft(spec * np.conjugate(spec), n)[:seg.size]

        # 80Hz-350Hz covers adult speech; outside that range it isn't voiced pitch.
        lo, hi = int(_SAMPLE_RATE / 350), int(_SAMPLE_RATE / 80)
        if corr.size <= hi or corr[0] <= 0:
            return 0.0
        peak = int(np.argmax(corr[lo:hi])) + lo
        return float((_SAMPLE_RATE / peak) / 350.0) if peak else 0.0

    # ── Enrollment + comparison ───────────────────────────────────────────────

    def _enroll(self, embedding: np.ndarray, seconds: float):
        self._enrollment_pool.append(embedding)
        self._enrolled_seconds += seconds
        if self._enrolled_seconds < self.cfg.enrollment_seconds:
            return

        # Anchor on the LARGEST consistent group, not the mean of everything heard.
        # Averaging blindly would let anyone talking during the first few seconds — an
        # invigilator, someone in the hallway — pull the candidate's anchor towards
        # themselves, after which the real candidate reads as the intruder.
        groups: List[List[np.ndarray]] = []
        for emb in self._enrollment_pool:
            for g in groups:
                if _cosine_distance(emb, g[0]) <= self._threshold:
                    g.append(emb)
                    break
            else:
                groups.append([emb])

        largest = max(groups, key=len)
        self._centroid = np.mean(np.stack(largest), axis=0)
        logger.info("Speaker diarization: candidate voice anchored from %.1fs of speech "
                    "(%d/%d segments agreed, %d distinct voice(s) heard while enrolling)",
                    self._enrolled_seconds, len(largest), len(self._enrollment_pool), len(groups))

    def analyze(self, segments: List[np.ndarray]) -> DiarizationResult:
        """
        Judge a window of VAD-gated speech segments (int16, 16kHz).

        Until the candidate's voice is anchored, everything heard is assumed to be theirs
        — flagging before enrollment would flag the candidate for existing.
        """
        result = DiarizationResult(enrolled=self._centroid is not None,
                                   segment_count=len(segments))
        if not self.cfg.diarization_enabled or not segments:
            self._consecutive_foreign = 0
            return result

        embeddings = []
        for seg in segments:
            emb = self._embed(seg)
            if emb is not None:
                embeddings.append((emb, seg.size / _SAMPLE_RATE))

        if not embeddings:
            self._consecutive_foreign = 0
            return result

        if self._centroid is None:
            for emb, secs in embeddings:
                self._enroll(emb, secs)
            result.enrolled = self._centroid is not None
            result.voice_count = 1
            return result

        result.distances = [round(_cosine_distance(emb, self._centroid), 3)
                            for emb, _ in embeddings]
        foreign = [d for d in result.distances if d > self._threshold]

        # Distinct voices *heard in this window*: the candidate counts only if one of
        # their segments actually appears here, so a window where only an intruder talks
        # reads as 1 voice, not 2. The MULTIPLE_VOICES decision comes from foreign_speech
        # below, never from this count.
        candidate_present = len(foreign) < len(result.distances)
        foreign_embeddings = [emb for (emb, _), d in zip(embeddings, result.distances)
                              if d > self._threshold]
        result.voice_count = int(candidate_present) + self._count_clusters(foreign_embeddings)

        if foreign:
            self._consecutive_foreign += 1
            # Confirm across windows: a clipped word or a cough can embed far from anyone.
            if self._consecutive_foreign >= self.cfg.multi_speaker_confirm_windows:
                result.foreign_speech = True
                worst = max(foreign)
                result.confidence = float(min((worst - self._threshold) /
                                              max(1e-6, 1.0 - self._threshold), 1.0))
        else:
            self._consecutive_foreign = 0
            # Keep the anchor current — voices drift as people relax, lean back, warm up.
            self._centroid = 0.95 * self._centroid + 0.05 * np.mean(
                np.stack([e for e, _ in embeddings]), axis=0)

        return result

    def _count_clusters(self, embeddings: List[np.ndarray]) -> int:
        """Agglomerate foreign segments so two other people don't count as one."""
        if not embeddings:
            return 0
        clusters: List[np.ndarray] = [embeddings[0]]
        for emb in embeddings[1:]:
            if all(_cosine_distance(emb, c) > self._threshold for c in clusters):
                clusters.append(emb)
        return len(clusters)

    def reset(self):
        self._enrollment_pool.clear()
        self._enrolled_seconds = 0.0
        self._centroid = None
        self._consecutive_foreign = 0
