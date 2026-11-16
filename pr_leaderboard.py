"""
Puerto Rico VGC Leaderboard
===========================
Pulls the Play! Pokémon leaderboard from the public API at
api.play.pokemon.com and filters it down to the players in competitors.json,
replacing in-game nicknames with real names via each player's "aliases".

No browser, no Tampermonkey, no third-party packages (stdlib only).

Usage:
    python pr_leaderboard.py                       # NA masters VGC, championship points
    python pr_leaderboard.py -o pr.json            # write JSON instead of CSV
    python pr_leaderboard.py --all -o na_full.csv  # dump the whole leaderboard (names still alias-corrected)
    python pr_leaderboard.py --period <hash>       # a different season (see note below)
    python pr_leaderboard.py --html                # also write pr_leaderboard.html (screenshot-ready)

The HTML page can't call the API itself: the API sends no CORS headers, so
browsers refuse to let a page read it. Instead this script fetches the data
and embeds it into leaderboard_template.html.

Finding a period hash:
    The API has no endpoint that lists seasons. Open the leaderboard on
    play.pokemon.com, open DevTools -> Network, filter for "leaderboards",
    and copy the `period=` value from the request.

    In CI, put the competitors JSON in the COMPETITORS_JSON environment
    variable instead of a file, and use --quiet so names never reach the logs.

competitors.json format:
    {"players": [{"name": "Real Name", "aliases": ["Nickname", "Other Spelling"]}, ...]}
    Matching ignores case, accents and punctuation, so "maria lopez",
    "María López" and "Maria Lopez." all match the same entry.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

API_URL = "https://api.play.pokemon.com/services/spar/leaderboards/"
DEFAULT_PERIOD = "1c13d02f09f3f0713f7e41986bef2f36"
MAX_PAGE_SIZE = 300  # API rejects anything larger
HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "leaderboard_template.html"


# ─── Name matching ────────────────────────────────────────────────────────────

def normalize(name: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def load_competitors(path: Path) -> dict[str, str]:
    """Return {normalized name or alias: canonical name}.

    The COMPETITORS_JSON environment variable, if set, takes precedence over the file.
    """
    raw = os.environ.get("COMPETITORS_JSON") or path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"Competitors JSON is invalid: {e}")
    lookup: dict[str, str] = {}
    for p in data["players"]:
        canonical = p["name"]
        for variant in [canonical, *p.get("aliases", [])]:
            key = normalize(variant)
            if key in lookup and lookup[key] != canonical:
                sys.exit("Competitors JSON: a name/alias is listed under two different players"
                         if os.environ.get("COMPETITORS_JSON") else
                         f"competitors.json: '{variant}' is listed under both "
                         f"'{lookup[key]}' and '{canonical}'")
            lookup[key] = canonical
    return lookup


# ─── API ──────────────────────────────────────────────────────────────────────

def fetch_page(params: dict, page: int, retries: int = 3) -> dict:
    url = API_URL + "?" + urllib.parse.urlencode({**params, "page": page})
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "pr-leaderboard-script/1.0",
    })
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code < 500 or attempt == retries:
                sys.exit(f"API error {e.code} on page {page}: {body}")
        except urllib.error.URLError as e:
            if attempt == retries:
                sys.exit(f"Network error on page {page}: {e.reason}")
        time.sleep(2 * attempt)
    raise RuntimeError("unreachable")


def fetch_leaderboard(*, product, region, region_type, division, period,
                      point_type, delay) -> list[dict]:
    params = {
        "product": product,
        "region": region,
        "region_type": region_type,
        "division": division,
        "period": period,
        "point_type": point_type,
        "page_size": MAX_PAGE_SIZE,
        "sort_by": "ranking_order:asc",
    }
    first = fetch_page(params, 1)
    total = first.get("count", 0)
    pages = max(1, -(-total // MAX_PAGE_SIZE))
    results = list(first["results"])
    print(f"{total} players on the {region} {division} {product} leaderboard "
          f"({pages} pages)", file=sys.stderr)

    for page in range(2, pages + 1):
        time.sleep(delay)
        print(f"  page {page}/{pages}", file=sys.stderr, end="\r")
        results.extend(fetch_page(params, page)["results"])
    print(file=sys.stderr)

    if results:
        print(f"Points as of {results[0].get('calculation_date', '?')}", file=sys.stderr)
    return results


# ─── Processing ───────────────────────────────────────────────────────────────

def to_row(r: dict, name: str) -> dict:
    return {
        "name": name,
        "display_name": r["display_name"],
        "region_rank": r["rank"],
        r["primary_point_type"] + "_points": r["primary_point_total"],
        r["secondary_point_type"] + "_points": r["secondary_point_total"],
        "country": r.get("player_country", ""),
    }


def build_rows(results, lookup, include_all):
    rows, by_player = [], defaultdict(list)
    for r in results:
        canonical = lookup.get(normalize(r["display_name"]))
        if canonical is None and not include_all:
            continue
        rows.append(to_row(r, canonical or r["display_name"]))
        if canonical:
            by_player[canonical].append(r["display_name"])

    # Local rank with ties sharing a rank (1, 2, 2, 4 …), same as the API does.
    prev_rank, local_rank = None, 0
    for i, row in enumerate(rows, 1):
        if row["region_rank"] != prev_rank:
            local_rank, prev_rank = i, row["region_rank"]
        row["rank"] = local_rank

    found = set(by_player)
    dupes = {k: v for k, v in by_player.items() if len(v) > 1}
    return rows, found, dupes


# ─── Output ───────────────────────────────────────────────────────────────────

def print_table(rows):
    if not rows:
        print("No matching players.")
        return
    pts = [k for k in rows[0] if k.endswith("_points")]
    w = max(len(r["name"]) for r in rows) + 2
    wd = max(len(r["display_name"]) for r in rows) + 2
    head = f"{'#':>4}  {'Name':<{w}}{'Listed as':<{wd}}{'Region':>7}" + "".join(
        f"{p.split('_')[0].title():>14}" for p in pts)
    print(head)
    print("─" * len(head))
    for r in rows:
        listed = r["display_name"] if r["display_name"] != r["name"] else ""
        print(f"{r['rank']:>4}  {r['name']:<{w}}{listed:<{wd}}{r['region_rank']:>7}"
              + "".join(f"{r[p]:>14,}" for p in pts))


def save(rows, path: Path):
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        cols = ["rank", *[k for k in rows[0] if k != "rank"]] if rows else []
        with path.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows accents
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    print(f"Saved {len(rows)} rows -> {path}", file=sys.stderr)


def save_html(rows, meta: dict, path: Path):
    payload = json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # can't close the <script> early
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"Saved page -> {path.resolve()}", file=sys.stderr)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", default="vg", help="vg or tcg (default: vg)")
    ap.add_argument("--division", default="masters", help="masters, seniors, juniors (default: masters)")
    ap.add_argument("--region", default="NA", help="leaderboard region (default: NA)")
    ap.add_argument("--region-type", default="zone", help="(default: zone)")
    ap.add_argument("--period", default=DEFAULT_PERIOD, help="season hash from the site's API calls")
    ap.add_argument("--point-type", default="championship", help="championship or play (default: championship)")
    ap.add_argument("--competitors", type=Path, default=HERE / "competitors.json")
    ap.add_argument("--all", action="store_true", help="keep every player, not just competitors")
    ap.add_argument("-o", "--output", type=Path, default=Path("pr_leaderboard.csv"),
                    help=".csv or .json (default: pr_leaderboard.csv)")
    ap.add_argument("--no-save", action="store_true", help="only print the table")
    ap.add_argument("--html", nargs="?", const=Path("pr_leaderboard.html"), type=Path, metavar="PATH",
                    help="also write a styled HTML page (default: pr_leaderboard.html)")
    ap.add_argument("--quiet", action="store_true",
                    help="print counts only, never names (use in public CI logs)")
    ap.add_argument("--delay", type=float, default=0.3, help="seconds between page requests")
    args = ap.parse_args()

    lookup = load_competitors(args.competitors)
    results = fetch_leaderboard(product=args.product, region=args.region,
                                region_type=args.region_type, division=args.division,
                                period=args.period, point_type=args.point_type,
                                delay=args.delay)
    rows, found, dupes = build_rows(results, lookup, args.all)

    if not args.all and not args.quiet:
        print_table(rows)

    canon = sorted(set(lookup.values()), key=normalize)
    missing = [n for n in canon if n not in found]
    print(f"\n{len(found)}/{len(canon)} competitors on the leaderboard.", file=sys.stderr)
    if args.quiet:
        if dupes:
            print(f"WARNING: {len(dupes)} competitor(s) matched more than one leaderboard entry. "
                  "Run locally without --quiet to see which.", file=sys.stderr)
    else:
        if missing:
            print("Not found: " + ", ".join(missing), file=sys.stderr)
        for name, listed in dupes.items():
            print(f"WARNING: '{name}' matched {len(listed)} entries ({', '.join(listed)}). "
                  "Probably different people; check competitors.json.", file=sys.stderr)

    if not args.no_save:
        save(rows, args.output)
    if args.html:
        first = results[0] if results else {}
        save_html(rows, {
            "region": args.region, "division": args.division, "product": args.product,
            "primary": first.get("primary_point_type", args.point_type),
            "secondary": first.get("secondary_point_type", "play"),
            "calculation_date": first.get("calculation_date", ""),
            "total": len(results),
        }, args.html)


if __name__ == "__main__":
    main()
