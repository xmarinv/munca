"""Experiment (nu e sursă): se pot găsi automat convocările BDNS pentru entități locale și condițiile lor?

Rulează în Actions (workflow `test_eligibilitate`). Fără model de limbaj: doar pașii determiniști ai lanțului propus în
atlas/cercetari/raport_oportunitate_eligibilitate_primarii.md, ca să măsurăm cât se poate face prin cod:
  1. eșantion aleator din catalogul BDNS (canonice, beneficiar „persoane juridice fără activitate economică”, ultimele 365 de zile,
     emitent stat / comunitate / diputación-cabildo-consell-comarca-mancomunidad);
  2. documentele atașate în BDNS: se pot descărca? sunt PDF cu text?
  3. secțiunea „beneficiari” găsită prin cod; entitățile locale numite acolo (termeni, nu verdict);
  4. bazele: legătura din BDNS, identificatorul BOE, textul consolidat BOE disponibil?
  5. pragurile de populație („N habitantes”) găsite prin cod.
Rezultatul: ramura `test-eligibilitate` (rezultate.csv, rezumat.json, texte/ pentru cele cu entități locale).
"""
import csv, json, os, random, re, subprocess, sys, tempfile, time
from pathlib import Path

import pyarrow.parquet as pq
import requests

N = int(os.environ.get('N', '300'))
SEED = int(os.environ.get('SEED', '42'))
OUT = Path(os.environ.get('OUT', 'out'))
BAZA = 'https://www.infosubvenciones.es/bdnstrans/api'
UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca; experiment eligibilitate)'}
S = requests.Session()

LOCAL_SUPRA = re.compile(r'DIPUTACI|CABILDO|CONSELL|CONSEJO INSULAR|COMARCA|MANCOMUN', re.I)
TERMENI_EELL = {
    'ayuntamiento': r'ayuntamientos?|ajuntaments?|concellos?|udalak?',
    'entidad_local': r'entidad(?:es)? locales?|ens locals|entidades? locales? menores|EATIM|corporaci[oó]n(?:es)? locales?',
    'municipio': r'\bmunicipios?\b|municipis?',
    'diputacion': r'diputaci[oó]n(?:es)?',
    'mancomunidad_comarca': r'mancomunidad(?:es)?|comarcas?|consells? comarcals?',
}
TITLU_BENEF = re.compile(r'(?im)^[^\n]{0,60}(beneficiari|solicitantes|podr[aá]n (?:ser beneficiari|solicitar|acceder))[^\n]{0,80}$')
HAB = re.compile(r'(\d{1,3}(?:\.\d{3})+|\d{3,7})\s*(?:habitantes|hab\.)', re.I)
BOE_ID = re.compile(r'BOE-A-\d{4}-\d+')


def get(url, **kw):
    for i in range(3):
        try:
            time.sleep(0.3)
            r = S.get(url, headers=UA, timeout=(20, 120), **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f'HTTP {r.status_code}')
            return r
        except requests.RequestException as e:
            if i == 2:
                return None
            time.sleep(5 * (i + 1))


def esantion():
    d = tempfile.mkdtemp()
    subprocess.run(['gh', 'release', 'download', 'bdns_catalog-ultima', '-p', 'bdns_catalog.parquet', '-D', d], check=True)
    t = pq.read_table(Path(d) / 'bdns_catalog.parquet',
                      columns=['numeroConvocatoria', 'fechaRecepcion', 'nivel1', 'nivel2', 'nivel3', 'titlu', 'tipuri_beneficiari',
                               'urlBasesReguladoras', 'descripcionBasesReguladoras', 'detaliu_json']).to_pylist()
    limita = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 365 * 86400))
    pool = [r for r in t if r['detaliu_json'] and (r['fechaRecepcion'] or '') >= limita
            and 'PERSONAS JURÍDICAS QUE NO DESARROLLAN' in (r['tipuri_beneficiari'] or '')
            and (r['nivel1'] in ('ESTADO', 'AUTONOMICA') or (r['nivel1'] == 'LOCAL' and LOCAL_SUPRA.search(r['nivel2'] or '')))]
    random.Random(SEED).shuffle(pool)
    return pool[:N], len(pool), len(t)


def documente(d):
    """Lista documentelor din detaliul BDNS, oricum s-ar numi cheia."""
    out = []
    for k, v in d.items():
        if 'document' in k.lower() and isinstance(v, list):
            out += [x for x in v if isinstance(x, dict)]
    return out


def descarca_pdf(num, doc, tinta):
    did = doc.get('id') or doc.get('idDocumento')
    for url in (f'{BAZA}/convocatorias/documentos?idDocumento={did}',
                f'https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/{num}/document/{did}',
                f'https://www.pap.hacienda.gob.es/bdnstrans/GE/es/convocatoria/{num}/document/{did}'):
        r = get(url)
        if r is not None and r.status_code == 200 and r.content[:5] == b'%PDF-':
            tinta.write_bytes(r.content)
            return url
    return None


