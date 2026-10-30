"""Arma el boletín semanal de Comexplora (HTML para MailerLite + texto plano) a partir de data/news.json.

Uso:
  python3 scripts/newsletter.py --out /ruta/carpeta [--date AAAA-MM-DD] [--intro "Texto de apertura"] [--days 7]

Genera en --out: boletin.html, boletin.txt y boletin.json (notas elegidas, asunto sugerido).
No envía nada: el HTML se carga como borrador en MailerLite y el dueño lo revisa y lo envía.
Solo usa notas de la redacción (curadas) y, como lista complementaria, resúmenes propios («Resumen»).
"""
import argparse
import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import deadlines  # noqa: E402  (misma carpeta: scripts/)

ROOT = Path(__file__).resolve().parents[1]
SITE = 'https://comexplora.github.io'
LOGO = f'{SITE}/img/brand/logo-email.png'
NAVY, BLUE, LIGHT, INK, MUTED, BG = '#0B2447', '#1766E3', '#5AA9FF', '#1B2433', '#5B6575', '#F3F5F8'
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
INDICATORS = ['arg-expo', 'arg-saldo', 'tc-mayorista', 'soja', 'wci', 'brent']

e = lambda s: html.escape(str(s or ''), quote=True)


def fecha_larga(d: date) -> str:
    return f'{d.day} de {MESES[d.month - 1]} de {d.year}'


def url(it, campaign):
    return f'{SITE}/noticias/{it["id"]}/?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}'


def pick(items, until: date, days: int):
    since = until - timedelta(days=days - 1)
    def in_window(i):
        try:
            return since <= date.fromisoformat(i['date'][:10]) <= until
        except Exception:
            return False
    cur = [i for i in items if not i.get('label') and in_window(i)]
    score = lambda i: (i.get('impact', 1) * 2 + (2 if i.get('affectsArgentina') else 0) + (1 if i.get('deadlines') else 0), i['date'])
    cur.sort(key=score, reverse=True)
    main, rest = cur[:5], sorted(cur[5:], key=lambda i: i['date'], reverse=True)[:8]
    digests = [i for i in items if i.get('label') == 'Automática' and (i.get('digest') or {}).get('lead') and in_window(i)]
    digests.sort(key=lambda i: (1 if (i.get('digest') or {}).get('flag') == 'curar' else 0, i.get('affectsArgentina', False), i['date']), reverse=True)
    return main, rest, digests[:5]


def upcoming(items, today: date, days=21):
    def keep(it, d):
        ds = str(d.get('date', ''))
        try:
            dd = date.fromisoformat(ds) if len(ds) == 10 else None
        except ValueError:
            return False
        return bool(dd and today <= dd <= today + timedelta(days=days))
    rows = deadlines.collect([i for i in items if not i.get('label')], keep)
    return [(date.fromisoformat(r[0]), r[1], r[2]) for r in rows][:6]


