"""Motorul comun `munca`: descărcare -> amprentă -> structurare -> verificări tehnice -> release.

Rulare (în Actions sau local, din rădăcina depozitului):
    python -m comun.motor <sursa> [--forteaza] [--fara-publicare]

Fiecare sursă stă în surse/<sursa>/: `sursa.yaml` (configurarea) și `sursa.py`, care definește:
    descarca(dir_brute, cfg) -> list[dict]      fișierele brute scrise în dir_brute, cu {fisier, url, ...}
    structureaza(dir_brute, dir_produse, cfg) -> dict   profilul tehnic (ce a găsit în fișierele brute)
    verifica_noutati(cfg) -> list[str]          opțional: date noi la sursă, încă neconfigurate (paznicul)
Structurarea nu conține reguli de business: doar citire, tipuri, forma tabelului. Sensul cifrelor stă în `atlas`.

Sursele cu `descarcare: local` nu se descarcă aici: fișierele brute vin din release-ul `<sursa>-intrare`, urcate de
pe calculatorul lui Marin; restul drumului e identic.

Ce produce: release permanent `<sursa>-<versiune>` și release mobil `<sursa>-ultima` (Parquet, manifest.json,
opțional brute.zip), plus stare/<sursa>.json. La eșec: ultima versiune bună rămâne neatinsă, se scrie stare/ și
(în Actions) se deschide un issue.
"""
import argparse, datetime as dt, hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

import pyarrow.parquet as pq
import yaml

RADACINA = Path(__file__).resolve().parent.parent
IN_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'


def acum():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def gh(*args, verifica=True):
    r = subprocess.run(['gh', *args], capture_output=True, text=True)
    if verifica and r.returncode:
        raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()}")
    return r


def modul(cale):
    spec = importlib.util.spec_from_file_location(f'sursa_{cale.parent.name}', cale)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def manifest_anterior(sursa, tmp):
    r = gh('release', 'download', f'{sursa}-ultima', '-p', 'manifest.json', '-D', str(tmp), '--clobber', verifica=False)
    f = Path(tmp) / 'manifest.json'
    return json.loads(f.read_text(encoding='utf-8')) if r.returncode == 0 and f.exists() else None


def e_html(p):
    with open(p, 'rb') as f:
        cap = f.read(512).lstrip().lower()
    return cap.startswith(b'<!doctype html') or cap.startswith(b'<html')


TIPURI = {'int8', 'int16', 'int32', 'int64', 'float', 'double', 'string', 'large_string', 'bool', 'date32[day]'}


def verificari(cfg, brute, produse, anterior):
    """Verificări tehnice: fișierul e ce trebuie, tabelul are forma așteptată, nu a pierdut brusc rânduri."""
    rez = []

    def adauga(nume, ok, detaliu=''):
        rez.append({'verificare': nume, 'rezultat': 'TRECE' if ok else 'EȘUAT', 'detaliu': detaliu})

    for b in brute:
        adauga(f"brut_nu_e_html:{b['fisier']}", not b['html'], 'pagină HTML în locul fișierului' if b['html'] else '')
        adauga(f"brut_nevid:{b['fisier']}", b['octeti'] > 0)
    asteptate = cfg.get('produse', {})
    prev = {p['fisier']: p for p in (anterior or {}).get('produse', [])}
    for nume, spec in asteptate.items():
        p = next((x for x in produse if x['fisier'] == nume), None)
        if p is None:
            adauga(f'produs_exista:{nume}', False, 'lipsește')
            continue
        lipsa = {c: t for c, t in (spec.get('coloane') or {}).items() if p['coloane'].get(c) != t}
        adauga(f'schema:{nume}', not lipsa, '' if not lipsa else f'așteptat {lipsa}, primit {p["coloane"]}')
        minim = spec.get('randuri_minim', 1)
        adauga(f'randuri_minim:{nume}', p['randuri'] >= minim, f"{p['randuri']} rânduri, minim {minim}")
        if nume in prev and prev[nume]['randuri']:
            scadere = 1 - p['randuri'] / prev[nume]['randuri']
            prag = cfg.get('scadere_maxima', 0.10)
            adauga(f'scadere_randuri:{nume}', scadere <= prag,
                   f"{prev[nume]['randuri']} -> {p['randuri']} ({-scadere:+.1%}); prag −{prag:.0%}")
    return rez


