# Wild PRs

Wild PRs is a static site with athletic records of individual wild animals. Each record comes from open tracking data in the Movebank Data Repository (CC0 packages only). Each record page has a recipe, and any person can recompute the record with `wildprs verify`.

## Requirements

- Linux or macOS, Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/).
- Headless Chromium for the share images. `wildprs` uses the first `~/.cache/ms-playwright/chromium_headless_shell-*` it finds. Set `WILDPRS_CHROMIUM` to use another binary, or run `uv run playwright install chromium-headless-shell`.
- About 30 GB of free disk during `fetch`. Raw downloads are deleted after extraction. The extracted fixes use about 0.6 GB.

Data goes to `data/` (set `WILDPRS_DATA` to change it). The site goes to `site/` (set `WILDPRS_SITE`).

## Rebuild everything from zero

Run the commands in the repository root. Each stage is idempotent: a second run does only the missing work. Use `--force` to redo a stage. The durations are measured on an 8-thread machine, except where marked.

| Step | Command | Duration |
|---|---|---|
| 1. Install | `uv sync` | 1 min |
| 2. Catalog and taxa | `uv run wildprs catalog --refresh` | 5 to 10 min (not measured from zero) |
| 3. Download and extract | `uv run wildprs fetch --jobs 3` | 2 to 6 hours, limited by the server (not measured from zero) |
| 4. Clean | `uv run wildprs clean --jobs 3` | 1 to 2 min |
| 5. Records | `uv run wildprs records --jobs 3` | 1 to 2 min |
| 6. Audit | `uv run wildprs audit` | 1 min (terrain look-ups are cached in `data/geo/terrain_cache.json`) |
| 7. Animal photos | `uv run wildprs art` | 5 to 10 min from zero; seconds when nothing changed |
| 8. Site | `uv run wildprs site --base-url https://example.org` | 2 to 5 min: 40 s for the pages, the rest for the share images (`--no-png` skips them) |
| 9. Videos | `uv run wildprs video` | 30 to 60 s per video, 27 videos; only new or changed videos are rendered |
| 10. Check | `uv run wildprs check` | 15 s |
| 11. Tests | `uv run pytest` | 2 s |

`uv run wildprs all --base-url https://example.org` runs steps 4 to 6, 8 and 9. Step 7 needs network access, so `all` does not run it. Natural Earth files download on first use.

`fetch` uses at most 3 parallel downloads, HTTP range requests to resume, exponential backoff, and a generic User-Agent. Packages that still fail are listed in `data/meta/not_covered.json`. Their partial raw files stay in `data/raw`, so the next `fetch` continues them. Run `fetch` again to retry them. The extraction of the largest packages (104 and 112) needs about 5 GB of memory.

To look at the site, serve it with any static server, for example `python3 -m http.server -d site 8000`.

## Motion and videos

Each record page has a replay on its course map: the route draws itself with a mark at every round distance step, the animal's 3D model follows the route seen from above, the big number counts up to the record, and a readout shows time, distance, speed and (for birds and bats) height. The chart under the map is tied to the replay: drag or tap it to move the replay. The replay starts when it scrolls into view. With `prefers-reduced-motion` it does not start by itself and the model holds one pose. Without JavaScript the page shows the whole route and the final numbers.

Board pages of the categories in `LANE_RACES` (`site.py`) have a lane race of the top 8: each animal runs in the lane of its rank, at its distance at the race clock. Segment boards have a race of the top 10 on one shared map between the two gates. The other boards (round trip, farthest north and south, most countries, highest flight) have no race.

`wildprs video [--only <substring,...>] [--force]` renders the MP4 files that the built site links: `r/<id>/replay.mp4` for the number one of every all-species board, and `b/<category>/race.mp4` for each all-species board with a race. A replay video ends on the result card of the record. The command opens each page with `?video=1` in headless Chromium, sets the animation time of each frame (30 fps, 1280x720), and encodes with `ffmpeg` (`libx264`, `yuv420p`, no audio). It needs `ffmpeg` on the `PATH`. Each frame is a function of its time only, so two renders give the same frames. Videos are kept in `data/video` under a hash of their inputs, so `wildprs site` puts unchanged videos back after a rebuild.

### Motion files in the site tree

