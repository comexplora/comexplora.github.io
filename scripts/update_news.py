#!/usr/bin/env python3
"""Ingesta RSS/Atom de Comexplora (sin dependencias externas).

Qué hace en cada ejecución:
- Lee las fuentes habilitadas en sources.json (una fuente caída no frena a las demás).
- Filtra por relevancia COMEX y descarta notas viejas o casi duplicadas.
- Deduplica por enlace original normalizado: una noticia = una entrada, siempre.
- No agrega (y retira) notas automáticas que repiten un hecho ya cubierto por una nota curada:
  por coincidencia de enlace, por el campo "absorbs" de la nota curada o por similitud de texto.
- Conserva el ID (URL), la fecha y la imagen de las notas ya conocidas.
  Si la fuente no informa fecha, se usa la de la primera vez que se vio la nota (no se "renueva").
- Detecta países, categorías y si la nota menciona a Argentina.
- Toma la imagen que publica la propia fuente (RSS o etiqueta og:image), con crédito.
- Guarda todo en data/news.json y el estado de cada fuente en data/sources-status.json.

Las páginas, el sitemap y el RSS del sitio se generan después con scripts/build_pages.py.
"""
from __future__ import annotations

import email.utils
import html
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
NEWS = DATA / 'news.json'
STATUS = DATA / 'sources-status.json'
TZ = timezone(timedelta(hours=-3))
NOW = datetime.now(timezone.utc)
UA = 'Mozilla/5.0 (compatible; ComexploraBot/1.1; +https://comexplora.github.io/)'

MAX_AGE_DAYS = 21          # al ingresar, ignorar notas más viejas que esto
ARCHIVE_DAYS = 365         # las notas automáticas se conservan un año
MAX_AUTO_ITEMS = 3000      # tope de notas automáticas en el archivo
MAX_OG_FETCH = 12          # páginas consultadas por fuente y corrida para buscar la imagen

# Duplicados de notas curadas (ver curated_duplicate). Valores calibrados con el archivo de octubre de 2026:
CURATED_WINDOW_DAYS = 4    # solo se compara con notas curadas de ±4 días
CURATED_MIN_FULL = 0.38    # parecido mínimo con título + resumen + datos de la nota curada
CURATED_MIN_TITLE = 0.25   # parecido mínimo con el título y las etiquetas de la nota curada
CURATED_STRONG = 0.30      # además: parecido de título ≥ esto, o una cifra en común, o parecido total ≥ 0.70
CURATED_MIN_SHARED = 4     # sin cifra en común, hacen falta al menos 4 palabras compartidas

# Agrupamiento de automáticas que cuentan el mismo hecho (ver group_auto). Calibrado con el archivo de octubre de 2026.
GROUP_WINDOW_HOURS = 60    # solo se agrupan notas publicadas con menos de 60 h de diferencia
GROUP_SAME_TITLE_DAYS = 10  # el mismo título publicado por otro medio días después también se une
GROUP_MIN_SIM = 0.55       # parecido mínimo de títulos (palabras raras pesan más; sobre el título más corto)
GROUP_MIN_SHARED = 3       # palabras significativas compartidas como mínimo
GROUP_RARE_IDF = 3.0       # una palabra es "rara" si aparece en pocas notas del archivo (idf ≥ esto)
GROUP_MIN_SIM_RARE = 0.40  # parecido de títulos alcanza con 0.40 si comparten dos palabras raras

# Google Noticias: resolver el enlace original para leer descripción e imagen del medio.
MAX_GN_RESOLVE = 60        # enlaces resueltos por corrida (las nuevas primero; después, las viejas sin resumen)

# Taxonomía: debe coincidir con TOPICS en assets/app.js
TOPICS = ['Argentina', 'Latinoamérica', 'Estados Unidos', 'Europa', 'Asia', 'China', 'Oceanía', 'Mercosur', 'Importaciones',
          'Exportaciones', 'Aduanas', 'Aranceles', 'Impuestos', 'Tratados y acuerdos', 'Logística', 'Transporte marítimo',
          'Transporte aéreo', 'Puertos', 'Economía internacional', 'Geopolítica y comercio', 'Empresas', 'Regulaciones',
          'Tecnología COMEX']

RELEVANCE = ['comercio exterior', 'comercio internacional', 'comex', 'trade', 'export', 'importac', 'importad', 'importar',
             'imports', 'arancel', 'tariff',
             'aduana', 'customs', 'mercosur', 'omc', 'wto', 'acuerdo comercial', 'free trade', 'tratado', 'logistic',
             'logistica', 'flete', 'freight', 'shipping', 'puerto', 'port ', 'contenedor', 'container', 'antidumping',
             'anti-dumping', 'salvaguardia', 'safeguard', 'cupo', 'cuota', 'balanza comercial', 'trade balance',
             'despachante', 'carga aerea', 'air cargo', 'naviera', 'supply chain', 'cadena de suministro', 'sanciones']

# País -> patrones (texto ya normalizado: minúsculas y sin acentos)
COUNTRIES = {
    'Argentina': [r'argentin', r'\barca\b', r'\bafip\b', r'\bindec\b', r'buenos aires', r'rosario', r'casa rosada', r'milei', r'caputo',
                  r'\bsanta fe\b(?! de bogota)', r'mendoza', r'neuquen', r'vaca muerta', r'tucuman', r'chubut', r'jujuy', r'entre rios',
                  r'bahia blanca', r'\bsenasa\b', r'\bbcra\b', r'ciara-cec', r'\babeceb\b',
                  r'\bchaco\b(?! (paraguayo|boreal|boliviano))', r'\bzdero\b', r'correntin', r'provincia de corrientes', r'misiones (argentina|provincia)',
                  r'provincia de (salta|misiones|la rioja|san juan|catamarca|formosa)', r'catamarca', r'santiago del estero', r'\bla pampa\b',
                  r'tierra del fuego', r'provincia de cordoba', r'cordoba,? argentina', r'gobierno de cordoba', r'llaryora', r'mar del plata', r'quequen', r'\bzarate\b', r'san nicolas de los arroyos'],
    'Brasil': [r'brasil', r'brazil', r'brasilen', r'brazilian', r'lula\b', r'itamaraty', r'paranagua', r'puerto de santos', r'\bitajai',
               r'sao paulo', r'rio grande do sul', r'\bmdic\b', r'bolsonaro'],
    'Uruguay': [r'uruguay', r'montevideo', r'nueva palmira'], 'Paraguay': [r'paraguay', r'asuncion del paraguay'],
    'Chile': [r'\bchile', r'bio ?bio', r'valparaiso', r'iquique', r'antofagasta', r'talcahuano', r'mejillones', r'\barica\b',
              r'punta arenas', r'puerto montt', r'coquimbo', r'\bcsav\b', r'\bsitrans\b', r'puerto (de )?san antonio', r'puerto (de )?coronel', r'gran concepcion', r'\bsubtel\b',
              r'terminal pacifico sur', r'san vicente terminal', r'puerto angamos', r'lirquen'],
    'Bolivia': [r'bolivia', r'santa cruz de la sierra', r'tarija', r'cochabamba'],
    'Perú': [r'\bperu', r'chancay', r'\bcallao'], 'Colombia': [r'colombia', r'ecopetrol'], 'México': [r'mexic'], 'Panamá': [r'panama'],
    'Ecuador': [r'ecuador', r'guayaquil'], 'Venezuela': [r'venezuel'], 'Guatemala': [r'guatemal'], 'Costa Rica': [r'costa rica'],
    'República Dominicana': [r'dominican', r'santo domingo', r'dr-cafta', r'abinader', r'danilo medina', r'abel martinez', r'\bcaucedo\b'],
    'Cuba': [r'\bcuba\b', r'\bcuban', r'la habana', r'\bmariel\b'],
    'Estados Unidos': [r'estados unidos', r'eeuu', r'ee\.uu', r'united states', r'\bu\.s\.', r'washington', r'trump', r'\bustr\b', r'casa blanca', r'white house'],
    'Canadá': [r'canad'], 'China': [r'\bchina\b', r'chinese', r'chino', r'beijing', r'pekin', r'shanghai'],
    'Japón': [r'japon', r'japan'], 'India': [r'\bindia\b', r'indian'], 'Corea del Sur': [r'corea del sur', r'south korea', r'surcorean'],
    'Vietnam': [r'vietnam'], 'Indonesia': [r'indonesia'], 'Singapur': [r'singap'], 'Malasia': [r'malasia', r'malaysia'],
    'Filipinas': [r'filipinas', r'philippin'], 'Tailandia': [r'tailandia', r'thailand'], 'Taiwán': [r'taiwan'],
    'Bangladés': [r'banglad'], 'Pakistán': [r'pakistan'], 'Sri Lanka': [r'sri lanka'],
    'Unión Europea': [r'union europea', r'european union', r'\bue\b', r'\beu\b', r'comision europea', r'european commission', r'bruselas', r'brussels'],
    'Alemania': [r'alemania', r'german'], 'España': [r'espana', r'\bspain', r'galicia', r'gallega', r'baleares', r'canarias', r'andaluc', r'catalu', r'\bmadrid\b',
                r'barcelona', r'algeciras', r'\bbilbao\b', r'valenciaport', r'puerto de valencia', r'\bvigo\b', r'tarragona'], 'Francia': [r'francia', r'\bfrance', r'french'],
    'Italia': [r'italia', r'\bital', r'trieste', r'genova'], 'Reino Unido': [r'reino unido', r'united kingdom', r'\buk\b', r'britan'],
    'Países Bajos': [r'paises bajos', r'holand', r'netherlands', r'rotterdam', r'amsterdam'], 'Bélgica': [r'belgica', r'belgium', r'amberes', r'antwerp'],
    'Grecia': [r'grecia', r'greece', r'\bel pireo\b', r'piraeus', r'heraklion'], 'Noruega': [r'noruega', r'norway'], 'Polonia': [r'polonia', r'poland'],
    'Portugal': [r'portugal', r'lisboa'],
    'Estonia': [r'estonia', r'tallin'], 'Letonia': [r'letonia', r'latvia', r'\briga\b'], 'Lituania': [r'lituania', r'lithuania', r'klaipeda'],
    'Finlandia': [r'finlandia', r'finland'], 'Suecia': [r'suecia', r'sweden', r'swedish', r'gotemburgo', r'gothenburg'],
    'Dinamarca': [r'dinamarca', r'denmark', r'copenhague'], 'Irlanda': [r'irlanda', r'ireland'], 'Suiza': [r'\bsuiza\b', r'switzerland', r'\bswiss\b'],
    'Austria': [r'\baustria\b'],
    'Rusia': [r'rusia', r'russia'], 'Ucrania': [r'ucrania', r'ukrain'], 'Turquía': [r'turquia', r'turkey', r'turkiye'],
    'Arabia Saudita': [r'arabia saudita', r'saudi'], 'Emiratos Árabes Unidos': [r'emiratos', r'\buae\b', r'dubai'],
    'Irán': [r'\biran'], 'Irak': [r'\birak', r'\biraq'], 'Israel': [r'israel'], 'Catar': [r'\bcatar\b', r'\bqatar'], 'Omán': [r'\boman\b'],
    'Egipto': [r'egipto', r'egypt', r'canal de suez', r'suez canal'], 'Marruecos': [r'marruec', r'morocc', r'tanger'],
    'Sudáfrica': [r'sudafrica', r'south africa'], 'Angola': [r'angola', r'luanda'], 'Nigeria': [r'nigeria'], 'Kenia': [r'kenia', r'kenya'],
    'Costa de Marfil': [r'costa de marfil', r'ivory coast', r'abiyan', r'abidjan'], 'Australia': [r'australia'],
    'Nueva Zelanda': [r'nueva zelanda', r'new zealand'],
}
LATAM = {'Argentina', 'Brasil', 'Uruguay', 'Paraguay', 'Chile', 'Bolivia', 'Perú', 'Colombia', 'México', 'Panamá',
         'Ecuador', 'Venezuela', 'Guatemala', 'Costa Rica', 'República Dominicana', 'Cuba'}
