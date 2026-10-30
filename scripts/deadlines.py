"""Fechas de agenda (`deadlines` de las notas curadas) sin repetidos.

Varias notas pueden cargar el mismo evento con otro texto («INDEC: intercambio comercial de
octubre» / «INDEC publica el intercambio…»). Se unen si tienen la misma fecha y comparten al
menos el 60 % de las raíces de palabras (5 letras) del texto más corto. Queda el texto de la
nota más reciente. Es la misma regla que `dedupeDeadlines` en assets/app.js.
"""
from __future__ import annotations

import re
import unicodedata

STOP = set(('de del la las el los en con por para a al y o u e un una que se su sus sobre desde hasta entre como mas ya '
            'es son lo le les rige empieza empiezan publica vence nueva nuevo nuevas nuevos primer primera segun fecha dia').split())


def roots(label: str) -> set[str]:
    s = unicodedata.normalize('NFKD', str(label).lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.replace('vmos', 'vaca muerta oil sur').replace('crudo', 'petroleo')
    return {w[:5] for w in re.findall(r'[a-z0-9]+', s) if len(w) > 1 and w not in STOP}


def same(date_a, label_a, date_b, label_b) -> bool:
    if str(date_a) != str(date_b):
        return False
    a, b = roots(label_a), roots(label_b)
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.6


def item_time(it) -> str:
    return str(it.get('datetime') or it.get('date') or '')


def collect(items, keep=lambda it, d: True):
    """Lista de (fecha, texto, nota, d) sin repetidos, ordenada por fecha."""
    rows = []
    for it in sorted(items, key=item_time, reverse=True):
        for d in it.get('deadlines') or []:
            if not d.get('date') or not d.get('label') or not keep(it, d):
                continue
            if any(same(d['date'], d['label'], r[0], r[1]) for r in rows):
                continue
            rows.append((str(d['date']), d['label'], it, d))
    rows.sort(key=lambda r: r[0])
    return rows
