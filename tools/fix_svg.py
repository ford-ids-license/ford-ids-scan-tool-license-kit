#!/usr/bin/env python3
"""
Make decoded legacy SVGs render in modern browsers.

Some archived SVGs omit the default SVG namespace, so modern browsers show the
XML source instead of drawing. They also contain text transforms like
rotate(-), which older viewers accepted as a no-op but modern SVG renderers
reject, hiding connector/terminal text. Some converted diagrams also reference
embedded SVG fonts (`font1`, `font2`, ...) which modern browsers no longer
render, and declare font-size as a bare CSS number (e.g.
`font-size:7.0000;`) -- valid for SVG presentation attributes but invalid CSS,
so browsers discard it and fall back to a much larger default size while every
glyph's x/y position stays baked in for the tiny intended size, badly
overlapping the artwork. Fix all of these:
  - ensure the root <svg> carries xmlns="http://www.w3.org/2000/svg"
  - normalize rotate(-) to rotate(0)
  - rewrite SVG-font CSS/attributes to browser fonts so text labels render
  - append a px unit to unitless CSS font-size declarations
  - color semantic wire runs red and clickable labels blue
  - add a file-origin-safe hotspot bridge for browsers that isolate local SVGs

Operates on raw BYTES (no text decoding -> can't corrupt/skip odd-byte files).
Idempotent, and also REPAIRS the malformed `.../2000/svg"<attr>` jam (missing
space between attributes) that an earlier buggy insert produced.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from volumes import discover_volumes  # noqa: E402

NS = b'xmlns="http://www.w3.org/2000/svg"'
ROOT = re.compile(rb"<svg\b", re.I)
# jam: the xmlns value's closing quote glued to the next attribute name
JAM = re.compile(rb'(http://www\.w3\.org/2000/svg")(?=[A-Za-z])')
EMPTY_NEGATIVE_ROTATE = re.compile(rb"rotate\(\s*-\s*\)")
# matterCast tabular text (e.g. connector pin tables) fakes column gaps with
# an empty <tspan dx="N"/> between words instead of a space character. SVG's
# dx/dy only shift *glyphs*, so a tspan with no characters has nothing to
# shift -- Chrome (and other modern renderers) silently drop the advance,
# collapsing the gap and running adjacent columns together. Giving the tspan
# a zero-width-space character lets the dx apply without adding visible ink.
EMPTY_SPACER_TSPAN = re.compile(rb'<tspan(\s+dx="[^"]*")\s*/>')
SVG_FONT_CSS = re.compile(rb"font-family\s*:\s*font[0-9A-Za-z_-]+\s*;", re.I)
SVG_FONT_ATTR = re.compile(rb'(font-family\s*=\s*["\'])font[0-9A-Za-z_-]+(["\'])', re.I)
# matterCast/PDF-converted SVGs declare font-size as a bare number in CSS
# (`.t0{font-size:13.0000;...}`, `style="font-size:20;..."`), valid for SVG
# presentation attributes but invalid CSS -> browsers drop it and fall back
# to the default (~16px) font size while every glyph's x/y stays baked in
# for the tiny intended size, so text overlaps/bleeds into the artwork.
CSS_FONT_SIZE_UNITLESS = re.compile(rb'(font-size\s*:\s*[0-9]+(?:\.[0-9]+)?)(\s*(?:;|["\']))', re.I)
# repairs a jam from an earlier buggy pass: the blue-link colorizer's
# style= lookup used to match the "style=" *inside* "font-style=" and
# appended "fill:blue;" to it (e.g. font-style="italic;fill:blue;"),
# silently breaking the font-style and never actually applying the color.
FONT_STYLE_FILL_JAM = re.compile(rb'font-style=(["\'])([^"\']*?);fill:blue;\1')
OLD_BROWSER_FONT_CSS = re.compile(rb"font-family\s*:\s*Verdana,Arial,sans-serif\s*;", re.I)
OLD_BROWSER_FONT_ATTR = re.compile(rb'(font-family\s*=\s*["\'])Verdana,Arial,sans-serif(["\'])', re.I)
BROWSER_FONT_CSS = b"font-family:'Liberation Sans Narrow','Arial Narrow',Arial,sans-serif;"
BROWSER_FONT_ATTR_VALUE = b"Liberation Sans Narrow,Arial Narrow,Arial,sans-serif"
BROWSER_FONT_ATTR = rb"\1" + BROWSER_FONT_ATTR_VALUE + rb"\2"
BLUE_GROUP = re.compile(
    rb'(<g\b[^>]*\bid=(["\'])(?:CONN_|PAGEREF_|SPLICE_|GROUND_)[^"\']*\2[^>]*>)(.*?)(</g>)',
    re.I | re.S,
)
WIRE_GROUP = re.compile(
    rb'(<g\b[^>]*\bid=(["\'])_[^"\']*\2[^>]*>)(.*?)(</g>)',
    re.I | re.S,
)
BLACK_FILL_OR_STROKE = re.compile(rb'\b(fill|stroke)=(["\'])(?:black|#000000|#000)\2', re.I)
BLACK_STROKE = re.compile(rb'\bstroke=(["\'])(?:black|#000000|#000)\1', re.I)
TEXT_NODE = re.compile(rb"<text\b[^>]*>.*?</text>", re.I | re.S)
TEXT_OR_TSPAN_OPEN = re.compile(rb"<(text|tspan)\b([^>]*)>", re.I)
TSPAN_NODE = re.compile(rb"<tspan\b[^>]*>.*?</tspan>", re.I | re.S)
TSPAN_OPEN = re.compile(rb"<tspan\b([^>]*)>", re.I)
TAG = re.compile(rb"<[^>]+>")
CONNECTOR_LABEL = re.compile(rb"\bC\d{2,5}[A-Za-z]?\b", re.I)
GROUND_OR_SPLICE_LABEL = re.compile(rb"\b[SG]\d{1,5}[A-Za-z]?\b", re.I)
PAGE_REF_LABEL = re.compile(rb"\b\d{1,3}(?:-|\xe2\x80\x93)\d{1,3}\b")
PIN_LABEL = re.compile(rb"^[A-Z]?\d{1,3}[A-Z]?$", re.I)
# negative lookbehind excludes "font-style=" -- \b alone matches the
# hyphen/letter boundary in "font-style=" too, which corrupted that
# attribute (e.g. font-style="italic" -> font-style="italic;fill:blue;")
STYLE_ATTR = re.compile(rb'(?<![A-Za-z0-9_-])style=(["\'])(.*?)\1', re.I | re.S)
FILL_STYLE = re.compile(rb"fill\s*:\s*[^;\"']*;?", re.I)
BLUE_FILL_STYLE = re.compile(rb"\s*fill\s*:\s*blue\s*;?", re.I)
BLUE_FILL = re.compile(rb'(?:\bfill=(["\'])blue\1|fill\s*:\s*blue\b)', re.I)
BAD_SELF_CLOSING_STYLE = re.compile(
    rb'(<(?:text|tspan)\b[^<>]*?)\s*/\s+(style=(?:"[^"]*"|\'[^\']*\'))\s*>',
    re.I,
)
HOTSPOT_ID = re.compile(
    rb'\bid\s*=\s*(["\'])(?:COMP_|CONN_|PAGEREF_|GROUND_|SPLICE_|FUSE_|ITEM_)[^"\']*\1',
    re.I,
)
SVG_CLOSE = re.compile(rb"</svg\s*>", re.I)
HOTSPOT_BRIDGE_MARKER = b'id="tso-wiring-hotspot-bridge"'
HOTSPOT_BRIDGE_NODE = re.compile(
    rb'<script\b[^>]*\bid=(["\'])tso-wiring-hotspot-bridge\1[^>]*>.*?</script>',
    re.I | re.S,
)
HOTSPOT_BRIDGE = rb'''
<script id="tso-wiring-hotspot-bridge" type="application/ecmascript"><![CDATA[
(function(){
  var selector='[id^="COMP_"],[id^="CONN_"],[id^="PAGEREF_"],[id^="GROUND_"],[id^="SPLICE_"],[id^="FUSE_"],[id^="ITEM_"]';
  var downX=0,downY=0,moved=false;
  function direct(){return document.documentElement.getAttribute('data-tso-wiring-direct')==='1';}
  document.addEventListener('mousedown',function(e){downX=e.clientX;downY=e.clientY;moved=false;});
  document.addEventListener('mousemove',function(e){if(Math.abs(e.clientX-downX)+Math.abs(e.clientY-downY)>3)moved=true;});
  document.addEventListener('click',function(e){
    if(moved||direct())return;
    var n=e.target;
    while(n&&n!==document){
      var id=n.getAttribute&&n.getAttribute('id');
      if(id&&/^(?:COMP_|CONN_|PAGEREF_|GROUND_|SPLICE_|FUSE_|ITEM_)/.test(id)){
        e.preventDefault();
        window.parent.postMessage({type:'tso-wiring-hotspot-v1',id:id},'*');
        return;
      }
      n=n.parentNode;
    }
  });
  document.addEventListener('wheel',function(e){
    if(direct())return;
    e.preventDefault();
    window.parent.postMessage({type:'tso-wiring-wheel-v1',deltaY:Number(e.deltaY)||0},'*');
  },{passive:false});
  var nodes=document.querySelectorAll(selector);
  for(var i=0;i<nodes.length;i++)nodes[i].style.cursor='pointer';
})();
]]></script>
'''


def color_fill_and_stroke(fragment: bytes, color: bytes) -> bytes:
    return BLACK_FILL_OR_STROKE.sub(lambda m: m.group(1) + b'="' + color + b'"', fragment)


def color_stroke(fragment: bytes, color: bytes) -> bytes:
    return BLACK_STROKE.sub(lambda _m: b'stroke="' + color + b'"', fragment)


def style_fill(attrs: bytes, color: bytes) -> bytes:
    style = STYLE_ATTR.search(attrs)
    decl = b"fill:" + color + b";"
    if not style:
        return attrs + b' style="' + decl + b'"'

    old = style.group(2)
    if FILL_STYLE.search(old):
        new = FILL_STYLE.sub(decl, old)
    else:
        sep = b"" if not old or old.rstrip().endswith(b";") else b";"
        new = old + sep + decl
    return attrs[:style.start(2)] + new + attrs[style.end(2):]


def strip_blue_style(attrs: bytes) -> bytes:
    style = STYLE_ATTR.search(attrs)
    if not style:
        return attrs
    new = BLUE_FILL_STYLE.sub(b"", style.group(2)).strip()
    if new:
        return attrs[:style.start(2)] + new + attrs[style.end(2):]
    return (attrs[:style.start()] + attrs[style.end():]).rstrip()


def split_self_closing(attrs: bytes) -> tuple[bytes, bytes]:
    stripped = attrs.rstrip()
    if stripped.endswith(b"/"):
        return stripped[:-1].rstrip(), b" /"
    return attrs, b""


def rebuild_text_open(tag: bytes, attrs: bytes, *, fill: bytes | None = None, strip_blue: bool = False) -> bytes:
    attrs, suffix = split_self_closing(attrs)
    if strip_blue:
        attrs = strip_blue_style(attrs)
    if fill:
        attrs = style_fill(attrs, fill)
    return b"<" + tag + attrs.rstrip() + suffix + b">"


def strip_inline_blue_text_styles(b: bytes) -> bytes:
    def repl(m: re.Match[bytes]) -> bytes:
        return rebuild_text_open(m.group(1), m.group(2), strip_blue=True)

    return TEXT_OR_TSPAN_OPEN.sub(repl, b)


def compact_text(fragment: bytes) -> bytes:
    return b" ".join(TAG.sub(b" ", fragment).split())


def is_link_label(text: bytes) -> bool:
    return bool(CONNECTOR_LABEL.search(text) or GROUND_OR_SPLICE_LABEL.search(text) or PAGE_REF_LABEL.search(text))


def color_text_link_node(match: re.Match[bytes]) -> bytes:
    node = match.group(0)
    if BLUE_FILL.search(node):
        return node
    spans = list(TSPAN_NODE.finditer(node))
    if spans:
        texts = [compact_text(span.group(0)) for span in spans]
        color_indexes: set[int] = set()
        for i, text in enumerate(texts):
            if is_link_label(text):
                color_indexes.add(i)
            if CONNECTOR_LABEL.search(text) and i > 0 and PIN_LABEL.match(texts[i - 1]):
                color_indexes.add(i - 1)
            if PAGE_REF_LABEL.search(text):
                if i > 0 and b"see page" in texts[i - 1].lower():
                    color_indexes.add(i - 1)
                if i + 1 < len(texts) and b"see page" in texts[i + 1].lower():
                    color_indexes.add(i + 1)
        if not color_indexes:
            return node

        out = []
        pos = 0
        for i, span in enumerate(spans):
            out.append(node[pos:span.start()])
            chunk = span.group(0)
            if i in color_indexes:
                chunk = TSPAN_OPEN.sub(
                    lambda m: rebuild_text_open(b"tspan", m.group(1), fill=b"blue"),
                    chunk,
                    count=1,
                )
            out.append(chunk)
            pos = span.end()
        out.append(node[pos:])
        return b"".join(out)

    text = compact_text(node)
    if not is_link_label(text):
        return node

    def repl(m: re.Match[bytes]) -> bytes:
        return rebuild_text_open(m.group(1), m.group(2), fill=b"blue")

    return TEXT_OR_TSPAN_OPEN.sub(repl, node)


def colorize_svg(b: bytes) -> bytes:
    def blue_group(m: re.Match[bytes]) -> bytes:
        return m.group(1) + color_fill_and_stroke(m.group(3), b"blue") + m.group(4)

    def red_wire(m: re.Match[bytes]) -> bytes:
        return m.group(1) + color_stroke(m.group(3), b"red") + m.group(4)

    b = BAD_SELF_CLOSING_STYLE.sub(rb"\1 \2 />", b)
    b = strip_inline_blue_text_styles(b)
    b = BLUE_GROUP.sub(blue_group, b)
    b = WIRE_GROUP.sub(red_wire, b)
    return TEXT_NODE.sub(color_text_link_node, b)


def add_hotspot_bridge(b: bytes) -> bytes:
    """Let embedded SVG hotspots work when file:// documents are isolated."""
    existing = HOTSPOT_BRIDGE_NODE.search(b)
    bridge = HOTSPOT_BRIDGE.strip()
    if existing:
        if existing.group(0) == bridge:
            return b
        return b[:existing.start()] + bridge + b[existing.end():]
    if not HOTSPOT_ID.search(b):
        return b
    closes = list(SVG_CLOSE.finditer(b))
    if not closes:
        return b
    pos = closes[-1].start()
    return b[:pos] + HOTSPOT_BRIDGE + b[pos:]


