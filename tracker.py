#!/usr/bin/env python3
"""CSK + Dhoni news and match tracker. Standard library only.

Usage:
  python tracker.py            # run forever (poll loop)
  python tracker.py --once     # one cycle, then exit (good for cron / GitHub Actions)
  python tracker.py --dry-run  # print alerts instead of sending them
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable Telegram alerts.
"""
import argparse, hashlib, html, json, os, re, sqlite3, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (compatible; CSKTracker/1.0)"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TELEGRAM_LIMIT = 4000                       # Telegram rejects messages over 4096 characters
RANKS = {"minor": 0, "important": 1, "breaking": 2}

BREAKING = (r"\b(retire[sd]?|retirement|ruled out|steps? down|sacked|released|retained|traded|signs|signed|appointed"
            r"|(new|named|as) (captain|skipper|vice-captain)|ban|banned|suspended|suspension|replacement|joins (csk|chennai)"
            r"|(csk|coach\w*|captain\w*|skipper)('s)? appointment|appointment of"   # not "names Dhoni appointment as..."
            r"|(as|new|head)\s([\w ]{0,25}\s)?coach(es)?)\b")    # "as Chennai Super Kings coach"
IMPORTANT = (r"\b(injur\w*|fitness|strain|niggle|side issue|hamstring|scans?|surgery|return\w*|comeback|back from|selected"
             r"|goes down|went down|limp\w* off|left the field|leaves the field|retired hurt|concussion|withdr[ae]w\w*"
             r"|squad|dropped|named|playing xi|doubt\w*|contract|auction|trade|captain\w*|interview|milestone|record"
             r"|century|hundred|ton|fifty|half-century|five-for|fifer|four-for|\d-fer"
             r"|hat-trick|player of the match|man of the match|potm|haul|\d+ wickets|one-handed|blinder|screamer"
             r"|stunning|jaw-dropping|masterclass)\b")     # standout performances
RUMOUR = r"\b(reportedly|rumou?rs?|speculat\w*|sources say|likely to|set to|could|may|might|claims? that|unconfirmed|tipped)\b"
# Cricbuzz match/team pages that Google News lists as if they were stories.
NOT_ARTICLE = (r" - (squads|match info|live scores?|scorecard|(full )?commentary|points table|schedule|results)\b"
               r"|\b(live (full )?scorecard|full scorecard|live (cricket )?scores?|match info|points table|squad ipl \d{4})\b")
ALIASES = {"MS Dhoni": ["Thala", "Mahi"]}     # nicknames headlines use on their own
# Words that make a headline about cricket; needed before a first name alone counts as a CSK player.
CRICKET = (r"\b(cricket\w*|ipl|csk|odis?|\w*t20\w*|tests?|wickets?|centur(y|ies)|fifty|fifties|innings|bat(ter|sman|smen|ting)s?"
           r"|bowl(s|ed|er|ers|ing)?|spinn?(er|ers)|pacers?|seamers?|all[- ]?round\w*|squads?|playing xi|bcci|icc|ranji|duleep"
           r"|vijay hazare|super kings|world cup|sa20|ilt20|bbl|cpl|big bash|the hundred|asia cup|asian games|league|hat-trick"
           r"|runs|sixes|stumps|catch|skipper|captain\w*|kings|royals|titans|capitals|knight riders|sunrisers|nets|debut"
           r"|whistle ?podu|yellove)\b")            # CSK's slogans
# News saying a player will play ("to play", "will feature", "set to turn out", "named in"), and the day words used
# to find when: today, tomorrow, a weekday, or a date like "30 September".
PLAY_PHRASE = (r"\b(to|will|shall)\s+(play|feature|turn out|appear|captain|lead|make (his |a )?(debut|comeback|return))\b"
               r"|\b(named|picked|included|selected) (in|for)\b")
DAY_WORDS = (r"\b(today|tonight|tomorrow|(mon|tues|wednes|thurs|fri|satur|sun)day"
             r"|\d{1,2}(st|nd|rd|th)? (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
             r"|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}(st|nd|rd|th)?)\b")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
# Injury or availability news, used to flag players in match alerts (never to remove them).
INJURY = (r"\b(injur\w*|ruled out|strain\w*|side issue|hamstring|niggle|scans?|fracture\w*|surgery|withdr[ae]w\w*"
          r"|miss(es|ed)? (the )?(rest|remainder|series|match|game|tour)|out of the|doubt\w*|limp\w* off|goes down"
          r"|went down|left the field|leaves the field|retired hurt|concussion|sore|stiff\w*|unavailable|fitness)\b")
# Kinds of event, to spot the same story told by different publishers ("Ellis, Davies ruled out..." / "Australia
# suffer Nathan Ellis blow..."). First match wins; selection comes before injury so "Brevis dropped, 3 injured
# pacers" is a selection story. The injury words are strict ("leaves out Dhoni" is not an injury).
STORY_EVENTS = [
    ("retirement", r"retir(e|es|ed|ement)"),
    ("captaincy", r"captaincy|(new|named|appointed|as) (captain|skipper|vice-captain)|steps? down|leadership"),
    ("coach", r"(head|bowling|batting|fielding|spin|assistant|new) coach(es)?|coach(ing)? (role|job|staff)|support staff"
              r"|(csk|coach\w*|captain\w*)('s)? appointment"),
    ("transfer", r"trade[sd]?|trading|released|retained|retention|auction|signs|signed|joins|ropes? in|roped in|swaps?"),
    ("selection", r"named (in|for)|selected|dropped|picked|included|recalled|omitted|omission|snub\w*|playing xi"
                  r"|announce[sd]? [\w\- ]{0,30}squad"),
    ("injury", r"injur\w*|ruled out|out of (the |this |next |final |remaining )?(match|game|series|tour|odi|t20i?|test"
               r"|season|ipl|tournament|squad)|withdr[ae]w\w*|sent home|leaves? (the )?(tour|squad|camp)|blow|setback"
               r"|strain\w*|side issue|hamstring|niggle|scans?|fracture\w*|surgery|doubtful|miss(es|ed)? (the )?(rest"
               r"|remainder|series|match|game|tour|odi|test|final)|goes down|went down|limp\w* off|retired hurt|concussion"),
    ("performance", r"centur(y|ies)|hundred|fifty|fifties|\d+ wickets?|five-for|fifer|four-for|\d-fer|hat-trick|record"
                    r"|\d+ runs|catch|knock|sixes|haul|masterclass|player of the match|potm|man of the match"
                    r"|stunning|jaw-dropping|one-handed|blinder|screamer|magic delivery|brilliant|spectacular"),
]
# Headlines where a tracked player is only a yardstick for someone else ("Gill joins MS Dhoni in elite list").
# {s} is the player's surname, lower case.
PASSING = [r"\b(joins|equals?|equalled|breaks?|broke|surpass\w*|overtak\w*|goes past|went past|levels? with|eclips\w*"
           r"|emulat\w*|than|the next|like|learning from|lessons from|comparisons? with|towards|loyal to|namedrops|credits)"
           r"\s([\w.,]+\s){{0,2}}?{s}\b",
           r"\b{s}'?s?'?\s(\d{{4}}|record|feat|tally|influen|legacy|(t20 )?world cup|captaincy|advice|mantra)",
           r"\bfrom\s([\w.]+\s){{0,2}}{s}'s\b",
           r"\b{s}'s (csk|chennai super kings|side|team)\b",       # "to join Dhoni's CSK" is CSK news
           r",\s([\w.]+\s)?{s},"]            # one name in a list: "Rohit, Dhoni, Bumrah"
