#!/usr/bin/env python3
"""Avisa a los buscadores que usan IndexNow (Bing, Yandex, Seznam, Naver…) qué páginas son nuevas o cambiaron.

Google no usa IndexNow: para Google están los sitemaps de Search Console.

Cómo funciona
- La clave es el archivo <clave>.txt de la raíz del sitio (32 caracteres hexadecimales, contenido = la clave).
  Prueba que el sitio es nuestro: si se borra o se cambia, los avisos dejan de valer.
- `python scripts/indexnow.py prepare` compara sitemap.xml con data/indexnow-state.json y arma la lista de
  direcciones nuevas o con fecha de modificación distinta. En GitHub Actions la deja como salida del paso
  (`payload`) para que el trabajo «Avisar a buscadores» la envíe DESPUÉS de publicar el sitio (si se avisa antes,
  el buscador puede encontrar la página todavía sin publicar). Actualiza el estado para no repetir avisos.
- `python scripts/indexnow.py submit` envía en el momento (uso manual).

No se avisan la portada ni las secciones: cambian en cada corrida y los buscadores las recorren igual.
Si el aviso falla, el sitio se publica igual (el trabajo es opcional).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'data' / 'indexnow-state.json'
ENDPOINT = 'https://api.indexnow.org/indexnow'
MAX_URLS = 10000            # límite de IndexNow por envío
SKIP = re.compile(r'^/(?:$|seccion/)')   # portada y secciones


def key():
    keys = [p.stem for p in ROOT.glob('*.txt') if re.fullmatch(r'[0-9a-f]{32}', p.stem)
            and p.read_text(encoding='utf-8').strip() == p.stem]
    return keys[0] if keys else None


def sitemap():
    xml = (ROOT / 'sitemap.xml').read_text(encoding='utf-8')
    return dict(re.findall(r'<url><loc>([^<]+)</loc>(?:<lastmod>([^<]*)</lastmod>)?', xml))


def pending():
    urls = sitemap()
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    out = []
    for u, mod in urls.items():
        path = '/' + u.split('://', 1)[-1].split('/', 1)[-1]
        if SKIP.match(path):
            continue
        if state.get(u) != mod:
            out.append(u)
            state[u] = mod
    state = {u: m for u, m in state.items() if u in urls}   # olvida las páginas que ya no existen
    return out[:MAX_URLS], state, urls


def payload(urls):
    k = key()
    host = urls[0].split('://', 1)[-1].split('/', 1)[0]
    base = urls[0].split('://', 1)[0] + '://' + host
    return {'host': host, 'key': k, 'keyLocation': f'{base}/{k}.txt', 'urlList': urls}


def main(mode):
    if not key():
        print('IndexNow: no hay archivo de clave en la raíz; no se avisa a nadie.')
        return
    todo, state, _ = pending()
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=0, sort_keys=True) + '\n', encoding='utf-8')
    if not todo:
        print('IndexNow: no hay páginas nuevas ni modificadas.')
        return
    body = json.dumps(payload(todo), ensure_ascii=False, separators=(',', ':'))
    print(f'IndexNow: {len(todo)} páginas para avisar.')
    if mode == 'prepare':
        out = os.environ.get('GITHUB_OUTPUT')
        if out:
            with open(out, 'a', encoding='utf-8') as f:
                f.write(f'payload={body}\n')
        return
    req = urllib.request.Request(ENDPOINT, data=body.encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json; charset=utf-8'})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f'IndexNow respondió {r.status}.')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'prepare')
