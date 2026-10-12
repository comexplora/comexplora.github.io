#!/usr/bin/env python3
"""Kit de marca de Comexplora: genera los logos en SVG (texto vectorizado) y sus versiones PNG.

Uso:
  python3 scripts/brand_kit.py preview          → hoja con las propuestas en build/brand-preview/
  python3 scripts/brand_kit.py apply <variante> → escribe img/brand/, favicon.svg, íconos y og-default

Requiere fontTools, uharfbuzz, cairosvg y Pillow (solo para correrlo a mano; el sitio no lo usa).
La tipografía es Schibsted Grotesk (SIL Open Font License), en scripts/fonts/.
"""
import io
import math
import sys
from pathlib import Path

import cairosvg
import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FONT_VF = ROOT / 'scripts' / 'fonts' / 'SchibstedGrotesk-VF.ttf'

NAVY = '#0B2140'
TEAL = '#1EC8B5'
WHITE = '#FFFFFF'
BLACK = '#111111'
NAME = 'Comexplora'

_fonts = {}


def font(weight):
    if weight not in _fonts:
        vf = TTFont(FONT_VF)
        inst = instantiateVariableFont(vf, {'wght': weight})
        buf = io.BytesIO()
        inst.save(buf)
        data = buf.getvalue()
        _fonts[weight] = (TTFont(io.BytesIO(data)), data)
    return _fonts[weight]


def text_paths(text, weight, cap_h, x0=0.0, baseline=0.0, tracking=0.0, colors=None):
    """Devuelve (lista de (color, d), ancho) con el texto convertido a curvas.

    cap_h: altura de mayúsculas deseada en unidades SVG. tracking: espacio extra entre letras (en unidades SVG).
    colors: función índice → color (None = color por defecto)."""
    tt, data = font(weight)
    upm = tt['head'].unitsPerEm
    cap = getattr(tt['OS/2'], 'sCapHeight', 0) or upm * 0.7
    scale = cap_h / cap
    face = hb.Face(data)
    hbf = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbf, buf, {'kern': True, 'liga': False})
    gs = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    out, x = [], 0.0
    for i, (info, pos) in enumerate(zip(buf.glyph_infos, buf.glyph_positions)):
        name = order[info.codepoint]
        pen = SVGPathPen(gs)
        tp = TransformPen(pen, (scale, 0, 0, -scale, x0 + x + pos.x_offset * scale, baseline))
        gs[name].draw(tp)
        d = pen.getCommands()
        if d:
            out.append(((colors(i) if colors else None), d))
        x += pos.x_advance * scale + tracking
    return out, x - tracking


def fmt(v):
    return f'{v:.2f}'.rstrip('0').rstrip('.')


# ---------- Símbolos (cada uno dibuja en una caja de 0..S) ----------

def sym_cruce(S, fg, accent):
    """Dos rutas que se cruzan: una diagonal de base y otra que sale en flecha hacia arriba a la derecha."""
    w = S * 0.17
    p = S * 0.16
    a = (p, S - p)
    b = (S - p - w * 0.9, p + w * 0.9)
    head = S * 0.30
    parts = [f'<path d="M{fmt(p)} {fmt(p)}L{fmt(S-p)} {fmt(S-p)}" stroke="{fg}" stroke-width="{fmt(w)}" stroke-linecap="round" fill="none"/>',
             f'<path d="M{fmt(a[0])} {fmt(a[1])}L{fmt(b[0])} {fmt(b[1])}" stroke="{accent}" stroke-width="{fmt(w)}" stroke-linecap="round" fill="none"/>',
             f'<path d="M{fmt(S-p-head)} {fmt(p)}H{fmt(S-p)}V{fmt(p+head)}" stroke="{accent}" stroke-width="{fmt(w)}" stroke-linecap="round" stroke-linejoin="round" fill="none"/>']
    return ''.join(parts)