CSK_TEAM_ID, CSK_SHORT = 58, "CSK"          # Cricbuzz team id and the short name its squad lists use
MAX_NEWS_AGE = timedelta(days=3)            # older stories are ignored entirely
ALERT_MAX_AGE = timedelta(hours=24)         # older unseen ones (backlog, re-dated pages) are recorded, not alerted
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

def http_get(url, timeout=20, ua=UA, tries=2):
    """Fetch a page, retrying once after a dropped connection, timeout or server error (not after 403/404)."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "en"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == tries - 1:
                raise
        except Exception:
            if attempt == tries - 1:
                raise
        time.sleep(2)

def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

def tokens(s):
    pub = re.search(r"\s[-|]\s([^-|]+)$", s)         # drop a " - Publisher" suffix, but only a short one:
    if pub and len(pub.group(1).split()) <= 4:     # "Jamie Overton - Older, wiser and still bowling fast" keeps its end
        s = s[:pub.start()]
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
    # Matches only the news mentions (practice games, trials), one row per player and story. "told" is the day we
    # last announced ("2026-09-29|2"), so a re-read that finds a new day can send a correction.
    db.execute("create table if not exists news_matches(player text, link text, title text, source text, pub real,"
               " day text, days integer, checked real, told text, status text, primary key(player, link))")
    db.execute("create table if not exists news_checked(key text primary key, ts real)")
    if db.execute("select 1 from sqlite_master where name='news_fixtures'").fetchone():   # table before corrections
        for player, day, days, title, link, source in db.execute(
                "select player, day, days, title, link, source from news_fixtures").fetchall():
            pub = db.execute("select coalesce(pub, ts) from seen where title like ? || '%'", (title,)).fetchone()
            told = db.execute("select 1 from kv where k=?", (f"announced:news:{player}:{day}",)).fetchone()
            db.execute("insert or ignore into news_matches values(?,?,?,?,?,?,?,?,?,?)",
                       (player, link, title, source, pub[0] if pub else time.time(), day, days, 0,
                        f"{day}|{days}" if told else None, "active"))
        db.execute("drop table news_fixtures"); db.commit()
    # best rank sent per player and kind of event, so other publishers' copies of a story aren't sent again
    db.execute("create table if not exists story_events(player text, event text, rank integer, ts real,"
               " primary key(player, event))")
    db.execute("create table if not exists outbox(id integer primary key autoincrement, text text, silent integer,"
               " created real)")            # Telegram messages that failed, retried next run
    db.execute("create table if not exists news_feed(key text primary key, title text, link text, source text,"
               " summary text, pub real, seen real, tags text, importance text, rumour integer)")   # dashboard news
    db.execute("create table if not exists series_cache(sid text primary key, ts real, matches text)")
    # Matches we told the user a CSK player is in, and the scorecard figures we last sent for them.
    db.execute("create table if not exists tracked_matches(match_id text primary key, title text, players text,"
               " start real, last_poll real, last_text text, final integer default 0, added real)")
    db.execute("create table if not exists performances(match_id text, player text, line text, final integer,"
               " ts real, primary key(match_id, player))")
    # Everyone in the squads read (match_squads is dropped after 7 days); never pruned, it only grows slowly.
    db.execute("create table if not exists known_players(id text primary key, name text)")
    if not db.execute("select 1 from known_players limit 1").fetchone():    # squads read before this table existed
        for (players,) in db.execute("select players from match_squads").fetchall():
            remember_players(db, json.loads(players))
        db.commit()
    return db

def remember_players(db, players):
    """Keep the names from a squad page ({id: {name...}}) so they are still known after match_squads drops it."""
    db.executemany("insert or replace into known_players values(?,?)",
                   [(pid, p["name"].strip()) for pid, p in players.items() if " " in p["name"].strip()])

# ---------- alerts ----------
alerts_sent = 0                             # counts alerts, so a cycle knows whether anything new went out
outbox_db = None                            # state.db while a cycle runs: undelivered messages wait there
last_send = 0.0                             # time of the last Telegram message, to pace them

def split_message(text, limit=TELEGRAM_LIMIT):
    """Telegram's size limit: split at line breaks into parts of at most `limit` characters."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                    # one very long line: hard split
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts

def telegram_post(token, chat, text, silent):
    """Send one message, at most one per second (Telegram's per-chat pace). Waits and retries when Telegram says
    "too many requests", retries once after a network error. True once Telegram has accepted it."""
    global last_send
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "false",
                                   "disable_notification": "true" if silent else "false"}).encode()
    for attempt in range(3):
        wait = last_send + 1.1 - time.time()
        if wait > 0:
            time.sleep(wait)
        last_send = time.time()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=20)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    retry = json.loads(e.read().decode()).get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry = 5
                time.sleep(min(retry, 60))
                continue
            print(f"Telegram send failed: {e}", file=sys.stderr)
            if e.code < 500:
                return False                # bad token, chat or message: retrying now won't help
        except Exception as e:
            print(f"Telegram send failed: {e}", file=sys.stderr)
        time.sleep(2)
    return False

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
    parts = split_message(text)
    for i, part in enumerate(parts):
        if not telegram_post(token, chat, part, silent):
            if outbox_db is not None:       # keep the rest for the next run, so nothing is lost
                outbox_db.executemany("insert into outbox(text, silent, created) values(?,?,?)",
                                      [(p, int(silent), time.time()) for p in parts[i:]])
                outbox_db.commit()
                print(f"[warn] {len(parts) - i} message part(s) kept to retry next run", file=sys.stderr)
            return

def flush_outbox(cfg, db):
    """Deliver messages that failed earlier, oldest first; drop any older than a day."""
    token = os.environ.get(cfg["alert"]["telegram_bot_token_env"], "")
    chat = os.environ.get(cfg["alert"]["telegram_chat_id_env"], "")
    db.execute("delete from outbox where created < ?", (time.time() - 86400,))
    for mid, text, silent in db.execute("select id, text, silent from outbox order by id").fetchall():
        if not (token and chat) or not telegram_post(token, chat, text, bool(silent)):
            break
        db.execute("delete from outbox where id=?", (mid,))
        db.commit()
    db.commit()

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
    """(rank, rumour). Clickbait-style headlines ("WATCH: ...", "jaw-dropping catch") are kept: they are often a
    player's standout moment. Other publishers' copies are dropped by the same-story grouping."""
    t = title.lower()
    imp = "breaking" if re.search(BREAKING, t) else "important" if re.search(IMPORTANT, t) else "minor"
    rumour = bool(re.search(RUMOUR, t))
    if rumour and imp == "breaking":
        imp = "important"   # unverified claims never rank as breaking
    return imp, rumour

def same_name(a, b):
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a)                   # "Zak" for "Zakary", "Matt" for "Matthew"

