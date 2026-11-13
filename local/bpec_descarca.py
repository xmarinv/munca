"""Descarcă pe calculatorul local anexele „Bibliotecas públicas españolas en cifras” (Ministerio de Cultura) pentru bpec.

Fără pachete externe (doar Python 3). Rulare:   python bpec_descarca.py
Rezultat: folderul `bpec_brute_2024/` cu paginile HTML și `descarcare.json`, plus arhiva `bpec_brute_2024.zip`.
Se poate opri și relua oricând: paginile deja descărcate se sar. Ritm lent și politicos (pauză între cereri,
la HTTP 429 se așteaptă și se reia). Nu ocolește nimic: aceleași pagini publice pe care le-ai deschide în browser.
"""
import json, sys, time, urllib.parse, urllib.request, urllib.error, zipfile
from pathlib import Path

BAZA = 'https://www.mcu.es/alziraweb/alziraweb.cmd'
EJERCICIO = 2024
PROVINCII = [f'{i:02d}' for i in range(1, 53)]
ANEXE = [56, 3, 11, 69, 41, 35]
PAUZA = 3.0
UA = 'munca/1.0 (+https://github.com/xmarinv/munca; descarcare manuala lenta de date deschise)'
OUT = Path(__file__).resolve().parent / f'bpec_brute_{EJERCICIO}'


def cere(url):
    for incercare in range(8):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get('Retry-After', '')
                asteapta = int(ra) if ra.isdigit() else min(60 * 2 ** incercare, 900)
                print(f'  HTTP {e.code}; aștept {asteapta} s', flush=True)
                time.sleep(asteapta)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            print(f'  eroare de rețea ({e}); aștept 30 s', flush=True)
            time.sleep(30)
    raise RuntimeError(f'renunț la {url}')


def main():
    OUT.mkdir(exist_ok=True)
    meta, total = [], len(ANEXE) * len(PROVINCII)
    n = 0
    for anexa in ANEXE:
        for prov in PROVINCII:
            n += 1
            nume = f'a{anexa:02d}_p{prov}_{EJERCICIO}.html'
            url = BAZA + '?' + urllib.parse.urlencode({'command': 'GetAnexo', 'id': anexa, 'origen': 'PR', 'codigo': prov,
                                                       'ejercicio': EJERCICIO, 'porcentaje': ''})
            f = OUT / nume
            if not f.exists() or f.stat().st_size < 500:
                f.write_bytes(cere(url))
                print(f'[{n}/{total}] {nume}', flush=True)
                time.sleep(PAUZA)
            meta.append({'fisier': nume, 'url': url})
    (OUT / 'descarcare.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
    zip_ = OUT.with_suffix('.zip')
    with zipfile.ZipFile(zip_, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.iterdir()):
            z.write(p, p.name)
    print(f'Gata: {len(meta)} pagini. Trimite-mi arhiva: {zip_}')


if __name__ == '__main__':
    sys.exit(main())
