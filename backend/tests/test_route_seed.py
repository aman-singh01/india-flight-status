import json

from app import route


def test_seed_file_is_valid():
    data = json.loads(route._SEED.read_text(encoding="utf-8"))
    seed = data["routes"]
    assert len(seed) >= 500
    india_india = 0
    for cs, r in seed.items():
        assert cs and isinstance(cs, str)
        assert r["dep"] and r["arr"]
        if r.get("dep_country") == "IN" and r.get("arr_country") == "IN":
            india_india += 1
    assert india_india >= 300  # the bulk of the seed is domestic


def test_seed_is_loaded_into_the_resolver():
    # route._load_cache() runs at import and pulls the seed into _routes
    assert len(route._routes) >= 500
    seed = json.loads(route._SEED.read_text(encoding="utf-8"))["routes"]
    sample = next(iter(seed))
    assert route.get(sample) is not None
    assert route.get(sample)["arr"] == seed[sample]["arr"]