def short_name(t, parts):
    """Match a player by one name alone. Missing a story is worse than a wrong tag, so:
    - the surname alone counts ("Ellis, Davies ruled out", "Macneil Hadley Noronha");
    - the first name alone counts in a cricket headline: one with the surname anywhere ("Jamie and Craig Overton")
      or a cricket word ("Ruturaj's gain: CSK skipper..."), which keeps out "Matt Cardona mourns PAC's death";
    - neither counts when it is part of another cricketer's full name ("Kuldeep Yadav" is not Kuldip Yadav);
    - a surname many people share doesn't count after a different first name ("Azam Khan", "Dilpreet Singh")."""
    first, last = parts[0], parts[-1]
    for m in re.finditer(rf"\b{re.escape(last)}\b", t):
        if not someone_else(t, m, parts) and not other_first_name(t, m, parts):
            return m
    cricket = re.search(rf"\b{re.escape(last)}", t, re.I) or re.search(CRICKET, t, re.I)
    if len(first) >= 3 and not first.isupper() and cricket:    # skip initials like "MS"
        for m in re.finditer(rf"\b{re.escape(first)}\b", t):
            if not someone_else(t, m, parts):
                return m
    return None

other_names = {}                            # word -> full names of other cricketers using it; see set_other_names
common_surnames = set()                     # config "common_surnames", lower case; see other_first_name

def set_other_names(cfg, db, roster_names):
    """Other cricketers' full names: config "namesakes", the other CSK players, every cricketer Cricbuzz lists
    (cricketers.json, see refresh_cricketers) and everyone in the squads read since. A short-name match inside one
    of these ("Kuldeep Yadav", "KL Rahul") is that person, not ours."""
    names = set(cfg.get("namesakes", [])) | set(roster_names)
    try:
        with open(os.path.join(HERE, "cricketers.json"), encoding="utf-8") as f:
            names |= set(json.load(f))
    except (OSError, ValueError) as e:
        print(f"[warn] cricketers.json not read ({e}); only namesakes and squads are known", file=sys.stderr)
    names |= {n for (n,) in db.execute("select name from known_players")}
    other_names.clear()
    for n in names:
        for w in n.lower().split():
            other_names.setdefault(w, set()).add(n)
    common_surnames.clear()
    common_surnames.update(s.lower() for s in cfg.get("common_surnames", []))

def someone_else(t, m, parts):
    """True if this match is part of another cricketer's full name written out in the headline. It can only reject
    words that spell out a different person, so it never hides a story about our player."""
    for other in other_names.get(m.group(0).lower(), ()):
        o = other.split()
        if o[-1].lower() == parts[-1].lower() and same_name(o[0], parts[0]):
            continue                        # a spelling of our own player ("Matt Short" for Matthew Short)
        if any(x.start() <= m.start() < x.end() for x in re.finditer(rf"\b{re.escape(other)}\b", t, re.I)):
            return True
    return False

# Capitalised words that can stand before a surname in a headline without being a first name ("CSK Sign Khan").
HEADLINE_WORDS = {"as", "for", "and", "of", "the", "to", "with", "by", "on", "in", "at", "from", "vs", "before", "why",
                  "how", "what", "when", "is", "was", "will", "can", "sign", "signs", "recall", "recalls", "drop", "drops",
                  "pick", "picks", "named", "names", "hails", "praises", "slams", "backs", "pacer", "skipper", "captain",
                  "uncapped", "young", "veteran", "spinner", "batter", "bowler", "opener", "keeper", "youngster", "seamer",
                  "batsman", "injured"}

def other_first_name(t, m, parts):
    """True if a different first name (or initials) stands right before this surname, and the surname is one many
    people share ("Azam Khan", "D.I. Khan", "Dilpreet Singh" are not our Khan or Singh). Only for those surnames:
    in a Title Case headline every word is capitalised, so for others "Australia Suffer Ellis Blow" would look like
    a man called Suffer Ellis, and a real story would be missed."""
    if parts[-1].lower() not in common_surnames:
        return False
    before = re.search(r"(?:^|\s)([A-Z][a-z]+|(?:[A-Z]\.){1,3})\s+$", t[:m.start()])
    if not before or before.group(1).lower() in HEADLINE_WORDS | NOT_A_PERSON:
        return False
    word = before.group(1)
    return word[0] != parts[0][0] if word.endswith(".") else not same_name(word, parts[0])

def mention(title, name):
    """'about' if the headline is about this player, 'passing' if he is only a yardstick for someone else, else None."""
    t = title.replace("’", "'")
    parts = name.split()
    found = re.search(rf"\b{re.escape(name)}\b", t, re.I)
    if not found and len(parts) > 1:
        found = short_name(t, parts)
    if not found and re.search(CRICKET, t, re.I):     # a nickname counts only in a cricket headline, like a first name
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
    team_page = re.search(r"\bcricket team news(\s*&\s*matches)?$", head, re.I)   # "Chennai Super Kings Cricket Team News & Matches"
    return head != head.lower() and not team_page and not re.search(NOT_ARTICLE, title, re.I)   # all-lowercase = a Cricbuzz team page

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

def story_event(title):
    return next((e for e, p in STORY_EVENTS if re.search(rf"\b({p})\b", title, re.I)), None)

NOT_A_PERSON = {"after", "ipl", "csk", "chennai", "super", "kings", "india", "indian", "star", "former", "ex", "watch",
                "big", "double", "breaking", "report", "reports", "sports", "news", "team", "south", "west", "new", "live"}

def story_person(title, roster_names):
    """The person a CSK team story is about (Mohit Sharma in "Mohit Sharma to join CSK as bowling coach"): the first
    known cricketer or namesake named in it who isn't a CSK player, else a name leading the headline."""
    low = title.lower().replace("’", "'")
    candidates = {n for w in re.findall(r"[\w'.-]+", low) for n in other_names.get(w, ())}
    found = [(low.find(n.lower()), n) for n in candidates
             if n not in roster_names and re.search(rf"\b{re.escape(n.lower())}\b", low)
             and not re.search(rf"\bafter {re.escape(n.lower())}\b", low)     # "After Zaheer Khan, CSK to appoint..."
             and not re.search(rf"\b{re.escape(n.lower())}[- ]led\b", low)]    # "Zaheer Khan-Led Coaching Staff Rope In"
    if found:
        return min(found)[1]
    m = re.match(r"^(?:[^:]{0,25}:\s*)?([A-Z][a-z]+) ([A-Z][a-z]+)\b(?![- ][Ll]ed\b)", title)   # "IPL: Mohit Sharma set to..."
    if m and m.group(1).lower() not in NOT_A_PERSON and m.group(2).lower() not in NOT_A_PERSON:
        return f"{m.group(1)} {m.group(2)}"
    return None

def coach_job(it, tags, roster_names):
    """("CSK", "bowling coach") for a team story about a coaching job, so reports that don't name the person
    ("CSK set to appoint India's 2015 World Cup pacer as bowling coach") are grouped with the ones that do."""
    m = re.search(r"\b(head|bowling|batting|fielding|spin|pace|assistant) coach\b", it["title"], re.I)
    if it.get("official") or not m or any(p in roster_names for p in tags):
        return None
    return ("CSK", f"{m.group(1).lower()} coach")

