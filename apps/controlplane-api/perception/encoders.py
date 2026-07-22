"""Frozen image encoders — witness B's eyes (PLAN_perception_v1 §3.2).

An encoder turns a screenshot into a vector. That is ALL it does: nothing here is trained, and
nothing here will be. The plan's rung 3 is "never a fine-tuned vision model on this machine" —
Gemma 4 E2B needed 7.2 GB resident and 50 s to emit one word on this 8 GB M3, which is the whole
reason the local stack is perception and not reasoning.

Three encoders, cheapest first, all behind one interface so the bench can rank them and the
observer can swap without knowing which won:

  pixel32   32x32 grayscale, PIL only. Free, instant, no download. The BASELINE that a neural
            encoder has to beat — UI screenshots are mostly layout, and layout survives
            downscaling. If this wins, we ship it and save 600 MB.
  apple     Apple Vision `VNGenerateImageFeaturePrint`. 768-dim, ~0.18 s/shot, no download, no
            API cost, macOS-native, and `pyobjc-framework-Vision` is already a dependency.
            Caveat measured 2026-07-22: trained on natural photographs, so its same-vs-different
            band on UI is narrow (0.897 vs 0.811 median cosine).
  clip      `openai/clip-vit-base-patch32` via transformers. ~600 MB one-time download — WIFI
            ONLY (docs/LOW_DATA_MODE.md), which is why it is lazily imported and never the
            default.

Embeddings are cached on disk keyed by (encoder, file identity), because re-benching should cost
nothing and the observer will ask for the same screenshot more than once.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Optional, Protocol

#: Where cached vectors live. Sits beside the other derived artifacts rather than in a new tree.
_CACHE_ENV = "PERCEPTION_CACHE_DIR"


def _cache_root() -> Path:
    if os.environ.get(_CACHE_ENV):
        return Path(os.environ[_CACHE_ENV])
    try:
        from settings import settings
        base = Path(__file__).resolve().parent.parent / settings.observer_artifacts_dir
    except Exception:
        base = Path(__file__).resolve().parent.parent.parent / "mcp" / "output"
    return (base / "derived" / "embeddings").resolve()


def _file_key(path: Path) -> str:
    """Identity of the file's CONTENT-ish: name + size + mtime. Cheap, and a screenshot never
    changes in place — a new capture is a new filename."""
    st = path.stat()
    return hashlib.sha1(f"{path.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:20]


class Encoder(Protocol):
    name: str

    def embed(self, path: Path) -> Optional[list[float]]:
        """Vector for one image, or None if this encoder cannot read it."""


class _Cached:
    """Mixin: one JSON file per encoder holding {file_key: vector}. Loaded once, appended to."""

    name = "base"

    def __init__(self) -> None:
        self._cache: Optional[dict[str, list[float]]] = None
        self._dirty = False

    @property
    def _cache_path(self) -> Path:
        return _cache_root() / f"{self.name}.json"

    def _load(self) -> dict[str, list[float]]:
        if self._cache is None:
            try:
                self._cache = json.loads(self._cache_path.read_text())
            except Exception:
                self._cache = {}
        return self._cache

    def flush(self) -> None:
        if not self._dirty or self._cache is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache))
        self._dirty = False

    def embed(self, path: Path) -> Optional[list[float]]:
        path = Path(path)
        if not path.exists():
            return None
        cache = self._load()
        key = _file_key(path)
        hit = cache.get(key)
        if hit is not None:
            return hit
        vec = self._compute(path)
        if vec is None:
            return None
        cache[key] = vec
        self._dirty = True
        return vec

    def _compute(self, path: Path) -> Optional[list[float]]:  # pragma: no cover - overridden
        raise NotImplementedError


class PixelEncoder(_Cached):
    """32x32 grayscale, mean-centred. The baseline a neural encoder must beat."""

    name = "pixel32"

    def __init__(self, side: int = 32) -> None:
        super().__init__()
        self.side = side
        self.name = f"pixel{side}"

    def _compute(self, path: Path) -> Optional[list[float]]:
        try:
            from PIL import Image
        except Exception:
            return None
        with Image.open(path) as im:
            small = im.convert("L").resize((self.side, self.side), Image.BILINEAR)
            vals = [p / 255.0 for p in small.tobytes()]
        mean = sum(vals) / len(vals)
        return [v - mean for v in vals]


class AppleVisionEncoder(_Cached):
    """`VNGenerateImageFeaturePrint` — 768-dim, native, free, no download. macOS only."""

    name = "apple_featureprint"

    def _compute(self, path: Path) -> Optional[list[float]]:
        try:
            import Vision
            from Foundation import NSURL
        except Exception:
            return None
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
            NSURL.fileURLWithPath_(str(path)), None)
        request = Vision.VNGenerateImageFeaturePrintRequest.alloc().init()
        ok, _err = handler.performRequests_error_([request], None)
        if not ok or not request.results():
            return None
        raw = bytes(request.results()[0].data())
        return list(struct.unpack(f"<{len(raw) // 4}f", raw))


class ClipEncoder(_Cached):
    """CLIP ViT-B/32 image tower. ~600 MB one-time download — WIFI ONLY."""

    name = "clip_vit_b32"

    def __init__(self, model_id: str = "openai/clip-vit-base-patch32") -> None:
        super().__init__()
        self.model_id = model_id
        self._model = None
        self._processor = None

    def _ensure(self) -> bool:
        if self._model is not None:
            return True
        try:
            import torch  # noqa: F401
            from transformers import CLIPModel, CLIPProcessor
        except Exception:
            return False
        self._model = CLIPModel.from_pretrained(self.model_id).eval()
        self._processor = CLIPProcessor.from_pretrained(self.model_id)
        return True

    def _compute(self, path: Path) -> Optional[list[float]]:
        if not self._ensure():
            return None
        import torch
        from PIL import Image
        with Image.open(path) as im:
            inputs = self._processor(images=im.convert("RGB"), return_tensors="pt")
        with torch.no_grad():
            feats = self._model.get_image_features(**inputs)
        return feats[0].tolist()


_REGISTRY: dict[str, type] = {
    "pixel32": PixelEncoder,
    "apple": AppleVisionEncoder,
    "apple_featureprint": AppleVisionEncoder,
    "clip": ClipEncoder,
    "clip_vit_b32": ClipEncoder,
}


def get_encoder(name: str) -> Encoder:
    """Factory. Unknown name is a hard error, not a silent fallback (PRINCIPLES: no silent
    fallbacks — a bench that quietly scored the wrong encoder is worse than one that crashed)."""
    key = (name or "").strip().lower()
    if key not in _REGISTRY:
        raise ValueError(f"unknown encoder {name!r}; known: {sorted(set(_REGISTRY))}")
    return _REGISTRY[key]()


def available_encoders() -> list[str]:
    return ["pixel32", "apple", "clip"]
