"""Record state. A label in labels.toml wins; otherwise the automatic rules decide. A record is
public only when its state is credible. The boards take credible records in rank order, one per
animal and at most STUDY_CAP per study in the top BOARD_SIZE."""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from functools import cache

import polars as pl

from . import config, geo, taxa
from .categories import BY_KEY, SEGMENT_BY_KEY, Category
from .model import BOARD_CLASS_TITLE, BoardClass, State, Taxon
from .repo import packages

BOARD_SIZE = 10
STUDY_CAP = 3
MIN_BOARD = 3
WALK_LIMIT = 400
MIN_AGL_M = 100


@dataclass(frozen=True)
class Verdict:
    state: State
    rule: str
    reason: str


@dataclass(frozen=True)
class Label:
    categories: tuple[str, ...]
    doi: str
    animal: str
    state: State
    reason: str

    def specificity(self) -> int:
        return (self.animal != "*") * 4 + (self.doi != "*") * 2 + ("*" not in self.categories)


@cache
def labels() -> tuple[Label, ...]:
    if not config.LABELS_FILE.exists():
        return ()
    raw = tomllib.loads(config.LABELS_FILE.read_text()).get("label", [])
    out = []
    for x in raw:
        cats = x.get("category", "*")
        cats = tuple(cats) if isinstance(cats, list) else (cats,)
        unknown = [c for c in cats if c != "*" and c not in BY_KEY]
        if unknown:
            raise ValueError(f"labels.toml: unknown category {unknown}")
        out.append(Label(cats, x.get("doi", "*"), x.get("animal", "*"), State(x["state"]), x["reason"]))
    return tuple(out)


def find_label(category: str, doi: str, ind: str) -> Label | None:
    hits = [x for x in labels() if ("*" in x.categories or category in x.categories)
            and x.doi in ("*", doi) and x.animal in ("*", ind)]
    return max(hits, key=Label.specificity) if hits else None


def _beyond(cat: Category, value: float, limit: float) -> bool:
    return value * cat.better > limit * cat.better


# Packages whose Argos positions are model output at a fixed interval, not fixes (from the package README).
# Speed and distance over hours or days then measure the model, not the animal.
MODELLED_ARGOS = {
    "doi:10.5441/001/1.hm5nk220": "Argos positions in this package are hourly estimates of a state-space model "
                                  "(R package crawl), not fixes (package README)",
}
SHORT_TERM = {"biggest-day", "biggest-week", "fastest-hour", "sea-crossing", *SEGMENT_BY_KEY}


def automatic(cat: Category, row: dict, tx: Taxon) -> Verdict:
    """Pure: the automatic rules for one candidate. Order: artefact rules, review rules, pass."""
    v = row["value"]
    if row.get("sensor") == "argos" and row.get("doi") in MODELLED_ARGOS and cat.key in SHORT_TERM:
        return Verdict(State.ARTEFACT, "modelled_positions", MODELLED_ARGOS[row["doi"]])
    if cat.artefact_beyond is not None and _beyond(cat, v, cat.artefact_beyond):
        return Verdict(State.ARTEFACT, "physical_limit", f"value beyond the physical limit {cat.artefact_beyond:g} {cat.unit}")
    if cat.unit == "deg" and abs(v) > 88:
        return Verdict(State.ARTEFACT, "pole", "position at a pole")
    if cat.key == "highest-flight":
        agl = row.get("agl_m")
        if agl is None:
            return Verdict(State.UNDER_REVIEW, "terrain_unknown", "terrain height not available")
        if agl < MIN_AGL_M:
            return Verdict(State.ARTEFACT, "below_terrain",
                           f"only {agl:.0f} m above the highest terrain within 3 km ({row['terrain_m']:.0f} m)")
    limit = cat.review_beyond.get(tx.group)
    if cat.care and limit is None:
        return Verdict(State.UNDER_REVIEW, "care_no_limit",
                       f"no automatic limit for group '{tx.group}' in a category that needs care")
    if limit is not None and _beyond(cat, v, limit):
        return Verdict(State.UNDER_REVIEW, "beyond_auto_limit",
                       f"beyond the automatic limit {limit:g} {cat.unit} for group '{tx.group}': needs a label")
    return Verdict(State.CREDIBLE, "auto_pass",
                   "passed the automatic rules (cleaning level L3, support, limits for the group)")


def verdict(cat: Category, row: dict, tx: Taxon) -> Verdict:
    lab = find_label(cat.key, row["doi"], row["ind"])
    if lab:
        return Verdict(lab.state, "label", lab.reason)
    return automatic(cat, row, tx)


# Words that name a kind of animal or a tag, not one animal.
NOT_A_NAME = {"animal", "bat", "bird", "tag", "test", "unknown", "male", "female", "adult", "juvenile", "chick",
              "stork", "gull", "tern", "goose", "duck", "eagle", "hawk", "kite", "vulture", "owl", "crane", "swan",
              "heron", "curlew", "seal", "whale", "shark", "turtle", "tortoise", "fox", "wolf", "deer", "elk",
              "moose", "bear", "cat", "dog", "hare", "buffalo", "elephant", "zebra"}


