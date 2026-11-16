# Puerto Rico VGC Leaderboard

Daily-updated ranking of Puerto Rican players on the Play! Pokémon VGC leaderboard,
built from the public `api.play.pokemon.com` leaderboard API.

## Local use

```
python pr_leaderboard.py --html     # writes pr_leaderboard.csv and pr_leaderboard.html
```

Needs Python 3.9+ and nothing else. Players and their nickname aliases live in
`competitors.json` (git-ignored):

```json
{"players": [{"name": "Real Name", "aliases": ["Nickname"]}]}
```

## GitHub Pages setup

1. **Settings → Pages → Source:** GitHub Actions.
2. **Settings → Secrets and variables → Actions → Secrets:** add `COMPETITORS_JSON`
   with the contents of `competitors.json`.
3. *(When a new season starts)* **Variables:** add `LEADERBOARD_PERIOD` with the
   season's `period=` value (from the leaderboard's network requests in DevTools).
4. **Actions → Update leaderboard → Run workflow** to publish right away. After that
   it runs daily at 11:00 UTC.

The workflow runs with `--quiet`, so names never appear in the (public) Actions logs.
Players who match the leaderboard are, of course, visible on the published page.

GitHub pauses scheduled workflows after 60 days without repository activity; re-enable
from the Actions tab if that happens.
