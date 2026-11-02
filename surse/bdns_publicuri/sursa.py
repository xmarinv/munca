"""Sursa bdns_publicuri: ce se poate cere acum, pentru paginile pe public ale atlasului (Marin, 2.11.2026; săptămânal).

descarca: (1) căutarea BDNS pe ultimele `zile_inapoi` zile, lună cu lună (codul sursei bdns); (2) filtrele pe titlu din
          sursa.yaml (`filtre`, copiate din atlas cercetari/bdns/filtre_publicuri.py), fără diacritice; (3) detaliul fiecărei
          convocări potrivite (codul sursei bdns_detalii, fără acordări). Doar selecția a ce se descarcă; ce e „deschisă”
          și pentru ce sat se decide în atlas.
structureaza: detaliul (bdns_detalii.parquet) și potrivirile pe public (bdns_publicuri.parquet).
"""
import datetime as dt, json, re, unicodedata
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import surse.bdns.sursa as _cauta
import surse.bdns_detalii.sursa as _det

norm = lambda s: unicodedata.normalize('NFKD', (s or '').lower()).encode('ascii', 'ignore').decode()


def descarca(dir_brute, cfg):
    azi = dt.date.today()
    d0 = azi - dt.timedelta(days=cfg['zile_inapoi'])
    fmt, size = '%d/%m/%Y', cfg['marime_pagina']
    conv, a = {}, d0
    while a <= azi:
        b = min((a.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1), azi)
        t = _cauta.cauta(cfg, a, b, fmt, 0, 1)['totalElements']
        ferestre = [(a, b)] if t <= size else [(a + dt.timedelta(k),) * 2 for k in range((b - a).days + 1)]
        for x, y in ferestre:
            tot, cont = _cauta.fereastra(cfg, x, y, fmt, size)
            if len(cont) < tot:
                raise RuntimeError(f'{x}–{y}: primite {len(cont)} din {tot}')
            for c in cont:
                conv[c['id']] = c
        print(f'{a:%Y-%m}: {t} convocări', flush=True)
        a = b + dt.timedelta(days=1)
    rural = re.compile(cfg['rural'])
    pot = []
    for c in conv.values():
        txt = norm(c.get('descripcion')) + ' || ' + norm(c.get('descripcionLeng'))
        r = bool(rural.search(txt))
        for pub, f in cfg['filtre'].items():
            if not re.search(f['rx'], txt):
                continue
            if f.get('nivel') and c.get('nivel1') != f['nivel']:
                continue
            if f.get('rural_daca_nu_local') and c.get('nivel1') != 'LOCAL' and not r:
                continue
            pot.append({'numeroConvocatoria': str(c['numeroConvocatoria']), 'public': pub, 'rural_in_titlu': r})
    nums = sorted({p['numeroConvocatoria'] for p in pot})
    print(f'{len(conv)} convocări în fereastră, {len(nums)} potrivite filtrelor', flush=True)
    (Path(dir_brute) / 'publicuri.json').write_text(json.dumps(pot, ensure_ascii=False), encoding='utf-8')
    (Path(dir_brute).parent / 'fereastra.json').write_text(json.dumps(
        {'de_la': str(d0), 'pana_la': str(azi), 'convocari_in_fereastra': len(conv), 'potrivite': len(nums)}), encoding='utf-8')
    return _det.descarca(dir_brute, cfg, nums) + [{'fisier': 'publicuri.json'}]


def structureaza(dir_brute, dir_produse, cfg):
    prof = _det.structureaza(dir_brute, dir_produse, cfg)
    (Path(dir_produse) / 'bdns_concesiuni_totaluri.parquet').unlink(missing_ok=True)
    pot = json.loads((Path(dir_brute) / 'publicuri.json').read_text(encoding='utf-8'))
    pq.write_table(pa.table({k: pa.array([p[k] for p in pot], pa.bool_() if k == 'rural_in_titlu' else pa.string())
                             for k in ('numeroConvocatoria', 'public', 'rural_in_titlu')}),
                   Path(dir_produse) / 'bdns_publicuri.parquet', compression='zstd')
    f = json.loads((Path(dir_brute).parent / 'fereastra.json').read_text(encoding='utf-8'))
    pe_public = {}
    for p in pot:
        pe_public[p['public']] = pe_public.get(p['public'], 0) + 1
    return {**f, 'fara_detaliu': prof['fara_detaliu'], 'pe_public': pe_public}
