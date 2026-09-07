#!/usr/bin/env python3
"""
Generate the static browsable site on top of decoded local content.

The generated site preserves the source navigation relationships:

  - Book categories (workshop, body repair, PC/ED) enter at the archive's own
    {code}main.htm frameset when present. A
    generated _book.html is created only as a fallback when main.htm is absent.
  - Calibration has NO per-book TOC by design: the coverage DB is the index.
    calib.html lists year/model/engine -> calibration ID -> the single page.
  - TSB/Recall entry files come from the coverage DB FILENAME column - never
    "first file in the archive".
  - Wiring lists year/model -> _wire.html (built by build_wiring.py).

Link rewriting inside content is exclusively rewrite_links.py's job.

Inputs per volume: catalog.json (.epl metadata) + coverage.json (source
navigation DB, built by build_coverage.py; optional - DBF-driven pages fall
back to catalog data when absent).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from volumes import discover_volumes  # noqa: E402

CATS = [("workshop", "Workshop Manuals"),
        ("bodyrep", "Body Repair Manuals"),
        ("wiring", "Wiring Diagrams"),
        ("tsb", "TSBs"),
        ("recall", "Field Service Actions (Recalls)"),
        ("pced", "PC/ED"),
        ("calib", "Engine/Emission Facts")]
CATNAMES = dict(CATS)

CSS = """
:root{--red:#900;--ink:#111;--line:#bbb;--accent:#94d6e7;--bg:#f4f4f4}
*{box-sizing:border-box}
body{font-family:Verdana,Arial,sans-serif;margin:0;color:var(--ink);background:var(--bg)}
a{color:#0645ad;text-decoration:none}a:hover{text-decoration:underline}
.top{background:var(--red);color:#fff;padding:10px 16px;display:flex;align-items:center;gap:14px}
.top b{font-size:18px}.top a{color:#fff;font-weight:bold}
.wrap{max-width:1100px;margin:0 auto;padding:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:#fff;border:1px solid var(--line);border-radius:6px;padding:14px 16px}
.card h3{margin:.1em 0 .3em}.card .sub{color:#666;font-size:12px}
.side{display:flex;min-height:100vh}
.nav{width:250px;background:#e9eef0;border-right:1px solid var(--line);padding:16px}
.nav h1{font-size:15px;margin:0 0 10px}
.nav ul{list-style:none;padding:0;margin:0}
.nav li{margin:6px 0}.nav .cat{font-weight:bold;display:block;padding:6px 8px;border-radius:4px}
.nav .cat:hover{background:var(--accent)}
.main{flex:1;padding:24px}
table{width:100%;border-collapse:collapse;background:#fff}
th,td{padding:6px 10px;border-bottom:1px solid #e2e2e2;text-align:left;font-size:13px}
th{background:#eef2f4;position:sticky;top:0}
tr:hover td{background:#f7fbfd}
.search{width:100%;padding:8px;font-size:14px;margin:0 0 12px;border:1px solid var(--line);border-radius:4px}
.count{color:#666;font-size:12px;margin:4px 0 12px}
.note{color:#666;font-size:12px;margin:6px 0 12px}
"""

SEARCH_JS = """
function filt(q){q=q.toLowerCase();var n=0;
document.querySelectorAll('tbody tr').forEach(function(r){
 var m=r.textContent.toLowerCase().indexOf(q)>=0;r.style.display=m?'':'none';if(m)n++;});
var c=document.getElementById('cnt');if(c)c.textContent=n+' shown';}
"""

# Static replacement for the idisp_frame.asp/dodisp.asp figure viewer: same
# layout as the original @param@ template (title, iframe with the figure,
# back button). Query args come through unchanged from the content's doDisp().
FIG_HTML = """<!DOCTYPE html><html><head><meta charset='windows-1252'><title>Figure</title>
<style>html,body{margin:0;height:100%;font-family:Arial}
.wrap{display:flex;flex-direction:column;height:100vh}
h3{text-align:center;margin:8px}iframe{flex:1;border:0}
.bar{background:#808080;text-align:right;padding:4px 8px}</style></head><body>
<div class='wrap'><h3 id='t'></h3><iframe id='f'></iframe>
<div class='bar'><button onclick='history.back()' id='b'>Return</button></div></div>
<script>var q=new URLSearchParams(location.search);
var t=q.get('Title')||q.get('title')||'';var img=q.get('img')||'';var b=q.get('button');
document.getElementById('t').textContent=t;document.title=t||'Figure';
if(img)document.getElementById('f').src=img;
if(b)document.getElementById('b').textContent=b;</script></body></html>"""


def esc(s: str) -> str:
    return html.escape(s or "")


def page(title: str, body: str, css_href: str) -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title><link rel='stylesheet' href='{css_href}'></head>"
            f"<body>{body}</body></html>")


def write_text_lf(path: Path, text: str, encoding: str = "utf-8") -> None:
    with path.open("w", encoding=encoding, newline="\n") as f:
        f.write(text)


def fmt_date(d: str) -> str:
    d = (d or "").strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


def archive_for(fname: str, archives_upper: dict[str, str]) -> str | None:
    """Workunit lookup: longest existing archive-name prefix."""
    up = Path(fname).name.upper()
    best = ""
    for a in archives_upper:
        if up.startswith(a) and len(a) > len(best):
            best = a
    return archives_upper.get(best)


def find_member(d: Path, name: str) -> str | None:
    """Case-insensitive member lookup; returns the on-disk name."""
    low = name.lower()
    for p in d.iterdir():
        if p.name.lower() == low:
            return p.name
    return None


def write_colframe_shell(d: Path) -> None:
    write_text_lf(d / "_2col.html",
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>html,body{margin:0;height:100%}"
        ".r{display:flex;height:100vh}iframe{border:0}</style></head><body>"
        "<div class='r'><iframe name='leftside' style='width:25%'></iframe>"
        "<iframe name='rightside' style='flex:1'></iframe></div><script>"
        "var q=new URLSearchParams(location.search);"
        "document.getElementsByName('leftside')[0].src=q.get('l');"
        "document.getElementsByName('rightside')[0].src=q.get('r');</script></body></html>")


def veh_label(rec) -> str:
    if rec["vehicles"]:
        v = rec["vehicles"][0]
        return f"{v.get('year','')} {v.get('name','')}".strip()
    return rec.get("title") or rec["code"]


def make_book_shells(d: Path, book: str, title: str, vehicle: str) -> str:
    """Ensure _front.html and _2col.html exist; return the book's entry page:
    the archive's own {code}main.htm when present, else a generated
    _book.html frameset."""
    vehic = None
    for p in d.iterdir():
        if "vehic" in p.name.lower():
            vehic = p.name
            break
    img = f"<img src='{esc(vehic)}' style='max-width:90%'>" if vehic else ""
    write_text_lf(d / "_front.html",
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>%s</title>"
        "<style>body{font-family:Verdana,Arial;text-align:center;padding:30px;background:#fff}"
        "h2{color:#900}</style></head><body><h2>%s</h2><p>%s</p>%s</body></html>"
        % (esc(title), esc(title), esc(vehicle), img))
    write_colframe_shell(d)

    main = find_member(d, f"{book}main.htm")
    if main:
        return main
    left = find_member(d, f"{book}left.htm") or ""
    write_text_lf(d / "_book.html",
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>%s</title>"
        "<style>html,body{margin:0;height:100%%}.r{display:flex;height:100vh}iframe{border:0}</style></head>"
        "<body><div class='r'><iframe name='leftside' src='%s' style='width:25%%;border-right:1px solid #bbb'></iframe>"
        "<iframe name='rightside' src='_front.html' style='flex:1'></iframe></div></body></html>"
        % (esc(title), esc(left)))
    return "_book.html"


def listing_page(vol_title: str, heading: str, cols: list[str], rows: list[str],
                 note: str = "") -> str:
    note_html = f"<div class='note'>{note}</div>" if note else ""
    return (f"<div class='wrap'><p><a href='home.html'>&#8592; {esc(vol_title)}</a></p>"
            f"<h2>{esc(heading)}</h2>{note_html}"
            f"<input class='search' placeholder='Filter...' oninput='filt(this.value)'>"
            f"<div class='count' id='cnt'>{len(rows)} shown</div>"
            f"<table><thead><tr>{''.join(f'<th>{esc(c)}</th>' for c in cols)}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div><script>{SEARCH_JS}</script>")


def build_volume_site(root: Path, vol: str, vol_title: str, release: str):
    voldir = root / vol
    cat = json.load(open(voldir / "catalog.json"))
    cov = {}
    if (voldir / "coverage.json").exists():
        cov = json.load(open(voldir / "coverage.json", encoding="utf-8"))
    cdir = voldir / "content"
    archives_upper = {d.name.upper(): d.name for d in cdir.iterdir() if d.is_dir()}
    for d in cdir.iterdir():
        if d.is_dir():
            write_colframe_shell(d)
    by = defaultdict(list)
    for r in cat:
        by[r["category"]].append(r)
    # supplements (SIA/SIB = workshop supplements) surface in the workshop
    # listing; VID/VIE (VECI sheets) are linked from the calib page.
    supplements = [a for a in ("SIA", "SIB") if a in archives_upper]
    veci_sheets = [a for a in ("VID", "VIE") if a in archives_upper]
    counts = {}

    def link_row(cells: list[str]) -> str:
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    def book_link(arch: str, label: str, rec=None) -> str:
        d = cdir / arch
        title = (rec.get("title") if rec else None) or label
        vehicle = veh_label(rec) if rec else label
        entry = make_book_shells(d, arch.lower(), title, vehicle)
        return f"<a href='content/{arch}/{entry}'>{esc(label)}</a>"

    rec_by_arch = {r["archive"]: r for r in cat}

    # ---- workshop / pced: coverage rows (year+model -> dest) when available
    for c, module in (("workshop", "service"), ("pced", "pced")):
        rows = []
        seen_archives = set()
        for cr in cov.get(module, []):
            arch = archive_for(cr["dest"], archives_upper)
            if not arch or not (cdir / arch).exists():
                continue
            seen_archives.add(arch)
            label = f"{cr['year']} {cr['model']}".strip()
            if cr.get("qualifier"):
                label += f" ({cr['qualifier']})"
            d = cdir / arch
            rec = rec_by_arch.get(arch)
            title = (rec.get("title") if rec else "") or cr["title"]
            make_book_shells(d, arch.lower(), title, label)
            entry = find_member(d, cr["dest"]) or find_member(d, f"{arch.lower()}main.htm") or "_book.html"
            rows.append(link_row(
                [f"<a href='content/{arch}/{entry}'>{esc(label)}</a>",
                 esc(arch), esc(cr["title"] if cr["title"] != "Table of Contents" else "")]))
        # catalog fallback for books the coverage DB doesn't list
        for r in sorted(by.get(c, []), key=veh_label):
            if r["archive"] in seen_archives or not (cdir / r["archive"]).exists():
                continue
            rows.append(link_row([book_link(r["archive"], veh_label(r), r),
                                  esc(r["code"]), esc(r.get("title", ""))]))
        if c == "workshop":
            for arch in supplements:
                d = cdir / arch
                m = re.search(r"<title>\s*([^<]+?)\s*</title>",
                              (d / f"{arch}main.htm").read_text(encoding="latin-1", errors="replace"),
                              re.I | re.S) if (d / f"{arch}main.htm").exists() else None
                label = m.group(1) if m else arch
                rows.append(link_row([book_link(arch, label), esc(arch), "Supplement"]))
        counts[c] = len(rows)
        listing = listing_page(vol_title, CATNAMES[c], ["Vehicle / Title", "Code", "Notes"], rows)
        write_text_lf(voldir / f"{c}.html", page(CATNAMES[c], listing, "../site.css"))

    # ---- body repair: catalog only (no coverage DB on these discs)
    rows = []
    for r in sorted(by.get("bodyrep", []), key=veh_label):
        if not (cdir / r["archive"]).exists():
            continue
        rows.append(link_row([book_link(r["archive"], veh_label(r), r),
                              esc(r["code"]), esc(r.get("title", ""))]))
    counts["bodyrep"] = len(rows)
    listing = listing_page(vol_title, CATNAMES["bodyrep"], ["Vehicle / Title", "Code", "Notes"], rows)
    write_text_lf(voldir / "bodyrep.html", page(CATNAMES["bodyrep"], listing, "../site.css"))

    # ---- calibration: the coverage DB IS the index (books have no TOC)
    rows = []
    for cr in cov.get("calib", []):
        arch = archive_for(cr["dest"], archives_upper)
        if not arch:
            continue
        d = cdir / arch
        entry = find_member(d, cr["dest"])
        if not entry:
            continue
        rows.append(link_row(
            [esc(cr["year"]), esc(cr["model"]), esc(cr["qualifier"]),
             f"<a href='content/{arch}/{entry}'>{esc(cr['title'])}</a>"]))
    # every calib book gets the static figure viewer used by doDisp() links
    for r in by.get("calib", []):
        d = cdir / r["archive"]
        if d.exists():
            write_text_lf(d / "_fig.html", FIG_HTML)
    counts["calib"] = len(rows)
    veci_note = ""
    if veci_sheets:
        links = " &middot; ".join(
            f"<a href='content/{a}/{find_member(cdir / a, f'{a.lower()}main.htm') or ''}'>{a}</a>"
            for a in veci_sheets)
        for a in veci_sheets:
            make_book_shells(cdir / a, a.lower(), f"VECI {a}", "VECI sheets")
        veci_note = f"VECI sheets: {links}"
    listing = listing_page(vol_title, CATNAMES["calib"],
                           ["Year", "Model", "Engine", "Calibration"], rows, veci_note)
    write_text_lf(voldir / "calib.html", page(CATNAMES["calib"], listing, "../site.css"))

    # ---- tsb / recall: entry file = coverage FILENAME column
    for c, key in (("tsb", "tsb"), ("recall", "recall")):
        rows = []
        for doc in cov.get(key, []):
            arch = archive_for(doc["filename"], archives_upper)
            if not arch:
                continue
            entry = find_member(cdir / arch, doc["filename"])
            if not entry:
                continue
            link = f"content/{arch}/{entry}"
            if key == "tsb":
                title = "; ".join(doc["titles"]) if doc["titles"] else arch
                label = doc["article"] or arch
                sup = f" (superseded by {esc(doc['super'])})" if doc.get("super") else ""
                rows.append(link_row(
                    [f"<a href='{esc(link)}' target='_blank'>{esc(label)}</a>",
                     esc(title) + sup, esc(fmt_date(doc["date"]))]))
            else:
                rows.append(link_row(
                    [f"<a href='{esc(link)}' target='_blank'>{esc(doc['recall'])}</a>",
                     esc(doc["title"]), esc(fmt_date(doc["date"]))]))
        # catalog fallback for docs missing from the coverage DB
        listed = len(rows)
        covered = {archive_for(doc["filename"], archives_upper) for doc in cov.get(key, [])}
        for r in sorted(by.get(c, []), key=lambda x: x["archive"]):
            if r["archive"] in covered:
                continue
            d = cdir / r["archive"]
            if not d.exists():
                continue
            f = next((p.name for p in sorted(d.iterdir())
                      if p.suffix.lower() in (".pdf", ".htm", ".html")), None)
            if not f:
                continue
            rows.append(link_row(
                [f"<a href='content/{r['archive']}/{f}' target='_blank'>{esc(r.get('title') or r['code'])}</a>",
                 esc(r["code"]), ""]))
        counts[c] = len(rows)
        head = ["Article", "Title", "Date"] if c == "tsb" else ["Recall", "Title", "Date"]
        listing = listing_page(vol_title, CATNAMES[c], head, rows)
        write_text_lf(voldir / f"{c}.html", page(CATNAMES[c], listing, "../site.css"))
        if listed != len(rows):
            print(f"  {vol}/{c}: {len(rows) - listed} entries from catalog fallback")

    # ---- wiring: coverage rows (year/model) -> _wire.html viewers
    rows = []
    seen_archives = set()
    for cr in cov.get("evtm", []):
        m = re.search(r"book=([A-Za-z0-9]+)", cr["dest"])
        if not m:
            continue
        arch = archives_upper.get(m.group(1).upper())
        if not arch or not (cdir / arch / "_wire.html").exists():
            continue
        seen_archives.add(arch)
        rows.append(link_row(
            [f"<a href='content/{arch}/_wire.html'>{esc(cr['year'] + ' ' + cr['model'])}</a>",
             esc(arch)]))
    for r in sorted(by.get("wiring", []), key=veh_label):
        if r["archive"] in seen_archives or not (cdir / r["archive"] / "_wire.html").exists():
            continue
        rows.append(link_row(
            [f"<a href='content/{r['archive']}/_wire.html'>{esc(veh_label(r))}</a>", esc(r["code"])]))
    counts["wiring"] = len(rows)
    listing = listing_page(vol_title, CATNAMES["wiring"], ["Vehicle", "Book"], rows)
    write_text_lf(voldir / "wiring.html", page(CATNAMES["wiring"], listing, "../site.css"))

    # ---- sidebar home
    navlis = "".join(
        f"<li><a class='cat' href='{c}.html'>{esc(name)} <span class='sub'>({counts.get(c, 0)})</span></a></li>"
        for c, name in CATS)
    home = f"""
<div class='side'>
 <div class='nav'>
   <h1><a href='../index.html' style='color:#900'>&#8592; Service Archive</a><br>{esc(vol_title)}<br><span class='sub'>{esc(release)}</span></h1>
   <ul>{navlis}</ul>
 </div>
 <div class='main'>
   <h2>{esc(vol_title)}</h2>
   <p>Select a category from the left. This is a static, offline conversion of
   local source files. No legacy browser runtime is required.</p>
   <div class='grid'>{''.join(f"<a class='card' href='{c}.html'><h3>{esc(name)}</h3><div class='sub'>{counts.get(c, 0)} items</div></a>" for c, name in CATS)}</div>
 </div>
</div>"""
    write_text_lf(voldir / "home.html", page(vol_title, home, "../site.css"))
    return counts


def vol_meta_for(root: Path, vol: str) -> tuple[str, str]:
    meta_path = root / vol / "vol_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return meta.get("title", vol), meta.get("release", "")
    return vol, ""


def main():
    ap = argparse.ArgumentParser(description="Generate the static archive site.")
    ap.add_argument("--root", type=Path, default=Path("."))
    args = ap.parse_args()

    root = args.root
    write_text_lf(root / "site.css", CSS)
    cards = []
    for vol in discover_volumes(root):
        if not (root / vol / "catalog.json").exists():
            continue
        title, rel = vol_meta_for(root, vol)
        stats = build_volume_site(root, vol, title, rel)
        cards.append(f"<a class='card' href='{vol}/home.html'><h3>{esc(title)}</h3>"
                     f"<div class='sub'>{esc(rel)}</div></a>")
        print(f"{vol}: {stats}")
    landing = (f"<div class='top'><b>Service Archive</b> &mdash; Local static view</div>"
               f"<div class='wrap'><h2>Please select a volume to view</h2>"
               f"<div class='grid'>{''.join(cards)}</div></div>")
    write_text_lf(root / "index.html", page("Service Archive", landing, "site.css"))
    print("wrote index.html")


if __name__ == "__main__":
    main()
