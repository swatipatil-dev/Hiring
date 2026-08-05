"""
Module 5 — Behavioral AI Engine: Isolation Forest anomaly detection + optional LSTM.
Analyzes typing rhythm, mouse dynamics, focus events, and time-on-question patterns.
"""
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BehavioralFeatures:
    """Feature vector for a 30-second window."""
    typing_speed_wpm: float = 0.0
    typing_rhythm_variance: float = 0.0
    mouse_velocity_mean: float = 0.0
    mouse_velocity_std: float = 0.0
    copy_paste_count: int = 0
    tab_switch_count: int = 0
    time_per_question_deviation: float = 0.0
    idle_time_ratio: float = 0.0

    def to_array(self) -> np.ndarray:
        return np.array([
            self.typing_speed_wpm,
            self.typing_rhythm_variance,
            self.mouse_velocity_mean,
            self.mouse_velocity_std,
            self.copy_paste_count,
            self.tab_switch_count,
            self.time_per_question_deviation,
            self.idle_time_ratio,
        ], dtype=np.float32)


@dataclass
class BehavioralResult:
    anomaly_score: float = 0.0  # -1 (anomaly) to 1 (normal) from IsolationForest
    is_anomaly: bool = False
    cheat_probability: float = 0.0  # from LSTM if available
    top_contributing_features: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    processing_time_ms: float = 0.0


FEATURE_NAMES = [
    "typing_speed_wpm", "typing_rhythm_variance",
    "mouse_velocity_mean", "mouse_velocity_std",
    "copy_paste_count", "tab_switch_count",
    "time_per_question_deviation", "idle_time_ratio",
]


