"""Movebank Data Repository (DSpace 7 REST). The only module that talks to the repository."""
from __future__ import annotations

import html
import json
import re
import time
from functools import cache
from pathlib import Path

import requests

from . import config
from .model import Package, RepoFile

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT

LOCATION_SKIP_WORDS = ("acc", "light-level", "barometer", "magnet", "orientation", "accessor",
                       "readme", "twilight", "gyro", "tdr")


def _get_json(url: str, **params) -> dict:
    for attempt in range(6):
        try:
            r = _session.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"repository request failed: {url}")


def _md(o: dict, key: str) -> list[str]:
    return [x["value"] for x in o["metadata"].get(key, [])]


def _item_record(o: dict) -> dict:
    """Raw catalog line for one repository item (the on-disk catalog format)."""
    b = _get_json(f"{config.REPO_API}/core/items/{o['uuid']}/bundles", embed="bitstreams")
    files = []
    for bun in b["_embedded"]["bundles"]:
        if bun["name"] != "ORIGINAL":
            continue
        for bs in bun["_embedded"]["bitstreams"]["_embedded"]["bitstreams"]:
            files.append({"name": bs["name"], "size": bs["sizeBytes"], "url": bs["_links"]["content"]["href"]})
    first = lambda k: (_md(o, k) or [""])[0]  # noqa: E731
    return {
        "uuid": o["uuid"], "title": o["name"], "doi": first("dc.identifier.doi"),
        "rights": first("dc.rights"), "rights_uri": first("dc.rights.uri"),
        "taxa": _md(o, "dwc.ScientificName"), "sensors": _md(o, "sensor.name"),
        "citation": first("dc.identifier.citation"), "cse": first("mdr.citation.CSE"),
        "authors": _md(o, "dc.contributor.author"), "issued": first("dc.date.issued"),
        "study_id": first("mdr.study.id"), "handle": first("dc.identifier.uri"), "files": files,
    }


def parse_package(index: int, rec: dict) -> Package:
    cse = re.sub(r"<[^>]+>", "", html.unescape(rec.get("cse") or "")).strip()
    return Package(
        index=index, uuid=rec["uuid"], doi=rec["doi"], title=rec["title"], citation=cse,
        licence=rec.get("rights") or "", study_id=str(rec.get("study_id") or ""),
        taxa=tuple(rec.get("taxa") or ()),
        files=tuple(RepoFile(f["name"], int(f["size"]), f["url"]) for f in rec["files"]),
    )


def update_catalog() -> int:
    """Append items not yet in the catalog. Existing lines keep their index, so package
    numbers stay stable."""
    have = set()
    if config.CATALOG.exists():
        have = {json.loads(line)["uuid"] for line in config.CATALOG.open()}
    items, page = [], 0
    while True:
        d = _get_json(f"{config.REPO_API}/discover/search/objects", dsoType="ITEM", size=100, page=page,
                      **{"f.entityType": "Datapackage,equals"})
        s = d["_embedded"]["searchResult"]
        items += [x["_embedded"]["indexableObject"] for x in s["_embedded"]["objects"]]
        page += 1
        if page >= s["page"]["totalPages"]:
            break
    config.CATALOG.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    with config.CATALOG.open("a") as f:
        for o in items:
            if o["uuid"] in have:
                continue
            f.write(json.dumps(_item_record(o)) + "\n")
            f.flush()
            added += 1
            time.sleep(0.2)
    return added


@cache
def packages() -> tuple[Package, ...]:
    return tuple(parse_package(i, json.loads(line)) for i, line in enumerate(config.CATALOG.open()))


def package_by_doi(doi: str) -> Package:
    """Look the DOI up in the repository itself, so verify does not trust the local catalog."""
    bare = doi.removeprefix("doi:")
    d = _get_json(f"{config.REPO_API}/discover/search/objects", dsoType="ITEM", query=f'"{bare}"')
    for x in d["_embedded"]["searchResult"]["_embedded"]["objects"]:
        o = x["_embedded"]["indexableObject"]
        if (_md(o, "dc.identifier.doi") or [""])[0].removeprefix("doi:") == bare:
            return parse_package(-1, _item_record(o))
    raise LookupError(f"DOI not found in the repository: {doi}")


def is_location_file(name: str) -> bool:
    n = name.lower()
    return n.endswith((".csv", ".zip")) and "reference" not in n and not any(w in n for w in LOCATION_SKIP_WORDS)


def is_reference_file(name: str) -> bool:
    return "reference" in name.lower()


def wanted_files(pkg: Package) -> list[RepoFile]:
    return [f for f in pkg.files if is_location_file(f.name) or is_reference_file(f.name)]


def download(f: RepoFile, dest: Path, max_stalls: int = 8) -> int:
    """Resumable download with HTTP Range requests and exponential backoff.
    The server cuts long transfers; each retry continues from the last byte on disk."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    stalls = 0
    while True:
        have = dest.stat().st_size if dest.exists() else 0
        if have >= f.size:
            return have
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with _session.get(f.url, headers=headers, stream=True, timeout=120) as r:
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                if have and r.status_code != 206:
                    dest.unlink()
                    continue
                with dest.open("ab") as out:
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk)
        except (requests.RequestException, OSError):
            pass
        now = dest.stat().st_size if dest.exists() else 0
        if now >= f.size:
            return now
        stalls = 0 if now > have else stalls + 1
        if stalls >= max_stalls:
            raise RuntimeError(f"download stalled at {now}/{f.size} bytes: {f.name}")
        time.sleep(min(300, 5 * 2 ** stalls))
