#!/usr/bin/env python3
"""CSK + Dhoni news and match tracker. Standard library only.

Usage:
  python tracker.py            # run forever (poll loop)
  python tracker.py --once     # one cycle, then exit (good for cron / GitHub Actions)
  python tracker.py --dry-run  # print alerts instead of sending them
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable Telegram alerts.
"""
import argparse, hashlib, html, json, os, re, sqlite3, sys, time
import urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (compatible; CSKTracker/1.0)"
RANKS = {"minor": 0, "important": 1, "breaking": 2}

BREAKING = (r"\b(retire[sd]?|retirement|ruled out|steps? down|sacked|released|retained|traded|signs|signed|appointed"
            r"|(new|named|as) (captain|skipper|vice-captain)|ban|banned|suspended|suspension|replacement|joins (csk|chennai)"
            r"|appointment|(as|new|head)\s([\w ]{0,25}\s)?coach(es)?)\b")    # "as Chennai Super Kings coach"
IMPORTANT = (r"\b(injur\w*|fitness|strain|niggle|side issue|hamstring|scans?|surgery|return\w*|comeback|back from|selected"
             r"|goes down|went down|limp\w* off|left the field|leaves the field|retired hurt|concussion|withdr[ae]w\w*"
             r"|squad|dropped|named|playing xi|doubt\w*|contract|auction|trade|captain\w*|interview|milestone|record"
             r"|century|hundred|ton|fifty|half-century|five-for|fifer|four-for|\d-fer)\b")
RUMOUR = r"\b(reportedly|rumou?rs?|speculat\w*|sources say|likely to|set to|could|may|might|claims? that|unconfirmed|tipped)\b"
CLICKBAIT = r"you won't believe|shocking|viral|goes wild|breaks the internet|netizens|\bmemes?\b|jaw-dropping|watch:"
# Cricbuzz match/team pages that Google News lists as if they were stories.
NOT_ARTICLE = r" - (squads|match info|live scores?|scorecard|(full )?commentary|points table|schedule|results)\b"
ALIASES = {"MS Dhoni": ["Thala", "Mahi"]}     # nicknames headlines use on their own
# Injury or availability news, used to flag players in match alerts (never to remove them).
INJURY = (r"\b(injur\w*|ruled out|strain\w*|side issue|hamstring|niggle|scans?|fracture\w*|surgery|withdr[ae]w\w*"
          r"|miss(es|ed)? (the )?(rest|remainder|series|match|game|tour)|out of the|doubt\w*|limp\w* off|goes down"
          r"|went down|left the field|leaves the field|retired hurt|concussion|sore|stiff\w*|unavailable|fitness)\b")
# Headlines where a tracked player is only a yardstick for someone else ("Gill joins MS Dhoni in elite list").
# {s} is the player's surname, lower case.
PASSING = [r"\b(joins|equals?|equalled|breaks?|broke|surpass\w*|overtak\w*|goes past|went past|levels? with|eclips\w*"
           r"|emulat\w*|than|the next|like|learning from|lessons from|comparisons? with|towards|loyal to|namedrops|credits)"
           r"\s([\w.,]+\s){{0,2}}?{s}\b",
           r"\b{s}'?s?'?\s(\d{{4}}|record|feat|tally|influen|legacy|(t20 )?world cup|captaincy|advice|mantra)",
           r"\bfrom\s([\w.]+\s){{0,2}}{s}'s\b",
           r",\s([\w.]+\s)?{s},"]            # one name in a list: "Rohit, Dhoni, Bumrah"
CSK_TEAM_ID, CSK_SHORT = 58, "CSK"          # Cricbuzz team id and the short name its squad lists use
MAX_NEWS_AGE = timedelta(days=3)
# Cricbuzz matchInfo "state" values. Seen live on 29 Sep 2026: Preview, Upcoming, Stumps, Complete, Abandon.
# The in-play ones follow Cricbuzz's usual wording; anything unrecognised is reported, never shown as live.
MATCH_STATES = {"live": {"in progress", "innings break", "lunch", "tea", "dinner", "drink", "drinks", "rain", "delay",
                         "bad light", "wet outfield", "strategic timeout"},
                "paused": {"stumps"},                                   # multi-day match, between days
                "upcoming": {"preview", "upcoming", "toss", "scheduled"},     # "scheduled": from the schedule page
                "finished": {"complete", "abandon", "abandoned", "cancelled", "no result", "draw", "tie"}}

# ---------- helpers ----------
def load_config():
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

def tokens(s):
    s = re.sub(r"\s[-|]\s[^-|]+$", "", s)          # drop " - Publisher" suffix
    s = re.sub(r"#\w+", "", s)                      # hashtags (#WhistlePodu) are on every official post
    return set(w[:5] for w in norm(s).split() if len(w) > 2)   # crude stemming

def ist(dt):
    return dt.astimezone(IST).strftime("%d %b %H:%M IST") if dt else "time TBC"

