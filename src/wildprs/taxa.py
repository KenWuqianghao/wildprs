"""Taxon facts from the GBIF backbone (class, order, family, English name, IUCN Red List category),
with a curated name table (`names.toml`) that overrides GBIF common names."""
from __future__ import annotations

import json
import time
import tomllib
from functools import cache

import requests

from . import config
from .model import Taxon

GBIF = "https://api.gbif.org/v1"


def _lookup(name: str, s: requests.Session) -> dict:
    m = s.get(f"{GBIF}/species/match", params={"name": name}, timeout=30).json()
    out = {k: m.get(k) or "" for k in ("class", "order", "family", "species", "canonicalName", "rank")}
    out["usageKey"] = m.get("speciesKey") or m.get("usageKey")
    out["common"], out["iucn"] = "", ""
    if out["usageKey"]:
        v = s.get(f"{GBIF}/species/{out['usageKey']}/vernacularNames", params={"limit": 100}, timeout=30).json()
        en = [x["vernacularName"] for x in v.get("results", []) if x.get("language") == "eng"]
        out["common"] = en[0] if en else ""
        r = s.get(f"{GBIF}/species/{out['usageKey']}/iucnRedListCategory", timeout=30)
        if r.status_code == 200 and r.text.strip():
            out["iucn"] = r.json().get("code") or ""
    return out


def update(names: set[str]) -> int:
    have = json.loads(config.TAXA.read_text()) if config.TAXA.exists() else {}
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    todo = sorted(n for n in names if n and n not in have)
    for n in todo:
        try:
            have[n] = _lookup(n, s)
        except (requests.RequestException, ValueError) as e:
            print("taxon lookup failed", n, e)
        time.sleep(0.1)
    config.atomic_write(config.TAXA, json.dumps(have, indent=0, sort_keys=True))
    return len(todo)


@cache
def _table() -> dict:
    return json.loads(config.TAXA.read_text()) if config.TAXA.exists() else {}


@cache
def curated_names() -> dict[str, str]:
    if not config.NAMES_FILE.exists():
        return {}
    return tomllib.loads(config.NAMES_FILE.read_text()).get("common", {})


def taxon(name: str, fallback: str = "") -> Taxon:
    t = _table()
    key = name if name in t else " ".join(name.split()[:2]) if name else ""
    row = t.get(key)
    if not (row and row.get("class")) and fallback:
        key, row = fallback, t.get(fallback)
    row = row or {}
    species = row.get("species") or row.get("canonicalName") or key
    common = curated_names().get(species) or curated_names().get(key) or row.get("common") or ""
    return Taxon(name=species or name, cls=row.get("class", ""), order=row.get("order", ""),
                 family=row.get("family", ""), common=common, iucn=row.get("iucn", ""))
