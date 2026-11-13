"""verify <record-id>: download the record's package again, recompute the record from raw data
and compare. Trusts only the record's recipe (DOI, animal, sensor, category, time range)."""
from __future__ import annotations

import json
import shutil
import time
import urllib.request
from pathlib import Path

from . import config, repo, taxa
from .categories import BY_KEY
from .clean import clean_package, load_deployments, load_fixes
from .extract import extract

REL_TOL = 1e-3
TIME_TOL_S = 1.0


def _recipe(record_id: str, site_dir: str | None) -> dict:
    src = site_dir or str(config.SITE)
    if src.startswith(("http://", "https://")):
        # The default urllib client name gets HTTP 403 from the site's host.
        req = urllib.request.Request(f"{src.rstrip('/')}/r/{record_id}/record.json", headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    return json.loads((Path(src) / "r" / record_id / "record.json").read_text())


def verify(record_id: str, site_dir: str | None = None, keep: bool = False) -> bool:
    rec = _recipe(record_id, site_dir)
    rp = rec["reproduce"]
    print(f"record   {record_id}")
    print(f"claim    {rec['category']} = {rec['value']} {rec['unit']}, animal {rp['animal']!r} ({rp['sensor']}), "
          f"{rp['start']} .. {rp['end']}")
    pkg = repo.package_by_doi(rp["doi"])
    files = repo.wanted_files(pkg)
    print(f"package  {pkg.doi} :: {pkg.title[:70]}")
    work = config.VERIFY / record_id
    shutil.rmtree(work, ignore_errors=True)
    raw = work / "raw"
    t0 = time.time()
    for f in files:
        repo.download(f, raw / f.name.replace("/", "_"))
    print(f"download {sum(f.size for f in files) / 1e6:.1f} MB, {len(files)} files, {time.time() - t0:.0f} s")
    stats = extract(raw, work / "fixes.parquet", only_ind=rp["animal"])
    ref = next((p for p in raw.iterdir() if repo.is_reference_file(p.name)), work / "none.csv")
    df = load_fixes(work / "fixes.parquet")
    names = set(df["taxon"].unique().to_list()) | set(pkg.taxa)
    taxa.update({n for n in names if n})
    taxa._table.cache_clear()
    fallback = (pkg.taxa or ("",))[0]
    tracks, counts = clean_package(df, load_deployments(ref), lambda n: taxa.taxon(n, fallback), -1)
    print(f"fixes    {stats['loc_rows']} raw rows for the animal, {stats['kept']} after 5-min thinning, "
          f"{counts['kept']} after cleaning")
    tr = next((t for t in tracks if t.sensor == rp["sensor"]), None)
    cat = BY_KEY[rec["category"]]
    got = cat.fn(tr) if tr is not None else []
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    if not got:
        print("result   MISMATCH: no effort recomputed")
        return False
    e = got[0]
    t0r, t1r = float(tr.t[e.i0]), float(tr.t[e.i1])
    ok_v = abs(e.value - rec["value"]) <= REL_TOL * max(abs(rec["value"]), 1e-9)
    ok_t = abs(t0r - rp["t0"]) <= TIME_TOL_S and abs(t1r - rp["t1"]) <= TIME_TOL_S
    print(f"recomputed {e.value:.3f} {cat.unit} from {e.i1 - e.i0 + 1} fixes, "
          f"t0 {t0r:.0f} (claim {rp['t0']:.0f}), t1 {t1r:.0f} (claim {rp['t1']:.0f})")
    print("result   " + ("MATCH" if ok_v and ok_t else "MISMATCH"))
    return ok_v and ok_t
