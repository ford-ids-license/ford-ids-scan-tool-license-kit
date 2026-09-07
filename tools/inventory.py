#!/usr/bin/env python3
"""
Inventory a content locale directory (e.g. useni4) without loading whole
archives.

Reads each .arc's header + TOC + filename table, then sniffs every member's
payload magic via a small seek/read. Classifies each payload as:
  - "raw"   : stored binary (gif/jpg/png/pdf) -> extractable now, no decode
  - "token" : IDICOMP token-encoded text (htm/xml/svg/epl/wcf) -> needs decoder
  - "other" : unrecognized

Writes a per-volume JSON manifest and prints aggregate stats.

Usage:
  python inventory.py "D:\\content\\useni4" --out vol_example/inventory.json
"""
from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from pathlib import Path

MAGIC_V2 = b"BAY POD\x02"
MAGIC_V1 = b"POD BAY\x01"
IDICOMP_SIG = b"\x01IDICOMP"
IDI_HDR = 11

RAW_EXT = {"gif", "jpg", "jpeg", "png", "pdf"}


def _u24le(b: bytes, off: int) -> int:
    return b[off + 1] | (b[off + 2] << 8) | (b[off + 3] << 16)


def _sniff(payload_head: bytes) -> str:
    """Return an extension guess from the first bytes of a payload."""
    s = payload_head
    if s[:3] == b"GIF":
        return "gif"
    if s[:4] == b"%PDF":
        return "pdf"
    if s[:4] == b"\x89PNG":
        return "png"
    if s[:2] == b"\xff\xd8":
        return "jpg"
    if s[:5] == b"<?xml":
        # svg and plain xml both start with <?xml; refine with a wider look upstream
        return "xml"
    low = s.lower()
    if b"<html" in low:
        return "htm"
    if b"<workunit>" in low:
        return "epl"
    if low[:3] == b"wcf" or b"workunit=" in low or b"proddate=" in low:
        return "wcf"
    return "bin"


def inventory_archive(path: Path) -> dict:
    with path.open("rb") as fh:
        head = fh.read(16)
        if len(head) < 12:
            raise ValueError("too small")
        magic = head[:8]
        if magic == MAGIC_V2:
            version = 2
            num_files = _u24le(head, 0x08)
            total_name_bytes = _u24le(head, 0x0C)
            toc_start = 0x10
            toc_end = toc_start + num_files * 16
            name_start = toc_end + 1
            # read TOC + name table only
            fh.seek(toc_start)
            toc = fh.read(num_files * 16)
            fh.seek(name_start)
            names = fh.read(total_name_bytes)
            entries = []
            for i in range(num_files):
                base = i * 16
                noff = _u24le(toc, base)
                nlen = _u24le(toc, base + 4)
                doff = struct.unpack_from("<I", toc, base + 9)[0]
                dsize = _u24le(toc, base + 12)
                name = names[noff:noff + nlen].decode("ascii", "replace")
                entries.append((name, doff, dsize))
        elif magic == MAGIC_V1:
            version = 1
            num_files = _u24le(head, 0x08)
            toc_start = 0x0C
            fh.seek(toc_start)
            toc = fh.read(num_files * 15)
            entries = []
            for i in range(num_files):
                base = i * 15
                doff = struct.unpack_from("<I", toc, base + 9)[0]
                dsize = struct.unpack_from("<H", toc, base + 13)[0]
                entries.append((f"{path.stem}_{i:04d}", doff, dsize))
        else:
            raise ValueError(f"unknown magic {magic!r}")

        members = []
        for name, doff, dsize in entries:
            fh.seek(doff)
            probe = fh.read(IDI_HDR + 16)
            has_idi = probe[:8] == IDICOMP_SIG
            payload_head = probe[IDI_HDR:] if has_idi else probe
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            sniff = _sniff(payload_head)
            # payload class: raw if the *stored* bytes are a known binary magic
            if sniff in RAW_EXT:
                cls = "raw"
            elif has_idi:
                cls = "token"
            else:
                cls = "other"
            members.append({
                "name": name, "ext": ext or sniff, "size": dsize,
                "idi": has_idi, "sniff": sniff, "class": cls,
            })

    return {
        "archive": path.name,
        "version": version,
        "num_files": num_files,
        "members": members,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("locale_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    arcs = sorted(args.locale_dir.glob("*.arc"), key=lambda p: p.name.upper())
    if args.limit:
        arcs = arcs[:args.limit]

    manifest = {"locale_dir": str(args.locale_dir), "archives": []}
    ext_counter = Counter()
    class_counter = Counter()
    class_bytes = Counter()
    failed = []
    for i, p in enumerate(arcs, 1):
        try:
            info = inventory_archive(p)
        except Exception as e:
            failed.append((p.name, str(e)))
            continue
        manifest["archives"].append(info)
        for m in info["members"]:
            ext_counter[m["ext"]] += 1
            class_counter[m["class"]] += 1
            class_bytes[m["class"]] += m["size"]
        if i % 500 == 0:
            print(f"  ...{i}/{len(arcs)} archives")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=1))

    total_members = sum(class_counter.values())
    print(f"\n== {args.locale_dir}")
    print(f"archives: {len(manifest['archives'])} parsed, {len(failed)} failed")
    print(f"members : {total_members}")
    print("by class:")
    for cls, n in class_counter.most_common():
        mb = class_bytes[cls] / 1e6
        print(f"   {cls:6s} {n:>8d}  {mb:>10.1f} MB")
    print("by ext (top 15):")
    for ext, n in ext_counter.most_common(15):
        print(f"   {ext:6s} {n:>8d}")
    if failed:
        print("failed:", failed[:10])


if __name__ == "__main__":
    main()
