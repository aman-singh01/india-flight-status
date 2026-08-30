from __future__ import annotations

import math
import random
import time

from ..domestic import INDIAN_AIRPORTS
from .base import Source, normalize_reapi

_AIRLINE_KEYS = ["IGO", "IGO", "IGO", "AIC", "AIC", "SEJ", "SEJ", "AKJ", "LLR", "AXB"]
_TYPES = ["A20N", "A21N", "A320", "B738", "B38M", "AT76"]
_METROS = {"DEL", "BOM", "BLR", "MAA", "HYD", "CCU", "GOX", "GOI", "AMD", "PNQ", "COK", "JAI", "LKO"}


def _pools() -> tuple[list, list]:
    metros = [a for a in INDIAN_AIRPORTS if a[0] in _METROS]
    others = [a for a in INDIAN_AIRPORTS if a[0] not in _METROS]
    return metros, others


def _gc_nm(p1, p2) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dp, dl = lat2 - lat1, lon2 - lon1
    a = math.sin(dp / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def _slerp(p1, p2, f):
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    v1 = (math.cos(lat1) * math.cos(lon1), math.cos(lat1) * math.sin(lon1), math.sin(lat1))
    v2 = (math.cos(lat2) * math.cos(lon2), math.cos(lat2) * math.sin(lon2), math.sin(lat2))
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(v1, v2, strict=True))))
    omega = math.acos(dot)
    if omega < 1e-6:
        x, y, z = v1
    else:
        a = math.sin((1 - f) * omega) / math.sin(omega)
        b = math.sin(f * omega) / math.sin(omega)
        x, y, z = (a * v1[0] + b * v2[0], a * v1[1] + b * v2[1], a * v1[2] + b * v2[2])
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def _bearing(p1, p2) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dl = lon2 - lon1
    y = math.sin(dl) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


class _Flight:
    def __init__(self) -> None:
        metros, others = _pools()

        def pick():
            return random.choice(metros) if random.random() < 0.7 else random.choice(others)

        self.dep = pick()
        self.arr = pick()
        while self.arr[0] == self.dep[0] or _gc_nm(self.dep[2:4], self.arr[2:4]) < 130:
            self.arr = pick()

        self.ak = random.choice(_AIRLINE_KEYS)
        self.num = random.randint(100, 3999)
        self.hex = f"a{random.randint(0, 0xFFFFF):05x}"
        self.reg = "VT-" + "".join(random.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(3))
        self.type = random.choice(_TYPES)
        self.dist = _gc_nm(self.dep[2:4], self.arr[2:4])
        self.gs = random.randint(410, 480)
        self.cruise = random.choice([30000, 32000, 34000, 36000, 38000])
        # start somewhere in the first 92% of the route so state() is always valid
        self.t0 = time.time() - random.uniform(0.0, self.dist / self.gs * 3600 * 0.92)

    def state(self, now: float) -> dict | None:
        f = (now - self.t0) / 3600 * self.gs / self.dist
        if f >= 1.02:
            return None
        f = max(0.0, min(1.0, f))
        p1, p2 = self.dep[2:4], self.arr[2:4]
        lat, lon = _slerp(p1, p2, f)
        climb = min(f * self.dist, 110) / 110
        desc = min((1 - f) * self.dist, 110) / 110
        alt = int(self.cruise * min(climb, desc))
        vs = 1900 if climb < 1 and f < 0.5 else (-1700 if desc < 1 and f > 0.5 else 0)
        return {
            "hex": self.hex,
            "flight": f"{self.ak}{self.num}",
            "r": self.reg,
            "t": self.type,
            "lat": lat + random.uniform(-0.004, 0.004),
            "lon": lon + random.uniform(-0.004, 0.004),
            "alt_baro": max(0, alt),
            "gs": self.gs,
            "track": _bearing((lat, lon), p2),
            "baro_rate": vs,
        }


class DemoSource(Source):
    """Synthetic domestic traffic between real Indian airports. No network needed."""

    name = "demo"

    def __init__(self, n: int = 45) -> None:
        self.flights = [_Flight() for _ in range(n)]

    async def fetch(self) -> list[dict]:
        now = time.time()
        out = []
        for i, fl in enumerate(self.flights):
            st = fl.state(now)
            if st is None:
                self.flights[i] = _Flight()
                st = self.flights[i].state(now)
            n = normalize_reapi(st, self.name) if st else None
            if n:
                out.append(n)
        return out