class BehavioralEngine:
    """
    Detects anomalous exam-taking behavior using Isolation Forest (primary)
    and optional LSTM sequence model (upgrade layer).
    """

    def __init__(self, config=None):
        from ..config import BehavioralConfig
        self.cfg = config or BehavioralConfig()

        self._feature_history: List[np.ndarray] = []
        self._iso_forest = None
        self._lstm_model = None
        self._baseline_collected = False
        self._last_train_time = 0.0
        self._raw_signals: Dict[str, deque] = {
            "keystrokes": deque(maxlen=5000),
            "mouse_positions": deque(maxlen=5000),
            "copy_paste": deque(maxlen=500),
            "tab_switches": deque(maxlen=500),
            "question_times": deque(maxlen=200),
        }

        logger.info("BehavioralEngine initialized")

    def ingest_signals(self, payload: dict):
        """
        Receive behavioral signals from frontend via WebSocket.
        Expected payload keys: keystrokes, mouse_positions, copy_paste, tab_switches, question_times
        """
        for key in self._raw_signals:
            if key in payload:
                items = payload[key]
                if isinstance(items, list):
                    self._raw_signals[key].extend(items)
                else:
                    self._raw_signals[key].append(items)

    def extract_features(self) -> BehavioralFeatures:
        """Extract feature vector from current signal window."""
        f = BehavioralFeatures()

        # Typing speed & rhythm
        keystrokes = list(self._raw_signals["keystrokes"])
        if len(keystrokes) >= 2:
            intervals = [keystrokes[i] - keystrokes[i - 1] for i in range(1, len(keystrokes))]
            intervals = [iv for iv in intervals if 0 < iv < 5000]  # filter outliers
            if intervals:
                avg_interval = np.mean(intervals)
                f.typing_speed_wpm = 60000.0 / (avg_interval * 5) if avg_interval > 0 else 0  # ~5 chars per word
                f.typing_rhythm_variance = float(np.var(intervals))

        # Mouse velocity
        positions = list(self._raw_signals["mouse_positions"])
        if len(positions) >= 2:
            velocities = []
            for i in range(1, len(positions)):
                p1, p2 = positions[i - 1], positions[i]
                if isinstance(p1, dict) and isinstance(p2, dict):
                    dx = p2.get("x", 0) - p1.get("x", 0)
                    dy = p2.get("y", 0) - p1.get("y", 0)
                    dt = p2.get("t", 1) - p1.get("t", 0)
                    if dt > 0:
                        velocities.append(np.sqrt(dx ** 2 + dy ** 2) / dt)
            if velocities:
                f.mouse_velocity_mean = float(np.mean(velocities))
                f.mouse_velocity_std = float(np.std(velocities))

        # Counts
        f.copy_paste_count = len(self._raw_signals["copy_paste"])
        f.tab_switch_count = len(self._raw_signals["tab_switches"])

        # Time per question deviation
        qtimes = list(self._raw_signals["question_times"])
        if len(qtimes) >= 2:
            avg_time = np.mean(qtimes)
            if avg_time > 0:
                last_time = qtimes[-1] if isinstance(qtimes[-1], (int, float)) else 0
                f.time_per_question_deviation = abs(last_time - avg_time) / avg_time

        # Idle ratio (from keystroke gaps)
        if len(keystrokes) >= 2:
            total_time = keystrokes[-1] - keystrokes[0]
            if total_time > 0:
                idle = sum(1 for iv in intervals if iv > 3000) * 3000 if 'intervals' in dir() else 0
                f.idle_time_ratio = min(idle / total_time, 1.0) if total_time > 0 else 0.0

        return f

    def analyze(self) -> BehavioralResult:
        """Run anomaly detection on current behavioral features."""
        t0 = time.perf_counter()
        result = BehavioralResult()

        features = self.extract_features()
        feat_array = features.to_array().reshape(1, -1)
        self._feature_history.append(feat_array.flatten())

        # Baseline collection phase
        baseline_frames = int(self.cfg.baseline_minutes * 60 / self.cfg.window_seconds)
        if len(self._feature_history) < baseline_frames:
            result.processing_time_ms = (time.perf_counter() - t0) * 1000
            return result

        # Train/retrain Isolation Forest
        if not self._baseline_collected or (time.time() - self._last_train_time > self.cfg.retrain_interval_minutes * 60):
            self._train_isolation_forest()

        # Predict
        if self._iso_forest is not None:
            score = self._iso_forest.decision_function(feat_array)[0]
            prediction = self._iso_forest.predict(feat_array)[0]
            result.anomaly_score = round(float(score), 4)
            result.is_anomaly = prediction == -1
            if result.is_anomaly:
                result.flags.append("BEHAVIORAL_ANOMALY")
                result.top_contributing_features = self._explain_anomaly(feat_array.flatten())

        # Optional LSTM layer
        if self.cfg.lstm_enabled and len(self._feature_history) >= 20:
            result.cheat_probability = self._run_lstm()
            if result.cheat_probability > 0.7:
                result.flags.append("LSTM_HIGH_CHEAT_PROB")

        result.processing_time_ms = (time.perf_counter() - t0) * 1000
        return result

    def _train_isolation_forest(self):
        """Train IsolationForest on collected feature history."""
        try:
            from sklearn.ensemble import IsolationForest
            X = np.array(self._feature_history)
            self._iso_forest = IsolationForest(
                contamination=self.cfg.contamination,
                random_state=42,
                n_estimators=100,
            )
            self._iso_forest.fit(X)
            self._baseline_collected = True
            self._last_train_time = time.time()
            logger.info("IsolationForest trained on %d samples", len(X))
        except ImportError:
            logger.warning("scikit-learn not installed — anomaly detection disabled")

    def _explain_anomaly(self, features: np.ndarray) -> List[str]:
        """Return top 3 features contributing most to anomaly score."""
        if not self._feature_history:
            return []
        history = np.array(self._feature_history)
        means = history.mean(axis=0)
        stds = history.std(axis=0) + 1e-8
        z_scores = np.abs((features - means) / stds)
        top_indices = np.argsort(z_scores)[-3:][::-1]
        return [FEATURE_NAMES[i] for i in top_indices if i < len(FEATURE_NAMES)]

    def _run_lstm(self) -> float:
        """Run LSTM model on last 20 feature windows."""
        try:
            import torch
            import torch.nn as nn
            from pathlib import Path

            model_path = Path(self.cfg.lstm_model_path)
            if not model_path.exists():
                return 0.0

            if self._lstm_model is None:
                class CheatLSTM(nn.Module):
                    def __init__(self):
                        super().__init__()
                        self.lstm = nn.LSTM(8, 64, batch_first=True)
                        self.fc1 = nn.Linear(64, 32)
                        self.fc2 = nn.Linear(32, 1)
                        self.sigmoid = nn.Sigmoid()

                    def forward(self, x):
                        out, _ = self.lstm(x)
                        out = torch.relu(self.fc1(out[:, -1, :]))
                        return self.sigmoid(self.fc2(out))

                self._lstm_model = CheatLSTM()
                self._lstm_model.load_state_dict(torch.load(str(model_path), map_location="cpu"))
                self._lstm_model.eval()

            window = np.array(self._feature_history[-20:])
            tensor = torch.FloatTensor(window).unsqueeze(0)
            with torch.no_grad():
                prob = self._lstm_model(tensor).item()
            return round(prob, 4)
        except Exception as e:
            logger.debug("LSTM inference skipped: %s", e)
            return 0.0

    def reset(self):
        """Reset all state."""
        self._feature_history.clear()
        for q in self._raw_signals.values():
            q.clear()
        self._baseline_collected = False
        self._iso_forest = None