def sym_brujula(S, fg, accent):
    """Anillo abierto (una C) con una aguja de brújula que apunta al noreste."""
    c = S / 2
    r = S * 0.40
    w = S * 0.11
    a0, a1 = math.radians(-35), math.radians(35)   # hueco a la derecha
    x0, y0 = c + r * math.cos(a1), c + r * math.sin(a1)
    x1, y1 = c + r * math.cos(a0), c + r * math.sin(a0)
    ring = (f'<path d="M{fmt(x0)} {fmt(y0)}A{fmt(r)} {fmt(r)} 0 1 1 {fmt(x1)} {fmt(y1)}" '
            f'stroke="{fg}" stroke-width="{fmt(w)}" stroke-linecap="round" fill="none"/>')
    ang = math.radians(-45)
    L, Wn = S * 0.27, S * 0.12
    ux, uy = math.cos(ang), math.sin(ang)
    vx, vy = -uy, ux
    tip = (c + ux * L, c + uy * L)
    tail = (c - ux * L, c - uy * L)
    l = (c + vx * Wn, c + vy * Wn)
    rr = (c - vx * Wn, c - vy * Wn)
    n1 = f'<path d="M{fmt(tip[0])} {fmt(tip[1])}L{fmt(l[0])} {fmt(l[1])}L{fmt(rr[0])} {fmt(rr[1])}Z" fill="{accent}"/>'
    n2 = f'<path d="M{fmt(tail[0])} {fmt(tail[1])}L{fmt(l[0])} {fmt(l[1])}L{fmt(rr[0])} {fmt(rr[1])}Z" fill="{fg}"/>'
    return ring + n1 + n2


def sym_ritmo(S, fg, accent):
    """Las cinco barras del logo anterior, con la central en turquesa."""
    hs = [0.36, 0.64, 1.0, 0.56, 0.30]
    bw = S * 0.13
    gap = (S - 5 * bw) / 4
    out = []
    for i, h in enumerate(hs):
        hh = S * h
        out.append(f'<rect x="{fmt(i*(bw+gap))}" y="{fmt((S-hh)/2)}" width="{fmt(bw)}" height="{fmt(hh)}" rx="{fmt(bw*0.17)}" fill="{accent if i == 2 else fg}"/>')
    return ''.join(out)


VARIANTS = {
    'cruce': {
        'label': 'A · Cruce',
        'idea': 'La X de COMEX y de EXPLORA es la misma: dos rutas que se cruzan, una sale en flecha.',
        'symbol': sym_cruce, 'upper': True, 'weight': 800, 'accent_letter': 'X', 'tracking': 0.02,
    },
    'brujula': {
        'label': 'B · Brújula',
        'idea': 'Una C abierta con una aguja que apunta al noreste: explorar mercados con rumbo.',
        'symbol': sym_brujula, 'upper': False, 'weight': 700, 'accent_letter': None, 'tracking': -0.01,
    },
    'ritmo': {
        'label': 'C · Ritmo',
        'idea': 'Conserva las cinco barras del logo actual y cambia solo el nombre: continuidad con lo ya publicado.',
        'symbol': sym_ritmo, 'upper': True, 'weight': 800, 'accent_letter': None, 'tracking': 0.03,
    },
}


def wordmark(v, cap_h, x0, baseline, fg, accent):
    cfg = VARIANTS[v]
    text = NAME.upper() if cfg['upper'] else NAME.lower()
    acc_idx = {i for i, ch in enumerate(text) if cfg['accent_letter'] and ch == cfg['accent_letter']}
    paths, width = text_paths(text, cfg['weight'], cap_h, x0, baseline, tracking=cfg['tracking'] * cap_h,
                              colors=lambda i: accent if i in acc_idx else fg)
    return ''.join(f'<path d="{d}" fill="{c}"/>' for c, d in paths), width


def logo_svg(v, fg, accent, title=True):
    """Logo horizontal: símbolo + nombre. Alto 149, como el anterior."""
    H = 149.0
    sym = VARIANTS[v]['symbol'](H, fg, accent)
    cap = 72.0 if not VARIANTS[v]['upper'] else 74.0
    gap = H * 0.30
    base = H / 2 + cap / 2
    if not VARIANTS[v]['upper']:
        base = H / 2 + cap * 0.36   # minúsculas: centrar por altura de x
    wm, ww = wordmark(v, cap, H + gap, base, fg, accent)
    W = H + gap + ww
    t = f'<title>{NAME}</title>' if title else ''
    return W, H, (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {fmt(W)} {fmt(H)}" width="{round(W)}" height="{round(H)}" '
                  f'role="img" aria-label="{NAME}">{t}{sym}{wm}</svg>')