EUROPE = {'Unión Europea', 'Alemania', 'España', 'Francia', 'Italia', 'Reino Unido', 'Rusia', 'Ucrania', 'Turquía',
          'Países Bajos', 'Bélgica', 'Grecia', 'Noruega', 'Polonia', 'Portugal', 'Estonia', 'Letonia', 'Lituania',
          'Finlandia', 'Suecia', 'Dinamarca', 'Irlanda', 'Suiza', 'Austria'}
ASIA = {'China', 'Japón', 'India', 'Corea del Sur', 'Vietnam', 'Indonesia', 'Singapur', 'Malasia', 'Filipinas', 'Tailandia',
        'Taiwán', 'Bangladés', 'Pakistán', 'Sri Lanka'}
OCEANIA = {'Australia', 'Nueva Zelanda'}

TOPIC_RULES = [
    ('Importaciones', [r'importa(?!n[tc])', r'\bimport(?!an)']),
    ('Exportaciones', [r'exporta', r'\bexport']),
    ('Aduanas', [r'aduan', r'customs', r'despachante', r'\barca\b', r'\bsim\b', r'ventanilla unica']),
    ('Aranceles', [r'arancel', r'tariff', r'antidumping', r'anti-dumping', r'salvaguard', r'safeguard', r'derechos de exportacion', r'retenciones', r'represalia']),
    ('Impuestos', [r'impuesto', r'\biva\b', r'percepcion', r'tributo', r'\btax']),
    ('Tratados y acuerdos', [r'acuerdo', r'tratado', r'agreement', r'\bfta\b', r'negociacion', r'cupo', r'cuota']),
    ('Logística', [r'logistic', r'supply chain', r'cadena de suministro', r'flete', r'freight']),
    ('Transporte marítimo', [r'maritim', r'naviera', r'shipping', r'buque', r'vessel', r'contenedor', r'container', r'hidrovia', r'canal de panama', r'suez', r'ormuz']),
    ('Transporte aéreo', [r'carga aerea', r'air cargo', r'aereo', r'airline', r'\biata\b']),
    ('Puertos', [r'puerto', r'\bport\b', r'portuari', r'terminal']),
    ('Regulaciones', [r'resolucion', r'decreto', r'normativa', r'regulation', r'reglamento', r'boletin oficial', r'\brg\b']),
    ('Geopolítica y comercio', [r'sancion', r'sanction', r'guerra comercial', r'trade war', r'conflicto', r'geopolit']),
    ('Economía internacional', [r'\bomc\b', r'\bwto\b', r'comercio mundial', r'world trade', r'balanza', r'trade balance', r'\bpbi\b', r'\bgdp\b', r'\bfmi\b', r'\bimf\b']),
    ('Tecnología COMEX', [r'digitaliz', r'blockchain', r'inteligencia artificial', r'\bia\b', r'software']),
]
VISUAL_RULES = [
    ('plane', [r'carga aerea', r'air cargo', r'aereo', r'airline', r'\biata\b', r'aeropuerto']),
    ('tanker', [r'petrol', r'\boil\b', r'crudo', r'tanker', r'\bgnl\b', r'\blng\b', r'ormuz']),
    ('steel', [r'acero', r'steel', r'aluminio', r'aluminium', r'aluminum']),
    ('court', [r'tribunal', r'court', r'corte', r'justicia', r'fallo']),
    ('port', [r'puerto', r'\bport\b', r'terminal']),
    ('container', [r'contenedor', r'container', r'\bteu\b', r'\bfeu\b']),
    ('ship', [r'maritim', r'shipping', r'buque', r'vessel', r'naviera', r'flete']),
    ('mining', [r'miner', r'\blitio', r'lithium', r'\bcobre\b', r'copper', r'\bmining\b']),
    ('agro', [r'\bsoja', r'soybean', r'\btrigo', r'\bmaiz\b', r'granos', r'cereal', r'cosecha', r'agroexport', r'\bagro\b', r'oleaginos']),
    ('customs', [r'aduan', r'customs', r'arancel', r'tariff', r'import(?!an)', r'export']),
    ('treaty', [r'acuerdo', r'tratado', r'agreement', r'mercosur', r'\bomc\b', r'\bwto\b']),
    ('chart', [r'balanza', r'estadistic', r'statistic', r'indice', r'index', r'crecimiento', r'growth']),
]


# Imágenes que no son fotos de la nota: logos, imágenes por defecto, íconos.
BAD_IMAGE = re.compile(r'(logo|avatar|gravatar|pixel|spacer|1x1|blank|placeholder|fallback|default[-_.]|sprite|icon|favicon)', re.I)

# Términos fuertes: una nota de un agregador (Google Noticias) debe tener al menos uno.
# Evita falsos positivos como "contenedores de basura" o "puerto" en sentido no comercial.
STRONG = [r'comercio exterior', r'comercio internacional', r'\bcomex\b', r'exporta', r'importa(?!n[tc])', r'arancel', r'aduan',
          r'mercosur', r'\bomc\b', r'acuerdo comercial', r'tratado de libre comercio', r'balanza comercial', r'flete',
          r'naviera', r'transporte maritimo', r'carga aerea', r'logistica internacional', r'antidumping', r'salvaguardia',
          r'guerra comercial', r'cadena de suministro', r'portacontenedores', r'contenedores maritimos', r'\bteu\b',
          r'logistic', r'transito de (contenedores|mercaderia|carga)', r'(etapa|relacion|intercambio|socio|apertura) comercial']

