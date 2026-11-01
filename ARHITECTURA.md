# Arhitectura `munca`

Decisă cu Marin pe 1.11.2026. Înlocuiește motorul din 31.10.2026, care s-a pierdut (commit local neîmpins).

## Rolul fiecărui depozit

- **`munca`** (public): ce a publicat sursa, adus într-un tabel curat și verificat tehnic. Nu știe nimic despre
  indicatori, fișe sau aprobări.
- **`atlas`** (privat): ce înseamnă cifrele (stratul semantic), validarea, aprobarea și situl. Nu descarcă nimic de pe
  portalurile spaniole.

## Sursele

Fiecare sursă are o fișă de configurare în `surse/<sursa>/`:

- de unde și cum se descarcă (API, fișier direct, browser);
- unde se descarcă: `actions` (implicit) sau `local` (pe calculatorul lui Marin, pentru serverele care refuză
  centrele de date, de exemplu MITMA: 403 din Actions);
- cadența sursei (majoritatea anuală sau semestrială);
- coloanele și tipurile așteptate;
- licența și atribuirea.

## Drumul unui fișier

1. **Descărcarea.** În Actions sau, pentru sursele `local`, pe calculatorul lui Marin cu o singură comandă, care doar
   descarcă și urcă fișierul brut, neschimbat, în `munca`.
2. **Prelucrarea, mereu în Actions** (o singură implementare, indiferent cine a descărcat):
   amprenta sha256 (identică cu ultima versiune -> stop) -> Parquet, fără reguli de business -> verificări tehnice
   (nu e HTML, schema așteptată, rândurile nu scad brusc).
3. **Publicarea.** Dacă trece: release permanent `<sursa>-<versiune>` și release mobil `<sursa>-ultima`.
   Dacă nu: issue în `munca`, iar ultima versiune bună rămâne neatinsă.

Un release conține Parquet-ul, `manifest.json` (amprenta, rândurile, data de referință, data descărcării, URL-ul,
licența, rezultatul verificărilor, locul descărcării: actions / local) și, opțional, fișierele brute.

Un release la care trimite un `set.yaml` din `atlas` nu se șterge niciodată.

## Paznicul

Un workflow săptămânal verifică ieftin (dată de modificare, dimensiune, `Ultima_Modificacion` la INE) dacă o sursă s-a
schimbat. Sursă `actions` -> pornește prelucrarea. Sursă `local` -> deschide un issue: „rulează descărcarea pentru X”.

## Legătura cu `atlas` (pull)

- `munca` nu are niciun secret și niciun drept asupra lui `atlas`.
- Un workflow mic, săptămânal, din `atlas` citește manifestele `-ultima` (publice) și le compară cu amprentele din
  fiecare `set.yaml`. La o diferență deschide un issue în `atlas`; nu schimbă date.
- Actualizarea setului se face într-o sesiune, după procedura „Actualizarea unui set” (verificare, `verifica.py`,
  cifrele publicate din teste actualizate, commit). Definiție schimbată -> aprobarea lui Marin. Generarea fișelor
  rămâne pe calculatorul lui Marin.

## De decis

- Fișierele brute în release (înlocuiesc folderul „date brute”); atenție la licențe (OSM: ODbL).
- Ordinea surselor de mutat din `atlas`.

## Rezolvat (1.11.2026)

- **Legătura cu `atlas` construită:** workflow-ul `actualizare_seturi` din `atlas` (luni 06:41 UTC, după paznicul de aici);
  la o versiune nouă reface setul și deschide un PR (`atlas/strat_semantic/actualizeaza.py`).
- **Testele cu cifre fixe:** în `atlas`, testele sunt `cifra` (se actualizează la o versiune nouă, raportate „de_actualizat”)
  sau `regula` (trebuie să treacă mereu); `verifica.py --actualizare`.
