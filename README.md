# Wild PRs

Wild PRs is a static site with athletic records of individual wild animals. Each record comes from open tracking data in the Movebank Data Repository (CC0 packages only). Each record page has a recipe, and any person can recompute the record with `wildprs verify`.

## Requirements

- Linux or macOS, Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/).
- Headless Chromium for the card images. `wildprs` uses the first `~/.cache/ms-playwright/chromium_headless_shell-*` it finds. Set `WILDPRS_CHROMIUM` to use another binary, or run `uv run playwright install chromium-headless-shell`.
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
| 7. Site | `uv run wildprs site --base-url https://example.org` | 3 to 8 min, most of it card images |
| 8. Videos | `uv run wildprs video` | about 30 s per video, 22 videos; only new or changed videos are rendered |
| 9. Check | `uv run wildprs check` | 15 s |
| 10. Tests | `uv run pytest` | 2 s |

`uv run wildprs all --base-url https://example.org` runs steps 4 to 8. Natural Earth files download on first use.

`fetch` uses at most 3 parallel downloads, HTTP range requests to resume, exponential backoff, and a generic User-Agent. Packages that still fail are listed in `data/meta/not_covered.json`. Their partial raw files stay in `data/raw`, so the next `fetch` continues them. Run `fetch` again to retry them. The extraction of the largest packages (104 and 112) needs about 5 GB of memory.

To look at the site, serve it with any static server, for example `python3 -m http.server -d site 8000`.

## Motion and videos

Each record page has a replay: the route draws itself, the big number counts up to the record, and a readout shows time, distance, speed and (for birds and bats) height. The chart under the poster is tied to the replay: drag or tap it to move the replay. The replay starts when it scrolls into view. With `prefers-reduced-motion` it does not start by itself, and without JavaScript the page shows the static poster and the final numbers.

Board pages of the categories in `CHART_RACES` (`site.py`) have a distance-against-time race of the top 10. Segment boards have a race on one shared map between the two gates. The other boards (round trip, farthest north and south, most countries, highest flight) have no race. On the home page each card draws its route when it comes into view.

`wildprs video [--only <substring,...>] [--force]` renders the MP4 files that the built site links: `r/<id>/replay.mp4` for the number one of every all-species board, and `b/<segment>/race.mp4` for each segment race. The command opens each page with `?video=1` in headless Chromium, sets the animation time of each frame (30 fps, 1280x720), and encodes with `ffmpeg` (`libx264`, `yuv420p`, no audio). It needs `ffmpeg` on the `PATH`. Videos are kept in `data/video` under a hash of their inputs, so `wildprs site` puts unchanged videos back after a rebuild.

### Motion files in the site tree

| Path | Content |
|---|---|
| `r/<id>/track.json` | The replay track: up to 360 points with `t` (seconds from `t_start`), `lon`, `lat`, `km` (distance along the track), `kmh`, `m` (the record's own measure so far), `h` (height, birds and bats only), `eff` (first and last index of the record itself), `t_zero` (start of the record clock), `brk` (legs that are not movement), `night` (spans with the sun below the horizon, windows of 5 days or less), `view` (map frame of the poster) |
| `b/<category>/[<class>/]race.json` | The race: `kind` (`map` or `chart`), `finish` (seconds), and per runner `t`, `x`, `y` (map or thumbnail units), `d` (raced measure), `finish` |
| `r/<id>/card-bare.jpg` | The card without its route, for the home page animation |
| `r/<id>/replay.mp4`, `b/<segment>/race.mp4` | Share videos |

For a sensitive record, `track.json` has positions on the 0.1° grid and times on a 15-minute grid, and `km` and `kmh` follow from those points only. `wildprs check` recomputes them.

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
| `src/wildprs/model.py` | Data shapes: `Package`, `Taxon`, `Animal`, `Track`, `Effort`, `State` |
| `src/wildprs/repo.py` | Repository client: catalog, DOI look-up, resumable download |
| `src/wildprs/extract.py` | Raw CSV to thinned fixes (one fix per animal, sensor and 5 minutes) |
| `src/wildprs/clean.py` | Cleaning rule registry |
| `src/wildprs/categories.py`, `segments.py` | Category and segment registries |
| `src/wildprs/pipeline.py` | Stages clean and records, duplicate-animal merge |
| `src/wildprs/audit.py` | Record states, labels, boards |
| `src/wildprs/site.py`, `cartography.py`, `render.py` | Static site, maps, card images |
| `src/wildprs/replay.py`, `site/static/motion.js` | Track and race files; replay, race and card animation |
| `src/wildprs/video.py` | Share videos |
| `src/wildprs/verify.py`, `sitecheck.py` | `verify` and `check` |
