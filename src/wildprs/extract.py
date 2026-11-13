"""Raw Movebank CSV (plain or zipped) -> fixes parquet, thinned to one fix per animal,
sensor and 5-minute bucket. The thinning keeps the first visible fix of each bucket."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.csv as pacsv

from .repo import LOCATION_SKIP_WORDS

BUCKET = "5m"
COLUMNS = {
    "individual-local-identifier": "ind", "tag-local-identifier": "tag",
    "individual-taxon-canonical-name": "taxon", "timestamp": "ts",
    "location-long": "lon", "location-lat": "lat", "sensor-type": "sensor", "visible": "vis",
    "manually-marked-outlier": "mmo", "algorithm-marked-outlier": "amo", "import-marked-outlier": "imo",
    "height-above-ellipsoid": "hae", "height-above-msl": "hmsl", "gps:hdop": "hdop",
    "gps:satellite-count": "sats", "argos:lc": "lc",
}


def _is_location_member(name: str) -> bool:
    n = name.lower()
    return n.endswith(".csv") and "reference" not in n and not any(w in n for w in LOCATION_SKIP_WORDS) \
        and not n.split("/")[-1].startswith("._")


def _reduce(batch: pa.RecordBatch, only_ind: str | None, stats: dict) -> pl.DataFrame:
    df = pl.from_arrow(pa.Table.from_batches([batch])).rename(COLUMNS)
    stats["rows"] += df.height
    df = df.with_columns(
        pl.coalesce([pl.col("ind"), pl.col("tag")]).alias("ind"),
        pl.col("ts").str.slice(0, 19).str.strptime(pl.Datetime("ms"), "%Y-%m-%d %H:%M:%S", strict=False).alias("t"),
        pl.col("lon").cast(pl.Float64, strict=False), pl.col("lat").cast(pl.Float64, strict=False),
        pl.col("hae").cast(pl.Float32, strict=False), pl.col("hmsl").cast(pl.Float32, strict=False),
        pl.col("hdop").cast(pl.Float32, strict=False), pl.col("sats").cast(pl.Float32, strict=False),
        (pl.col("vis").str.to_lowercase() != "false").fill_null(True).alias("vis"),
        ((pl.col("mmo").str.to_lowercase() == "true").fill_null(False).cast(pl.Int8)
         + (pl.col("amo").str.to_lowercase() == "true").fill_null(False).cast(pl.Int8) * 2
         + (pl.col("imo").str.to_lowercase() == "true").fill_null(False).cast(pl.Int8) * 4).alias("flags"),
        pl.col("sensor").fill_null("unknown"),
    ).drop(["tag", "ts", "mmo", "amo", "imo"])
    if only_ind is not None:
        df = df.filter(pl.col("ind") == only_ind)
    df = df.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null() & pl.col("t").is_not_null()
                   & (pl.col("lat").abs() <= 90) & (pl.col("lon").abs() <= 180)
                   & ~((pl.col("lat") == 0) & (pl.col("lon") == 0)))
    stats["loc_rows"] += df.height
    df = df.with_columns(pl.col("t").dt.truncate(BUCKET).alias("bk"))
    return (df.sort("vis", descending=True, maintain_order=True)
              .unique(["ind", "sensor", "bk"], keep="first", maintain_order=True))


def _open_location_files(raw: Path):
    for p in sorted(raw.iterdir()):
        n = p.name.lower()
        if "reference" in n or any(w in n for w in LOCATION_SKIP_WORDS):
            continue
        if n.endswith(".zip"):
            z = zipfile.ZipFile(p)
            for m in z.namelist():
                if _is_location_member(m):
                    yield m, io.BufferedReader(z.open(m), 1 << 20)
        elif n.endswith(".csv"):
            yield p.name, open(p, "rb")


def extract(raw: Path, out: Path, only_ind: str | None = None) -> dict:
    stats = {"rows": 0, "loc_rows": 0, "files": [], "skipped_files": []}
    parts = []
    for name, fh in _open_location_files(raw):
        with fh:
            header = fh.peek(4096)[:4096].split(b"\n")[0].decode("utf8", "replace")
            if "location-lat" not in header or "timestamp" not in header:
                stats["skipped_files"].append(name)
                continue
            stats["files"].append(name)
            reader = pacsv.open_csv(fh, read_options=pacsv.ReadOptions(block_size=64 << 20),
                                    convert_options=pacsv.ConvertOptions(
                                        include_columns=list(COLUMNS), include_missing_columns=True,
                                        column_types={k: pa.string() for k in COLUMNS}))
            for batch in reader:
                parts.append(_reduce(batch, only_ind, stats))
    stats["kept"] = 0
    if parts:
        df = pl.concat(parts, how="vertical_relaxed")
        df = (df.sort(["ind", "sensor", "t", "vis"], descending=[False, False, False, True])
                .unique(["ind", "sensor", "bk"], keep="first", maintain_order=True).drop("bk"))
        df = df.with_columns(pl.col("ind").cast(pl.Categorical), pl.col("sensor").cast(pl.Categorical),
                             pl.col("taxon").cast(pl.Categorical), pl.col("lc").cast(pl.Categorical))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        df.write_parquet(tmp, compression="zstd")
        tmp.replace(out)
        stats["kept"] = df.height
        stats["individuals"] = df["ind"].n_unique()
    out.with_suffix(".json").write_text(json.dumps(stats))
    return stats
