"""wildprs: one command per pipeline stage. Every stage is idempotent: it does only missing work
unless --force is given."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

from . import config


def _pkgs(arg: str | None, available) -> list[int]:
    if arg:
        return [int(x) for x in arg.split(",")]
    return sorted(available)


def _fixes_pkgs():
    return [int(p.name[:3]) for p in config.FIXES.glob("*.parquet")]


def _stale(out, inp) -> bool:
    return not out.exists() or out.stat().st_mtime < inp.stat().st_mtime


def _run_pool(fn, pkgs, jobs, label):
    done = 0
    t0 = time.time()
    with ProcessPoolExecutor(jobs) as ex:
        futs = {ex.submit(fn, p): p for p in pkgs}
        for f in as_completed(futs):
            p = futs[f]
            try:
                f.result()
                done += 1
            except Exception as e:  # one bad package must not stop the stage
                print(f"{label} {p:03d} FAILED {e!r}", file=sys.stderr)
                config.log(f"{label} pkg {p:03d} FAILED {e!r}"[:300])
            if done % 25 == 0:
                print(f"{label}: {done}/{len(pkgs)} in {time.time() - t0:.0f}s", flush=True)
    return done


def cmd_catalog(a):
    from . import repo, taxa
    import polars as pl
    if a.refresh:
        print("new packages:", repo.update_catalog())
    names = {t for p in repo.packages() for t in p.taxa}
    for f in config.FIXES.glob("*.parquet"):
        names.update(x for x in pl.read_parquet(f, columns=["taxon"])["taxon"].cast(pl.String).unique().to_list() if x)
    print("taxa looked up:", taxa.update(names))
    config.log(f"catalog done: {len(repo.packages())} packages, {len(names)} taxa")


def _fetch_one(pkg: int, lock: threading.Lock, max_bytes: float) -> str:
    from . import extract, repo
    p = repo.packages()[pkg]
    raw = config.RAW / f"{pkg:03d}"
    files = repo.wanted_files(p)
    need = sum(f.size for f in files)
    while True:
        with lock:
            used = sum(f.stat().st_size for f in config.RAW.rglob("*") if f.is_file())
            if used + need <= max_bytes or used == 0:
                break
        time.sleep(30)
    t0 = time.time()
    for f in files:
        repo.download(f, raw / f.name.replace("/", "_"))
    for f in raw.iterdir():
        if repo.is_reference_file(f.name):
            config.REF.mkdir(parents=True, exist_ok=True)
            shutil.copy(f, config.REF / f"{pkg:03d}.csv")
    stats = extract.extract(raw, config.FIXES / f"{pkg:03d}.parquet")
    shutil.rmtree(raw)
    msg = f"fetch pkg {pkg:03d} ok: {need / 1e6:.0f} MB in {time.time() - t0:.0f}s, {stats['kept']} fixes :: {p.title[:60]}"
    config.log(msg)
    return msg


def cmd_fetch(a):
    from . import repo
    have = {int(p.name[:3]) for p in config.FIXES.glob("*.json")}
    todo = [p.index for p in repo.packages() if p.is_cc0 and p.index not in have]
    if a.pkgs:
        todo = [int(x) for x in a.pkgs.split(",")]
    todo.sort(key=lambda i: sum(f.size for f in repo.wanted_files(repo.packages()[i])))
    print(f"fetch: {len(todo)} packages")
    lock = threading.Lock()
    failed = []
    with ThreadPoolExecutor(min(a.jobs, 3)) as ex:
        futs = {ex.submit(_fetch_one, i, lock, a.max_gb * 1e9): i for i in todo}
        for f in as_completed(futs):
            i = futs[f]
            try:
                print(f.result(), flush=True)
            except Exception as e:
                failed.append(i)
                # Partial raw files stay on disk so that the next fetch resumes them with Range requests.
                config.log(f"fetch pkg {i:03d} FAILED {e!r}"[:300])
                print(f"fetch {i:03d} FAILED {e!r}", flush=True)
    config.atomic_write(config.META / "not_covered.json", json.dumps(sorted(failed)))
    config.log(f"fetch end: {len(todo) - len(failed)} ok, {len(failed)} failed")


def cmd_extract(a):
    from . import extract
    for d in sorted(config.RAW.iterdir()) if config.RAW.exists() else []:
        if a.pkgs and int(d.name) not in _pkgs(a.pkgs, []):
            continue
        print(extract.extract(d, config.FIXES / f"{int(d.name):03d}.parquet"))


def _clean_one(pkg):
    from .pipeline import clean_pkg
    return clean_pkg(pkg)


def cmd_clean(a):
    pkgs = [p for p in _pkgs(a.pkgs, _fixes_pkgs())
            if a.force or _stale(config.CLEAN / f"{p:03d}.json", config.FIXES / f"{p:03d}.parquet")]
    config.CLEAN.mkdir(parents=True, exist_ok=True)
    n = _run_pool(_clean_one, pkgs, a.jobs, "clean")
    config.log(f"clean done: {n}/{len(pkgs)} packages")


def _records_one(pkg):
    from .pipeline import records_pkg
    return records_pkg(pkg)


def cmd_records(a):
    from .pipeline import candidates
    have = [int(p.name[:3]) for p in config.CLEAN.glob("*.json")]
    pkgs = [p for p in _pkgs(a.pkgs, have)
            if a.force or _stale(config.RECORDS / "efforts" / f"{p:03d}.parquet", config.CLEAN / f"{p:03d}.json")]
    n = _run_pool(_records_one, pkgs, a.jobs, "records")
    c = candidates()
    config.log(f"records done: {n}/{len(pkgs)} packages, {c.height} candidates, "
               f"{c['animal'].n_unique()} animals")


def cmd_audit(a):
    from . import audit
    s = audit.run()
    config.log(f"audit done: {s}")
    print(s)


def cmd_site(a):
    from . import site
    s = site.build(base_url=a.base_url, png=not a.no_png)
    config.log(f"site done: {s}")
    print(s)


def cmd_video(a):
    from . import video
    done = video.render(config.SITE, force=a.force, only=a.only.split(",") if a.only else None)
    for d in done:
        print(d)
    config.log(f"video done: {len(done)} rendered, {len(video.wanted(config.SITE))} linked")


def cmd_verify(a):
    from . import verify
    sys.exit(0 if verify.verify(a.record_id, site_dir=a.site, keep=a.keep) else 1)


def cmd_check(a):
    from . import sitecheck
    sys.exit(0 if sitecheck.main(config.SITE) else 1)


def cmd_all(a):
    for fn in (cmd_clean, cmd_records, cmd_audit, cmd_site):
        fn(a)
    if not a.no_png:
        a.only = None
        cmd_video(a)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wildprs", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("catalog", help="list repository packages and look up taxa (GBIF)")
    s.add_argument("--refresh", action="store_true", help="also ask the repository for new packages")
    s.set_defaults(fn=cmd_catalog)
    s = sub.add_parser("fetch", help="download, extract and delete raw files of CC0 packages")
    s.add_argument("--pkgs", help="comma-separated package numbers (default: all CC0 packages not yet extracted)")
    s.add_argument("--jobs", type=int, default=3, help="parallel downloads, at most 3")
    s.add_argument("--max-gb", type=float, default=25, help="disk limit for raw files")
    s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("extract", help="extract raw files left in data/raw")
    s.add_argument("--pkgs")
    s.set_defaults(fn=cmd_extract)
    for name, fn, hlp in (("clean", cmd_clean, "apply the cleaning rules"),
                          ("records", cmd_records, "compute efforts per package, merge duplicate animals")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--pkgs")
        s.add_argument("--jobs", type=int, default=3)
        s.add_argument("--force", action="store_true")
        s.set_defaults(fn=fn)
    s = sub.add_parser("audit", help="set the state of each record, build the boards")
    s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("site", help="build the static site")
    s.add_argument("--base-url", default="http://localhost:8000", help="absolute URL of the site root")
    s.add_argument("--no-png", action="store_true", help="skip the card images")
    s.set_defaults(fn=cmd_site)
    s = sub.add_parser("video", help="render the share videos (MP4) that the built site links")
    s.add_argument("--force", action="store_true", help="render again even when the inputs did not change")
    s.add_argument("--only", help="comma-separated substrings of page paths, for example sahara,biggest-day")
    s.set_defaults(fn=cmd_video)
    s = sub.add_parser("verify", help="recompute one record from a fresh download of its package")
    s.add_argument("record_id")
    s.add_argument("--site", help="site directory or URL that holds r/<id>/record.json", default=None)
    s.add_argument("--keep", action="store_true", help="keep the downloaded files")
    s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("check", help="walk the built site: board data, record pages, cards and meta tags agree")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("all", help="clean, records, audit, site")
    s.add_argument("--pkgs")
    s.add_argument("--jobs", type=int, default=3)
    s.add_argument("--force", action="store_true")
    s.add_argument("--base-url", default="http://localhost:8000")
    s.add_argument("--no-png", action="store_true")
    s.set_defaults(fn=cmd_all)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
