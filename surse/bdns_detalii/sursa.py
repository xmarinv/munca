"""Sursa bdns_detalii: detaliul convocărilor din convocatorii.txt și totalurile acordărilor lor, fără beneficiari.

descarca: pentru fiecare număr de convocare, /convocatorias?numConv=N (detaliul: beneficiari-tip, regiuni, buget, termene,
          bazele; JSON-ul ca atare) și /concesiones/busqueda?numeroConvocatoria=N, toate paginile. Acordările se adună
          în memorie pe (an, tip_beneficiar, instrument): număr, suma importului, suma ajutorului echivalent. Numele și
          NIF-ul beneficiarului nu se scriu nicăieri; din NIF se păstrează doar categoria (administrație / entitate /
          persoană fizică), după prima literă.
structureaza: două tabele, fără interpretare: detaliul (un rând pe convocare) și totalurile.
"""
import json, time
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)', 'Accept': 'application/json'}
S = requests.Session()
AICI = Path(__file__).parent


def cere(url, params, pauza):
    for i in range(5):
        try:
            time.sleep(pauza)
            r = S.get(url, params=params, headers=UA, timeout=(30, 300))
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f'HTTP {r.status_code}')
            if r.status_code in (400, 404):
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if i == 4:
                raise
            time.sleep(10 * (i + 1))


def tip(beneficiar):
    """Doar categoria, din prima literă a NIF-ului (primul cuvânt); restul textului se aruncă."""
    nif = (beneficiar or '').strip().split(' ')[0].upper()
    if not nif:
        return 'necunoscut'
    c = nif[0]
    if c in 'PQS':
        return 'administratie'
    if c in 'ABCDEFGHJNRUVW':
        return 'entitate'
    return 'persoana_fizica'


def detaliu(cfg, n):
    for p in ({'numConv': n}, {'numConv': n, 'vpd': 'GE'}):
        j = cere(cfg['baza'] + '/convocatorias', p, cfg['pauza_s'])
        if j:
            return j
    return None


def totaluri(cfg, n):
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    page, vazute, total = 0, 0, None
    while True:
        j = cere(cfg['baza'] + '/concesiones/busqueda',
                 {'numeroConvocatoria': n, 'pageSize': cfg['marime_pagina'], 'page': page}, cfg['pauza_s'])
        if not j:
            break
        total = j.get('totalElements', 0)
        cont = j.get('content') or []
        for c in cont:
            a = agg[((c.get('fechaConcesion') or '????')[:4], tip(c.get('beneficiario')), c.get('instrumento') or '')]
            a[0] += 1
            a[1] += float(c.get('importe') or 0)
            a[2] += float(c.get('ayudaEquivalente') or 0)
        vazute += len(cont)
        page += 1
        if not cont or vazute >= total:
            break
        del cont, j
    return total, vazute, [[k[0], k[1], k[2], *v] for k, v in agg.items()]


def descarca(dir_brute, cfg, nums=None):
    if nums is None:
        nums = [l.strip() for l in (AICI / cfg['lista']).read_text(encoding='utf-8').splitlines()
                if l.strip() and not l.startswith('#')]
    det, tot, jurnal = [], [], {'convocari': len(nums), 'fara_detaliu': 0, 'acordari_declarate': 0, 'acordari_primite': 0}
    for i, n in enumerate(nums):
        try:
            d = detaliu(cfg, n)
            det.append({'numeroConvocatoria': n, 'ok': d is not None, 'eroare': None if d else 'fără detaliu',
                        'detaliu_json': json.dumps(d, ensure_ascii=False) if d else None})
        except requests.RequestException as e:
            det.append({'numeroConvocatoria': n, 'ok': False, 'eroare': str(e)[:300], 'detaliu_json': None})
        if not det[-1]['ok']:
            jurnal['fara_detaliu'] += 1
        if not cfg.get('totaluri', True):
            continue
        try:
            declarat, primit, rows = totaluri(cfg, n)
            jurnal['acordari_declarate'] += declarat or 0
            jurnal['acordari_primite'] += primit
            tot += [[n, *r] for r in rows]
        except requests.RequestException as e:
            det[-1]['eroare'] = (det[-1]['eroare'] or '') + f' | concesiones: {str(e)[:200]}'
        if i % 250 == 0:
            print(f'{i}/{len(nums)}: fără detaliu {jurnal["fara_detaliu"]}, acordări {jurnal["acordari_primite"]}', flush=True)
    (Path(dir_brute) / 'detalii.json').write_text(json.dumps(det, ensure_ascii=False), encoding='utf-8')
    (Path(dir_brute) / 'totaluri.json').write_text(json.dumps(tot, ensure_ascii=False), encoding='utf-8')
    (Path(dir_brute).parent / 'jurnal.json').write_text(json.dumps(jurnal), encoding='utf-8')
    if jurnal['fara_detaliu'] > cfg['erori_maxime'] * len(nums):
        raise RuntimeError(f'prea multe convocări fără detaliu: {jurnal}; prima eroare: '
                           + next((d['eroare'] or '') for d in det if not d['ok']))
    return [{'fisier': 'detalii.json'}, {'fisier': 'totaluri.json'}]


def structureaza(dir_brute, dir_produse, cfg):
    det = json.loads((Path(dir_brute) / 'detalii.json').read_text(encoding='utf-8'))
    tot = json.loads((Path(dir_brute) / 'totaluri.json').read_text(encoding='utf-8'))
    pq.write_table(pa.table({
        'numeroConvocatoria': pa.array([d['numeroConvocatoria'] for d in det], pa.string()),
        'ok': pa.array([d['ok'] for d in det], pa.bool_()),
        'eroare': pa.array([d['eroare'] for d in det], pa.string()),
        'detaliu_json': pa.array([d['detaliu_json'] for d in det], pa.string())}),
        Path(dir_produse) / 'bdns_detalii.parquet', compression='zstd')
    cols = ['numeroConvocatoria', 'an', 'tip_beneficiar', 'instrument', 'acordari', 'suma_importe', 'suma_ayuda_equivalente']
    tipuri = [pa.string()] * 4 + [pa.int64(), pa.float64(), pa.float64()]
    pq.write_table(pa.table({c: pa.array([r[i] for r in tot], t) for i, (c, t) in enumerate(zip(cols, tipuri))}),
                   Path(dir_produse) / 'bdns_concesiuni_totaluri.parquet', compression='zstd')
    j = json.loads((Path(dir_brute).parent / 'jurnal.json').read_text(encoding='utf-8'))
    pe_tip = defaultdict(lambda: [0, 0.0])
    for r in tot:
        pe_tip[r[2]][0] += r[4]
        pe_tip[r[2]][1] += r[5]
    return {**j, 'convocari_cu_acordari': len({r[0] for r in tot}),
            'pe_tip_beneficiar': {k: {'acordari': v[0], 'suma': round(v[1], 2)} for k, v in pe_tip.items()}}