| Path | Content |
|---|---|
| `r/<id>/track.json` | The replay track: up to 360 points with `t` (seconds from `t_start`), `lon`, `lat`, `km` (distance along the track), `kmh`, `m` (the record's own measure so far), `h` (height, birds and bats only), `eff` (first and last index of the record itself), `t_zero` (start of the record clock), `brk` (legs that are not movement), `night` (spans with the sun below the horizon, windows of 5 days or less), `view` (frame of the course map) |
| `b/<category>/[<class>/]race.json` | The race: `kind` (`map` or `lanes`), `finish` (seconds), `gates` (`map`) or `dmax` (`lanes`, the distance of the finish line), and per runner `rank`, `name`, `bib` (tag id), `taxon` (the 3D model), `band` (group colour), `display` (final mark), `t`, `d` (raced measure), `finish`, and `x`, `y` (`map` only, map units) |
| `r/<id>/replay.mp4`, `b/<category>/race.mp4` | Share videos |

For a sensitive record, `track.json` has positions on the 0.1° grid and times on a 15-minute grid, and `km` and `kmh` follow from those points only. `wildprs check` recomputes them.

## Share images

Each record has three share images, each a 1200 x 675 PNG beside its record page. `SHARE_FORMATS` in `model.py` lists them:

| Key | Label | File | Look |
|---|---|---|---|
| `track` | Track | `card.png` | One lane of the track with the animal, its bib and the mark. This file is also the Open Graph and Twitter image of the page |
| `collector` | Collector card | `card-collector.png` | A trading card on a green table, with the animal reaching out of the card. The frame is gold for the first of all animals, silver for the first of a group, and plain for the other ranks |
| `ascii` | Scoreboard | `card-ascii.png` | Amber characters on black, with the animal drawn in characters |

`wildprs site` writes one poster page for each record and format, renders the pages with 4 headless browsers at the same time, and then deletes the pages. A poster holds its 3D model in one pose, so two builds give the same images. The fonts, styles and scripts of the collector card and the scoreboard are in `src/wildprs/site/share/`. They are in the site tree only during the build, and no page of the site loads them. `wildprs check` fails when a record does not have each of the three files at 1200 x 675.

To add a format, add a `ShareFormat` to `SHARE_FORMATS` and a function for its poster page to `POSTERS` in `share.py`. The record page, the render step and the check read the same list.

## Animal photos

A taxon on the site can have a photo from [Wikimedia Commons](https://commons.wikimedia.org). The record page shows it with its credit. `art.toml` names the files, one table per taxon (the key is the taxon name in `names.toml`). A taxon with no table has no photo.

| Field | Required | Content |
|---|---|---|
| `photo` | yes | Commons file title, for example `File:Ciconia ciconia - 01.jpg` |
| `focus` | no | `[x, y]`, 0 to 1: the point that the crops keep in the frame. Default `[0.5, 0.5]` |
| `photo_taxon` | no | The taxon the photo shows, when the name on Commons differs from the key |

`wildprs art [--only <taxon,...>] [--force]` reads `art.toml`, fetches each file with its licence facts, and writes `data/art/`:

- `photo/<taxon>-<hash>-wide-{480,960,1600}.webp` (3:2) and `-square-{160,400,800}.webp` (1:1), cropped around `focus`.
- `manifest.json`: per photo the taxon, files, licence name and URL, creator (plain text), source page, the taxon shown and the change made.

The stage accepts only these licences, read from the API response: CC0, public domain, CC BY and CC BY-SA (any version). Any other licence, a missing licence or a photo under 1600 px stops the stage with an error that names the taxon. The hash in a file name comes from the `art.toml` fields of that photo, so a changed entry is fetched again. `wildprs site` reads only the manifest. The photo files are not in the repository. `/credits/` on the site lists every photo with its creator, licence and source. `wildprs check` fails when a page shows a photo file that does not exist or that has no entry on `/credits/`. The code is MIT; each photo keeps its own licence.

## 3D models

Every animal on the site is a low-poly 3D model that the browser draws on a canvas. The models are code, not image files: `src/wildprs/site/static/zoo.js` is the engine, and `zoo-species.js` is the table of species, with one `def("<taxon>", <base>, { ... })` entry for each taxon. An entry gives the proportions and colours of the animal as changes to a base body plan (soaring bird, flapping bird, dog, hoofed animal, whale, seal, sea turtle, tortoise, bat, ray, penguin). The proportions and colours are read from reference photos and are approximate.

To add a species:

1. Add its curated name to `names.toml`.
2. Add a `def(...)` entry to `zoo-species.js`. Start from the entry of a similar animal. The key is the taxon name.
3. Run `wildprs site --no-png` and look at the animal on a board page and on its record page.

`wildprs site` stops with a message that names the taxon when a taxon on a public board has no entry, and `wildprs check` reports the same fault in a built site.

## Check a record

```
uv run wildprs verify <record-id>
uv run wildprs verify <record-id> --site https://example.org
```

The command reads the recipe in `r/<record-id>/record.json`, finds the DOI in the repository, downloads the package again into `data/verify/`, extracts only that animal, applies the cleaning rules and the category rule, and prints `MATCH` or `MISMATCH`. The exit code is 0 for a match.

## Review records

`labels.toml` holds the review decisions, each with its evidence in the reason. A speed record of a bird gets `credible` only when the implied airspeed is in range: the NCEP/NCAR reanalysis tailwind along the track gives the airspeed, and its mean equivalent airspeed must be at most the species value in Alerstam et al. 2007 (PLoS Biol 5:e197, Protocol S1) plus 4 m/s. Without a GPS height, the check must pass at 925, 850 and 700 hPa. A label wins over the automatic rules. Each label names a category (or a list, or `*`), a DOI (or `*`), an animal id (or `*`), a state (`credible`, `under_review` or `artefact`) and a reason. Run `wildprs audit` and `wildprs site` after a change.

`names.toml` holds the curated common names. `wildprs site` stops if a taxon on a public board has no entry.

## Add a category

1. Write a pure function `Track -> list[Effort]` in `src/wildprs/categories.py`. Return the best effort of the track, or an empty list.
2. Add a `Category(...)` entry to `CATEGORIES` with the key, title, one-line rule, unit, direction (`better`), sensors, hook template, profile kind, and the review and artefact limits.
3. Add one test with a synthetic track and a known answer in `tests/test_categories.py`.
4. Run `wildprs records --force`, `wildprs audit` and `wildprs site`.

Hook templates can use `{value}`, `{unit}`, `{duration}`, `{start_place}`, `{end_place}`, `{start_where}`, `{end_where}`, `{segment}` and `{agl}`.

## Add a segment

1. Add a `Segment(key, title, gate_a, gate_b, span)` to `SEGMENTS` in `src/wildprs/segments.py`. A gate is a line `((lon1, lat1), (lon2, lat2))`. A passage counts in both directions.
2. Set `care=True` if the automatic limit for birds must apply.
3. Run `wildprs records --force`, `wildprs audit` and `wildprs site`.

## Layout

| Path | Content |
|---|---|
| `src/wildprs/model.py` | Data shapes: `Package`, `Taxon`, `Animal`, `Track`, `Effort`, `State`, `ArtEntry`, `ShareFormat` |
| `src/wildprs/repo.py` | Repository client: catalog, DOI look-up, resumable download |
| `src/wildprs/extract.py` | Raw CSV to thinned fixes (one fix per animal, sensor and 5 minutes) |
| `src/wildprs/clean.py` | Cleaning rule registry |
| `src/wildprs/categories.py`, `segments.py` | Category and segment registries |
| `src/wildprs/pipeline.py` | Stages clean and records, duplicate-animal merge |
| `src/wildprs/audit.py` | Record states, labels, boards |
| `src/wildprs/art.py` | Animal photos, licence check |
| `src/wildprs/site.py`, `cartography.py` | Static site, maps |
| `src/wildprs/share.py`, `render.py`, `site/share/` | Share images: poster pages, rendering, build-time fonts, styles and scripts |
| `src/wildprs/zoo.py`, `site/static/zoo.js`, `zoo-species.js`, `models.js` | 3D animal models: the list of taxa with a model, the engine, the species table, the script that puts a model on a page |
| `src/wildprs/replay.py`, `site/static/motion.js` | Track and race files; replay, race and card animation |
| `src/wildprs/video.py` | Share videos |
| `src/wildprs/verify.py`, `sitecheck.py` | `verify` and `check` |
