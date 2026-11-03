"""Sursa bdns_ajutoare: ce se poate cere acum din BDNS și unde se aplică (prototipul din atlas, cercetari/bdns/legare.py).

Produse:
  ajutoare.parquet       o convocare canonică valabilă pe rând: câmpurile catalogului, `stare` (deschisa / se deschide /
                         termen in text), `termen_sigur` (data de sfârșit: din câmp sau o dată completă scrisă în text),
                         legătura spre pagina BDNS
  ajutoare_arii.parquet  convocare x arie: `arie_tip` (muni / insula / prov / ccaa / stat / doar_pagina), `arie_cod`
                         (cod INE municipiu / NUTS3 / cod INE provincie / cheia comunității / ES), `metoda`
Aria = teritoriul emitentului; `regiuni` (NUTS, regiunea de impact) doar îngustează. `doar_pagina`: comarques,
mancomunidades, „alte organisme” – fără legătură cu municipiile, doar pentru pagina de provincie, după `regiuni`.
"""
import datetime as dt, json, re, subprocess, time, unicodedata, collections
from pathlib import Path

import pandas as pd

LUNI = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
        'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}


def descarca(dir_brute, cfg):
    rez = []
    for tag, fisiere in (('bdns_catalog-ultima', ['bdns_catalog.parquet', 'manifest.json']),
                         ('teritorii-ultima', ['ine_municipii.parquet', 'lau_nuts_es.parquet', 'manifest.json'])):
        d = Path(dir_brute) / tag
        d.mkdir()
        for f in fisiere:
            subprocess.run(['gh', 'release', 'download', tag, '-p', f, '-D', str(d), '--clobber'], check=True)
        m = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
        for f in fisiere:
            rez.append({'fisier': f'{tag}/{f}', 'url': f'release {tag} (versiunea {m["versiune"]})',
                        'descarcat_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    # „valabil acum” depinde de zi: ziua intră în amprentă, deci rularea de luni publică mereu
    (Path(dir_brute) / 'azi.txt').write_text(dt.date.today().isoformat())
    rez.append({'fisier': 'azi.txt', 'url': 'ziua rulării'})
    return rez


# ── chei de nume ──
ART = r'(el|la|los|las|l|lo|o|a|os|as|es|sa|ses|els|les)'
def k(s):
    s = unicodedata.normalize('NFKD', str(s or '').lower()).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()

def k_muni(s):
    s = unicodedata.normalize('NFKD', str(s or '').lower()).encode('ascii', 'ignore').decode().strip()
    s = re.sub(r'\s*\(.*?\)\s*', ' ', s).strip()
    s = re.sub(rf'^(.*), {ART}$', r'\2 \1', s)
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()

def chei_muni(s):
    ch = set()
    for parte in [s] + str(s or '').split('/'):
        x = k_muni(parte)
        if x:
            ch.add(x)
            ch.add(re.sub(rf'^{ART} ', '', x))
    return ch


# comunitățile autonome -> codurile INE ale provinciilor; provinciile după nume (fixe)
CCAA = {c: p.split() for c, p in {
    'andalucia': '04 11 14 18 21 23 29 41', 'aragon': '22 44 50', 'asturias': '33', 'illes balears': '07',
    'canarias': '35 38', 'cantabria': '39', 'castilla y leon': '05 09 24 34 37 40 42 47 49',
    'castilla la mancha': '02 13 16 19 45', 'cataluna': '08 17 25 43', 'comunitat valenciana': '03 12 46',
    'extremadura': '06 10', 'galicia': '15 27 32 36', 'madrid': '28', 'murcia': '30', 'navarra': '31',
    'pais vasco': '01 20 48', 'la rioja': '26', 'ceuta': '51', 'melilla': '52'}.items()}
ALIAS_CCAA = {'principado de asturias': 'asturias', 'comunidad de madrid': 'madrid', 'region de murcia': 'murcia',
              'comunidad foral de navarra': 'navarra', 'comunidad valenciana': 'comunitat valenciana',
              'ciudad autonoma de ceuta': 'ceuta', 'ciudad autonoma de melilla': 'melilla', 'baleares': 'illes balears',
              'euskadi': 'pais vasco', 'castilla leon': 'castilla y leon'}
PROV = {'01': 'araba alava', '02': 'albacete', '03': 'alicante alacant', '04': 'almeria', '05': 'avila', '06': 'badajoz',
        '07': 'illes balears baleares', '08': 'barcelona', '09': 'burgos', '10': 'caceres', '11': 'cadiz',
        '12': 'castellon castello', '13': 'ciudad real', '14': 'cordoba', '15': 'a coruna la coruna coruna', '16': 'cuenca',
        '17': 'girona gerona', '18': 'granada', '19': 'guadalajara', '20': 'gipuzkoa guipuzcoa', '21': 'huelva',
        '22': 'huesca', '23': 'jaen', '24': 'leon', '25': 'lleida lerida', '26': 'la rioja', '27': 'lugo', '28': 'madrid',
        '29': 'malaga', '30': 'murcia', '31': 'navarra', '32': 'ourense orense', '33': 'asturias', '34': 'palencia',
        '35': 'las palmas', '36': 'pontevedra', '37': 'salamanca', '38': 'santa cruz de tenerife', '39': 'cantabria',
        '40': 'segovia', '41': 'sevilla', '42': 'soria', '43': 'tarragona', '44': 'teruel', '45': 'toledo',
        '46': 'valencia', '47': 'valladolid', '48': 'bizkaia vizcaya', '49': 'zamora', '50': 'zaragoza', '51': 'ceuta',
        '52': 'melilla'}
PROV_NUME = {}
for cp, n in PROV.items():
    PROV_NUME[n] = cp
    for multi in ('araba alava', 'alicante alacant', 'illes balears', 'castellon castello', 'a coruna', 'la coruna',
                  'girona', 'gerona', 'gipuzkoa', 'guipuzcoa', 'lleida', 'lerida', 'ourense', 'orense', 'bizkaia', 'vizcaya',
                  'araba', 'alava', 'alicante', 'alacant', 'castellon', 'castello', 'baleares', 'coruna'):
        if f' {multi} ' in f' {n} ':
            PROV_NUME[multi] = cp
INSULE = {'ES531': 'eivissa|formentera|ibiza', 'ES532': 'mallorca', 'ES533': 'menorca', 'ES703': 'hierro',
          'ES704': 'fuerteventura', 'ES705': 'gran canaria', 'ES706': 'gomera', 'ES707': 'la palma',
          'ES708': 'lanzarote', 'ES709': 'tenerife'}   # expresii întregi, separate prin |
# denumiri din BDNS diferite de cele INE, verificate pe nomenclator (3.11.2026)
ALIAS_MUNI = {'castell platja d aro': '17048', 'bigues i riells': '08023', 'facheca': '03067'}
ASOCIATII = (r'COMARCA|COMARCAL|MANCOMUN|MANKOMUN|CONSORCI|AGRUPACI|ASOCIACI|ÁREA METROPOLITANA|AREA METROPOLITANA|'
             r'ENTIDAD METROPOLITANA|CUADRILLA|MERINDAD|VEGUERIA|ENTIDAD LOCAL MENOR|ENTITAT MUNICIPAL DESCENTRALITZADA')


def ccaa(nume):
    x = k(nume)
    x = ALIAS_CCAA.get(x, x)
    return x if x in CCAA else None

def prov_din_nume(nume):
    x = k(nume)
    return PROV_NUME.get(x) or next((PROV_NUME[k(p)] for p in re.split(r'\s*/\s*', str(nume)) if k(p) in PROV_NUME), None)

def insula_din_nume(s):
    x = ' ' + k(s) + ' '
    gas = [c for c, n in INSULE.items() if any(f' {w} ' in x for w in n.split('|'))]
    return gas[0] if len(gas) == 1 else None


def termen_din_text(t):
    """O singură dată completă scrisă în text („hasta el 31 de diciembre de 2026”, „31/12/2026”) -> ISO; altfel None."""
    t = unicodedata.normalize('NFKD', str(t or '').lower()).encode('ascii', 'ignore').decode()
    gas = set()
    for z, l, a in re.findall(r'\b(\d{1,2}) de (' + '|'.join(LUNI) + r')(?: de(?:l)?)? (\d{4})\b', t):
        gas.add((int(a), LUNI[l], int(z)))
    for z, l, a in re.findall(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b', t):
        gas.add((int(a), int(l), int(z)))
    if len(gas) != 1:
        return None
    try:
        return dt.date(*gas.pop()).isoformat()
    except ValueError:
        return None


def structureaza(dir_brute, dir_produse, cfg):
    B = Path(dir_brute)
    azi = (B / 'azi.txt').read_text().strip()
    recent = (dt.date.fromisoformat(azi) - dt.timedelta(days=cfg['zile_termen_text'])).isoformat()
    cat = pd.read_parquet(B / 'bdns_catalog-ultima/bdns_catalog.parquet')
    c = cat[cat.cu_detaliu & cat.tipoConvocatoria.fillna('').str.contains('canónica')].drop(columns=['detaliu_json']).copy()
    c = c.astype(object).where(c.notna(), None)

    # ── ce se poate cere acum ──
    c['termen_din_text'] = pd.Series([None if f else termen_din_text(t) for f, t in zip(c.fechaFinSolicitud, c.textFin)],
                                     index=c.index, dtype=object)
    def stare(r):
        fin = r.fechaFinSolicitud or r.termen_din_text
        if fin:
            if fin < azi:
                return 'expirata'
            return 'se deschide' if r.fechaInicioSolicitud and r.fechaInicioSolicitud > azi else 'deschisa'
        if r.fechaRecepcion and r.fechaRecepcion >= recent and (r.textFin or r.textInicio):
            return 'termen in text'
        return None
    c['stare'] = c.apply(stare, axis=1)
    v = c[c.stare.isin(['deschisa', 'se deschide', 'termen in text'])].copy()
    v['termen_sigur'] = v.fechaFinSolicitud.where(v.fechaFinSolicitud.notna(), v.termen_din_text)
    v['url_bdns'] = 'https://www.infosubvenciones.es/bdnstrans/GE/es/convocatorias/' + v.numeroConvocatoria.astype(str)
    v['valabil_la'] = azi

    # ── teritoriile ──
    T = B / 'teritorii-ultima'
    im = pd.read_parquet(T / 'ine_municipii.parquet')
    im['cod_muni'] = im.CPRO + im.CMUN
    lau = pd.read_parquet(T / 'lau_nuts_es.parquet')[['LAU CODE', 'NUTS3']].rename(columns={'LAU CODE': 'cod_muni'})
    nuts3 = dict(zip(lau.cod_muni, lau.NUTS3))
    toate = sorted(im.cod_muni)
    assert set(toate) == set(nuts3), 'INE și Eurostat nu au aceleași municipii'
    nume_muni = collections.defaultdict(set)
    for cm, n in zip(im.cod_muni, im.NOMBRE):
        for ch in chei_muni(n):
            nume_muni[ch].add(cm)
    for ch, cm in ALIAS_MUNI.items():
        nume_muni[ch].add(cm)

    def muni(tip, cod):
        if tip == 'stat':
            return toate
        if tip == 'ccaa':
            return [m for m in toate if m[:2] in CCAA[cod]]
        if tip == 'prov':
            return [m for m in toate if m[:2] == cod]
        if tip == 'insula':
            return [m for m in toate if nuts3[m] == cod]
        return [cod] if tip == 'muni' else []

    def regiuni(s):
        """`regiuni` -> arii după codul NUTS (prefix): ES = Spania, ES51 = comunitatea, ES512 = provincia / insula."""
        r = []
        for parte in filter(None, (x.strip() for x in str(s or '').split(';'))):
            cod = parte.partition(' - ')[0].strip()
            if not cod.startswith('ES'):
                continue                                   # XXX / XXXX: în afara Spaniei
            ms = [m for m in toate if nuts3[m].startswith(cod)]
            if not ms:
                continue
            if cod == 'ES':
                r.append(('stat', 'ES'))
            elif cod in INSULE:
                r.append(('insula', cod))
            elif len({m[:2] for m in ms}) == 1 and len(cod) == 5:
                r.append(('prov', ms[0][:2]))
            else:                                          # NUTS1 / NUTS2: comunitatea (sau grupul de comunități)
                pv = {m[:2] for m in ms}
                cc = [c for c, p in CCAA.items() if set(p) <= pv]
                r += [('ccaa', c) for c in cc]
        return r

    def prov_arii(arii):
        return set().union(*[{m[:2] for m in muni(t, c)} for t, c in arii] or [set()])

    def leaga(r):
        reg = regiuni(r.regiuni)
        n2, n3 = r.nivel2 or '', r.nivel3 or ''
        if r.nivel1 == 'LOCAL':
            tot = (n2 + ' | ' + n3).upper()
            if 'DIPUTACI' in tot:
                nume = re.sub(r'.*DIPUTACI[OÓ]N\s+(PROV\.?|PROVINCIAL|FORAL)?\s*(DE|D\')?\s*', '', n2.upper())
                p = prov_din_nume(nume)
                return [('prov', p, 'diputacion')] if p else [('doar_pagina', None, 'diputacion fara provincie')]
            if re.search(r'CABILDO|CONSELL INSULAR|CONSEJO INSULAR', tot):
                i = insula_din_nume(n2) or insula_din_nume(n3)
                return [('insula', i, 'cabildo/consell')] if i else [('doar_pagina', None, 'insular fara insula')]
            if re.search(ASOCIATII, tot):
                return [('doar_pagina', None, 'asociere de municipii / entitate locala menor')]
            permis = prov_arii(reg) if reg and reg != [('stat', 'ES')] else set()
            permis |= {p for x in re.findall(r'\(([^)]+)\)', n2 + ' ' + n3) if (p := prov_din_nume(x))}
            cand = set().union(*[nume_muni.get(k_muni(x), set()) for x in [n2] + n2.split('/')])
            if not cand:
                cand = set().union(*[nume_muni.get(ch, set()) for ch in chei_muni(n2)] or [set()])
            if not cand:               # numele oficial în paranteză: „NOÁIN (VALLE DE ELORZ)” = Valle de Elorz/Elortzibar
                cand = set().union(*[nume_muni.get(k_muni(x), set()) for x in re.findall(r'\(([^)]+)\)', n2)] or [set()])
            if len(cand) > 1:          # nume repetat: nivelul superior (provincia / comunitatea)
                cand = {m for m in cand if m[:2] in permis}
            if len(cand) == 1:
                return [('muni', next(iter(cand)), 'municipal')]
            return [('doar_pagina', None, 'municipiu negasit' if not cand else 'municipiu ambiguu')]
        if r.nivel1 in ('AUTONOMICA', 'ESTADO'):
            baza = ('ccaa', ccaa(n2)) if r.nivel1 == 'AUTONOMICA' else ('stat', 'ES')
            if baza[1] is None:
                return [('doar_pagina', None, 'comunitate negasita')]
            pb = prov_arii([baza])
            ingust = [(t, c) for t, c in reg if prov_arii([(t, c)]) < pb]
            if ingust and len(ingust) == len(reg):
                return [(t, c, 'emitent ingustat de regiuni') for t, c in ingust]
            return [(baza[0], baza[1], 'emitent')]
        return [('doar_pagina', None, 'alte organisme')]

    rows = []
    for r in v.itertuples():
        arii = leaga(r)
        for tip, cod, metoda in arii:
            if tip == 'doar_pagina':   # pentru pagina de provincie: provinciile / insulele din `regiuni`
                for t, c in regiuni(r.regiuni) or [(None, None)]:
                    rows.append(dict(numeroConvocatoria=r.numeroConvocatoria, arie_tip='doar_pagina',
                                     arie_cod=None if t is None else f'{t}:{c}', metoda=metoda))
            else:
                rows.append(dict(numeroConvocatoria=r.numeroConvocatoria, arie_tip=tip, arie_cod=cod, metoda=metoda))
    a = pd.DataFrame(rows).drop_duplicates()
    v.to_parquet(Path(dir_produse) / 'ajutoare.parquet', index=False, compression='zstd')
    a.to_parquet(Path(dir_produse) / 'ajutoare_arii.parquet', index=False, compression='zstd')

    leg = a[a.arie_tip != 'doar_pagina'].numeroConvocatoria.unique()
    return [{'valabil_la': azi, 'convocari': int(len(v)), 'pe_stare': v.stare.value_counts().to_dict(),
             'termen_citit_din_text': int(v.termen_din_text.notna().sum()),
             'legate_de_teritoriu': int(len(leg)), 'doar_pagina': int(v.numeroConvocatoria.nunique() - len(leg)),
             'pe_arie': a.arie_tip.value_counts().to_dict(), 'pe_metoda': a.metoda.value_counts().to_dict(),
             'pe_emitent': v.nivel1.value_counts().to_dict()}]
