"""Sursa bdns_catalog: catalogul convocărilor BDNS din ultimii `zile_inapoi` ani, cu detaliul fiecăreia (termene,
beneficiari-tip, sectoare, regiuni, finalitate, instrumente, buget, bazele). Toată Spania, fără filtre pe public sau pe
populație (Marin, 2.11.2026): selecția (ce e valabil acum, ce sat, ce public) se face în atlas sau în altă aplicație.
Fără acordări și fără beneficiari.

descarca: (1) căutarea BDNS pe fereastră, lună cu lună (codul sursei bdns); (2) detaliul convocărilor noi și reîmprospătarea
          celor care pot încă să se schimbe (termen în viitor sau fără termen și recente), pornind de la release-ul
          `bdns_catalog-ultima`; (3) dacă timpul rulării (`minute_max`) nu ajunge, se publică ce s-a strâns și se cere o
          rulare de continuare (fișierul continua/<sursa>, citit de workflow).
structureaza: un rând pe convocare (unic pe numeroConvocatoria), câmpurile detaliului aplatizate + JSON-ul întreg.
"""
import datetime as dt, json, os, subprocess, tempfile, time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import surse.bdns.sursa as _cauta
import surse.bdns_detalii.sursa as _det

RADACINA = Path(__file__).resolve().parents[2]


def anterior(sursa):
    d = tempfile.mkdtemp()
    r = subprocess.run(['gh', 'release', 'download', f'{sursa}-ultima', '-p', 'bdns_catalog.parquet', '-D', d, '--clobber'],
                       capture_output=True, text=True)
    p = Path(d) / 'bdns_catalog.parquet'
    if r.returncode or not p.exists():
        print(f'fără release anterior ({r.stderr.strip()[:200]})', flush=True)
        return {}
    t = pq.read_table(p, columns=['numeroConvocatoria', 'detaliu_json', 'descarcat_utc']).to_pylist()
    return {x['numeroConvocatoria']: x for x in t if x['detaliu_json']}


def poate_schimba(det_json, azi, recent):
    d = json.loads(det_json)
    fin, rec = d.get('fechaFinSolicitud'), d.get('fechaRecepcion') or ''
    return (fin and fin >= azi) or (not fin and rec >= recent)


def descarca(dir_brute, cfg):
    start = time.time()
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
                conv[str(c['numeroConvocatoria'])] = c
        a = b + dt.timedelta(days=1)
    print(f'fereastra {d0}–{azi}: {len(conv)} convocări', flush=True)
    prev = anterior(cfg['_sursa'])
    recent = (azi - dt.timedelta(days=cfg['zile_reimprospatare'])).isoformat()
    noi = [n for n in conv if n not in prev]
    reimp = [n for n in conv if n in prev and poate_schimba(prev[n]['detaliu_json'], azi.isoformat(), recent)]
    print(f'din release-ul anterior: {len(prev)}; noi {len(noi)}, de reîmprospătat {len(reimp)}', flush=True)
    det, erori, facute = {n: prev[n] for n in conv if n in prev}, 0, 0
    acum = lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    for i, n in enumerate(reimp + noi):
        if (time.time() - start) / 60 > cfg['minute_max']:
            break
        try:
            d = _det.detaliu(cfg, n)
        except Exception as e:
            d, erori = None, erori + 1
            print(f'{n}: {e}', flush=True)
        if d:
            det[n] = {'numeroConvocatoria': n, 'detaliu_json': json.dumps(d, ensure_ascii=False), 'descarcat_utc': acum()}
        facute += 1
        if i % 2000 == 0:
            print(f'{i}/{len(reimp) + len(noi)} ({(time.time() - start) / 60:.0f} min)', flush=True)
    ramase = len(reimp) + len(noi) - facute
    if ramase:
        (RADACINA / 'continua').mkdir(exist_ok=True)
        (RADACINA / 'continua' / cfg['_sursa']).write_text(str(ramase))
    with open(Path(dir_brute) / 'catalog.jsonl', 'w', encoding='utf-8') as f:
        for n in sorted(conv):
            f.write(json.dumps({'cautare': conv[n], **({k: det[n][k] for k in ('detaliu_json', 'descarcat_utc')} if n in det else {})},
                               ensure_ascii=False) + '\n')
    (Path(dir_brute).parent / 'jurnal.json').write_text(json.dumps(
        {'fereastra': [str(d0), str(azi)], 'convocari': len(conv), 'din_anterior': len(prev), 'noi': len(noi),
         'reimprospatate_tinta': len(reimp), 'cereri_facute': facute, 'erori': erori, 'ramase_pentru_continuare': ramase}),
        encoding='utf-8')
    return [{'fisier': 'catalog.jsonl'}]


LISTE = {'instrumente': 'instrumentos', 'tipuri_beneficiari': 'tiposBeneficiarios', 'sectoare': 'sectores',
         'regiuni': 'regiones', 'fonduri': 'fondos', 'obiective': 'objetivos'}


def structureaza(dir_brute, dir_produse, cfg):
    rows = []
    with open(Path(dir_brute) / 'catalog.jsonl', encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            c, dj = r['cautare'], r.get('detaliu_json')
            d = json.loads(dj) if dj else {}
            o = d.get('organo') or {}
            lst = lambda k: '; '.join(sorted({str(x.get('descripcion')) for x in d.get(k) or [] if isinstance(x, dict)})) or None
            buget = d.get('presupuestoTotal')
            rows.append({
                'numeroConvocatoria': str(c['numeroConvocatoria']), 'id': c.get('id'), 'fechaRecepcion': c.get('fechaRecepcion'),
                'nivel1': o.get('nivel1') or c.get('nivel1'), 'nivel2': o.get('nivel2') or c.get('nivel2'),
                'nivel3': o.get('nivel3') or c.get('nivel3'), 'titlu': d.get('descripcion') or c.get('descripcion'),
                'titlu_cooficial': d.get('descripcionLeng') or c.get('descripcionLeng'), 'mrr': bool(c.get('mrr')),
                'cu_detaliu': bool(dj), 'tipoConvocatoria': d.get('tipoConvocatoria'),
                **{k: lst(v) for k, v in LISTE.items()},
                'finalitate': d.get('descripcionFinalidad'), 'buget': float(buget) if buget not in (None, '') else None,
                'fechaInicioSolicitud': d.get('fechaInicioSolicitud'), 'fechaFinSolicitud': d.get('fechaFinSolicitud'),
                'textInicio': d.get('textInicio'), 'textFin': d.get('textFin'), 'abierto': d.get('abierto'),
                'sedeElectronica': d.get('sedeElectronica'), 'urlBasesReguladoras': d.get('urlBasesReguladoras'),
                'descripcionBasesReguladoras': d.get('descripcionBasesReguladoras'),
                'descarcat_utc': r.get('descarcat_utc'), 'detaliu_json': dj})
    sch = {k: pa.string() for k in rows[0]}
    sch.update(id=pa.int64(), mrr=pa.bool_(), cu_detaliu=pa.bool_(), buget=pa.float64(), abierto=pa.bool_())
    pq.write_table(pa.table({k: pa.array([r[k] for r in rows], t) for k, t in sch.items()}),
                   Path(dir_produse) / 'bdns_catalog.parquet', compression='zstd')
    j = json.loads((Path(dir_brute).parent / 'jurnal.json').read_text(encoding='utf-8'))
    return {**j, 'cu_detaliu': sum(r['cu_detaliu'] for r in rows), 'fara_detaliu': sum(not r['cu_detaliu'] for r in rows)}
