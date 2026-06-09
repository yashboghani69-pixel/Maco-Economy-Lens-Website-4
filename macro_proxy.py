"""
Live India macro indicator proxy.
Pulls monthly data from DBnomics (free, no API key) which mirrors
IMF/IFS series. Caches in memory for 6 hours.
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Optional
import httpx

DBN_BASE = "https://api.db.nomics.world/v22/series"
WB_BASE = "https://api.worldbank.org/v2/country/IN/indicator"
CACHE_TTL = 6 * 60 * 60  # 6 hours
_cache: dict[str, tuple[float, Any]] = {}

# IMF/IFS series codes via DBnomics for monthly Indian indicators.
# Each entry maps our indicator id to a recipe.
#   provider/dataset/series, transform (raw / yoy_index / scale), unit_note
DBNOMICS_MAP: dict[str, dict] = {
    "inflation":      {"path": "IMF/IFS/M.IN.PCPI_IX",     "transform": "yoy_index"},   # CPI YoY% from index
    "forex":          {"path": "IMF/IFS/M.IN.RAFA_USD",    "transform": "scale", "factor": 1/1000},  # $M -> $B
    "industrial_prod":{"path": "IMF/IFS/M.IN.AIP_PC_CP_A_PT","transform": "raw"},        # IIP YoY%
    "exports":        {"path": "IMF/IFS/M.IN.TXG_FOB_USD", "transform": "yoy_index"},   # Exports YoY%
}

# World Bank annual codes
WB_MAP: dict[str, str] = {
    "gdp_growth":         "NY.GDP.MKTP.KD.ZG",
    "gfcf":               "NE.GDI.FTOT.ZS",
    "unemployment":       "SL.UEM.TOTL.ZS",
    "consumer_spending":  "NE.CON.PRVT.KD.ZG",
}


async def _fetch_dbnomics(client: httpx.AsyncClient, path: str) -> list[dict]:
    url = f"{DBN_BASE}/{path}?observations=1"
    r = await client.get(url, timeout=15)
    r.raise_for_status()
    j = r.json()
    docs = j.get("series", {}).get("docs", [])
    if not docs:
        return []
    d = docs[0]
    periods = d.get("period", [])
    values = d.get("value", [])
    # Filter out None values (DBnomics uses "NA" string sometimes)
    out = []
    for p, v in zip(periods, values):
        if v is None or v == "NA":
            continue
        try:
            out.append({"period": p, "value": float(v)})
        except (TypeError, ValueError):
            continue
    return out


def _yoy_from_index(series: list[dict]) -> list[dict]:
    """Convert a monthly index series into YoY % change."""
    if len(series) < 13:
        return []
    by_period = {s["period"]: s["value"] for s in series}
    out = []
    for s in series:
        # period format: YYYY-MM
        try:
            y, m = s["period"].split("-")
            prev = f"{int(y)-1}-{m}"
        except ValueError:
            continue
        if prev in by_period and by_period[prev]:
            yoy = (s["value"] / by_period[prev] - 1) * 100
            out.append({"period": s["period"], "value": round(yoy, 2)})
    return out


async def _build_indicator(client: httpx.AsyncClient, ind_id: str, recipe: dict) -> Optional[dict]:
    try:
        raw = await _fetch_dbnomics(client, recipe["path"])
        if not raw:
            return None
        if recipe["transform"] == "yoy_index":
            series = _yoy_from_index(raw)
        elif recipe["transform"] == "scale":
            f = recipe.get("factor", 1)
            series = [{"period": s["period"], "value": round(s["value"] * f, 2)} for s in raw]
        else:
            series = [{"period": s["period"], "value": round(s["value"], 2)} for s in raw]
        if not series:
            return None
        latest = series[-1]
        return {
            "id": ind_id,
            "value": latest["value"],
            "period": latest["period"],
            "source": "IMF/IFS via DBnomics",
            "history": series[-60:],  # last 5 years monthly
        }
    except Exception as e:
        return {"id": ind_id, "error": str(e)}


async def _fetch_wb(client: httpx.AsyncClient, code: str) -> list[dict]:
    url = f"{WB_BASE}/{code}?format=json&per_page=40"
    r = await client.get(url, timeout=15)
    r.raise_for_status()
    j = r.json()
    if len(j) < 2 or not j[1]:
        return []
    points = []
    for d in j[1]:
        if d.get("value") is None:
            continue
        try:
            points.append({"period": str(d["date"]), "value": round(float(d["value"]), 2)})
        except (TypeError, ValueError):
            continue
    points.sort(key=lambda x: x["period"])
    return points


async def _build_wb(client: httpx.AsyncClient, ind_id: str, code: str) -> Optional[dict]:
    try:
        series = await _fetch_wb(client, code)
        if not series:
            return None
        return {
            "id": ind_id,
            "value": series[-1]["value"],
            "period": series[-1]["period"],
            "source": "World Bank",
            "history": series,
        }
    except Exception as e:
        return {"id": ind_id, "error": str(e)}


async def fetch_all_indicators() -> dict:
    """Return all available live indicators. Caches for CACHE_TTL seconds."""
    key = "all_indicators"
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL:
        return {**cached[1], "cached": True, "cache_age_s": int(now - cached[0])}

    async with httpx.AsyncClient(headers={"User-Agent": "india-macro-lens/1.0"}) as client:
        dbn_tasks = [_build_indicator(client, k, v) for k, v in DBNOMICS_MAP.items()]
        wb_tasks = [_build_wb(client, k, c) for k, c in WB_MAP.items()]
        results = await asyncio.gather(*dbn_tasks, *wb_tasks, return_exceptions=True)

    indicators = {}
    errors = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        if not r:
            continue
        if "error" in r:
            errors[r["id"]] = r["error"]
        else:
            indicators[r["id"]] = r

    payload = {
        "indicators": indicators,
        "errors": errors,
        "fetched_at": int(now),
    }
    _cache[key] = (now, payload)
    return {**payload, "cached": False, "cache_age_s": 0}


async def fetch_single(ind_id: str) -> Optional[dict]:
    all_data = await fetch_all_indicators()
    return all_data["indicators"].get(ind_id)