def next_data(page):
    """Cricbuzz is a Next.js site: page data sits in self.__next_f.push([1,"<JSON string>"]) script chunks."""
    out = []
    for chunk in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', page, re.S):
        try:
            out.append(json.loads(f'"{chunk}"'))
        except ValueError:
            pass
    return "".join(out)

def json_objects(text, key):
    """Yield every JSON object or list stored under "key": in text."""
    dec = json.JSONDecoder()
    for m in re.finditer(r'"%s":[{[]' % re.escape(key), text):
        try:
            yield dec.raw_decode(text, m.end() - 1)[0]
        except ValueError:
            pass

def db_connect(path=None):
    db = sqlite3.connect(path or os.path.join(HERE, "state.db"))
    db.execute("create table if not exists seen(key text primary key, title text, ts real)")
    if "pub" not in [c[1] for c in db.execute("pragma table_info(seen)")]:     # publish time, added later
        db.execute("alter table seen add column pub real")
    db.execute("create table if not exists squad(id text primary key, name text)")
    db.execute("create table if not exists kv(k text primary key, v text)")
    db.execute("create table if not exists match_squads(match_id text primary key, ts real, state text, players text)")
    return db

# ---------- alerts ----------
alerts_sent = 0                             # counts alerts, so a cycle knows whether anything new went out

def send_alert(cfg, text, dry_run=False, silent=False):
    global alerts_sent
    alerts_sent += 1
    stamp = datetime.now(IST).strftime("%d %b %H:%M IST")
    text = f"{text}\n🕒 {stamp}"
    token = os.environ.get(cfg["alert"]["telegram_bot_token_env"], "")
    chat = os.environ.get(cfg["alert"]["telegram_chat_id_env"], "")
    if dry_run or not (token and chat):
        print("\n--- ALERT (not sent: dry-run or Telegram not configured) ---\n" + text + "\n")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "false",
                                   "disable_notification": "true" if silent else "false"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20)
    except Exception as e:
        print("Telegram send failed:", e, file=sys.stderr)

# ---------- news ----------
def parse_rss(xml_text):
    """RSS <item>s (Google News, Bing, publishers) and Atom <entry>s (YouTube channel feeds)."""
    items = []
    for it in ET.fromstring(xml_text).iter():
        if it.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        kids = {}                           # child name without namespace ("News:Source" -> "Source") -> element
        for c in it:
            kids.setdefault(c.tag.rsplit("}", 1)[-1].lower(), c)
        text = lambda k: (kids[k].text or "").strip() if k in kids else ""
        title = html.unescape(text("title"))
        link = text("link") or (kids["link"].get("href", "") if "link" in kids else "")
        if "bing.com/news/apiclick" in link:            # Bing wraps the article URL in a redirect
            link = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url", [link])[0]
        src = text("source")
        pub = text("pubdate") or text("published")
        try:
            when = (parsedate_to_datetime(pub) if "," in pub else datetime.fromisoformat(pub)).astimezone(IST) if pub else None
        except Exception:
            when = None
        desc = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text("description")))).strip()
        items.append({"title": title, "link": link, "source": src, "when": when, "summary": one_line(desc, title, src)})
    return items

def one_line(desc, title, src):
    """First sentence of an RSS description, or "" if it only repeats the headline (Google News does that)."""
    if set(norm(desc).split()) <= set(norm(f"{title} {src}").split()):
        return ""
    s = re.split(r"(?<=[.!?])\s", desc)[0]
    return s if len(s) <= 180 else s[:177].rsplit(" ", 1)[0] + "…"


def classify(title):
    t = title.lower()
    if re.search(CLICKBAIT, t):
        return None
    imp = "breaking" if re.search(BREAKING, t) else "important" if re.search(IMPORTANT, t) else "minor"
    rumour = bool(re.search(RUMOUR, t))
    if rumour and imp == "breaking":
        imp = "important"   # unverified claims never rank as breaking
    return imp, rumour

def same_name(a, b):
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a)                   # "Zak" for "Zakary", "Matt" for "Matthew"

def short_name(t, parts):
    """Match a player by one name alone: surname ("Ellis, Davies ruled out") or first name ("Ruturaj's gain").
    Missing a story is worse than a wrong tag, so any capitalised match counts, unless the headline is in sentence
    case and a different name sits right next to it: "Kuldeep Yadav" is not Kuldip Yadav, "Shreyas Iyer" is not
    Shreyas Gopal. (In Title Case headlines every word is capitalised, so that check can't be made.)"""
    small = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "vs", "v", "with"}
    head = re.sub(r"\s[-|]\s[^-|]+$", "", t)                   # publisher suffix is always capitalised
    title_case = sum(w[0].islower() and w not in small for w in re.findall(r"[A-Za-z][\w']*", head)) <= 1
    first, last = parts[0], parts[-1]
    for m in re.finditer(rf"\b{re.escape(last)}\b", t):
        prev = re.search(r"([A-Z][a-z]+)\s+$", t[:m.start()])
        if title_case or not prev or same_name(prev.group(1), parts[-2]):
            return m
    if len(first) >= 3 and not first.isupper():                 # skip initials like "MS"
        for m in re.finditer(rf"\b{re.escape(first)}\b", t):
            nxt = re.match(r"\s+([A-Z][a-z]+)", t[m.end():])
            if title_case or not nxt or same_name(nxt.group(1), last):
                return m
    return None

