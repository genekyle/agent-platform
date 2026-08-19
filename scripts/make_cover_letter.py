#!/usr/bin/env python3
"""Fill the base cover letter's slots for one opportunity and emit a .doc the ATS will take.

Why a script and not a one-off: every ATS that asks for a cover letter asks for a FILE, and a file
produced by hand each time drifts from the résumé it is supposed to match. The base
(`assets/documents/cover_letters/BASE.md`) is the single source; tailoring is four named slots, so a
diff between two tailored letters shows exactly what was claimed differently — which is the property
we want when the same person applies to twenty places.

Output is `.doc` because that is what `textutil` can produce without a third-party dependency, and
every ATS met so far (Paylocity: ".pdf and .doc files only") accepts it.

    python3 scripts/make_cover_letter.py --slug gardner-museum \
        --role "Community Relations Database Analyst" --org "the Isabella Stewart Gardner Museum" \
        --hook "..." --bridge "..."
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "assets" / "documents" / "cover_letters" / "BASE.md"
OUT_DIR = ROOT / "assets" / "documents" / "cover_letters"

#: The letter starts after the front-matter table; everything above `---` is instructions to us.
_BODY_MARKER = "\n---\n"


def body_of(base_text: str) -> str:
    """The letter itself, without the how-to-tailor header."""
    _, _, body = base_text.partition(_BODY_MARKER)
    return body.strip()


def fill(body: str, slots: dict[str, str]) -> str:
    missing = [k for k in ("ROLE", "ORG", "HOOK", "BRIDGE") if not (slots.get(k) or "").strip()]
    if missing:
        raise SystemExit(f"refusing to write a letter with empty slots: {', '.join(missing)}")
    for key, value in slots.items():
        body = body.replace("{{%s}}" % key, value.strip())
    left = [line for line in body.splitlines() if "{{" in line]
    if left:
        raise SystemExit(f"unfilled slot left in the letter: {left[0].strip()}")
    return body


def to_doc(text: str, out_stem: Path) -> Path:
    """Write the letter as .doc via textutil, going through HTML so paragraphs survive."""
    # A single newline inside a paragraph is a deliberate line break (the address block, the
    # sign-off); a blank line starts a new paragraph. Joining both with a space ran "Sincerely,"
    # into the name.
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    # The charset meta is load-bearing: without it textutil reads UTF-8 as Latin-1 and every
    # en-dash, em-dash and middot in the letter arrives as mojibake (Â·, â€”) in the .doc a
    # hiring manager opens.
    page = ('<html><head><meta charset="utf-8"></head>'
            '<body style="font-family:Georgia,serif;font-size:12pt;line-height:1.4">')
    for para in paragraphs:
        page += "<p>" + "<br>".join(html.escape(line) for line in para.splitlines()) + "</p>"
    page += "</body></html>"

    src = out_stem.with_suffix(".html")
    src.write_text(page, encoding="utf-8")
    subprocess.run(
        ["textutil", "-convert", "doc", "-inputencoding", "UTF-8",
         "-output", str(out_stem.with_suffix(".doc")), str(src)],
        check=True,
    )
    src.unlink()
    return out_stem.with_suffix(".doc")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="short name for the file, e.g. gardner-museum")
    ap.add_argument("--role", required=True)
    ap.add_argument("--org", required=True)
    ap.add_argument("--hook", required=True)
    ap.add_argument("--bridge", required=True)
    args = ap.parse_args(argv)

    body = fill(body_of(BASE.read_text(encoding="utf-8")),
                {"ROLE": args.role, "ORG": args.org, "HOOK": args.hook, "BRIDGE": args.bridge})

    stem = OUT_DIR / f"GM_Cover_Letter_{args.slug}"
    stem.with_suffix(".md").write_text(body + "\n", encoding="utf-8")
    doc = to_doc(body, stem)
    print(f"wrote {stem.with_suffix('.md').relative_to(ROOT)}")
    print(f"wrote {doc.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
