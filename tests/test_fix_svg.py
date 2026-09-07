import tools.fix_svg as fix_svg

fix_bytes = fix_svg.fix_bytes


def test_fix_bytes_adds_namespace_and_repairs_jammed_attrs():
    raw = b'<svg width="10"><text>ok</text></svg>'
    fixed = fix_bytes(raw)
    assert fixed is not None
    assert fix_svg.NS in fixed

    raw = b'<svg xmlns="http://www.w3.org/2000/svg"width="10"></svg>'
    fixed = fix_bytes(raw)
    assert fixed is not None
    assert b'svg" width' in fixed


def test_fix_bytes_repairs_empty_negative_rotate():
    svg = (
        b'<svg><text transform="translate(697, 47) rotate(-)">10</text>'
        b'<text transform="translate(716, 232) rotate(-90)">33</text></svg>'
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b'rotate(0)">10</text>' in fixed
    assert b'rotate(-90)">33</text>' in fixed


def test_fix_bytes_rotate_repair_is_idempotent():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text transform="rotate(0)">10</text></svg>'

    assert fix_bytes(svg) is None


def test_fix_bytes_adds_unit_to_unitless_css_font_size():
    svg = (
        b"<svg><style type='text/css'><![CDATA["
        b".t3 { font-size:7.0000;font-family:Arial,sans-serif; }"
        b"]]></style><g style=\"font-size:20; font-family:Helvetica;\"></g></svg>"
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b"font-size:7.0000px;" in fixed
    assert b"font-size:20px;" in fixed


def test_fix_bytes_rewrites_svg_font_css_and_attributes():
    svg = (
        b'<svg><style>.t2 { font-size:12;font-family:font1;fill:#000; }</style>'
        b'<text font-family="font3">C1035a</text></svg>'
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert fix_svg.BROWSER_FONT_CSS in fixed
    assert b'font-family="Liberation Sans Narrow,Arial Narrow,Arial,sans-serif"' in fixed
    assert b"font-family:font1" not in fixed
    assert b'font-family="font3"' not in fixed


def test_fix_bytes_rewrites_previous_verdana_fallback():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<style>.t2 { font-family:Verdana,Arial,sans-serif; }</style>'
        b'<text font-family="Verdana,Arial,sans-serif">ok</text>'
        b"</svg>"
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert fix_svg.BROWSER_FONT_CSS in fixed
    assert b'font-family="Liberation Sans Narrow,Arial Narrow,Arial,sans-serif"' in fixed


def test_fix_bytes_does_not_corrupt_font_style_attribute():
    svg = b'<svg><tspan font-style="italic" class="t4">86-1</tspan></svg>'

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b'font-style="italic"' in fixed
    assert b"fill:blue" not in fixed


def test_fix_bytes_colors_semantic_wire_and_link_groups():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<g id="CONN_C1035B_B9"><line stroke="black"/>'
        b'<text fill="black">B9</text><text fill="black">C1035B</text></g>'
        b'<g id="PAGEREF_11-3"><text fill="black">11-3</text></g>'
        b'<g id="_a2037B"><line stroke="black"/>'
        b'<text fill="black">2037</text><text fill="black">RD</text></g>'
        b"</svg>"
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b'<line stroke="blue"/>' in fixed
    assert b'<text fill="blue">B9</text>' in fixed
    assert b'<text fill="blue">C1035B</text>' in fixed
    assert b'<text fill="blue">11-3</text>' in fixed
    assert b'<g id="_a2037B"><line stroke="red"/>' in fixed
    assert b'<text fill="black">2037</text>' in fixed


def test_fix_bytes_colors_legacy_text_link_labels_with_inline_style():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<style>.t2 { fill:#000; }</style>'
        b'<text class="t2"><tspan>51</tspan><tspan>C1035a</tspan></text>'
        b'<text class="t2"><tspan>WH-BK</tspan></text>'
        b'<text class="t2"><tspan>See page</tspan><tspan>85-2</tspan><tspan>HEADLAMP</tspan></text>'
        b"</svg>"
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b'<text class="t2"><tspan style="fill:blue;">51</tspan>' in fixed
    assert b'<tspan style="fill:blue;">C1035a</tspan>' in fixed
    assert b'<text class="t2"><tspan>WH-BK</tspan></text>' in fixed
    assert (
        b'<text class="t2"><tspan style="fill:blue;">See page</tspan>'
        b'<tspan style="fill:blue;">85-2</tspan><tspan>HEADLAMP</tspan></text>'
    ) in fixed


def test_fix_bytes_repairs_bad_self_closing_tspan_style_from_previous_batch():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<text><tspan class="t2" style="fill:blue;">VBA'
        b'<tspan dx="-0.5670" / style="fill:blue;">TT</tspan>'
        b'<tspan>85-2</tspan></text></svg>'
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b'<tspan dx="-0.5670" />' in fixed
    assert b'/ style=' not in fixed
    assert b'<tspan class="t2">VBA<tspan dx="-0.5670" />TT</tspan>' in fixed
    assert b'<tspan style="fill:blue;">85-2</tspan>' in fixed


def test_fix_bytes_gives_empty_spacer_tspan_a_glyph_to_shift():
    svg = b'<svg><text><tspan>Pin<tspan dx="40.0" />Circuit</tspan></text></svg>'

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b'<tspan dx="40.0" />' not in fixed
    assert b'Pin<tspan dx="40.0">\xe2\x80\x8b</tspan>Circuit' in fixed


def test_fix_bytes_adds_file_origin_hotspot_bridge_once():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<g id="CONN_C1035B_B9"><text fill="blue">C1035B</text></g></svg>'
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert fixed.count(fix_svg.HOTSPOT_BRIDGE_MARKER) == 1
    assert b"window.parent.postMessage" in fixed
    assert b"data-tso-wiring-direct" in fixed
    assert b"tso-wiring-wheel-v1" in fixed
    assert b"{passive:false}" in fixed
    assert fix_bytes(fixed) is None


def test_fix_bytes_upgrades_older_hotspot_bridge():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><g id="CONN_C100_1"/>'
        b'<script id="tso-wiring-hotspot-bridge"><![CDATA[old bridge]]></script></svg>'
    )

    fixed = fix_bytes(svg)

    assert fixed is not None
    assert b"old bridge" not in fixed
    assert fixed.count(fix_svg.HOTSPOT_BRIDGE_MARKER) == 1
    assert b"tso-wiring-wheel-v1" in fixed
    assert fix_bytes(fixed) is None


def test_fix_bytes_does_not_bridge_svg_without_hotspots():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text fill="black">plain</text></svg>'

    assert fix_bytes(svg) is None


def test_fix_bytes_is_idempotent_after_all_repairs():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b"<style>.t2 { font-family:'Liberation Sans Narrow','Arial Narrow',Arial,sans-serif; }</style>"
        b'<text font-family="Liberation Sans Narrow,Arial Narrow,Arial,sans-serif" transform="rotate(0)">ok</text>'
        b'<g id="CONN_C1035B_B9"><line stroke="blue"/>'
        b'<text fill="blue">B9</text><text fill="blue">C1035B</text></g>'
        b'<g id="_a2037B"><line stroke="red"/><text fill="black">RD</text></g>'
        b"</svg>"
    )

    fixed = fix_bytes(svg)
    assert fixed is not None
    assert fix_bytes(fixed) is None
