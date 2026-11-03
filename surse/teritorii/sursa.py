"""Sursa teritorii: lista municipiilor INE și corespondența LAU – NUTS 3 (Eurostat) pentru Spania.

Structurarea, generică: fiecare foaie aleasă devine un tabel text, cu antetul detectat (primul rând cu cel puțin trei
celule completate; în diccionario INE primul rând e titlul). Fără conversii de sens.
"""
import time
from pathlib import Path

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'Mozilla/5.0 (compatible; munca/1.0; +https://github.com/xmarinv/munca)'}
PRODUS = {'diccionario26.xlsx': 'ine_municipii.parquet', 'lau_nuts_2024.xlsx': 'lau_nuts_es.parquet'}


def descarca(dir_brute, cfg):
    rez = []
    for f in cfg['fisiere']:
        dest = Path(dir_brute) / f['fisier']
        for i in range(6):
            try:
                r = requests.get(f['url'], headers=UA, timeout=(30, 600))
            except requests.RequestException:
                time.sleep(2 ** i * 5)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** i * 5)
                continue
            r.raise_for_status()
            dest.write_bytes(r.content)
            break
        else:
            raise RuntimeError(f"{f['url']}: nicio reușită după 6 încercări")
        rez.append({'fisier': f['fisier'], 'url': f['url'], 'descarcat_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    return rez


def _nume(antet):
    vazute, rez = {}, []
    for j, c in enumerate(antet):
        c = (str(c).strip() if c is not None else '') or f'col_{j}'
        k = vazute.get(c, 0)
        vazute[c] = k + 1
        rez.append(c if k == 0 else f'{c}_{k}')
    return rez


def structureaza(dir_brute, dir_produse, cfg):
    profil = []
    for f in cfg['fisiere']:
        wb = openpyxl.load_workbook(Path(dir_brute) / f['fisier'], read_only=True, data_only=True)
        foi = [ws for ws in wb.worksheets if not f.get('foi') or ws.title in f['foi']]
        profil.append({'fisier': f['fisier'], 'foi_toate': wb.sheetnames, 'foi_alese': [w.title for w in foi]})
        tabele = []
        for ws in foi:
            randuri = [['' if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
            randuri = [r for r in randuri if any(r)]
            i0 = next(i for i, r in enumerate(randuri) if sum(bool(x) for x in r) >= 3)
            antet = _nume(randuri[i0])
            corp = [r + [''] * (len(antet) - len(r)) for r in randuri[i0 + 1:]]
            cols = {'foaie': pa.array([ws.title] * len(corp), pa.string())}
            for j, c in enumerate(antet):
                cols[c] = pa.array([r[j] or None for r in corp], pa.string())
            tabele.append(pa.table(cols))
            profil.append({'foaie': ws.title, 'titlu_ignorat': randuri[:i0], 'coloane': antet, 'randuri': len(corp),
                           'primele': corp[:3]})
        pq.write_table(pa.concat_tables(tabele, promote_options='permissive'), Path(dir_produse) / PRODUS[f['fisier']],
                       compression='zstd')
    return profil