# Notas que no son de comercio exterior aunque usen sus palabras ("aranceles" médicos o notariales, accidentes
# laborales en un puerto, turismo de cruceros, promociones de consumo). Se descartan al ingresar y se retiran del archivo.
EXCLUDE = [
    r'\bpami\b', r'dialisis', r'obras? socia(l|les)\b', r'prestador(es)? (de salud|medic)', r'clinicas?\b', r'hospital',
    r'notari', r'conservador(es)? de bienes', r'honorarios', r'colegio de (abogados|escribanos|medicos)',
    r'arancel(es)? (universitari|medic|profesional|judicial|notarial|escolar|de (los )?(medicos|abogados|escribanos))',
    r'\bbuen fin\b', r'\binapam\b', r'\bhot sale\b', r'black friday', r'cyber ?monday',
    r'crucer', r'turismo receptivo', r'\bviking (mira|\w+ cruise)', r'royal caribbean', r'carnival (cruise|corporation)', r'norwegian cruise',
    r'\baccidente (de transito|vial|con un)\b', r'empotrad', r'\batropell',
    r'\b(fallec\w*|muere|murio|cadaver|homicid\w*|asesina\w*)\b', r'(hallan|encuentran) muert[oa]', r'(hallan|localizado|rescatan) (el )?cuerpo', r'en memoria de', r'hasta siempre',
    r'\bfutbol', r'\bhoroscopo', r'\bquiniela', r'\bloteria',
    # Policiales de narcotráfico en puertos: no cambian reglas ni costos del comercio.
    r'cocain', r'narcotrafic', r'estupefacient', r'marihuana', r'\bkilos? de (droga|coca)\b',
    # Premios, aniversarios y notas institucionales.
    r'galardon', r'recibe (un |el )?(premio|reconocimiento)',
    # Obras y servicios sin efecto en el comercio de mercaderías.
    r'locomotoras? para (el|la) (metro|linea)', r'turbina eolica', r'\bferr(y|ies) electric', r'redes [56]g\b', r'\b[56]g y [56]g\b', r'artesan',
    # Ofertas de productos de consumo ("envío gratis y sin aduanas"), concursos, fiestas y curiosidades (11/10).
    r'envio gratis.{0,80}(llega|por \d|euros|dolares)', r'\bchollo', r'precio minimo historico', r'\bcupon(es)? de descuento',
    r'concurso de (pintura|dibujo|fotografia|cuentos?|poesia)', r'pintura infantil', r'eleccion de la (reina|embajadora|soberana)',
    r'\breina (nacional|provincial) de', r'reconocid[oa] por (el )?programa', r'tazas? comestible',
]

# Medios que no se publican aunque su nota hable de comercio: órganos de propaganda de partes en un conflicto armado
# y medios estatales sancionados por la UE. Se descartan al ingresar y se retiran del archivo (también como "+N medios").
BLOCKED_DOMAINS = ['saba.ye', 'almasirah.net', 'almasirah.net.ye', 'rt.com', 'sputniknews.com', 'sputniknews.lat', 'mundo.sputniknews.com',
                   'hispantv.com', 'presstv.ir', 'irna.ir', 'tasnimnews.com', 'farsnews.ir', 'kcna.kp']


def blocked(name: str = '', url: str = '') -> bool:
    """True si el medio (nombre o dirección) está en BLOCKED_DOMAINS."""
    host = re.sub(r'^https?://', '', (url or '').lower()).split('/')[0].split(':')[0]
    name = re.sub(r'^https?://', '', (name or '').lower().strip()).split('/')[0]
    for d in BLOCKED_DOMAINS:
        for h in (host, name):
            if h and (h == d or h.endswith('.' + d)):
                return True
    return False


# Etiquetas propias detectadas en el texto (las categorías de los feeds traían ruido: "inter", "ig", "Titulares"…)
TAG_RULES = [
    ('ARCA', [r'\barca\b']), ('Mercosur', [r'mercosur']), ('UE-Mercosur', [r'(ue|union europea)[- ]mercosur']),
    ('OMC', [r'\bomc\b', r'\bwto\b']), ('Fletes', [r'flete', r'freight']), ('Contenedores', [r'portacontenedores', r'contenedores maritimos', r'\bteu\b', r'\bfeu\b']),
    ('Acero', [r'acero', r'siderurg']), ('Aluminio', [r'aluminio']), ('Soja', [r'\bsoja']), ('Carne', [r'\bcarne']),
    ('Minería', [r'miner', r'\blitio', r'cobre']), ('Energía', [r'vaca muerta', r'petrol', r'\bgnl\b', r'\bgas\b']),
    ('Autos', [r'\bautos?\b', r'vehicul', r'automotr']), ('Agro', [r'agro', r'granos', r'cereal']),
    ('Cruceros', [r'crucer']), ('Puertos', [r'\bpuerto']), ('Medidas comerciales', [r'antidumping', r'salvaguard', r'represalia']),
    ('Aranceles', [r'arancel']), ('Estados Unidos', [r'estados unidos', r'eeuu', r'trump']), ('China', [r'\bchina\b']),
]


# ---------------------------------------------------------------- utilidades
def norm(s: str) -> str:
    s = unicodedata.normalize('NFD', (s or '').lower())
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def clean_text(s: str) -> str:
    s = html.unescape(s or '')
    s = re.sub(r'<(script|style)[\s\S]*?</\1>', ' ', s, flags=re.I)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def trim(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(' ', 1)[0].rstrip(' ,.;:')
    return cut + '…'


def slug(s: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', norm(clean_text(s))).strip('-')
    return s[:80].rstrip('-') or 'nota'


def canonical_url(u: str) -> str:
    """Normaliza la URL para deduplicar: sin fragmento ni parámetros de seguimiento."""
    try:
        p = urlparse((u or '').strip())
    except ValueError:
        return (u or '').strip()
    if not p.scheme:
        return (u or '').strip()
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith(('utm_', 'fbclid', 'gclid', 'mc_')) and k.lower() not in ('ref', 'source', 'oc')]
    path = p.path.rstrip('/') or '/'
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, '', urlencode(q), ''))


def parse_date(value):
    if not value:
        return None
    value = str(value).strip()
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ if len(value) <= 10 else timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def any_match(text: str, patterns) -> bool:
    return any(re.search(p, text) for p in patterns)


def fetch(url: str, limit: int = 4_000_000, accept: str = 'application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1') -> bytes:
    req = Request(url, headers={'User-Agent': UA, 'Accept': accept, 'Accept-Language': 'es-AR,es;q=0.9,en;q=0.6'})
    with urlopen(req, timeout=25) as r:
        return r.read(limit)


# ---------------------------------------------------------------- RSS / Atom
def local(tag: str) -> str:
    return tag.split('}')[-1].lower()


def child_text(el, names):
    for child in el:
        if local(child.tag) in names:
            txt = ''.join(child.itertext()).strip()
            if txt:
                return txt
    return ''


def child_link(el):
    for child in el:
        if local(child.tag) == 'link':
            rel = child.attrib.get('rel', 'alternate')
            href = child.attrib.get('href')
            if href and rel == 'alternate':
                return href.strip()
            if (child.text or '').strip():
                return child.text.strip()
    for child in el:
        if local(child.tag) == 'guid' and child.attrib.get('isPermaLink', 'true') != 'false' and (child.text or '').startswith('http'):
            return child.text.strip()
    return ''


def feed_image(el, base: str) -> str:
    """Imagen que la propia fuente publica en el feed (media:content, media:thumbnail, enclosure o <img>)."""
    best, best_w = '', -1
    for child in el.iter():
        name = local(child.tag)
        url = child.attrib.get('url') or child.attrib.get('href') or ''
        typ = child.attrib.get('type', '')
        medium = child.attrib.get('medium', '')
        if name in ('content', 'thumbnail') and url and (medium == 'image' or typ.startswith('image') or name == 'thumbnail'
                                                        or re.search(r'\.(jpe?g|png|webp)(\?|$)', url, re.I)):
            w = int(child.attrib.get('width') or 0)
            if w > best_w:
                best, best_w = url, w
        elif name == 'enclosure' and url and typ.startswith('image'):
            if best_w < 0:
                best, best_w = url, 0
    if not best:
        raw = ''.join(child_text(el, [n]) for n in ('encoded', 'content', 'description', 'summary'))
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html.unescape(raw), re.I)
        if m:
            best = m.group(1)
    return absolute_image(best, base)


def absolute_image(url: str, base: str) -> str:
    if not url:
        return ''
    url = urljoin(base, html.unescape(url.strip()))
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    if not url.startswith('https://') or BAD_IMAGE.search(url):
        return ''
    return url


