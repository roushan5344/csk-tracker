# CSK Tracker – how to run

## 1. Telegram (5 min)
1. In Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.

## 2. Try it on your laptop
    export TELEGRAM_BOT_TOKEN=...   TELEGRAM_CHAT_ID=...
    python tracker.py --once --dry-run   # prints alerts, sends nothing
    python tracker.py --once             # real run (first run only records existing stories, no alert flood)
    python tracker.py                    # keep running (Ctrl+C to stop)
Open `dashboard.html` in your browser (auto-refreshes every 2 min).

## 3. Keep it running 24/7 (free)
**GitHub Actions:** put this folder in a GitHub repo (public repos get unlimited free Actions minutes; a private repo's free quota is only 2,000 min/month, so change the cron to every 30 min if private), add secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
(Settings → Secrets → Actions). `.github/workflows/tracker.yml` runs it every 10 min. Alerts are up to ~10-15 min late,
and there is no live dashboard (run it locally for that). Each run checks matches; CSK/Dhoni news runs every run,
player news about every 20 min and the roster once a day (last-run times are kept in `state.db`).
**Always-on machine (Pi / small VPS):** `nohup python tracker.py &` or a systemd service. Near-real-time (news every 10 min, matches every 90 s).

## 4. Customise (`config.json`)
- `muted_players`: names to ignore   - `alert.min_importance`: minor / important / breaking
- `alert.quiet_update_hours`: after this many hours with no alerts, send one silent "No new updates" message (default 3)
- `intervals_seconds`: polling speed  - `team_queries`, `extra_rss_feeds`: news sources
- `roster`: fallback squad (Cricbuzz profile ids). The live squad is refreshed daily from Cricbuzz's CSK team page
  plus every CSK player in the latest started IPL season's match squads (the team page misses some, e.g. Aman Khan
  and replacement signings). Someone who drops off the team page is treated as released. A player released at
  retention time who was never on the team page stays tracked until the next IPL season's first match.

## How match detection works
The Cricbuzz live-scores page embeds each match's `state` (Preview / Upcoming / Toss / In Progress / Stumps /
Complete / Abandon...) and start time. For every match that is live, paused (Stumps), or starts within 24 h, the
tracker reads that match's Cricbuzz squad page and looks for CSK players by profile id, so it works for any
national, A, U19 or league team. Before the toss it says "MATCH TODAY" (in squad); **PLAYING NOW** only appears when
the match state is live and the player is in the playing XI (or in the squad if Cricbuzz hasn't published the XI yet).
All times are IST. Run `python test_offline.py` after changes; it uses real Cricbuzz/Google News samples.

## Known limits
- Live in-play state names ("In Progress", "Innings Break", "Tea"...) follow Cricbuzz's usual wording but had not been
  seen live when this was written (no match was live). An unrecognised state is printed as a `[warn]` and never shown
  as PLAYING NOW; add it to `MATCH_STATES` in tracker.py.
- News relevance is keyword-based: passing mentions ("X breaks Dhoni's record") are dropped, but some fluff gets through.
- If Cricbuzz changes its page format you'll see `[warn] no match data...` or a roster-source warning.