def build(until: date, intro: str, days: int):
    news = json.loads((ROOT / 'data' / 'news.json').read_text(encoding='utf-8'))
    items = news.get('items', [])
    campaign = f'boletin-{until.isoformat()}'
    main, rest, digests = pick(items, until, days)
    dates = upcoming(items, until)
    inds = {i['id']: i for i in news.get('indicators', [])}
    lead = main[0]['title'] if main else 'Comercio exterior'
    subject = f'Comexplora | {lead}'
    if len(subject) > 95:
        subject = subject[:92].rsplit(' ', 1)[0] + '…'

    td = f'font-family:Arial,Helvetica,sans-serif;color:{INK};'
    h = []
    h.append(f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>{e(subject)}</title></head><body style="margin:0;padding:0;background:{BG};">')
    h.append(f'<div style="display:none;max-height:0;overflow:hidden;">{e(main[0]["summary"][:140] if main else "")}</div>')
    h.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};"><tr><td align="center" style="padding:24px 12px;">')
    h.append(f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:6px;overflow:hidden;">')
    # cabecera
    h.append(f'<tr><td style="background:{NAVY};padding:22px 28px;">'
             f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
             f'<td><a href="{SITE}/?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}"><img src="{LOGO}" width="190" alt="Comexplora" style="display:block;border:0;width:190px;height:auto;"></a></td>'
             f'<td align="right" style="{td}color:#C9D6E8;font-size:13px;">Boletín semanal<br><b style="color:#ffffff;">{e(fecha_larga(until))}</b></td>'
             f'</tr></table></td></tr>')
    # apertura
    if intro:
        h.append(f'<tr><td style="padding:26px 28px 6px;{td}font-size:16px;line-height:1.55;">{e(intro)}</td></tr>')
    # notas principales
    h.append(f'<tr><td style="padding:18px 28px 0;{td}"><div style="font-size:12px;font-weight:bold;letter-spacing:1.5px;color:{BLUE};text-transform:uppercase;">Lo más importante de la semana</div></td></tr>')
    for n, it in enumerate(main):
        ai = (it.get('argentinaImpact') or {}).get('change') if it.get('affectsArgentina') else ''
        h.append(f'<tr><td style="padding:16px 28px;{td}border-bottom:1px solid #E6EAF0;">'
                 f'<a href="{e(url(it, campaign))}" style="font-size:19px;line-height:1.3;font-weight:bold;color:{NAVY};text-decoration:none;">{e(it["title"])}</a>'
                 f'<p style="margin:8px 0 0;font-size:15px;line-height:1.55;color:{INK};">{e(it.get("summary"))}</p>'
                 + (f'<p style="margin:8px 0 0;font-size:14px;line-height:1.5;color:{MUTED};"><b style="color:{INK};">Qué cambia para Argentina:</b> {e(ai)}</p>' if ai else '')
                 + f'<p style="margin:10px 0 0;font-size:14px;"><a href="{e(url(it, campaign))}" style="color:{BLUE};font-weight:bold;text-decoration:none;">Leer la nota completa →</a></p>'
                 f'</td></tr>')
    # indicadores
    rows = [inds[k] for k in INDICATORS if k in inds and inds[k].get('value')]
    if rows:
        cells = ''.join(f'<tr><td style="padding:7px 0;{td}font-size:14px;border-bottom:1px solid #E6EAF0;">{e(r["label"])}<br><span style="font-size:12px;color:{MUTED};">{e(r.get("period"))}</span></td>'
                        f'<td align="right" style="padding:7px 0;{td}font-size:15px;font-weight:bold;border-bottom:1px solid #E6EAF0;white-space:nowrap;">{e(r["value"])}<br><span style="font-size:12px;font-weight:normal;color:{MUTED};">{e(r.get("change"))}</span></td></tr>' for r in rows)
        h.append(f'<tr><td style="padding:22px 28px 6px;{td}"><div style="font-size:12px;font-weight:bold;letter-spacing:1.5px;color:{BLUE};text-transform:uppercase;margin-bottom:6px;">Indicadores</div>'
                 f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{cells}</table>'
                 f'<p style="margin:8px 0 0;font-size:13px;"><a href="{SITE}/datos/?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}" style="color:{BLUE};text-decoration:none;">Ver todos los datos →</a></p></td></tr>')
    # agenda
    if dates:
        lis = ''.join(f'<tr><td valign="top" style="padding:6px 12px 6px 0;{td}font-size:14px;font-weight:bold;white-space:nowrap;color:{NAVY};">{d.day} {MESES[d.month - 1][:3]}</td>'
                      f'<td style="padding:6px 0;{td}font-size:14px;line-height:1.45;">{e(lbl)}</td></tr>' for d, lbl, _ in dates)
        h.append(f'<tr><td style="padding:22px 28px 6px;{td}"><div style="font-size:12px;font-weight:bold;letter-spacing:1.5px;color:{BLUE};text-transform:uppercase;margin-bottom:6px;">Próximas fechas</div>'
                 f'<table role="presentation" cellpadding="0" cellspacing="0">{lis}</table>'
                 f'<p style="margin:8px 0 0;font-size:13px;"><a href="{SITE}/agenda/?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}" style="color:{BLUE};text-decoration:none;">Ver la agenda completa →</a></p></td></tr>')
    # otras notas
    others = rest + digests
    if others:
        lis = ''.join(f'<li style="margin:0 0 8px;"><a href="{e(url(i, campaign))}" style="color:{NAVY};text-decoration:none;">{e(i["title"])}</a>'
                      + (f' <span style="color:{MUTED};font-size:12px;">· Resumen</span>' if i.get('label') == 'Automática' else '') + '</li>' for i in others)
        h.append(f'<tr><td style="padding:22px 28px 6px;{td}"><div style="font-size:12px;font-weight:bold;letter-spacing:1.5px;color:{BLUE};text-transform:uppercase;margin-bottom:8px;">También en Comexplora</div>'
                 f'<ul style="margin:0;padding:0 0 0 18px;font-size:14px;line-height:1.45;">{lis}</ul></td></tr>')
    # cierre
    h.append(f'<tr><td align="center" style="padding:26px 28px;"><a href="{SITE}/?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}" '
             f'style="display:inline-block;background:{BLUE};color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:bold;text-decoration:none;padding:12px 22px;border-radius:4px;">Ir a Comexplora</a></td></tr>')
    h.append(f'<tr><td style="background:{NAVY};padding:20px 28px;{td}color:#C9D6E8;font-size:12px;line-height:1.6;">'
             f'Comexplora · Noticias, datos y análisis de comercio exterior, con foco en Argentina y Latinoamérica. Cada nota enlaza sus fuentes.<br>'
             f'Recibís este correo porque te suscribiste en comexplora.github.io. '
             f'<a href="{{$unsubscribe}}" style="color:{LIGHT};">Darte de baja</a> · <a href="{{$url}}" style="color:{LIGHT};">Ver en el navegador</a></td></tr>')
    h.append('</table></td></tr></table></body></html>')

    t = [f'COMEXPLORA · Boletín semanal · {fecha_larga(until)}', '']
    if intro:
        t += [intro, '']
    t += ['LO MÁS IMPORTANTE DE LA SEMANA', '']
    for it in main:
        t += [it['title'], it.get('summary', ''), 'Leer: ' + url(it, campaign), '']
    if rows:
        t += ['INDICADORES'] + [f'- {r["label"]}: {r["value"]} ({r.get("change", "")}) · {r.get("period", "")}' for r in rows] + ['']
    if dates:
        t += ['PRÓXIMAS FECHAS'] + [f'- {d.day} {MESES[d.month - 1][:3]}: {lbl}' for d, lbl, _ in dates] + ['']
    if others:
        t += ['TAMBIÉN EN COMEXPLORA'] + [f'- {i["title"]}: {url(i, campaign)}' for i in others] + ['']
    t += ['Darte de baja: {$unsubscribe}']
    meta = {'subject': subject, 'campaign': campaign, 'main': [i['id'] for i in main],
            'others': [i['id'] for i in others], 'dates': [(d.isoformat(), l) for d, l, _ in dates]}
    return ''.join(h), '\n'.join(t), meta


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--date', default=date.today().isoformat())
    ap.add_argument('--intro', default='')
    ap.add_argument('--days', type=int, default=7)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    html_s, txt, meta = build(date.fromisoformat(a.date), a.intro, a.days)
    (out / 'boletin.html').write_text(html_s, encoding='utf-8')
    (out / 'boletin.txt').write_text(txt, encoding='utf-8')
    (out / 'boletin.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(meta, ensure_ascii=False, indent=1))
