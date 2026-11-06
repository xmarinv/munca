"""Sursa placsp_baruri: licitațiile PLACSP (perfiles de contratante + platforme agregate) care par oferte de bar, centru
social sau multiservicio -> un tabel, un rând pe licitație, cu starea, termenul, legătura și documentele.

descarca: ZIP-urile lunare din sursa.yaml (de la `de_la` până la luna curentă, în întregime la fiecare rulare), citite în
          flux; rămân doar intrările care trec de filtrul GENEROS (CPV de hosteleria sau cuvânt-cheie în titlu), ca XML
          brut, într-un singur fișier (.jsonl.gz, mtime 0, ordonat după id). O licitație care apare în mai multe luni
          (cu starea schimbată) se ține o singură dată: apariția cu `updated` cel mai recent. ZIP-urile se șterg.
          Un al doilea fișier brut, `placsp_baruri_parcurs.json`, numără intrările citite și păstrate pe lună și set.
structureaza: XML-ul păstrat -> un rând pe licitație (CODICE). Câmpurile se caută după numele local al elementelor,
          nu după prefixe de spațiu de nume. Nicio regulă de business: ce e „bar permanent" sau „de sezon", ce primărie
          și ce municipiu INE e – se decid în atlas. NIF-ul și codul DIR3 ale organului se păstrează pentru legarea sigură.
"""
import datetime as dt, gzip, io, json, re, time, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca)'}
BRUT = 'placsp_baruri_brute.jsonl.gz'
PARCURS = 'placsp_baruri_parcurs.json'


def local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def copii(el, nume):
    return [c for c in el if local(c.tag) == nume]


def desc(el, nume):
    return [x for x in el.iter() if local(x.tag) == nume]


def cale(el, *nume):
    cur = [el]
    for n in nume:
        cur = [c for e in cur for c in copii(e, n)]
        if not cur:
            return None
    return cur[0]


def txt(e):
    if e is None:
        return None
    return (e.text or '').strip() or None


def tcale(el, *nume):
    return txt(cale(el, *nume)) if el is not None else None


def prim(el, nume):
    """Primul descendent cu numele dat (text), sau None."""
    if el is None:
        return None
    for x in desc(el, nume):
        t = txt(x)
        if t:
            return t
    return None


def num(x):
    try:
        return float(x) if x is not None else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# descărcarea
# ---------------------------------------------------------------------------------------------------------------------

def luni(de_la, azi):
    a, m = int(de_la[:4]), int(de_la[4:])
    out = []
    while (a, m) <= (azi.year, azi.month):
        out.append(f'{a}{m:02d}')
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def aduce(url, dest):
    """Descarcă în flux. Întoarce statusul HTTP; 200 = fișierul e la dest."""
    for i in range(5):
        try:
            with requests.get(url, headers=UA, stream=True, timeout=(30, 1800)) as r:
                if r.status_code != 200:
                    return r.status_code
                with open(dest, 'wb') as f:
                    for bloc in r.iter_content(1 << 22):
                        f.write(bloc)
            return 200
        except requests.RequestException as e:
            if i == 4:
                raise
            print(f'{url}: {e}; reîncercare', flush=True)
            time.sleep(30 * (i + 1))


def citeste_zip(cale_zip, re_titlu, cpv_pref):
    """Intrările unui ZIP: (total unice, {id: (updated, xml)} pentru cele care trec de filtru)."""
    vazute, pastrate = set(), {}
    with zipfile.ZipFile(cale_zip) as z:
        for nume in sorted(n for n in z.namelist() if n.endswith('.atom')):
            for _, el in ET.iterparse(z.open(nume), events=('end',)):
                if local(el.tag) != 'entry':
                    continue
                ident = txt(cale(el, 'id'))
                if ident is None or ident in vazute:
                    el.clear()
                    continue
                vazute.add(ident)
                titlu = txt(cale(el, 'title')) or ''
                cpv = [txt(x) or '' for x in desc(el, 'ItemClassificationCode')]
                if any(c.startswith(cpv_pref) for c in cpv) or re_titlu.search(titlu):
                    upd = txt(cale(el, 'updated')) or ''
                    pastrate[ident] = (upd, ET.tostring(el, encoding='unicode'))
                el.clear()
    return len(vazute), pastrate