def story_keys(it, tags, roster_names):
    """What a story is about, to spot other publishers' copies: (CSK player, kind of event) for player stories;
    (person, "any") for team stories about someone else (a coach, a trade target). None: never grouped (the team's
    own posts, player stories of no recognised kind, team stories about no one in particular)."""
    if it.get("official"):
        return None
    players = [p for p in tags if p in roster_names]
    if players:
        ev = story_event(it["title"])
        return [(p, ev) for p in players] if ev else None
    person = story_person(it["title"], roster_names)
    job = coach_job(it, tags, roster_names)
    return [(person, "any")] if person else [job] if job else None

def repeat_story(db, it, tags, imp, roster_names):
    """The same story from another publisher: everyone it's about already had a story of this kind in the last
    24 hours, ranked at least as high."""
    keys = story_keys(it, tags, roster_names)
    if not keys:
        return False
    for who, ev in keys:
        row = db.execute("select rank from story_events where player=? and event=? and ts>=?",
                         (who, ev, time.time() - 86400)).fetchone()
        if not row or RANKS[imp] > row[0]:          # new, or a higher-ranked report ("set to join" -> "joins")
            return False
    return True

def remember_story(db, it, tags, imp, roster_names):
    keys = story_keys(it, tags, roster_names) or []
    job = coach_job(it, tags, roster_names)              # a named report also covers the unnamed ones
    for who, ev in keys + ([job] if job and job not in keys else []):
        row = db.execute("select rank from story_events where player=? and event=? and ts>=?",
                         (who, ev, time.time() - 86400)).fetchone()
        db.execute("insert or replace into story_events values(?,?,?,?)",
                   (who, ev, max(RANKS[imp], row[0]) if row else RANKS[imp], time.time()))
    db.commit()

def process_items(cfg, db, items, roster_names, dry_run, latest, first_run):
    now = datetime.now(IST)
    oldest, fresh_after = now - MAX_NEWS_AGE, now - ALERT_MAX_AGE
    for it in items:
        if (it["when"] and it["when"] < oldest) or not is_article(it["title"]):
            continue
        tags = tag_item(it["title"], roster_names, set(cfg["muted_players"]))
        if it.get("official"):              # the team's own post: always relevant, never a rumour
            tags = [it["official"]] + [x for x in tags if x != "CSK"]
        if not tags:
            continue
        record_news_fixture(db, it, tags, roster_names)     # stories seen before too: each is checked once
        if is_duplicate(db, it["title"]):
            continue
        imp, rumour = classify(it["title"])
        rumour = rumour and not it.get("official")
        mark_seen(db, it["title"], it["when"])
        if it["when"] and it["when"] < fresh_after:     # seen for the first time but over a day old: record only
            continue
        if RANKS[imp] < RANKS[cfg["alert"]["min_importance"]]:
            continue
        if repeat_story(db, it, tags, imp, roster_names):   # another publisher's copy: not sent, not on the dashboard
            continue
        remember_story(db, it, tags, imp, roster_names)
        latest.append({**it, "tags": tags, "importance": imp, "rumour": rumour})   # dashboard = what alerts cover
        db.execute("insert or ignore into news_feed values(?,?,?,?,?,?,?,?,?,?)",   # kept, so a restart can't empty it
                   (hashlib.sha1(norm(it["title"]).encode()).hexdigest(), it["title"], it["link"], it["source"],
                    it.get("summary") or "", it["when"].timestamp() if it["when"] else None, time.time(),
                    json.dumps(tags), imp, int(rumour)))
        db.commit()
        if first_run:                       # don't spam old stories on first start
            continue
        icon = {"breaking": "🚨", "important": "⚠️", "minor": "📰"}[imp]
        flag = " [RUMOUR]" if rumour else ""
        lines = [f"{icon} {imp.upper()}{flag} | {', '.join(tags)}", it["title"], it.get("summary"),
                 f"Published {ist(it['when'])}" if it["when"] else "", it["source"], it["link"]]
        send_alert(cfg, "\n".join(x for x in lines if x), dry_run)

def day_from_words(w, pub):
    """One day expression ("Tuesday", "tomorrow", "30 September") -> a date, counted from the publish date."""
    w, d = w.lower(), pub.astimezone(IST).date()
    if w in ("today", "tonight"):
        return d
    if w == "tomorrow":
        return d + timedelta(days=1)
    if w in WEEKDAYS:
        return d + timedelta(days=(WEEKDAYS.index(w) - d.weekday()) % 7)
    try:
        return d.replace(month=MONTHS.index(re.search(r"[a-z]{3}", w).group()) + 1, day=int(re.search(r"\d+", w).group()))
    except ValueError:
        return None

def news_match_days(text, player, pub):
    """(first day, number of days) of a match the text says this player will play, or None. Only sentences naming
    him count (page sidebars carry other dates), and a day word after the "will play" phrase is preferred."""
    parts = player.split()
    names = [n for n in (parts[-1], parts[0]) if len(n) >= 3 and not n.isupper()]
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    for s in sentences:
        play = re.search(PLAY_PHRASE, s, re.I)
        if len(s) > 500:                    # page menus and datelines without full stops, not an article sentence
            continue
        if not play or not any(re.search(rf"\b{re.escape(n)}\b", s) for n in names):
            continue
        words = list(re.finditer(DAY_WORDS, s, re.I))
        after = [m for m in words if m.start() >= play.start()]
        day = day_from_words((after or words)[0].group(0), pub) if words else None
        if day:
            span = re.search(r"\b(two|three|four|five)-day\b", " ".join(x for x in sentences if re.search(PLAY_PHRASE, x, re.I)), re.I)
            return day, {"two": 2, "three": 3, "four": 4, "five": 5}[span.group(1).lower()] if span else 1
    return None

