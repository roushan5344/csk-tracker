"""Offline test using real data captured from Google News and Cricbuzz on 29 Sep 2026 (no network).
Run: python test_offline.py"""
import tracker as t, tempfile, os, json
from datetime import datetime, timedelta
from email.utils import format_datetime

tmp = tempfile.mkdtemp()
cfg = t.load_config()
roster = cfg["roster"]
names = [p["name"] for p in roster]
sent = []
t.send_alert = lambda c, text, d=False: sent.append(text)

# ---------- news ----------
def rss(titles, when=None):
    date = format_datetime(when or datetime.now(t.IST))
    return "<rss><channel>" + "".join(
        f"<item><title>{t.html.escape(x)}</title><link>http://x/{i}</link><pubDate>{date}</pubDate></item>"
        for i, x in enumerate(titles)) + "</channel></rss>"

# Real headlines. The first group must be kept; the second must be dropped.
KEEP = {
    "South Africa announce 18-member squad for 1st Test vs Australia: Brevis dropped, 3 injured pacers unavailable - SportsTak":
        ("important", ["Dewald Brevis"]),
    "Back from hamstring injury, Ayush Mhatre to play his first competitive match in 5 months - The Times of India":
        ("important", ["Ayush Mhatre"]),
    "Nathan Ellis suffered a side issue - Cricinfo": ("important", ["Nathan Ellis"]),
    "England v Sri Lanka Third ODI - Asitha Fernando is caught by Jamie Overton - BBC": ("minor", ["Jamie Overton"]),
    "Rob Walter on preparing Zak Foulkes to play an allrounder role with the Blackcaps | Cricket Nation - Sport Nation":
        ("minor", ["Zakary Foulkes"]),
    "Gaikwad At 4, Jadeja & Kuldeep Return: India's Likely Playing XI For 1st ODI Against West Indies - News18":
        ("important", ["Ruturaj Gaikwad"]),
    "Moeen Ali Reveals Why MS Dhoni and Stephen Fleming Made CSK the “Dream Combo” - IceCric News":
        ("minor", ["CSK", "MS Dhoni"]),
}
DROP = [
    "chennai super kings - Cricbuzz",                                               # team page, not news
    "CSK vs SRH, 63rd Match, Indian Premier League 2026 - Squads - Cricbuzz",       # match page, not news
    "IND vs WI: Shubman Gill slams 10th ODI century, joins MS Dhoni in elite list - The Times of India",
    "Temba Bavuma equals MS Dhoni's record with fantastic century vs Australia in 2nd ODI - Cricket Addictor",
    "David Miller equals ODI middle-order century record with stunning 142; surpasses MS Dhoni - SportsTak",
    "Is Dhruv Jurel the Next MS Dhoni? India's Wicketkeeper Succession Explained - The Sports Legends",
    "‘Dhoni’s 2011 six inspired me to take up cricket,’ says Puri Titans’ Aashirwad Swain - Social News XYZ",
    "Star player from MS Dhoni’s T20 World Cup winning team applies for selector's job - India.com",
    "Virat Kohli Rapid Fire: Rohit, Dhoni, Bumrah, Steyn Named - TechnoSports Media Group",
    "\"He Will Always Be My Captain\": Virat Kohli Shares His Feelings Towards MS Dhoni - Sportscape Magazine",
    "Rhythm, control and a Kuldeep Yadav masterclass - Cricbuzz",                   # not Kuldip Yadav
    "Who have scored the most ODI centuries while batting at no. 5 or lower? | Miller recorded career-best score of 142 vs Aus | Inshorts - Inshorts",
    "SHOCKING: Dhoni viral video breaks the internet",
    "Unrelated news about football",
]
# The surname rule must reject other people with a tracked player's surname.
assert t.tag_item("Rhythm, control and a Kuldeep Yadav masterclass", ["Kuldip Yadav"], set()) == []
assert t.tag_item("Cox and Overton collide", names, set()) == ["Jamie Overton"]

db = t.db_connect(os.path.join(tmp, "state.db"))
latest = []
t.process_items(cfg, db, t.parse_rss(rss(list(KEEP) + DROP)), names, True, latest, first_run=False)
got = {i["title"]: (i["importance"], i["tags"]) for i in latest}
assert got == KEEP, "\n".join(f"{v}  {k}" for k, v in got.items())
print("news relevance OK:", len(KEEP), "kept,", len(DROP), "dropped")

