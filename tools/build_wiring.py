#!/usr/bin/env python3
"""
Build the interactive wiring-diagram viewer for each EVTM (E*) book.

Data model (per decoded EVTM book):
  E{cell}{page}.SVG            schematic diagram pages (the actual wiring)
  E{cell}{page}.xml            companion: <title>, <connector_collection> with
                               each <conn> -> name, loc, zone, loc_view, face_view;
                               plus <ground_collection>, <splice_collection>,
                               <fuse_collection> (loc_view only; field presence
                               varies by model year - splices gain loc_view in
                               later books, fuse face_page/bestview_page always
                               duplicate loc_view where present)
  E{book}151{loc}.SVG          connector LOCATION views (where it physically is)
  <partnum>.SVG (CONNECTOR_FACE)  connector FACE / pinout views
  e{book}cf{conn}.xml          maps a connector -> its face-view SVG

Output per book dir:
  wiring_data.js   window.WIRING = {toc, pages, ...}   (loaded via <script>, so
                   it works from file:// with no fetch/CORS)
  _wire.html       the single-page viewer app

Everything is offline/static: no ASP, no DB, no ActiveX.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from volumes import discover_volumes  # noqa: E402
from jetdb import JetDb  # noqa: E402
from wiring_index import build_mdb_index, build_xml_index, entity_key  # noqa: E402


UI_JS = Path(__file__).with_name("wiring_index_ui.js")
UI_CSS = Path(__file__).with_name("wiring_index_ui.css")


def write_viewer(book_dir: Path, book: str, template: str) -> None:
    """Write the viewer plus its shared, offline index/detail UI assets."""
    html = template.replace("{BOOK}", book)
    html = html.replace("</head>", "<link rel='stylesheet' href='wiring_index_ui.css'></head>")
    html = html.replace("</body>",
                        "<script src='wiring_index_ui.js'></script>"
                        "<script>WiringIndexUI.init();</script></body>")
    (book_dir / "_wire.html").write_text(html)
    (book_dir / "wiring_index_ui.js").write_text(UI_JS.read_text())
    (book_dir / "wiring_index_ui.css").write_text(UI_CSS.read_text())


def tag(t, n):
    m = re.search(rf"<{n}>(.*?)</{n}>", t, re.S | re.I)
    return (m.group(1).strip() if m else "")


def cdata(s):
    m = re.search(r"<!\[CDATA\[(.*?)\]\]>", s, re.S)
    return (m.group(1).strip() if m else s.strip())


def parse_pins(t: str) -> list:
    """<Pin Cavity=.. CircuitNumber=.. Color=.. Guage=.. Function=.. Used=..> -> rows."""
    pins = []
    for pm in re.finditer(r"<Pin\b([^>]*)/?>", t, re.I):
        a = dict(re.findall(r'(\w+)="([^"]*)"', pm.group(1)))
        circ = a.get("CircuitNumber", "")
        if circ and a.get("Color"):
            circ += " (" + a["Color"] + ")"
        pins.append([a.get("Cavity", ""), circ, a.get("Guage", ""),
                     a.get("Function", ""), a.get("Used", "1")])
    return pins


def resolve_face_svg(book_dir: Path, face_view: str) -> tuple[str, list]:
    """cf-xml -> (actual face SVG filename, pin table rows), or ('', [])."""
    if not face_view:
        return "", []
    for cand in (face_view + ".xml", face_view.upper() + ".XML"):
        p = book_dir / cand
        if p.exists():
            t = p.read_text(errors="replace")
            m = re.search(r"([0-9A-Za-z_\-]+\.svg)", t, re.I)
            if m and (book_dir / m.group(1)).exists():
                return m.group(1), parse_pins(t)
    # sometimes the face view is itself an svg (table is drawn inside it)
    for cand in (face_view + ".SVG", face_view + ".svg"):
        if (book_dir / cand).exists():
            return cand, []
    return "", []


def svg_name(book_dir: Path, base: str) -> str:
    for c in (base + ".SVG", base + ".svg"):
        if (book_dir / c).exists():
            return c
    return ""


def parse_items(t: str, kind: str, book_dir: Path) -> list:
    """<ground>/<splice>/<fuse> entries -> [{n, loc, z, locv?}], deduplicated.
    Source books repeat entries verbatim; field presence varies by year."""
    items, seen = [], set()
    for m in re.finditer(rf"<{kind}>(.*?)</{kind}>", t, re.S | re.I):
        c = m.group(1)
        name = cdata(tag(c, "name"))
        if not name:
            continue
        lv = svg_name(book_dir, tag(c, "loc_view"))
        key = (name.upper(), lv)
        if key in seen:
            continue
        seen.add(key)
        it = {"n": name, "loc": cdata(tag(c, "loc")), "z": tag(c, "zone")}
        if lv:
            it["locv"] = lv
        items.append(it)
    return items


# standard EVTM section titles, for cells that ship without any titled page
# (verified uniform across the XML books and the 2004 .mdb CELLS tables)
STD_CELLS = {
    "003": "Introduction",
    "004": "Symbols",
    "005": "Connector Repair Procedures",
    "009": "Wiring Harness Overview",
    "010": "Grounds",
    "011": "Fuse and Relay Information",
    "149": "Component Testing",
    "150": "Connector Views",
    "151": "Component Location Views",
    "152": "Component Location Charts",
    "160": "Vehicle Repair Location Charts",
}


def build_book(book_dir: Path, book: str) -> dict | None:
    pages = {}          # "003001" -> {cell,page,title,type,svg,connectors:[...]}
    toc = {}            # cell -> {title, pages:[pagekey...]}
    page_xmls = sorted(book_dir.glob(f"{book}[0-9]*.xml"))
    for xp in page_xmls:
        m = re.match(rf"{re.escape(book)}(\d{{3}})(\d{{3}})$", xp.stem, re.I)
        if not m:
            continue
        cell, pg = m.group(1), m.group(2)
        t = xp.read_text(errors="replace")
        ptype = tag(t, "type")
        title = cdata(tag(t, "title"))
        svg = svg_name(book_dir, xp.stem)
        if not svg:
            continue
        conns = []
        for cm in re.finditer(r"<conn>(.*?)</conn>", t, re.S | re.I):
            c = cm.group(1)
            name = cdata(tag(c, "name"))
            if not name:
                continue
            fv, pins = resolve_face_svg(book_dir, tag(c, "face_view"))
            lv = svg_name(book_dir, tag(c, "loc_view"))
            k = {"n": name, "loc": cdata(tag(c, "loc")),
                 "z": tag(c, "zone"), "face": fv, "locv": lv}
            if pins:
                k["pins"] = pins
            conns.append(k)
        key = cell + pg
        pages[key] = {
            "cell": cell,
            "page": pg,
            "type": ptype,
            "title": title,
            "svg": svg,
            "conns": conns,
        }
        for jskey, kind in (("gnds", "ground"), ("spl", "splice"), ("fuses", "fuse")):
            items = parse_items(t, kind, book_dir)
            if items:
                pages[key][jskey] = items
        # every cell joins the TOC: the original section list included the
        # TXT cells (Introduction, Symbols, Component Testing, ...) and the
        # LOC cell (Component Location Views), and workshop manuals link
        # into them by cell number (e.g. _wire.html#149).
        d = toc.setdefault(cell, {"title": title, "pages": []})
        if title and not d["title"]:
            d["title"] = title
        d["pages"].append(key)
    if not toc:
        # no XML page model: 2004-vintage books are PDF pages + .mdb nav
        return build_pdf_book(book_dir, book)
    # pages with no XML companion: several books ship whole cells SVG-only
    # (Component Testing, location views, intro/repair text pages)
    for sp in book_dir.iterdir():
        if sp.suffix.lower() != ".svg":
            continue
        m = re.match(rf"{re.escape(book)}(\d{{3}})(\d{{3}})$", sp.stem, re.I)
        if not m:
            continue
        key = m.group(1) + m.group(2)
        if key in pages:
            continue
        pages[key] = {"cell": m.group(1), "page": m.group(2), "type": "",
                      "title": "", "svg": sp.name, "conns": []}
        toc.setdefault(m.group(1), {"title": "", "pages": []})["pages"].append(key)
    for cell in toc:
        toc[cell]["pages"].sort()
        if not toc[cell]["title"]:
            toc[cell]["title"] = STD_CELLS.get(cell, "")
    entities, warnings = build_xml_index(book_dir, book, pages)
    data = {"book": book, "toc": toc, "pages": pages, "entities": entities}
    if warnings:
        data["warnings"] = warnings
    (book_dir / "wiring_data.js").write_text("window.WIRING=" + json.dumps(data, separators=(",", ":")) + ";")
    write_viewer(book_dir, book, VIEWER_HTML)
    return {"cells": len(toc), "pages": len(pages), "entities": len(entities),
            "warnings": len(warnings)}


# ---------------------------------------------------------------------------
# 2004-vintage PDF books (E4*): one PDF per page, navigation in a Jet 3 .mdb
# (tables CELLS, CONN/CONNREF, grnd/grndref, splice/splcref, Fuse/Fuseref,
# Comp/Compref, Pageref). CELLS FILENAME references the pre-conversion .TIF
# names; the decoded books carry the same stems as .pdf.
# ---------------------------------------------------------------------------

def _pdf_for(book_dir: Path, filename: str, names: dict[str, str]) -> str:
    """CELLS FILENAME (x.TIF / x.pdf) -> actual on-disk pdf name, or ''.
    names: lowercase name -> real name (exists() alone would false-match
    case on Windows and emit links that 404 on case-sensitive hosts)."""
    fn = (filename or "").strip()
    if not fn:
        return ""
    stem = fn.rsplit(".", 1)[0].lower()
    return names.get(fn.lower(), "") or names.get(stem + ".pdf", "")


def _item_index(db: JetDb, table: str) -> dict:
    """CONN/grnd/splice/Fuse/Comp -> {NAME: [[qual, loc, zone, locpage], ...]}."""
    idx: dict[str, list] = {}
    try:
        rows = db.read_table(table)
    except KeyError:
        return idx
    for r in rows:
        get = lambda *ks: next((str(r[k]).strip() for k in ks
                                if r.get(k) not in (None, "")), "")
        name = get("NAME", "Name")
        if not name:
            continue
        ent = [get("QUALIFIER", "Qualifier"), get("LOCATION", "Location"),
               get("ZONE", "zone"), get("LOCPAGE", "Locpage")]
        if ent not in idx.setdefault(name, []):
            idx[name].append(ent)
    return idx


def _page_refs(db: JetDb, table: str) -> dict:
    """CONNREF/... -> {cell+page: [name or [name, qual], ...]}."""
    out: dict[str, list] = {}
    try:
        rows = db.read_table(table)
    except KeyError:
        return out
    for r in rows:
        cell = str(r.get("CELL") or "").strip().zfill(3)
        page = str(r.get("PAGE") or "").strip().zfill(3)
        name = str(r.get("NAME") or r.get("Name") or "").strip()
        if not name or not cell.strip("0") or not page.strip("0"):
            continue
        qual = str(r.get("QUALIFIER") or "").strip()
        ent = [name, qual] if qual else name
        lst = out.setdefault(cell + page, [])
        if ent not in lst:
            lst.append(ent)
    return out


def build_pdf_book(book_dir: Path, book: str) -> dict | None:
    mdb = next((p for p in book_dir.iterdir()
                if p.suffix.lower() == ".mdb"), None)
    if not mdb:
        return None
    try:
        db = JetDb(mdb)
        cells = db.read_table("CELLS")
    except (ValueError, KeyError, OSError):
        return None

    names = {p.name.lower(): p.name for p in book_dir.iterdir()}
    pages: dict[str, dict] = {}
    toc: dict[str, dict] = {}
    for r in sorted(cells, key=lambda r: (str(r.get("CELL") or ""),
                                          str(r.get("PAGE") or ""))):
        cell = str(r.get("CELL") or "").strip()
        pg = str(r.get("PAGE") or "").strip()
        if not cell or not pg:
            continue
        key = cell + pg
        pdf = _pdf_for(book_dir, r.get("FILENAME") or "", names)
        subt = str(r.get("SUBTITLE") or "").strip()
        if key in pages:
            # extra row for the same page slot: another face/index subtitle,
            # or a qualifier variant pointing at a different file
            if subt and subt not in pages[key].setdefault("subt", []):
                pages[key]["subt"].append(subt)
            if pdf and not pages[key]["pdf"]:
                pages[key]["pdf"] = pdf
            continue
        pages[key] = {
            "cell": cell, "page": pg,
            "type": str(r.get("CELLTYPE") or "").strip(),
            "title": str(r.get("TITLE") or "").strip(),
            "pdf": pdf,
        }
        if subt:
            pages[key]["subt"] = [subt]
        d = toc.setdefault(cell, {"title": pages[key]["title"], "pages": []})
        if pages[key]["title"] and not d["title"]:
            d["title"] = pages[key]["title"]
        d["pages"].append(key)
    if not toc:
        return None
    for cell in toc:
        toc[cell]["pages"].sort()

    items = {k: _item_index(db, t) for k, t in
             (("conns", "CONN"), ("gnds", "grnd"), ("spl", "splice"),
              ("fuses", "Fuse"), ("comps", "Comp"))}
    refs = {k: _page_refs(db, t) for k, t in
            (("conns", "CONNREF"), ("gnds", "grndref"), ("spl", "splcref"),
             ("fuses", "Fuseref"), ("comps", "Compref"))}
    for kind, per_page in refs.items():
        for key, lst in per_page.items():
            if key in pages:
                pages[key].setdefault(kind, []).extend(lst)
    # connector face pages: cell 150 subtitles name the connectors drawn there
    faces = {}
    for key, p in pages.items():
        if p["cell"] == "150":
            for s in p.get("subt", []):
                faces.setdefault(s.upper(), key)
    # in-diagram page references with their labels
    try:
        for r in db.read_table("Pageref"):
            key = (str(r.get("CELL") or "").strip() +
                   str(r.get("PAGE") or "").strip())
            tgt = (str(r.get("REFCELL") or "").strip() +
                   str(r.get("REFPAGE") or "").strip())
            name = str(r.get("NAME") or "").strip()
            if key in pages and len(tgt) == 6:
                lst = pages[key].setdefault("refs", [])
                if [name, tgt] not in lst:
                    lst.append([name, tgt])
    except KeyError:
        pass

    entities, warnings = build_mdb_index(db, pages)
    # Many 2004 books leave CONN_CELL/CONN_PAGE blank but explicitly list the
    # connector names in cell-150 CELLS subtitles. Preserve that original
    # face-page mapping in the canonical entity model as a source fallback.
    for name, page in sorted(faces.items()):
        key = entity_key("connector", name)
        if key not in entities:
            entities[key] = {"type": "connector", "name": name}
        entities[key].setdefault("face_page", page)
        entities[key].setdefault("face_available", page in pages)
        if page in pages:
            membership = pages[page].setdefault("entities", {}).setdefault("connector", [])
            if key not in membership:
                membership.append(key)
                membership.sort()
    data = {"book": book, "mode": "pdf", "toc": toc, "pages": pages,
            "items": items, "faces": faces, "entities": entities}
    if warnings:
        data["warnings"] = warnings
    (book_dir / "wiring_data.js").write_text(
        "window.WIRING=" + json.dumps(data, separators=(",", ":")) + ";")
    write_viewer(book_dir, book, VIEWER_PDF_HTML)
    return {"cells": len(toc), "pages": len(pages), "entities": len(entities),
            "warnings": len(warnings)}


VIEWER_HTML = r"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Wiring Viewer - {BOOK}</title>
<style>
 html,body{margin:0;height:100%;font-family:Verdana,Arial,sans-serif;font-size:13px}
 #app{display:flex;height:100vh}
 #toc{width:260px;background:#e9eef0;border-right:1px solid #bbb;overflow:auto;padding:8px}
 #toc h2{font-size:14px;color:#900;margin:6px 4px}
 .cell{display:block;padding:5px 8px;border-radius:4px;color:#0645ad;cursor:pointer;text-decoration:none}
 .cell:hover{background:#94d6e7}.cell b{color:#333}
 #stage{flex:1;display:flex;flex-direction:column;min-width:0}
 #bar{background:#900;color:#fff;padding:6px 10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 #bar button,#bar select{font-size:12px}
 #bar .t{font-weight:bold}
 #view{flex:1;position:relative;overflow:hidden;background:#fff}
 #pan{position:absolute;left:0;top:0;transform-origin:0 0}
	 #pan object{display:block;width:1100px;height:850px;border:0;pointer-events:auto}
 #side{width:280px;border-left:1px solid #bbb;overflow:auto;padding:8px;background:#f7fbfd}
 #side h3{font-size:13px;margin:6px 2px}
 .conn{padding:4px 6px;border-bottom:1px solid #e2e2e2;cursor:pointer}
 .conn:hover{background:#eef6fb}.conn b{color:#036}
 .conn small{color:#666}
 #chooser{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:10}
	 #chooserbox{position:absolute;left:50%;top:40%;transform:translate(-50%,-50%);background:#fff;border:1px solid #666;border-radius:6px;box-shadow:0 4px 18px rgba(0,0,0,.4);min-width:240px}
	 #chooserbox .mh{background:#036;color:#fff;padding:5px 10px;display:flex;justify-content:space-between;gap:14px;border-radius:5px 5px 0 0}
	 #chooserbtns{display:flex;flex-direction:column;gap:6px;padding:12px 14px}
	 #chooserbtns button{font-size:13px;padding:6px}
 #modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9}
	 #modalbox{position:absolute;inset:5%;background:#fff;border-radius:6px;display:flex;flex-direction:column;min-height:0}
	 #modalbox .mh{background:#036;color:#fff;padding:6px 10px;display:flex;justify-content:space-between}
	 #mdetail{padding:8px 10px;border-bottom:1px solid #ddd;background:#f7fbfd}
	 #tabs{display:flex;gap:4px;padding:6px 10px;border-bottom:1px solid #ddd}
	 #tabs button{font-size:12px}
	 #mviews{flex:1;display:flex;min-height:0}
	 #modalbox object{flex:1;border:0;min-width:0}
	 #facewrap{display:flex;flex-direction:column;min-width:0;overflow:auto}
	 #facewrap object{flex:0 0 auto;height:55%;min-height:200px}
	 #pintbl{padding:4px 10px 12px}
	 #pintbl table{border-collapse:collapse;font-size:12px;width:100%}
	 #pintbl th{background:#c9c9c9;text-align:left;padding:2px 8px;border:1px solid #aaa}
	 #pintbl td{padding:2px 8px;border:1px solid #ccc;vertical-align:top}
	 #pintbl tr.nu td{background:#e4e4e4;color:#555}
	 a{color:#0645ad}
</style></head><body>
<div id='app'>
 <div id='toc'><h2>Contents</h2><div id='toclist'></div></div>
 <div id='stage'>
   <div id='bar'>
     <a href='../../wiring.html' style='color:#fff'>&#8592; Books</a>
     <span class='t' id='title'>Select a diagram</span>
     <span style='flex:1'></span>
     <button onclick='pg(-1)'>&#8592; Page</button>
     <span id='pgnum'></span>
     <button onclick='pg(1)'>Page &#8594;</button>
     <button onclick='zoom(1.25)'>+</button><button onclick='zoom(0.8)'>&#8722;</button>
     <button onclick='fit()'>Fit</button>
   </div>
   <div id='view'><div id='pan'><object id='svg' type='image/svg+xml'></object></div></div>
 </div>
 <div id='side'><h3>On this page</h3><div id='conns'></div></div>
</div>
	<div id='chooser' onclick='if(event.target.id=="chooser")closeChooser()'>
	  <div id='chooserbox'><div class='mh'><span id='ctitle'></span><span onclick='closeChooser()' style='cursor:pointer'>&#10006;</span></div>
	  <div id='chooserbtns'></div></div>
	</div>
	<div id='modal' onclick='if(event.target.id=="modal")closeM()'>
	  <div id='modalbox'><div class='mh'><span id='mtitle'></span><span onclick='closeM()' style='cursor:pointer'>&#10006; close</span></div>
	  <div id='mdetail'></div><div id='tabs'></div><div id='mviews'>
	  <object id='mobj' type='image/svg+xml'></object>
	  <div id='facewrap'><object id='mface' type='image/svg+xml'></object><div id='pintbl'></div></div></div></div>
	</div>
<script src='wiring_data.js'></script>
<script>
	var W=window.WIRING, cur=null, ci=0, sc=1, ox=0, oy=0, dragging=false, sx=0, sy=0, moved=false, downX=0, downY=0;
function toclist(){
 var cells=Object.keys(W.toc).sort(), h='';
 cells.forEach(function(c){var e=W.toc[c];
   h+="<a class='cell' onclick='openCell(\""+c+"\")'><b>"+(+c)+"</b> &nbsp;"+(e.title||"")+"</a>";});
 document.getElementById('toclist').innerHTML=h;
}
function openCell(c){cur=W.toc[c];ci=0;showPage();}
	function showPage(){if(!cur)return;renderPage(cur.pages[ci]);}
	function openKey(key){
	 var p=W.pages[key]; if(!p)return false;
	 cur=W.toc[p.cell]||null; ci=cur?Math.max(0,cur.pages.indexOf(key)):0;
	 renderPage(key); return true;
	}
	function renderPage(key){
	 var p=W.pages[key]; if(!p)return;
	 document.getElementById('title').textContent=(+p.cell)+"-"+(+p.page)+"  "+p.title;
	 var inToc=cur&&cur.pages.indexOf(key)>=0;
	 document.getElementById('pgnum').textContent=inToc?(ci+1)+" / "+cur.pages.length:(+p.cell)+"-"+(+p.page);
	 var obj=document.getElementById('svg'); obj.onload=hookSvg; obj.data=p.svg;
	 fit();
	 var h=grp("Connectors","conns",p.conns)+grp("Grounds","gnds",p.gnds)
	      +grp("Splices","spl",p.spl)+grp("Fuses","fuses",p.fuses);
	 document.getElementById('conns').innerHTML=h||"<small>none listed</small>";
	 window._cp=p;
	}
	function grp(title,kind,list){if(!list||!list.length)return"";
	 var h="<h3>"+title+"</h3>";
	 list.forEach(function(k,i){
	   h+="<div class='conn' onclick='showItem(\""+kind+"\","+i+")'><b>"+esc(k.n)+"</b> <small>"+esc(k.z||"")+"</small><br><small>"+esc(k.loc||"")+"</small></div>";});
	 return h;
	}
	function pg(d){if(!cur)return;ci=Math.max(0,Math.min(cur.pages.length-1,ci+d));showPage();}
	function esc(s){return String(s||"").replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];});}
	function showItem(kind,i){var p=window._cp||{},k=(p[kind]||[])[i];
	 if(!k)return;
	 if(kind=="conns")return showConnK(k);
	 if(!k.locv){alert(k.n+"\n"+(k.loc||""));return;}
	 window._conn=k; openConnView("loc");
	}
	function showConn(i){var k=((window._cp||{}).conns||[])[i]; if(k)showConnK(k);}
	function showConnK(k){
	 if(!k.locv&&!k.face){alert(k.n+"\n"+(k.loc||""));return;}
	 window._conn=k;
	 document.getElementById('ctitle').textContent=k.n;
	 var b='';
	 if(k.face)b+="<button onclick='openConnView(\"face\")'>Connector Face</button>";
	 if(k.locv)b+="<button onclick='openConnView(\"loc\")'>Location</button>";
	 if(k.face&&k.locv)b+="<button onclick='openConnView(\"both\")'>Both</button>";
	 document.getElementById('chooserbtns').innerHTML=b;
	 document.getElementById('chooser').style.display='block';
	}
	function closeChooser(){document.getElementById('chooser').style.display='none';}
	function openConnView(mode){var k=window._conn; if(!k)return;
	 closeChooser();
	 document.getElementById('mtitle').textContent=k.n;
	 document.getElementById('mdetail').innerHTML="<b>Location:</b> "+esc(k.loc||"")+" &nbsp; <small>"+esc(k.z||"")+"</small>";
	 var tabs='',both=k.locv&&k.face;
	 if(k.locv)tabs+="<button onclick='connMode(\"loc\")'>Location</button>";
	 if(k.face)tabs+="<button onclick='connMode(\"face\")'>Connector Face</button>";
	 if(both)tabs+="<button onclick='connMode(\"both\")'>Both</button>";
	 document.getElementById('tabs').innerHTML=tabs;
	 document.getElementById('modal').style.display='block';
	 connMode(mode);
	}
	function connMode(mode){var k=window._conn||{},loc=document.getElementById('mobj'),face=document.getElementById('mface'),wrap=document.getElementById('facewrap');
	 var showLoc=(mode=="loc"||mode=="both")&&k.locv,showFace=(mode=="face"||mode=="both")&&k.face;
	 loc.style.display=showLoc?"block":"none";wrap.style.display=showFace?"flex":"none";
	 loc.data=showLoc?k.locv:"";face.data=showFace?k.face:"";
	 loc.style.flex=wrap.style.flex=(showLoc&&showFace)?"1 1 50%":"1 1 100%";
	 face.style.height=(k.pins&&k.pins.length)?"55%":"100%"; // face SVGs without cf-xml pins draw their own table
	 document.getElementById('pintbl').innerHTML=showFace?pinTable(k.pins):"";
	}
	function pinTable(pins){if(!pins||!pins.length)return"";
	 var h="<table><tr><th>Pin</th><th>Circuit</th><th>Gauge</th><th>Circuit Function</th></tr>";
	 pins.forEach(function(p){var used=p[4]!="0";
	   h+=used?"<tr><td>"+esc(p[0])+"</td><td>"+esc(p[1])+"</td><td>"+esc(p[2])+"</td><td>"+esc(p[3])+"</td></tr>"
	          :"<tr class='nu'><td>"+esc(p[0])+"</td><td>*</td><td>*</td><td>not used</td></tr>";});
	 return h+"</table>";
	}
	function closeM(){document.getElementById('modal').style.display='none';document.getElementById('mobj').data='';document.getElementById('mface').data='';}
	// hotspot id grammar varies by model year:
	//   CONN_C352_H | GROUND_G301 | GROUND_G104~DATA~... | SPLICE_S326 |
	//   SPLICE_S251~INDEX~... | FUSE_F37_Sheet_2 | FUSE_F2.7_COMP_CJB~DATA~... |
	//   FUSE_COMP_SJB~INDEX~F50 SMART JUNCTION BOX | ITEM_C931_ALL | ITEM_14401,14290_TEXT
	function itemName(rest){
	 // name sits before the ~ (GROUND_G104~DATA~..., SPLICE_S251~INDEX~S251)
	 // except FUSE_COMP_SJB~INDEX~F50 ... where only the ~INDEX~ side has it
	 var t=(rest.indexOf("COMP")==0&&rest.indexOf("~INDEX~")>=0)?rest.split("~INDEX~")[1]:rest.split("~")[0];
	 t=t.replace(/_(ALL|TEXT|BACKPAD|ARROW)$/,"").replace(/_Sheet_\d+$/i,"");
	 t=t.replace(/_COMP_.*$/,"");
	 return t.split(" ")[0];
	}
	function findItem(name){var w=String(name||"").toUpperCase();if(!w)return null;
	 var kinds=["conns","gnds","spl","fuses"],i,j,l,first=null;
	 var scan=function(p){if(!p)return null;
	   for(j=0;j<kinds.length;j++){l=p[kinds[j]]||[];
	     for(i=0;i<l.length;i++)if(String(l[i].n).toUpperCase()==w){
	       var h={k:l[i],kind:kinds[j]};
	       if(l[i].locv||l[i].face)return h;    // prefer an entry with a view
	       if(!first)first=h;
	     }}
	   return null;};
	 var hit=scan(window._cp); if(hit)return hit;
	 var keys=Object.keys(W.pages);
	 for(var x=0;x<keys.length;x++){hit=scan(W.pages[keys[x]]);if(hit)return hit;}
	 return first;
	}
	function openItemByName(name){
	 var parts=String(name||"").split(",");
	 for(var i=0;i<parts.length;i++){var hit=findItem(parts[i]);
	   if(hit){
	     if(hit.kind=="conns")showConnK(hit.k);
	     else if(hit.k.locv){window._conn=hit.k;openConnView("loc");}
	     else alert(hit.k.n+"\n"+(hit.k.loc||""));
	     return true;}}
	 alert(name); return false;
	}
	function openRef(ref){var m=String(ref).match(/^(\d{1,3})-(\d{1,3})$/);if(!m)return false;
	 var key=("000"+m[1]).slice(-3)+("000"+m[2]).slice(-3);
	 if(W.pages[key]){closeM();return openKey(key);}
	 alert("Page "+ref+" is not available in this book.");return false;
	}
	function openHotspot(id){
	 if(id.indexOf("CONN_")==0){var m=id.match(/^CONN_(.+)_[^_]+$/);return m?openItemByName(m[1]):false;}
	 if(id.indexOf("PAGEREF_")==0)return openRef(id.substring(8));
	 var m2=id.match(/^(GROUND|SPLICE|FUSE|ITEM)_(.+)$/);
	 return m2?openItemByName(itemName(m2[2])):false;
	}
	function svgClick(e){if(moved)return;var n=e.target;
	 while(n&&n.id!==undefined){var id=String(n.id||"");
	   if(/^(?:CONN_|PAGEREF_|GROUND_|SPLICE_|FUSE_|ITEM_)/.test(id)){e.preventDefault();openHotspot(id);return;}
	   n=n.parentNode;
	 }
	}
	var HOT='[id^="CONN_"],[id^="PAGEREF_"],[id^="GROUND_"],[id^="SPLICE_"],[id^="FUSE_"],[id^="ITEM_"]';
	function hookSvg(){var obj=document.getElementById('svg'),doc=obj.contentDocument;if(!doc)return;
	 try{doc.addEventListener('click',svgClick);doc.addEventListener('mousedown',startDrag);doc.addEventListener('mousemove',moveDrag);doc.addEventListener('mouseup',stopDrag);doc.addEventListener('wheel',wheelZoom,{passive:false});
	   doc.documentElement.setAttribute('data-tso-wiring-direct','1');
	   var a=doc.querySelectorAll(HOT);for(var i=0;i<a.length;i++)a[i].style.cursor='pointer';
	 }catch(e){}
	}
	function hookModal(){var obj=document.getElementById('mobj'),doc=obj.contentDocument;if(!doc)return;
	 try{doc.addEventListener('mousedown',function(){moved=false;});
	   doc.addEventListener('click',svgClick);
	   doc.documentElement.setAttribute('data-tso-wiring-direct','1');
	   var a=doc.querySelectorAll(HOT);for(var i=0;i<a.length;i++)a[i].style.cursor='pointer';
	 }catch(e){}
	}
	window.addEventListener('message',function(e){var d=e.data;
	 if(!d)return;
	 if(d.type==='tso-wiring-wheel-v1'){
	   if(typeof d.deltaY==='number'&&isFinite(d.deltaY)&&d.deltaY!==0)zoom(d.deltaY<0?1.1:0.9);
	   return;
	 }
	 if(d.type!=='tso-wiring-hotspot-v1'||typeof d.id!=='string'||d.id.length>512)return;
	 if(!/^(?:COMP_|CONN_|PAGEREF_|GROUND_|SPLICE_|FUSE_|ITEM_)/.test(d.id))return;
	 if(d.id.indexOf('PAGEREF_')!==0&&window.WiringIndexUI&&WiringIndexUI.hotspotKey){
	   var key=WiringIndexUI.hotspotKey(d.id);if(key&&WiringIndexUI.select(key))return;
	 }
	 openHotspot(d.id);
	});
	function apply(){document.getElementById('pan').style.transform='translate('+ox+'px,'+oy+'px) scale('+sc+')';}
	function zoom(f){sc*=f;apply();}
	function fit(){var v=document.getElementById('view');sc=Math.min(v.clientWidth/1100,v.clientHeight/850)||1;ox=(v.clientWidth-1100*sc)/2;oy=10;apply();}
	// drag to pan
	function startDrag(e){dragging=true;downX=e.clientX;downY=e.clientY;sx=e.clientX-ox;sy=e.clientY-oy;moved=false;}
	function moveDrag(e){if(dragging){if(Math.abs(e.clientX-downX)+Math.abs(e.clientY-downY)>3)moved=true;ox=e.clientX-sx;oy=e.clientY-sy;apply();}}
	function stopDrag(){dragging=false;}
	function wheelZoom(e){e.preventDefault();zoom(e.deltaY<0?1.1:0.9);}
	(function(){var v=document.getElementById('view');
	 v.addEventListener('mousedown',startDrag);
	 window.addEventListener('mouseup',stopDrag);
	 window.addEventListener('mousemove',moveDrag);
	 v.addEventListener('wheel',wheelZoom,{passive:false});
	 document.getElementById('mobj').onload=hookModal; // ITEM_ hotspots in location views
	})();
toclist();
function fromHash(){var h=(location.hash||'').replace('#','');
 if(h){ // match by numeric cell (padded)
   var cells=Object.keys(W.toc); var pad=('000'+h).slice(-3);
   var hit=cells.indexOf(pad)>=0?pad:(cells.indexOf(h)>=0?h:null);
	   if(hit){openCell(hit);return true;}
	   var key=h.replace(/\D/g,''); if(key.length==6&&W.pages[key])return openKey(key); }
 return false;}
window.addEventListener('hashchange',fromHash);
if(!fromHash()){var first=Object.keys(W.toc).sort()[0]; if(first)openCell(first);}
</script></body></html>"""


