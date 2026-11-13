"""Sursa cat_centres: directorul centrelor educative din Catalunya (Socrata, set fix din sursa.yaml).

descarca: metadatele setului (api/views/<id>.json: nume, licență, data actualizării rândurilor) și toate rândurile ca CSV
          prin API-ul SODA (resource/<id>.csv, antet cu numele de câmp, ordonat după :id ca amprenta să fie stabilă).
structureaza: un rând pe centru × curs, doar coloanele permise din sursa.yaml, toate text, neschimbate (o coloană absentă
          din CSV – SODA omite coloanele complet goale – devine NULL). Nicio interpretare: ce curs se ia, ce înseamnă o
          valoare din coloanele de învățământ și de ce sat ține centrul se decid în atlas.
"""
import csv, json, time
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)'}
OBLIGATORII = ['curs', 'codi_centre', 'denominaci_completa', 'nom_naturalesa', 'codi_municipi', 'nom_localitat',
               'coordenades_geo_x', 'coordenades_geo_y', 'einf2c', 'epri', 'eso']
PROFIL_VALORI = ['nom_naturalesa', 'einf1c', 'einf2c', 'epri', 'eso', 'batx', 'cfpm', 'cfps', 'ee']


def cere(url, **kw):
    for i in range(5):
        try:
            r = requests.get(url, headers=UA, timeout=(30, 900), **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f'HTTP {r.status_code}')
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i == 4:
                raise
            print(f'{url}: {e}; reîncercare', flush=True)
            time.sleep(20 * (i + 1))


def descarca(dir_brute, cfg):
    dom, sid = cfg['domeniu'], cfg['set_id']
    v = cere(f'https://{dom}/api/views/{sid}.json').json()
    meta = {'set_id': sid, 'set_nume': v.get('name'), 'licenta_metadate': v.get('license') or v.get('licenseId'),
            'atribuire_metadate': v.get('attribution'), 'randuri_actualizate_unix': v.get('rowsUpdatedAt'),
            'coloane_api': [c.get('fieldName') for c in v.get('columns', [])]}
    url = f'https://{dom}/resource/{sid}.csv'
    fis = f'directori_{sid}.csv'
    with cere(url, params={'$limit': 1000000, '$order': ':id'}, stream=True) as resp, open(Path(dir_brute) / fis, 'wb') as f:
        for bloc in resp.iter_content(1 << 20):
            f.write(bloc)
    (Path(dir_brute).parent / 'set.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return [{'fisier': fis, 'url': url, **meta}]


def structureaza(dir_brute, dir_produse, cfg):
    meta = json.loads((Path(dir_brute).parent / 'set.json').read_text(encoding='utf-8'))
    pastrate = list(cfg['coloane'])
    cols = {c: [] for c in pastrate}
    valori = {c: Counter() for c in PROFIL_VALORI}
    cursuri, pe_curs_valori = Counter(), {}
    with open(Path(dir_brute) / f"directori_{meta['set_id']}.csv", encoding='utf-8-sig', newline='') as f:
        cit = csv.DictReader(f)
        antet = cit.fieldnames or []
        lipsa = [c for c in OBLIGATORII if c not in antet]
        if lipsa:
            raise RuntimeError(f'coloanele obligatorii {lipsa} lipsesc din antet {antet}')
        for r in cit:
            for c in pastrate:
                x = r.get(c)
                cols[c].append(x if x not in (None, '') else None)
            cursuri[r['curs']] += 1
            for c in PROFIL_VALORI:
                x = r.get(c) or ''
                if len(valori[c]) < 50 or x in valori[c]:
                    valori[c][x] += 1
            d = pe_curs_valori.setdefault(r['curs'], Counter())
            for c in ('einf1c', 'einf2c', 'epri', 'eso', 'batx'):
                if r.get(c):
                    d[c] += 1
    n = len(cols['curs'])
    chei = Counter(zip(cols['curs'], cols['codi_centre']))
    schema = pa.schema([(c, pa.string()) for c in pastrate])
    pq.write_table(pa.table(cols, schema=schema), Path(dir_produse) / 'cat_centres.parquet', compression='zstd')
    return {**meta, 'randuri': n, 'coloane_absente_din_csv': [c for c in pastrate if c not in antet],
            'coloane_excluse': [c for c in antet if c not in pastrate],
            'randuri_pe_curs': dict(sorted(cursuri.items())),
            'centre_cu_invatamant_pe_curs': {k: dict(v) for k, v in sorted(pe_curs_valori.items())},
            'chei_curs_centru_duplicate': sum(1 for v in chei.values() if v > 1),
            'fara_coordonate': sum(1 for x in cols['coordenades_geo_x'] if x is None),
            'valori': {c: dict(v.most_common(50)) for c, v in valori.items()}}