def logo_inner(v, fg='currentColor', accent=TEAL):
    """Para incrustar en la cabecera (sin <title>)."""
    W, H, svg = logo_svg(v, fg, accent, title=False)
    inner = svg[svg.index('>') + 1:-len('</svg>')]
    return W, H, inner


def symbol_svg(v, fg, accent, size=100, bg=None, pad=0.0, radius=0.0):
    inner_s = size * (1 - 2 * pad)
    sym = VARIANTS[v]['symbol'](inner_s, fg, accent)
    rect = f'<rect width="{size}" height="{size}" rx="{fmt(size*radius)}" fill="{bg}"/>' if bg else ''
    off = size * pad
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
            f'<title>{NAME}</title>{rect}<g transform="translate({fmt(off)} {fmt(off)})">{sym}</g></svg>')


def png(svg, width, out=None, bg=None):
    data = cairosvg.svg2png(bytestring=svg.encode(), output_width=width, background_color=bg)
    if out:
        Path(out).write_bytes(data)
    return data


# ---------- Hoja de presentación ----------

def preview():
    outdir = ROOT / 'build' / 'brand-preview'
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for v, cfg in VARIANTS.items():
        rows.append(preview_row(v, cfg))
    W = 1600
    H = sum(h for _, h in rows) + 140
    y = 140
    body = []
    t, _ = text_paths('Comexplora · tres propuestas de logo', 600, 34, 80, 90, colors=lambda i: NAVY)
    body.append(''.join(f'<path d="{d}" fill="{c}"/>' for c, d in t))
    for svg_g, h in rows:
        body.append(f'<g transform="translate(0 {y})">{svg_g}</g>')
        y += h
    sheet = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
             f'<rect width="{W}" height="{H}" fill="#F4F6F9"/>{"".join(body)}</svg>')
    (outdir / 'propuestas.svg').write_text(sheet)
    png(sheet, W, outdir / 'propuestas.png')
    for v in VARIANTS:
        _, _, s = logo_svg(v, NAVY, TEAL)
        (outdir / f'logo-{v}.svg').write_text(s)
    print(outdir / 'propuestas.png')


def embed(svg, x, y, w):
    """Inserta un SVG dentro de otro, escalado a ancho w."""
    vb = svg.split('viewBox="')[1].split('"')[0].split()
    vw, vh = float(vb[2]), float(vb[3])
    s = w / vw
    inner = svg[svg.index('>') + 1:-len('</svg>')]
    inner = inner.replace(f'<title>{NAME}</title>', '')
    return f'<g transform="translate({fmt(x)} {fmt(y)}) scale({s:.5f})">{inner}</g>', vh * s


def preview_row(v, cfg):
    H = 470
    g = [f'<rect x="40" y="0" width="1520" height="{H-30}" rx="18" fill="#FFFFFF"/>']
    lab, _ = text_paths(cfg['label'], 700, 26, 80, 56, colors=lambda i: NAVY)
    g += [f'<path d="{d}" fill="{c}"/>' for c, d in lab]
    idea, _ = text_paths(cfg['idea'], 400, 15, 80, 92, colors=lambda i: '#4A5568')
    g += [f'<path d="{d}" fill="{c}"/>' for c, d in idea]
    # Color sobre blanco
    _, _, lc = logo_svg(v, NAVY, TEAL)
    e, h = embed(lc, 80, 130, 560)
    g.append(e)
    # Negativo sobre azul
    g.append(f'<rect x="700" y="112" width="620" height="170" rx="12" fill="{NAVY}"/>')
    _, _, ln = logo_svg(v, WHITE, TEAL)
    e, _ = embed(ln, 730, 152, 560)
    g.append(e)
    # Íconos: avatar redondo, app, favicon 48/32/16
    av = symbol_svg(v, WHITE, TEAL, 512, NAVY, 0.24, 0.5)
    e, _ = embed(av, 1360, 112, 170)
    g.append(e)
    x = 80
    for sz in (96, 48, 32, 16):
        fav = symbol_svg(v, WHITE, TEAL, 64, NAVY, 0.2, 0.22)
        e, _ = embed(fav, x, 330, sz)
        g.append(e)
        x += sz + 28
    # Cabecera simulada
    g.append(f'<rect x="420" y="318" width="900" height="96" rx="10" fill="{NAVY}"/>')
    e, _ = embed(ln, 450, 345, 200)
    g.append(e)
    for i, lbl in enumerate(['Noticias', 'Argentina', 'Mundo', 'Logística', 'Datos']):
        t, _ = text_paths(lbl, 500, 13, 720 + i * 110, 372, colors=lambda j: '#C9D3E0')
        g += [f'<path d="{d}" fill="{c}"/>' for c, d in t]
    mono, _ = text_paths('Monocromo', 500, 13, 1360, 330, colors=lambda j: '#4A5568')
    g += [f'<path d="{d}" fill="{c}"/>' for c, d in mono]
    _, _, lm = logo_svg(v, BLACK, BLACK)
    e, _ = embed(lm, 1360, 350, 170)
    g.append(e)
    return ''.join(g), H