def descarca(dir_brute, cfg):
    dir_brute = Path(dir_brute)
    tmp = dir_brute.parent / 'zip'
    tmp.mkdir(exist_ok=True)
    re_titlu = re.compile(cfg['filtru']['titlu'], re.I)
    cpv_pref = tuple(cfg['filtru']['cpv'])
    azi = dt.datetime.now(dt.timezone.utc).date()
    luna_curenta = f'{azi.year}{azi.month:02d}'
    toate, parcurs = {}, []
    for luna in luni(cfg['de_la'], azi):
        for set_, sablon in cfg['seturi'].items():
            url = sablon.format(luna=luna)
            z = tmp / f'{set_}_{luna}.zip'
            st = aduce(url, z)
            if st != 200:
                if st == 404 and luna == luna_curenta:     # începutul lunii: fișierul lunii nu e încă publicat
                    parcurs.append({'set': set_, 'luna': luna, 'http': st, 'intrari': 0, 'pastrate': 0})
                    continue
                raise RuntimeError(f'{url}: HTTP {st}')
            total, pastrate = citeste_zip(z, re_titlu, cpv_pref)
            z.unlink()
            for ident, (upd, xml) in pastrate.items():
                if ident not in toate or upd >= toate[ident][0]:
                    toate[ident] = (upd, set_, luna, xml)
            parcurs.append({'set': set_, 'luna': luna, 'http': 200, 'intrari': total, 'pastrate': len(pastrate)})
            print(f'{set_} {luna}: {total} intrări, {len(pastrate)} păstrate', flush=True)
    if not toate:
        raise RuntimeError('nicio intrare păstrată: filtrul sau sursa s-au stricat')
    cale_brut = dir_brute / BRUT
    with open(cale_brut, 'wb') as f, gzip.GzipFile(filename='', mode='wb', fileobj=f, mtime=0) as g:
        for ident in sorted(toate):
            upd, set_, luna, xml = toate[ident]
            g.write((json.dumps({'id': ident, 'set': set_, 'luna': luna, 'updated': upd, 'xml': xml},
                                ensure_ascii=False) + '\n').encode('utf-8'))
    (dir_brute / PARCURS).write_text(json.dumps({'de_la': cfg['de_la'], 'pe_luna': parcurs}, ensure_ascii=False,
                                                sort_keys=True, indent=1), encoding='utf-8')
    return [{'fisier': BRUT, 'url': list(cfg['seturi'].values()), 'intrari_pastrate': len(toate)},
            {'fisier': PARCURS, 'url': None}]


# ---------------------------------------------------------------------------------------------------------------------
# structurarea
# ---------------------------------------------------------------------------------------------------------------------

DOC_TIPURI = ('LegalDocumentReference', 'TechnicalDocumentReference', 'AdditionalDocumentReference', 'GeneralDocument')


