"""Local asset store — the single seam for listing photos, stubbed for eventual cloud (S3) storage.

Item photos are stored as asset KEYS (e.g. "marketplace/sample-hoodie.jpg"), NOT URLs, because FB's
create-listing form uploads via a file input and the driver needs a real LOCAL FILE PATH at post
time. Today keys resolve to files under the local `assets/` folder; moving to S3 later means
reimplementing ONLY the three functions here (list_assets / abs_path / public_url) — callers keep
using keys unchanged. See assets/README.md.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from settings import settings

# Repo-root /assets by default (robust to cwd); override with ASSETS_DIR in .env.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "assets"
ASSETS_ROOT = (Path(settings.assets_dir).resolve() if getattr(settings, "assets_dir", "")
               else _DEFAULT_ROOT)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# Document assets (resumes, cover letters). Same key/abs_path/public_url machinery as images — the
# apply flow uploads these into cross-site ATS file inputs (Workday's autofillWithResume, etc.), so
# they are DOMAIN-AGNOSTIC on purpose: one resume serves Indeed, Workday and any company site.
DOC_EXTS = {".pdf", ".doc", ".docx", ".rtf", ".txt"}

# The canonical resume the apply flow grabs when a site needs a file upload. Overridable via env so a
# different resume can be swapped without code changes; the default is the file we ship under assets/.
RESUME_ASSET_KEY = os.environ.get("RESUME_ASSET_KEY", "documents/GM_Resume.pdf")

# Item-OWNED photos live under this subtree, one folder per inventory item
# (marketplace/items/<item_id>/<file>). Ownership is encoded in the key path, so an uploaded photo
# belongs to exactly one item and never leaks into another item's shared-library picker. Each file
# gets a "<file>.meta.json" sidecar of makeshift metadata (owner item, original name, uploaded_at).
ITEM_PREFIX = "marketplace/items"

# Map an image content-type to a canonical extension when the uploaded filename lacks a usable one.
_CT_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
           "image/webp": ".webp", "image/gif": ".gif"}


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
            key = _key(p)
            # Skip item-OWNED photos — they belong to one item, not the shared library the picker
            # shows, so they must not leak into other items' choices.
            if key == ITEM_PREFIX or key.startswith(ITEM_PREFIX + "/"):
                continue
            st = p.stat()
            out.append({"key": key, "name": p.name, "size": st.st_size,
                        "mtime": st.st_mtime, "url": public_url(key)})
    out.sort(key=lambda a: a["mtime"], reverse=True)
    for a in out:
        a.pop("mtime", None)
    return out


def list_documents(prefix: str = "documents") -> list[dict]:
    """Enumerate document assets (resumes, cover letters) under `prefix`, newest first. Mirrors
    list_assets but for DOC_EXTS. Each is flagged `is_resume` when it's the canonical resume."""
    base = (ASSETS_ROOT / prefix) if prefix else ASSETS_ROOT
    if not base.exists():
        return []
    out = []
    for p in base.rglob("*"):
        if p.is_file() and p.suffix.lower() in DOC_EXTS:
            st = p.stat()
            key = _key(p)
            out.append({"key": key, "name": p.name, "size": st.st_size,
                        "mtime": st.st_mtime, "url": public_url(key),
                        "is_resume": key == RESUME_ASSET_KEY})
    out.sort(key=lambda a: a["mtime"], reverse=True)
    for a in out:
        a.pop("mtime", None)
    return out


def resume_key() -> str:
    """Canonical resume asset key. The apply flow / autofill uploader calls this so it never has to
    hard-code a path — one pointer, reused across Indeed, Workday and any company ATS."""
    return RESUME_ASSET_KEY


def resume_path() -> str | None:
    """Local filesystem path to the canonical resume (for a file-input upload), or None if missing."""
    return abs_path(RESUME_ASSET_KEY)


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


