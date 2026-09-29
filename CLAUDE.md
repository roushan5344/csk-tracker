# CSK Tracker – notes for Claude

Tracks CSK players and MS Dhoni: Telegram alerts (news, matches, live scores) plus an HTML dashboard. Runs every
10 min on GitHub Actions (`.github/workflows/tracker.yml`), which also publishes the dashboard to GitHub Pages.
README.md is the user guide; this file is the why.

## Files
- `tracker.py` – everything. Sections: helpers, alerts, news, roster, match day, player performance, dashboard, main loop.
- `config.json` – sources, intervals, fallback roster, `namesakes`. `test_offline.py` – offline regression suite.
- `state.db` (SQLite) and `dashboard.html` – git-ignored, written each run; Actions keeps `state.db` in its cache.

## How a run works (`run_cycle`)
flush outbox → roster refresh + prune (daily) → `set_other_names` → news (`fetch_news` → `process_items`) →
`check_matches` → `poll_performances` → `write_dashboard` → `quiet_check_in`. Tables in `db_connect`:
seen (news already handled) · squad (live roster) · kv (last-run times, announced flags) · match_squads · news_matches
(matches only the news mentions) · news_checked · story_events (same story from other publishers) · outbox (unsent
Telegram) · news_feed (dashboard news) · series_cache · tracked_matches (matches we alerted on) · performances.

## Rules
- Standard library only, keep it to these few files; ask before installing anything.
- Ask before any code change, revert, commit or push. Feedback or a preference is not a go-ahead.
- Never put the bot token or chat ID in a file: they come from env vars / GitHub Secrets.
- Try changes without sending: `python tracker.py --once --dry-run --no-warmup`.
- Run `python test_offline.py` before every push; every fix gets a test built from the real example that broke.
- Commit with the GitHub noreply address set in the repo's local git config.

## Design decisions
- Recall over precision: a missed story is worse than a wrong or duplicate one. Surname alone matches; first names
  and nicknames (`ALIASES`) only in cricket headlines; a hit inside another cricketer's full name is rejected.
- News never removes a player from a match alert; injury news only adds a ⚠️ flag. Report headlines as written.
- Only a live match state (`MATCH_STATES`) means PLAYING NOW; unknown states are warned about, never shown as live.
- All times are IST. The dashboard shows the same stories as Telegram (last 24 h, from `news_feed`).

## Gotchas
- Cricbuzz is Next.js: page data is in `self.__next_f.push` chunks (`next_data` helper), not plain HTML.
- Cricinfo returns random 403s, retried with `BROWSER_UA`. Google News links can't be opened to read the article.
- Workflow YAML: never put `${{ }}` inside a `{...}` flow mapping (broke the run on 29 Sep 2026; tested).
- The anonymous GitHub API allows 60 calls an hour: check a run's status once, don't poll it in a loop.