# ---------- Aplicar la variante elegida ----------

def apply(v):
    b = ROOT / 'img' / 'brand'
    b.mkdir(parents=True, exist_ok=True)
    W, H, s = logo_svg(v, NAVY, TEAL); (b / 'logo-color.svg').write_text(s)
    _, _, s = logo_svg(v, WHITE, TEAL); (b / 'logo-negativo.svg').write_text(s)
    neg = s
    _, _, s = logo_svg(v, NAVY, NAVY); (b / 'logo-mono-azul.svg').write_text(s)
    _, _, s = logo_svg(v, BLACK, BLACK); (b / 'logo-mono-negro.svg').write_text(s)
    _, _, s = logo_svg(v, WHITE, WHITE); (b / 'logo-mono-blanco.svg').write_text(s)
    (b / 'simbolo.svg').write_text(symbol_svg(v, NAVY, TEAL, 100))
    sq = symbol_svg(v, WHITE, TEAL, 512, NAVY, 0.22, 0.0)
    (b / 'icono-cuadrado.svg').write_text(sq)
    av = symbol_svg(v, WHITE, TEAL, 512, NAVY, 0.25, 0.0)
    (b / 'avatar-redes.svg').write_text(av)
    png(av, 1080, b / 'avatar-redes-1080.png')
    fav = symbol_svg(v, WHITE, TEAL, 64, NAVY, 0.2, 0.22)
    (ROOT / 'favicon.svg').write_text(fav)
    png(symbol_svg(v, WHITE, TEAL, 180, NAVY, 0.2, 0.0), 180, ROOT / 'apple-touch-icon.png')
    png(symbol_svg(v, WHITE, TEAL, 512, NAVY, 0.2, 0.0), 512, ROOT / 'logo.png')
    # Logo en negativo para las imágenes de redes (og_images.py lo pega sobre fondo azul)
    png(neg, 900, b / 'logo-negativo-og.png')
    # Imagen por defecto para compartir (1200×630)
    e, h = embed(neg, 200, 0, 800)
    og = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630">'
          f'<rect width="1200" height="630" fill="{NAVY}"/>'
          f'<g transform="translate(0 {fmt(315 - h/2 - 30)})">{e}</g>')
    sub, sw = text_paths('Noticias de comercio exterior', 500, 30, 0, 0, colors=lambda i: '#C9D3E0')
    sub_paths = ''.join(f'<path d="{d}" fill="{c}"/>' for c, d in sub)
    og += f'<g transform="translate({fmt(600 - sw/2)} {fmt(315 + h/2 + 40)})">{sub_paths}</g>'
    og += f'<rect x="0" y="618" width="1200" height="12" fill="{TEAL}"/></svg>'
    (b / 'og-default.svg').write_text(og)
    png(og, 1200, ROOT / 'og-default.png')
    W, H, inner = logo_inner(v)
    (ROOT / 'build').mkdir(exist_ok=True)
    (ROOT / 'build' / 'header-logo.txt').write_text(f'{fmt(W)} {fmt(H)}\n{inner}\n')
    print('kit generado:', v)


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'preview':
        preview()
    elif len(sys.argv) >= 3 and sys.argv[1] == 'apply':
        apply(sys.argv[2])
    else:
        print(__doc__)