def display_name(ind: str, nick: str, common: str = "") -> str:
    """A given name for the animal, or "" when there is none. A word of the species name, a word from
    NOT_A_NAME, a token with digits (a tag or ring code) and a word glued to a code (Curlew-4163152,
    Ich-2_tu96) are not names."""
    species = set(re.findall(r"[a-z]+", common.lower()))

    def ok(word: str) -> bool:
        return word.lower() not in NOT_A_NAME | species
    if nick:
        words = [w for w in re.split(r"\s+", nick.strip()) if not re.search(r"\d", w)]
        name = " ".join(words)
        return name if words and all(re.fullmatch(r"[^\W\d_]+", w) for w in words) and ok(words[0]) and len(name) > 1 else ""
    m = re.match(r"^([A-Z][a-z]{2,}(?:[ -][A-Z][a-z]+)*)(?=$|[^A-Za-z0-9-])", ind or "")
    if not m:
        return ""
    name = m.group(1)
    return name if all(ok(w) for w in re.split(r"[ -]", name)) else ""


def record_id(category: str, doi: str, ind: str, label: str) -> str:
    """Stable: depends only on the data package, the animal and the category."""
    h = hashlib.sha1(f"{doi}|{ind}|{category}".encode()).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40].strip("-")
    return f"{category}-{slug}-{h}"


def _animals() -> dict[tuple[int, str], dict]:
    out = {}
    for p in config.CLEAN.glob("*.json"):
        pkg = int(p.name[:3])
        for ind, a in json.loads(p.read_text())["animals"].items():
            out[(pkg, ind)] = a
    return out


def _enrich(c: pl.DataFrame) -> pl.DataFrame:
    animals = _animals()
    pk = packages()
    rows = []
    for r in c.iter_rows(named=True):
        a = animals.get((r["pkg"], r["ind"]), {"taxon": "", "nick": ""})
        tx = taxa.taxon(a["taxon"], (pk[r["pkg"]].taxa or ("",))[0])
        p = pk[r["pkg"]]
        name = display_name(r["ind"], a["nick"], tx.common)
        rows.append({"taxon": tx.name, "common": tx.common, "cls": tx.cls, "group": str(tx.group),
                     "board_class": str(tx.board_class) if tx.board_class else "", "iucn": tx.iucn,
                     "name": name, "doi": p.doi, "study": p.study_id or p.doi,
                     "id": record_id(r["category"], p.doi, r["ind"], name or r["ind"])})
    return pl.concat([c, pl.DataFrame(rows)], how="horizontal")


def _terrain(row: dict) -> dict:
    ev = json.loads(row["evidence"])
    try:
        t = geo.terrain_max_m(ev["peak_lat"], ev["peak_lon"])
    except Exception:  # network or server error: the record stays under review
        return {}
    return {"terrain_m": t, "agl_m": row["value"] - t}


def run() -> dict:
    c = _enrich(pl.read_parquet(config.RECORDS / "candidates.parquet"))
    verdicts: dict[str, dict] = {}
    boards: dict[str, dict[str, list[str]]] = {}
    for key, cat in BY_KEY.items():
        g = c.filter(pl.col("category") == key).with_columns((pl.col("value") * cat.better).alias("_s")).sort(
            "_s", descending=True)
        boards[key] = {}
        for scope in [None, *BoardClass]:
            gs = g if scope is None else g.filter(pl.col("board_class") == str(scope))
            board, per_study = [], {}
            for row in gs.head(WALK_LIMIT).iter_rows(named=True):
                if len(board) >= BOARD_SIZE:
                    break
                if row["id"] not in verdicts:
                    extra = _terrain(row) if key == "highest-flight" else {}
                    row = row | extra
                    v = verdict(cat, row, taxa.taxon(row["taxon"]))
                    verdicts[row["id"]] = row | {"state": str(v.state), "state_rule": v.rule, "state_reason": v.reason}
                vr = verdicts[row["id"]]
                if vr["state"] != State.CREDIBLE or per_study.get(row["study"], 0) >= STUDY_CAP:
                    continue
                per_study[row["study"]] = per_study.get(row["study"], 0) + 1
                board.append(row["id"])
            if len(board) >= MIN_BOARD:
                boards[key]["all" if scope is None else str(scope)] = board
    n_comp = {(k, s): n for k, s, n in c.with_columns(pl.col("board_class")).group_by("category", "board_class")
              .len().iter_rows()}
    counts = {key: {"all": int(c.filter(pl.col("category") == key).height)} |
              {str(s): int(n_comp.get((key, str(s)), 0)) for s in BoardClass} for key in BY_KEY}
    config.AUDIT.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(list(verdicts.values()), infer_schema_length=None).drop("_s").write_parquet(config.AUDIT / "verdicts.parquet")
    config.atomic_write(config.AUDIT / "boards.json", json.dumps({"boards": boards, "competitors": counts}, indent=1))
    public = {i for b in boards.values() for ids in b.values() for i in ids}
    states = pl.DataFrame(list(verdicts.values()), infer_schema_length=None)["state"].value_counts().rows()
    return {"boards": sum(len(b) for b in boards.values()), "public_records": len(public),
            "walked": len(verdicts), "states": dict(states)}


BOARD_TITLES = {"all": "All species"} | {str(k): v for k, v in BOARD_CLASS_TITLE.items()}