def mention(title, name):
    """'about' if the headline is about this player, 'passing' if he is only a yardstick for someone else, else None."""
    t = title.replace("’", "'")
    parts = name.split()
    found = re.search(rf"\b{re.escape(name)}\b", t, re.I)
    if not found and len(parts) > 1:
        found = short_name(t, parts)
    if not found:
        found = next((re.search(rf"\b{a}\b", t) for a in ALIASES.get(name, []) if re.search(rf"\b{a}\b", t)), None)
    if not found:
        return None
    s = re.escape(parts[-1].lower())
    low = t.lower()
    return "passing" if any(re.search(p.format(s=s), low) for p in PASSING) else "about"

def tag_item(title, roster_names, muted):
    tags = ["CSK"] if re.search(r"\bchennai super kings\b|\bcsk\b", title, re.I) else []
    for n in dict.fromkeys(["MS Dhoni", *roster_names]):     # Dhoni stays tracked even if he leaves the squad
        if n not in muted and mention(title, n) == "about":
            tags.append(n)
    return tags

def is_article(title):
    head = re.sub(r"\s[-|]\s[^-|]+$", "", title)                # drop " - Publisher"
    return head != head.lower() and not re.search(NOT_ARTICLE, title, re.I)   # all-lowercase = a Cricbuzz team page

def is_duplicate(db, title):
    key = hashlib.sha1(norm(title).encode()).hexdigest()
    if db.execute("select 1 from seen where key=?", (key,)).fetchone():
        return True
    tk = tokens(title)
    for (old,) in db.execute("select title from seen order by ts desc limit 300"):
        ot = tokens(old)
        if tk and ot and len(tk & ot) / len(tk | ot) >= 0.6:
            return True
    return False

def mark_seen(db, title, when=None):
    key = hashlib.sha1(norm(title).encode()).hexdigest()
    db.execute("insert or ignore into seen(key, title, ts, pub) values(?,?,?,?)",
               (key, title, time.time(), when.timestamp() if when else None))
    db.commit()

def process_items(cfg, db, items, roster_names, dry_run, latest, first_run):
    oldest = datetime.now(IST) - MAX_NEWS_AGE
    for it in items:
        if (it["when"] and it["when"] < oldest) or not is_article(it["title"]):
            continue
        tags = tag_item(it["title"], roster_names, set(cfg["muted_players"]))
        if it.get("official"):              # the team's own post: always relevant, never a rumour
            tags = [it["official"]] + [x for x in tags if x != "CSK"]
        if not tags:
            continue
        c = classify(it["title"])
        if c is None or is_duplicate(db, it["title"]):
            continue
        imp, rumour = c[0], c[1] and not it.get("official")
        mark_seen(db, it["title"], it["when"])
        latest.append({**it, "tags": tags, "importance": imp, "rumour": rumour})
        if first_run:                       # don't spam old stories on first start
            continue
        if RANKS[imp] < RANKS[cfg["alert"]["min_importance"]]:
            continue
        icon = {"breaking": "🚨", "important": "⚠️", "minor": "📰"}[imp]
        flag = " [RUMOUR]" if rumour else ""
        lines = [f"{icon} {imp.upper()}{flag} | {', '.join(tags)}", it["title"], it.get("summary"),
                 f"Published {ist(it['when'])}" if it["when"] else "", it["source"], it["link"]]
        send_alert(cfg, "\n".join(x for x in lines if x), dry_run)

def fetch_news(cfg, roster_names, include_players):
    queries = list(cfg["team_queries"])
    if include_players:
        queries += [f"{n} cricket" for n in roster_names if n not in cfg["muted_players"]]
    items = []
    for q in queries:                       # every search engine in news_search_urls (Google News, Bing News)
        for template in cfg["news_search_urls"]:
            url = template.format(q=urllib.parse.quote_plus(q))
            try:
                items += parse_rss(http_get(url))
            except Exception as e:
                print(f"[warn] news search '{q}' ({urllib.parse.urlparse(url).netloc}) failed: {e}", file=sys.stderr)
    for url in cfg["extra_rss_feeds"]:
        try:
            items += parse_rss(http_get(url))
        except Exception as e:
            print(f"[warn] feed {url} failed: {e}", file=sys.stderr)
    for feed in cfg.get("official_feeds", []):  # e.g. CSK's YouTube channel: every post is about CSK
        try:
            items += [{**it, "official": feed["tag"]} for it in parse_rss(http_get(feed["url"]))]
        except Exception as e:
            print(f"[warn] official feed {feed['url']} failed: {e}", file=sys.stderr)
    return items

