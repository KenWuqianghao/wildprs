"""Domain shapes. External data is parsed into these at the boundary (repo, extract, taxa)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

R_EARTH_KM = 6371.0088


class State(StrEnum):
    CREDIBLE = "credible"
    UNDER_REVIEW = "under_review"
    ARTEFACT = "artefact"


class Group(StrEnum):
    """Speed group: sets the speed ceiling of the cleaning rules."""
    BIRD = "bird"
    BAT = "bat"
    LAND_MAMMAL = "land_mammal"
    MARINE = "marine"
    TURTLE = "turtle"
    OTHER = "other"


class BoardClass(StrEnum):
    BIRDS = "birds"
    LAND_MAMMALS = "land-mammals"
    MARINE = "marine-animals"
    BATS = "bats"
    REPTILES = "reptiles"


BOARD_CLASS_TITLE = {
    BoardClass.BIRDS: "Birds",
    BoardClass.LAND_MAMMALS: "Land mammals",
    BoardClass.MARINE: "Marine animals",
    BoardClass.BATS: "Bats",
    BoardClass.REPTILES: "Reptiles",
}

THREATENED = {"VU", "EN", "CR"}


@dataclass(frozen=True)
class RepoFile:
    name: str
    size: int
    url: str


@dataclass(frozen=True)
class Package:
    index: int
    uuid: str
    doi: str
    title: str
    citation: str
    licence: str
    study_id: str
    taxa: tuple[str, ...]
    files: tuple[RepoFile, ...]

    @property
    def is_cc0(self) -> bool:
        return "CC0" in self.licence

    @property
    def doi_url(self) -> str:
        return "https://doi.org/" + self.doi.removeprefix("doi:")


@dataclass(frozen=True)
class Taxon:
    name: str
    cls: str = ""
    order: str = ""
    family: str = ""
    common: str = ""
    iucn: str = ""

    @property
    def group(self) -> Group:
        if self.cls == "Aves":
            return Group.BIRD
        if self.cls == "Mammalia":
            if self.order == "Chiroptera":
                return Group.BAT
            if self.order == "Cetacea" or self.family in MARINE_MAMMAL_FAMILIES:
                return Group.MARINE
            return Group.LAND_MAMMAL
        if self.cls == "Testudines":
            return Group.TURTLE
        if self.cls in ("Elasmobranchii", "Actinopterygii"):
            return Group.MARINE
        return Group.OTHER

    @property
    def board_class(self) -> BoardClass | None:
        g = self.group
        if g is Group.BIRD:
            return BoardClass.BIRDS
        if g is Group.BAT:
            return BoardClass.BATS
        if g is Group.LAND_MAMMAL:
            return BoardClass.LAND_MAMMALS
        if g is Group.MARINE:
            return BoardClass.MARINE
        if self.cls in ("Testudines", "Squamata", "Crocodylia", "Reptilia"):
            return BoardClass.REPTILES
        return None

    @property
    def threatened(self) -> bool:
        return self.iucn in THREATENED


MARINE_MAMMAL_FAMILIES = {"Phocidae", "Otariidae", "Odobenidae", "Trichechidae", "Dugongidae"}


@dataclass(frozen=True)
class Animal:
    pkg: int
    ind: str
    taxon: Taxon
    nick: str = ""


@dataclass
class Track:
    """Cleaned fixes of one animal from one sensor class, sorted by time.
    brk[i] is True when the leg from fix i to fix i+1 is not evidence of movement
    (the cleaning split the track there)."""
    animal: Animal
    sensor: str
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    h: np.ndarray
    brk: np.ndarray
    n_input: int = 0
    _legs: np.ndarray | None = field(default=None, repr=False)

    def __len__(self) -> int:
        return len(self.t)

    @property
    def legs_km(self) -> np.ndarray:
        if self._legs is None:
            self._legs = haversine_km(self.lat[:-1], self.lon[:-1], self.lat[1:], self.lon[1:])
        return self._legs

    @property
    def dt(self) -> np.ndarray:
        return np.diff(self.t)

    @property
    def median_interval_s(self) -> float:
        return float(np.median(self.dt)) if len(self.t) > 1 else 0.0


@dataclass(frozen=True)
class Effort:
    """One performance of one track in one category. i0..i1 index the track's fixes."""
    category: str
    value: float
    i0: int
    i1: int
    support: int
    evidence: dict = field(default_factory=dict)


class ArtKind(StrEnum):
    PHOTO = "photo"
    SILHOUETTE = "silhouette"


@dataclass(frozen=True)
class ArtFile:
    """One image file under data/art. crop: "wide" (3:2), "square" (1:1) or "full" (a silhouette)."""
    path: str
    crop: str
    width: int
    height: int


@dataclass(frozen=True)
class ArtEntry:
    """One image of a taxon with its licence facts, as data/art/manifest.json holds it."""
    taxon: str
    kind: ArtKind
    files: tuple[ArtFile, ...]
    licence: str
    licence_url: str
    creator: str
    source_url: str
    shows: str
    change: str


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
