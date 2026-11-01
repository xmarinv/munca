"""Sursa cat_registre: seturi din portalul Socrata al Generalitat, găsite după nume în catalog, descărcate ca CSV întreg.

descarca: pentru fiecare set din sursa.yaml, căutare în catalogul Socrata (api.us.socrata.com/api/catalog/v1, domeniul din
          sursa.yaml); toate rezultatele al căror nume se potrivește cu `nume_regex` se descarcă (rows.csv) și se notează
          id-ul, numele, data actualizării și coloanele.
structureaza: un rând pe înregistrare, cu rândul ca JSON (`rand`), fără coloanele de date personale / de contact
          (`coloane_excluse`). Nicio interpretare: ce e bibliotecă publică sau cazare, și în ce sat cade, se decid în atlas.
"""
import csv, json, re, time
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)'}
CATALOG = 'https://api.us.socrata.com/api/catalog/v1'


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


def catalog(domeniu, q):
    """Catalogul Socrata: întâi cel al domeniului, apoi cel central; prima variantă care răspunde."""
    variante = [(f'https://{domeniu}/api/catalog/v1', {'q': q, 'limit': 50, 'only': 'dataset'}),
                (f'https://{domeniu}/api/catalog/v1', {'q': q, 'limit': 50}),
                (CATALOG, {'domains': domeniu, 'q': q, 'limit': 50, 'only': 'dataset'}),
                (CATALOG, {'domains': domeniu, 'q': q, 'limit': 50})]
    erori = []
    for url, p in variante:
        try:
            r = requests.get(url, params=p, headers=UA, timeout=(30, 120))
            if r.ok and 'results' in r.json():
                print(f'catalog: {r.url}', flush=True)
                return r.json()
            erori.append(f'{r.url}: HTTP {r.status_code}')
        except (requests.RequestException, ValueError) as e:
            erori.append(f'{url}: {e}')
    raise RuntimeError('catalogul nu răspunde: ' + ' | '.join(erori))


def descarca(dir_brute, cfg):
    rez = []
    for s in cfg['seturi']:
        r = catalog(cfg['domeniu'], s['cauta'])
        gasite = [x['resource'] for x in r.get('results', []) if re.search(s['nume_regex'], x['resource']['name'])]
        print(f"{s['cheie']}: {len(r.get('results', []))} rezultate, {len(gasite)} potrivite: "
              + '; '.join(f"{g['id']} {g['name']}" for g in gasite), flush=True)
        if not gasite:
            raise RuntimeError(f"{s['cheie']}: niciun set cu numele /{s['nume_regex']}/ în catalog; rezultate: "
                               + '; '.join(x['resource']['name'] for x in r.get('results', [])[:20]))
        for g in gasite:
            fis = f"{s['cheie']}_{g['id']}.csv"
            url = f"https://{cfg['domeniu']}/api/views/{g['id']}/rows.csv?accessType=DOWNLOAD"
            with cere(url, stream=True) as resp, open(Path(dir_brute) / fis, 'wb') as f:
                for bloc in resp.iter_content(1 << 20):
                    f.write(bloc)
            rez.append({'fisier': fis, 'url': url, 'set_cheie': s['cheie'], 'set_id': g['id'], 'set_nume': g['name'],
                        'set_actualizat': g.get('data_updated_at') or g.get('updatedAt'),
                        'coloane': g.get('columns_name')})
    (Path(dir_brute).parent / 'seturi.json').write_text(json.dumps(rez, ensure_ascii=False), encoding='utf-8')
    return rez


def structureaza(dir_brute, dir_produse, cfg):
    meta = json.loads((Path(dir_brute).parent / 'seturi.json').read_text(encoding='utf-8'))
    excl = [e.lower() for e in cfg['coloane_excluse']]
    cols = {k: [] for k in ['set_cheie', 'set_id', 'set_nume', 'set_actualizat', 'rand']}
    profil = {}
    for m in meta:
        with open(Path(dir_brute) / m['fisier'], encoding='utf-8-sig', newline='') as f:
            cit = csv.DictReader(f)
            antet = cit.fieldnames or []
            pastrate = [c for c in antet if not any(e in c.lower() for e in excl)]
            valori = {c: Counter() for c in pastrate}
            n = 0
            for r in cit:
                d = {c: r[c] for c in pastrate if r.get(c) not in (None, '')}
                for c, v in d.items():
                    if len(valori[c]) < 200 or v in valori[c]:
                        valori[c][v] += 1
                cols['set_cheie'].append(m['set_cheie'])
                cols['set_id'].append(m['set_id'])
                cols['set_nume'].append(m['set_nume'])
                cols['set_actualizat'].append(str(m['set_actualizat']))
                cols['rand'].append(json.dumps(d, ensure_ascii=False, sort_keys=True))
                n += 1
        profil[m['fisier']] = {
            'set_nume': m['set_nume'], 'set_actualizat': m['set_actualizat'], 'randuri': n,
            'coloane_pastrate': pastrate, 'coloane_excluse': [c for c in antet if c not in pastrate],
            # coloanele cu puține valori distincte (tipuri, categorii): toate valorile cu numărul lor
            'categorii': {c: dict(v.most_common(60)) for c, v in valori.items() if 0 < len(v) < 60},
        }
    schema = pa.schema([(k, pa.string()) for k in cols])
    pq.write_table(pa.table(cols, schema=schema), Path(dir_produse) / 'cat_registre.parquet', compression='zstd')
    return profil
