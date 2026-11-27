"""Mottak for temperaturmålingar frå Arduino MKR NB 1500 over mobilnettet.

Kvar gruppe har ein hemmeleg nøkkel. Arduinoen sender

    POST /api/maaling
    Content-Type: application/x-www-form-urlencoded

    nokkel=<gruppenøkkel>&temperatur=21.44

og serveren legg til tidspunktet og lagrar målinga i <gruppe>.csv, i same format
som i del 3 av opplegget (tid,temperatur).
"""

import hmac
import json
import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"
CSV_HOVUD = "tid,temperatur\n"
# Ei måling er nokre få byte. Større førespurnader blir avviste.
MAKS_STORLEIK_BYTE = 1024
# Gruppenamnet blir brukt som filnamn, så det må vere enkelt.
GYLDIG_NAMN = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
MIN_NOKKEL_LENGD = 8


def les_grupper(tekst: str) -> dict[str, str]:
    """Les «namn:nøkkel,namn:nøkkel» og returnerer {namn: nøkkel}."""
    grupper: dict[str, str] = {}
    for del_ in tekst.replace("\n", ",").split(","):
        del_ = del_.strip()
        if not del_:
            continue
        namn, skiljeteikn, nokkel = del_.partition(":")
        namn, nokkel = namn.strip(), nokkel.strip()
        if not skiljeteikn:
            raise ValueError(f"Manglar «:» mellom namn og nøkkel i «{del_}»")
        if not GYLDIG_NAMN.match(namn):
            raise ValueError(f"Ugyldig gruppenamn «{namn}»: bruk berre a-z, 0-9 og bindestrek (maks 30 teikn)")
        if len(nokkel) < MIN_NOKKEL_LENGD:
            raise ValueError(f"Nøkkelen til «{namn}» må ha minst {MIN_NOKKEL_LENGD} teikn")
        if namn in grupper:
            raise ValueError(f"Gruppa «{namn}» er nemnd fleire gonger")
        if nokkel in grupper.values():
            raise ValueError(f"To grupper har same nøkkel (sjå «{namn}»)")
        grupper[namn] = nokkel
    return grupper


