"""Sursa bdns: lista completă a convocărilor din BDNS (API-ul REST al SNPSAP), etapa 1.

descarca: /convocatorias/busqueda, lună cu lună de la `de_la` până în luna curentă (filtrul fechaDesde/fechaHasta);
          o lună cu mai multe convocări decât o pagină se ia zi cu zi, ca ordonarea să nu amestece paginile. Formatul
          datei și mărimea reală a paginii se află la început, din răspunsurile API-ului (se notează în descarcare.json).
          Fiecare fereastră se scrie ca JSON (lista `content` + totalElements).
structureaza: un rând pe convocare (unic pe `id`), coloanele exact cum le dă căutarea. Nicio interpretare: ce
          convocare are legătură cu satele se decide în atlas.
"""
import calendar, datetime as dt, json, time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)', 'Accept': 'application/json'}
COL = ['id', 'numeroConvocatoria', 'descripcion', 'descripcionLeng', 'fechaRecepcion', 'nivel1', 'nivel2', 'nivel3',
       'mrr', 'codigoInvente']
S = requests.Session()


def cere(url, params):
    for i in range(6):
        try:
            r = S.get(url, params=params, headers=UA, timeout=(30, 300))
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f'HTTP {r.status_code}')
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if i == 5:
                raise
            print(f'{url} {params}: {e}; reîncercare', flush=True)
            time.sleep(15 * (i + 1))


def cauta(cfg, desde, hasta, fmt, page, size):
    p = {'page': page, 'pageSize': size, 'order': 'fechaRecepcion', 'direccion': 'asc'}
    if desde:
        p['fechaDesde'], p['fechaHasta'] = desde.strftime(fmt), hasta.strftime(fmt)
    time.sleep(cfg.get('pauza_s', 0.3))
    return cere(cfg['baza'] + '/convocatorias/busqueda', p)


def sondeaza(cfg):
    """Totalul general, formatul de dată care chiar filtrează și mărimea maximă a paginii."""
    tot = cauta(cfg, None, None, None, 0, 1)['totalElements']
    luna = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    d0 = luna.replace(day=1)
    fmt = None
    for f in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            t = cauta(cfg, d0, luna, f, 0, 1)['totalElements']
        except requests.RequestException as e:
            print(f'format {f}: {e}', flush=True)
            continue
        print(f'format {f}: {t} convocări în {d0:%Y-%m} (total {tot})', flush=True)
        if 0 < t < tot:
            fmt = f
            break
    if not fmt:
        raise RuntimeError(f'niciun format de dată nu filtrează căutarea (total {tot})')
    j = cauta(cfg, d0, luna, fmt, 0, cfg['marime_pagina'])
    size = min(cfg['marime_pagina'], max(len(j['content']), 1)) if j['totalElements'] > len(j['content']) else cfg['marime_pagina']
    print(f'mărimea paginii: {size}', flush=True)
    return tot, fmt, size


def fereastra(cfg, d0, d1, fmt, size):
    j = cauta(cfg, d0, d1, fmt, 0, size)
    tot, cont = j['totalElements'], list(j['content'])
    pag = 1
    while len(cont) < tot and pag * size < tot + size:
        cont += cauta(cfg, d0, d1, fmt, pag, size)['content']
        pag += 1
    return tot, cont


def descarca(dir_brute, cfg):
    tot_general, fmt, size = sondeaza(cfg)
    an, ln = map(int, cfg['de_la'].split('-'))
    azi = dt.date.today()
    rez = []
    while (an, ln) <= (azi.year, azi.month):
        d0, d1 = dt.date(an, ln, 1), dt.date(an, ln, calendar.monthrange(an, ln)[1])
        t = cauta(cfg, d0, d1, fmt, 0, 1)['totalElements']
        if t <= size:
            ferestre = [(d0, d1)]
        else:
            ferestre = [(d0 + dt.timedelta(k), d0 + dt.timedelta(k)) for k in range((d1 - d0).days + 1)]
        for a, b in ferestre:
            tot, cont = fereastra(cfg, a, b, fmt, size)
            fis = f'conv_{a:%Y%m%d}_{b:%Y%m%d}.json'
            (Path(dir_brute) / fis).write_text(json.dumps({'totalElements': tot, 'content': cont}, ensure_ascii=False),
                                               encoding='utf-8')
            rez.append({'fisier': fis, 'desde': str(a), 'hasta': str(b), 'total': tot, 'primite': len(cont)})
        print(f'{an}-{ln:02d}: {t} convocări în {len(ferestre)} ferestre', flush=True)
        an, ln = (an + 1, 1) if ln == 12 else (an, ln + 1)
    (Path(dir_brute).parent / 'sondaj.json').write_text(json.dumps(
        {'total_general': tot_general, 'format_data': fmt, 'marime_pagina': size}), encoding='utf-8')
    return rez


def structureaza(dir_brute, dir_produse, cfg):
    sondaj = json.loads((Path(dir_brute).parent / 'sondaj.json').read_text(encoding='utf-8'))
    randuri, declarat, primit = {}, 0, 0
    for p in sorted(Path(dir_brute).glob('conv_*.json')):
        j = json.loads(p.read_text(encoding='utf-8'))
        declarat += j['totalElements']
        primit += len(j['content'])
        for c in j['content']:
            randuri[c['id']] = c
    def s(v):
        return None if v is None else str(v)
    tab = pa.table({
        'id': pa.array([int(c['id']) for c in randuri.values()], pa.int64()),
        **{k: pa.array([s(c.get(k)) for c in randuri.values()], pa.string())
           for k in COL if k not in ('id', 'mrr')},
        'mrr': pa.array([bool(c.get('mrr')) for c in randuri.values()], pa.bool_()),
    }).select(COL).sort_by('id')
    pq.write_table(tab, Path(dir_produse) / 'bdns_convocatorias.parquet', compression='zstd')
    ani = {}
    for f in tab.column('fechaRecepcion').to_pylist():
        ani[(f or '????')[:4]] = ani.get((f or '????')[:4], 0) + 1
    niv = {}
    for n in tab.column('nivel1').to_pylist():
        niv[n] = niv.get(n, 0) + 1
    return {**sondaj, 'declarat_ferestre': declarat, 'primit': primit, 'unice': tab.num_rows,
            'lipsa_fata_de_total_general': sondaj['total_general'] - tab.num_rows, 'pe_ani': dict(sorted(ani.items())),
            'pe_nivel1': niv}