def article_text(url):
    """Page text with one line per paragraph or block, so a header can't run into the first sentence.
    Some sites (Cricinfo) block one identity or the other at random, so a 403 is retried with the other one."""
    try:
        page = http_get(url)
    except urllib.error.HTTPError as e:
        if e.code != 403:
            raise
        page = http_get(url, ua=BROWSER_UA)
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)</(p|div|h[1-6]|li|td|section|article|header)>|<br\s*/?>", "\n", page)
    return re.sub(r"[^\S\n]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))

def record_news_fixture(db, it, tags, roster_names):
    """Remember a match a story says a CSK player will play: practice games and trials that Cricbuzz doesn't list.
    Each story is checked once; its article is read only if the headline and summary don't give the day (Google News
    links can't be read, so those count on the headline alone)."""
    players = [p for p in tags if p in roster_names or p == "MS Dhoni"]
    text = f"{it['title']}. {it.get('summary') or ''}"
    if not players or not it["when"] or not re.search(PLAY_PHRASE, text, re.I):
        return
    key = hashlib.sha1(norm(it["title"]).encode()).hexdigest()
    if db.execute("select 1 from news_checked where key=?", (key,)).fetchone():
        return
    db.execute("insert into news_checked values(?,?)", (key, time.time()))
    article = None
    for p in players:
        found = news_match_days(text, p, it["when"])
        if not found and "news.google.com" not in it["link"]:
            if article is None:
                try:
                    article = article_text(it["link"])
                except Exception as e:
                    print(f"[warn] article {it['link']} failed: {e}", file=sys.stderr)
                    article = ""
            found = news_match_days(article, p, it["when"])
        if found:
            db.execute("insert or ignore into news_matches values(?,?,?,?,?,?,?,?,?,?)",
                       (p, it["link"], re.sub(r"\s[-|]\s[^-|]+$", "", it["title"]),
                        it["source"] or urllib.parse.urlparse(it["link"]).netloc, it["when"].timestamp(),
                        found[0].isoformat(), found[1], datetime.now(IST).timestamp(), None, "active"))
    db.execute("delete from news_checked where ts < ?", (time.time() - 10 * 86400,))
    db.commit()

def days_text(day, days):
    d = date.fromisoformat(day)
    return f"{int(days)}-day from {d:%a} {d.day} {d:%b}" if int(days) > 1 else f"{d:%a} {d.day} {d:%b}"

def recheck_news_matches(cfg, db, now, dry_run):
    """Articles get updated ("practice match from Tuesday" became "Kanga League game on Friday"), so the article
    behind each news-only match is read again every 3 hours until the match is over. A new day, or no match at all,
    updates the entry; if we had already announced it, a correction is sent."""
    rows = db.execute("select player, link, title, source, pub, day, days, told from news_matches"
                      " where status='active' and checked < ?", (now.timestamp() - 3 * 3600,)).fetchall()
    for player, link, title, source, pub, day, days, told in rows:
        if "news.google.com" in link or date.fromisoformat(day) + timedelta(days=days - 1) < now.date():
            continue
        try:
            found = news_match_days(article_text(link), player, datetime.fromtimestamp(pub, IST))
        except Exception as e:
            print(f"[warn] re-reading {link} failed: {e}", file=sys.stderr)
            continue
        where = (player, link)
        db.execute("update news_matches set checked=? where player=? and link=?", (now.timestamp(), *where))
        new = (found[0].isoformat(), found[1]) if found else None
        if new == (day, days):
            db.commit()
            continue
        if new:
            db.execute("update news_matches set day=?, days=? where player=? and link=?", (*new, *where))
        else:
            db.execute("update news_matches set status='dropped' where player=? and link=?", where)
        if told:                            # we told the user about the old day: correct it
            was = days_text(*told.split("|"))
            if new:
                db.execute("update news_matches set told=? where player=? and link=?", (f"{new[0]}|{new[1]}", *where))
                db.execute("insert or replace into kv values(?,?)", (f"announced:news:{player}:{new[0]}", "1"))
                says = f"The article now says: {days_text(*new)}."
            else:
                says = "The article no longer mentions this match, so ignore the earlier alert."
            send_alert(cfg, f"✏️ CORRECTION: {player}\nEarlier alert said: match per news, {was}.\n{says}\n"
                            f"“{title}” ({source})\n{link}", dry_run)
        db.commit()

def news_fixture_hits(cfg, db, now):
    """Match hits for news-only matches running today or starting tomorrow (see record_news_fixture)."""
    today, hour, out, seen_days = now.date(), cfg["alert"].get("match_digest_hour", 8), [], set()
    for player, day, days, title, link, source in db.execute(
            "select player, day, days, title, link, source from news_matches where status='active' order by pub"):
        first = date.fromisoformat(day)
        if first + timedelta(days=days - 1) < today or first > today + timedelta(days=1) or (player, day) in seen_days:
            continue
        seen_days.add((player, day))        # two stories about the same match: list it once
        shown = max(first, today)
        m = {"id": f"news:{player}:{day}", "phase": "news", "first": first, "days": days,
             "start": datetime(shown.year, shown.month, shown.day, hour, tzinfo=IST),   # held for the digest
             "title": title, "teams": [], "format": "", "desc": "per news, not an official fixture",
             "series": source, "state": "", "status": "", "url": link, "series_id": ""}
        out.append({"player": player, "team": "", "opponent": "", "role": "news", "match": m})
    return out

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
    remember_players(db, players)
    db.execute("delete from match_squads where ts < ?", (time.time() - 7 * 86400,)); db.commit()
    return players

def series_matches(cfg, db, sid):
    """A series' matches ({id, phase, start}), cached for 30 minutes."""
    row = db.execute("select ts, matches from series_cache where sid=?", (sid,)).fetchone()
    if row and time.time() - row[0] < 1800:
        return [{**x, "start": datetime.fromtimestamp(x["start"], IST) if x["start"] else None} for x in json.loads(row[1])]
    found = [x for x in parse_live_scores(http_get(cfg["series_matches_url"].format(sid=sid))) if x["series_id"] == sid]
    db.execute("insert or replace into series_cache values(?,?,?)", (sid, time.time(), json.dumps(
        [{"id": x["id"], "state": x["state"], "phase": x["phase"], "start": x["start"].timestamp() if x["start"] else None}
         for x in found])))
    db.commit()
    return [{"id": x["id"], "state": x["state"], "phase": x["phase"], "start": x["start"]} for x in found]

def expected_squads(cfg, db, m):
    """Squad not published yet: take each team's squad from its latest started match in the same series
    (e.g. the 1st ODI squad for the 2nd ODI). These players are marked "expected"."""
    wanted = {short for _, short in m["teams"] if short}
    earlier = [x for x in series_matches(cfg, db, m["series_id"])
               if x["id"] != m["id"] and x["phase"] in ("live", "paused", "finished")
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
                hits.append({"player": p["name"], "id": str(p.get("id", "")), "team": team, "team_short": e["team"],
                             "opponent": opponent, "role": role, "match": m})
    return hits

ROLE_TEXT = {"xi": "playing XI", "squad": "in squad", "bench": "on bench", "expected": "expected, squad not out yet",
             "news": "per news"}

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
    if m["phase"] == "news":
        return "soon", f"PLAYING {day_label(m['start'], now)}, per news (not an official fixture)"
    if m["phase"] == "paused":
        return "off", f"{m['state']}: {m['status']}"
    return "off", f"Cricbuzz state '{m['state']}': {m['status']}"

def match_block(hs):
    """Lines for one match in a digest or alert: teams, format, start time, then the CSK players in it."""
    m = hs[0]["match"]
    if m["phase"] == "news":                # a match only the news mentions: no teams or time, show the story
        span = f", {m['days']}-day match from {m['first']:%a %d %b}" if m["days"] > 1 else ""
        lines = [f"• Match per news, not an official fixture{span}"] + [f"   {h['player']}" for h in hs]
        return "\n".join(lines + [f"   “{m['title']}” ({m['series']})\n   {m['url']}"])
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

def track_match(db, h):
    """Remember that we told the user this CSK player is in this match, so its scorecard is followed."""
    m = h["match"]
    row = db.execute("select players from tracked_matches where match_id=?", (m["id"],)).fetchone()
    players = json.loads(row[0]) if row else {}
    players[h["id"]] = {"name": h["player"], "team": h["team"]}
    db.execute("insert into tracked_matches(match_id, title, players, start, last_poll, last_text, final, added)"
               " values(?,?,?,?,null,'',0,?) on conflict(match_id) do update set players=excluded.players",
               (m["id"], f"{m['title']} · {m['format']}" if m.get("format") else m["title"], json.dumps(players),
                m["start"].timestamp() if m["start"] else None, time.time()))

def correct_expected(cfg, db, raw, dry_run):
    """A player we announced as "expected" (from the previous match's squad) gets a correction if his team's squad
    is published without him (e.g. Nathan Ellis, injured before the 3rd ODI)."""
    for k, v in db.execute("select k, v from kv where k like 'expected:%'").fetchall():
        _, mid, pid = k.split(":", 2)
        e = json.loads(v)
        sq = raw.get(mid)
        if not sq or e["team_short"] not in {x["team"] for x in sq.values()}:     # his team's squad isn't out yet
            if time.time() > e["start"] + 2 * 86400:
                db.execute("delete from kv where k=?", (k,))
            continue
        db.execute("delete from kv where k=?", (k,))
        if pid not in sq:
            send_alert(cfg, f"✏️ CORRECTION: {e['name']}\nEarlier alert said: expected for {e['title']} ({e['team']}), "
                            f"starts {e['when']}.\nThe squad is now published and doesn't include him.\n{e['url']}",
                       dry_run)
    db.commit()

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
    if not live and not scheduled:          # carry on: matches found in the news still count
        print("[warn] no match data on Cricbuzz; its page format may have changed", file=sys.stderr)
    now = datetime.now(IST)
    matches = {m["id"]: m for m in scheduled if m["start"] and m["start"] > now}   # started ones: trust live scores
    matches.update({m["id"]: m for m in live})
    squads, raw = {}, {}                    # raw: what Cricbuzz published, before the "expected" fallback
    for m in matches.values():
        if m["phase"] == "unknown":
            print(f"[warn] unknown Cricbuzz match state '{m['state']}' ({m['title']}); not treated as live", file=sys.stderr)
        soon = m["phase"] == "upcoming" and m["start"] and (m["start"].date() - now.date()).days in (0, 1)
        if soon or m["phase"] in ("live", "paused", "unknown"):
            try:
                squads[m["id"]] = raw[m["id"]] = get_squads(cfg, db, m)
                if not squads[m["id"]] and soon and m["series_id"]:
                    squads[m["id"]] = expected_squads(cfg, db, m)
            except Exception as e:
                print(f"[warn] squads for {m['title']} failed: {e}", file=sys.stderr)
    hits = players_in_matches(list(matches.values()), roster, squads)
    for h in hits:                          # before the XI is out, flag recent injury news (the XI itself is definitive)
        if h["role"] in ("squad", "expected"):
            h["injury"] = injury_news(db, h["player"])
    correct_expected(cfg, db, raw, dry_run)          # an "expected" player missing from the published squad
    recheck_news_matches(cfg, db, now, dry_run)     # articles change; correct what we already announced
    hits += news_fixture_hits(cfg, db, now)         # practice games, trials: matches only the news mentions
    kv = lambda k: db.execute("select 1 from kv where k=?", (k,)).fetchone()
    mark = lambda k: db.execute("insert or replace into kv values(?,?)", (k, str(time.time())))
    def announce(h):
        mark(f"announced:{h['match']['id']}")
        m = h["match"]
        if m["phase"] == "news":            # remember what we told, so a later change in the article is corrected
            db.execute("update news_matches set told=? where player=? and day=? and status='active'",
                       (f"{m['first'].isoformat()}|{m['days']}", h["player"], m["first"].isoformat()))
            return
        track_match(db, h)                  # follow its scorecard for performance updates
        if h["role"] == "expected":         # so a squad published without him gets a correction
            db.execute("insert or replace into kv values(?,?)", (f"expected:{m['id']}:{h['id']}", json.dumps(
                {"name": h["player"], "team": h["team"], "team_short": h["team_short"], "title": m["title"],
                 "when": ist(m["start"]), "url": m["url"], "start": m["start"].timestamp(), "ts": time.time()})))

    # 1. PLAYING NOW: once per match, when it is live and a CSK player is in the XI (or the squad, before the XI is out).
    live_hits = {}
    for h in hits:
        if h["match"]["phase"] in ("live", "paused") and h["role"] not in ("bench", "news"):
            track_match(db, h)              # every run, so matches announced before this existed are followed too
        if h["match"]["phase"] == "live" and h["role"] != "bench":
            live_hits.setdefault(h["match"]["id"], []).append(h)
    db.commit()
    for mid, hs in live_hits.items():
        if kv(f"alert:live:{mid}"):
            continue
        mark(f"alert:live:{mid}")
        db.commit()
        m = hs[0]["match"]
        who = ", ".join(f"{h['player']} ({h['team']}, {ROLE_TEXT[h['role']]})" for h in hs)
        note = " (playing XI not published yet)" if any(h["role"] != "xi" for h in hs) else ""
        send_alert(cfg, f"🏏 PLAYING NOW: {who}\nStarted {ist(m['start'])}{note}\n{m['title']} · {m['format']}\n"
                        f"{m['series']}\n{m['status']}\n{m['url']}", dry_run)

    # 2. Morning digest of today's and tomorrow's matches, then an alert for any match found after it.
    coming = [h for h in hits if h["role"] != "bench" and h["match"]["start"]
              and (h["match"]["phase"] in ("live", "paused", "news")
                   or (h["match"]["phase"] == "upcoming" and (h["match"]["start"].date() - now.date()).days in (0, 1)))]
    hour = cfg["alert"].get("match_digest_hour", 8)
    digest_at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now >= digest_at and not kv(f"digest:{now:%Y-%m-%d}"):
        mark(f"digest:{now:%Y-%m-%d}")
        for h in coming:
            announce(h)
        db.commit()
        if coming:
            send_alert(cfg, "📅 CSK PLAYERS' MATCHES\n\n" + day_sections(coming, now), dry_run)
        else:
            send_alert(cfg, "📅 No CSK player has a match today or tomorrow.", dry_run, silent=True)
        return hits
    digest_done = now >= digest_at                          # before 8 AM: hold matches the digest will cover
    new = [h for h in coming if h["match"]["phase"] in ("upcoming", "news")   # live ones already got PLAYING NOW
           and not kv(f"announced:{h['match']['id']}") and (digest_done or h["match"]["start"] < digest_at)]
    if new:
        for h in new:
            announce(h)
        db.commit()
        send_alert(cfg, "📅 NEW MATCH FOR CSK PLAYERS\n\n" + day_sections(new, now), dry_run)
    return hits

# ---------- player performance ----------
def parse_scorecard(page):
    """Cricbuzz scorecard page -> (matchHeader, innings). Each innings has batTeamDetails.batsmenData (runs, balls,
    fours, sixes, outDesc, wicketCode, bowlerId, fielderId1-3) and bowlTeamDetails.bowlersData (overs, maidens,
    runs, wickets), keyed by Cricbuzz player id."""
    data = next_data(page)
    header = next((o for o in json_objects(data, "matchHeader") if isinstance(o, dict)), {})
    innings = next((o for o in json_objects(data, "scoreCard")
                    if isinstance(o, list) and o and isinstance(o[0], dict) and "batTeamDetails" in o[0]), [])
    return header, innings

def player_figures(innings, pid):
    """One player's batting, bowling and fielding in a match. Catches: CAUGHT with him as fielder, or CAUGHTBOWLED
    off his bowling; stumpings: STUMPED with him as keeper; run outs: RUNOUT with him among the fielders."""
    pid, f = int(pid), {"bat": [], "bowl": [], "catches": 0, "stumpings": 0, "runouts": 0, "played": False}
    for inn in innings:
        for b in ((inn.get("batTeamDetails") or {}).get("batsmenData") or {}).values():
            if b.get("batId") == pid:
                f["played"] = True                              # in the XI (players yet to bat are listed too)
                if b.get("balls") or b.get("outDesc"):
                    f["bat"].append(b)
            code = b.get("wicketCode", "")
            if (code == "CAUGHT" and b.get("fielderId1") == pid) or (code == "CAUGHTBOWLED" and b.get("bowlerId") == pid):
                f["catches"] += 1
            elif code == "STUMPED" and b.get("fielderId1") == pid:
                f["stumpings"] += 1
            elif code == "RUNOUT" and pid in (b.get("fielderId1"), b.get("fielderId2"), b.get("fielderId3")):
                f["runouts"] += 1
        for w in ((inn.get("bowlTeamDetails") or {}).get("bowlersData") or {}).values():
            if w.get("bowlerId") == pid:
                f["played"] = True
                f["bowl"].append(w)
    f["active"] = bool(f["bat"] or f["bowl"] or f["catches"] or f["stumpings"] or f["runouts"])
    f["played"] = f["played"] or f["active"]
    return f

def count(n, one, many=None):
    return f"{n:g} {one if n == 1 else many or one + 's'}"

def not_out(b):
    return b.get("outDesc", "") in ("", "not out", "batting")

def figures_text(f, final):
    """Batting (runs, balls, fours, sixes), bowling (overs, maidens, runs, wickets), fielding (catches, stumpings)."""
    inns = lambda i, n: f"{('1st', '2nd', '3rd', '4th')[i]} inns: " if n > 1 else ""
    bat = "; ".join(f"{inns(i, len(f['bat']))}{b['runs']} runs ({count(b['balls'], 'ball')}, {count(b['fours'], 'four')}, "
                    f"{count(b['sixes'], 'six', 'sixes')}), {'not out' if not_out(b) else b['outDesc']}"
                    for i, b in enumerate(f["bat"]))
    bowl = "; ".join(f"{inns(i, len(f['bowl']))}{count(w['overs'], 'over')}, {count(w['maidens'], 'maiden')}, "
                     f"{count(w['runs'], 'run')}, {count(w['wickets'], 'wicket')}" for i, w in enumerate(f["bowl"]))
    field = f"{count(f['catches'], 'catch', 'catches')}, {count(f['stumpings'], 'stumping')}"
    if f["runouts"]:
        field += f", {count(f['runouts'], 'run out')}"
    return [f"Batting: {bat or ('did not bat' if final else 'yet to bat')}",
            f"Bowling: {bowl or ('did not bowl' if final else 'none yet')}", f"Fielding: {field}"]

def figures_short(f):
    """Compact line for the dashboard: 13* (19b, 0x4, 0x6) · 4-0-29-1 · 1 st"""
    bits = [f"{b['runs']}{'*' if not_out(b) else ''} ({b['balls']}b, {b['fours']}x4, {b['sixes']}x6)" for b in f["bat"]]
    bits += [f"{w['overs']:g}-{w['maidens']}-{w['runs']}-{w['wickets']}" for w in f["bowl"]]
    field = ", ".join(x for x in (f"{f['catches']} ct" if f["catches"] else "", f"{f['stumpings']} st" if f["stumpings"] else "",
                                  f"{f['runouts']} ro" if f["runouts"] else "") if x)
    return " · ".join(bits + ([field] if field else [])) or "in the XI, no batting or bowling yet"

def poll_performances(cfg, db, dry_run):
    """Scorecard figures for the CSK players we said are playing: an update every hour while the match is on
    (only when a figure changed), and a final summary once it's over."""
    every, now = cfg["intervals_seconds"].get("performance", 3600), time.time()
    for mid, title, players, start, last_poll, last_text in db.execute(
            "select match_id, title, players, start, last_poll, last_text from tracked_matches where final=0").fetchall():
        if (start and now < start) or (last_poll and now - last_poll < every - 60):
            continue
        if start and now > start + 6 * 86400:                  # never saw it finish: stop following it
            db.execute("update tracked_matches set final=1 where match_id=?", (mid,))
            continue
        url = cfg["scorecard_url"].format(id=mid)
        try:
            header, innings = parse_scorecard(http_get(url))
        except Exception as e:
            print(f"[warn] scorecard {mid} failed: {e}", file=sys.stderr)
            continue
        db.execute("update tracked_matches set last_poll=? where match_id=?", (now, mid))
        phase = match_phase(header.get("state"))
        if phase not in ("live", "paused", "finished"):
            db.commit()
            continue
        final, blocks, active = phase == "finished", [], False
        for pid, p in json.loads(players).items():
            f = player_figures(innings, pid)
            if not f["played"]:
                if final and innings:
                    blocks.append(f"• {p['name']} ({p['team']}): did not play")
                continue
            active = active or f["active"]
            blocks.append(f"• {p['name']} ({p['team']})\n   " + "\n   ".join(figures_text(f, final)))
            db.execute("insert or replace into performances values(?,?,?,?,?)",
                       (mid, p["name"], figures_short(f), int(final), now))
        text = "\n".join(blocks) if innings else "No play in this match."
        status = header.get("status", "")
        if final:
            send_alert(cfg, f"📊 FINAL: {title}\n{status}\n\n{text}\n{url}", dry_run)
            db.execute("update tracked_matches set final=1, last_text=? where match_id=?", (text, mid))
        elif active and text != last_text:
            send_alert(cfg, f"📊 LIVE UPDATE: {title}\n{status}\n\n{text}\n{url}", dry_run)
            db.execute("update tracked_matches set last_text=? where match_id=?", (text, mid))
        db.commit()

def prune_state(db):
    """Keep state.db small: forget what's no longer needed. Runs once a day."""
    now, day = time.time(), 86400
    db.execute("delete from seen where ts < ?", (now - 14 * day,))
    db.execute("delete from kv where (k like 'alert:%' or k like 'announced:%' or k like 'digest:%')"
               " and cast(v as real) > 1e9 and cast(v as real) < ?", (now - 14 * day,))   # old '1' values are kept
    db.execute("delete from story_events where ts < ?", (now - 2 * day,))
    db.execute("delete from news_feed where seen < ?", (now - 3 * day,))
    db.execute("delete from news_matches where status != 'active' or day < ?",
               ((datetime.now(IST) - timedelta(days=14)).date().isoformat(),))
    db.execute("delete from tracked_matches where added < ?", (now - 10 * day,))
    db.execute("delete from performances where ts < ?", (now - 3 * day,))
    db.execute("delete from series_cache where ts < ?", (now - day,))
    db.execute("delete from outbox where created < ?", (now - day,))
    db.commit()

# ---------- dashboard ----------
def write_dashboard(roster, hits, db, path=None, min_importance="minor", muted=()):
    names = [p["name"] for p in roster]
    order = {"live": 0, "soon": 1, "off": 2}
    best = {}                               # player -> most relevant (class, text, hit)
    for h in hits:
        cls, text = describe(h)
        if h["player"] not in best or order[cls] < order[best[h["player"]][0]]:
            best[h["player"]] = (cls, text, h)
    perf = {}                               # player -> (latest scorecard line, final?) from the last 24 hours
    for player, line, final in db.execute("select player, line, final from performances where ts >= ? order by ts",
                                          (time.time() - 86400,)):
        perf[player] = (line, final)
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
        if n in perf:
            badge += f'<br><small>📊 {"Final" if perf[n][1] else "Live"}: {html.escape(perf[n][0])}</small>'
        rows += f'<tr class="{cls}"><td>{html.escape(n)}</td><td>{badge}</td></tr>'
    news = ""
    day_ago = datetime.now(IST) - ALERT_MAX_AGE     # stories drop off after 24 h, like the alerts
    recent = []
    for title, link, summary, pub, seen, tags, imp, rumour in db.execute(
            "select title, link, summary, pub, seen, tags, importance, rumour from news_feed"):
        when, tags = datetime.fromtimestamp(pub or seen, IST), json.loads(tags)
        if not any("(official)" in x for x in tags):    # tagged again with today's rules, so a story a later fix
            tags = tag_item(title, names, set(muted))   # no longer tags ("Azam Khan" as our Khan) drops off
        if tags and when >= day_ago and RANKS[imp] >= RANKS[min_importance]:
            recent.append({"title": title, "link": link, "summary": summary, "tags": tags, "importance": imp,
                           "rumour": bool(rumour), "when": when})
    newest_first = sorted(recent, key=lambda i: (RANKS[i["importance"]], i["when"] or datetime.min.replace(tzinfo=IST)), reverse=True)
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
<h2>News, last 24 hours</h2><ul>{news or "<li>Nothing new yet.</li>"}</ul>"""
    with open(path or os.path.join(HERE, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(page)

# ---------- cricketer names ----------
def refresh_cricketers(cfg, path=None, pause=0.25):
    """Write cricketers.json: every cricketer Cricbuzz lists, so a headline naming someone else ("Shadab Khan") isn't
    tagged as our player. Sources: the team pages (international and major teams; most others list nobody) and one
    squad page per team in every series of this year and last. About 2,000 pages and 30+ minutes, so it is run by
    hand now and then (python tracker.py --refresh-names); squads read on match days add new names in between.
    Names are only ever added: a retired player is still someone else, and a page that failed this time was read before."""
    names = {}
    def add(pid, name):
        if " " in name.strip():             # a single name ("Ashraful") can't spell out anyone in a headline
            names[pid] = name.strip()
    teams = set()
    for url in cfg["team_index_urls"]:
        teams |= set(re.findall(r"/cricket-team/([a-z0-9-]+)/(\d+)", http_get(url)))
    for slug, tid in sorted(teams):
        try:
            for p in parse_roster_page(http_get(cfg["team_players_url"].format(slug=slug, id=tid))):
                add(p["id"], p["name"])
        except Exception as e:
            print(f"[warn] team {slug}: {e}", file=sys.stderr)
        time.sleep(pause)
    series = set()
    year = datetime.now(IST).year
    for y in (year - 1, year):              # the season archive lists every series, not only the IPL
        series |= set(re.findall(r"/cricket-series/(\d+)/", http_get(cfg["ipl_archive_url"].format(year=y))))
    for sid in sorted(series, key=int):
        try:
            matches = [m for m in parse_live_scores(http_get(cfg["series_matches_url"].format(sid=sid)))
                       if m["series_id"] == sid]
        except Exception as e:
            print(f"[warn] series {sid}: {e}", file=sys.stderr)
            continue
        todo = {team for m in matches for team, _ in m["teams"]}
        started = lambda m: m["phase"] in ("live", "paused", "finished")    # their squads are published
        for m in sorted(matches, key=lambda m: not started(m)):
            if not todo & {team for team, _ in m["teams"]}:
                continue
            time.sleep(pause)
            try:
                squads = parse_squads(http_get(cfg["squads_url"].format(id=m["id"])))
            except Exception as e:
                print(f"[warn] squads {m['id']}: {e}", file=sys.stderr)
                continue
            for pid, p in squads.items():
                add(pid, p["name"])
            if squads:
                todo -= {team for team, _ in m["teams"]}
    path, found = path or os.path.join(HERE, "cricketers.json"), set(names.values())
    try:
        with open(path, encoding="utf-8") as f:
            old = set(json.load(f))
    except (OSError, ValueError):
        old = set()
    if len(found) < len(old) / 2:
        print(f"[warn] only {len(found)} names found on Cricbuzz (list has {len(old)}): did its pages change?",
              file=sys.stderr)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(old | found), f, ensure_ascii=False, indent=0)
    return len(old | found)

# ---------- main loop ----------
def run_cycle(cfg, db, state, dry_run, first_run):
    global outbox_db
    now = time.time(); iv = cfg["intervals_seconds"]; sent_before = alerts_sent
    outbox_db = db                          # a Telegram message that fails waits in state.db for the next run
    if not dry_run:
        flush_outbox(cfg, db)
    # Last-run times live in state.db, so separate --once runs (GitHub Actions) also slow down roster and player news.
    last = {k: float(v) for k, v in db.execute("select k, v from kv where k like 'last:%'")}
    due = lambda job, every: now - last.get(f"last:{job}", 0) >= every - 60   # 60 s slack for cron jitter
    def done(job):
        db.execute("insert or replace into kv values(?,?)", (f"last:{job}", str(now))); db.commit()
    if due("roster", iv["roster"]):
        state["roster"] = refresh_roster(cfg, db, dry_run, first_run); done("roster")
        prune_state(db)                     # daily, with the roster
    elif "roster" not in state:
        state["roster"] = [{"id": i, "name": n} for i, n in db.execute("select id, name from squad")] or cfg["roster"]
    roster = state["roster"]
    names = [p["name"] for p in roster]
    set_other_names(cfg, db, names)
    if due("news", iv["news"]):
        pn = due("player_news", iv["player_news"])
        process_items(cfg, db, fetch_news(cfg, names, pn), names, dry_run, [], first_run)
        done("news")
        if pn: done("player_news")
    hits = check_matches(cfg, db, roster, dry_run)
    poll_performances(cfg, db, dry_run)     # scorecards of matches we told the user about
    write_dashboard(roster, hits, db, min_importance=cfg["alert"]["min_importance"], muted=cfg["muted_players"])
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
    ap.add_argument("--refresh-names", action="store_true", help="rebuild cricketers.json from Cricbuzz (30+ min)")
    a = ap.parse_args()
    cfg = load_config()
    if a.refresh_names:
        print(f"cricketers.json: {refresh_cricketers(cfg)} names")
        return
    db = db_connect(); state = {}
    fresh_db = not db.execute("select 1 from seen limit 1").fetchone()
    first = fresh_db and not a.no_warmup
    while True:
        hits = run_cycle(cfg, db, state, a.dry_run, first); first = False
        if a.once: break
        live = any(h["match"]["phase"] == "live" for h in hits)
        time.sleep(cfg["intervals_seconds"]["match_day_live"] if live else 60)

if __name__ == "__main__":
    main()
