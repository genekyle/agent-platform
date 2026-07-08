"""Local asset store — the single seam for listing photos, stubbed for eventual cloud (S3) storage.

Item photos are stored as asset KEYS (e.g. "marketplace/sample-hoodie.jpg"), NOT URLs, because FB's
create-listing form uploads via a file input and the driver needs a real LOCAL FILE PATH at post
time. Today keys resolve to files under the local `assets/` folder; moving to S3 later means
reimplementing ONLY the three functions here (list_assets / abs_path / public_url) — callers keep
using keys unchanged. See assets/README.md.
"""

from __future__ import annotations

from pathlib import Path

from settings import settings

# Repo-root /assets by default (robust to cwd); override with ASSETS_DIR in .env.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "assets"
ASSETS_ROOT = (Path(settings.assets_dir).resolve() if getattr(settings, "assets_dir", "")
               else _DEFAULT_ROOT)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _key(path: Path) -> str:
    """The asset key = path relative to the root, forward-slashed (an S3-style key)."""
    return path.relative_to(ASSETS_ROOT).as_posix()


def list_assets(prefix: str = "") -> list[dict]:
    """Enumerate image assets under `prefix` (a subfolder like "marketplace"), newest first.
    Each: {key, name, size, url}. The UI picker reads this. Later: an S3 ListObjects call."""
    base = (ASSETS_ROOT / prefix) if prefix else ASSETS_ROOT
    if not base.exists():
        return []
    out = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            st = p.stat()
            out.append({"key": _key(p), "name": p.name, "size": st.st_size,
                        "mtime": st.st_mtime, "url": public_url(_key(p))})
    out.sort(key=lambda a: a["mtime"], reverse=True)
    for a in out:
        a.pop("mtime", None)
    return out


def abs_path(key: str) -> str | None:
    """Local filesystem path for an asset key, for upload — or None if it escapes the root or is
    missing. Later: download the S3 object to a temp file and return that path."""
    if not key:
        return None
    p = (ASSETS_ROOT / key).resolve()
    if ASSETS_ROOT not in p.parents or not p.is_file():
        return None
    return str(p)


def public_url(key: str) -> str:
    """URL the UI can render a thumbnail from — served by the API's /assets mount. Later: an S3/CDN
    URL (presigned if the bucket is private)."""
    return f"/assets/{key}"
