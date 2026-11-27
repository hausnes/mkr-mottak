from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import les_grupper, lag_app

GRUPPER = {"gruppe1": "hemmeleg-1", "gruppe2": "hemmeleg-2"}


class Klokke:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def klokke():
    return Klokke()


@pytest.fixture
def klient(tmp_path, klokke):
    app = lag_app(
        grupper=GRUPPER,
        data_dir=tmp_path,
        min_sekund=10,
        klokke=klokke,
        no=lambda sone: datetime(2026, 11, 27, 9, 15, 3),
    )
    return TestClient(app)


def send(klient, nokkel="hemmeleg-1", temperatur="21.44"):
    return klient.post("/api/maaling", data={"nokkel": nokkel, "temperatur": temperatur})


def test_lagrar_maaling_som_csv(klient, tmp_path):
    svar = send(klient)
    assert svar.status_code == 200
    assert svar.json()["gruppe"] == "gruppe1"
    assert (tmp_path / "gruppe1.csv").read_text() == "tid,temperatur\n2026-11-27 09:15:03,21.44\n"


def test_godtek_json_og_komma(klient, klokke, tmp_path):
    assert klient.post("/api/maaling", json={"nokkel": "hemmeleg-2", "temperatur": 19.5}).status_code == 200
    klokke.t += 10
    assert send(klient, "hemmeleg-2", "19,75").status_code == 200
    assert (tmp_path / "gruppe2.csv").read_text().splitlines()[1:] == [
        "2026-11-27 09:15:03,19.50",
        "2026-11-27 09:15:03,19.75",
    ]


@pytest.mark.parametrize("nokkel", ["feil-nokkel", "", "hemmeleg-"])
def test_avviser_feil_nokkel(klient, nokkel):
    assert send(klient, nokkel).status_code == 403


@pytest.mark.parametrize("temperatur", ["", "varm", "nan", "inf", "5000"])
def test_avviser_ugyldig_temperatur(klient, temperatur):
    assert send(klient, temperatur=temperatur).status_code == 422


def test_held_sensorfeil_som_minus_127(klient, tmp_path):
    # DS18B20 gjev -127 når sensoren er fråkopla. Det er ekte rådata som elevane skal lære å rydde.
    assert send(klient, temperatur="-127").status_code == 200


def test_avviser_for_stor_forespurnad(klient):
    assert klient.post("/api/maaling", content=b"x" * 2000).status_code == 413


def test_avgrensar_kor_ofte_ei_gruppe_kan_sende(klient, klokke):
    assert send(klient).status_code == 200
    klokke.t += 4
    svar = send(klient)
    assert svar.status_code == 429
    assert svar.headers["Retry-After"] == "6"
    # Andre grupper blir ikkje påverka
    assert send(klient, "hemmeleg-2").status_code == 200
    klokke.t += 6
    assert send(klient).status_code == 200


def test_oversikt_og_maalingar(klient, klokke):
    assert klient.get("/api/grupper").json() == [
        {"namn": "gruppe1", "antal": 0, "siste": None},
        {"namn": "gruppe2", "antal": 0, "siste": None},
    ]
    for t in ["20.0", "20.5", "21.0"]:
        send(klient, temperatur=t)
        klokke.t += 10
    oversikt = klient.get("/api/grupper").json()
    assert oversikt[0] == {"namn": "gruppe1", "antal": 3, "siste": {"tid": "2026-11-27 09:15:03", "temperatur": 21.0}}
    siste_to = klient.get("/api/grupper/gruppe1/maalingar?antal=2").json()
    assert [m["temperatur"] for m in siste_to] == [20.5, 21.0]
    assert klient.get("/api/grupper/gruppe2/maalingar").json() == []
    assert klient.get("/api/grupper/finst-ikkje/maalingar").status_code == 404


def test_last_ned_csv(klient):
    assert klient.get("/data/gruppe1.csv").status_code == 404
    send(klient)
    svar = klient.get("/data/gruppe1.csv")
    assert svar.status_code == 200
    assert svar.headers["content-type"].startswith("text/csv")
    assert svar.text.startswith("tid,temperatur\n")
    assert klient.get("/data/finst-ikkje.csv").status_code == 404
    assert klient.get("/data/..%2Fetc%2Fpasswd.csv").status_code == 404


def test_framside_og_health(klient):
    assert klient.get("/api/health").json() == {"ok": True}
    svar = klient.get("/")
    assert svar.status_code == 200
    assert "MKR-mottak" in svar.text


def test_les_grupper():
    assert les_grupper(" a:12345678 ,\n b-2:abcdefgh,") == {"a": "12345678", "b-2": "abcdefgh"}
    for feil in ["a12345678", "A:12345678", "a/b:12345678", "a:kort", "a:12345678,a:abcdefgh", "a:12345678,b:12345678"]:
        with pytest.raises(ValueError):
            les_grupper(feil)


def test_manglande_grupper_stoppar_oppstart(monkeypatch, tmp_path):
    monkeypatch.delenv("GRUPPER", raising=False)
    with pytest.raises(SystemExit):
        lag_app(data_dir=tmp_path)