latest2 = []
t.process_items(cfg, db, t.parse_rss(rss([
    "MS Dhoni retires from IPL, CSK confirm - Cricbuzz",
    "MS Dhoni retirement from IPL: CSK confirm - ESPN",                              # same story
    "Sanju Samson reportedly set to be named CSK vice-captain",
    "Noor Ahmad picks five-for in Afghanistan win"]))
    + t.parse_rss(rss(["Shivam Dube hits fifty"], datetime.now(t.IST) - timedelta(days=10))),   # too old
    names, True, latest2, first_run=False)
assert [i["title"][:12] for i in latest2] == ["MS Dhoni ret", "Sanju Samson", "Noor Ahmad p"], latest2
assert latest2[0]["importance"] == "breaking" and not latest2[0]["rumour"]
assert latest2[1]["rumour"] and latest2[1]["importance"] != "breaking"
assert latest2[2]["tags"] == ["Noor Ahmad"]
print("dedupe / rumour / age OK")
assert "\nPublished " in sent[0] and sent[0].endswith("http://x/0"), sent[0]

# One-line summaries: real Times of India description (kept) and Google News one (only repeats the headline).
items = t.parse_rss(
    "<rss><channel><item><title>India through to Asian Games semis as rain washes out quarterfinal</title><description>"
    "&lt;img border=\"0\" src=\"https://timesofindia.indiatimes.com/photo/134532295.cms\" /&gt;In a dramatic turn of "
    "events at the 2026 Asian Games, India's men's cricket team has reached the semi-finals after their quarter-final "
    "against Hong Kong, China was called off due to rain. Set to clash</description></item>"
    "<item><title>chennai super kings - cricbuzz.com</title><source>cricbuzz.com</source><description>"
    "&lt;a href=\"https://news.google.com/rss/articles/CBMi\"&gt;chennai super kings&lt;/a&gt;&amp;nbsp;&amp;nbsp;"
    "&lt;font color=\"#6f6f6f\"&gt;cricbuzz.com&lt;/font&gt;</description></item></channel></rss>")
assert items[0]["summary"].startswith("In a dramatic turn of events") and len(items[0]["summary"]) <= 180
assert "Set to clash" not in items[0]["summary"] and items[1]["summary"] == ""
print("summaries OK")

# ---------- roster ----------
ROSTER_PAGE = ('<a href="/profiles/265/ms-dhoni" title="MS Dhoni"><div>x</div></a>'
               '<a href="/profiles/24391/zakary-foulkes" title="Zakary Foulkes"><div>x</div></a>'
               '<a href="/profiles/265/ms-dhoni" title="MS Dhoni"></a>')
assert t.parse_roster_page(ROSTER_PAGE) == [{"id": "265", "name": "MS Dhoni"}, {"id": "24391", "name": "Zakary Foulkes"}]
print("roster parsing OK")

# ---------- matches ----------
def next_page(*objs):
    """How Cricbuzz (Next.js) embeds data: JSON text inside self.__next_f.push([1,"..."]) script tags."""
    return "".join('<script>self.__next_f.push([1,%s])</script>' % json.dumps(json.dumps(o, separators=(",", ":")))
                   for o in objs)

def info(mid, series, desc, fmt, start, state, status, t1, t2):
    """t1/t2 are "Full name/SHORT"; squad pages use the short name."""
    team = lambda x: dict(zip(("teamName", "teamSName"), x.split("/")))
    return {"matchInfo": {"matchId": mid, "seriesName": series, "matchDesc": desc, "matchFormat": fmt, "startDate": start,
                          "state": state, "status": status, "team1": team(t1), "team2": team(t2)}}

LIVE_PAGE = next_page(   # real matchInfo values from the live-scores page, 29 Sep 2026 01:44 IST
    info(151532, "West Indies tour of India, 2026", "1st ODI", "ODI", 1790497800000, "Complete", "India won by 8 wkts",
         "West Indies/WI", "India/IND"),
    info(155499, "Australia U19 tour of India 2026", "1st unofficial Test", "TEST", 1790481600000, "Stumps",
         "Day 2: Stumps - India U19 lead by 213 runs", "Australia U19/AUSU19", "India U19/INDU19"),
    info(155422, "Australia A tour of India 2026", "2nd unofficial Test", "TEST", "1790654400000", "Preview",
         "Match starts at Sep 29, 04:00 GMT", "India A/INDA", "Australia A/AUSA"),
    info(171040, "Asian Games 2026", "3rd Quarter-Final", "T20", "1790640000000", "Preview",
         "Match starts at Sep 29, 00:00 GMT", "Sri Lanka/SL", "Nepal/NEP"),
    # Not seen live yet: an in-play match and a state name we don't know.
    info(900001, "SA20 2027", "5th Match", "T20", 1790497800000, "In Progress", "Paarl Royals opt to bat",
         "Paarl Royals/PR", "MI Cape Town/MICT"),
    info(900002, "Test league", "1st Match", "TEST", 1790497800000, "Weird State", "?", "Team A/A", "Team B/B"),
) + '<a href="/live-cricket-scores/151532/wi-vs-ind-1st-odi-west-indies-tour-of-india-2026">x</a>'