# ---------- roster ----------
def parse_roster_page(page):
    """Cricbuzz team page -> [{"id", "name"}]. The link title holds the display name ("MS Dhoni", not "Ms Dhoni")."""
    found = {}
    for pid, name in re.findall(r'<a href="/profiles/(\d+)/[a-z0-9-]+" title="([^"]+)"', page):
        found.setdefault(pid, html.unescape(name).strip())
    return [{"id": i, "name": n} for i, n in found.items()]

def ipl_series_urls(page):
    """IPL season match-list URLs linked from a Cricbuzz archive page, newest season first."""
    found = set(re.findall(r"/cricket-series/(\d+)/indian-premier-league-(\d{4})", page))
    return [f"https://www.cricbuzz.com/cricket-series/{sid}/indian-premier-league-{yr}/matches"
            for sid, yr in sorted(found, key=lambda x: x[1], reverse=True)]

def team_matches(page, team_id):
    """Started matches of one team on a Cricbuzz series-matches page, as {"id", "state"} dicts."""
    out = {}
    for m in json_objects(next_data(page), "matchInfo"):
        if not isinstance(m, dict):
            continue
        teams = {str(m.get(k, {}).get("teamId")) for k in ("team1", "team2")}
        if str(team_id) in teams and match_phase(m.get("state")) in ("live", "paused", "finished"):
            out.setdefault(str(m["matchId"]), {"id": str(m["matchId"]), "state": m.get("state", "")})
    return list(out.values())

def season_squad(cfg, db):
    """CSK players named in any match squad of the latest IPL season that has started. Cricbuzz's team page
    misses some (auction buys like Aman Khan, replacement signings), the match squads don't."""
    year, urls = datetime.now(IST).year, []
    for y in (year, year - 1):
        try:
            urls += ipl_series_urls(http_get(cfg["ipl_archive_url"].format(year=y)))
        except Exception as e:
            print(f"[warn] IPL archive {y} failed: {e}", file=sys.stderr)
    for url in dict.fromkeys(urls):
        try:
            matches = team_matches(http_get(url), CSK_TEAM_ID)
        except Exception as e:
            print(f"[warn] IPL season page {url} failed: {e}", file=sys.stderr)
            continue
        if not matches:
            continue                        # season not started yet: use the one before
        found = {}
        for m in matches:
            try:
                for pid, e in get_squads(cfg, db, m).items():
                    if e["team"] == CSK_SHORT:
                        found.setdefault(pid, e["name"])
            except Exception as e:
                print(f"[warn] squads for IPL match {m['id']} failed: {e}", file=sys.stderr)
        return [{"id": i, "name": n} for i, n in found.items()]
    return []

def refresh_roster(cfg, db, dry_run, first_run):
    saved = [{"id": i, "name": n} for i, n in db.execute("select id, name from squad")]
    fresh = []
    for url in cfg["roster_sources"]:
        try:
            fresh = parse_roster_page(http_get(url))
        except Exception as e:
            print(f"[warn] roster source {url} failed: {e}", file=sys.stderr)
            fresh = []
        if 15 <= len(fresh) <= 40:
            break
        if fresh:
            print(f"[warn] roster source {url} gave {len(fresh)} players; ignoring it", file=sys.stderr)
        fresh = []
    if not fresh:                           # sanity check failed -> keep what we have
        return saved or cfg["roster"]
    # Add season-squad players the team page misses, except anyone who was on the team page and then left it
    # (released or traded: the old season's match squads would otherwise keep them).
    kv = lambda k: set(json.loads((db.execute("select v from kv where k=?", (k,)).fetchone() or ["[]"])[0]))
    on_page = {p["id"] for p in fresh}
    released = (kv("released_ids") | (kv("team_page_ids") - on_page)) - on_page
    for k, v in (("team_page_ids", on_page), ("released_ids", released)):
        db.execute("insert or replace into kv values(?,?)", (k, json.dumps(sorted(v))))
    fresh += [p for p in season_squad(cfg, db) if p["id"] not in on_page and p["id"] not in released]
    db.execute("delete from squad")
    db.executemany("insert into squad values(?,?)", [(p["id"], p["name"]) for p in fresh]); db.commit()
    old, new = {p["id"]: p["name"] for p in saved}, {p["id"]: p["name"] for p in fresh}
    added = sorted(new[i] for i in new.keys() - old.keys())
    removed = sorted(old[i] for i in old.keys() - new.keys())
    # Only compare with a squad fetched earlier: the config seed may be out of date.
    if saved and not first_run and (added or removed):
        send_alert(cfg, "🔁 CSK SQUAD CHANGE\n" + (f"Added: {', '.join(added)}\n" if added else "")
                   + (f"Removed: {', '.join(removed)}" if removed else ""), dry_run)
    return fresh

