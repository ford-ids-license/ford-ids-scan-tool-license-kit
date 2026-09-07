#!/usr/bin/env python3
"""
Navigation integrity check across EVERY category.

Four passes:
  1. Generated pages (landing/home/category listings): every href resolves.
  2. Content sample per category prefix (S,B,V,C,E,T,R + supplements):
     href/src in single OR double quotes resolve to existing files.
  3. Leftover-endpoint sweep: any surviving /tpsasps|/tpscontent|/tpsreposit|
     /tsorep string anywhere (attributes AND JavaScript) is a rewrite failure.
  4. Wiring deep links - FULL content sweep (not sampled). The generic passes
     cannot see these: check_file() drops #fragments, and sampling misses
     books with rare targets. Validates every _wire.html#<frag> against the
     target book's wiring_data.js (cell or 6-digit page key, mirroring the
     viewer's fromHash), and re-runs the leftover sweep on every file.
     Leftover wiring-app links whose book= is absent from the volume's
     decoded content (source disc never shipped it, e.g. E65) are reported
     as warnings, not failures.

Exit code 1 if any pass finds problems, so this can gate the pipeline.
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent))
from volumes import discover_volumes  # noqa: E402

LINK = re.compile(r'''(?:href|src|data)\s*=\s*(?:"([^"]+)"|'([^']+)')''', re.I)
LEFTOVER = re.compile(r'/(?:tpsasps|tpscontent|tpsreposit|tsorep)/', re.I)
WIRE_FRAG = re.compile(r'(?:\.\./([A-Za-z0-9]+)/)?_wire\.html#([^"\'<>\s]*)')
WIRE_ENDPOINT = re.compile(
    r'/tpsasps/(?:wiringsvg/)?(?:ep_main|edirect|eframe)\.asp\b', re.I
)
EP_BOOK = re.compile(r'book=([A-Za-z0-9]+)', re.I)
SKIP = ("http:", "https:", "mailto:", "javascript:", "#", "data:")

# category prefixes -> sample size per volume
AREAS = {
    "workshop (S*)": ("S", 40),
    "bodyrep (B*)": ("B", 20),
    "pced (V*)": ("V", 40),
    "calib (C*)": ("C", 60),
    "tsb (T*)": ("T", 60),
    "recall (R*)": ("R", 40),
    "wiring viewers (E*)": ("E", 10),
}


def finalized_volumes(root: Path) -> list[str]:
    """Volumes that have completed the site-build/finalize stage."""
    return [
        vol for vol in discover_volumes(root)
        if (root / vol / "catalog.json").exists() and (root / vol / "home.html").exists()
    ]


def check_file(p: Path, bad: Counter) -> tuple[int, int]:
    t = p.read_text(encoding="latin-1", errors="replace")
    total = miss = 0
    for m in LINK.finditer(t):
        ref = (m.group(1) or m.group(2)).strip()
        if not ref or ref.lower().startswith(SKIP):
            continue
        total += 1
        tgt = ref.split("#")[0].split("?")[0]
        if not tgt:
            continue
        if tgt.startswith("/"):
            miss += 1
            bad[f"absolute: {tgt}"] += 1
            continue
        if not (p.parent / unquote(tgt.replace("&amp;", "&"))).exists():
            miss += 1
            bad[f"{p.parent.name}/{p.name} -> {ref[:70]}"] += 1
    return total, miss


def leftover_scan(p: Path, bad: Counter) -> int:
    t = p.read_text(encoding="latin-1", errors="replace")
    n = 0
    for m in LEFTOVER.finditer(t):
        n += 1
        bad[t[m.start():m.start() + 45].split('"')[0].split("'")[0]] += 1
    return n


def load_wiring_books(cdir: Path) -> dict[str, tuple[set[str], set[str]]]:
    """ARCHIVE (upper) -> (openable cells incl. unpadded aliases, page keys)."""
    books = {}
    for wd in cdir.glob("E*/wiring_data.js"):
        try:
            data = json.loads(wd.read_text(encoding="latin-1")
                              .removeprefix("window.WIRING=").rstrip(";\n"))
        except (ValueError, OSError):
            continue
        cells = set(data.get("toc", {}))
        cells |= {c.lstrip("0") or "0" for c in cells}
        books[wd.parent.name.upper()] = (cells, set(data.get("pages", {})))
    return books


def wiring_sweep(root: Path, vols: list[str]) -> int:
    """Pass 4: every content file, every _wire.html#frag, every leftover."""
    failures = 0
    for vol in vols:
        cdir = root / vol / "content"
        if not cdir.exists():
            continue
        books = load_wiring_books(cdir)
        archives = {d.name.upper() for d in cdir.iterdir() if d.is_dir()}
        bad = Counter()
        missing_book = Counter()
        files = links = leftovers = 0
        for d in cdir.iterdir():
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if p.suffix.lower() not in (".htm", ".html"):
                    continue
                files += 1
                t = p.read_text(encoding="latin-1", errors="replace")
                tl = t.lower()
                if "_wire.html#" in tl:
                    for m in WIRE_FRAG.finditer(t):
                        frag = m.group(2)
                        if not frag:
                            continue
                        links += 1
                        book = (m.group(1) or d.name).upper()
                        if book not in books:
                            bad[f"{d.name}/{p.name} -> _wire.html in {book}: no viewer"] += 1
                            continue
                        cells, pages = books[book]
                        key = re.sub(r"\D", "", frag)
                        if (frag in cells or frag.zfill(3)[-3:] in cells
                                or (len(key) == 6 and key in pages)):
                            continue
                        bad[f"{d.name}/{p.name} -> {book}#{frag}: no such cell/page"] += 1
                for m in LEFTOVER.finditer(t):
                    snippet = t[m.start():m.start() + 120]
                    bm = EP_BOOK.search(snippet)
                    if (WIRE_ENDPOINT.match(snippet) and bm
                            and bm.group(1).upper() not in archives):
                        missing_book[bm.group(1).upper()] += 1
                    else:
                        leftovers += 1
                        bad[snippet[:45].split('"')[0].split("'")[0]] += 1
        print(f"[wiring deep links] {vol}: files={files} frag-links={links} "
              f"broken={sum(bad.values())}")
        for k, v in bad.most_common(8):
            print(f"    {k} x{v}")
        for k, v in missing_book.most_common():
            print(f"    warning: {v} links to book {k} (not on this source disc)")
        failures += sum(bad.values())
    return failures