def squads(team, group, players):
    return {"players": {group: [{"id": i, "name": n, "teamName": team} for i, n in players],
                        "support staff": [{"id": 308, "name": "Gautam Gambhir", "teamName": team}]}}

SQUAD_PAGES = {   # real squads: only Ruturaj played the WI ODI; Kamboj is in India A's squad (pre-toss)
    "151532": next_page(squads("IND", "playing XI", [(576, "Rohit Sharma"), (11813, "Ruturaj Gaikwad")]),
                        squads("IND", "bench", [(13940, "Yashasvi Jaiswal")]),
                        squads("WI", "playing XI", [(8431, "John Campbell")])),
    "155422": next_page(squads("INDA", "Squad", [(13088, "Devdutt Padikkal"), (14598, "Anshul Kamboj")])),
    "171040": next_page(squads("SL", "Squad", [(1, "Someone Else")])),
    "155499": next_page(squads("INDU19", "playing XI", [(1431163, "Ayush Mhatre")])),         # made up
    "900001": next_page(squads("PR", "playing XI", [(20538, "Dewald Brevis")]),
                        squads("MICT", "bench", [(15452, "Noor Ahmad")])),                     # made up
    "900002": next_page(squads("A", "playing XI", [(8271, "Sanju Samson")])),                  # made up
}

matches = {m["id"]: m for m in t.parse_live_scores(LIVE_PAGE)}
assert {k: m["phase"] for k, m in matches.items()} == {"151532": "finished", "155499": "paused", "155422": "upcoming",
                                                        "171040": "upcoming", "900001": "live", "900002": "unknown"}
assert t.ist(matches["151532"]["start"]) == "27 Sep 14:00 IST" and t.ist(matches["155422"]["start"]) == "29 Sep 09:30 IST"
assert matches["151532"]["url"].endswith("/151532/wi-vs-ind-1st-odi-west-indies-tour-of-india-2026")
assert t.parse_squads(SQUAD_PAGES["151532"])["11813"] == {"name": "Ruturaj Gaikwad", "team": "IND", "group": "playing XI"}

sq = {k: t.parse_squads(v) for k, v in SQUAD_PAGES.items()}
hits = t.players_in_matches(list(matches.values()), roster, sq)
by = {(h["player"], h["match"]["id"]): h for h in hits}
assert set(by) == {("Ruturaj Gaikwad", "151532"), ("Anshul Kamboj", "155422"), ("Ayush Mhatre", "155499"),
                   ("Dewald Brevis", "900001"), ("Noor Ahmad", "900001"), ("Sanju Samson", "900002")}, set(by)
now_playing = {h["player"] for h in hits if t.describe(h)[1].startswith("PLAYING NOW")}
assert now_playing == {"Dewald Brevis"}, now_playing          # finished, stumps, bench, unknown state: not playing now
assert t.describe(by[("Anshul Kamboj", "155422")])[1] == "PLAYING TODAY, starts 29 Sep 09:30 IST, in squad"
k = by[("Anshul Kamboj", "155422")]
assert (k["team"], k["opponent"], k["match"]["format"]) == ("India A", "Australia A", "TEST")
print("match states, IST times and squad mapping OK")

# check_matches end to end with fake HTTP, pretending it is 29 Sep 2026 01:44 IST
real_now, sent[:] = t.datetime, []
class FakeNow(datetime):
    @classmethod
    def now(cls, tz=None): return datetime(2026, 9, 29, 1, 44, tzinfo=t.IST)
t.datetime = FakeNow
t.http_get = lambda url, timeout=20: LIVE_PAGE if url == cfg["live_scores_url"] else SQUAD_PAGES[url.rsplit("/", 1)[1]]
t.check_matches(cfg, db, roster, True)
t.check_matches(cfg, db, roster, True)                        # second run: no repeat alerts
t.datetime = real_now
assert len(sent) == 2, sent
assert sent[0].startswith("📅 PLAYING TODAY: Anshul Kamboj (India A, in squad)\nStarts 29 Sep 09:30 IST\n"
                          "India A vs Australia A, 2nd unofficial Test · TEST\n"), sent[0]
