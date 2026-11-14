"""Sursa ine_vut: locuințe turistice, locuri și procent pe municipiu (INE, estadística experimental).

descarca: fiecare tabel din sursa.yaml din API-ul Tempus (`DATOS_TABLA/<id>?tip=AM`: seriile cu metadate și toate perioadele),
          salvat neschimbat ca JSON. Dacă serverul refuză, eroarea (cod HTTP) apare în stare/ine_vut.json.
structureaza: un rând pe serie × perioadă (formă lungă): codul și numele seriei, metadatele ei ca text JSON (variabilele de
          clasificare: municipiu, indicator…), data (ms), anul, id-ul perioadei, valoarea și indicatorul de secret statistic.
          Nicio interpretare: ce municipiu e (cod INE), ce indicator, ce perioadă se ia – în atlas.
"""
import json, time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)', 'Accept': 'application/json'}


def cere(url, params):
    for i in range(5):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=(30, 600))
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f'HTTP {r.status_code}')
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i == 4:
                raise RuntimeError(f'{url}: {e}')
            print(f'{url}: {e}; reîncercare', flush=True)
            time.sleep(15 * (i + 1))


def descarca(dir_brute, cfg):
    fisiere = []
    for tabla in cfg['tabele']:
        url = f"{cfg['api']}/{tabla}"
        r = cere(url, {'tip': 'AM'})
        date = r.json()
        if not isinstance(date, list) or not date:
            raise RuntimeError(f'tabela {tabla}: răspuns gol sau neașteptat: {str(date)[:200]}')
        fis = f'tabla_{tabla}.json'
        (Path(dir_brute) / fis).write_text(json.dumps(date, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        fisiere.append({'fisier': fis, 'url': r.url, 'serii': len(date)})
        time.sleep(1)
    return fisiere


def structureaza(dir_brute, dir_produse, cfg):
    cols = {k: [] for k in ('tabla', 'serie_cod', 'serie_nombre', 'meta', 'fecha_ms', 'anyo', 'id_periodo', 'valor', 'secreto')}
    pe_tabla, perioade = {}, set()
    for tabla in cfg['tabele']:
        serii = json.loads((Path(dir_brute) / f'tabla_{tabla}.json').read_text(encoding='utf-8'))
        pe_tabla[tabla] = {'serii': len(serii), 'randuri': 0, 'exemplu_serie': serii[0].get('Nombre')}
        for s in serii:
            meta = json.dumps(s.get('MetaData'), ensure_ascii=False, separators=(',', ':')) if s.get('MetaData') is not None else None
            for d in s.get('Data') or []:
                cols['tabla'].append(tabla)
                cols['serie_cod'].append(s.get('COD'))
                cols['serie_nombre'].append(s.get('Nombre'))
                cols['meta'].append(meta)
                cols['fecha_ms'].append(d.get('Fecha'))
                cols['anyo'].append(d.get('Anyo'))
                cols['id_periodo'].append(d.get('FK_Periodo'))
                cols['valor'].append(None if d.get('Valor') is None else float(d['Valor']))
                cols['secreto'].append(bool(d.get('Secreto')))
                pe_tabla[tabla]['randuri'] += 1
                perioade.add((d.get('Anyo'), d.get('FK_Periodo')))
    tip = {'tabla': pa.string(), 'serie_cod': pa.string(), 'serie_nombre': pa.string(), 'meta': pa.string(),
           'fecha_ms': pa.int64(), 'anyo': pa.int64(), 'id_periodo': pa.int64(), 'valor': pa.float64(), 'secreto': pa.bool_()}
    pq.write_table(pa.table({k: pa.array(v, type=tip[k]) for k, v in cols.items()}), Path(dir_produse) / 'ine_vut.parquet')
    return {'pe_tabla': pe_tabla, 'perioade': sorted(perioade, key=lambda x: (x[0] or 0, x[1] or 0))[-12:],
            'randuri': len(cols['valor'])}
