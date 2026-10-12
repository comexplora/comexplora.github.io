#!/usr/bin/env python3
"""Imágenes para redes (Open Graph, 1200×630) de las notas curadas.

Cada nota curada recibe una tarjeta con la marca, la categoría, el título y su dato clave, para que
al compartirla en WhatsApp, LinkedIn o X se vea profesional y se reconozca Comexplora.
Las automáticas (que solo enlazan a otro medio) siguen usando la foto de archivo.

- Salida: img/og/<id>.png y img/og/manifest.json (huella de cada imagen: solo se regenera si cambió la nota).
- Tipografías: scripts/fonts/ (Source Serif 4 e IBM Plex, licencia SIL OFL; ver scripts/fonts/OFL.txt).
- Requiere Pillow. Si no está instalado, build_pages.py sigue usando la foto de archivo.
Lo llama scripts/build_pages.py; no hace falta ejecutarlo a mano.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = Path(__file__).resolve().parent / 'fonts'
OUT = ROOT / 'img' / 'og'
VERSION = '3'          # cambiarlo fuerza a regenerar todas las imágenes (por ejemplo, tras un rediseño)
MAX_AGE_DAYS = 180     # notas más viejas vuelven a la foto de archivo y su imagen se borra
W, H = 1200, 630
PAD = 72

NAVY, NAVY_2, ACCENT = (11, 37, 69), (19, 49, 92), (227, 162, 26)
WHITE, MUTED, SOFT = (238, 243, 249), (175, 192, 212), (120, 145, 175)
CELESTE = (116, 172, 223)
MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def wrap(draw, text, fnt, width):
    words, lines, cur = text.split(), [], ''
    for w in words:
        test = f'{cur} {w}'.strip()
        if draw.textlength(test, font=fnt) <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


TITLE_BOX = 252   # alto disponible para el título (entre la categoría y la banda inferior)


def fit_title(draw, text, width, max_lines=4):
    """Tamaño más grande (64 → 40 px) con el que el título entra en el recuadro, en hasta max_lines renglones."""
    for size in range(64, 39, -2):
        f = font('SourceSerif4-Bold.ttf', size)
        lines = wrap(draw, text, f, width)
        if len(lines) <= max_lines and len(lines) * round(size * 1.16) <= TITLE_BOX:
            return f, lines
    f = font('SourceSerif4-Bold.ttf', 40)
    lines = wrap(draw, text, f, width)[:max_lines]
    while lines and draw.textlength(lines[-1] + '…', font=f) > width:
        lines[-1] = lines[-1].rsplit(' ', 1)[0]
    lines[-1] = lines[-1].rstrip(',;:') + '…'
    return f, lines


def clip(draw, text, fnt, width):
    if draw.textlength(text, font=fnt) <= width:
        return text
    while text and draw.textlength(text + '…', font=fnt) > width:
        text = text[:-1]
    return text.rstrip(' ,;:') + '…'


LOGO_PNG = ROOT / 'img' / 'brand' / 'logo-negativo-og.png'   # logo en negativo (blanco + turquesa), fondo transparente


def logo(im, x, y, h):
    """Pega el logo del sitio con altura h; si falta el archivo, escribe el nombre."""
    try:
        lg = Image.open(LOGO_PNG).convert('RGBA')
    except OSError:
        ImageDraw.Draw(im).text((x, y), 'comexplora', font=font('IBMPlexSans-SemiBold.ttf', round(h * 0.6)), fill=WHITE)
        return
    lg = lg.resize((round(lg.width * h / lg.height), h), Image.LANCZOS)
    im.paste(lg, (x, y), lg)


def day_label(it) -> str:
    try:
        d = datetime.fromisoformat(str(it.get('datetime') or it.get('date'))[:10])
        return f'{d.day} {MESES[d.month - 1]} {d.year}'
    except ValueError:
        return ''


def key_figure(it):
    """Primer dato clave de la nota (rótulo, valor), tal como lo cargó la redacción."""
    for row in it.get('keyData') or []:
        if isinstance(row, (list, tuple)) and len(row) == 2 and row[0] and row[1]:
            return str(row[0]), str(row[1])
    return None


def draw_card(it, category: str, host: str) -> Image.Image:
    im = Image.new('RGB', (W, H), NAVY)
    d = ImageDraw.Draw(im)
    # Fondo: banda inferior y trama sutil de contenedores a la derecha
    d.rectangle([0, H - 150, W, H], fill=NAVY_2)
    d.rectangle([0, 0, W, 8], fill=ACCENT)
    for row in range(3):
        for col in range(4):
            x0, y0 = W - 300 + col * 58 + (row % 2) * 29, 70 + row * 34
            d.rounded_rectangle([x0, y0, x0 + 50, y0 + 26], radius=3, outline=(30, 62, 104), width=2)

    # Marca
    logo(im, PAD, 46, 64)

    # Categoría e impacto en Argentina
    y = 168
    eb = font('IBMPlexSans-SemiBold.ttf', 21)
    cat = clip(d, category.upper(), eb, 560)
    d.text((PAD, y), cat, font=eb, fill=ACCENT)
    x = PAD + d.textlength(cat, font=eb) + 18
    if it.get('affectsArgentina'):
        chip = 'Impacto en Argentina'
        cf = font('IBMPlexSans-SemiBold.ttf', 17)
        cw = d.textlength(chip, font=cf)
        d.rounded_rectangle([x, y - 3, x + cw + 46, y + 28], radius=5, fill=(37, 64, 100))
        fx, fy = x + 10, y + 5
        for i, c in enumerate((CELESTE, WHITE, CELESTE)):
            d.rectangle([fx, fy + i * 6, fx + 20, fy + i * 6 + 5], fill=c)
        d.text((x + 38, y), chip, font=cf, fill=WHITE)

    # Título
    tf, lines = fit_title(d, it['title'], W - 2 * PAD)
    lh = round(tf.size * 1.16)
    ty = 214
    for ln in lines:
        d.text((PAD, ty), ln, font=tf, fill=WHITE)
        ty += lh

    # Banda inferior: dato clave (o fuente) + fecha y dirección del sitio
    by = H - 150 + 30
    kf = key_figure(it)
    right = f'{day_label(it)}  ·  {host}'
    rf = font('IBMPlexSans-Regular.ttf', 19)
    rw = d.textlength(right, font=rf)
    avail = W - 2 * PAD - rw - 40
    if kf:
        d.text((PAD, by), clip(d, kf[0].upper(), font('IBMPlexSans-SemiBold.ttf', 16), avail), font=font('IBMPlexSans-SemiBold.ttf', 16), fill=MUTED)
        vsize = 40
        vf = font('IBMPlexMono-Medium.ttf', vsize)
        while vsize > 24 and d.textlength(kf[1], font=vf) > avail:
            vsize -= 2
            vf = font('IBMPlexMono-Medium.ttf', vsize)
        d.text((PAD, by + 26), clip(d, kf[1], vf, avail), font=vf, fill=ACCENT)
    else:
        src = next((s for s in it.get('sources') or [] if s.get('primary')), (it.get('sources') or [{}])[0])
        d.text((PAD, by), 'FUENTE', font=font('IBMPlexSans-SemiBold.ttf', 16), fill=MUTED)
        d.text((PAD, by + 26), clip(d, src.get('name') or '', font('IBMPlexSans-SemiBold.ttf', 30), avail),
               font=font('IBMPlexSans-SemiBold.ttf', 30), fill=WHITE)
    d.text((W - PAD - rw, by + 40), right, font=rf, fill=SOFT)
    return im


def fingerprint(it, category: str, host: str) -> str:
    key = json.dumps([VERSION, it.get('title'), category, key_figure(it), it.get('affectsArgentina'),
                      it.get('date'), it.get('datetime'), host, it.get('sources', [{}])[:1]], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]


def build(items, category_of, host: str) -> dict:
    """Genera las imágenes que falten o hayan cambiado. Devuelve {id: '/img/og/<id>.png'}."""
    OUT.mkdir(parents=True, exist_ok=True)
    man_f = OUT / 'manifest.json'
    try:
        manifest = json.loads(man_f.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        manifest = {}
    limit = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).date().isoformat()
    out, new_manifest, made = {}, {}, 0
    for it in items:
        iid = it.get('id') or ''
        if not iid or '/' in iid or iid.startswith('.') or str(it.get('date') or '') < limit:
            continue
        cat = category_of(it)
        fp = fingerprint(it, cat, host)
        f = OUT / f'{iid}.png'
        if manifest.get(iid) != fp or not f.exists():
            draw_card(it, cat, host).quantize(colors=96, method=Image.Quantize.MEDIANCUT).save(f, 'PNG', optimize=True)
            made += 1
        new_manifest[iid] = fp
        out[iid] = f'/img/og/{iid}.png'
    removed = 0
    for f in OUT.glob('*.png'):
        if f.stem not in new_manifest:
            f.unlink()
            removed += 1
    man_f.write_text(json.dumps(new_manifest, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(f'Imágenes para redes: {len(out)} notas curadas · {made} nuevas o actualizadas · {removed} borradas')
    return out
