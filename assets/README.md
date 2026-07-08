# Assets

Local asset store for listing photos (and future media). **This is a stub for cloud storage** —
today it's a folder on disk; the plan is to back it with S3 (or the cheapest fit) later.

## Why local files, not URLs
Facebook Marketplace's create-listing form uploads photos through a **file input** (not a URL field),
so the driver needs a real **local file path** at post time. Storing item photos as asset *keys* that
resolve to local paths (now) / downloaded temp files (S3 later) keeps the fill step working either way.

## Structure — organized like S3 keys
```
assets/
  marketplace/          # item photos for Facebook Marketplace listings
    <name>.jpg
```
The path under `assets/` IS the asset **key** (e.g. `marketplace/sample-hoodie.jpg`). Items store keys
in `item.photos`; the same key becomes the S3 object key with no caller changes.

## The seam (swap to cloud here only)
`apps/controlplane-api/assets.py` is the single place that knows where assets live:
- `list_assets(prefix)` — enumerate keys (the UI picker reads this)
- `abs_path(key)` — local path for upload (later: download from S3 to a temp file)
- `public_url(key)` — `/assets/<key>` served by the API for UI thumbnails (later: an S3/CDN URL)

Moving to S3 = reimplement those three functions; nothing else changes. Root is configurable via
`ASSETS_DIR` in `.env` (defaults to this folder).

The `sample-*.jpg` files are throwaway placeholders so the flow can be tested end-to-end — replace
them with real photos (drop files into `assets/marketplace/`, they appear in the picker).
