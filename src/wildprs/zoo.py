from __future__ import annotations

import re
from pathlib import Path

SPECIES_JS = "zoo-species.js"
_DEF = re.compile(r'^\s*def\("([^"]+)"', re.M)


def model_taxa(static: Path) -> set[str]:
    return set(_DEF.findall((static / SPECIES_JS).read_text()))