def rand(rec):
    e = ET.fromstring(rec['xml'])
    cfs = next((c for c in e if local(c.tag) == 'ContractFolderStatus'), None)
    r = {'id': rec['id'], 'set': rec['set'], 'luna_fisier': rec['luna'], 'updated': rec['updated'] or txt(cale(e, 'updated')),
         'titlu': txt(cale(e, 'title')), 'rezumat': txt(cale(e, 'summary')), 'link': None}
    for l in copii(e, 'link'):
        if l.get('href'):
            r['link'] = l.get('href')
            break
    if cfs is None:
        return r
    r['expediente'] = tcale(cfs, 'ContractFolderID')
    r['estat'] = tcale(cfs, 'ContractFolderStatusCode')
    # organul de contractare
    lcp = cale(cfs, 'LocatedContractingParty')
    if lcp is not None:
        r['tip_organ'] = tcale(lcp, 'ContractingPartyTypeCode')
        party = cale(lcp, 'Party') or lcp
        r['organ'] = tcale(party, 'PartyName', 'Name') or prim(party, 'Name')
        r['oras'] = tcale(party, 'PostalAddress', 'CityName') or prim(party, 'CityName')
        r['cod_postal'] = tcale(party, 'PostalAddress', 'PostalZone') or prim(party, 'PostalZone')
        for pi in copii(party, 'PartyIdentification'):
            id_ = cale(pi, 'ID')
            schema = (id_.get('schemeName') or '').upper() if id_ is not None else ''
            v = txt(id_)
            if v and schema == 'NIF':
                r['nif'] = v
            elif v and schema == 'DIR3':
                r['dir3'] = v
            elif v and schema in ('ID_PLATAFORMA', 'ID_PLATAFORMA_CONTRATACION'):
                r['id_plataforma'] = v
    # obiectul contractului
    pp = cale(cfs, 'ProcurementProject')
    if pp is not None:
        r['tip_contract'] = tcale(pp, 'TypeCode')
        r['subtip_contract'] = tcale(pp, 'SubTypeCode')
        r['valoare_buget'] = num(tcale(pp, 'BudgetAmount', 'TotalAmount'))
        r['valoare_buget_fara_tva'] = num(tcale(pp, 'BudgetAmount', 'TaxExclusiveAmount'))
        r['valoare_estimata'] = num(tcale(pp, 'BudgetAmount', 'EstimatedOverallContractAmount'))
        r['nuts'] = tcale(pp, 'RealizedLocation', 'CountrySubentityCode')
        dm = cale(pp, 'PlannedPeriod', 'DurationMeasure')
        if dm is not None:
            r['durata'] = num(txt(dm))
            r['durata_unitate'] = dm.get('unitCode')
    cpv = []
    for x in desc(cfs, 'ItemClassificationCode'):
        t = txt(x)
        if t and t not in cpv:
            cpv.append(t)
    r['cpv'] = ';'.join(cpv) or None
    # procedura și termenul de depunere
    tp = cale(cfs, 'TenderingProcess')
    if tp is not None:
        r['procedura'] = tcale(tp, 'ProcedureCode')
        r['termen_data'] = tcale(tp, 'TenderSubmissionDeadlinePeriod', 'EndDate')
        r['termen_ora'] = tcale(tp, 'TenderSubmissionDeadlinePeriod', 'EndTime')
    # documentele (pliegos etc.) și publicațiile (BOE, DOUE, buletine, profilul)
    docuri = []
    for tip in DOC_TIPURI:
        for d in copii(cfs, tip):
            uri = prim(d, 'URI')
            if uri:
                docuri.append({'tip': tip, 'nume': prim(d, 'ID'), 'uri': uri})
    r['documente'] = json.dumps(docuri, ensure_ascii=False) if docuri else None
    pub = []
    for v in copii(cfs, 'ValidNoticeInfo'):
        tip_anunt = tcale(v, 'NoticeTypeCode')
        for a in copii(v, 'AdditionalPublicationStatus'):
            pub.append({'anunt': tip_anunt, 'media': tcale(a, 'PublicationMediaName'), 'data': prim(a, 'IssueDate')})
    r['publicatii'] = json.dumps(pub, ensure_ascii=False) if pub else None
    return r


def structureaza(dir_brute, dir_produse, cfg):
    dir_brute, dir_produse = Path(dir_brute), Path(dir_produse)
    spec = cfg['produse']['licitatii.parquet']['coloane']
    TIP = {'string': pa.string(), 'double': pa.float64()}
    schema = pa.schema([(c, TIP[t]) for c, t in spec.items()])
    randuri = []
    with gzip.open(dir_brute / BRUT, 'rt', encoding='utf-8') as f:
        for linie in f:
            randuri.append(rand(json.loads(linie)))
    randuri.sort(key=lambda r: r['id'])
    cols = {c: [r.get(c) for r in randuri] for c in spec}
    pq.write_table(pa.table(cols, schema=schema), dir_produse / 'licitatii.parquet', compression='zstd')
    n = len(randuri)

    def umplut(c):
        return round(sum(1 for r in randuri if r.get(c) not in (None, '')) / n, 4) if n else 0

    def numara(c):
        d = {}
        for r in randuri:
            k = r.get(c) or '(gol)'
            d[k] = d.get(k, 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1])[:15])

    parcurs = json.loads((dir_brute / PARCURS).read_text(encoding='utf-8'))
    return {
        'randuri': n,
        'de_la': parcurs['de_la'],
        'intrari_citite': sum(p['intrari'] for p in parcurs['pe_luna']),
        'luni_lipsa': [f"{p['set']} {p['luna']}" for p in parcurs['pe_luna'] if p['http'] != 200],
        'completare': {c: umplut(c) for c in spec},        # ponderea rândurilor cu valoare, pe coloană
        'estat': numara('estat'),
        'tip_contract': numara('tip_contract'),
        'set': numara('set'),
        'termen_cel_mai_nou': max((r['termen_data'] for r in randuri if r.get('termen_data')), default=None),
        'pe_luna': parcurs['pe_luna'],
    }
