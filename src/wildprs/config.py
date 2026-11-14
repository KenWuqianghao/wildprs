import os
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("WILDPRS_DATA", APP / "data"))
SITE = Path(os.environ.get("WILDPRS_SITE", APP / "site"))

META = DATA / "meta"
CATALOG = META / "catalog.jsonl"
REF = META / "ref"
TAXA = META / "taxa_gbif.json"
RAW = DATA / "raw"
FIXES = DATA / "fixes"
CLEAN = DATA / "clean"
RECORDS = DATA / "records"
AUDIT = DATA / "audit"
GEO = DATA / "geo"
VERIFY = DATA / "verify"
ART = DATA / "art"
LOG = DATA / "log" / "progress.log"

LABELS_FILE = APP / "labels.toml"
NAMES_FILE = APP / "names.toml"
ART_FILE = APP / "art.toml"

REPO_API = "https://datarepository.movebank.org/server/api"
SOURCE_URL = "https://github.com/KenWuqianghao/wildprs"
# Generic client name: requests must not carry personal details.
USER_AGENT = "Mozilla/5.0 (compatible; wildprs/0.1; open-data research script)"

PIPELINE_VERSION = "1"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n")


def atomic_write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data.encode() if isinstance(data, str) else data)
    tmp.replace(path)
