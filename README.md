# MKR-mottak

Ein liten server som tek imot temperaturmålingar frå **Arduino MKR NB 1500** over mobilnettet, og lagrar dei som CSV-filer, éi per elevgruppe. Laga til del 4 av MKR-opplegget i [raspberry-mm](https://github.com/hausnes/raspberry-mm/tree/main/mkr).

- Kvar gruppe har eit namn og ein hemmeleg nøkkel. Nøkkelen står i Arduino-koden til gruppa.
- Serveren set tidspunktet når målinga kjem fram, og lagrar i same format som i del 3 (`tid,temperatur`).
- Framsida viser siste måling og ein graf for kvar gruppe. CSV-filene kan lastast ned eller lesast rett inn i pandas.
- Kvar gruppe kan sende éi måling per 10. sekund (kan endrast).

## API

| Metode og adresse | Kva |
|---|---|
| `POST /api/maaling` | Ny måling. Skjemaformat (`nokkel=...&temperatur=21.44`) eller JSON. Svarar `200` ved suksess, `403` ved feil nøkkel, `422` om temperaturen ikkje er eit tal, `429` om gruppa sender for ofte. |
| `GET /data/<gruppe>.csv` | Alle målingane til gruppa. |
| `GET /api/grupper` | Oversikt: tal målingar og siste måling per gruppe. |
| `GET /api/grupper/<gruppe>/maalingar?antal=500` | Dei siste målingane som JSON. |
| `GET /api/health` | Helsesjekk for Docker. |

Test frå terminalen:

```sh
curl -d "nokkel=DIN-NOKKEL&temperatur=21.44" https://<din-adresse>/api/maaling
```

I Python:

```python
import pandas as pd
df = pd.read_csv("https://<din-adresse>/data/gruppe1.csv", parse_dates=["tid"])
```

Rådata blir lagra slik dei kjem, også feilverdiar som `-127` (fråkopla DS18B20) og `85` (rett etter oppstart). Å rydde dei er ein del av opplegget.

## Utvikling

Krev Python 3.12+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest

# Køyr lokalt på http://localhost:8000 (CSV-filene havnar i ./data)
GRUPPER="gruppe1:testnokkel1,gruppe2:testnokkel2" DATA_DIR=data \
  .venv/bin/uvicorn app.main:lag_app --factory --reload
```

## Hosting på Synology NAS

Ved kvar push til `main` køyrer GitHub Actions testane og publiserer imaget til `ghcr.io/hausnes/mkr-mottak:latest` (amd64 og arm64).

1. **Lag gruppenøklar**, éin per gruppe:
   ```sh
   python3 -c "import secrets; print(secrets.token_urlsafe(9))"
   ```
2. **Container Manager → Prosjekt → Opprett.** Lim inn `compose.yaml` og byt ut nøklane i `GRUPPER`. Ekte nøklar skal berre liggje på NAS-en, aldri i dette repoet.
3. Tenesta svarar no på `http://<nas-ip>:8010`.
4. **Reverse proxy med HTTPS:** Kontrollpanel → Påloggingsportal → Avansert → Omvendt proxy → Opprett.
   - Kjelde: HTTPS, vertsnamn t.d. `mkr.dittdomene.no`, port 443.
   - Mål: HTTP, `localhost`, port 8010.
   - Sertifikat: Kontrollpanel → Sikkerheit → Sertifikat. Legg til eit Let's Encrypt-sertifikat for vertsnamnet, og vel det for proxyen under «Innstillingar».
   - Opne berre port 443 i ruteren, ikkje 8010.
5. **Oppdatere:** Container Manager → Prosjekt → mkr-mottak → Stopp → Bygg. `pull_policy: always` gjer at nyaste image blir henta.

### HTTPS og MKR NB 1500

Modemet på MKR NB 1500 (u-blox SARA-R410M) kontrollerer sertifikatet sjølv. MKRNB-biblioteket (v1.6.0) lastar ikkje inn rotsertifikatet til Let's Encrypt (ISRG Root X1), så Arduino-koden må leggje det inn sjølv med `NBSSLClient(rotsertifikat, antal)`. Det er gjort i Arduino-koden til del 4.

Om tilkoplinga feilar under TLS-handtrykket, kan det hjelpe å setje TLS-profilen for proxyen til «Mellomliggjande kompatibilitet» (Kontrollpanel → Sikkerheit → Avansert → TLS/SSL-profilnivå). Eldre modem støttar ikkje alltid dei strengaste innstillingane.

### Data

CSV-filene ligg i Docker-volumet `mkr-data` og overlever oppdateringar og omstart. Slik ser du eller slettar filene (t.d. før ein ny klasse):

Container Manager → Container → mkr-mottak → Handling → Opne terminal → Opprett → `sh`:

```sh
ls -l /data
rm /data/gruppe1.csv      # nullstill gruppe1
```

Ta ein kopi først ved å laste ned CSV-fila frå framsida.

Vil du heller ha filene i ei vanleg mappe på NAS-en, kan du byte `mkr-data:/data` med t.d. `/volume1/docker/mkr-mottak:/data`. Då må mappa vere skrivbar for brukaren i containeren, til dømes ved å gje «Alle» skrivetilgang til mappa i File Station.
