#!/usr/bin/env python3
"""Descarga una copia local de las fotos de archivo (data/photos.json) en img/stock/.

Así las fotos se sirven desde el propio sitio y no dependen de servicios externos.
Fuentes admitidas (solo licencias que no requieren permiso):
- Unsplash (licencia Unsplash: uso libre, también comercial). Se mantiene el crédito al autor.
- Wikimedia Commons, solo archivos de dominio público o CC0, sin restricciones (derechos de imagen, marcas,
  insignias). Cada entrada guarda "license" y el enlace a su página de descripción en "page".
Se ejecuta en cada corrida del workflow, pero solo descarga lo que falta.
Las fotos que ya no existen en Unsplash quedan marcadas en img/stock/status.json y build_pages.py las omite.
"""
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'img' / 'stock'
OUT.mkdir(parents=True, exist_ok=True)
STATUS = OUT / 'status.json'
SIZES = {'1600': 1600, '800': 800}


def download(url: str) -> bytes:
    # Wikimedia pide un User-Agent que identifique al sitio y un contacto.
    req = Request(url, headers={'User-Agent': 'ComexploraBot/1.2 (https://pulso-comex.github.io; pulso.comex26@gmail.com)'})
    with urlopen(req, timeout=30) as r:
        return r.read(40_000_000)


def fetch_variant(p: dict, w: int) -> bytes:
    if 'images.unsplash.com' in p['src']:
        return download(f"{p['src']}?auto=format&fit=crop&w={w}&q=72&fm=jpg")
    # Wikimedia Commons u otra fuente: se baja una vez y se achica acá (requiere Pillow).
    from io import BytesIO
    from PIL import Image
    key = p['src']
    if key not in _ORIG:
        try:
            raw = download(p['src'])
        except Exception:
            if not p.get('orig'):
                raise
            raw = download(p['orig'])   # si la miniatura no está disponible, el original
        _ORIG[key] = Image.open(BytesIO(raw)).convert('RGB')
    im = _ORIG[key].copy()
    if im.width > w:
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    out = BytesIO()
    im.save(out, 'JPEG', quality=80, optimize=True, progressive=True)
    return out.getvalue()


_ORIG = {}


def main():
    bank = json.loads((ROOT / 'data' / 'photos.json').read_text(encoding='utf-8'))
    status = json.loads(STATUS.read_text(encoding='utf-8')) if STATUS.exists() else {}
    got = failed = 0
    for photos in bank.values():
        for p in photos:
            pid = p['id']
            for label, w in SIZES.items():
                f = OUT / f'{pid}-{label}.jpg'
                if f.exists() and f.stat().st_size > 5000:
                    continue
                try:
                    data = fetch_variant(p, w)
                    if not data.startswith(b'\xff\xd8') or len(data) < 5000:
                        raise ValueError('la respuesta no es una imagen JPEG')
                    f.write_bytes(data)
                    got += 1
                    status[pid] = 'ok'
                except Exception as e:
                    failed += 1
                    status[pid] = 'missing' if '404' in str(e) else status.get(pid, 'error')
                    print(f'  ✘ {pid} ({w}px): {e}', file=sys.stderr)
                    break
            else:
                status[pid] = 'ok'
    STATUS.write_text(json.dumps(status, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'Fotos de archivo: {got} descargadas, {failed} con error, {sum(1 for v in status.values() if v == "ok")} disponibles.')


if __name__ == '__main__':
    main()
