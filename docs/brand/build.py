"""Build the Telltale lamp logo as outlined SVG (no font needed to view it)."""
import sys, pathlib
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

D = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2]); OUT.mkdir(exist_ok=True)
INK, PAPER, ORANGE, GREEN, GREEN_ON_INK = "#13283b", "#f4f1ea", "#e8501a", "#2f6b4a", "#4f9a70"
TAGLINE = "Someone has done this job before."

class Face:
    def __init__(self, path):
        self.tt = TTFont(path); self.gs = self.tt.getGlyphSet()
        blob = hb.Blob.from_file_path(str(path)); self.hb = hb.Font(hb.Face(blob))
    def shape(self, text):
        buf = hb.Buffer(); buf.add_str(text); buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": True})
        names = self.tt.getGlyphOrder()
        return [(names[i.codepoint], p.x_advance, p.x_offset, p.y_offset) for i, p in zip(buf.glyph_infos, buf.glyph_positions)]
    def path(self, text, x0, y0, scale):
        """SVG path data for text, baseline at y0, font units scaled."""
        pen = SVGPathPen(self.gs); x = 0
        for name, adv, xo, yo in self.shape(text):
            t = TransformPen(pen, (scale, 0, 0, -scale, x0 + (x + xo) * scale, y0 - yo * scale))
            self.gs[name].draw(t); x += adv
        return pen.getCommands(), x * scale
    def bounds(self, ch):
        name = self.tt.getBestCmap()[ord(ch)]; bp = BoundsPen(self.gs); self.gs[name].draw(bp); return bp.bounds

gar, geist = Face(D / "garamond600.ttf"), Face(D / "geist500.ttf")
S = 0.128                         # 1 font unit = 0.128 px: wordmark at 128 px
BASE = 150                        # baseline
l_top = gar.bounds("l")[3]        # top of the l ascender, used for lamp height
T_left = gar.bounds("T")[0] * S
W, GAP_IN, GAP_L, GAP_R = 172 * S, 96 * S, 70 * S, 62 * S

def wordmark(fg, green, tagline):
    te, te_w = gar.path("Te", 0, BASE, S)
    x1 = te_w + GAP_L; x2 = x1 + W + GAP_IN; xt = x2 + W + GAP_R
    tale, tale_w = gar.path("tale", xt, BASE, S)
    top = BASE - l_top * S; h = l_top * S
    parts = [f'<path d="{te}" fill="{fg}"/>', f'<path d="{tale}" fill="{fg}"/>',
             f'<circle cx="{x2 + W/2:.2f}" cy="{top + W/2:.2f}" r="{W*0.95:.2f}" fill="{ORANGE}" opacity="0.2"/>',
             f'<rect x="{x1:.2f}" y="{top:.2f}" width="{W:.2f}" height="{h:.2f}" rx="{W/2:.2f}" fill="{green}"/>',
             f'<rect x="{x2:.2f}" y="{top:.2f}" width="{W:.2f}" height="{h:.2f}" rx="{W/2:.2f}" fill="{ORANGE}"/>']
    right = xt + tale_w; bottom = BASE + 6
    if tagline:
        ts = 0.0255            # tagline about 25.5 px
        tag, tag_w = geist.path(TAGLINE, T_left - geist.bounds("S")[0] * ts, BASE + 58, ts)
        parts.append(f'<path d="{tag}" fill="{fg}" opacity="0.82"/>')
        right = max(right, T_left + tag_w); bottom = BASE + 72
    pad = 24; top_edge = top - W * 0.95 - 4
    vb = f"{T_left - pad:.1f} {top_edge - pad + W*0.5:.1f} {right - T_left + 2*pad:.1f} {bottom - top_edge + 2*pad - W*0.5:.1f}"
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="Telltale">\n<title>Telltale</title>\n' + "\n".join(parts) + "\n</svg>\n"

def icon(bg, green):
    # Two lamps on a rounded square, for avatars and favicons.
    s = 512; w = 92; h = 300; gap = 52; x1 = (s - 2*w - gap)/2; x2 = x1 + w + gap; y = (s - h)/2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" role="img" aria-label="Telltale">\n<title>Telltale</title>\n'
            f'<rect width="{s}" height="{s}" rx="96" fill="{bg}"/>\n'
            f'<circle cx="{x2 + w/2}" cy="{y + w/2}" r="{w*0.95}" fill="{ORANGE}" opacity="0.2"/>\n'
            f'<rect x="{x1}" y="{y}" width="{w}" height="{h}" rx="{w/2}" fill="{green}"/>\n'
            f'<rect x="{x2}" y="{y}" width="{w}" height="{h}" rx="{w/2}" fill="{ORANGE}"/>\n</svg>\n')

files = {
    "telltale-logo.svg": wordmark(INK, GREEN, False),
    "telltale-logo-on-dark.svg": wordmark(PAPER, GREEN_ON_INK, False),
    "telltale-logo-tagline.svg": wordmark(INK, GREEN, True),
    "telltale-logo-tagline-on-dark.svg": wordmark(PAPER, GREEN_ON_INK, True),
    "telltale-icon.svg": icon(PAPER, GREEN),
    "telltale-icon-on-dark.svg": icon(INK, GREEN_ON_INK),
}
for n, svg in files.items(): (OUT / n).write_text(svg); print(n, len(svg))