def text_pdf(p):
    r = subprocess.run(['pdftotext', '-layout', str(p), '-'], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


def sectiune_beneficiari(txt):
    m = TITLU_BENEF.search(txt)
    return txt[m.start(): m.start() + 2500] if m else ''


def consolidat_boe(bid):
    r = get(f'https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{bid}/metadatos',
            headers={**UA, 'Accept': 'application/json'})
    return None if r is None else r.status_code


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'texte').mkdir(exist_ok=True)
    rows, pool_n, total = esantion()
    print(f'catalog {total}; pool {pool_n}; eșantion {len(rows)}', flush=True)
    chei_detaliu, exemple_doc, rez = {}, [], []
    tmp = Path(tempfile.mkdtemp())
    for i, r in enumerate(rows):
        num, d = r['numeroConvocatoria'], json.loads(r['detaliu_json'])
        for k in d:
            chei_detaliu[k] = chei_detaliu.get(k, 0) + 1
        docs = documente(d)
        if docs and len(exemple_doc) < 5:
            exemple_doc.append({'num': num, 'documente': docs[:3]})
        texte, url_ok, pdf_n, pdf_fara_text = [], None, 0, 0
        for j, doc in enumerate(docs[:4]):
            p = tmp / f'{num}_{j}.pdf'
            u = descarca_pdf(num, doc, p)
            if u:
                pdf_n += 1
                url_ok = url_ok or u.split('?')[0].rsplit('/', 3)[0]
                t = text_pdf(p)
                if len(t.strip()) < 500:
                    pdf_fara_text += 1
                texte.append(t)
        txt = '\n\n'.join(texte)
        ben = sectiune_beneficiari(txt)
        termeni = sorted(k for k, rx in TERMENI_EELL.items() if re.search(rx, ben, re.I))
        bases = ' '.join(filter(None, [r['urlBasesReguladoras'], r['descripcionBasesReguladoras'], txt[:20000]]))
        boe = sorted(set(BOE_ID.findall(bases)))
        hab = sorted({int(x.replace('.', '')) for x in HAB.findall(txt)})
        rez.append({
            'numero': num, 'fecha': r['fechaRecepcion'], 'nivel1': r['nivel1'], 'nivel2': r['nivel2'], 'titlu': (r['titlu'] or '')[:200],
            'documente_bdns': len(docs), 'pdf_descarcate': pdf_n, 'pdf_fara_text': pdf_fara_text, 'caractere_text': len(txt),
            'sectiune_beneficiari_gasita': bool(ben), 'termeni_eell': ' '.join(termeni),
            'url_baze': r['urlBasesReguladoras'] or '', 'boe_ids': ' '.join(boe),
            'boe_consolidat_http': ' '.join(str(consolidat_boe(b)) for b in boe[:2]),
            'praguri_habitantes': ' '.join(map(str, hab[:12])), 'cale_document': url_ok or ''})
        if termeni and txt:
            (OUT / 'texte' / f'{num}.txt').write_text(f'BENEFICIARI (secțiunea găsită):\n{ben}\n\n=====\n{txt[:80000]}', encoding='utf-8')
        if i % 25 == 0:
            print(f'{i}/{len(rows)}', flush=True)
    with open(OUT / 'rezultate.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rez[0]))
        w.writeheader()
        w.writerows(rez)
    n = len(rez)
    cnt = lambda f: sum(1 for x in rez if f(x))
    rezumat = {
        'catalog': total, 'pool': pool_n, 'esantion': n, 'seed': SEED,
        'cu_documente_bdns': cnt(lambda x: x['documente_bdns']), 'cu_pdf_descarcat': cnt(lambda x: x['pdf_descarcate']),
        'cu_text_extras': cnt(lambda x: x['caractere_text'] >= 2000), 'cu_pdf_scanat': cnt(lambda x: x['pdf_fara_text']),
        'sectiune_beneficiari_gasita': cnt(lambda x: x['sectiune_beneficiari_gasita']),
        'cu_termeni_eell_in_beneficiari': cnt(lambda x: x['termeni_eell']),
        'cu_url_baze': cnt(lambda x: x['url_baze']), 'cu_boe_id': cnt(lambda x: x['boe_ids']),
        'boe_consolidat_200': cnt(lambda x: '200' in x['boe_consolidat_http']),
        'cu_praguri_habitantes': cnt(lambda x: x['praguri_habitantes']),
        'cale_document': {k: cnt(lambda x, k=k: x['cale_document'] == k) for k in {x['cale_document'] for x in rez}},
        'chei_detaliu_bdns': chei_detaliu, 'exemple_documente': exemple_doc}
    (OUT / 'rezumat.json').write_text(json.dumps(rezumat, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in rezumat.items() if k not in ('chei_detaliu_bdns', 'exemple_documente')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.exit(main())