def ruleaza(sursa, forteaza=False, publica=True):
    D = RADACINA / 'surse' / sursa
    cfg = yaml.safe_load((D / 'sursa.yaml').read_text(encoding='utf-8'))
    m = modul(D / 'sursa.py')
    lucru = Path(tempfile.mkdtemp(prefix=f'munca_{sursa}_'))
    dir_brute, dir_produse = lucru / 'brute', lucru / 'produse'
    dir_brute.mkdir()
    dir_produse.mkdir()
    stare = {'sursa': sursa, 'pornit_utc': acum(), 'rezultat': None}
    anterior = manifest_anterior(sursa, lucru) if publica else None

    noutati = m.verifica_noutati(cfg) if hasattr(m, 'verifica_noutati') else []
    stare['noutati'] = noutati

    # 1. descărcarea (sau preluarea fișierelor urcate de pe calculatorul lui Marin)
    if cfg.get('descarcare', 'actions') == 'local':
        gh('release', 'download', f'{sursa}-intrare', '-D', str(dir_brute), '--clobber')
        meta = json.loads((dir_brute / 'descarcare.json').read_text(encoding='utf-8'))
        (dir_brute / 'descarcare.json').unlink()
        loc = 'local'
    else:
        meta = m.descarca(dir_brute, cfg)
        loc = 'actions'

    # 2. amprenta
    brute = []
    for b in meta:
        p = dir_brute / b['fisier']
        brute.append({**b, 'octeti': p.stat().st_size, 'sha256': sha256(p), 'html': e_html(p)})
    amprenta = hashlib.sha256('\n'.join(f"{b['fisier']}:{b['sha256']}" for b in sorted(brute, key=lambda x: x['fisier'])).encode()).hexdigest()
    stare['amprenta'] = amprenta
    if anterior and anterior.get('amprenta') == amprenta and not forteaza:
        stare.update(rezultat='neschimbat', versiune=anterior['versiune'])
        return stare, lucru

    # 3. structurarea
    profil = m.structureaza(dir_brute, dir_produse, cfg)
    produse = []
    for p in sorted(dir_produse.glob('*.parquet')):
        schema = pq.read_schema(p)
        produse.append({'fisier': p.name, 'randuri': pq.ParquetFile(p).metadata.num_rows, 'octeti': p.stat().st_size,
                        'sha256': sha256(p), 'coloane': {f.name: str(f.type) for f in schema}})

    # 4. verificările
    ver = verificari(cfg, brute, produse, anterior)
    ok = all(v['rezultat'] == 'TRECE' for v in ver)
    versiune = f"{dt.date.today():%Y%m%d}-{amprenta[:8]}"
    manifest = {
        'sursa': sursa, 'versiune': versiune, 'amprenta': amprenta, 'creat_utc': acum(), 'descarcat_in': loc,
        'nume': cfg.get('nume'), 'editor': cfg.get('editor'), 'pagina': cfg.get('pagina'),
        'licenta': cfg.get('licenta'), 'atribuire': cfg.get('atribuire'),
        'fisiere_brute': [{k: v for k, v in b.items() if k != 'html'} for b in brute],
        'produse': produse, 'profil': profil, 'verificari': ver,
        'versiunea_anterioara': (anterior or {}).get('versiune'),
        'rezultat': 'TRECE' if ok else 'EȘUAT',
        'rulare': os.environ.get('GITHUB_RUN_ID'),
    }
    (dir_produse / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    stare.update(versiune=versiune, verificari=ver)
    if not ok:
        stare['rezultat'] = 'EȘUAT'
        return stare, lucru

    # 5. publicarea
    active = [dir_produse / 'manifest.json'] + [dir_produse / p['fisier'] for p in produse]
    if cfg.get('publica_brute'):
        z = dir_produse / 'brute.zip'
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_STORED) as zf:
            for b in brute:
                zf.write(dir_brute / b['fisier'], b['fisier'])
        active.append(z)
    if publica:
        note = dir_produse / 'note.md'
        note.write_text(f"{cfg.get('nume')}\n\nVersiunea {versiune}, amprenta `{amprenta}`. Licența: {cfg.get('licenta')}\n"
                        f"Atribuire: {cfg.get('atribuire')}\n\nDetaliile în manifest.json.\n", encoding='utf-8')
        gh('release', 'create', f'{sursa}-{versiune}', *map(str, active), '--title', f'{sursa} {versiune}',
           '--notes-file', str(note), '--latest=false')
        gh('release', 'delete', f'{sursa}-ultima', '--cleanup-tag', '-y', verifica=False)
        gh('release', 'create', f'{sursa}-ultima', *map(str, active), '--title', f'{sursa} – ultima versiune ({versiune})',
           '--notes-file', str(note), '--latest=false')
    stare['rezultat'] = 'publicat' if publica else 'verificat_nepublicat'
    return stare, lucru


def main():
    a = argparse.ArgumentParser()
    a.add_argument('sursa')
    a.add_argument('--forteaza', action='store_true', help='structurează și publică chiar dacă amprenta e aceeași')
    a.add_argument('--fara-publicare', action='store_true')
    args = a.parse_args()
    try:
        stare, lucru = ruleaza(args.sursa, args.forteaza, not args.fara_publicare)
    except Exception as e:   # orice eroare devine stare citibilă, nu doar un jurnal
        stare, lucru = {'sursa': args.sursa, 'rezultat': 'EROARE', 'eroare': f'{type(e).__name__}: {e}'}, None
    stare['terminat_utc'] = acum()
    (RADACINA / 'stare').mkdir(exist_ok=True)
    (RADACINA / 'stare' / f'{args.sursa}.json').write_text(json.dumps(stare, indent=2, ensure_ascii=False), encoding='utf-8')
    if lucru and (lucru / 'produse' / 'manifest.json').exists():
        shutil.copy(lucru / 'produse' / 'manifest.json', RADACINA / 'stare' / f'{args.sursa}.manifest.json')
    print(json.dumps({k: v for k, v in stare.items() if k != 'verificari'}, indent=2, ensure_ascii=False))
    if IN_ACTIONS and (stare['rezultat'] in ('EȘUAT', 'EROARE') or stare.get('noutati')):
        if stare['rezultat'] in ('EȘUAT', 'EROARE'):
            titlu = f'[{args.sursa}] eșec'
        elif all(str(n).startswith('paznic:') for n in stare['noutati']):
            titlu = f'[{args.sursa}] paznicul nu a putut verifica sursa'
        else:
            titlu = f'[{args.sursa}] date noi la sursă'
        deja = gh('issue', 'list', '--state', 'open', '--search', f'"{titlu}" in:title', '--json', 'number', verifica=False)
        if deja.returncode == 0 and json.loads(deja.stdout or '[]'):
            pass   # issue deschis deja
        else:
            corp = '```json\n' + json.dumps(stare, indent=2, ensure_ascii=False)[:60000] + '\n```'
            gh('issue', 'create', '--title', titlu, '--body', corp, verifica=False)
    sys.exit(1 if stare['rezultat'] in ('EȘUAT', 'EROARE') else 0)


if __name__ == '__main__':
    main()