def fix_bytes(b: bytes) -> bytes | None:
    orig = b
    # 1) repair any jammed attribute after the svg xmlns value
    b = JAM.sub(rb"\1 ", b)
    # 1b) repair font-style clobbered by an earlier buggy blue-link pass
    b = FONT_STYLE_FILL_JAM.sub(rb'font-style=\1\2\1 style="fill:blue;"', b)
    # 2) repair older viewers' accepted-but-invalid no-op rotation.
    b = EMPTY_NEGATIVE_ROTATE.sub(b"rotate(0)", b)
    # 2b) give empty column-gap tspans a glyph so their dx advance applies.
    b = EMPTY_SPACER_TSPAN.sub(b'<tspan\\1>\xe2\x80\x8b</tspan>', b)
    # 3) replace embedded-font references with browser fonts.
    b = SVG_FONT_CSS.sub(BROWSER_FONT_CSS, b)
    b = SVG_FONT_ATTR.sub(BROWSER_FONT_ATTR, b)
    b = OLD_BROWSER_FONT_CSS.sub(BROWSER_FONT_CSS, b)
    b = OLD_BROWSER_FONT_ATTR.sub(BROWSER_FONT_ATTR, b)
    # 3b) give matterCast's unitless CSS font-size declarations a unit so
    # browsers don't discard them and fall back to an oversized default.
    b = CSS_FONT_SIZE_UNITLESS.sub(rb"\1px\2", b)
    # 4) restore legacy color semantics for wire runs and hyperlinks.
    b = colorize_svg(b)
    # 5) ensure the root <svg> has a default xmlns
    m = ROOT.search(b)
    if m:
        end = b.find(b">", m.start())
        tag = b[m.start(): end + 1] if end != -1 else b[m.start(): m.start() + 400]
        if b"xmlns=" not in tag:
            ins = m.end()  # right after "<svg"
            b = b[:ins] + b" " + NS + b[ins:]
    # 6) Firefox may give file:// HTML and its embedded SVG separate opaque
    # origins. The child can still report a validated hotspot ID by message.
    b = add_hotspot_bridge(b)
    return b if b != orig else None


