from app.sources import build_sources
from app.sources.base import GRID, norm_alt, normalize_reapi


def test_norm_alt():
    assert norm_alt(35000) == 35000
    assert norm_alt("35000") == 35000
    assert norm_alt("ground") is None
    assert norm_alt(None) is None
    assert norm_alt("junk") is None


def test_normalize_reapi_typical():
    ac = {
        "hex": "800ABC",
        "flight": "IGO2416 ",
        "r": "VT-IPZ",
        "t": "A20N",
        "alt_baro": 36000,
        "gs": 465.4,
        "track": 210.2,
        "baro_rate": -64,
        "lat": 19.1,
        "lon": 75.2,
    }
    n = normalize_reapi(ac, "adsblol")
    assert n["hex"] == "800abc"  # lowercased
    assert n["callsign"] == "IGO2416"  # trailing space stripped
    assert n["registration"] == "VT-IPZ"
    assert n["alt_ft"] == 36000
    assert n["vs_fpm"] == -64
    assert n["source"] == "adsblol"


def test_normalize_reapi_rejects_non_icao_and_empty():
    assert normalize_reapi({"hex": "~ABC123", "lat": 1, "lon": 2}, "x") is None
    assert normalize_reapi({"hex": "", "lat": 1, "lon": 2}, "x") is None


def test_normalize_reapi_ground_and_missing_callsign():
    n = normalize_reapi({"hex": "abc123", "alt_baro": "ground", "lat": 1, "lon": 2}, "x")
    assert n["alt_ft"] is None
    assert n["callsign"] is None


def test_grid_covers_india_span():
    lats = {p[0] for p in GRID}
    lons = {p[1] for p in GRID}
    assert min(lats) <= 12 and max(lats) >= 28
    assert min(lons) <= 73 and max(lons) >= 91
    assert len(GRID) == 12


def test_build_sources_parses_and_dedups_kinds():
    names = [s.name for s in build_sources("demo,adsblol,adsbfi,readsb:http://pi/x.json,bogus")]
    assert names == ["demo", "adsblol", "adsbfi", "readsb"]


def test_build_sources_falls_back_to_demo():
    assert [s.name for s in build_sources("")] == ["demo"]
    assert [s.name for s in build_sources("readsb:")] == ["demo"]  # missing url -> skipped -> fallback
