"""Sursa bpec: Bibliotecas públicas españolas en cifras, anexele pe municipii (Ministerio de Cultura, Alzira).

descarca: pentru fiecare anexă și provincie, pagina `?command=GetAnexo&id=<anexa>&origen=PR&codigo=<prov>&ejercicio=<an>`
          (HTML, ISO-8859-15), salvată neschimbată; pauza dintre cereri din sursa.yaml.
structureaza: tabelul fiecărei pagini în formă lungă – un rând pe (provincie, anexă, municipiu, coloană), valoarea ca text,
          exact cum o afișează portalul (separator de mii „.”, procente cu „%”). Antetele pe două rânduri se unesc cu „ / ”.
          Rândul TOTAL al provinciei se păstrează (este_total). Municipiile fără bibliotecă nu apar în sursă.
          Nicio interpretare: ce înseamnă o coloană și legarea numelui de municipiu de codul INE se fac în atlas.
"""
import json, re, time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

UA = {'User-Agent': 'munca/1.0 (+https://github.com/xmarinv/munca; open data, slow polite download)'}


def cere(url, params):
    """Portalul limitează ritmul (HTTP 429): se respectă Retry-After, altfel pauze tot mai lungi (până la 15 minute)."""
    pauze = [60, 120, 300, 600, 900, 900]
    for i in range(len(pauze) + 1):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=(30, 120))
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get('Retry-After', '')
                raise requests.RequestException(f'HTTP {r.status_code} (Retry-After: {ra or "-"})', response=r)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i == len(pauze):
                raise RuntimeError(f"{e} la anexa {params.get('id')} provincia {params.get('codigo')}, după {i} reîncercări")
            ra = getattr(getattr(e, 'response', None), 'headers', {}).get('Retry-After', '') if getattr(e, 'response', None) is not None else ''
            asteapta = min(int(ra), 1800) if str(ra).isdigit() else pauze[i]
            print(f"{params.get('id')}/{params.get('codigo')}: {e}; aștept {asteapta} s", flush=True)
            time.sleep(asteapta)


def descarca(dir_brute, cfg):
    """Portalul refuză adresele din Actions (HTTP 429), așa că paginile se descarcă pe calculatorul lui Marin cu
    local/bpec_descarca.py și se păstrează în surse/bpec/intrare/; aici doar se preiau, neschimbate."""
    import shutil
    intrare = Path(__file__).resolve().parent / 'intrare'
    meta = json.loads((intrare / 'descarcare.json').read_text(encoding='utf-8'))
    for m in meta:
        shutil.copy2(intrare / m['fisier'], Path(dir_brute) / m['fisier'])
    return meta


class Tabel(HTMLParser):
    """Primul <table> al paginii: antetul ca grilă (rowspan / colspan), rândurile corpului, titlul din <h1 class=titulo_anexo>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titlu, self.antet, self.randuri = '', [], []
        self._in_h1 = self._in_tabel = self._gata = False
        self._zona, self._rand, self._celula, self._span = None, None, None, (1, 1)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'h1' and 'titulo_anexo' in (a.get('class') or ''):
            self._in_h1 = True
        elif tag == 'table' and not self._gata:
            self._in_tabel = True
        elif self._in_tabel and tag in ('thead', 'tbody'):
            self._zona = tag
        elif self._in_tabel and tag == 'tr':
            self._rand = []
        elif self._in_tabel and tag in ('th', 'td') and self._rand is not None:
            self._celula, self._span = [], (int(a.get('rowspan') or 1), int(a.get('colspan') or 1))

    def handle_endtag(self, tag):
        if tag == 'h1':
            self._in_h1 = False
        elif tag == 'table' and self._in_tabel:
            self._in_tabel, self._gata = False, True
        elif self._in_tabel and tag in ('th', 'td') and self._celula is not None:
            self._rand.append((re.sub(r'\s+', ' ', ''.join(self._celula)).strip(), *self._span))
            self._celula = None
        elif self._in_tabel and tag == 'tr' and self._rand is not None:
            (self.antet if self._zona == 'thead' else self.randuri).append(self._rand)
            self._rand = None

    def handle_data(self, data):
        if self._in_h1:
            self.titlu += data
        elif self._celula is not None:
            self._celula.append(data)


def coloane(antet):
    """Numele coloanelor din antetul pe unul sau mai multe rânduri (celulele cu colspan se repetă, nivelurile se unesc cu „ / ”)."""
    grila = {}
    for i, rand in enumerate(antet):
        j = 0
        for text, rs, cs in rand:
            while (i, j) in grila:
                j += 1
            for di in range(rs):
                for dj in range(cs):
                    grila[(i + di, j + dj)] = text
            j += cs
    n = 1 + max(j for _, j in grila) if grila else 0
    nume = []
    for j in range(n):
        parti = []
        for i in range(len(antet)):
            t = grila.get((i, j), '')
            if t and (not parti or parti[-1] != t):
                parti.append(t)
        nume.append(' / '.join(parti))
    return nume


def citeste(html):
    t = Tabel()
    t.feed(html)
    return re.sub(r'\s+', ' ', t.titlu).strip(), coloane(t.antet), [[c[0] for c in r] for r in t.randuri]


def structureaza(dir_brute, dir_produse, cfg):
    col = {k: [] for k in ('ejercicio', 'cod_prov', 'anexa', 'anexa_titlu', 'municipio', 'este_total', 'coloana', 'valoare')}
    municipii, antete, goale, nepotrivite, titluri = Counter(), {}, [], [], {}
    for f in sorted(Path(dir_brute).glob('a*_p*_*.html')):
        m = re.match(r'a(\d+)_p(\d+)_(\d+)\.html$', f.name)
        anexa, prov, an = int(m.group(1)), m.group(2), int(m.group(3))
        titlu, nume, randuri = citeste(f.read_bytes().decode('iso-8859-15'))
        titluri[f'{anexa}/{prov}'] = titlu
        if not randuri:
            goale.append(f.name)
            continue
        antete.setdefault(anexa, nume)
        if nume != antete[anexa]:
            nepotrivite.append(f.name)
        for r in randuri:
            if len(r) != len(nume):
                raise RuntimeError(f'{f.name}: rând cu {len(r)} celule, antet cu {len(nume)}: {r}')
            total = r[0].strip().upper() == 'TOTAL'
            if not total and anexa == cfg['anexe'][0]:
                municipii[prov] += 1
            for c, v in zip(nume[1:], r[1:]):
                for k, x in zip(col, (an, prov, anexa, titlu.split('.')[0], r[0], total, c, v)):
                    col[k].append(x)
    schema = pa.schema([('ejercicio', pa.int64()), ('cod_prov', pa.string()), ('anexa', pa.int64()), ('anexa_titlu', pa.string()),
                        ('municipio', pa.string()), ('este_total', pa.bool_()), ('coloana', pa.string()), ('valoare', pa.string())])
    pq.write_table(pa.table(col, schema=schema), Path(dir_produse) / 'bpec_municipii.parquet', compression='zstd')
    return {'ejercicio': cfg['ejercicio'], 'randuri': len(col['anexa']), 'antete': {str(k): v for k, v in antete.items()},
            'municipii_cu_biblioteca_pe_provincie': dict(sorted(municipii.items())), 'municipii_cu_biblioteca': sum(municipii.values()),
            'pagini_fara_tabel': goale, 'pagini_cu_alt_antet': nepotrivite,
            'titluri_prima_anexa': {k: v for k, v in titluri.items() if k.startswith(f"{cfg['anexe'][0]}/")}}
