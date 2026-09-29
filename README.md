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
- `intervals_seconds`: polling speed (`performance`: how often live scorecards are read, default 3600 = hourly)
- `team_queries`, `extra_rss_feeds`: news sources
- `news_search_urls`: news search engines run for every query (Google News and Bing News; `{q}` is the query)
- `official_feeds`: feeds whose every post is about CSK (CSK's YouTube channel), tagged "CSK (official)".
  X/Twitter and Instagram have no free feed; their posts arrive once news sites report them.
- `roster`: fallback squad (Cricbuzz profile ids). The live squad is refreshed daily from Cricbuzz's CSK team page
  plus every CSK player in the latest started IPL season's match squads (the team page misses some, e.g. Aman Khan
  and replacement signings). Someone who drops off the team page is treated as released. A player released at
  retention time who was never on the team page stays tracked until the next IPL season's first match.

## How match detection works
Matches come from Cricbuzz's schedule page (every international, domestic, T20 league and women's match for the
next ~5 days) plus its live-scores page, which gives each match's `state` (Preview / In Progress / Stumps /
Complete...). For every match today or tomorrow (IST dates), and every live one, the tracker reads the match's
Cricbuzz squad page and looks for CSK players by profile id, so any team or league works. If a squad isn't published
yet, it uses the previous match of the same series and marks the player "expected".
- **Morning digest** (`alert.match_digest_hour`, default 8 AM IST): one message listing CSK players' matches TODAY and
  TOMORROW: player, team, opponent, format, start time. A silent one-liner if there are none.
- **NEW MATCH**: a match found after the digest (or one starting before it) is sent straight away, once.
- **PLAYING NOW**: once per match, only when it is live and the player is in the XI (or squad, before the XI is out).
- **Matches only the news mentions** (practice games, trials): a story saying a CSK player "will play / set to play /
  to feature" with a day ("today", "Tuesday", "30 September", counted from the publish date) goes into the digest as
  "Match per news, not an official fixture", with the headline. If the headline and summary give no day, the article
  itself is read (not possible for Google News links). "Two-day" etc. matches are listed on each day. Articles get
  updated, so each one is re-read every 3 hours until its match is over: if the day changed or the match is gone
  and it was already announced, a "✏️ CORRECTION" alert is sent.
- **Corrections**: a player announced as "expected" (from the previous match's squad) who is missing when his team's
  squad is published gets a "✏️ CORRECTION" alert.
- **Player performance** (📊): for every match we told you a CSK player is in, the Cricbuzz scorecard is read every hour
  while it's on (`intervals_seconds.performance`) and a LIVE UPDATE is sent when his figures change; when it ends, a
  FINAL summary: batting (runs, balls, fours, sixes, how out), bowling (overs, maidens, runs, wickets) and fielding
  (catches, stumpings, run outs). The dashboard shows the latest figures under the player's name.
All times are IST.

## Telegram delivery
Messages go out at most one per second (Telegram's limit). If Telegram says "too many requests" the tracker waits
and retries; if it can't deliver (network down, Telegram error), the message waits in `state.db` and is retried on
the next run for up to a day. Messages over Telegram's 4,096-character limit are split into parts.

## Tests
`python test_offline.py` runs the regression suite (106 tests, offline, fixed clock, real Cricbuzz / Google News /
Bing samples): news, roster, matches, performance, Telegram messages, dashboard, config and workflows. GitHub runs
it on every push (`.github/workflows/tests.yml`); a red ✗ on the commit means something broke.

## Known limits
- Live match states confirmed on 29 Sep 2026 ("In Progress"). An unrecognised state is printed as a `[warn]` and never
  shown as PLAYING NOW; add it to `MATCH_STATES` in tracker.py.
- Clickbait-style headlines ("WATCH: ...", "jaw-dropping catch") are kept, because they are often a player's standout
  moment; other publishers' copies of the same moment are dropped by the grouping below.
- News relevance is keyword-based: passing mentions ("X breaks Dhoni's record") are dropped, but some fluff gets through.
- Missing a story is treated as worse than a wrong tag: a player is also matched by surname or first name alone
  ("Ellis, Davies ruled out", "Ruturaj's gain"). A headline that spells out another cricketer's full name
  ("Kuldeep Yadav", "KL Rahul") isn't counted for our player: those names come from `namesakes` in config.json plus
  every Cricbuzz squad the tracker reads. Some wrong tags remain, mostly non-cricket "Khan" stories.
- Duplicates: a near-identical headline is dropped, and the same story told by other publishers in other words is
  grouped by player and kind of event (injury, selection, performance, trade, captaincy, coach, retirement): once a
  story about a player is sent, further reports of the same kind within 24 hours are neither sent nor shown on the
  dashboard, unless they rank higher ("injury doubt" -> "ruled out"). CSK team stories about someone else (a new
  coach, a trade target: "Mohit Sharma to join CSK as bowling coach") are grouped by that person the same way.
  Player stories of no recognised kind and team stories about no one in particular are never grouped, so a few
  repeats of those can still get through.
- Match alerts show recent injury headlines (last 4 days) under a player who isn't confirmed in the XI yet, marked
  ⚠️. The player is never removed because of news; Cricbuzz's squad and playing XI decide.
- If Cricbuzz changes its page format you'll see `[warn] no match data...` or a roster-source warning.