def meta_content(raw: str, props) -> str:
    for prop in props:
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)["\']',
                    rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(prop)}["\']'):
            m = re.search(pat, raw, re.I)
            if m and m.group(1).strip():
                return html.unescape(m.group(1)).strip()
    return ''


def page_meta(page_url: str) -> dict:
    """Descripción, imagen y enlace canónico que la página original publica para compartir (og:/twitter:/meta)."""
    try:
        raw = fetch(page_url, limit=800_000, accept='text/html,application/xhtml+xml').decode('utf-8', 'ignore')
    except Exception:
        return {}
    head = raw[: raw.lower().find('</head>')] if '</head>' in raw.lower() else raw[:300_000]
    image = ''
    for prop in ('og:image:secure_url', 'og:image', 'twitter:image', 'twitter:image:src'):
        image = absolute_image(meta_content(head, [prop]), page_url)
        if image:
            break
    desc = clean_text(meta_content(head, ['og:description', 'description', 'twitter:description']))
    canon = ''
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']', head, re.I) or \
        re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']', head, re.I)
    if m and m.group(1).startswith('http'):
        canon = html.unescape(m.group(1))
    return {'image': image, 'desc': desc, 'canonical': canon}


def og_image(page_url: str) -> str:
    """Busca la imagen principal (og:image / twitter:image) en la página original."""
    return page_meta(page_url).get('image', '')


# ---------------------------------------------------------------- Google Noticias
def is_gnews(url: str) -> bool:
    return urlparse(url or '').netloc.endswith('news.google.com')


def gnews_id(url: str) -> str:
    parts = urlparse(url).path.split('/')
    return parts[parts.index('articles') + 1] if 'articles' in parts and parts.index('articles') + 1 < len(parts) else ''


