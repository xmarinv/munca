"""Sursa miteco_recarga: punctele de reîncărcare publice pentru vehicule electrice (MITECO, DATEX II v3 prin NAP DGT).

descarca: fișierul XML din sursa.yaml (`url`), neschimbat.
structureaza: două tabele, fără interpretare:
  - recarga_amplasamente.parquet: un rând pe amplasament (energyInfrastructureSite): identificatori, nume, coordonatele
    afișate (coordinatesForDisplay), adresa (liniile adresei unite cu „, ”), codul poștal, operatorul, tipul amplasamentului,
    accesul, programul, data actualizării și numărătorile (puncte, conectori, puterea maximă a unui conector);
  - recarga_conectori.parquet: un rând pe conector (refillPoint > connector).
Datele personale: dacă identificatorul operatorului are forma unui NIF de persoană fizică (DNI 8 cifre + literă sau NIE
X/Y/Z + 7 cifre + literă), identificatorul și numele operatorului nu se păstrează; rămâne doar indicatorul
`operator_persoana_fizica` (aceeași regulă ca la registrele de cazare: nu stocăm numele titularilor persoane fizice).
Unitatea puterii: fluxul dă maxPowerAtSocket în wați (mediana primului rulaj, 23.11.2026: 22.000); tabelele păstrează kW.
"""
import re, time, xml.etree.ElementTree as ET
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)'}
NIF_PERSOANA = re.compile(r'^(\d{8}[A-Z]|[XYZ]\d{7}[A-Z])$')
# forme juridice și administrații: un nume fără ele poate fi al unei persoane fizice (doar numărat în profil, nu listat)
SOCIETATE = re.compile(r'\b(S\.?\s?L\.?|S\.?\s?A\.?|S\.?L\.?U\.?|S\.?A\.?U\.?|S\.?\s?COOP|COOP|C\.?B\.?|S\.?C\.?|SLL|SLNE|AIE|LTD|GMBH|INC|BV|SAS|SRL|SPA)(\b|$)|\b(AYUNTAMIENTO|AJUNTAMENT|CONCELLO|DIPUTACI|CONSELL|CABILDO|GOBIERNO|JUNTA|GENERALITAT|MUNICIPAL|UNIVERSI|FUNDACI|ASOCIACI|COMUNIDAD|MANCOMUNIDAD|AUTORIDAD|EMPRESA|ENERG|ELECTR|RECARG|CHARG|MOBILITY|MOVILIDAD|GRUPO|HOTEL|TESLA|IBERDROLA|ENDESA|REPSOL)', re.I)


def descarca(dir_brute, cfg):
    for i in range(4):
        try:
            r = requests.get(cfg['url'], headers=UA, timeout=(30, 900))
            r.raise_for_status()
            break
        except requests.RequestException as e:
            if i == 3:
                raise RuntimeError(f"{cfg['url']}: {e}")
            print(f'{e}; reîncercare', flush=True)
            time.sleep(20 * (i + 1))
    (Path(dir_brute) / 'electrolineras.xml').write_bytes(r.content)
    return [{'fisier': 'electrolineras.xml', 'url': cfg['url'], 'octeti': len(r.content)}]


def nume_local(tag):
    return tag.rsplit('}', 1)[-1]


def copii(el, nume):
    return [c for c in el if nume_local(c.tag) == nume]


def primul(el, nume):
    """Primul descendent cu numele local dat (sau None)."""
    for d in el.iter():
        if d is not el and nume_local(d.tag) == nume:
            return d
    return None


def text_valoare(el):
    """Textul primului <value> din el (structura DATEX II name/values/value), altfel textul elementului."""
    if el is None:
        return None
    v = primul(el, 'value')
    t = (v.text if v is not None else el.text) or ''
    return t.strip() or None


def num(el):
    try:
        return float(el.text) if el is not None and el.text and el.text.strip() else None
    except ValueError:
        return None


