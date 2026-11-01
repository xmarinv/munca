# Pentru Claude: depozitul `munca`

`munca` (public) descarcă și structurează datele oficiale; `atlas` (privat) le dă sens. Arhitectura: `ARHITECTURA.md`.
Starea proiectului și deciziile: `STARE.md` din `atlas`.

## Reguli

- **Nimic nu rămâne doar local.** Se împinge după fiecare pas încheiat. Dacă push-ul nu merge, codul se salvează ca
  arhivă în proiectul claude.ai și se notează în `STARE.md` din `atlas`.
- **Structurarea nu are reguli de business:** citire, tipuri, forma tabelului. Ce înseamnă o cifră, ce an se ia, ce
  celulă aparține unui sat – în `atlas`.
- **O sursă = un folder** `surse/<sursa>/` cu `sursa.yaml` (de unde, unde se descarcă: `actions` / `local`, licența,
  produsele și schema lor) și `sursa.py` (`descarca`, `structureaza`, opțional `verifica_noutati`). Motorul comun
  (`comun/motor.py`) face restul: amprenta, comparația cu versiunea anterioară, verificările, manifestul, release-ul.
- **Licența se verifică înainte de publicare**, pentru că depozitul e public și release-urile pot conține fișierele brute.
- **Un release la care trimite un `set.yaml` din `atlas` nu se șterge.**
- **Împărțirea muncii (Marin, 1.11.2026; regula obișnuită până se termină prelucrarea și urcarea seturilor):** Claude e
  creierul, Actions e executantul. Claude decide, scrie codul și configurația, citește rezumatele și verifică rezultatele;
  tot ce e descărcare, prelucrare, calcul, generare și urcare rulează în Actions (`munca` pentru surse, `atlas` pentru
  verificări și fișe), folosit intensiv, nu economisit. Sesiunea nu repetă local ce poate face Actions și nu așteaptă
  calcule lungi din sesiune. Excepții: ce serverele refuză din Actions (MITMA – descărcarea se face pe calculatorul lui
  Marin, prelucrarea tot în Actions) și deciziile care îi aparțin lui Marin (aprobarea indicatorilor, textele noi).
- **Economie de tokeni:** calculul în Actions; din sesiune se citesc doar `stare/<sursa>.json` și manifestul, nu
  jurnalele; o singură comandă blocantă pentru așteptare.

## Rulare

- Actions: workflow `sursa` (la cerere cu `sursa`, sau săptămânal pentru toate sursele `actions`).
- Local, fără publicare: `python -m comun.motor <sursa> --fara-publicare` (cere `gh` doar pentru publicare).
