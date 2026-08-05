"""
Proctoring System Configuration — all thresholds and settings in one place.
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class FaceConfig:
    detection_confidence: float = 0.4  # lowered from 0.6 — improves second face detection at distance
    face_match_tolerance: float = 0.363  # SFace recommended cosine threshold — rejects different faces reliably
    gaze_left_threshold: float = 0.4
    gaze_right_threshold: float = 0.6
    gaze_deviation_threshold: float = 0.25  # tighter — flag gaze away from screen earlier
    head_yaw_threshold: float = 20.0  # catch turns at ~20° — visible side turn
    head_pitch_threshold: float = -25.0  # reduced from -30 — catch looking down earlier
    mar_threshold: float = 0.5  # Mouth Aspect Ratio for lip movement
    skip_frames: int = 2  # face matching runs every 2nd frame — more frequent identity checks


@dataclass
class ObjectConfig:
    model_cpu: str = "yolov8n.pt"
    model_gpu: str = "yolov8m.pt"
    inference_size: int = 416  # bumped from 320 for better small-object detection
    skip_frames: int = 3  # run YOLO every Nth frame, reuse last result
    conf_threshold: float = 0.35  # lowered global pre-filter to catch phones earlier
    iou_threshold: float = 0.45  # NMS IoU threshold — lower = fewer overlapping boxes
    min_bbox_area: int = 400  # ignore tiny false-positive detections (< 20x20 px)
    targets: Dict[str, dict] = field(default_factory=lambda: {
        "cell phone":  {"confidence": 0.20, "severity": "HIGH",     "coco_id": 67},
        "book":        {"confidence": 0.40, "severity": "MEDIUM",   "coco_id": 73},
        "person":      {"confidence": 0.50, "severity": "CRITICAL", "coco_id": 0},
        "remote":      {"confidence": 0.35, "severity": "MEDIUM",   "coco_id": 65},
        "tablet":      {"confidence": 0.35, "severity": "HIGH",     "coco_id": 77},
    })
    evidence_dir: str = "evidence_frames"
    evidence_cooldown_sec: float = 10.0  # throttle evidence saves to 1 per 10s per session
    track_confirm_frames: int = 2  # consecutive detections needed to confirm (reduces false positives)
    score_decay_rate: float = 0.85  # per-frame decay for detection smoothing


@dataclass
class AudioConfig:
    vad_aggressiveness: int = 3
    sample_rate: int = 16000
    frame_duration_ms: int = 30
    speech_trigger_seconds: float = 3.0
    whisper_model: str = "base"
    flagged_keywords: List[str] = field(default_factory=lambda: [
        "hey", "what is", "tell me", "answer", "google", "search"
    ])
    buffer_max_samples: int = 160000  # 10 seconds at 16kHz

    # ── Level measurement ──
    # dBFS (decibels relative to digital full scale), NOT acoustic dB SPL: an
    # uncalibrated browser mic at an unknown distance cannot give a physical sound
    # pressure level. Everything below is relative to the capture chain, which is all
    # that's needed to compare a candidate against their own room over time.
    silence_dbfs: float = -60.0       # below this the mic is muted/dead (replaces `rms < 10`)
    noise_floor_dbfs: float = -35.0   # background level (while nobody speaks) worth flagging
    noise_confirm_windows: int = 4    # consecutive noisy windows before BACKGROUND_NOISE
    level_history_windows: int = 20   # rolling window count for the noise-floor average

    # ── Speaker diarization ──
    diarization_enabled: bool = True
    # analyze() is called once per VIDEO frame, so diarization must not run every time —
    # embedding the buffer at 10fps would cost more CPU than YOLO and MediaPipe combined.
    # Between runs the previous verdict is reused.
    diarization_interval_sec: float = 2.0
    diarization_window_sec: float = 4.0    # only the newest audio is embedded, not the whole ring buffer
    min_speech_segment_sec: float = 0.75   # shorter spans embed too unreliably to cluster
    # Cosine distance beyond which a segment is "not the candidate". Per engine, because
    # the two feature spaces are not remotely comparable: neural embeddings put different
    # speakers around 0.6-1.0, while the MFCC+F0 summary compresses everything into
    # roughly 0.0-0.4 (measured: same speaker ~0.0 even 14dB quieter, different ~0.3).
    # One shared threshold meant the DSP fallback could never flag anyone.
    speaker_match_threshold: float = 0.75      # resemblyzer
    speaker_match_threshold_dsp: float = 0.18  # MFCC+F0 fallback — calibrate on real audio
    enrollment_seconds: float = 5.0        # speech collected before the candidate's voice is anchored
    multi_speaker_confirm_windows: int = 2 # consecutive windows with a foreign voice before flagging


@dataclass
class PoseConfig:
    phone_gesture_y_threshold: float = 0.35
    lean_angle_threshold: float = 30.0
    absence_visibility_threshold: float = 0.5
    absence_duration_seconds: float = 2.0
    baseline_capture_seconds: float = 30.0
    deviation_std_multiplier: float = 2.0


@dataclass
class BehavioralConfig:
    window_seconds: int = 30
    contamination: float = 0.1
    baseline_minutes: int = 5
    retrain_interval_minutes: int = 10
    lstm_enabled: bool = False
    lstm_model_path: str = "models/behavioral_lstm.pt"


@dataclass
class ProctorConfig:
    face: FaceConfig = field(default_factory=FaceConfig)
    objects: ObjectConfig = field(default_factory=ObjectConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    pose: PoseConfig = field(default_factory=PoseConfig)
    behavioral: BehavioralConfig = field(default_factory=BehavioralConfig)
    target_fps: int = 15
    evidence_dir: str = "evidence_frames"

    # Decision thresholds (cheating score ranges)
    SCORE_NORMAL_MAX: float = 30.0
    SCORE_WARNING_MAX: float = 60.0
    SCORE_SUSPICIOUS_MAX: float = 80.0
    # Above 80 = CHEATING

    # Per-trigger score table (flag -> score impact)
    TRIGGER_SCORES: Dict[str, float] = field(default_factory=lambda: {
        "NO_FACE": 40, "MULTIPLE_FACES": 30, "IDENTITY_MISMATCH": 85,
        "HEAD_TURNED": 15, "SUSPICIOUS_GAZE": 10,
        "PHONE_DETECTED": 50, "PHONE_GESTURE": 35,
        "MULTIPLE_VOICES": 40, "FLAGGED_SPEECH": 20, "MIC_MUTED": 15,
        "CANDIDATE_LEFT": 50, "LEANING_FORWARD": 15, "HEAD_DOWN": 10,
        "BEHAVIORAL_ANOMALY": 30, "LSTM_HIGH_CHEAT_PROB": 40,
        "TAB_SWITCH": 25,
    })

    # Suspicious objects with COCO class names and score weights
    OBJECT_SCORE_WEIGHTS: Dict[str, int] = field(default_factory=lambda: {
        "cell phone": 50, "book": 30, "person": 40, "laptop": 40,
        "headphones": 25, "remote": 20, "tv": 35, "tablet": 45,
    })

    # DB persistence settings
    db_persist_every_n_frames: int = 15  # write cheating log every 15th frame (was 5 — reduced DB load)
    persist_violations_immediately: bool = True  # HIGH/CRITICAL saved instantly

    # Auto-termination policy (per-flag 3 warnings → 4th strike ends the test)
    TERMINATE_STRIKES: int = 3                    # warnings allowed per serious flag; terminate when strikes > this
    NO_FACE_STRIKE_SEC: float = 105.0             # sustained no-face that counts as one NO_FACE strike
    IDENTITY_MISMATCH_STRIKE_SEC: float = 300.0   # sustained mismatch (5 min) that counts as one strike
    TERMINATION_GRACE_SEC: float = 10.0           # ignore strikes during camera warm-up / live enrollment

    def score_to_decision(self, score: float) -> str:
        """Map a cheating score to a decision label."""
        if score <= self.SCORE_NORMAL_MAX:
            return "NORMAL"
        if score <= self.SCORE_WARNING_MAX:
            return "WARNING"
        if score <= self.SCORE_SUSPICIOUS_MAX:
            return "SUSPICIOUS"
        return "CHEATING"