# ---------- match day ----------
def match_phase(state):
    s = (state or "").strip().lower()
    return next((phase for phase, names in MATCH_STATES.items() if s in names), "unknown")

def match_from_info(m, slugs=None, series=""):
    """One Cricbuzz matchInfo object -> our match dict."""
    mid, ms = str(m.get("matchId", "")), str(m.get("startDate", ""))
    teams = [(m.get(k, {}).get("teamName", "?"), m.get(k, {}).get("teamSName", "")) for k in ("team1", "team2")]
    return {"id": mid, "series_id": str(m.get("seriesId", "")),
            "title": f"{teams[0][0]} vs {teams[1][0]}, {m.get('matchDesc', '')}".rstrip(", "),
            "teams": teams, "format": m.get("matchFormat", ""), "desc": m.get("matchDesc", ""),
            "series": m.get("seriesName") or series, "state": m.get("state", ""),
            "phase": match_phase(m.get("state")), "status": m.get("status", ""),
            "start": datetime.fromtimestamp(int(ms) / 1000, IST) if ms.isdigit() else None,
            "url": f"https://www.cricbuzz.com/live-cricket-scores/{mid}/{(slugs or {}).get(mid, '')}".rstrip("/")}

def parse_live_scores(page):
    """Any Cricbuzz page with matchInfo objects (live scores, series match lists) -> match dicts."""
    slugs = dict(re.findall(r"/live-cricket-scores/(\d+)/([a-z0-9-]+)", page))
    matches = {}
    for m in json_objects(next_data(page), "matchInfo"):
        if isinstance(m, dict) and m.get("matchId") and str(m["matchId"]) not in matches:
            matches[str(m["matchId"])] = match_from_info(m, slugs)
    return list(matches.values())

def parse_schedule(page):
    """Cricbuzz schedule page: every international, domestic, league and women's match of the next ~5 days.
    It carries no match state, so these are "Scheduled" until the live-scores page shows them."""
    out = {}
    for days in json_objects(next_data(page), "matchScheduleMap"):
        for day in days if isinstance(days, list) else []:
            for series in (day.get("scheduleAdWrapper") or {}).get("matchScheduleList", []):
                for mi in series.get("matchInfo", []):
                    m = match_from_info({**mi, "state": "Scheduled"}, series=series.get("seriesName", ""))
                    out.setdefault(m["id"], m)
    return list(out.values())

def parse_squads(page):
    """Cricbuzz match-squads page -> {player id: {name, team, group}}.
    group is "Squad" before the toss, then "playing XI" or "bench"."""
    out = {}
    for groups in json_objects(next_data(page), "players"):
        for group, players in (groups.items() if isinstance(groups, dict) else []):
            if group.lower() == "support staff" or not isinstance(players, list):
                continue
            for p in players:
                if isinstance(p, dict) and "id" in p:
                    out[str(p["id"])] = {"name": p.get("name", ""), "team": p.get("teamName", ""), "group": group}
    return out

def get_squads(cfg, db, m):
    """Squads for one match, cached: refetched every 30 min or on a state change until the XI is out."""
    row = db.execute("select ts, state, players from match_squads where match_id=?", (m["id"],)).fetchone()
    if row:
        players = json.loads(row[2])
        has_xi = any(p["group"].lower() == "playing xi" for p in players.values())
        if has_xi or (row[1] == m["state"] and time.time() - row[0] < 1800):
            return players
    players = parse_squads(http_get(cfg["squads_url"].format(id=m["id"])))
    db.execute("insert or replace into match_squads values(?,?,?,?)", (m["id"], time.time(), m["state"], json.dumps(players)))
    db.execute("delete from match_squads where ts < ?", (time.time() - 7 * 86400,)); db.commit()
    return players

def expected_squads(cfg, db, m):
    """Squad not published yet: take each team's squad from its latest started match in the same series
    (e.g. the 1st ODI squad for the 2nd ODI). These players are marked "expected"."""
    wanted = {short for _, short in m["teams"] if short}
    earlier = [x for x in parse_live_scores(http_get(cfg["series_matches_url"].format(sid=m["series_id"])))
               if x["series_id"] == m["series_id"] and x["id"] != m["id"] and x["phase"] in ("live", "paused", "finished")
               and x["start"] and m["start"] and x["start"] < m["start"]]
    out = {}
    for x in sorted(earlier, key=lambda x: x["start"], reverse=True)[:6]:
        found = {pid: {**e, "group": "expected"} for pid, e in get_squads(cfg, db, x).items() if e["team"] in wanted}
        out.update(found)
        wanted -= {e["team"] for e in found.values()}
        if not wanted:
            break
    return out