def svg_paths(content_dir: Path):
    """Yield decoded SVG files without walking every XML/search asset."""
    with os.scandir(content_dir) as entries:
        for entry in entries:
            path = Path(entry.path)
            if entry.is_file() and entry.name.lower().endswith(".svg"):
                yield path
            elif entry.is_dir(follow_symlinks=False):
                with os.scandir(path) as book_entries:
                    for book_entry in book_entries:
                        if book_entry.is_file() and book_entry.name.lower().endswith(".svg"):
                            yield Path(book_entry.path)


def target_paths(args: list[str]) -> list[Path]:
    if args:
        return [Path(arg) for arg in args]

    root = Path(".")
    return [root / vol / "content" for vol in discover_volumes(root)]


def iter_targets(paths: list[Path]):
    for path in paths:
        if path.is_dir():
            yield from svg_paths(path)
        elif path.is_file() and path.name.lower().endswith(".svg"):
            yield path


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Modernize decoded SVGs for browser rendering.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional SVG file or content/book directory targets. Defaults to both volume content dirs.",
    )
    ns = parser.parse_args(argv)

    fixed = scanned = 0
    for p in iter_targets(target_paths(ns.paths)):
        scanned += 1
        b = p.read_bytes()
        nb = fix_bytes(b)
        if nb is not None:
            p.write_bytes(nb)
            fixed += 1
        if scanned % 500 == 0:
            print(f"  ...{scanned} scanned, {fixed} fixed", flush=True)
    print(f"done: scanned {scanned} svg, fixed {fixed}")


if __name__ == "__main__":
    main()
