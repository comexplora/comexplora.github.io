#!/usr/bin/env python3
"""Intercambio comercial argentino (INDEC) para el panel de datos: data/trade.json.

Fuente: series de tiempo del INDEC publicadas en datos.gob.ar (catálogo «sspm»):
- Intercambio Comercial Argentino (exportaciones por rubro, importaciones por uso económico, saldo)
- Exportaciones por país y región
- Importaciones por país y región

La dirección de cada archivo se toma de la API de series (metadata=full), así que si el
catálogo la cambia el script la sigue. Si una fuente falla, se conserva el archivo anterior
(o la parte que sí se pudo actualizar): nunca se inventa un número. Lo corre el workflow.
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'trade.json'
UA = 'Mozilla/5.0 (compatible; ComexploraBot/1.1; +https://pulso-comex.github.io)'
API = 'https://apis.datos.gob.ar/series/api/series/?ids={}&last=1&metadata=full'
SOURCE_PAGE = 'https://www.indec.gob.ar/indec/web/Nivel4-Tema-3-2-40'
DATASETS = {   # serie conocida de cada conjunto → su archivo CSV
    'ica': '74.3_IEPP_0_M_35',
    'expo': '77.3_IET_0_A_25',
    'impo': '78.3_IIT_0_A_25',
}
FALLBACK_CSV = {
    'ica': 'https://infra.datos.gob.ar/catalog/sspm/dataset/74/distribution/74.3/download/intercambio-comercial-argentino-mensual.csv',
    'expo': 'https://infra.datos.gob.ar/catalog/sspm/dataset/77/distribution/77.3/download/exportaciones-por-paises-regiones-mensual.csv',
    'impo': 'https://infra.datos.gob.ar/catalog/sspm/dataset/78/distribution/78.3/download/importaciones-por-paises-regiones-mensual.csv',
}
MONTHS = 37   # tres años y un mes: alcanza para comparar contra el mismo mes del año anterior
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']

RUBROS = [   # exportaciones por gran rubro
    ('ica_exportacion_productos_primarios', 'Productos primarios'),
    ('ica_exportacion_manufacturas_origen_agropecuario', 'Manufacturas de origen agropecuario (MOA)'),
    ('ica_exportacion_manufacturas_origen_industrial', 'Manufacturas de origen industrial (MOI)'),
    ('ica_exportacion_combustible_energia', 'Combustibles y energía'),
]
USOS = [   # importaciones por uso económico
    ('ica_importaciones_bienes_capital', 'Bienes de capital'),
    ('ica_importaciones_bienes_intermedios', 'Bienes intermedios'),
    ('ica_importaciones_combustibles_lubricantes', 'Combustibles y lubricantes'),
    ('ica_importaciones_piezas_accesorios_bienes_capital', 'Piezas y accesorios para bienes de capital'),
    ('ica_importaciones_bienes_consumo', 'Bienes de consumo'),
    ('ica_importaciones_vehiculos_automotores_pasajeros', 'Vehículos automotores de pasajeros'),
    ('ica_importaciones_resto', 'Resto'),
]
NAMES = {'espana': 'España', 'japon': 'Japón', 'peru': 'Perú', 'mexico': 'México', 'canada': 'Canadá', 'belgica': 'Bélgica',
         'paises_bajos': 'Países Bajos', 'reino_unido': 'Reino Unido', 'estados_unidos': 'Estados Unidos', 'sudafrica': 'Sudáfrica',
         'nueva_zelanda': 'Nueva Zelanda', 'corea': 'Corea del Sur', 'autria': 'Austria', 'taiwan': 'Taiwán', 'argelia': 'Argelia',
         'marruecos': 'Marruecos', 'tailandia': 'Tailandia', 'israel': 'Israel', 'egipto': 'Egipto'}
BLOCS = {'total_mercosur': 'Mercosur', 'total_asia_pacifico': 'Asia Pacífico',
         'total_pacifico': 'Asia Pacífico', 'total_america_latina': 'América Latina', 'total_africa': 'África', 'asean': 'ASEAN',
         'medio_oriente': 'Medio Oriente', 'total_nafta': 'América del Norte (ex-NAFTA)', 'alianza_pacifico': 'Alianza del Pacífico',
         'total_alianza_pacifico': 'Alianza del Pacífico'}
SKIP = ('resto', 'total', 'totales', 'aladi', 'participacion', 'can', 'mercado_comun')


def fetch(url: str, accept='*/*', limit=20_000_000) -> str:
    req = Request(url, headers={'User-Agent': UA, 'Accept': accept})
    with urlopen(req, timeout=60) as r:
        return r.read(limit).decode('utf-8-sig', 'ignore')


def csv_url(key: str) -> str:
    try:
        meta = json.loads(fetch(API.format(quote(DATASETS[key])), 'application/json'))
        for m in meta.get('meta', []):
            url = (m.get('distribution') or {}).get('downloadURL')
            if url and url.endswith('.csv'):
                return url
    except Exception as e:  # la API puede fallar aunque el archivo esté disponible
        print(f'  aviso: metadatos de {key}: {str(e)[:120]}')
    return FALLBACK_CSV[key]


def load_rows(key: str) -> list[dict]:
    text = fetch(csv_url(key), 'text/csv')
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        t = (r.get('indice_tiempo') or '')[:7]
        if len(t) != 7:
            continue
        vals = {}
        for k, v in r.items():
            if k == 'indice_tiempo' or v in (None, ''):
                continue
            try:
                vals[k] = float(v)
            except ValueError:
                pass
        rows.append({'month': t, **vals})
    rows.sort(key=lambda r: r['month'])
    if not rows:
        raise ValueError('archivo vacío')
    return rows


def pretty(col: str, prefix: str) -> str:
    k = col[len(prefix):]
    return BLOCS.get(k) or NAMES.get(k) or k.replace('_', ' ').capitalize()


def month_label(m: str) -> str:
    y, mo = m.split('-')
    return f'{MESES[int(mo) - 1]} de {y}'


def ytd(rows: list, col: str, year: str, upto: str) -> float:
    mo = upto[5:]
    return sum(r.get(col, 0) for r in rows if r['month'][:4] == year and r['month'][5:] <= mo)


def pct(a, b):
    return round((a - b) / b * 100, 1) if b else None


def by_partner(rows: list, prefix: str, total_col: str, last: str):
    """Ranking de países (y bloques aparte) por acumulado del año, con el mismo período del año anterior."""
    y = last[:4]
    py = str(int(y) - 1)
    cols = [c for c in rows[-1] if c.startswith(prefix)]
    countries, blocs = [], []
    total = ytd(rows, total_col, y, last)
    for c in cols:
        k = c[len(prefix):]
        cur, prev = ytd(rows, c, y, last), ytd(rows, c, py, last)
        if cur <= 0:
            continue
        entry = {'name': pretty(c, prefix), 'ytd': round(cur, 1), 'prevYtd': round(prev, 1), 'change': pct(cur, prev),
                 'share': round(cur / total * 100, 1) if total else None, 'last': round(rows[-1].get(c, 0), 1)}
        if k in BLOCS:
            blocs.append(entry)
        elif not any(k == s or k.startswith(s + '_') for s in SKIP):
            countries.append(entry)
    countries.sort(key=lambda e: -e['ytd'])
    blocs.sort(key=lambda e: -e['ytd'])
    return countries[:12], blocs


def breakdown(rows: list, spec: list, total_col: str, last: str):
    y, py = last[:4], str(int(last[:4]) - 1)
    lastrow = rows[-1]
    same = next((r for r in rows if r['month'] == f'{py}-{last[5:]}'), {})
    total = ytd(rows, total_col, y, last)
    out = []
    for col, label in spec:
        cur = ytd(rows, col, y, last)
        out.append({'name': label, 'last': round(lastrow.get(col, 0), 1), 'lastChange': pct(lastrow.get(col, 0), same.get(col, 0)),
                    'ytd': round(cur, 1), 'ytdChange': pct(cur, ytd(rows, col, py, last)), 'share': round(cur / total * 100, 1) if total else None})
    return out


def main():
    old = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    data, report = {}, []
    for key in DATASETS:
        try:
            data[key] = load_rows(key)
            report.append((key, True, f"{len(data[key])} meses, último {data[key][-1]['month']}"))
        except Exception as e:
            report.append((key, False, str(e)[:160]))
    out = dict(old)
    if 'ica' in data:
        ica = [r for r in data['ica'] if 'ica_expo_totales' in r and 'ica_importaciones_totales' in r]
        last = ica[-1]['month']
        y, py = last[:4], str(int(last[:4]) - 1)
        same = next((r for r in ica if r['month'] == f'{py}-{last[5:]}'), {})
        L = ica[-1]
        e_ytd, i_ytd = ytd(ica, 'ica_expo_totales', y, last), ytd(ica, 'ica_importaciones_totales', y, last)
        pe_ytd, pi_ytd = ytd(ica, 'ica_expo_totales', py, last), ytd(ica, 'ica_importaciones_totales', py, last)
        out.update({
            'lastMonth': last, 'lastMonthLabel': month_label(last),
            'summary': {
                'expo': round(L['ica_expo_totales'], 1), 'impo': round(L['ica_importaciones_totales'], 1),
                'saldo': round(L['ica_expo_totales'] - L['ica_importaciones_totales'], 1),
                'expoChange': pct(L['ica_expo_totales'], same.get('ica_expo_totales')),
                'impoChange': pct(L['ica_importaciones_totales'], same.get('ica_importaciones_totales')),
                'expoYtd': round(e_ytd, 1), 'impoYtd': round(i_ytd, 1), 'saldoYtd': round(e_ytd - i_ytd, 1),
                'expoYtdChange': pct(e_ytd, pe_ytd), 'impoYtdChange': pct(i_ytd, pi_ytd), 'ytdLabel': f'enero–{MESES[int(last[5:]) - 1]} de {y}',
            },
            'monthly': [{'m': r['month'], 'expo': round(r['ica_expo_totales'], 1), 'impo': round(r['ica_importaciones_totales'], 1)}
                        for r in ica[-MONTHS:]],
            'rubros': breakdown(ica, RUBROS, 'ica_expo_totales', last),
            'usos': breakdown(ica, USOS, 'ica_importaciones_totales', last),
        })
    if 'expo' in data and out.get('lastMonth'):
        rows = [r for r in data['expo'] if r['month'] <= out['lastMonth']]
        out['destinos'], out['destinosBloques'] = by_partner(rows, 'ica_exportaciones_', 'ica_exportaciones_totales', rows[-1]['month'])
        out['destinosMonth'] = rows[-1]['month']
    if 'impo' in data and out.get('lastMonth'):
        rows = [r for r in data['impo'] if r['month'] <= out['lastMonth']]
        out['origenes'], out['origenesBloques'] = by_partner(rows, 'ica_importaciones_', 'ica_importaciones_totales', rows[-1]['month'])
        out['origenesMonth'] = rows[-1]['month']
    if any(ok for _, ok, _ in report):
        out.update({'source': 'INDEC · Intercambio comercial argentino (series de tiempo de datos.gob.ar)', 'sourceUrl': SOURCE_PAGE,
                    'units': 'millones de dólares (exportaciones FOB, importaciones CIF)',
                    'checkedAt': datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')})
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')

    for key, ok, msg in report:
        print(('  ✔ ' if ok else '  ✘ ') + f'INDEC {key}: {msg}')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write('\n## Intercambio comercial (INDEC)\n\n| Conjunto | Estado | Detalle |\n|---|---|---|\n')
            for key, ok, msg in report:
                f.write(f"| {key} | {'✔' if ok else '✘ (se conserva el dato anterior)'} | {msg} |\n")


if __name__ == '__main__':
    main()
