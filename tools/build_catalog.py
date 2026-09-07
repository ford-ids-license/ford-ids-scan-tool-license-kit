#!/usr/bin/env python3
"""
Build a navigation catalog from decoded .epl metadata (one per archive).

Each archive dir under a volume's content/ holds an .epl describing the work
unit: its type (SERVICE/EVTM/TSB/RECALL/PCED/CALIB/...), code, title, the
vehicles it applies to, and its sections. We parse these into catalog.json.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from volumes import discover_volumes  # noqa: E402

TYPE_TO_CATEGORY = {
    "SERVICE": "workshop",
    "EVTM": "wiring",
    "TSB": "tsb",
    "RECALL": "recall",
    "PCED": "pced",
    "CALIB": "calib",
    "VECI": "veci",
    "BODYREP": "bodyrep",
}
CATEGORY_TITLE = {
    "workshop": "Workshop Manuals",
    "wiring": "Wiring Diagrams",
    "tsb": "TSBs",
    "recall": "Field Service Actions (Recalls)",
    "pced": "PC/ED",
    "calib": "Engine/Emission Facts",
    "veci": "VECI",
    "other": "Other",
}


def _tag(text: str, name: str) -> str | None:
    m = re.search(rf"<{name}>(.*?)</{name}>", text, re.I | re.S)
    return m.group(1).strip() if m else None


def parse_epl(text: str) -> dict:
    info = {
        "type": (_tag(text, "type") or "").upper(),
        "code": _tag(text, "code") or "",
        "title": _tag(text, "title") or "",
        "vehicles": [],
    }
    for vm in re.finditer(r"<vehicle>(.*?)</vehicle>", text, re.I | re.S):
        v = vm.group(1)
        yr = _tag(v, "year") or ""
        nm = _tag(v, "name") or ""
        eng = _tag(v, "engine") or _tag(v, "engsize") or ""
        if yr or nm:
            info["vehicles"].append({"year": yr, "name": nm, "engine": eng})
    return info


def build_volume(content_dir: Path) -> list[dict]:
    entries = []
    for d in sorted(content_dir.iterdir()):
        if not d.is_dir():
            continue
        epl = next((p for p in d.iterdir() if p.suffix.lower() == ".epl"), None)
        code = d.name
        rec = {"archive": d.name, "type": "", "code": code, "title": "", "vehicles": [], "category": "other"}
        if epl:
            try:
                rec.update(parse_epl(epl.read_text(errors="replace")))
            except Exception:
                pass
        rec["category"] = TYPE_TO_CATEGORY.get(rec["type"], "other")
        # find the entry HTML for this archive (main frameset or single page)
        mains = [p.name for p in d.iterdir() if p.suffix.lower() == ".htm" and "main" in p.stem.lower()]
        rec["entry"] = mains[0] if mains else ""
        entries.append(rec)
    return entries


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    args = ap.parse_args()
    for vol in discover_volumes(args.root):
        cdir = args.root / vol / "content"
        if not cdir.exists():
            continue
        cat = build_volume(cdir)
        (args.root / vol / "catalog.json").write_text(json.dumps(cat, indent=1))
        from collections import Counter
        c = Counter(r["category"] for r in cat)
        print(f"{vol}: {len(cat)} archives -> {dict(c)}")


if __name__ == "__main__":
    main()