def players_in_matches(matches, roster, squads):
    """Link CSK players to matches through each match's Cricbuzz squad list, so any team or league works.
    One hit per (player, match); role is "xi", "bench", "squad" (XI not out yet) or "expected" (squad not out yet)."""
    hits = []
    for m in matches:
        sq = squads.get(m["id"]) or {}
        by_name = {norm(p["name"]): p for p in sq.values()}
        for p in roster:
            e = sq.get(str(p.get("id", ""))) or by_name.get(norm(p["name"]))
            if e:
                g = e["group"].lower()
                role = {"playing xi": "xi", "bench": "bench", "expected": "expected"}.get(g, "squad")
                # squad lists use the short team name ("INDA"); map it to "India A" and find the opponent
                mine = next((i for i, tm in enumerate(m.get("teams", [])) if tm[1] == e["team"]), None)
                team = m["teams"][mine][0] if mine is not None else e["team"]
                opponent = m["teams"][1 - mine][0] if mine is not None else ""
                hits.append({"player": p["name"], "team": team, "opponent": opponent, "role": role, "match": m})
    return hits

ROLE_TEXT = {"xi": "playing XI", "squad": "in squad", "bench": "on bench", "expected": "expected, squad not out yet"}

def day_label(dt, now):
    days = (dt.astimezone(IST).date() - now.date()).days
    return "TODAY" if days == 0 else "TOMORROW" if days == 1 else dt.astimezone(IST).strftime("%a %d %b").upper()

def describe(h, now=None):
    """(css class, badge text) for a hit. Only a live match gives PLAYING NOW."""
    m, role, now = h["match"], h["role"], now or datetime.now(IST)
    if m["phase"] == "live":
        if role == "xi": return "live", f"PLAYING NOW (started {ist(m['start'])})"
        if role == "bench": return "off", "Match live, on the bench"
        return "live", f"LIVE, in squad, XI not published (started {ist(m['start'])})"
    if m["phase"] == "upcoming" and m["start"]:
        return "soon", (f"PLAYING {day_label(m['start'], now)}, starts {ist(m['start'])}, {ROLE_TEXT[role]}"
                        + (" ⚠️ injury news" if h.get("injury") else ""))
    if m["phase"] == "paused":
        return "off", f"{m['state']}: {m['status']}"
    return "off", f"Cricbuzz state '{m['state']}': {m['status']}"

def match_block(hs):
    """Lines for one match in a digest or alert: teams, format, start time, then the CSK players in it."""
    m = hs[0]["match"]
    when = {"live": "LIVE now", "paused": m["state"]}.get(m["phase"], f"starts {m['start']:%H:%M} IST")
    lines = [f"• {m['teams'][0][0]} vs {m['teams'][1][0]} · {m['format'] or '?'} · {when}"]
    for h in hs:
        lines.append(f"   {h['player']} ({h['team']}, {ROLE_TEXT[h['role']]})")
        lines += [f"      ⚠️ injury news: “{title}” ({day})" for title, day in h.get("injury", [])]
    lines.append(f"   {m['desc']}, {m['series']}\n   {m['url']}")
    return "\n".join(lines)

def injury_news(db, player, days=4):
    """Recent headlines naming this player with injury or availability words, newest first (at most 2).
    Shown next to the player in match alerts so you can judge; he is never dropped because of them."""
    out = []
    for title, ts in db.execute("select title, coalesce(pub, ts) t from seen where t >= ? order by t desc",
                                (time.time() - days * 86400,)):
        if re.search(INJURY, title, re.I) and mention(title, player):
            out.append((re.sub(r"\s[-|]\s[^-|]+$", "", title), datetime.fromtimestamp(ts, IST).strftime("%d %b")))
    return out[:2]

def match_day(m, now):
    return "TODAY" if m["phase"] in ("live", "paused") else day_label(m["start"], now)   # a Test on day 3 is today's

def day_sections(hits, now):
    """Group hits by IST day (TODAY / TOMORROW), then by match, earliest first."""
    by_match = {}
    for h in sorted(hits, key=lambda h: h["match"]["start"]):
        by_match.setdefault(h["match"]["id"], []).append(h)
    days = {}
    for hs in by_match.values():
        days.setdefault(match_day(hs[0]["match"], now), []).append(match_block(hs))
    return "\n\n".join(f"{day} ({(now + timedelta(days=0 if day == 'TODAY' else 1)):%a %d %b})\n" + "\n".join(blocks)
                       for day, blocks in days.items())