VIEWER_PDF_HTML = r"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Wiring Viewer - {BOOK}</title>
<style>
 html,body{margin:0;height:100%;font-family:Verdana,Arial,sans-serif;font-size:13px}
 #app{display:flex;height:100vh}
 #toc{width:260px;background:#e9eef0;border-right:1px solid #bbb;overflow:auto;padding:8px}
 #toc h2{font-size:14px;color:#900;margin:6px 4px}
 .cell{display:block;padding:5px 8px;border-radius:4px;color:#0645ad;cursor:pointer;text-decoration:none}
 .cell:hover{background:#94d6e7}.cell b{color:#333}
 #stage{flex:1;display:flex;flex-direction:column;min-width:0}
 #bar{background:#900;color:#fff;padding:6px 10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 #bar button{font-size:12px}
 #bar .t{font-weight:bold}
 #view{flex:1;position:relative;overflow:auto;background:#fff}
 #view object{width:100%;height:100%;border:0}
 #chart{padding:10px 16px}
 #chart table{border-collapse:collapse;font-size:12px;width:100%}
 #chart th{background:#c9c9c9;text-align:left;padding:2px 8px;border:1px solid #aaa}
 #chart td{padding:2px 8px;border:1px solid #ccc;vertical-align:top}
 #chart h2{font-size:14px;color:#900}
 #chart a{color:#0645ad;cursor:pointer}
 #side{width:280px;border-left:1px solid #bbb;overflow:auto;padding:8px;background:#f7fbfd}
 #side h3{font-size:13px;margin:6px 2px}
 .conn{padding:4px 6px;border-bottom:1px solid #e2e2e2;cursor:pointer}
 .conn:hover{background:#eef6fb}.conn b{color:#036}
 .conn small{color:#666}
 #chooser{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:10}
 #chooserbox{position:absolute;left:50%;top:40%;transform:translate(-50%,-50%);background:#fff;border:1px solid #666;border-radius:6px;box-shadow:0 4px 18px rgba(0,0,0,.4);min-width:300px;max-width:480px;max-height:70%;overflow:auto}
 #chooserbox .mh{background:#036;color:#fff;padding:5px 10px;display:flex;justify-content:space-between;gap:14px;border-radius:5px 5px 0 0}
 #chooserbody{padding:10px 14px}
 #chooserbody .ent{border-bottom:1px solid #ddd;padding:6px 0}
 #chooserbody button{font-size:12px;margin:4px 6px 0 0}
 a{color:#0645ad}
</style></head><body>
<div id='app'>
 <div id='toc'><h2>Contents</h2><div id='toclist'></div></div>
 <div id='stage'>
   <div id='bar'>
     <a href='../../wiring.html' style='color:#fff'>&#8592; Books</a>
     <span class='t' id='title'>Select a diagram</span>
     <span style='flex:1'></span>
     <button onclick='pg(-1)'>&#8592; Page</button>
     <span id='pgnum'></span>
     <button onclick='pg(1)'>Page &#8594;</button>
   </div>
   <div id='view'></div>
 </div>
 <div id='side'><h3>On this page</h3><div id='conns'></div></div>
</div>
<div id='chooser' onclick='if(event.target.id=="chooser")closeChooser()'>
  <div id='chooserbox'><div class='mh'><span id='ctitle'></span><span onclick='closeChooser()' style='cursor:pointer'>&#10006;</span></div>
  <div id='chooserbody'></div></div>
</div>
<script src='wiring_data.js'></script>
<script>
var W=window.WIRING, cur=null, ci=0;
var KINDS=[["conns","Connectors"],["gnds","Grounds"],["spl","Splices"],["fuses","Fuses"],["comps","Components"]];
function esc(s){return String(s||"").replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];});}
function toclist(){
 var cells=Object.keys(W.toc).sort(), h='';
 cells.forEach(function(c){var e=W.toc[c];
   h+="<a class='cell' onclick='openCell(\""+c+"\")'><b>"+(+c)+"</b> &nbsp;"+esc(e.title||"")+"</a>";});
 document.getElementById('toclist').innerHTML=h;
}
function openCell(c){cur=W.toc[c];ci=0;showPage();}
function showPage(){if(!cur)return;renderPage(cur.pages[ci]);}
function openKey(key){
 var p=W.pages[key]; if(!p)return false;
 cur=W.toc[p.cell]||null; ci=cur?Math.max(0,cur.pages.indexOf(key)):0;
 renderPage(key); return true;
}
function renderPage(key){
 var p=W.pages[key]; if(!p)return;
 var t=(+p.cell)+"-"+(+p.page)+"  "+p.title;
 if(p.subt&&p.subt.length==1)t+=" ("+p.subt[0]+")";
 document.getElementById('title').textContent=t;
 var inToc=cur&&cur.pages.indexOf(key)>=0;
 document.getElementById('pgnum').textContent=inToc?(ci+1)+" / "+cur.pages.length:(+p.cell)+"-"+(+p.page);
 var v=document.getElementById('view');
 if(p.pdf){v.innerHTML="";var o=document.createElement('object');o.type='application/pdf';
  var fb=document.createElement('p');fb.style.padding='16px';
  fb.appendChild(document.createTextNode('Cannot display PDF inline. '));
  var a=document.createElement('a');a.href=p.pdf;a.textContent='Open '+p.pdf;
  fb.appendChild(a);o.appendChild(fb);o.data=p.pdf;v.appendChild(o);}
 else{v.innerHTML="<div id='chart'>"+chartHtml(p)+"</div>";}
 var h='';
 KINDS.forEach(function(kd){h+=grp(kd[1],kd[0],p[kd[0]]);});
	 if(p.type=="CON"&&p.subt&&p.subt.length){
	  h+="<h3>Views on this page</h3>";
	  p.subt.forEach(function(s){h+="<div class='conn' data-name=\""+esc(s)+"\" onclick='showItem(\"conns\",this.dataset.name)'><b>"+esc(s)+"</b></div>";});
	 }
 if(p.refs&&p.refs.length){
  h+="<h3>Page references</h3>";
  p.refs.forEach(function(r){h+="<div class='conn' onclick='openKey(\""+r[1]+"\")'><b>"+esc(r[0])+"</b></div>";});
 }
 document.getElementById('conns').innerHTML=h||"<small>none listed</small>";
 window._cp=p;
}
	function grp(title,kind,list){if(!list||!list.length)return"";
	 var h="<h3>"+title+"</h3>";
	 list.forEach(function(k){var n=typeof k=="string"?k:k[0],q=typeof k=="string"?"":k[1];
	   h+="<div class='conn' data-name=\""+esc(n)+"\" onclick='showItem(\""+kind+"\",this.dataset.name)'><b>"+esc(n)+"</b> <small>"+esc(q)+"</small></div>";});
	 return h;
	}
function pg(d){if(!cur)return;ci=Math.max(0,Math.min(cur.pages.length-1,ci+d));showPage();}
function locKey(lp){if(!lp)return"";var key="151"+("000"+lp).slice(-3);return W.pages[key]?key:"";}
function showItem(kind,name){
 var idx=(W.items[kind]||{}),ents=idx[name]||idx[String(name).toUpperCase()]||[];
 var face=kind=="conns"?W.faces[String(name).toUpperCase()]:"";
 document.getElementById('ctitle').textContent=name;
 var h='';
 if(!ents.length&&!face)h="<div class='ent'>No location data.</div>";
 ents.forEach(function(e){ // [qual, loc, zone, locpage]
   h+="<div class='ent'>"+(e[0]?"<b>"+esc(e[0])+"</b><br>":"")+esc(e[1]||"")+(e[2]?" <small>(zone "+esc(e[2])+")</small>":"");
   var lk=locKey(e[3]);
   if(lk)h+="<br><button onclick='go(\""+lk+"\")'>Location view 151-"+(+e[3])+"</button>";
   h+="</div>";});
 if(face)h+="<div class='ent'><button onclick='go(\""+face+"\")'>Connector face "+(+face.substring(0,3))+"-"+(+face.substring(3))+"</button></div>";
 document.getElementById('chooserbody').innerHTML=h;
 document.getElementById('chooser').style.display='block';
}
function go(key){closeChooser();openKey(key);}
function closeChooser(){document.getElementById('chooser').style.display='none';}
function chartHtml(p){ // IDX pages (Component Location Charts) have no file:
 // the original app generated them from the database; rebuild them here.
 var subs=p.subt||[],h='';
 var map={"Connectors":"conns","Ground points":"gnds","Splices":"spl","Fuses":"fuses","Components":"comps"};
 subs.forEach(function(s){var kind=map[s]||"",idx=W.items[kind]||{};
  var names=Object.keys(idx).sort();
  if(!names.length)return;
  h+="<h2>"+esc(s)+"</h2><table><tr><th>Name</th><th>Qualifier</th><th>Location</th><th>Zone</th><th>Page</th></tr>";
  names.forEach(function(n){(idx[n]||[]).forEach(function(e){
    var lk=locKey(e[3]);
    h+="<tr><td>"+esc(n)+"</td><td>"+esc(e[0])+"</td><td>"+esc(e[1])+"</td><td>"+esc(e[2])+"</td><td>"+
       (lk?"<a onclick='openKey(\""+lk+"\")'>151-"+(+e[3])+"</a>":"")+"</td></tr>";});});
  h+="</table>";});
 return h||"<p>No chart data.</p>";
}
toclist();
function fromHash(){var h=(location.hash||'').replace('#','');
 if(h){var cells=Object.keys(W.toc); var pad=('000'+h).slice(-3);
   var hit=cells.indexOf(pad)>=0?pad:(cells.indexOf(h)>=0?h:null);
   if(hit){openCell(hit);return true;}
   var key=h.replace(/\D/g,''); if(key.length==6&&W.pages[key])return openKey(key); }
 return false;}
window.addEventListener('hashchange',fromHash);
if(!fromHash()){var first=Object.keys(W.toc).sort()[0]; if(first)openCell(first);}
</script></body></html>"""


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Build static wiring viewers for EVTM books.")
    parser.add_argument(
        "book_dirs",
        nargs="*",
        help="Optional EVTM book directories, e.g. vol_07_08/content/E7B. Defaults to all wiring books.",
    )
    ns = parser.parse_args(argv)

    if ns.book_dirs:
        total = 0
        for arg in ns.book_dirs:
            d = Path(arg)
            if not d.is_dir():
                print(f"skip missing book dir: {d}", flush=True)
                continue
            res = build_book(d, d.name)
            if res:
                total += 1
                suffix = f", {res.get('entities', 0)} entities"
                if res.get("warnings"):
                    suffix += f", {res['warnings']} warnings"
                print(f"  {d}: {res['cells']} cells, {res['pages']} pages{suffix}", flush=True)
        print(f"built wiring viewer for {total} books", flush=True)
        return

    root = Path(".")
    total = 0
    for vol in discover_volumes(root):
        cat_path = root / vol / "catalog.json"
        if not cat_path.exists():
            continue
        cat = json.load(open(cat_path))
        for r in cat:
            if r["category"] != "wiring":
                continue
            d = root / vol / "content" / r["archive"]
            if not d.exists():
                continue
            res = build_book(d, r["archive"])
            if res:
                total += 1
                suffix = f", {res.get('entities', 0)} entities"
                if res.get("warnings"):
                    suffix += f", {res['warnings']} warnings"
                print(f"  {vol}/{r['archive']}: {res['cells']} cells, {res['pages']} pages{suffix}", flush=True)
    print(f"built wiring viewer for {total} books", flush=True)


if __name__ == "__main__":
    main()