# --- Item-owned uploads (per-post, not shared) -------------------------------
def _slug(text: str) -> str:
    """Filesystem-safe slug (keeps alnum . _ -) — used for the item-id folder + filename stems."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip()).strip("-.") or "item"


def _safe_name(filename: str, content_type: str = "") -> str:
    """A safe on-disk name preserving an IMAGE extension: sanitize the stem, and force a valid image
    ext (from the filename, else the content-type, else .jpg) so the key resolves as an image."""
    base = os.path.basename(filename or "").strip()
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in IMAGE_EXTS:
        ext = _CT_EXT.get((content_type or "").lower(), ".jpg")
    return f"{_slug(stem) or 'photo'}{ext}"


def _dedupe(path: Path) -> Path:
    """If `path` exists, append -1, -2, … before the extension so an upload never clobbers a sibling."""
    if not path.exists():
        return path
    stem, ext = path.stem, path.suffix
    for n in range(1, 1000):
        cand = path.with_name(f"{stem}-{n}{ext}")
        if not cand.exists():
            return cand
    return path.with_name(f"{stem}-{os.getpid()}{ext}")


def save_item_photo(item_id: str, filename: str, data: bytes, content_type: str = "") -> dict:
    """Persist an uploaded photo as an asset OWNED by `item_id` (under marketplace/items/<id>/) and
    write a makeshift metadata sidecar. Returns the asset descriptor {key, name, size, url, ...}.
    The caller assigns the returned `key` to the item's photos. Later: a PUT to S3 + a DynamoDB item."""
    owner = _slug(item_id)
    dest_dir = ASSETS_ROOT / ITEM_PREFIX / owner
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _dedupe(dest_dir / _safe_name(filename, content_type))
    target.write_bytes(data)
    key = _key(target)
    meta = {"item_id": owner, "original_name": os.path.basename(filename or "") or target.name,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "size": len(data), "content_type": (content_type or "")}
    try:
        target.with_name(target.name + ".meta.json").write_text(json.dumps(meta, indent=2))
    except OSError:
        pass  # metadata is best-effort; the file + path-encoded ownership are the source of truth
    return {"key": key, "name": target.name, "size": len(data), "url": public_url(key), **meta}


def asset_meta(key: str) -> dict | None:
    """Metadata for an asset key: the sidecar if present, else owner derived from the key path
    (marketplace/items/<item_id>/…). None if the key doesn't resolve."""
    p = abs_path(key)
    if not p:
        return None
    sidecar = Path(p + ".meta.json")
    if sidecar.exists():
        try:
            return json.loads(sidecar.read_text())
        except (OSError, ValueError):
            pass
    owner = None
    parts = key.split("/")
    if (key.startswith(ITEM_PREFIX + "/")) and len(parts) >= 4:
        owner = parts[2]
    return {"item_id": owner, "original_name": os.path.basename(key)}


def delete_asset(key: str) -> bool:
    """Delete an asset file (and its metadata sidecar) — used when a photo is unassigned from its
    owning item. Guards against escaping the root via abs_path. Returns True if the file was removed."""
    p = abs_path(key)
    if not p:
        return False
    try:
        os.remove(p)
    except OSError:
        return False
    sidecar = p + ".meta.json"
    if os.path.exists(sidecar):
        try:
            os.remove(sidecar)
        except OSError:
            pass
    return True


def delete_item_assets(item_id: str) -> int:
    """Remove an item's entire OWNED photo folder (marketplace/items/<id>/) — called when the item
    is hard-deleted so its photos don't orphan. Returns how many files were removed. Best-effort."""
    import shutil
    folder = (ASSETS_ROOT / ITEM_PREFIX / _slug(item_id)).resolve()
    if ASSETS_ROOT not in folder.parents or not folder.is_dir():
        return 0
    n = sum(1 for _ in folder.rglob("*") if _.is_file())
    try:
        shutil.rmtree(folder)
    except OSError:
        return 0
    return n
