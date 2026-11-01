"""Sursa osm_poi: extrasele Geofabrik (Spania, Canare) -> felia cu bibliotecile, cazările și lăcașurile de cult -> tabel.

descarca: fiecare extras întreg (~1,5 GB), md5 verificat față de cel publicat de Geofabrik, data datelor din antet;
          osmium tags-filter cu filtrele din sursa.yaml; extrasul întreg se șterge, felia (unită) e fișierul brut.
structureaza: osmium export -> un rând pe obiect OSM: nodul însuși, punctul reprezentativ al poligonului (interior)
          sau mijlocul liniei; etichetele cerute în coloane, toate etichetele în `etichete` (JSON).
Nicio regulă de business: ce e bibliotecă publică, cazare sau biserică și în ce sat cade un punct se decid în atlas.
Cere osmium-tool (apt) și shapely.
"""
import hashlib, json, subprocess, time
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests
from shapely.geometry import shape

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)'}


def salveaza(url, dest):
    for i in range(5):
        try:
            with requests.get(url, headers=UA, stream=True, timeout=(30, 1800)) as r:
                r.raise_for_status()
                with open(dest, 'wb') as f:
                    for bloc in r.iter_content(1 << 22):
                        f.write(bloc)
            return
        except requests.RequestException as e:
            if i == 4:
                raise
            print(f'{url}: {e}; reîncercare', flush=True)
            time.sleep(30 * (i + 1))


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        while b := f.read(1 << 22):
            h.update(b)
    return h.hexdigest()


def descarca(dir_brute, cfg):
    dir_brute = Path(dir_brute)
    lucru = dir_brute.parent / 'extrase'
    lucru.mkdir(exist_ok=True)
    felii, extrase = [], []
    for e in cfg['extrase']:
        p = lucru / e['nume']
        salveaza(e['url'], p)
        salveaza(e['url'] + '.md5', lucru / (e['nume'] + '.md5'))
        asteptat = (lucru / (e['nume'] + '.md5')).read_text().split()[0]
        m = md5(p)
        if m != asteptat:
            raise RuntimeError(f"{e['nume']}: md5 {m} diferă de cel publicat {asteptat}")
        pana_la = subprocess.run(['osmium', 'fileinfo', '-g', 'header.option.osmosis_replication_timestamp', str(p)],
                                 capture_output=True, text=True).stdout.strip() or None
        extrase.append({'fisier': e['nume'], 'url': e['url'], 'octeti': p.stat().st_size, 'md5': m, 'osm_pana_la': pana_la})
        f = lucru / e['nume'].replace('.osm.pbf', '-poi.osm.pbf')
        subprocess.run(['osmium', 'tags-filter', '--overwrite', '-o', str(f), str(p), *cfg['filtre']], check=True)
        felii.append(f)
        p.unlink()
        print(f"{e['nume']}: {extrase[-1]['octeti']} octeți, md5 ok, date până la {pana_la}, felie {f.stat().st_size}", flush=True)
    felie = dir_brute / 'osm_poi.osm.pbf'
    # antetul feliei unite nu păstrează data datelor: data fiecărui extras e în metadatele fișierului brut
    subprocess.run(['osmium', 'merge', '--overwrite', '-o', str(felie), *map(str, felii)], check=True)
    return [{'fisier': felie.name, 'url': ' + '.join(e['url'] for e in extrase), 'extrase': extrase,
             'filtre': cfg['filtre']}]


def structureaza(dir_brute, dir_produse, cfg):
    felie = Path(dir_brute) / 'osm_poi.osm.pbf'
    gj = Path(dir_produse).parent / 'export.geojsonseq'
    subprocess.run(['osmium', 'export', '--overwrite', '-f', 'geojsonseq', '-a', 'type,id', '-o', str(gj), str(felie)],
                   check=True)
    chei, col = cfg['chei'], cfg['coloane_eticheta']
    randuri = {k: [] for k in ['osm', 'lat', 'lon', 'geom', *col, 'etichete', 'extras']}
    vazut, pe_eticheta, pe_geom, fara_nume = set(), Counter(), Counter(), Counter()
    with open(gj, encoding='utf-8') as fi:
        for linie in fi:
            linie = linie.strip().lstrip('\x1e')
            if not linie:
                continue
            ft = json.loads(linie)
            pr = dict(ft['properties'])
            tip, oid = pr.pop('@type'), pr.pop('@id')
            if not any(k in pr for k in chei) or (tip, oid) in vazut:
                continue      # noduri-geometrie ale altor obiecte; o cale închisă poate apărea și ca linie
            vazut.add((tip, oid))
            g = shape(ft['geometry'])
            pt = g if g.geom_type == 'Point' else (
                g.representative_point() if 'Polygon' in g.geom_type else g.interpolate(0.5, normalized=True))
            randuri['osm'].append(f'{tip}/{oid}')
            randuri['lat'].append(round(pt.y, 7))
            randuri['lon'].append(round(pt.x, 7))
            randuri['geom'].append(g.geom_type)
            for c in col:
                randuri[c].append(pr.get(c))
            randuri['etichete'].append(json.dumps(pr, ensure_ascii=False, sort_keys=True))
            randuri['extras'].append(None)
            for k in chei:
                if k in pr:
                    pe_eticheta[f'{k}={pr[k]}'] += 1
                    if 'name' not in pr:
                        fara_nume[f'{k}={pr[k]}'] += 1
            pe_geom[g.geom_type] += 1
    gj.unlink()
    schema = pa.schema([('osm', pa.string()), ('lat', pa.float64()), ('lon', pa.float64()), ('geom', pa.string()),
                        *[(c, pa.string()) for c in col], ('etichete', pa.string()), ('extras', pa.string())])
    pq.write_table(pa.table(randuri, schema=schema), Path(dir_produse) / 'osm_poi.parquet', compression='zstd')
    # profilul: doar etichetele filtrate (valorile rare din building/tourism nu se listează pe rând)
    filtrate = {f"{s.split('/')[1].split('=')[0]}={v}" for s in cfg['filtre'] for v in s.split('=', 1)[1].split(',')}
    return {'obiecte': len(randuri['osm']), 'pe_geom': dict(pe_geom),
            'pe_eticheta_filtrata': {k: v for k, v in pe_eticheta.most_common() if k in filtrate},
            'fara_nume': {k: v for k, v in fara_nume.most_common() if k in filtrate},
            'religion': dict(Counter(r for r in randuri['religion'] if r).most_common(15))}