def lag_app(
    grupper: dict[str, str] | None = None,
    data_dir: Path | None = None,
    min_sekund: float | None = None,
    tidssone: str | None = None,
    klokke: Callable[[], float] = time.monotonic,
    no: Callable[[ZoneInfo], datetime] = datetime.now,
) -> FastAPI:
    """Lagar appen. Utan argument blir innstillingane henta frå miljøvariablar (sjå compose.yaml)."""
    if grupper is None:
        grupper = les_grupper(os.environ.get("GRUPPER", ""))
        if not grupper:
            raise SystemExit("Miljøvariabelen GRUPPER manglar. Sjå compose.yaml.")
    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    if min_sekund is None:
        min_sekund = float(os.environ.get("MIN_SEKUND_MELLOM_MAALINGAR", 10))
    sone = ZoneInfo(tidssone or os.environ.get("TIDSSONE", "Europe/Oslo"))

    data_dir.mkdir(parents=True, exist_ok=True)
    skrivelaas = threading.Lock()
    siste_maaling: dict[str, float] = {}

    app = FastAPI(title="MKR-mottak")

    def csv_fil(namn: str) -> Path:
        if namn not in grupper:
            raise HTTPException(404, f"Fann inga gruppe som heiter «{namn}».")
        return data_dir / f"{namn}.csv"

    def finn_gruppe(nokkel: str) -> str | None:
        funnen = None
        for namn, rett in grupper.items():
            # compare_digest brukar like lang tid uansett kor mykje som stemmer
            if hmac.compare_digest(nokkel.encode(), rett.encode()):
                funnen = namn
        return funnen

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/maaling")
    async def ny_maaling(request: Request):
        kropp = await request.body()
        if len(kropp) > MAKS_STORLEIK_BYTE:
            raise HTTPException(413, "For stor førespurnad.")
        felt = _les_felt(kropp, request.headers.get("content-type", ""))

        namn = finn_gruppe(felt.get("nokkel", ""))
        if namn is None:
            raise HTTPException(403, "Ukjend eller manglande nøkkel.")

        tekst = felt.get("temperatur", "").strip().replace(",", ".")
        try:
            temperatur = float(tekst)
        except ValueError:
            raise HTTPException(422, f"«temperatur» må vere eit tal, fekk «{tekst}».")
        if not math.isfinite(temperatur) or abs(temperatur) > 1000:
            raise HTTPException(422, f"Temperaturen {tekst} er ikkje gyldig.")

        with skrivelaas:
            no_mono = klokke()
            forrige = siste_maaling.get(namn)
            if forrige is not None and no_mono - forrige < min_sekund:
                vent = math.ceil(min_sekund - (no_mono - forrige))
                raise HTTPException(
                    429,
                    f"For mange målingar. Vent minst {min_sekund:g} sekund mellom kvar.",
                    headers={"Retry-After": str(vent)},
                )
            siste_maaling[namn] = no_mono

            tid = no(sone).strftime("%Y-%m-%d %H:%M:%S")
            fil = data_dir / f"{namn}.csv"
            ny_fil = not fil.exists()
            with fil.open("a", encoding="utf-8", newline="") as f:
                if ny_fil:
                    f.write(CSV_HOVUD)
                f.write(f"{tid},{temperatur:.2f}\n")
        return {"ok": True, "gruppe": namn, "tid": tid, "temperatur": round(temperatur, 2)}

    @app.get("/api/grupper")
    def oversikt():
        resultat = []
        for namn in sorted(grupper):
            antal, siste = 0, None
            fil = data_dir / f"{namn}.csv"
            if fil.exists():
                with fil.open(encoding="utf-8") as f:
                    next(f, None)  # hopp over overskrifta
                    for linje in f:
                        antal += 1
                        siste = linje
            maaling = _les_linje(siste) if siste else None
            resultat.append({"namn": namn, "antal": antal, "siste": maaling})
        return resultat

    @app.get("/api/grupper/{namn}/maalingar")
    def maalingar(namn: str, antal: int = Query(500, ge=1, le=10_000)):
        fil = csv_fil(namn)
        if not fil.exists():
            return []
        with fil.open(encoding="utf-8") as f:
            next(f, None)
            siste = deque(f, maxlen=antal)
        return [m for linje in siste if (m := _les_linje(linje))]

    @app.get("/data/{namn}.csv")
    def last_ned(namn: str):
        fil = csv_fil(namn)
        if not fil.exists():
            raise HTTPException(404, f"Gruppa «{namn}» har ikkje sendt nokon målingar enno.")
        return FileResponse(
            fil,
            media_type="text/csv; charset=utf-8",
            filename=f"{namn}.csv",
            content_disposition_type="inline",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/", include_in_schema=False)
    def framside():
        return FileResponse(STATIC_DIR / "index.html")

    return app


def _les_felt(kropp: bytes, innhaldstype: str) -> dict[str, str]:
    """Godtek både skjemaformat (nokkel=...&temperatur=...) og JSON."""
    try:
        tekst = kropp.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "Førespurnaden må vere UTF-8.")
    if "json" in innhaldstype:
        try:
            data = json.loads(tekst)
        except json.JSONDecodeError:
            raise HTTPException(400, "Ugyldig JSON.")
        if not isinstance(data, dict):
            raise HTTPException(400, "JSON må vere eit objekt, t.d. {\"nokkel\": \"...\", \"temperatur\": 21.4}.")
        return {k: str(v) for k, v in data.items()}
    return {k: v[0] for k, v in parse_qs(tekst).items()}


def _les_linje(linje: str) -> dict | None:
    tid, _, temperatur = linje.strip().partition(",")
    try:
        return {"tid": tid, "temperatur": float(temperatur)}
    except ValueError:
        return None
