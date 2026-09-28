"""MiniLM token-classification inference.

Two things this fixes relative to the bundle's `test__pii.py`:

1.  `test__pii.py` calls the tokenizer with `truncation=True, max_length=256`
    and nothing else, so on a real webpage everything past roughly the first
    256 word-pieces is silently never inspected. The config file already
    declares `chunk_chars` and `chunk_overlap`; this module actually uses them
    and maps each chunk's local offsets back to global offsets in the original
    string.

2.  It uses `tokenizers` directly instead of `transformers`. The tokenizer.json
    in the deployment bundle is self-contained, and dropping `transformers`
    removes roughly 1 GB of dependency from an on-device package that is
    supposed to be lightweight.

If `pii_detector_int8.onnx` is not present, `NERDetector.available` is False and
`detect()` returns an empty list. The firewall keeps working on rules plus
structure; it simply loses contextual detections such as a bare person name.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from .entities import Entity

try:  # onnxruntime is optional at import time so tests can run without it
    import onnxruntime as ort
except Exception:  # pragma: no cover
    ort = None

try:
    from tokenizers import Tokenizer
except Exception:  # pragma: no cover
    Tokenizer = None

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "models"


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    exponentials = np.exp(shifted)
    return exponentials / exponentials.sum(axis=-1, keepdims=True)


class NERDetector:
    def __init__(self, model_dir: str | os.PathLike | None = None, min_score: float = 0.60):
        self.model_dir = Path(model_dir or os.environ.get("PRIVYAGENT_MODEL_DIR") or DEFAULT_MODEL_DIR)
        self.min_score = min_score
        self.available = False
        self.reason = ""
        self.config: dict = {}
        self._session = None
        self._tokenizer = None
        self._id2label: dict[int, str] = {}
        self._load()

    # ---------------------------------------------------------------- loading

    def _load(self) -> None:
        model_path = self.model_dir / "pii_detector_int8.onnx"
        tokenizer_path = self.model_dir / "tokenizer" / "tokenizer.json"
        config_path = self.model_dir / "config.json"
        label_path = self.model_dir / "label_map.json"

        if config_path.exists():
            self.config = json.loads(config_path.read_text(encoding="utf-8"))

        if ort is None:
            self.reason = "onnxruntime is not installed"
            return
        if Tokenizer is None:
            self.reason = "tokenizers is not installed"
            return
        if not model_path.exists():
            self.reason = f"model file not found: {model_path}"
            return
        if not tokenizer_path.exists():
            self.reason = f"tokenizer not found: {tokenizer_path}"
            return
        if not label_path.exists():
            self.reason = f"label_map.json not found: {label_path}"
            return

        label_map = json.loads(label_path.read_text(encoding="utf-8"))
        self._id2label = {int(key): value for key, value in label_map["id2label"].items()}
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_names = {inp.name for inp in self._session.get_inputs()}
        self.available = True
        self.reason = "ready"

    @property
    def max_length(self) -> int:
        return int(self.config.get("max_length", 256))

    @property
    def chunk_chars(self) -> int:
        return int(self.config.get("chunk_chars", 1500))

    @property
    def chunk_overlap(self) -> int:
        return int(self.config.get("chunk_overlap", 200))

    # --------------------------------------------------------------- chunking

    def _chunks(self, text: str) -> list[tuple[int, str]]:
        size = self.chunk_chars
        overlap = min(self.chunk_overlap, size - 1)
        step = size - overlap
        chunks: list[tuple[int, str]] = []
        offset = 0
        while offset < len(text):
            chunks.append((offset, text[offset:offset + size]))
            if offset + size >= len(text):
                break
            offset += step
        return chunks or [(0, "")]

    # -------------------------------------------------------------- inference

    def _run_chunk(self, chunk: str, base_offset: int) -> list[Entity]:
        encoding = self._tokenizer.encode(chunk)
        ids = encoding.ids[: self.max_length]
        offsets = encoding.offsets[: self.max_length]
        attention = [1] * len(ids)

        feeds: dict[str, np.ndarray] = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = np.array([ids], dtype=np.int64)
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = np.array([attention], dtype=np.int64)
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros((1, len(ids)), dtype=np.int64)

        logits = self._session.run(None, feeds)[0][0]
        probabilities = _softmax(logits)
        predictions = probabilities.argmax(axis=-1)

        entities: list[Entity] = []
        current: dict | None = None

        for index, label_id in enumerate(predictions):
            label = self._id2label.get(int(label_id), "O")
            start, end = offsets[index]
            if start == end:  # special tokens carry an empty offset
                continue
            score = float(probabilities[index][label_id])

            if label == "O" or score < self.min_score:
                if current:
                    entities.append(self._finish(current, base_offset))
                    current = None
                continue

            prefix, _, kind = label.partition("-")
            if current and kind == current["label"] and (prefix == "I" or start <= current["end"] + 1):
                current["end"] = end
                current["scores"].append(score)
            else:
                if current:
                    entities.append(self._finish(current, base_offset))
                current = {"start": int(start), "end": int(end), "label": kind, "scores": [score]}

        if current:
            entities.append(self._finish(current, base_offset))
        return entities

    @staticmethod
    def _finish(current: dict, base_offset: int) -> Entity:
        return Entity(
            start=current["start"] + base_offset,
            end=current["end"] + base_offset,
            label=current["label"],
            source="ner",
            score=sum(current["scores"]) / len(current["scores"]),
        )

    def detect(self, text: str) -> list[Entity]:
        if not self.available or not text:
            return []
        entities: list[Entity] = []
        for base_offset, chunk in self._chunks(text):
            if not chunk.strip():
                continue
            entities.extend(self._run_chunk(chunk, base_offset))
        return entities
