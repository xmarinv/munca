"""Sursa eea_aer: hărțile EEA interpolate de calitate a aerului (GeoTIFF, 1 km, EPSG:3035) -> un tabel de celule.

Structurarea: fiecare GeoTIFF din fiecare fișier descărcat, decupat pe zonele din sursa.yaml, devine rânduri
(poluant, an, fisier_tif, zona, x, y, valoare): x, y = centrul celulei în metri (EPSG:3035), valoare exact cum e
în fișier (float32). Celulele fără date (nodata) nu se scriu. Nicio alegere de indicator: toate GeoTIFF-urile intră,
cu numele lor; atlas alege ce înseamnă fiecare.
"""
import re, time, zipfile
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
import requests
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

BAZA = 'https://sdi.eea.europa.eu'
UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)'}


def cere(url, **kw):
    """GET cu reîncercări: backoff exponențial, Retry-After la 429/503, fără reîncercare la 4xx permanente."""
    for i in range(6):
        try:
            r = requests.get(url, headers=UA, timeout=(30, 600), allow_redirects=True, **kw)
        except requests.RequestException:
            time.sleep(2 ** i * 5)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(int(r.headers.get('Retry-After', 2 ** i * 5)))
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f'{url}: nicio reușită după 6 încercări')


def salveaza(url, dest):
    r = cere(url, stream=True)
    with open(dest, 'wb') as f:
        for bloc in r.iter_content(1 << 20):
            f.write(bloc)
    return r


def descarca(dir_brute, cfg):
    """Pentru fiecare uuid: /data/<uuid> duce la o arhivă sau la o partajare „datashare” (Nextcloud),
    descărcată întreagă prin /download."""
    rez = []
    for f in cfg['fisiere']:
        dest = Path(dir_brute) / f"{f['poluant']}_{f['an']}_{f['stare']}.zip"
        url = f"{BAZA}/data/{f['uuid']}"
        r = salveaza(url, dest)
        final = r.url
        if not zipfile.is_zipfile(dest):
            text = dest.read_text(encoding='utf-8', errors='replace')
            m = re.search(r'/datashare/s/([A-Za-z0-9]+)', final + ' ' + text)
            if not m:
                raise RuntimeError(f"{url} -> {final}: nici arhivă, nici partajare datashare; început: {text[:300]!r}")
            url = f"{BAZA}/datashare/s/{m.group(1)}/download"
            r = salveaza(url, dest)
            if not zipfile.is_zipfile(dest):
                raise RuntimeError(f'{url}: nu e arhivă zip ({dest.stat().st_size} octeți)')
        rez.append({'fisier': dest.name, 'url': url, 'uuid': f['uuid'], 'poluant': f['poluant'], 'an': f['an'],
                    'stare': f['stare'], 'titlu': f['titlu'],
                    'descarcat_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    return rez


def structureaza(dir_brute, dir_produse, cfg):
    dir_brute, dir_produse = Path(dir_brute), Path(dir_produse)
    profil, bucati = [], []
    for f in cfg['fisiere']:
        arh = dir_brute / f"{f['poluant']}_{f['an']}_{f['stare']}.zip"
        dest = dir_brute / arh.stem
        with zipfile.ZipFile(arh) as z:
            membri = z.namelist()
            z.extractall(dest)
        tifuri = sorted(p for p in dest.rglob('*') if p.suffix.lower() in ('.tif', '.tiff'))
        profil.append({'arhiva': arh.name, 'membri': membri})
        for t in tifuri:
            with rasterio.open(t) as src:
                info = {'arhiva': arh.name, 'tif': t.name, 'crs': str(src.crs), 'rezolutie': list(src.res),
                        'nodata': None if src.nodata is None else float(src.nodata), 'tip': src.dtypes[0],
                        'benzi': src.count, 'dimensiune': [src.width, src.height], 'descriere': src.descriptions,
                        'etichete': src.tags(), 'zone': {}}
                for zona, bbox in cfg['zone'].items():
                    st, jos, dr, sus = transform_bounds('EPSG:4326', src.crs, *bbox, densify_pts=21)
                    w = from_bounds(st, jos, dr, sus, src.transform).round_offsets().round_lengths()
                    w = w.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
                    a = src.read(1, window=w, masked=True)
                    valid = ~np.ma.getmaskarray(a) & np.isfinite(np.ma.filled(a.astype('float64'), np.nan))
                    rand, col = np.nonzero(valid)
                    t_w = src.window_transform(w)
                    x = (t_w.c + (col + 0.5) * t_w.a).round().astype('int32')
                    y = (t_w.f + (rand + 0.5) * t_w.e).round().astype('int32')
                    v = np.ma.getdata(a)[valid].astype('float32')
                    n = len(v)
                    info['zone'][zona] = {'celule': int(n), 'min': float(v.min()) if n else None,
                                          'max': float(v.max()) if n else None, 'medie': float(v.mean()) if n else None}
                    if n:
                        bucati.append(pa.table({
                            'poluant': pa.array([f['poluant']] * n, pa.string()),
                            'an': pa.array(np.full(n, f['an'], 'int16')),
                            'fisier_tif': pa.array([t.name] * n, pa.string()),
                            'zona': pa.array([zona] * n, pa.string()),
                            'x': pa.array(x), 'y': pa.array(y), 'valoare': pa.array(v)}))
                profil.append(info)
    if bucati:
        pq.write_table(pa.concat_tables(bucati), dir_produse / 'eea_aer_celule.parquet', compression='zstd')
    return profil


def verifica_noutati(cfg):
    """Paznicul: un an mai nou decât cel configurat, cu date validate, apărut în seria EEA."""
    an_cfg = max(f['an'] for f in cfg['fisiere'] if f['stare'] == 'validat')
    text = ''
    for url in (f"{BAZA}/catalogue/srv/api/records/{cfg['serie_uuid']}/related?type=children",
                f"{BAZA}/catalogue/srv/api/records/{cfg['serie_uuid']}/formatters/xml"):
        try:
            r = requests.get(url, headers={**UA, 'Accept': 'application/json'}, timeout=60)
            if r.ok:
                text += r.text
        except requests.RequestException:
            pass
    if 'European air quality data for' not in text:
        return ['paznic: seria EEA nu a putut fi citită']
    noi = set()
    for m in re.finditer(r'(PM2\.5|NO2|O3), European air quality data for (\d{4})[^"<]{0,80}', text):
        titlu, an = m.group(0), int(m.group(2))
        if an > an_cfg and 'interim' not in titlu.lower():
            noi.add(titlu.strip())
    return sorted(noi)