def check_matches(cfg, db, roster, dry_run):
    try:
        live = parse_live_scores(http_get(cfg["live_scores_url"]))
    except Exception as e:
        print(f"[warn] live scores failed: {e}", file=sys.stderr)
        live = []
    try:
        scheduled = parse_schedule(http_get(cfg["schedule_url"]))
    except Exception as e:
        print(f"[warn] schedule failed: {e}", file=sys.stderr)
        scheduled = []
    if not live and not scheduled:
        print("[warn] no match data on Cricbuzz; its page format may have changed", file=sys.stderr)
        return []
    now = datetime.now(IST)
    matches = {m["id"]: m for m in scheduled if m["start"] and m["start"] > now}   # started ones: trust live scores
    matches.update({m["id"]: m for m in live})
    squads = {}
    for m in matches.values():
        if m["phase"] == "unknown":
            print(f"[warn] unknown Cricbuzz match state '{m['state']}' ({m['title']}); not treated as live", file=sys.stderr)
        soon = m["phase"] == "upcoming" and m["start"] and (m["start"].date() - now.date()).days in (0, 1)
        if soon or m["phase"] in ("live", "paused", "unknown"):
            try:
                squads[m["id"]] = get_squads(cfg, db, m)
                if not squads[m["id"]] and soon and m["series_id"]:
                    squads[m["id"]] = expected_squads(cfg, db, m)
            except Exception as e:
                print(f"[warn] squads for {m['title']} failed: {e}", file=sys.stderr)
    hits = players_in_matches(list(matches.values()), roster, squads)
    for h in hits:                          # before the XI is out, flag recent injury news (the XI itself is definitive)
        if h["role"] in ("squad", "expected"):
            h["injury"] = injury_news(db, h["player"])
    kv = lambda k: db.execute("select 1 from kv where k=?", (k,)).fetchone()
    mark = lambda k: db.execute("insert or replace into kv values(?,?)", (k, "1"))

    # 1. PLAYING NOW: once per match, when it is live and a CSK player is in the XI (or the squad, before the XI is out).
    live_hits = {}
    for h in hits:
        if h["match"]["phase"] == "live" and h["role"] != "bench":
            live_hits.setdefault(h["match"]["id"], []).append(h)
    for mid, hs in live_hits.items():
        if kv(f"alert:live:{mid}"):
            continue
        mark(f"alert:live:{mid}"); db.commit()
        m = hs[0]["match"]
        who = ", ".join(f"{h['player']} ({h['team']}, {ROLE_TEXT[h['role']]})" for h in hs)
        note = " (playing XI not published yet)" if any(h["role"] != "xi" for h in hs) else ""
        send_alert(cfg, f"🏏 PLAYING NOW: {who}\nStarted {ist(m['start'])}{note}\n{m['title']} · {m['format']}\n"
                        f"{m['series']}\n{m['status']}\n{m['url']}", dry_run)

    # 2. Morning digest of today's and tomorrow's matches, then an alert for any match found after it.
    coming = [h for h in hits if h["role"] != "bench" and h["match"]["start"]
              and (h["match"]["phase"] in ("live", "paused")
                   or (h["match"]["phase"] == "upcoming" and (h["match"]["start"].date() - now.date()).days in (0, 1)))]
    hour = cfg["alert"].get("match_digest_hour", 8)
    digest_at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= digest_at and not kv(f"digest:{now:%Y-%m-%d}"):
        mark(f"digest:{now:%Y-%m-%d}")
        for h in coming:
            mark(f"announced:{h['match']['id']}")
        db.commit()
        if coming:
            send_alert(cfg, "📅 CSK PLAYERS' MATCHES\n\n" + day_sections(coming, now), dry_run)
        else:
            send_alert(cfg, "📅 No CSK player has a match today or tomorrow.", dry_run, silent=True)
        return hits
    digest_done = now >= digest_at                          # before 8 AM: hold matches the digest will cover
    new = [h for h in coming if h["match"]["phase"] == "upcoming"   # live ones already got PLAYING NOW
           and not kv(f"announced:{h['match']['id']}") and (digest_done or h["match"]["start"] < digest_at)]
    if new:
        for h in new:
            mark(f"announced:{h['match']['id']}")
        db.commit()
        send_alert(cfg, "📅 NEW MATCH FOR CSK PLAYERS\n\n" + day_sections(new, now), dry_run)
    return hits

# ---------- dashboard ----------
def write_dashboard(roster, hits, latest, db, path=None):
    names = [p["name"] for p in roster]
    order = {"live": 0, "soon": 1, "off": 2}
    best = {}                               # player -> most relevant (class, text, hit)
    for h in hits:
        cls, text = describe(h)
        if h["player"] not in best or order[cls] < order[best[h["player"]][0]]:
            best[h["player"]] = (cls, text, h)
    rows = ""
    for n in sorted(names, key=lambda x: (order[best[x][0]] if x in best else 3, x)):
        cls, badge = "", ""
        if n in best:
            cls, text, h = best[n]
            m = h["match"]
            vs = f"{h['team']} vs {h['opponent']}" if h["opponent"] else m["title"]
            fmt = f" · {m['format']}" if m.get("format") else ""
            badge = (f'<span class="b {cls}">{html.escape(text)}</span> '
                     f'<a href="{html.escape(m["url"])}">{html.escape(vs + fmt)}</a> '
                     f'<small>{html.escape(m["desc"])}, {html.escape(m["series"])}</small>')
        rows += f'<tr class="{cls}"><td>{html.escape(n)}</td><td>{badge}</td></tr>'
    news = ""
    newest_first = sorted(latest, key=lambda i: (RANKS[i["importance"]], i["when"] or datetime.min.replace(tzinfo=IST)), reverse=True)
    for it in newest_first[:60]:
        w = it["when"].strftime("%d %b %H:%M") if it["when"] else ""
        r = " <em>(rumour)</em>" if it["rumour"] else ""
        news += (f'<li class="{it["importance"]}"><b>{it["importance"].upper()}</b>{r} '
                 f'<a href="{html.escape(it["link"])}">{html.escape(it["title"])}</a> '
                 f'<small>{html.escape(", ".join(it["tags"]))} · {w} IST</small>'
                 + (f'<br><small>{html.escape(it["summary"])}</small>' if it.get("summary") else "") + '</li>')
    page = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=120><title>CSK Tracker</title>