def gnews_resolve(url: str) -> str:
    """Devuelve el enlace del medio detrás de un enlace de Google Noticias, o '' si no se pudo.

    Formato viejo: el enlace va codificado en base64 dentro del id. Formato actual (AU_yqL…): Google exige
    pedirlo con la firma y la marca de tiempo que publica en la página del artículo."""
    import base64
    gid = gnews_id(url)
    if not gid:
        return ''
    try:
        raw = base64.urlsafe_b64decode(gid + '=' * (-len(gid) % 4))
        if raw.startswith(b'\x08\x13"'):
            raw = raw[3:]
            n, raw = raw[0], raw[1:]
            if n >= 0x80:
                n, raw = (n & 0x7f) | (raw[0] << 7), raw[1:]
            cand = raw[:n].decode('utf-8', 'ignore')
            if cand.startswith('http'):
                return cand
    except Exception:
        pass
    try:
        page = ''
        for base in ('https://news.google.com/articles/', 'https://news.google.com/rss/articles/'):
            try:
                page = fetch(base + gid, limit=600_000, accept='text/html').decode('utf-8', 'ignore')
            except Exception:
                continue
            if 'data-n-a-sg' in page:
                break
        sig = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not sig or not ts:
            return ''
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                 f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sig.group(1)}"]')
        body = urlencode({'f.req': json.dumps([[['Fbv4je', inner, None, 'generic']]])}).encode()
        req = Request('https://news.google.com/_/DotsSplashUi/data/batchexecute', data=body, headers={
            'User-Agent': UA, 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'})
        with urlopen(req, timeout=25) as r:
            txt = r.read(400_000).decode('utf-8', 'ignore')
        chunk = txt.split('\n\n', 1)[1] if '\n\n' in txt else txt
        data = json.loads(chunk)
        decoded = json.loads(data[0][2])[1]
        return decoded if isinstance(decoded, str) and decoded.startswith('http') else ''
    except Exception:
        return ''


# Fotos de los medios: desactivadas. Mostrar la foto de otro medio sin licencia expone a reclamos de derecho de autor
# (las fotos suelen ser de agencias o fotógrafos, no del medio). Las notas usan fotos de archivo con licencia.
# Para habilitar una fuente puntual que lo autorice por escrito: "use_source_images": true en sources.json.
USE_SOURCE_IMAGES_DEFAULT = False

# ---------------------------------------------------------------- limpieza de textos
BYLINE = re.compile(r'^(Por\s+[^@\n]{2,60}?\s+@\w+\s*|Por\s+Redacci[oó]n\s*[|:–-]?\s*)', re.I)
# Pie que agregan los feeds de WordPress: «La entrada <título> se publicó primero en <medio>.»
WP_FOOTER = re.compile(r'\s*(?:\[(?:…|\.\.\.)\]\s*)?(?:La entrada .+? se public[oó] primero en|The post .+? appeared first on) .+?\.?\s*$', re.S)
GENERIC_SUMMARY = re.compile(r'^Nota publicada por .*Abrí el artículo original', re.I)
# Descripciones genéricas del sitio del medio (eslóganes que algunas páginas ponen en todas sus notas):
# no resumen nada, así que no se muestran.
SITE_SLOGAN = re.compile(
    r'^(?:Mantenete informad|Mantente informad|Manténgase informad|Enterate de|Entérate de|Informate|Infórmate|Suscrib'
    r'|Encontr[aá] (?:las|toda)|Toda la informaci[oó]n|Todas las noticias|[UÚ]ltimas noticias'
    r'|Noticias (?:del [aá]mbito|de [uú]ltimo momento|de hoy|actualizadas)|El diario (?:l[ií]der|de)|Portal de noticias'
    r'|Le[eé] las [uú]ltimas|Sitio oficial|P[aá]gina oficial|Bienvenid)', re.I)


def clean_summary(s: str) -> str:
    s = BYLINE.sub('', (s or '').strip()).strip()
    cut = WP_FOOTER.sub('', s).strip()
    if cut != s:
        s = cut.rstrip(' ,;:') + ('' if cut.endswith(('.', '…', '!', '?')) else '…') if cut else ''
    s = re.sub(r'\s*\[(?:…|\.\.\.)\]\s*$', '…', s)   # «[…]» final de WordPress → «…»
    return '' if GENERIC_SUMMARY.match(s) or SITE_SLOGAN.match(s) or len(s) < 40 else s


# ---------------------------------------------------------------- nombres de medios y títulos en mayúsculas
# Google Noticias a veces informa el medio como dominio («www.abc.com.py»): se muestra con su nombre.
OUTLET_NAMES = {
    'abc.com.py': 'ABC Color', 'latribuna.com.py': 'La Tribuna', 'moncloa.com': 'Moncloa.com',
    'diariopinion.com.ar': 'Diario Opinión', 'noticiasdebariloche.com.ar': 'Noticias de Bariloche',
    'logistica360chile.cl': 'Logística 360 Chile', 'soy-chile.cl': 'Soy Chile', 'zonazero.cl': 'Zona Zero',
    'revistaeyn.com': 'Revista E&N', 'diariodelpuerto.com': 'Diario del Puerto', 'metrolibre.com': 'Metro Libre',
    'frequencynews.ca': 'Frequency News', 'elmiercolesdigital.com.ar': 'El Miércoles Digital',
    'clarin.com': 'Clarín', 'infobae.com': 'Infobae', 'lmneuquen.com': 'LM Neuquén', 'radionacional.com.ar': 'Radio Nacional',
    'euronews.com': 'Euronews', 'rtve.es': 'RTVE', 'lacapital.com.ar': 'La Capital', 'larepublica.co': 'La República',
    'portafolio.co': 'Portafolio', 'lne.es': 'La Nueva España', 'vanguardia.com.mx': 'Vanguardia MX',
    'econojournal.com.ar': 'EconoJournal', 'mercado.com.ar': 'Revista Mercado', 'soychile.cl': 'Soy Chile',
    'merca2.es': 'Merca2', 'motor.es': 'Motor.es', 'cadenadesuministro.es': 'Cadena de Suministro', 'gub.uy': 'Gobierno de Uruguay',
    'argentina.gob.ar': 'Argentina.gob.ar', 'revistachacra.com.ar': 'Revista Chacra', 'diariojujuy.com.ar': 'Diario Jujuy',
    'canal26.com': 'Canal 26', 'elheraldo.com.ar': 'El Heraldo', 'economis.com.ar': 'Economis', 'dolarhoy.com': 'DolarHoy',
    'lacamara.pe': 'La Cámara (CCL)', 'dataportuaria.com': 'Data Portuaria', 'ace-cargadores.com': 'Asociación de Cargadores de España',
    'korea.net': 'Korea.net', 'diaadia.com.pa': 'Día a Día', '7dias.com.do': '7 Días', 'negocios.com': 'Negocios.com',
}
OUTLET_ALIASES = {'investing.com méxico - finanzas, bolsa y forex': 'Investing.com México'}
DOMAIN_RX = re.compile(r'^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)/?$', re.I)


def outlet_name(name: str, url: str = '') -> str:
    n = (name or '').strip()
    n = OUTLET_ALIASES.get(n.lower(), n)
    m = DOMAIN_RX.match(n)
    if not m:
        return n
    dom = m.group(1).lower()
    return OUTLET_NAMES.get(dom, dom)


# Siglas y nombres propios que deben quedar en mayúsculas o con inicial mayúscula al bajar un título en mayúsculas.
CAPS_KEEP = {'arca', 'afip', 'indec', 'bcra', 'senasa', 'omc', 'oma', 'ue', 'eeuu', 'ee.uu.', 'eua', 'fob', 'cif', 'iva',
             'pbi', 'pyme', 'pymes', 'rigi', 'vucea', 'sim', 'ata', 'cda', 'ipcva', 'ypf', 'usd', 'oea', 'tmec', 't-mec', 'bid'}
CAPS_PROPER = {'mercosur': 'Mercosur', 'aduana': 'Aduana', 'posadas': 'Posadas', 'comex': 'COMEX', 'sial': 'SIAL'}


def fix_caps(title: str) -> str:
    """Un título TODO EN MAYÚSCULAS pasa a mayúscula inicial, conservando siglas, países y nombres conocidos."""
    letters = [c for c in title if c.isalpha()]
    if len(letters) < 12 or sum(c.isupper() for c in letters) / len(letters) < 0.9:
        return title
    proper = {}
    for country in COUNTRIES:
        for x in country.split():
            if x[:1].isupper():
                proper[x.lower()] = x
    out = []
    for i, w in enumerate(re.split(r'(\s+)', title.lower())):
        core = w.strip('¿?¡!:;,.«»"()')
        if core in CAPS_KEEP:
            w = w.replace(core, core.upper())
        elif core in CAPS_PROPER:
            w = w.replace(core, CAPS_PROPER[core])
        elif core in proper:
            w = w.replace(core, proper[core])
        out.append(w)
    s = ''.join(out)
    # Mayúscula al inicio y después de «:», «¿», «¡», «?» o «.»
    s = re.sub(r'(^|[:.?!]\s+|[¿¡])([a-záéíóúñ])', lambda m: m.group(1) + m.group(2).upper(), s)
    return s


def excluded(text: str) -> bool:
    return any_match(norm(text), EXCLUDE)


def parse_feed(raw: bytes, source: dict):
    root = ET.fromstring(raw)
    nodes = [x for x in root.iter() if local(x.tag) in ('item', 'entry')]
    out = []
    for n in nodes:
        title = clean_text(child_text(n, ['title']))
        link = child_link(n)
        if not title or not link:
            continue
        desc_raw = child_text(n, ['description', 'summary', 'encoded', 'content'])
        desc = clean_text(desc_raw)
        origin = ''
        if source.get('aggregator'):
            # Google News: "Título - Medio" y <source url="…">Medio</source>
            for child in n:
                if local(child.tag) == 'source':
                    origin = (child.text or '').strip()
            if origin and title.endswith(' - ' + origin):
                title = title[: -len(origin) - 3].strip()
            desc = ''  # la descripción del agregador solo repite el título
        pub = parse_date(child_text(n, ['pubdate', 'published', 'updated', 'date', 'issued']))
        author = clean_text(child_text(n, ['creator', 'author']))
        cats = [clean_text(c.text or c.attrib.get('term', '')) for c in n if local(c.tag) == 'category']
        out.append({'title': title, 'url': link, 'desc': desc, 'pub': pub, 'author': author, 'origin': origin,
                    'categories': [c for c in cats if c][:6], 'image': '' if source.get('aggregator') else feed_image(n, link)})
    return out


# ---------------------------------------------------------------- clasificación
def classify(text: str, source: dict):
    t = norm(text)
    countries = [c for c, pats in COUNTRIES.items() if any_match(t, pats)]
    topics = [name for name, pats in TOPIC_RULES if any_match(t, pats)]
    if 'Argentina' in countries:
        topics.insert(0, 'Argentina')
    if any(c in LATAM for c in countries if c != 'Argentina'):
        topics.append('Latinoamérica')
    if 'mercosur' in t:
        topics.append('Mercosur')
    if 'Estados Unidos' in countries:
        topics.append('Estados Unidos')
    if 'China' in countries:
        topics.append('China')
    if any(c in ASIA for c in countries):
        topics.append('Asia')
    if any(c in EUROPE for c in countries):
        topics.append('Europa')
    if any(c in OCEANIA for c in countries):
        topics.append('Oceanía')
    if not topics:  # las categorías por defecto de la fuente solo se usan si no se detectó ninguna
        topics.extend(source.get('topics', []))
    seen, clean = set(), []
    for x in topics:
        if x in TOPICS and x not in seen:
            seen.add(x)
            clean.append(x)
    if not clean:
        clean = ['Economía internacional']
    if not countries:
        countries = list(source.get('countries') or ['Global'])
    visual = next((v for v, pats in VISUAL_RULES if any_match(t, pats)), 'globe')
    affects = 'Argentina' in countries or 'mercosur' in t
    impact = 2 if affects and any(x in clean for x in ('Aranceles', 'Regulaciones', 'Aduanas', 'Impuestos')) else 1
    return clean[:5], countries[:5], visual, affects, impact


def make_tags(text: str, source: dict) -> list:
    t = norm(text)
    tags = list(source.get('tags') or []) + [name for name, pats in TAG_RULES if any_match(t, pats)]
    return list(dict.fromkeys(tags))[:5]


def strong(text: str) -> bool:
    return any_match(norm(text), STRONG)


def relevance(text: str) -> int:
    t = norm(text)
    return sum(1 for k in RELEVANCE if norm(k) in t)


def title_key(title: str) -> set:
    words = [w for w in re.findall(r'[a-z0-9]+', norm(title)) if len(w) > 3]
    return set(words)


def near_duplicate(title: str, recent: list) -> bool:
    a = title_key(title)
    if len(a) < 4:
        return False
    for b in recent:
        if b and len(a & b) / len(a | b) >= 0.6:
            return True
    return False


# ---------------------------------------------------------------- duplicados de notas curadas
STOPWORDS = set('''para como sobre entre desde hasta tras ante contra durante mientras segun este esta estos estas pero porque
cuando donde cual cuales quien tiene tienen sera puede hace mas menos muy todo toda todos todas otro otra otros otras sus
nuevo nueva nuevos nuevas millones dolares anos with from that this have will their more over after into about tambien'''.split())


def tokens(text: str) -> set:
    """Palabras significativas y cifras (las cifras se marcan con #; los años no cuentan)."""
    t = norm(text)
    words = {w for w in re.findall(r'[a-z]+', t) if len(w) > 3 and w not in STOPWORDS}
    nums = {'#' + n for n in re.findall(r'\d+(?:[.,]\d+)?', t) if len(n) >= 2 and not re.fullmatch(r'(19|20)\d\d', n)}
    return words | nums


def detect_countries(text: str) -> set:
    t = norm(text)
    return {c for c, pats in COUNTRIES.items() if any_match(t, pats)}


class CuratedIndex:
    """Índice de las notas curadas para detectar automáticas que cuentan el mismo hecho."""

    def __init__(self, items: list):
        import math
        docs = [tokens(it.get('title', '') + ' ' + it.get('summary', '')) for it in items]
        df = {}
        for d in docs:
            for w in d:
                df[w] = df.get(w, 0) + 1
        n = len(docs)
        # Las palabras raras pesan más; las cifras, todavía más.
        self.idf = lambda w: math.log((n + 1) / (df.get(w, 0) + 1)) + (1.5 if w.startswith('#') else 0)
        self.items, self.absorbed, self.urls = [], {}, {}
        for it in items:
            if it.get('label') == 'Automática' or not it.get('title'):
                continue
            for a in it.get('absorbs') or []:
                self.absorbed[a] = it['id']
            for src in it.get('sources') or []:
                if src.get('url'):
                    self.urls[canonical_url(src['url'])] = it['id']
            extra = ' '.join([' '.join(it.get('tags') or []), ' '.join(it.get('countries') or [])])
            key_data = ' '.join(' '.join(map(str, k)) for k in it.get('keyData') or [])
            self.items.append({
                'id': it['id'],
                'when': parse_date(it.get('datetime') or it.get('date')),
                'full': tokens(' '.join([it['title'], it.get('summary', ''), extra, key_data])),
                'title': tokens(it['title'] + ' ' + ' '.join(it.get('tags') or [])),
                'countries': set(it.get('countries') or []) | detect_countries(it['title'] + ' ' + it.get('summary', '')),
            })

    def match(self, item_id: str, url: str, title: str, when) -> str:
        """Devuelve el id de la nota curada que ya cubre el hecho, o '' si no hay ninguna."""
        if item_id in self.absorbed:
            return self.absorbed[item_id]
        if url and canonical_url(url) in self.urls:
            return self.urls[canonical_url(url)]
        a = tokens(title)
        if len(a) < 3 or not when:
            return ''
        total = sum(self.idf(w) for w in a)
        countries = detect_countries(title)
        best, best_score = '', 0.0
        for c in self.items:
            if not c['when'] or abs((when - c['when']).total_seconds()) > CURATED_WINDOW_DAYS * 86400:
                continue
            if not countries <= c['countries']:  # menciona un país que la nota curada no trata
                continue
            full = sum(self.idf(w) for w in a & c['full']) / total
            title_sim = sum(self.idf(w) for w in a & c['title']) / total
            shared_number = any(w.startswith('#') for w in a & c['full'])
            if not shared_number and len(a & c['full']) < CURATED_MIN_SHARED:
                continue
            if (full >= CURATED_MIN_FULL and title_sim >= CURATED_MIN_TITLE
                    and (title_sim >= CURATED_STRONG or shared_number or full >= 0.70) and full > best_score):
                best, best_score = c['id'], full
        return best


# ---------------------------------------------------------------- agrupamiento de automáticas
def item_score(it: dict) -> tuple:
    """Cuál nota de un grupo queda como principal: con resumen, con foto, de un medio propio (no agregador), la primera."""
    src = (it.get('sources') or [{}])[0]
    return (1 if clean_summary(it.get('summary', '')) else 0, 1 if it.get('photo') else 0,
            0 if 'vía' in src.get('type', '') else 1,
            -(parse_date(it.get('datetime') or it.get('date')) or NOW).timestamp())


SYNONYMS = {'smartphones': 'celulares', 'smartphone': 'celular', 'telefonos': 'celulares', 'moviles': 'celulares',
            'derogacion': 'elimina', 'deroga': 'elimina', 'supresion': 'elimina', 'quitado': 'elimina', 'quita': 'elimina',
            'eliminacion': 'elimina', 'disparan': 'dispara', 'dispararon': 'dispara', 'fabricacion': 'produccion'}


# Nombres de países, gentilicios y meses: no cuentan como palabras en común (los países se comparan aparte).
PLACE_WORDS = set('''argentina argentino argentina argentinos argentinas estados unidos estadounidense estadounidenses
brasil brasileno brasilena brasilenos chile chileno chilena china chino china chinos chinas europa europeo europea europeos
union europea mexico mexicano mexicana espana espanol espanola canada canadiense japon japones india paraguay uruguay
reino unido britanico peru bolivia colombia panama rusia ucrania turquia vietnam corea mundo mundial global latinoamerica
enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre diciembre'''.split())


def stems(text: str) -> set:
    """Palabras significativas reducidas a su raíz (6 letras), con sinónimos: «importaciones», «importación» e
    «importados» cuentan como la misma palabra; «smartphones» como «celulares»."""
    out = set()
    for w in re.findall(r'[a-z]+', norm(text)):
        w = SYNONYMS.get(w, w)
        if len(w) > 3 and w not in STOPWORDS and w not in PLACE_WORDS:
            out.add(w[:6])
    return out


def key_numbers(text: str) -> set:
    """Cifras distintivas (746, 746,6, 693-24…): no años, ni números chicos que se repiten en cualquier nota."""
    t = norm(text)
    nums = set()
    for n in re.findall(r'\d+(?:[.,]\d+)?', t):
        base = re.split(r'[.,]', n)[0]
        if re.fullmatch(r'(19|20)\d\d', n) or len(base) < 3 or n in ('100', '000'):
            continue
        nums.add(base)
    return nums


MONTHS_RE = r'\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b'


def group_auto(items: list) -> list:
    """Une las notas automáticas que cuentan el mismo hecho con distinto título.

    Queda una sola nota (la más completa); las demás pasan a "También en" (fuentes con alsoIn: true) y sus IDs
    se guardan en mergedIds para redirigir sus páginas viejas. Devuelve la lista de grupos formados."""
    import math
    auto = [it for it in items if it.get('label') == 'Automática']
    docs = {id(it): stems(it.get('title', '')) for it in auto}
    nums = {id(it): key_numbers(it.get('title', '') + ' ' + it.get('summary', '')) for it in auto}
    ctx = {id(it): stems(it.get('title', '') + ' ' + it.get('summary', '')) for it in auto}
    df = {}
    for d in ctx.values():
        for w in d:
            df[w] = df.get(w, 0) + 1
    n = len(auto) or 1
    idf = lambda w: math.log((n + 1) / (df.get(w, 0) + 1))
    when = {id(it): parse_date(it.get('datetime') or it.get('date')) or NOW for it in auto}
    auto.sort(key=lambda it: when[id(it)])
    num_df = {}
    for ns in nums.values():
        for x in ns:
            num_df[x] = num_df.get(x, 0) + 1
    countries = {id(it): detect_countries(it.get('title', '')) for it in auto}
    months = {id(it): set(re.findall(MONTHS_RE, norm(it.get('title', '')))) for it in auto}

    def sim_title(ta, tb) -> float:
        shared = ta & tb
        return sum(idf(w) for w in shared) / (min(sum(idf(w) for w in ta), sum(idf(w) for w in tb)) or 1)

    def same_event(a, b) -> str:
        """Regla que une a y b ('' si no son el mismo hecho)."""
        ta, tb = docs[id(a)], docs[id(b)]
        if len(ta) < 3 or len(tb) < 3:
            return ''
        ca, cb = countries[id(a)], countries[id(b)]
        if ca and cb and not (ca & cb):
            return ''  # mismos términos, distintos países: no es el mismo hecho
        if months[id(a)] and months[id(b)] and not (months[id(a)] & months[id(b)]):
            return ''  # el dato de julio y el de agosto no son la misma noticia
        shared = ta & tb
        if len(shared) >= GROUP_MIN_SHARED and sim_title(ta, tb) >= GROUP_MIN_SIM:
            return 'titulo'
        # Misma cifra distintiva (746%, decreto 693-24…) más palabras raras en común en título y resumen.
        rare = {w for w in (ctx[id(a)] & ctx[id(b)]) if idf(w) >= GROUP_RARE_IDF}
        rare_title = {w for w in shared if idf(w) >= GROUP_RARE_IDF}
        num = {x for x in nums[id(a)] & nums[id(b)] if num_df[x] <= 6}
        if num and rare_title and (len(rare) >= 3 or any(num_df[x] <= 3 for x in num)):
            return 'cifra'
        # Títulos parecidos con al menos dos palabras raras en común (p. ej. «arroz» y «elimina»).
        if len(rare_title) >= 2 and len(rare) >= 4 and sim_title(ta, tb) >= GROUP_MIN_SIM_RARE:
            return 'raras'
        return ''

    def same_title(a, b) -> bool:
        if (when[id(b)] - when[id(a)]).total_seconds() > GROUP_SAME_TITLE_DAYS * 86400:
            return False
        ta, tb = docs[id(a)], docs[id(b)]
        return len(ta) >= 4 and (ta == tb or (len(ta & tb) >= 5 and sim_title(ta, tb) >= 0.9))

    # Grupos sin encadenar: una nota entra a un grupo solo si coincide con un tercio o más de sus miembros
    # (evita que A≈B y B≈C terminen uniendo A con C cuando hablan de cosas distintas).
    groups_tmp = []
    for i, b in enumerate(auto):
        best, best_hits = None, 0
        for g in groups_tmp:
            if (when[id(b)] - when[id(g[0])]).total_seconds() > GROUP_WINDOW_HOURS * 3600 and \
                    not any(same_title(a, b) for a in g):
                continue  # fuera de la ventana solo se une el mismo título (otro medio que la levanta días después)
            hits = sum(1 for a in g if same_event(a, b))
            if hits and hits * 3 >= len(g) and hits > best_hits:
                best, best_hits = g, hits
        if best is not None:
            best.append(b)
        else:
            groups_tmp.append([b])
    clusters = {id(g[0]): g for g in groups_tmp}
    drop, groups = set(), []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=item_score, reverse=True)
        main, rest = members[0], members[1:]
        seen = {canonical_url(s.get('url', '')) for s in main.get('sources') or []}
        merged = list(main.get('mergedIds') or [])
        for o in rest:
            for s in o.get('sources') or []:
                u = canonical_url(s.get('url', ''))
                if u and u not in seen:
                    seen.add(u)
                    main.setdefault('sources', []).append({'name': s.get('name', ''), 'type': 'También publicó esta noticia',
                                                           'url': s['url'], 'alsoIn': True, 'title': o.get('title', '')})
            for oid in [o.get('id')] + list(o.get('mergedIds') or []):
                if oid and oid not in merged and oid != main.get('id'):
                    merged.append(oid)
            for k in ('aliasUrls',):
                main[k] = sorted((set(main.get(k) or []) | set(o.get(k) or []) | {canonical_url(primary_url(o)),
                                  canonical_url(o.get('gnUrl', ''))}) - {''})
            if not main.get('photo') and o.get('photo'):
                main['photo'] = o['photo']
            main['affectsArgentina'] = bool(main.get('affectsArgentina') or o.get('affectsArgentina'))
            drop.add(id(o))
        main['mergedIds'] = merged
        groups.append({'main': main.get('id'), 'merged': [o.get('id') for o in rest]})
    items[:] = [it for it in items if id(it) not in drop]
    return groups


# ---------------------------------------------------------------- archivo
def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        print(f'Aviso: no se pudo leer {path.name}; se usa un archivo vacío.', file=sys.stderr)
        return default


def primary_url(it: dict) -> str:
    srcs = it.get('sources') or [{}]
    p = next((s for s in srcs if s.get('primary')), srcs[0])
    return p.get('url', '')


def resolve_gnews(by_url: dict) -> dict:
    """Reemplaza el enlace de Google Noticias por el del medio y toma su descripción e imagen públicas."""
    pending = [(k, it) for k, it in by_url.items()
               if it.get('label') == 'Automática' and is_gnews(primary_url(it))
               and (parse_date(it.get('gnTried')) or NOW - timedelta(days=9)) < NOW - timedelta(hours=20)]
    pending.sort(key=lambda kv: parse_date(kv[1].get('datetime') or kv[1].get('date')) or NOW, reverse=True)
    stats = {'pending': len(pending), 'resolved': 0, 'withSummary': 0, 'withPhoto': 0, 'failed': 0, 'duplicates': 0}
    for key, it in pending[:MAX_GN_RESOLVE]:
        it['gnTried'] = iso(NOW)
        gurl = primary_url(it)
        real = gnews_resolve(gurl)
        if not real or is_gnews(real):
            stats['failed'] += 1
            continue
        stats['resolved'] += 1
        meta = page_meta(real)
        canon = meta.get('canonical') or ''
        if canon and urlparse(canon).netloc.replace('www.', '') == urlparse(real).netloc.replace('www.', ''):
            real = canon
        new_key = canonical_url(real)
        if new_key in by_url and by_url[new_key] is not it:
            # La misma nota ya entró por otra fuente: queda esa, y este enlace se recuerda como alias.
            other = by_url[new_key]
            other['aliasUrls'] = sorted(set(other.get('aliasUrls') or []) | {canonical_url(gurl)})
            del by_url[key]
            stats['duplicates'] += 1
            continue
        src = next((s for s in it['sources'] if s.get('primary')), it['sources'][0])
        src['url'] = real
        it['gnUrl'] = gurl
        desc = clean_summary(meta.get('desc', ''))
        if desc and len(desc) >= 60 and norm(desc)[:60] != norm(it['title'])[:60] and not excluded(desc):
            it['summary'] = trim(desc, 320)
            stats['withSummary'] += 1
        if meta.get('image') and not it.get('photo') and USE_SOURCE_IMAGES_DEFAULT:
            it['photo'] = {'src': meta['image'], 'by': src.get('name', ''), 'page': real, 'alt': it['title'][:140]}
            stats['withPhoto'] += 1
        del by_url[key]
        by_url[new_key] = it
    return stats


def put_keep_curated(index: dict, key: str, it: dict) -> None:
    """Guarda la nota en el índice por URL. Si la clave ya está ocupada, gana la nota curada:
    una automática con la misma URL principal que una curada nunca la reemplaza."""
    prev = index.get(key)
    if prev is None:
        index[key] = it
        return
    prev_cur, it_cur = prev.get('label') != 'Automática', it.get('label') != 'Automática'
    if it_cur and not prev_cur:
        index[key] = it
    elif it_cur and prev_cur and prev.get('id') != it.get('id'):
        index['curada:' + it.get('id', '')] = it  # dos curadas con la misma fuente principal: se conservan las dos


def main():
    sources = load_json(ROOT / 'sources.json', [])
    store = load_json(NEWS, {})
    status = load_json(STATUS, {})
    items = store.get('items', [])

    # Índices del archivo. Clave única: URL original normalizada.
    by_url, ids = {}, set()
    for it in items:
        if not it.get('title'):
            continue
        key = canonical_url(primary_url(it)) or it.get('id')
        put_keep_curated(by_url, key, it)  # limpia duplicados heredados; una curada nunca se pierde
    ids = {it.get('id') for it in by_url.values()}
    # Enlaces de notas fusionadas en otra (agrupamiento) o de Google Noticias ya resueltos: apuntan a la nota que quedó.
    alias = {}
    for k, it in by_url.items():
        for u in list(it.get('aliasUrls') or []) + [it.get('gnUrl', '')] + \
                [s.get('url', '') for s in it.get('sources') or [] if s.get('alsoIn')]:
            if u and canonical_url(u) not in by_url:
                alias[canonical_url(u)] = k
    curated = CuratedIndex(list(by_url.values()))
    suppressed = []  # automáticas descartadas o retiradas por repetir una nota curada

    report = []
    for src in sources:
        if not src.get('enabled', True):
            continue
        name = src['name']
        entry = {'source': name, 'url': src['url']}
        try:
            parsed = parse_feed(fetch(src['url']), src)
        except Exception as e:  # una fuente caída no frena a las demás
            st = status.get(name, {})
            st.update({'ok': False, 'lastError': str(e)[:200], 'lastRun': iso(NOW), 'failures': st.get('failures', 0) + 1})
            status[name] = st
            entry.update({'ok': False, 'error': str(e)[:200]})
            report.append(entry)
            continue

        added = updated = skipped = og_used = 0
        max_new = int(src.get('max_new', 15))  # tope de notas nuevas por fuente y corrida
        parsed.sort(key=lambda x: x['pub'] or NOW, reverse=True)
        for p in parsed:
            key = canonical_url(p['url'])
            text = ' '.join([p['title'], p['desc'], ' '.join(p['categories'])])
            if key in alias:
                continue  # ya está dentro de otra nota (agrupada o resuelta desde Google Noticias)
            old = by_url.get(key)
            if old and old.get('label') != 'Automática':
                continue  # nunca pisar una nota curada a mano
            if not old:
                if p['pub'] and p['pub'] < NOW - timedelta(days=MAX_AGE_DAYS):
                    skipped += 1
                    continue
                if src.get('min_relevance', 0) and relevance(text) < src['min_relevance']:
                    skipped += 1
                    continue
                if src.get('aggregator') and not strong(p['title']):
                    skipped += 1
                    continue
                if excluded(p['title'] + ' ' + p['desc']) or blocked(p['origin'], p['url']):
                    skipped += 1
                    continue
                if added >= max_new:  # las repetidas con otro título se agrupan después (group_auto)
                    skipped += 1
                    continue
                dup = curated.match(slug(p['title']), p['url'], p['title'], p['pub'] or NOW)
                if dup:
                    skipped += 1
                    suppressed.append({'title': trim(p['title'], 120), 'url': p['url'], 'curated': dup})
                    continue

            first_seen = parse_date((old or {}).get('firstSeen')) or NOW
            pub = p['pub'] or parse_date((old or {}).get('datetime')) or first_seen
            if pub > first_seen:
                pub = first_seen  # algunos feeds informan una hora posterior a la captura (zona horaria mal declarada)
            topics, countries, visual, affects, impact = classify(text, src)

            if old:
                item_id = old['id']
            else:
                base = slug(p['title'])
                item_id, n = base, 2
                while item_id in ids:
                    item_id = f'{base[:76]}-{n}'
                    n += 1
                ids.add(item_id)

            source_name = outlet_name(p['origin'] or name, p['url'])
            title_txt = fix_caps(p['title'])
            photo = (old or {}).get('photo')
            if not src.get('use_source_images', USE_SOURCE_IMAGES_DEFAULT):
                photo = None
            elif not photo:
                img = p['image']
                if not img and src.get('fetch_og') and og_used < MAX_OG_FETCH and not old:
                    og_used += 1
                    img = og_image(p['url'])
                if img:
                    photo = {'src': img, 'by': source_name, 'page': p['url'], 'alt': p['title'][:140]}

            desc = clean_summary(p['desc'])
            summary = trim(desc, 320) if desc else ''
            if old and not summary and clean_summary(old.get('summary', '')):
                summary = old['summary']  # resumen obtenido antes de la página original (Google Noticias)
            item = {
                'id': item_id,
                'datetime': iso(pub),
                'date': pub.astimezone(TZ).date().isoformat(),
                'firstSeen': iso(first_seen),
                'title': trim(title_txt, 180),
                'summary': summary,
                'body': [trim(desc, 900)] if len(desc) > 320 else [],
                'keyData': [],
                'topics': topics,
                'countries': countries,
                'tags': make_tags(text, src),
                'visual': visual,
                'impact': impact,
                'affectsArgentina': affects,
                'kind': 'noticia',
                'label': 'Automática',
                'feed': name,
                'sources': [{
                    'name': source_name,
                    'type': src.get('type', 'Fuente externa') if not p['origin'] else f"Medio · vía {name.split('·')[0].strip()}",
                    'url': p['url'],
                    'primary': True,
                    'author': p['author'],
                }],
            }
            if photo:
                item['photo'] = photo
            if old:  # conserva lo que agregaron el agrupamiento y la resolución de Google Noticias
                for k in ('mergedIds', 'aliasUrls', 'gnUrl', 'gnTried'):
                    if old.get(k):
                        item[k] = old[k]
                item['sources'] += [s for s in old.get('sources') or [] if s.get('alsoIn')]
                if old.get('title') != item['title']:
                    item['updated'] = iso(NOW)
                elif old.get('updated'):
                    item['updated'] = old['updated']
                if any(old.get(k) != item.get(k) for k in ('title', 'summary', 'datetime', 'photo')):
                    updated += 1
            else:
                added += 1
            by_url[key] = item

        status[name] = {'ok': True, 'lastRun': iso(NOW), 'lastOk': iso(NOW), 'failures': 0,
                        'items': len(parsed), 'added': added}
        entry.update({'ok': True, 'items': len(parsed), 'added': added, 'updated': updated, 'skipped': skipped})
        report.append(entry)

    # Limpieza de notas automáticas ya guardadas (aplica las reglas actuales a lo que entró antes).
    disabled = {s['name'] for s in sources if not s.get('enabled', True)}
    excluded_n = 0
    aggregators = {s['name'] for s in sources if s.get('aggregator')}
    # Un mismo «resumen» en notas con títulos distintos es la descripción genérica del sitio, no un extracto.
    shared = {}
    for it in by_url.values():
        if it.get('label') == 'Automática' and it.get('summary'):
            shared.setdefault(norm(it['summary'])[:120], set()).add(norm(it.get('title', '')))
    for it in by_url.values():
        if it.get('label') == 'Automática' and it.get('summary') and len(shared.get(norm(it['summary'])[:120], ())) > 1:
            it['summary'] = ''
    for key in list(by_url):
        it = by_url[key]
        if it.get('label') != 'Automática':
            continue
        feed = it.get('feed') or (it.get('sources') or [{}])[0].get('name', '')
        via_aggregator = feed in aggregators or 'vía Google Noticias' in (it.get('sources') or [{}])[0].get('type', '')
        srcs = it.get('sources') or []
        if any(blocked(x.get('name', ''), x.get('url', '')) for x in srcs):
            kept = [x for x in srcs if not blocked(x.get('name', ''), x.get('url', ''))]
            if kept and not kept[0].get('alsoIn'):
                it['sources'] = kept
            else:  # el medio bloqueado era el principal: se retira la nota
                excluded_n += 1
                del by_url[key]
                continue
        if feed in disabled or (via_aggregator and not strong(it.get('title', ''))) \
                or excluded(it.get('title', '') + ' ' + it.get('summary', '')):
            excluded_n += 1
            del by_url[key]
            continue
        it['summary'] = clean_summary(it.get('summary', ''))
        it['title'] = fix_caps(it.get('title', ''))
        for x in it.get('sources') or []:
            x['name'] = outlet_name(x.get('name', ''), x.get('url', ''))
        pub, seen = parse_date(it.get('datetime') or ''), parse_date(it.get('firstSeen') or '')
        if pub and seen and pub > seen:
            it['datetime'], it['date'] = iso(seen), seen.astimezone(TZ).date().isoformat()
        if it.get('body'):
            it['body'] = [b for b in (clean_summary(x) for x in it['body']) if b]
        dup = curated.match(it.get('id', ''), primary_url(it), it.get('title', ''),
                            parse_date(it.get('datetime') or it.get('date')))
        if dup:
            suppressed.append({'title': trim(it.get('title', ''), 120), 'url': primary_url(it), 'curated': dup})
            del by_url[key]
            continue
        ph = it.get('photo') or {}
        src_cfg = next((s for s in sources if s.get('name') == feed), {})
        if ph.get('src') and (BAD_IMAGE.search(ph['src']) or not src_cfg.get('use_source_images', USE_SOURCE_IMAGES_DEFAULT)):
            it.pop('photo', None)
        # Reclasifica con las reglas actuales (países, temas, impacto): corrige lo que entró con reglas viejas.
        text = ' '.join([it.get('title', ''), it.get('summary', ''), ' '.join(it.get('body') or [])])
        topics, countries, _, affects, impact = classify(text, src_cfg)
        it.update({'topics': topics, 'countries': countries, 'impact': impact,
                   'affectsArgentina': affects or bool(it.get('mergedIds') and it.get('affectsArgentina'))})
        it['tags'] = make_tags(' '.join([it.get('title', ''), it.get('summary', '')]), {})
        it['visual'] = next((v for v, pats in VISUAL_RULES if any_match(norm(it.get('title', '') + ' ' + it.get('summary', '')), pats)), 'globe')

    # Google Noticias: enlace del medio, descripción e imagen de la página original.
    gn = resolve_gnews(by_url)

    # Notas automáticas que cuentan el mismo hecho: una sola, con "También en".
    values = list(by_url.values())
    groups = group_auto(values)
    by_url = {}
    for it in values:
        put_keep_curated(by_url, canonical_url(primary_url(it)) or it.get('id'), it)

    # Archivo: notas automáticas hasta un año y con tope; las curadas no se borran.
    cutoff = NOW - timedelta(days=ARCHIVE_DAYS)
    kept = []
    for it in by_url.values():
        dt = parse_date(it.get('datetime') or it.get('date'))
        if it.get('label') == 'Automática' and dt and dt < cutoff:
            continue
        kept.append(it)
    kept.sort(key=lambda x: parse_date(x.get('datetime') or x.get('date')) or NOW, reverse=True)
    auto = [i for i in kept if i.get('label') == 'Automática']
    if len(auto) > MAX_AUTO_ITEMS:
        drop = {id(i) for i in auto[MAX_AUTO_ITEMS:]}
        kept = [i for i in kept if id(i) not in drop]

    before = json.dumps(items, ensure_ascii=False, sort_keys=True)
    after = json.dumps(kept, ensure_ascii=False, sort_keys=True)
    store.update({'schemaVersion': '2.1', 'feedId': 'comexplora', 'timezone': 'America/Argentina/Buenos_Aires', 'items': kept})
    if before != after or not store.get('updatedAt'):
        store['updatedAt'] = iso(NOW)
    store['ingestion'] = {'ranAt': iso(NOW), 'sources': report, 'curatedDuplicates': suppressed,
                          'excluded': excluded_n, 'grouped': groups, 'googleNews': gn}
    NEWS.write_text(json.dumps(store, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    ok = sum(1 for r in report if r.get('ok'))
    new = sum(r.get('added', 0) for r in report)
    print(f'Fuentes OK: {ok}/{len(report)} · notas nuevas: {new} · total en archivo: {len(kept)}')
    for d in suppressed:
        print(f"  ↺ repetida de «{d['curated']}»: {d['title']}")
    print(f"Fuera de tema retiradas: {excluded_n} · grupos formados: {len(groups)} "
          f"({sum(len(g['merged']) for g in groups)} notas unidas) · Google Noticias: {gn}")
    for r in report:
        print(('  ✔ ' if r.get('ok') else '  ✘ ') + r['source'] + (f" · {r.get('items', 0)} leídas, {r.get('added', 0)} nuevas" if r.get('ok') else f" · {r.get('error')}"))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write(f'## Actualización de noticias\n\n**{new}** notas nuevas · **{ok}/{len(report)}** fuentes respondieron · {len(kept)} notas en el archivo\n\n')
            if suppressed:
                f.write('**Automáticas omitidas por repetir una nota curada:**\n\n')
                for d in suppressed:
                    f.write(f"- {d['title']} → `{d['curated']}`\n")
                f.write('\n')
            f.write('| Fuente | Estado | Leídas | Nuevas | Detalle |\n|---|---|---|---|---|\n')
            for r in report:
                f.write(f"| {r['source']} | {'✔' if r.get('ok') else '✘'} | {r.get('items', '')} | {r.get('added', '')} | {r.get('error', '') if not r.get('ok') else ''} |\n")


if __name__ == '__main__':
    main()
    # Indicadores de mercado (BCRA, Bolsa de Comercio de Rosario, Brent): una falla no frena la publicación.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import update_indicators
        update_indicators.main()
    except Exception as e:
        print(f'Aviso: no se pudieron actualizar los indicadores de mercado ({e}).', file=sys.stderr)
    # Intercambio comercial argentino (INDEC, datos.gob.ar) para el panel de datos.
    try:
        import update_trade
        update_trade.main()
    except Exception as e:
        print(f'Aviso: no se pudo actualizar el intercambio comercial ({e}).', file=sys.stderr)