assert sent[1].startswith("🏏 PLAYING NOW: Dewald Brevis (Paarl Royals, playing XI)\nStarted 27 Sep 14:00 IST\n"
                          "Paarl Royals vs MI Cape Town, 5th Match · T20\n"), sent[1]
print("match alerts OK")
samples = list(sent)

# Polling: last-run times are saved in state.db, so a second --once run (fresh process) skips roster and news.
t.HERE, calls = tmp, []                                       # keep run_cycle's dashboard.html in the temp folder
def fake_get(url, timeout=20):
    calls.append(url)
    if url == cfg["live_scores_url"]: return LIVE_PAGE
    return SQUAD_PAGES.get(url.rsplit("/", 1)[1], "<rss><channel></channel></rss>")
t.http_get = fake_get
pdb = t.db_connect(os.path.join(tmp, "poll.db"))
t.run_cycle(cfg, pdb, {}, True, False)
first = len(calls)
assert any("news.google.com" in u for u in calls) and cfg["roster_sources"][0] in calls
calls.clear()
t.run_cycle(cfg, pdb, {}, True, False)
assert calls == [cfg["live_scores_url"]], calls              # squads are cached too
print(f"polling OK: first run {first} requests, next run {len(calls)}")

# Roster: Cricbuzz's CSK team page lists 24 players; Aman Khan and the replacement signings only appear in the
# IPL 2026 match squads (real ids, and the series page's real format: upper-case names, lower-case state).
missing = {"19473", "13143", "15727", "50444", "29181", "13537"}
team_page = "".join(f'<a href="/profiles/{p["id"]}/x" title="{p["name"]}"></a>' for p in roster if p["id"] not in missing)
def ipl(mid, state, t1, t2):
    ids = {"CSK": 58, "GT": 59, "RR": 64}
    return {"matchInfo": {"matchId": mid, "seriesId": 9241, "matchDesc": "Match", "state": state,
                          "team1": {"teamId": ids[t1], "teamName": t1, "teamSName": t1},
                          "team2": {"teamId": ids[t2], "teamName": t2, "teamSName": t2}}}
csk_squad = [(int(p["id"]), p["name"]) for p in roster]                     # all 30, as in the real match squads
PAGES = {
    "scorecard-archives": '<a href="/cricket-series/9241/indian-premier-league-2026/matches">IPL</a>'
                          '<a href="/cricket-series/9249/indian-premier-league-2027/matches">IPL</a>',
    "9249/indian-premier-league-2027/matches": next_page(ipl(160001, "Preview", "CSK", "GT")),      # not started
    "9241/indian-premier-league-2026/matches": next_page(ipl(149640, "complete", "RR", "CSK"),
                                                         ipl(152229, "complete", "GT", "CSK")),
    "cricket-match-squads/149640": next_page(squads("CSK", "playing XI", csk_squad[:11]),
                                             squads("CSK", "bench", csk_squad[11:]),
                                             squads("RR", "playing XI", [(8, "Someone Else")])),
    "cricket-match-squads/152229": next_page(squads("CSK", "playing XI", csk_squad[:11])),
}
def roster_get(url, timeout=20):
    if url in cfg["roster_sources"]: return team_page
    return next(v for k, v in PAGES.items() if k in url)
t.http_get = roster_get
rdb = t.db_connect(os.path.join(tmp, "roster.db"))
sent[:] = []
got = t.refresh_roster(cfg, rdb, True, False)
assert {p["id"] for p in got} == {p["id"] for p in roster} and len(got) == 30, len(got)
assert sent == []                                              # first fetch: nothing to compare with
team_page = team_page.replace('title="Sanju Samson"', 'title="x"').replace("/profiles/8271/", "/profiles/0/")
got = t.refresh_roster(cfg, rdb, True, False)                  # Sanju leaves the team page (released)
assert "Sanju Samson" not in {p["name"] for p in got}, "still kept via the old season's match squads"
assert sent and "Removed: Sanju Samson" in sent[0], sent
print("roster: team page + IPL season squads OK (30 players; released players drop off)")

t.write_dashboard(roster, hits, latest, db, os.path.join(tmp, "dashboard.html"))
print("dashboard written", os.path.getsize(os.path.join(tmp, "dashboard.html")), "bytes")
print("\nSAMPLE ALERTS:\n" + "\n\n".join(samples))