def main():
    root = Path(".")
    random.seed(3)
    failures = 0

    # pass 1: generated pages
    pages = [root / "index.html"] + list(root.glob("vol_*/*.html"))
    tot = miss = 0
    bad = Counter()
    for f in pages:
        a, b = check_file(f, bad)
        tot += a
        miss += b
    print(f"[generated pages] pages={len(pages)} links={tot} broken={miss}")
    for k, v in bad.most_common(8):
        print(f"    {k} x{v}")
    failures += miss

    # passes 2+3: content samples per category
    vols = finalized_volumes(root)
    skipped = [vol for vol in discover_volumes(root) if vol not in vols]
    if skipped:
        print(f"[skipped draft volumes] {', '.join(skipped)}")
    for area, (prefix, n) in AREAS.items():
        files = []
        for vol in vols:
            cdir = root / vol / "content"
            if not cdir.exists():
                continue
            dirs = [d for d in cdir.iterdir() if d.is_dir() and d.name.upper().startswith(prefix)]
            for d in random.sample(dirs, min(max(n // 10, 1), len(dirs))):
                htms = [p for p in d.iterdir() if p.suffix.lower() in (".htm", ".html")]
                files += random.sample(htms, min(10, len(htms)))
        tot = miss = left = 0
        bad = Counter()
        leftovers = Counter()
        for f in files:
            a, b = check_file(f, bad)
            tot += a
            miss += b
            left += leftover_scan(f, leftovers)
        print(f"[{area}] files={len(files)} links={tot} broken={miss} leftover-endpoints={left}")
        for k, v in bad.most_common(5):
            print(f"    broken: {k} x{v}")
        for k, v in leftovers.most_common(5):
            print(f"    leftover: {k} x{v}")
        failures += miss + left

    # pass 4: full wiring deep-link sweep
    failures += wiring_sweep(root, vols)

    print("PASS" if failures == 0 else f"FAIL ({failures} problems)")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
