"""Sursa miteco_aer_statii: datele zilnice oficiale 2024 și metainformația stațiilor (MITECO).

Structurarea, generică (prima versiune, până se fixează formatul): fiecare fișier text din arhiva zilnică devine rânduri
cu coloanele lui, ca text, exact cum apar (plus fisier_sursa); fiecare foaie din metainformație la fel (plus foaia).
Fără conversii de sens: codurile de magnitud, validarea și unitățile se interpretează în atlas, după formato_datos.pdf.
Profilul notează delimitatorul, antetul și primele rânduri ale fiecărui fișier.
"""
import csv, io, time, zipfile
from pathlib import Path

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'Mozilla/5.0 (compatible; munca/1.0; +https://github.com/xmarinv/munca)'}


def descarca(dir_brute, cfg):
    rez = []
    for f in cfg['fisiere']:
        dest = Path(dir_brute) / f['fisier']
        for i in range(6):
            try:
                r = requests.get(f['url'], headers=UA, timeout=(30, 900), stream=True)
            except requests.RequestException:
                time.sleep(2 ** i * 5)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(int(r.headers.get('Retry-After', 2 ** i * 5)))
                continue
            r.raise_for_status()
            with open(dest, 'wb') as o:
                for b in r.iter_content(1 << 20):
                    o.write(b)
            break
        else:
            raise RuntimeError(f"{f['url']}: nicio reușită după 6 încercări")
        rez.append({'fisier': f['fisier'], 'url': f['url'], 'descarcat_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    return rez


def _tabel_text(randuri, antet, extra):
    n = len(randuri)
    cols = {k: pa.array([v] * n, pa.string()) for k, v in extra.items()}
    for j, c in enumerate(antet):
        cols[c] = pa.array([r[j] if j < len(r) else None for r in randuri], pa.string())
    return pa.table(cols)


def _nume_coloane(antet):
    vazute, rez = {}, []
    for j, c in enumerate(antet):
        c = (str(c).strip() if c is not None else '') or f'col_{j}'
        k = vazute.get(c, 0)
        vazute[c] = k + 1
        rez.append(c if k == 0 else f'{c}_{k}')
    return rez


def structureaza(dir_brute, dir_produse, cfg):
    dir_brute, dir_produse = Path(dir_brute), Path(dir_produse)
    profil, zilnic = [], []
    with zipfile.ZipFile(dir_brute / 'Datos_DD_2024.zip') as z:
        membri = [m for m in z.namelist() if not m.endswith('/')]
        profil.append({'arhiva': 'Datos_DD_2024.zip', 'membri': membri[:200], 'nr_membri': len(membri)})
        for m in membri:
            date = z.read(m)
            if m.lower().endswith('.zip'):
                raise RuntimeError(f'arhivă în arhivă: {m} (de tratat)')
            text = None
            for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
                try:
                    text = date.decode(enc)
                    break
                except UnicodeDecodeError:
                    pass
            linii = text.splitlines()
            try:
                dialect = csv.Sniffer().sniff('\n'.join(linii[:20]), delimiters=';,\t|')
                delim = dialect.delimiter
            except csv.Error:
                delim = ';'
            randuri = list(csv.reader(io.StringIO(text), delimiter=delim))
            randuri = [r for r in randuri if any(x.strip() for x in r)]
            if not randuri:
                continue
            antet_pare = sum(not x.replace('.', '').replace(',', '').strip().lstrip('-').isdigit() for x in randuri[0]) > len(randuri[0]) / 2
            antet = _nume_coloane(randuri[0] if antet_pare else [f'col_{j}' for j in range(max(map(len, randuri)))])
            corp = randuri[1:] if antet_pare else randuri
            latime = max(len(antet), max(map(len, corp)) if corp else 0)
            antet = _nume_coloane(antet + [f'col_{j}' for j in range(len(antet), latime)])
            profil.append({'fisier': m, 'codare': enc, 'delimitator': delim, 'antet_detectat': antet_pare,
                           'coloane': antet, 'randuri': len(corp), 'primele': linii[:4]})
            zilnic.append(_tabel_text(corp, antet, {'fisier_sursa': m}))
    if zilnic:
        pq.write_table(pa.concat_tables(zilnic, promote_options='permissive'), dir_produse / 'miteco_aer_zilnic.parquet',
                       compression='zstd')

    wb = openpyxl.load_workbook(dir_brute / 'Metainformacion_2024.xlsx', read_only=True, data_only=True)
    meta = []
    for ws in wb.worksheets:
        randuri = [['' if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)]
        randuri = [r for r in randuri if any(x.strip() for x in r)]
        if not randuri:
            continue
        antet = _nume_coloane(randuri[0])
        profil.append({'foaie': ws.title, 'coloane': antet, 'randuri': len(randuri) - 1, 'primele': randuri[:3]})
        meta.append(_tabel_text(randuri[1:], antet, {'foaie': ws.title}))
    if meta:
        pq.write_table(pa.concat_tables(meta, promote_options='permissive'), dir_produse / 'miteco_aer_metainfo.parquet',
                       compression='zstd')
    return profil
