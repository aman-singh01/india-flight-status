from app.status import candidate_callsigns, nearest_place, phase


def test_candidate_callsigns_iata_forms():
    assert "IGO203" in candidate_callsigns("6E203")
    assert "AIC2984" in candidate_callsigns("AI 2984")
    assert "AKJ1401" in candidate_callsigns("QP1401")


def test_candidate_callsigns_icao_and_suffix():
    assert candidate_callsigns("IGO203") == ["IGO203"]
    assert "IGO248F" in candidate_callsigns("6E248F")  # trailing-letter suffix


def test_candidate_callsigns_raw_fallback():
    assert "ZZ99" in candidate_callsigns("zz99")  # unknown prefix -> raw string kept


def test_phase():
    assert phase(None, 0, 5)[0] == "On ground"
    assert phase(None, 0, 60)[1] == "taxiing"
    assert phase(4000, 1800, 200)[0] == "Departed"
    assert phase(3000, -1200, 180)[0] == "On approach"
    assert phase(36000, 0, 460)[0] == "En route"
    assert "cruising" in phase(36000, 0, 460)[1]
    assert "climbing" in phase(36000, 900, 460)[1]


def test_nearest_place_over_and_near():
    assert nearest_place(28.556, 77.100) == "over Delhi"
    near = nearest_place(28.9, 77.5)
    assert near and "Delhi" in near and "km" in near
