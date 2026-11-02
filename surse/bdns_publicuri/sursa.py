"""Sursa bdns_publicuri: același cod ca bdns_detalii (detaliu + totaluri, fără beneficiari), cu lista din acest folder."""
from pathlib import Path
import surse.bdns_detalii.sursa as _b

_b.AICI = Path(__file__).parent
descarca, structureaza = _b.descarca, _b.structureaza
