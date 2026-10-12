"""PDF situation report: map snapshot + prediction summary + annotations."""
from __future__ import annotations

import base64
import io
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#1b2a3a")
ACCENT = colors.HexColor("#0b6e99")
MUTED = colors.HexColor("#5b6b7a")
RULE = colors.HexColor("#d6dde4")


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontSize=18, leading=22, textColor=INK, alignment=0, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=ss["Normal"], fontSize=9, textColor=MUTED, spaceAfter=10),
        "h": ParagraphStyle("h", parent=ss["Heading2"], fontSize=12, textColor=ACCENT, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontSize=9, leading=12, textColor=INK),
        "small": ParagraphStyle("sm", parent=ss["Normal"], fontSize=7.5, leading=9.5, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=ss["Normal"], fontSize=8, leading=10, textColor=INK),
    }


def _table(rows, widths, header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    else:
        style += [("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8), ("TEXTCOLOR", (0, 0), (0, -1), MUTED)]
    t.setStyle(TableStyle(style))
    return t


def _img_from_dataurl(data_url: str, max_w: float, max_h: float):
    raw = base64.b64decode(data_url.split(",", 1)[1])
    from PIL import Image as PILImage

    w, h = PILImage.open(io.BytesIO(raw)).size
    scale = min(max_w / w, max_h / h)
    return Image(io.BytesIO(raw), width=w * scale, height=h * scale)


def _p(text: str, style) -> Paragraph:
    """User text -> Paragraph (escape markup, keep line breaks)."""
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def _geom_summary(g: dict) -> str:
    t = g.get("type")
    c = g.get("coordinates")
    if t == "Point":
        return f"Point {c[1]:.4f}, {c[0]:.4f}"
    if t == "LineString":
        return f"Line, {len(c)} vertices"
    if t == "Polygon":
        return f"Area, {len(c[0]) - 1} vertices"
    return t or ""


def build_pdf(*, title: str, author: str, notes: str, meta: dict | None, map_png: str | None,
              annotations: dict | None, routes: dict | None, layers_on: list[str] | None) -> bytes:
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.55 * inch, bottomMargin=0.6 * inch, title=title, author=author)
    W = letter[0] - 1.2 * inch
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    el = [_p(title or "A9 PPFE situation report", st["title"]),
          Paragraph(f"{escape(author or 'Anonymous')} · generated {gen} · A9_PPFE decision-support portal", st["sub"])]

    if meta:
        model = (meta.get("model") or {}).get("card", {})
        rows = [
            ["AOI", meta["aoi"]["name"]],
            ["HRRR init / valid", f"{meta.get('init_time', '')}  →  {meta.get('valid_time', '')}"],
            ["HRRR source", f"{meta['hrrr']['source']}{' (cached)' if meta['hrrr'].get('cached') else ''}"],
            ["Model", f"{model.get('name', 'remote')} v{model.get('version', '')}  ·  ×{round(meta['hrrr']['staging_grid']['res'] / meta['grids']['fine']['res'])} downscale"],
            ["Run", f"{meta['id']}  ·  total {meta['timings']['total_s']} s (inference {meta['timings']['inference_s']} s)"],
        ]
        if layers_on:
            rows.append(["Layers shown", ", ".join(layers_on)])
        el.append(_table(rows, [1.4 * inch, W - 1.4 * inch], header=False))
        el.append(Spacer(1, 8))

    if map_png:
        el.append(_img_from_dataurl(map_png, W, 5.4 * inch))
        el.append(Paragraph("Map snapshot as displayed in the portal. Numbered markers correspond to the annotation table.", st["small"]))

    if notes:
        el += [Paragraph("Notes", st["h"]), _p(notes, st["body"])]

    if meta:
        hz = meta["hazard"]
        el.append(Paragraph("Prediction summary", st["h"]))
        rows = [["Variable", "Min", "Mean", "Max"]]
        for k, v in meta.get("stats", {}).items():
            u = "°C" if "tmp" in k else "mm"
            off = -273.15 if "tmp" in k else 0
            rows.append([k, f"{v['min'] + off:.2f} {u}", f"{v['mean'] + off:.2f} {u}", f"{v['max'] + off:.2f} {u}"])
        rows.append(["Flood hazard index", "", f"{hz['mean']:.3f}", f"{hz['max']:.3f}"])
        el.append(_table(rows, [2.6 * inch, (W - 2.6 * inch) / 3] + [(W - 2.6 * inch) / 3] * 2))
        el.append(Spacer(1, 4))
        pa = hz["percent_area"]
        el.append(Paragraph("Share of AOI by hazard class: " + " · ".join(f"<b>{k}</b> {v}%" for k, v in pa.items()), st["body"]))

        exp = meta.get("exposed_infrastructure") or []
        if exp:
            head = Paragraph(f"Infrastructure in moderate-or-worse hazard ({len(exp)})", st["h"])
            rows = [["Facility", "Type", "Hazard", "Location"]] + [
                [_p(e["name"], st["cell"]), e["class"], f"{e['level']} ({e['hazard']:.2f})", f"{e['lat']:.4f}, {e['lon']:.4f}"]
                for e in exp[:25]]
            el.append(KeepTogether([head, _table(rows, [2.6 * inch, 1.4 * inch, 1.2 * inch, W - 5.2 * inch])]))

    if routes:
        head = Paragraph("Storm-management routing", st["h"])
        rows = [["Route", "Length", "Travel time", "Max hazard on route"]]
        for k, lbl in (("baseline", "Fastest (ignores forecast)"), ("safe", "Hazard-aware")):
            r = routes.get(k)
            rows.append([lbl, f"{r['length_km']} km", f"{r['minutes']} min", f"{r['max_hazard']:.2f}"] if r else [lbl, "no path", "", ""])
        el.append(KeepTogether([head, _table(rows, [2.4 * inch, 1.2 * inch, 1.3 * inch, W - 4.9 * inch])]))
        if routes.get("blocked_edges"):
            el.append(Paragraph(f"{routes['blocked_edges']} road segments closed above the hazard threshold.", st["small"]))
        if routes.get("trace"):
            el.append(Paragraph(f"Downstream flow path traced: {len(routes['trace'])} cells.", st["small"]))

    feats = (annotations or {}).get("features", [])
    if feats:
        head = Paragraph(f"Annotations ({len(feats)})", st["h"])
        rows = [["#", "Title", "Note", "Geometry", "By"]]
        for i, f in enumerate(feats, 1):
            p = f.get("properties", {})
            rows.append([str(i), _p(p.get("title", ""), st["cell"]), _p(p.get("note", ""), st["cell"]),
                         _geom_summary(f.get("geometry", {})), _p(p.get("author", "") or author, st["cell"])])
        el.append(KeepTogether([head, _table(rows, [0.3 * inch, 1.5 * inch, W - 4.0 * inch, 1.3 * inch, 0.9 * inch])]))

    el += [Spacer(1, 12), Paragraph(
        "Placeholder model notice: predictions come from an untrained placeholder foundation model hosted on the "
        "A9_PPFE platform for system testing. Do not use for operational decisions. Context layers: OpenStreetMap "
        "contributors (ODbL), FEMA National Flood Hazard Layer, AWS Terrain Tiles, NOAA HRRR.", st["small"])]

    def footer(c, d):
        c.saveState()
        c.setFont("Helvetica", 7)
        c.setFillColor(MUTED)
        c.drawString(0.6 * inch, 0.35 * inch, "A9_PPFE · NASA EarthRISE · UAH")
        c.drawRightString(letter[0] - 0.6 * inch, 0.35 * inch, f"Page {d.page}")
        c.restoreState()

    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