def structureaza(dir_brute, dir_produse, cfg):
    A = {k: [] for k in ('site_id', 'statie_id', 'nume', 'lat', 'lon', 'adresa', 'cod_postal', 'operator_id',
                         'operator_nume', 'operator_persoana_fizica', 'tip_amplasament', 'acces', 'program', 'actualizat',
                         'nr_puncte', 'nr_conectori', 'putere_max_kw')}
    C = {k: [] for k in ('site_id', 'punct_id', 'punct_tip', 'conector_tip', 'conector_format', 'mod_incarcare',
                         'putere_max_kw', 'tensiune_v', 'curent_a')}
    publicat, persoane_fizice, fara_coord = None, 0, 0
    for ev, el in ET.iterparse(str(Path(dir_brute) / 'electrolineras.xml'), events=('end',)):
        n = nume_local(el.tag)
        if n == 'publicationTime' and publicat is None:
            publicat = (el.text or '').strip()
        if n != 'energyInfrastructureSite':
            continue
        sid = el.get('id')
        statie = primul(el, 'energyInfrastructureStation')
        lat, lon = num(primul(el, 'latitude')), num(primul(el, 'longitude'))
        fara_coord += lat is None or lon is None
        adr = primul(el, 'address')
        linii = []
        if adr is not None:
            for al in sorted((d for d in adr.iter() if nume_local(d.tag) == 'addressLine'), key=lambda d: int(d.get('order') or 0)):
                t = text_valoare(al)
                if t:
                    linii.append(t)
        op = primul(el, 'operator')
        op_id = op.get('id') if op is not None else None
        op_nume = text_valoare(primul(op, 'name')) if op is not None else None
        pf = bool(op_id and NIF_PERSOANA.match(op_id.strip().upper()))
        if pf:
            persoane_fizice += 1
            op_id, op_nume = None, None
        oh = primul(el, 'operatingHours')
        acc = primul(el, 'accessibility')
        puncte = [d for d in el.iter() if nume_local(d.tag) == 'refillPoint']
        nc, pmax = 0, None
        for p in puncte:
            for c in (d for d in p.iter() if nume_local(d.tag) == 'connector'):
                pw = num(primul(c, 'maxPowerAtSocket'))
                pw = pw / 1000 if pw is not None else None
                C['site_id'].append(sid)
                C['punct_id'].append(p.get('id'))
                C['punct_tip'].append(p.get('{http://www.w3.org/2001/XMLSchema-instance}type') or p.get('type'))
                C['conector_tip'].append(text_valoare(primul(c, 'connectorType')))
                C['conector_format'].append(text_valoare(primul(c, 'connectorFormat')))
                C['mod_incarcare'].append(text_valoare(primul(c, 'chargingMode')))
                C['putere_max_kw'].append(pw)
                C['tensiune_v'].append(num(primul(c, 'voltage')))
                C['curent_a'].append(num(primul(c, 'maximumCurrent')))
                nc += 1
                if pw is not None and (pmax is None or pw > pmax):
                    pmax = pw
        A['site_id'].append(sid)
        A['statie_id'].append(statie.get('id') if statie is not None else None)
        A['nume'].append(text_valoare(next(iter(copii(el, 'name')), None)))
        A['lat'].append(lat)
        A['lon'].append(lon)
        A['adresa'].append(', '.join(linii) or None)
        A['cod_postal'].append((primul(el, 'postcode').text or '').strip() or None if primul(el, 'postcode') is not None else None)
        A['operator_id'].append(op_id)
        A['operator_nume'].append(op_nume)
        A['operator_persoana_fizica'].append(pf)
        A['tip_amplasament'].append(text_valoare(primul(el, 'typeOfSite')))
        A['acces'].append(None if acc is None or acc.get('{http://www.w3.org/2001/XMLSchema-instance}nil') == 'true' else text_valoare(acc))
        A['program'].append(oh.get('{http://www.w3.org/2001/XMLSchema-instance}type') if oh is not None else None)
        A['actualizat'].append((primul(el, 'lastUpdated').text or '').strip() or None if primul(el, 'lastUpdated') is not None else None)
        A['nr_puncte'].append(len(puncte))
        A['nr_conectori'].append(nc)
        A['putere_max_kw'].append(pmax)
        el.clear()
    s = pa.string()
    tipA = {'lat': pa.float64(), 'lon': pa.float64(), 'operator_persoana_fizica': pa.bool_(), 'nr_puncte': pa.int64(),
            'nr_conectori': pa.int64(), 'putere_max_kw': pa.float64()}
    tipC = {'putere_max_kw': pa.float64(), 'tensiune_v': pa.float64(), 'curent_a': pa.float64()}
    ta = pa.table({k: pa.array(v, type=tipA.get(k, s)) for k, v in A.items()})
    tc = pa.table({k: pa.array(v, type=tipC.get(k, s)) for k, v in C.items()})
    pq.write_table(ta, Path(dir_produse) / 'recarga_amplasamente.parquet')
    pq.write_table(tc, Path(dir_produse) / 'recarga_conectori.parquet')

    # profilul tehnic (în stare/): numărători și distribuții, fără nume de persoane
    from collections import Counter
    def top(lst, k=15):
        return dict(Counter(x for x in lst if x is not None).most_common(k))
    pw = sorted(x for x in C['putere_max_kw'] if x is not None)
    q = lambda f: pw[int(f * (len(pw) - 1))] if pw else None
    lats = [x for x in A['lat'] if x is not None]
    lons = [x for x in A['lon'] if x is not None]
    return {'publicat': publicat, 'amplasamente': len(A['site_id']), 'conectori': len(C['site_id']),
            'fara_coordonate': fara_coord, 'operatori_persoane_fizice_ascunsi': persoane_fizice,
            'operatori_distincti': len(set(x for x in A['operator_id'] if x)),
            'operatori_fara_forma_juridica': len(set(x for x in A['operator_nume'] if x and not SOCIETATE.search(x))),
            'amplasamente_operator_fara_forma_juridica': sum(1 for x in A['operator_nume'] if x and not SOCIETATE.search(x)),
            'operatori_top': top(A['operator_nume'], 20),
            'tip_amplasament': top(A['tip_amplasament']), 'acces': top(A['acces']), 'program': top(A['program']),
            'punct_tip': top(C['punct_tip']), 'conector_tip': top(C['conector_tip']), 'conector_format': top(C['conector_format']),
            'mod_incarcare': top(C['mod_incarcare']),
            'putere_conector_cuantile': {'min': q(0), 'p10': q(.1), 'mediana': q(.5), 'p90': q(.9), 'max': q(1)},
            'lat_interval': [min(lats), max(lats)] if lats else None, 'lon_interval': [min(lons), max(lons)] if lons else None,
            'cod_postal_lipsa': sum(1 for x in A['cod_postal'] if not x), 'site_id_duplicate': len(A['site_id']) - len(set(A['site_id'])),
            'actualizat_interval': [min(x for x in A['actualizat'] if x), max(x for x in A['actualizat'] if x)] if any(A['actualizat']) else None}