<style>body{{font:15px system-ui;max-width:900px;margin:1rem auto;padding:0 1rem;background:#fffbe6;color:#222}}
h1{{color:#b8860b}}table{{width:100%;border-collapse:collapse}}td{{padding:.35rem;border-bottom:1px solid #eee}}
tr.live{{background:#ffe066;font-weight:600}}.b{{color:#fff;padding:2px 8px;border-radius:10px;font-size:12px}}
.b.live{{background:#c00}}.b.soon{{background:#d97706}}.b.off{{background:#888}}
li{{margin:.4rem 0}}li.breaking b{{color:#c00}}li.important b{{color:#d97706}}small{{color:#666}}</style>
<h1>💛 CSK Tracker</h1><p>Updated {datetime.now(IST):%d %b %Y %H:%M} IST</p>
<h2>Squad ({len(names)})</h2><table>{rows}</table>
<h2>News this session</h2><ul>{news or "<li>Nothing new yet.</li>"}</ul>"""
    with open(path or os.path.join(HERE, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(page)

# ---------- main loop ----------
def run_cycle(cfg, db, state, dry_run, first_run):
    now = time.time(); iv = cfg["intervals_seconds"]; sent_before = alerts_sent
    # Last-run times live in state.db, so separate --once runs (GitHub Actions) also slow down roster and player news.
    last = {k: float(v) for k, v in db.execute("select k, v from kv where k like 'last:%'")}
    due = lambda job, every: now - last.get(f"last:{job}", 0) >= every - 60   # 60 s slack for cron jitter
    def done(job):
        db.execute("insert or replace into kv values(?,?)", (f"last:{job}", str(now))); db.commit()
    if due("roster", iv["roster"]):
        state["roster"] = refresh_roster(cfg, db, dry_run, first_run); done("roster")
    elif "roster" not in state:
        state["roster"] = [{"id": i, "name": n} for i, n in db.execute("select id, name from squad")] or cfg["roster"]
    roster = state["roster"]
    names = [p["name"] for p in roster]
    latest = state.setdefault("latest", [])
    if due("news", iv["news"]):
        pn = due("player_news", iv["player_news"])
        process_items(cfg, db, fetch_news(cfg, names, pn), names, dry_run, latest, first_run)
        done("news")
        if pn: done("player_news")
    hits = check_matches(cfg, db, roster, dry_run)
    write_dashboard(roster, hits, latest, db)
    quiet_check_in(cfg, db, now, sent_before, dry_run)
    return hits

def quiet_check_in(cfg, db, now, sent_before, dry_run):
    """Every quiet_update_hours without any alert, send one silent "no new updates" message.
    It doubles as proof that the tracker is still running."""
    get = lambda k: db.execute("select v from kv where k=?", (k,)).fetchone()
    put = lambda k: db.execute("insert or replace into kv values(?,?)", (k, str(now)))
    if alerts_sent > sent_before or not get("last:quiet"):   # something went out, or first run: restart the clock
        put("last:quiet"); db.commit()
        return
    since = float(get("last:quiet")[0])
    if now - since >= cfg["alert"].get("quiet_update_hours", 3) * 3600 - 60:
        put("last:quiet"); db.commit()
        send_alert(cfg, f"✅ No new updates since {ist(datetime.fromtimestamp(since, IST))}.\n"
                        "Tracker is running normally.", dry_run, silent=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-warmup", action="store_true", help="alert even on stories found in the first run")
    a = ap.parse_args()
    cfg = load_config(); db = db_connect(); state = {}
    fresh_db = not db.execute("select 1 from seen limit 1").fetchone()
    first = fresh_db and not a.no_warmup
    while True:
        hits = run_cycle(cfg, db, state, a.dry_run, first); first = False
        if a.once: break
        live = any(h["match"]["phase"] == "live" for h in hits)
        time.sleep(cfg["intervals_seconds"]["match_day_live"] if live else 60)

if __name__ == "__main__":
    main()
