#!/usr/bin/env python3
"""Genera el sitio estático de Comexplora a partir de templates/page.html y data/news.json.

Salidas:
- index.html                 portada (con las últimas notas embebidas como respaldo)
- noticias/<id>/index.html   una página liviana por nota, con su propio título, descripción,
                             imagen para redes (Open Graph) y texto visible sin JavaScript
- data/latest.json           feed que lee la página (notas recientes, con tope de tamaño)
- seccion/<slug>/, tema/<id>/ y las herramientas (datos/, agenda/, glosario/, calculadora-importacion/…):
                             páginas con dirección propia, contenido visible sin JavaScript y metadatos propios
- sitemap.xml, news-sitemap.xml, feed.xml
También borra las carpetas de /noticias/ cuyas notas ya no están en el archivo.

Para cambiar el diseño o la estructura, editá templates/page.html, assets/app.css o assets/app.js
y volvé a ejecutar este script (el workflow lo hace solo). No edites index.html a mano.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from taxonomy import Taxonomy, norm  # noqa: E402
try:   # imágenes para redes de las notas curadas (requiere Pillow; sin él se usa la foto de archivo)
    import og_images  # noqa: E402
except ImportError as e:
    og_images = None
    print(f'Aviso: sin imágenes para redes ({e}). Instalá Pillow para generarlas.')

ROOT = Path(__file__).resolve().parents[1]
# Dirección pública del sitio. El workflow la detecta sola desde GitHub Pages (github.io o dominio propio).
def detect_site() -> str:
    """Dirección pública del sitio, sin configurar nada:
    1) SITE_URL si el workflow la pasa; 2) dominio propio del archivo CNAME;
    3) la dirección de GitHub Pages según el repositorio (usuario.github.io o usuario.github.io/repo)."""
    if os.environ.get('SITE_URL'):
        return os.environ['SITE_URL']
    cname = ROOT / 'CNAME'
    if cname.exists() and cname.read_text(encoding='utf-8').strip():
        return 'https://' + cname.read_text(encoding='utf-8').strip().split()[0]
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if '/' in repo:
        owner, name = repo.split('/', 1)
        owner = owner.lower()
        return f'https://{owner}.github.io' if name.lower() == f'{owner}.github.io' else f'https://{owner}.github.io/{name}'
    return 'https://comexplora.github.io'


SITE = detect_site().rstrip('/')
SITE_NAME = 'Comexplora'
TZ = timezone(timedelta(hours=-3))
NOW = datetime.now(timezone.utc)
INDEX_AUTOMATIC = False   # las notas automáticas (solo enlazan a otro medio) no se indexan en buscadores
LATEST_MAX = 400          # notas incluidas en data/latest.json
INLINE_MAX = 40           # notas embebidas en la portada como respaldo
HOME_TITLE = 'Comexplora · Noticias de Comercio Exterior'
HOME_DESC = ('Portal de noticias de comercio exterior: aranceles, aduanas, acuerdos comerciales, logística, puertos e '
             'indicadores, con foco en Argentina y el Mercosur. Cada nota enlaza a su fuente original.')
OG_DEFAULT = f'{SITE}/og-default.png'

esc = lambda s: html.escape(str(s or ''), quote=True)


def load_site_config():
    """site.json: correo, responsable, Google Analytics, verificación de Search Console, newsletter y redes."""
    f = ROOT / 'site.json'
    try:
        cfg = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    except ValueError as e:
        print(f'Aviso: site.json tiene un error de formato y se ignora ({e}).')
        cfg = {}
    cfg.pop('_ayuda', None)
    email = str(cfg.get('contactEmail') or '').strip()
    cfg['contactEmail'] = email if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) else ''
    ga = str(cfg.get('googleAnalyticsId') or '').strip().upper()
    cfg['googleAnalyticsId'] = ga if re.fullmatch(r'G-[A-Z0-9]{4,20}', ga) else ''
    ver = str(cfg.get('googleSiteVerification') or '').strip()
    m = re.search(r'content=["\']([^"\']+)["\']', ver)   # acepta la etiqueta completa o solo el código
    cfg['googleSiteVerification'] = re.sub(r'[^A-Za-z0-9_\-]', '', m.group(1) if m else ver)
    nl = cfg.get('newsletter') or {}
    cfg['newsletter'] = {k: str(nl.get(k) or '').strip() for k in ('formAction', 'url')
                         if str(nl.get(k) or '').strip().startswith('https://')}
    cfg['redes'] = [r for r in (cfg.get('redes') or []) if str(r.get('url') or '').startswith('https://')]
    resp = cfg.get('responsable') or {}
    cfg['responsable'] = {k: str(resp.get(k) or '').strip() for k in ('nombre', 'rol', 'descripcion', 'linkedin')}
    return cfg


SITECFG = load_site_config()
STATUS = {}   # textos fijos que la página muestra antes de que cargue el JavaScript (fecha del feed, año)


def parse_date(v):
    if not v:
        return None
    v = str(v)
    try:
        dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc)


def item_dt(it):
    return parse_date(it.get('datetime')) or parse_date(it.get('date')) or NOW


def js_hash(s: str) -> int:
    """Mismo hash que assets/app.js, para que la foto de la página y la de redes coincidan."""
    h = 0
    for ch in s:
        cp = ord(ch)
        unit = cp if cp < 0x10000 else 0xD800 + ((cp - 0x10000) >> 10)  # c.charCodeAt(0) en JS
        h = (h * 31 + unit) & 0xFFFFFFFF
    if h >= 2 ** 31:
        h -= 2 ** 32
    return abs(h)


def json_script(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')


def load():
    news = json.loads((ROOT / 'data' / 'news.json').read_text(encoding='utf-8'))
    trade_f = ROOT / 'data' / 'trade.json'   # intercambio comercial (INDEC), lo genera scripts/update_trade.py
    if trade_f.exists():
        news['trade'] = json.loads(trade_f.read_text(encoding='utf-8'))
    bank = json.loads((ROOT / 'data' / 'photos.json').read_text(encoding='utf-8'))
    status_f = ROOT / 'img' / 'stock' / 'status.json'
    status = json.loads(status_f.read_text(encoding='utf-8')) if status_f.exists() else {}
    front = {}
    for k, photos in bank.items():
        out = []
        for p in photos:
            if status.get(p['id']) == 'missing':
                continue
            q = {x: p[x] for x in ('src', 'by', 'page', 'alt', 'license') if p.get(x)}
            big, small = ROOT / 'img/stock' / f"{p['id']}-1600.jpg", ROOT / 'img/stock' / f"{p['id']}-800.jpg"
            if big.exists() and small.exists():
                q['local'] = f"/img/stock/{p['id']}-1600.jpg"
                q['localSmall'] = f"/img/stock/{p['id']}-800.jpg"
            out.append(q)
        if out:
            front[k] = out
    return news, front


USE_SOURCE_IMAGES = False   # fotos de los medios desactivadas por derechos de autor (igual que assets/app.js)


def photo_for(it, bank):
    p = it.get('photo') or {}
    if USE_SOURCE_IMAGES and p.get('src'):
        return p['src'], p.get('alt') or it['title'], True
    photos = bank.get(it.get('visual')) or bank.get('globe') or []
    if not photos:
        return OG_DEFAULT, SITE_NAME, False
    q = photos[js_hash(it['id']) % len(photos)]
    if q.get('local'):
        src = SITE + q['local']
    elif 'images.unsplash.com' in q['src']:
        src = f"{q['src']}?auto=format&fit=crop&w=1200&h=630&q=70&fm=jpg"
    else:
        src = q['src']
    return src, q.get('alt', ''), False


def primary(it):
    srcs = it.get('sources') or [{}]
    return next((s for s in srcs if s.get('primary')), srcs[0])


def category(it):
    return next((t for t in it.get('topics', []) if t != 'Argentina'), (it.get('topics') or ['Comercio exterior'])[0])


def is_auto(it):
    return it.get('label') == 'Automática'


def meta_block(*, title, desc, url, image, image_alt, og_type='website', robots='index,follow,max-image-preview:large',
               ld=None, extra='', image_size=None, base=''):
    lines = [
        f'<title>{esc(title)}</title>',
        f'<meta name="description" content="{esc(desc)}">',
        f'<meta name="robots" content="{robots}">',
        '<meta name="author" content="Comexplora">',
        '<meta name="application-name" content="Comexplora">',
        '<meta name="referrer" content="strict-origin-when-cross-origin">',
        f'<link rel="icon" href="{base}favicon.ico" sizes="32x32">',
        f'<link rel="icon" href="{base}favicon.svg" type="image/svg+xml">',
        f'<link rel="apple-touch-icon" href="{base}apple-touch-icon.png">',
        f'<link rel="alternate" type="application/rss+xml" title="Comexplora · Noticias" href="{base}feed.xml">',
        '<meta name="theme-color" content="#0B2545">',
        f'<link rel="canonical" id="canonical" href="{esc(url)}">',
        f'<meta property="og:type" content="{og_type}">',
        '<meta property="og:site_name" content="Comexplora">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(desc)}">',
        '<meta property="og:locale" content="es_AR">',
        f'<meta property="og:image" content="{esc(image)}">',
        f'<meta property="og:image:alt" content="{esc(image_alt)}">',
    ]
    if image_size:
        lines += [f'<meta property="og:image:width" content="{image_size[0]}">', f'<meta property="og:image:height" content="{image_size[1]}">']
    lines += [
        f'<meta property="og:url" content="{esc(url)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{esc(title)}">',
        f'<meta name="twitter:description" content="{esc(desc)}">',
        f'<meta name="twitter:image" content="{esc(image)}">',
    ]
    if SITECFG.get('googleSiteVerification'):
        lines.append(f'<meta name="google-site-verification" content="{esc(SITECFG["googleSiteVerification"])}">')
    ga = SITECFG.get('googleAnalyticsId')
    if ga:  # las páginas vistas se envían desde assets/app.js en cada cambio de sección
        lines.append(f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga}"></script>')
        lines.append("<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}"
                     f"gtag('js',new Date());gtag('config','{ga}',{{send_page_view:false}});</script>")
    if extra:
        lines.append(extra)
    lines.append(f'<script type="application/ld+json" id="ld">{json_script(ld or {})}</script>')
    return '\n'.join(lines)


def fmt_day(dt):
    meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
    d = dt.astimezone(TZ)
    return f'{d.day} de {meses[d.month - 1]} de {d.year}'


def explainer_html(it):
    x = it.get('explainer') or {}
    rows = [('Qué pasó', x.get('what')), ('Por qué importa', x.get('why')), ('A quién afecta', x.get('who')),
            ('Productos involucrados', ', '.join(x.get('products') or [])), ('Qué puede pasar ahora', x.get('next'))]
    rows = [(k, v) for k, v in rows if v]
    if not rows:
        return ''
    return '<section><h2>COMEX explicado</h2><dl>' + ''.join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k, v in rows) + '</dl></section>'


def prerender_article(it, img, img_alt, source_img):
    p = primary(it)
    body = it.get('body') or ([it['summary']] if it.get('summary') else [])
    credit = f'Imagen: {esc(p.get("name"))}' if source_img else 'Foto de archivo ilustrativa'
    return f'''    <article class="pre-article">
      <p class="pre-meta"><a href="../../">Noticias</a> · {esc(category(it))} · <time datetime="{esc(it.get('datetime') or it.get('date'))}">{fmt_day(item_dt(it))}</time></p>
      <h1>{esc(it['title'])}</h1>
      {f'<p class="lede">{esc(it["summary"])}</p>' if it.get('summary') else ''}
      <p class="pre-meta">Fuente: <a href="{esc(p.get('url'))}" rel="noopener noreferrer">{esc(p.get('name'))}</a></p>
      <p class="pre-meta">{'Nota automática: título y extracto del feed de la fuente, sin revisión de la redacción.' if is_auto(it) else 'Nota de la redacción, verificada contra las fuentes enlazadas.'}</p>
      <img src="{esc(img)}" alt="{esc(img_alt)}" width="1200" height="675"{' referrerpolicy="no-referrer"' if source_img else ''}>
      <p class="pre-meta">{credit}</p>
      {explainer_html(it)}
      {''.join(f'<p>{esc(x)}</p>' for x in body)}
      <p><a href="{esc(p.get('url'))}" rel="noopener noreferrer">Leer el original en {esc(p.get('name'))}</a></p>
    </article>
'''


def feed_payload(news, items):
    return {k: news.get(k) for k in ('schemaVersion', 'feedId', 'updatedAt', 'timezone', 'editorialNote', 'indicators', 'stories', 'trade') if k in news} | {'items': items}


# Enlaces fijos de la plantilla (#datos, #agenda…) → dirección propia. data-h permite volver al hash al abrir con doble clic.
TOOL_PATHS = {'datos': 'datos', 'agenda': 'agenda', 'glosario': 'glosario', 'fuentes': 'fuentes', 'calculadora': 'calculadora-importacion',
              'exportacion': 'calculadora-exportacion', 'guias': 'guias', 'acerca': 'quienes-somos', 'contacto': 'contacto', 'privacidad': 'privacidad', 'terminos': 'terminos'}


def site_data(news):
    tax = json.loads((ROOT / 'data' / 'taxonomy.json').read_text(encoding='utf-8'))
    tax.pop('_ayuda', None)
    glo = json.loads((ROOT / 'data' / 'glossary.json').read_text(encoding='utf-8'))
    return {'taxonomy': tax, 'glossary': glo.get('terms', []),
            'guides': [{k: g.get(k) for k in ('slug', 'title', 'desc', 'updated')} for g in load_guides()],
            'calcRules': load_calc_rules()}


def load_calc_rules():
    """Reglas tributarias de las calculadoras (data/calc-rules.json): una sola fuente para la página, el texto sin JS y las pruebas."""
    rules = json.loads((ROOT / 'data' / 'calc-rules.json').read_text(encoding='utf-8'))
    rules.pop('_ayuda', None)
    return rules


def load_guides():
    f = ROOT / 'data' / 'guides.json'
    return json.loads(f.read_text(encoding='utf-8')).get('guides', []) if f.exists() else []


def pretty_links(page, base):
    def sub(m):
        h = m.group(1)
        if h in TOOL_PATHS:
            return f'href="{base}{TOOL_PATHS[h]}/" data-h="{h}"'
        if h.startswith('tema-'):
            return f'href="{base}seccion/{h[5:]}/" data-h="{h}"'
        if h.startswith('guia-'):
            return f'href="{base}guias/{h[5:]}/" data-h="{h}"'
        return m.group(0)
    return re.sub(r'href="#([a-z][a-z0-9-]*)"', sub, page)


def render(template, *, meta, prerender, feed, bank, version, base='', sitedata=None):
    """prerender: función que recibe el contenido por defecto de <main> (el esqueleto de carga) y devuelve el final."""
    a, b = template.index('<!--meta:start-->'), template.index('<!--meta:end-->') + len('<!--meta:end-->')
    page = template[:a] + meta + template[b:]
    a, b = page.index('<!--mail:start-->'), page.index('<!--mail:end-->') + len('<!--mail:end-->')
    email = SITECFG.get('contactEmail')
    page = page[:a] + (page[a + 17:b - 15].replace('{{CONTACT_EMAIL}}', esc(email)) if email else '') + page[b:]
    start, end = '<!--prerender:start-->', '<!--prerender:end-->'
    a, b = page.index(start), page.index(end)
    page = page[:a] + prerender(page[a + len(start):b]) + page[b + len(end):]
    page = pretty_links(page, base)
    if base:   # páginas internas: el encabezado ya sale compacto (evita que el contenido salte cuando arranca el JavaScript)
        page = page.replace('<section class="phead" id="phead">', '<section class="phead compact" id="phead">', 1)
    if STATUS:
        page = (page.replace('<b id="updatedLong">—</b>', f'<b id="updatedLong">{esc(STATUS["updated"])}</b>')
                    .replace('<span id="footUpdated">—</span>', f'<span id="footUpdated">{esc(STATUS["updated"])}</span>')
                    .replace('<span id="year"></span>', f'<span id="year">{STATUS["year"]}</span>')
                    .replace('<b id="todayLong"></b>', f'<b id="todayLong">{esc(STATUS.get("today", ""))}</b>')
                    .replace('<span id="countLine"></span>', f'<span id="countLine">{STATUS.get("count", "")}</span>'))
    return (page.replace('{{PHOTO_BANK}}', json_script(bank))
                .replace('{{SITE_DATA}}', json_script(sitedata or {}))
                .replace('{{FEED}}', json_script(feed))
                .replace('{{ASSET_VERSION}}', version)
                .replace('{{BASE}}', base)
                .replace('{{SITE_CONFIG}}', json_script(SITECFG)))


def home_prerender(items, base=''):
    """Portada visible sin JavaScript (buscadores y vistas previas): primero las notas curadas, después
    los titulares automáticos, con un máximo de dos por medio para que ninguno acapare la lista."""
    curated = [i for i in items if not is_auto(i)][:10]
    per, auto = {}, []
    for i in items:
        if is_auto(i):
            n = per[primary(i).get('name')] = per.get(primary(i).get('name'), 0) + 1
            if n <= 2:
                auto.append(i)
    def lead_item(i):
        summary = f'<p>{esc(i["summary"])}</p>' if i.get('summary') else ''
        return (f'<article><small>{esc(category(i))} · {fmt_day(item_dt(i))}</small>'
                f'<h3><a href="{base}noticias/{esc(i["id"])}/">{esc(i["title"])}</a></h3>{summary}'
                f'<small>Fuente: {esc(primary(i).get("name"))}</small></article>')
    lead = ''.join(lead_item(i) for i in curated)
    return (f'    <div class="pre-home">\n      <h2>Noticias verificadas por la redacción</h2>\n      <div class="pre-lead">{lead}</div>\n'
            f'      <h2>Otras noticias de comercio exterior</h2>\n      {headline_list(auto, base, 20)}\n    </div>\n')


def headline_list(lst, base, n=30):
    return '<ul class="pre-list">' + ''.join(
        f'<li><a href="{base}noticias/{esc(i["id"])}/">{esc(i["title"])}</a><small>{esc(primary(i).get("name"))} · {fmt_day(item_dt(i))}</small></li>'
        for i in lst[:n]) + '</ul>'


def static_article(h1, lede, body='', crumb=None, base='../'):
    trail = f'<a href="{base}">Inicio</a>' + (f' · {crumb}' if crumb else '')
    return f'''    <article class="pre-article">
      <p class="pre-meta">{trail}</p>
      <h1>{esc(h1)}</h1>
      <p class="lede">{esc(lede)}</p>
      {body}
    </article>
'''


def tool_body(key, news, items, sitedata, base):
    """Contenido visible sin JavaScript (y para buscadores) de cada herramienta."""
    if key == 'datos':
        rows = ''.join(f'<li><b>{esc(d.get("label"))}</b>: {esc(d.get("value") or "Sin datos")}'
                       f'{(" · " + esc(d.get("period"))) if d.get("period") else ""}'
                       f'{(" · dato " + esc(d.get("frequency"))) if d.get("frequency") else ""} <small>Fuente: {esc(d.get("source"))}</small></li>'
                       for d in news.get('indicators', []))
        return f'<h2>Indicadores</h2><ul class="pre-list">{rows}</ul>'
    if key == 'glosario':
        terms = sorted(sitedata['glossary'], key=lambda t: norm(t[0]))
        return '<dl class="gloss-list">' + ''.join(f'<dt>{esc(t[0])}</dt><dd>{esc(t[2])}</dd>' for t in terms) + '</dl>'
    if key == 'agenda':
        today = NOW.astimezone(TZ).date().isoformat()
        rows = []
        for it in items:
            if is_auto(it):
                continue
            for d in it.get('deadlines') or []:
                if str(d.get('date', '')) >= today[:len(str(d.get('date', '')))]:
                    rows.append((str(d['date']), d.get('label', ''), it))
        rows.sort(key=lambda r: r[0])
        return '<h2>Próximas fechas</h2><ul class="pre-list">' + ''.join(
            f'<li><b>{esc(dt)}</b> · {esc(lbl)} <small><a href="{base}noticias/{esc(it["id"])}/">{esc(it["title"])}</a></small></li>'
            for dt, lbl, it in rows[:60]) + '</ul>'
    if key in ('calculadora', 'exportacion'):
        return calc_prerender(key, sitedata['calcRules'])
    if key == 'guias':
        return '<ul class="pre-list">' + ''.join(f'<li><a href="{base}guias/{esc(g["slug"])}/">{esc(g["title"])}</a><small>{esc(g["desc"])}</small></li>'
                                                for g in load_guides()) + '</ul>'
    if key == 'fuentes':
        names = sorted({primary(i).get('name') for i in items if primary(i).get('name')}, key=norm)
        ingest = ''.join(f'<li><b>{esc(x["name"])}</b> · {esc(x["type"])} <small>{esc(x["status"])}'
                         f'{(" · última lectura correcta: " + fmt_day(parse_date(x["lastOk"]))) if x.get("lastOk") else ""}</small></li>'
                         for x in sources_public())
        return ('<h2>Fuentes que se consultan automáticamente</h2><ul class="pre-list">' + ingest + '</ul>'
                '<h2>Fuentes citadas en las noticias</h2><ul class="pre-list">' + ''.join(f'<li>{esc(n)}</li>' for n in names) + '</ul>')
    mail = SITECFG.get('contactEmail')
    mail_html = f'<a href="mailto:{esc(mail)}">{esc(mail)}</a>' if mail else ''
    if key == 'acerca':
        return ('<p>Comexplora es un portal de noticias, datos y herramientas sobre comercio exterior, con foco en Argentina y Latinoamérica.</p>'
                '<h2>Criterios editoriales</h2><ul>'
                '<li>Toda nota identifica su fuente y enlaza al original; no se publican noticias sin título, fecha y fuente.</li>'
                '<li>Las notas de la redacción se verifican contra la fuente original. Las marcadas como «Automática» provienen del feed de la fuente y no tienen revisión editorial.</li>'
                '<li>Las calculadoras dan estimaciones orientativas y citan las normas que usan, con su fecha de revisión.</li>'
                '<li>Para pedir una corrección, escribinos con el enlace a la nota' + (f' a {mail_html}' if mail else '') + '.</li></ul>')
    if key == 'contacto':
        return f'<p>Escribinos a {mail_html} para correcciones, sugerencias de fuentes o propuestas.</p>' if mail else ''
    if key == 'privacidad':
        return ('<p>No hace falta registrarse para leer el sitio. Las noticias guardadas y las preferencias se guardan solo en tu navegador.</p>'
                + ('<p>Usamos Google Analytics para contar visitas; usa cookies y recibe datos técnicos de la visita.</p>' if SITECFG.get('googleAnalyticsId') else ''))
    if key == 'terminos':
        return ('<p>El contenido es informativo y no constituye asesoramiento legal, aduanero, tributario ni financiero. '
                'Las notas citan y enlazan a sus fuentes; los datos y declaraciones pertenecen a ellas.</p>')
    return ''


STALE_SOURCE_HOURS = 48     # una fuente activa sin lecturas correctas en este plazo se marca para revisar
FAILING_SOURCE_RUNS = 3     # o con esta cantidad de fallas seguidas


def sources_public():
    """Estado de las fuentes de ingesta para la página /fuentes/: comprensible para el público, sin detalles técnicos.
    El error exacto queda en data/sources-status.json y en el resumen de cada ejecución de GitHub Actions."""
    cfg = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8')) if (ROOT / 'sources.json').exists() else []
    f = ROOT / 'data' / 'sources-status.json'
    status = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    out = []
    for src in cfg:
        st = status.get(src['name'], {})
        last_ok = parse_date(st.get('lastOk'))
        hours = (NOW - last_ok).total_seconds() / 3600 if last_ok else None
        if not src.get('enabled', True):
            state, text = 'off', 'Desactivada'
        elif not st:
            state, text = 'new', 'Todavía no se consultó'
        elif st.get('failures', 0) >= FAILING_SOURCE_RUNS:
            state, text = 'fail', f'No responde ({st["failures"]} intentos seguidos)'
        elif not last_ok and st.get('failures'):
            state, text = 'fail', 'No responde'
        elif hours is None or hours > STALE_SOURCE_HOURS:
            state, text = 'stale', 'Sin lecturas correctas recientes'
        elif not st.get('ok', True):
            state, text = 'warn', 'Falló la última consulta'
        else:
            state, text = 'ok', 'Funciona'
        out.append({'name': src['name'], 'type': src.get('type', ''), 'aggregator': bool(src.get('aggregator')),
                    'state': state, 'status': text, 'lastOk': st.get('lastOk'), 'lastRun': st.get('lastRun'),
                    'items': st.get('items'), 'added': st.get('added')})
    return out


def calc_prerender(key, rules):
    """Explicación de la calculadora visible sin JavaScript, armada con las mismas reglas que usa el cálculo."""
    I = rules['import']
    usd = lambda n: f'USD {n:,.0f}'.replace(',', '.')
    rev = '/'.join(reversed(rules['reviewed'].split('-')))
    srcs = '<h2>Normas y fuentes oficiales</h2><ul>' + ''.join(
        f'<li><a href="{esc(x["url"])}" rel="noopener">{esc(x["name"])}</a></li>' for x in rules['sources']) + \
        f'</ul><p>Reglas revisadas el {rev}. Es una estimación orientativa: confirmá las alícuotas y los requisitos con tu despachante de aduana.</p>'
    if key == 'calculadora':
        caps, low = [], 0
        for lim, cap in I['te']['caps']:
            tramo = f'Hasta {usd(lim)}' if low == 0 else (f'Más de {usd(low)} y hasta {usd(lim)}' if lim else f'Más de {usd(low)}')
            caps.append(f'<tr><td>{tramo}</td><td>{usd(cap)}</td></tr>')
            low = lim or low
        until = '/'.join(reversed(I['te']['validUntil'].split('-')))
        return ('<h2>Cómo se calcula</h2>'
                '<ol><li><b>Valor CIF</b> (valor en aduana): precio de compra más lo que el Incoterm no incluye: gastos hasta el embarque (EXW, FCA), '
                'flete (EXW, FCA, FOB) y seguro (todos salvo CIF).</li>'
                '<li><b>Derecho de importación</b>: alícuota de la posición arancelaria sobre el CIF. Se pide siempre: no se asume ningún porcentaje. '
                'Con origen Mercosur y certificado de origen es 0 %, salvo productos excluidos como el azúcar y el sector automotor.</li>'
                f'<li><b>Tasa de estadística</b>: {I["te"]["rate"]} % del CIF hasta el {until} ({esc(I["te"]["norm"])}), con topes por tramo. '
                'Exentas: mercadería originaria del Mercosur, acuerdos que lo prevean y operaciones con normas especiales.</li>'
                '<li><b>Base imponible</b>: CIF + derecho + tasa. Sobre ella se calculan el IVA (21 %, 10,5 % o exento), la percepción de IVA '
                f'({I["percIva"]["rates"]["21"]} % o {I["percIva"]["rates"]["10.5"]} %), la de Ganancias ({I["percGan"]["general"]} %; '
                f'{I["percGan"]["particular"]} % para uso particular) y la de Ingresos Brutos (general {str(I["iibb"]["general"]).replace(".", ",")} %).</li>'
                '<li><b>Situación fiscal</b>: un responsable inscripto recupera el IVA y las percepciones; para un monotributista o un particular son costo. '
                'Los bienes de uso no tienen percepciones de IVA ni de Ganancias.</li></ol>'
                '<h2>Topes de la tasa de estadística</h2><table><tr><th>Valor en aduana</th><th>Tope</th></tr>' + ''.join(caps) + '</table>'
                '<p>No incluye derechos antidumping, impuestos internos, valores criterio, licencias ni regímenes especiales.</p>' + srcs)
    return ('<h2>Cómo se calcula</h2>'
            '<ol><li><b>Derecho de exportación</b>: alícuota de la posición sobre el valor FOB. Solo si el producto lleva insumos importados '
            'temporariamente (Decreto 1330/2004) se descuenta su valor CIF de la base.</li>'
            '<li><b>Reintegro</b>: no se aplica por defecto. Si la posición lo tiene y se cumplen los requisitos, se calcula sobre el FOB menos el '
            'CIF de los insumos importados incorporados y las comisiones (Decreto 571/1996).</li>'
            '<li><b>Ingreso neto estimado</b>: FOB − derecho − comisiones − gastos hasta el embarque (+ reintegro, si corresponde).</li></ol>' + srcs)


TOOLS = {
    'datos': ('Datos e indicadores de comercio exterior', 'Tipo de cambio, soja, petróleo, fletes, carga aérea e intercambio comercial argentino: los indicadores del comercio exterior, con su fuente oficial.'),
    'agenda': ('Agenda de comercio exterior', 'Vencimientos, entradas en vigor, publicaciones oficiales y fechas clave del comercio exterior argentino e internacional.'),
    'glosario': ('Glosario de comercio exterior', 'Qué significan CIF, FOB, NCM, ARCA, antidumping, Incoterms y otros términos del comercio exterior, explicados en simple.'),
    'calculadora': ('Calculadora de costo de importación', 'Calculá el valor CIF, los derechos de importación, la tasa de estadística, el IVA y las percepciones para importar en la Argentina.'),
    'exportacion': ('Calculadora de exportación', 'Calculá los derechos de exportación, los reintegros y los gastos de una exportación desde la Argentina, y cuánto te queda a partir del valor FOB.'),
    'guias': ('Guías de comercio exterior', 'Guías prácticas: cómo calcular el costo de importar, qué es el valor CIF y el FOB, Incoterms 2020 y posición arancelaria NCM.'),
    'fuentes': ('Fuentes de información', 'Organismos oficiales, aduanas, organizaciones internacionales y medios especializados que usa Comexplora.'),
    'acerca': ('Quiénes somos', 'Comexplora es un portal de noticias, datos y análisis sobre comercio exterior, con foco en Argentina y Latinoamérica.'),
    'contacto': ('Contacto', 'Cómo comunicarte con Comexplora para sugerencias, correcciones o propuestas.'),
    'privacidad': ('Política de privacidad', 'Cómo trata Comexplora los datos de quienes visitan el sitio.'),
    'terminos': ('Términos y condiciones', 'Condiciones de uso del contenido de Comexplora.'),
}
TOOLS_INDEXED = {'datos', 'agenda', 'glosario', 'calculadora', 'exportacion', 'guias', 'fuentes', 'acerca'}


def build_static_pages(template, news, items, bank, version, sitedata, tax):
    """Genera /seccion/<slug>/, /tema/<id>/ y las herramientas. Devuelve [(ruta, lastmod)] para el sitemap."""
    out, keep = [], set()
    curated = [i for i in items if not is_auto(i)]
    updated = news.get('updatedAt') or NOW.isoformat()

    def write(rel, page):
        d = ROOT / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / 'index.html').write_text(page, encoding='utf-8')

    def page_for(rel, title, desc, body, feed_items, robots, crumbs, ld_type='CollectionPage'):
        base = '../' * rel.count('/')
        url = f'{SITE}/{rel}'
        ld = {'@context': 'https://schema.org', '@graph': [
            {'@type': ld_type, 'name': title, 'description': desc, 'url': url, 'inLanguage': 'es-AR',
             'isPartOf': {'@type': 'WebSite', 'name': SITE_NAME, 'url': f'{SITE}/'}},
            {'@type': 'BreadcrumbList', 'itemListElement': [{'@type': 'ListItem', 'position': n + 1, 'name': c[0], 'item': c[1]}
                                                              for n, c in enumerate([('Inicio', f'{SITE}/')] + crumbs)]}]}
        meta = meta_block(title=f'{title} · {SITE_NAME}', desc=desc, url=url, image=OG_DEFAULT, image_alt=SITE_NAME,
                          robots=robots, ld=ld, image_size=(1200, 630), base=base)
        return render(template, meta=meta, prerender=lambda inner: body(base), feed=feed_payload(news, feed_items),
                      bank=bank, version=version, base=base, sitedata=sitedata)

    # Secciones
    for slug, sec in tax.sections.items():
        lst = tax.items_for(slug, items)
        rel = f'seccion/{slug}/'
        keep.add(('seccion', slug))
        indexed = len(lst) >= tax.min_index
        title = sec.get('title') or sec['label']
        g = tax.group_of(slug)
        crumbs = ([(g['label'], f'{SITE}/seccion/{g["slug"]}/')] if g and g['slug'] != slug else []) + [(title, f'{SITE}/{rel}')]
        trail = (f'<a href="../../seccion/{g["slug"]}/">{esc(g["label"])}</a> · ' if g and g['slug'] != slug else '') + esc(title)
        siblings = ''
        if g:
            sl = [tax.sections[x] for x in ([g['slug']] + g['sections']) if x in tax.sections and x != slug]
            siblings = '<p class="pre-meta">' + ' · '.join(f'<a href="../../seccion/{x["slug"]}/">{esc(x["label"])}</a>' for x in sl) + '</p>'
        body = lambda base, lst=lst, title=title, sec=sec, trail=trail, siblings=siblings: static_article(
            f'{title}: noticias de comercio exterior', sec.get('desc', ''),
            siblings + ('<h2>Últimas noticias</h2>' + headline_list(lst, base) if lst else '<p>Todavía no hay noticias en esta sección.</p>'),
            crumb=trail, base=base)
        feed_items = (sorted([i for i in lst if not is_auto(i)], key=item_dt, reverse=True) + [i for i in lst if is_auto(i)])[:60]
        write(rel, page_for(rel, f'{title} · Noticias de comercio exterior', sec.get('desc', ''), body, feed_items,
                            'index,follow' if indexed else 'noindex,follow', crumbs))
        if indexed:
            out.append((rel, item_dt(lst[0]).isoformat()))

    # Temas en desarrollo
    for st in news.get('stories', []):
        own = [i for i in curated if i.get('story') == st['id']]
        rel = f'tema/{st["id"]}/'
        keep.add(('tema', st['id']))
        body = lambda base, own=own, st=st: static_article(st['title'], st.get('desc', ''),
                                                            '<h2>Cronología</h2>' + headline_list(own, base) if own else '', crumb='Temas en desarrollo', base=base)
        indexed = len(own) >= 2
        write(rel, page_for(rel, st['title'], st.get('desc', ''), body, own, 'index,follow' if indexed else 'noindex,follow',
                            [(st['title'], f'{SITE}/{rel}')]))
        if indexed:
            out.append((rel, item_dt(own[0]).isoformat()))

    # Herramientas y páginas institucionales
    for key, (title, desc) in TOOLS.items():
        rel = f'{TOOL_PATHS[key]}/'
        feed_items = curated[:80] if key in ('agenda', 'datos', 'fuentes') else curated[:12]
        body = lambda base, key=key, title=title, desc=desc: static_article(title, desc, tool_body(key, news, items, sitedata, base), base=base)
        ld_type = 'WebApplication' if key in ('calculadora', 'exportacion') else 'CollectionPage' if key == 'guias' else 'WebPage'
        write(rel, page_for(rel, title, desc, body, feed_items, 'index,follow' if key in TOOLS_INDEXED else 'noindex,follow',
                            [(title, f'{SITE}/{rel}')], ld_type))
        if key in TOOLS_INDEXED:
            out.append((rel, updated))

    # Guías
    for g in load_guides():
        rel = f'guias/{g["slug"]}/'
        keep.add(('guias', g['slug']))
        gdata = dict(sitedata, guide={'slug': g['slug'], 'body': g.get('body', '')})
        url = f'{SITE}/{rel}'
        ld = {'@context': 'https://schema.org', '@graph': [
            {'@type': 'Article', 'headline': g['title'], 'description': g.get('desc', ''), 'url': url, 'inLanguage': 'es-AR',
             'dateModified': g.get('updated'), 'author': {'@type': 'Organization', 'name': SITE_NAME, 'url': f'{SITE}/'},
             'publisher': {'@type': 'NewsMediaOrganization', 'name': SITE_NAME, 'logo': {'@type': 'ImageObject', 'url': f'{SITE}/logo.png'}}},
            {'@type': 'BreadcrumbList', 'itemListElement': [
                {'@type': 'ListItem', 'position': 1, 'name': 'Inicio', 'item': f'{SITE}/'},
                {'@type': 'ListItem', 'position': 2, 'name': 'Guías', 'item': f'{SITE}/guias/'},
                {'@type': 'ListItem', 'position': 3, 'name': g['title'], 'item': url}]}]}
        meta = meta_block(title=f'{g["title"]} · {SITE_NAME}', desc=g.get('desc', ''), url=url, image=OG_DEFAULT, image_alt=SITE_NAME,
                          og_type='article', ld=ld, image_size=(1200, 630), base='../../')
        body_html = pretty_links(g.get('body', ''), '../../')
        pre = static_article(g['title'], g.get('desc', ''), body_html, crumb='<a href="../">Guías</a>', base='../../')
        write(rel, render(template, meta=meta, prerender=lambda inner, pre=pre: pre, feed=feed_payload(news, curated[:12]),
                          bank=bank, version=version, base='../../', sitedata=gdata))
        out.append((rel, g.get('updated') or updated))

    # Borra secciones, temas o guías que ya no existen
    for folder in ('seccion', 'tema', 'guias'):
        d = ROOT / folder
        if d.exists():
            for sub in d.iterdir():
                if sub.is_dir() and (folder, sub.name) not in keep and not (folder == 'guias' and sub.name == 'index.html'):
                    shutil.rmtree(sub)
    return out


def main():
    template = (ROOT / 'templates' / 'page.html').read_text(encoding='utf-8')
    news, bank = load()
    sitedata = site_data(news)
    tax = Taxonomy.load()
    items = sorted([i for i in news.get('items', []) if i.get('id') and i.get('title')], key=item_dt, reverse=True)
    version = hashlib.sha1(b''.join((ROOT / f).read_bytes() for f in ('assets/app.js', 'assets/app.css', 'assets/calc-core.js'))).hexdigest()[:10]
    up = parse_date(news.get('updatedAt')) or NOW
    up_local = up.astimezone(TZ)
    meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
    STATUS.update(updated=f'{up_local.day} de {meses[up_local.month - 1]} de {up_local.year}, {up_local:%H:%M} h', year=NOW.astimezone(TZ).year)
    # Fecha y conteo ya escritos en el HTML: la página no cambia de alto cuando el JavaScript los actualiza.
    dias = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
    meses_l = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
    hoy = NOW.astimezone(TZ)
    STATUS['today'] = f'{dias[hoy.weekday()]}, {hoy.day} de {meses_l[hoy.month - 1]} de {hoy.year}'
    week = sum(1 for i in items if item_dt(i) >= NOW - timedelta(days=7))
    STATUS['count'] = (f'<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>'
                       f'<b>{min(len(items), LATEST_MAX)}</b>&nbsp;noticias · <b>{week}</b>&nbsp;en 7 días')
    today_n = sum(1 for i in items if item_dt(i).astimezone(TZ).date() == hoy.date())
    if today_n:
        STATUS['count'] += f' · <b>{today_n}</b>&nbsp;hoy'
    og_map = {}
    if og_images:
        try:
            og_map = og_images.build([i for i in items if not is_auto(i)], category, SITE.split('://', 1)[-1])
        except Exception as e:   # una falla en las imágenes no frena la publicación
            print(f'Aviso: no se pudieron generar las imágenes para redes ({e}).')

    # Feed para la página: todas las curadas + las automáticas más recientes, con tope.
    curated = [i for i in items if not is_auto(i)]
    latest = sorted(curated + [i for i in items if is_auto(i)][:max(0, LATEST_MAX - len(curated))], key=item_dt, reverse=True)
    (ROOT / 'data' / 'latest.json').write_text(json.dumps(feed_payload(news, latest), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

    # Portada
    org_ld = {'@context': 'https://schema.org', '@graph': [
        {'@type': 'NewsMediaOrganization', '@id': f'{SITE}/#org', 'name': SITE_NAME, 'url': f'{SITE}/', 'logo': f'{SITE}/logo.png',
         'description': 'Portal de noticias, datos y análisis sobre comercio exterior, con foco en Argentina y Latinoamérica.', 'inLanguage': 'es-AR'},
        {'@type': 'WebSite', '@id': f'{SITE}/#web', 'name': SITE_NAME, 'url': f'{SITE}/', 'inLanguage': 'es-AR', 'publisher': {'@id': f'{SITE}/#org'},
         'potentialAction': {'@type': 'SearchAction', 'target': f'{SITE}/#buscar?q={{q}}', 'query-input': 'required name=q'}}]}
    # Feed embebido: primero las curadas recientes (así la portada arma su jerarquía antes de leer latest.json).
    inline = sorted(curated[:INLINE_MAX // 2] + [i for i in items if is_auto(i)][:INLINE_MAX // 2], key=item_dt, reverse=True)
    home = render(template, meta=meta_block(title=HOME_TITLE, desc=HOME_DESC, url=f'{SITE}/', image=OG_DEFAULT,
                                            image_alt='Comexplora · Noticias de Comercio Exterior', ld=org_ld, image_size=(1200, 630)),
                  prerender=lambda inner: home_prerender(items), feed=feed_payload(news, inline), bank=bank, version=version,
                  sitedata=sitedata)
    # La portada lleva el CSS y el JS embebidos: así funciona sola, incluso abierta con doble clic
    # desde adentro del zip (Windows extrae solo ese archivo). Las páginas de notas usan /assets/.
    css = (ROOT / 'assets' / 'app.css').read_text(encoding='utf-8')
    js = (ROOT / 'assets' / 'app.js').read_text(encoding='utf-8').replace('</script', '<\\/script')
    home = re.sub(r'<link rel="stylesheet" href="assets/app\.css\?v=[^"]*">', lambda _: f'<style>\n{css}</style>', home, count=1)
    core = (ROOT / 'assets' / 'calc-core.js').read_text(encoding='utf-8').replace('</script', '<\\/script')
    home = re.sub(r'<script src="assets/calc-core\.js\?v=[^"]*" defer></script>', lambda _: f'<script>\n{core}</script>', home, count=1)
    home = re.sub(r'<script src="assets/app\.js\?v=[^"]*" defer></script>', lambda _: f'<script>\n{js}</script>', home, count=1)
    assert '<style>' in home and 'src="assets/app.js' not in home and 'src="assets/calc-core.js' not in home, 'no se pudo embeber CSS/JS en la portada'
    (ROOT / 'index.html').write_text(home, encoding='utf-8')

    # Una página por nota
    out_dir = ROOT / 'noticias'
    out_dir.mkdir(exist_ok=True)
    ids = set()
    for it in items:
        ids.add(it['id'])
        url = f'{SITE}/noticias/{it["id"]}/'
        img, img_alt, source_img = photo_for(it, bank)
        share_img, share_alt, share_size = img, img_alt, None
        if it['id'] in og_map:   # tarjeta propia para redes (título + dato clave + marca)
            share_img, share_alt, share_size = SITE + og_map[it['id']], f'{it["title"]} · {SITE_NAME}', (1200, 630)
        p = primary(it)
        robots = 'noindex,follow' if is_auto(it) and not INDEX_AUTOMATIC else 'index,follow,max-image-preview:large'
        ld = {'@context': 'https://schema.org', '@graph': [{
            '@type': 'NewsArticle', 'headline': it['title'][:110], 'description': it.get('summary') or it['title'], 'image': [img] + ([share_img] if share_img != img else []),
            'datePublished': it.get('datetime') or it.get('date'), 'dateModified': it.get('updated') or it.get('datetime') or it.get('date'),
            'inLanguage': 'es-AR', 'mainEntityOfPage': url, 'articleSection': category(it),
            'keywords': ', '.join(it.get('tags', []) + it.get('topics', [])),
            'author': {'@type': 'Organization', 'name': f'{SITE_NAME} · Redacción', 'url': f'{SITE}/'},
            'publisher': {'@type': 'NewsMediaOrganization', 'name': SITE_NAME, 'logo': {'@type': 'ImageObject', 'url': f'{SITE}/logo.png'}},
            'isBasedOn': [{'@type': 'CreativeWork', 'url': s.get('url'), 'publisher': {'@type': 'Organization', 'name': s.get('name')}} for s in it.get('sources', [])],
        }]}
        extra = '\n'.join([f'<meta property="article:published_time" content="{esc(it.get("datetime") or it.get("date"))}">',
                           f'<meta property="article:section" content="{esc(category(it))}">'])
        meta = meta_block(title=f'{it["title"]} · {SITE_NAME}', desc=it.get('summary') or it['title'], url=url, image=share_img,
                          image_alt=share_alt, og_type='article', robots=robots, ld=ld, extra=extra, base='../../', image_size=share_size)
        pre = prerender_article(it, img, img_alt, source_img)
        page = render(template, meta=meta, prerender=lambda inner: pre,
                      feed=feed_payload(news, [it]), bank=bank, version=version, base='../../', sitedata=sitedata)
        d = out_dir / it['id']
        d.mkdir(parents=True, exist_ok=True)
        (d / 'index.html').write_text(page, encoding='utf-8')
        # Notas automáticas unidas a esta (mismo hecho, otro medio): su dirección vieja redirige acá.
        for old_id in it.get('mergedIds') or []:
            if not old_id or old_id in ids or '/' in old_id or old_id.startswith('.'):
                continue
            ids.add(old_id)
            rd = out_dir / old_id
            rd.mkdir(parents=True, exist_ok=True)
            (rd / 'index.html').write_text(
                f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{esc(it["title"])}</title>'
                f'<link rel="canonical" href="{esc(url)}"><meta name="robots" content="noindex,follow">'
                f'<meta http-equiv="refresh" content="0; url=../{esc(it["id"])}/"></head>'
                f'<body><p><a href="../{esc(it["id"])}/">{esc(it["title"])}</a></p></body></html>\n', encoding='utf-8')
    removed = 0
    for d in out_dir.iterdir():
        if d.is_dir() and d.name not in ids:
            shutil.rmtree(d)
            removed += 1

    # Estado público de las fuentes de ingesta (página /fuentes/) y aviso en el registro si alguna necesita revisión.
    srcs = sources_public()
    (ROOT / 'data' / 'sources-public.json').write_text(json.dumps({'generatedAt': NOW.isoformat(), 'sources': srcs}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    for x in srcs:
        if x['state'] in ('fail', 'stale'):
            print(f"Aviso: revisar la fuente «{x['name']}»: {x['status']}.")

    # Páginas con dirección propia: secciones, temas en desarrollo y herramientas
    static_urls = build_static_pages(template, news, items, bank, version, sitedata, tax)

    # Sitemaps y RSS
    indexable = [i for i in items if INDEX_AUTOMATIC or not is_auto(i)]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
          f'  <url><loc>{SITE}/</loc><lastmod>{esc(news.get("updatedAt") or NOW.isoformat())}</lastmod><changefreq>hourly</changefreq></url>']
    for u, lastmod in static_urls:
        sm.append(f'  <url><loc>{SITE}/{u}</loc><lastmod>{esc(lastmod)}</lastmod></url>')
    for it in indexable:
        sm.append(f'  <url><loc>{SITE}/noticias/{esc(it["id"])}/</loc><lastmod>{esc(it.get("updated") or it.get("datetime") or it.get("date"))}</lastmod></url>')
    sm.append('</urlset>')
    (ROOT / 'sitemap.xml').write_text('\n'.join(sm) + '\n', encoding='utf-8')

    ns = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">']
    for it in [i for i in indexable if item_dt(i) >= NOW - timedelta(days=2)][:1000]:
        ns.append(f'  <url><loc>{SITE}/noticias/{esc(it["id"])}/</loc><news:news><news:publication><news:name>{SITE_NAME}</news:name>'
                  f'<news:language>es</news:language></news:publication><news:publication_date>{item_dt(it).isoformat()}</news:publication_date>'
                  f'<news:title>{esc(it["title"])}</news:title></news:news></url>')
    ns.append('</urlset>')
    (ROOT / 'news-sitemap.xml').write_text('\n'.join(ns) + '\n', encoding='utf-8')

    rss = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
           f'<title>{SITE_NAME} · Noticias</title><link>{SITE}/</link><description>Noticias y actualidad del comercio exterior.</description>',
           f'<language>es-ar</language><lastBuildDate>{email.utils.format_datetime(NOW)}</lastBuildDate>',
           f'<atom:link href="{SITE}/feed.xml" rel="self" type="application/rss+xml"/>']
    for it in items[:50]:
        u = f'{SITE}/noticias/{it["id"]}/'
        rss.append(f'<item><title>{esc(it["title"])}</title><link>{u}</link><guid isPermaLink="true">{u}</guid>'
                   f'<pubDate>{email.utils.format_datetime(item_dt(it))}</pubDate><source url="{esc(primary(it).get("url"))}">{esc(primary(it).get("name"))}</source>'
                   f'<description>{esc(it.get("summary") or ("Nota de " + (primary(it).get("name") or SITE_NAME)))}</description></item>')
    rss.append('</channel></rss>')
    (ROOT / 'feed.xml').write_text('\n'.join(rss) + '\n', encoding='utf-8')
    (ROOT / 'robots.txt').write_text(f'User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\nSitemap: {SITE}/news-sitemap.xml\n', encoding='utf-8')

    print(f'Páginas con dirección propia en el sitemap: {len(static_urls)}')
    print(f'Páginas generadas: {len(items)} · carpetas viejas borradas: {removed} · en latest.json: {len(latest)} · '
          f'fotos de archivo locales: {sum(1 for v in bank.values() for p in v if p.get("local"))}')


if __name__ == '__main__':
    main()
