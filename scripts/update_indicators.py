#!/usr/bin/env python3
"""Actualiza los indicadores de mercado de data/news.json desde fuentes oficiales (sin dependencias externas).

Indicadores (por id en "indicators"):
- tc-mayorista: tipo de cambio mayorista de referencia (Com. A 3500), API de estadísticas del BCRA.
- soja, maiz, trigo: precio pizarra en Rosario, Cámara Arbitral de Cereales de la Bolsa de Comercio de Rosario.
- brent: petróleo Brent spot (serie de la EIA de EE.UU.), publicada por FRED (Reserva Federal de St. Louis).
  La EIA la actualiza una vez por semana (miércoles); por eso el último dato puede tener hasta ~9 días.
  No hay fuente diaria gratuita y que permita lectura automática (Yahoo y Stooq la prohíben en su robots.txt).

Si una fuente no responde o cambia su formato, el indicador conserva el último valor válido
(o queda como "Sin datos" si nunca tuvo uno): nunca se inventa un número.
Lo corre el workflow después de scripts/update_news.py.
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
NEWS = ROOT / 'data' / 'news.json'
UA = 'Mozilla/5.0 (compatible; ComexploraBot/1.1; +https://comexplora.github.io)'
MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

BCRA_URL = 'https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/5?limit=40'
HISTORY_MAX = 30   # puntos guardados por indicador para el minigráfico de tendencia
BCRA_PAGE = 'https://www.bcra.gob.ar/PublicacionesEstadisticas/Principales_variables.asp'
BCR_URL = 'https://www.cac.bcr.com.ar/es/precios-de-pizarra'
FRED_CSV = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU'
FRED_PAGE = 'https://fred.stlouisfed.org/series/DCOILBRENTEU'


def fetch(url: str, accept: str = '*/*') -> str:
    req = Request(url, headers={'User-Agent': UA, 'Accept': accept, 'Accept-Language': 'es-AR,es;q=0.9,en;q=0.6'})
    with urlopen(req, timeout=30) as r:
        return r.read(3_000_000).decode('utf-8', 'ignore')


# ---------------------------------------------------------------- formato es-AR
def num_es(x: float, dec: int) -> str:
    s = f'{abs(x):,.{dec}f}'.replace(',', '§').replace('.', ',').replace('§', '.')
    return ('-' if x < 0 else '') + s


def parse_es(s: str) -> float:
    return float(s.replace('.', '').replace(',', '.'))


def day_es(d: date) -> str:
    return f'{d.day} {MESES[d.month - 1]} {d.year}'


def change_fields(cur: float, prev: float | None, label: str) -> dict:
    if not prev:
        return {'change': '', 'trend': 'flat'}
    pct = (cur - prev) / prev * 100
    trend = 'up' if pct >= 0.05 else 'down' if pct <= -0.05 else 'flat'
    if round(pct, 1) == 0:
        return {'change': f'0,0% {label}', 'trend': 'flat'}
    sign = '+' if pct > 0 else ''
    return {'change': f'{sign}{num_es(pct, 1)}% {label}', 'trend': trend}


# ---------------------------------------------------------------- fuentes
def bcra_tc() -> dict:
    data = json.loads(fetch(BCRA_URL, 'application/json'))
    det = sorted(data['results'][0]['detalle'], key=lambda x: x['fecha'], reverse=True)
    cur, prev = det[0], det[1] if len(det) > 1 else None
    d = date.fromisoformat(cur['fecha'][:10])
    v = float(cur['valor'])
    if not 10 < v < 1_000_000:
        raise ValueError(f'valor fuera de rango: {v}')
    return {'value': f'$ {num_es(v, 2)}', 'period': f'Com. A 3500 · {day_es(d)}', 'obsDate': d.isoformat(),
            **change_fields(v, float(prev['valor']) if prev else None, 'vs. día hábil anterior'),
            'source': 'BCRA', 'url': BCRA_PAGE, 'num': v,
            'series': [(x['fecha'][:10], float(x['valor'])) for x in det if 10 < float(x['valor']) < 1_000_000]}


_BCR_TEXT = {}


def bcr_text() -> str:
    if 't' not in _BCR_TEXT:   # una sola descarga para los tres granos
        page = fetch(BCR_URL, 'text/html')
        text = html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<(script|style)[\s\S]*?</\1>', ' ', page, flags=re.I)))
        _BCR_TEXT['t'] = re.sub(r'\s+', ' ', text)
    return _BCR_TEXT['t']


def bcr_grain(name: str, label: str):
    def get(old: dict) -> dict:
        return bcr_price(old, name, label)
    return get


def bcr_price(old: dict, name: str, label: str) -> dict:
    text = bcr_text()
    m = re.search(name + r'\s*(?:S/C\s*)?(\(E\)\s*)?\$\s*([\d.]+,\d{2})\s*US\$\s*(\(E\)\s*)?([\d.]+,\d{2})', text)
    if not m:
        raise ValueError(f'no se encontró el precio de {label} en la página')
    ars, usd = parse_es(m.group(2)), parse_es(m.group(4))
    est = bool(m.group(1) or m.group(3))
    if not 1_000 < ars < 100_000_000 or not 30 < usd < 5_000:
        raise ValueError(f'valores fuera de rango: {ars} / {usd}')
    dm = re.search(r'Precios? Pizarra del d[ií]a\s*(\d{2})/(\d{2})/(\d{4})', text, re.I)
    d = date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1))) if dm else None
    if not d:
        raise ValueError('no se encontró la fecha de la pizarra')
    # Variación contra la pizarra anterior guardada (la página solo muestra el día).
    prev = old.get('_prev') or {}
    base = old.get('_last') or {}
    if base.get('date') and base.get('date') != d.isoformat():
        prev = base
    out = {'value': f'$ {num_es(ars, 0)}/t', 'period': f'Rosario · {day_es(d)} · US$ {num_es(usd, 2)}/t' + (' (estimado)' if est else ''),
           'obsDate': d.isoformat(), **change_fields(ars, prev.get('ars'), 'vs. pizarra anterior'),
           'source': 'Bolsa de Comercio de Rosario', 'url': BCR_URL,
           '_last': {'date': d.isoformat(), 'ars': ars}, '_prev': prev or None, 'num': ars}
    return out


def fred_brent() -> dict:
    rows = [r for r in csv.reader(io.StringIO(fetch(FRED_CSV, 'text/csv'))) if len(r) >= 2]
    obs = []
    for r in rows[1:]:
        try:
            obs.append((date.fromisoformat(r[0]), float(r[1])))
        except ValueError:
            continue  # FRED usa "." para los días sin dato
    if len(obs) < 2:
        raise ValueError('serie vacía')
    (d, v), (_, pv) = obs[-1], obs[-2]
    if not 5 < v < 500:
        raise ValueError(f'valor fuera de rango: {v}')
    # La EIA publica la serie diaria una vez por semana (los miércoles), con unos días de demora: se aclara en el período.
    return {'value': f'USD {num_es(v, 2)}', 'period': f'Barril, spot · {day_es(d)} · la EIA publica los miércoles', 'obsDate': d.isoformat(),
            **change_fields(v, pv, 'vs. día anterior'), 'source': 'EIA (vía FRED)', 'url': FRED_PAGE,
            'num': v, 'series': [(x.isoformat(), y) for x, y in obs[-HISTORY_MAX:]]}


SOURCES = {
    'tc-mayorista': ('Tipo de cambio mayorista ARS/USD', lambda old: bcra_tc()),
    'soja': ('Soja · precio pizarra Rosario', bcr_grain(r'Soja', 'la soja')),
    'maiz': ('Maíz · precio pizarra Rosario', bcr_grain(r'Ma[ií]z', 'el maíz')),
    'trigo': ('Trigo · precio pizarra Rosario', bcr_grain(r'Trigo', 'el trigo')),
    'brent': ('Petróleo Brent', lambda old: fred_brent()),
}
# Frecuencia del dato (la página la muestra y la usa para avisar si el último dato quedó viejo).
# maxAgeDays: días desde la fecha de referencia a partir de los cuales el dato se marca como desactualizado.
FREQUENCY = {
    'tc-mayorista': ('diaria', 6), 'soja': ('diaria', 6), 'maiz': ('diaria', 6), 'trigo': ('diaria', 6),
    'brent': ('diaria', 14),   # la EIA publica una vez por semana, con demora
}


def merge_history(old: dict, new: dict) -> None:
    """Serie corta [[fecha, valor], …] para el minigráfico: solo datos publicados por la fuente, uno por fecha."""
    pts = {d: v for d, v in (old.get('history') or []) if isinstance(d, str) and isinstance(v, (int, float))}
    for d, v in new.pop('series', None) or []:
        pts[d] = round(v, 4)
    num = new.pop('num', None)
    if num is not None and new.get('obsDate'):
        pts[new['obsDate']] = round(num, 4)
    if pts:
        old['history'] = [[d, pts[d]] for d in sorted(pts)][-HISTORY_MAX:]


def main():
    store = json.loads(NEWS.read_text(encoding='utf-8'))
    inds = store.get('indicators', [])
    by_id = {i.get('id'): i for i in inds}
    report = []
    for ind_id, (label, getter) in SOURCES.items():
        old = by_id.get(ind_id)
        if old is None:
            old = {'id': ind_id, 'label': label, 'value': None, 'group': 'Mercados'}
            inds.append(old)
            by_id[ind_id] = old
        freq, max_age = FREQUENCY.get(ind_id, ('diaria', 6))
        old.update({'frequency': freq, 'maxAgeDays': max_age})
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        old['lastAttempt'] = now
        try:
            new = getter(old)
        except Exception as e:  # una fuente caída no frena a las demás ni borra el último dato
            old['lastError'] = str(e)[:160]   # la página avisa que se muestra el último dato válido
            report.append((ind_id, False, str(e)[:160]))
            continue
        old.pop('lastError', None)
        merge_history(old, new)
        old.update({k: v for k, v in new.items() if v is not None or k == '_prev'})
        if old.get('_prev') is None:
            old.pop('_prev', None)
        old['label'] = label
        old.pop('pending', None)
        old['checkedAt'] = now
        report.append((ind_id, True, f"{old['value']} · {old['period']}"))
    store['indicators'] = inds
    NEWS.write_text(json.dumps(store, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    for ind_id, ok, msg in report:
        print(('  ✔ ' if ok else '  ✘ ') + f'{ind_id}: {msg}')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write('## Indicadores de mercado\n\n| Indicador | Estado | Detalle |\n|---|---|---|\n')
            for ind_id, ok, msg in report:
                f.write(f"| {ind_id} | {'✔' if ok else '✘ (se conserva el último dato)'} | {msg} |\n")


if __name__ == '__main__':
    main()
