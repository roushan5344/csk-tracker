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
silent = []
def fake_send(c, text, d=False, silent_=False, **kw):
    t.alerts_sent += 1
    sent.append(text)
    silent.append(kw.get("silent", silent_))
t.send_alert = fake_send
def offline(url, timeout=20):           # the tests never touch the network; sections below swap in fake pages
    raise OSError(f"offline test: {url}")
t.http_get = offline

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
    # short names alone (27-28 Sep 2026): missing these is worse than the odd wrong tag
    "Ellis, Davies ruled out to further deplete Aussies - cricket.com.au": ("breaking", ["Nathan Ellis"]),
    "3rd T20I: Noor, Naib out as Afghanistan elect to bowl against India, Thakur replaces Arshdeep": ("minor", ["Noor Ahmad"]),
    "Iyer's absence, Ruturaj's gain: CSK skipper cashing in on WI ODIs could boost India's middle-order resources":
        ("minor", ["CSK", "Ruturaj Gaikwad"]),
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
    "Shashi Tharoor praises Kuldeep Yadav’s four-wicket spell vs West Indies - The News Mill",   # nor this
    "Who have scored the most ODI centuries while batting at no. 5 or lower? | Miller recorded career-best score of 142 vs Aus | Inshorts - Inshorts",
    "SHOCKING: Dhoni viral video breaks the internet",
    "Unrelated news about football",
    "GT vs CSK Live Full Scorecard",                                                 # old pages Bing re-dated to
    "CSK Squad IPL 2026",                                                            # 26 Sep, not news
    "Live Cricket Score, Schedule, Latest News, Stats & Videos - cricbuzz.com",
    "Chennai Super Kings Cricket Team News & Matches",                               # team pages (real, 28 Sep)
    "INDIA CRICKET TEAM NEWS",
]
db = t.db_connect(os.path.join(tmp, "state.db"))
t.set_other_names(cfg, db, names)       # other cricketers' full names, loaded before news as run_cycle does
# Short names must reject other people with a tracked player's surname, and keep ours (middle names too).
assert t.tag_item("Rhythm, control and a Kuldeep Yadav masterclass", ["Kuldip Yadav"], set()) == []
assert t.tag_item("Cox and Overton collide", names, set()) == ["Jamie Overton"]
assert t.tag_item("IPL 2026: Meet Macneil Hadley Noronha, UAE-raised Karnataka all-rounder, who replaces Ghosh",
                  names, set()) == ["Ramakrishna Ghosh", "Macneil Noronha"]     # real; the old next-word rule missed it
latest = []
t.process_items(cfg, db, t.parse_rss(rss(list(KEEP) + DROP)), names, True, latest, first_run=False)
got = {i["title"]: (i["importance"], i["tags"]) for i in latest}
assert got == KEEP, "\n".join(f"{v}  {k}" for k, v in got.items())
print("news relevance OK:", len(KEEP), "kept,", len(DROP), "dropped")

# Other cricketers' full names (config "namesakes" + Cricbuzz squads) take their words away from short-name
# matches, but never from our own players. Real headlines, 26-29 Sep 2026.
t.set_other_names(cfg, db, names)
for h, want in [
        ("Kuldeep Yadav Overtakes Venkatesh Prasad To Become India's 9th Highest Wicket-Taker In ODIs - News18", []),
        ("KL Rahul guides Delhi to third straight IPL victory", []),
        ("England Call Up Hat-Trick Hero Henry Crocombe For Sri Lanka ODI Series", []),
        ("Ex-India Fast Bowler Zaheer Khan Appointed Chennai Super Kings Head Coach For IPL 2027", ["CSK"]),
        ("WATCH: Matt Short’s Jaw-Dropping One-Handed Catch To Dismiss Marnus Labuschagne", ["Matthew Short"]),
        ("Ellis, Davies ruled out to further deplete Aussies - cricket.com.au", ["Nathan Ellis"]),
        # a first name alone counts only in a cricket headline (surname anywhere, or a cricket word)
        ("\"He Was The Perfect Pro Wrestler\": Matt Cardona Mourns PAC’s Death At 40 With Emotional WWE Throwback", []),
        ("England twins Jamie and Craig Overton will play Big Bash League alongside each other this summer", ["Jamie Overton"]),
        ("UPT20 2026: Prashant Veers all round show, Kartik Siddhus hat-trick help Noida Kings beat Kashi Rudras",
         ["Prashant Veer", "Kartik Sharma"]),       # Kartik Sharma is wrong (it's Kartik Siddhu): accepted, a miss is worse
        # a nickname counts only in a cricket headline ("Thala" is also the actor Ajith)
        ("Mahi bhai back in the nets ahead of IPL 2027", ["MS Dhoni"]),
        ("Thala Ajith's new film to release on Diwali", []),
        ("POV: Thala smiled, and suddenly everything feels better! \U0001f979\U0001f49b#WhistlePodu", ["MS Dhoni"])]:   # CSK slogan
    assert t.tag_item(h, names, set()) == want, (h, t.tag_item(h, names, set()))
# "appointment" is breaking only for a CSK / coach / captain appointment
assert t.classify("Mohammad Yousuf names MS Dhoni appointment as decisive moment in Indian cricket's rise")[0] == "minor"
assert t.classify("‘The game gave me many chapters’ - Zaheer Khan reflects on journey after CSK appointment")[0] == "breaking"
print("namesakes and appointment rank OK")

# Same story from many publishers (real, 28-29 Sep 2026): sent once per player and kind of event, plus a
# higher-ranked report; the copies are neither sent nor on the dashboard. A different story still goes out.
ELLIS = ["Ellis and Davies out of the South Africa tour with injury - Cricinfo",                       # important
         "Nathan Ellis and Joel Davies out of final ODI against South Africa - thenewsmill.com",
         "Ellis, Davies ruled out to further deplete Aussies - cricket.com.au",                         # breaking
         "Injured Ellis ruled out of third ODI against South Africa - Cricbuzz",
         "AUS vs SA: Nathan Ellis, Joel Davies ruled out of third ODI in Potchefstroom - Yardbarker",
         "Nathan Ellis’ 3-fer vs SA | 1st ODI - Cricbuzz",                                          # other story
         "Nathan Ellis, Joel Davies leave Australia’s South Africa tour injured - Cricket Addictor",
         "Nathan Ellis, Joel Davies ruled out of 3rd ODI vs South Africa with injuries - CricTracker",
         "Australia suffer Nathan Ellis blow ahead of 3rd SA ODI - NewsBytes",
         "Double injury blow for Australia as Ellis, Davies ruled out of SA ODI",
         "Nathan Ellis ruled out of third Australia vs South Africa ODI; Joel Davies also sent home",
         "Australia faces setback ahead of final ODI against South Africa as Ellis and Davis withdraw",
         # a real duplicate the old check missed: " - Older, wiser..." was mistaken for a publisher name
         "Jamie Overton - Older, wiser and still bowling fast",
         "Jamie Overton - Older, wiser and still bowling fast - Cricbuzz",
         # team-only and unclassified stories are never grouped (two different CSK trade stories)
         "IPL 2027 transfers: List of confirmed swaps, trade rumours as Hardik Pandya links with CSK",
         "Washington Sundar CSK Trade Rumor: Is It Actually Happening? - TechnoSports Media Group"]
gdb, n_sent, glatest = t.db_connect(os.path.join(tmp, "group.db")), len(sent), []
t.process_items(cfg, gdb, t.parse_rss(rss(ELLIS)), names, True, glatest, first_run=False)
got = [i["title"] for i in glatest]
assert got == [ELLIS[0], ELLIS[2], ELLIS[5], ELLIS[12], ELLIS[14], ELLIS[15]], got
assert len(sent) - n_sent == 6                                    # Telegram gets the same six
assert t.story_event("Virat Kohli names his 'ideal ODI batter', leaves out Rohit Sharma and MS Dhoni") != "injury"
assert t.story_event("South Africa announce 18-member squad for 1st Test vs Australia: Brevis dropped, "
                     "3 injured pacers unavailable") == "selection"
print("same story from other publishers: sent once (+ a higher-ranked report), not on the dashboard")

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

# A story seen for the first time but published over 24 h ago (real: 26 Sep, reached us on 29 Sep via Bing) is
# recorded for injury flags and dedupe, but not alerted.
n_sent, stale_latest = len(sent), []
old = t.parse_rss(rss(["Ruturaj Gaikwad poised to strengthen India middle order in West Indies ODIs"],
                      datetime.now(t.IST) - timedelta(hours=30)))
t.process_items(cfg, db, old, names, True, stale_latest, first_run=False)
assert len(sent) == n_sent and stale_latest == [] and t.is_duplicate(db, old[0]["title"]), sent[n_sent:]  # not on dashboard
print("over-a-day-old stories: recorded, not alerted")
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

# Bing News (real item, 26 Sep 2026): redirect link unwrapped, publisher from <News:Source>, summary kept.
now_rfc = format_datetime(datetime.now(t.IST))
BING = ('<rss xmlns:News="https://www.bing.com/news/search?q=Chennai+Super+Kings&amp;format=rss" version="2.0"><channel>'
        '<item><title>Different challenge: Big shoes for Zaheer Khan to fill as Chennai Super Kings coach</title>'
        '<link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;tid=6abadf&amp;url=https%3a%2f%2fwww.telegraphindia.com'
        '%2fsports%2fcricket%2fdifferent-challenge-big-shoes-for-zaheer-khan-to-fill-as-chennai-super-kings-coach-prnt%2fcid%2f2181766'
        '&amp;c=964&amp;mkt=en-in</link><description>There were several names doing the rounds as probable replacements once '
        'Fleming left the job after IPL 2026, but Zaheer got the nod after former captain Dhoni agreed to his choice ...'
        f'</description><pubDate>{now_rfc}</pubDate><News:Source>Telegraph India</News:Source></item></channel></rss>')
b = t.parse_rss(BING)[0]
assert b["link"].startswith("https://www.telegraphindia.com/sports/cricket/different-challenge"), b["link"]
assert b["source"] == "Telegraph India" and b["summary"].startswith("There were several names"), b

# CSK's official YouTube channel (Atom feed, real titles): tagged "CSK (official)", never a rumour.
def entry(title, vid):
    return (f'<entry><title>{title}</title><link rel="alternate" href="https://www.youtube.com/watch?v={vid}"/>'
            f'<published>{datetime.now(t.IST).isoformat()}</published></entry>')
YT = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>Chennai Super Kings</title>'
      + entry("Hitting the Right Areas with Jamie Overton \U0001f981 | Lion in Focus | CSK", "bOYRjyFr3J8")
      + entry("Mood for today ✨\U0001f90c   #WhistlePodu #Yellove", "KUFQ6inp_LM")
      + entry("Looks familiar \U0001f914 Think inside out, Superfans! #WhistlePodu #Yellove", "zNeCNiXm6pI") + "</feed>")
yt = [{**i, "official": "CSK (official)"} for i in t.parse_rss(YT)]
assert yt[0]["link"] == "https://www.youtube.com/watch?v=bOYRjyFr3J8" and yt[0]["when"]

GOOGLE_COPY = t.parse_rss(rss(["Different challenge: Big shoes for Zaheer Khan to fill as Chennai Super Kings coach - Telegraph India"]))
latest3, sent[:] = [], []
t.process_items(cfg, db, [b] + GOOGLE_COPY + yt, names, True, latest3, first_run=False)
assert [(i["tags"], i["rumour"]) for i in latest3] == [
    (["CSK"], False), (["CSK (official)", "Jamie Overton"], False), (["CSK (official)"], False), (["CSK (official)"], False)
], [(i["title"], i["tags"]) for i in latest3]          # the Google copy of the Bing story is a duplicate
assert "https://www.telegraphindia.com/" in sent[0] and "Telegraph India" in sent[0]
assert latest3[0]["importance"] == "breaking"                        # new head coach
assert t.classify("Proteas coach questions Dewald Brevis’ suitability")[0] == "minor"
print("Bing News + official YouTube OK")

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
NIGHT = datetime(2026, 9, 29, 1, 44, tzinfo=t.IST)
now_playing = {h["player"] for h in hits if t.describe(h, NIGHT)[1].startswith("PLAYING NOW")}
assert now_playing == {"Dewald Brevis"}, now_playing          # finished, stumps, bench, unknown state: not playing now
assert t.describe(by[("Anshul Kamboj", "155422")], NIGHT)[1] == "PLAYING TODAY, starts 29 Sep 09:30 IST, in squad"
k = by[("Anshul Kamboj", "155422")]
assert (k["team"], k["opponent"], k["match"]["format"]) == ("India A", "Australia A", "TEST")
print("match states, IST times and squad mapping OK")

# ---------- schedule, TODAY/TOMORROW, expected squads, morning digest ----------
ms = lambda *a: str(int(datetime(*a, tzinfo=t.IST).timestamp() * 1000))
def sched_page(*entries):   # real structure of cricket-schedule/upcoming-series/all
    return next_page({"matchScheduleMap": [{"scheduleAdWrapper": {"date": "TUE, SEP 29 2026", "matchScheduleList": [
        {"seriesName": series, "seriesId": sid, "seriesCategory": cat, "matchInfo": [
            {"matchId": mid, "seriesId": sid, "matchDesc": desc, "matchFormat": fmt, "startDate": start,
             "team1": dict(zip(("teamName", "teamSName"), t1.split("/"))),
             "team2": dict(zip(("teamName", "teamSName"), t2.split("/")))}]}
        for mid, sid, series, cat, desc, fmt, start, t1, t2 in entries]}}]})
WI_ODI2 = (151543, 11902, "West Indies tour of India, 2026", "International", "2nd ODI", "ODI", ms(2026, 9, 30, 14, 0),
           "India/IND", "West Indies/WI")                          # real: squad not published yet on 29 Sep
EARLY = (600001, 700001, "Caribbean Premier League 2026", "League", "20th Match", "T20", ms(2026, 9, 29, 6, 0),
         "Guyana Amazon Warriors/GAW", "Trinbago Knight Riders/TKR")                                     # made up
LATER = (600002, 700002, "Afghanistan tour of Bangladesh", "International", "1st T20I", "T20", ms(2026, 9, 30, 18, 0),
         "Bangladesh/BAN", "Afghanistan/AFG")                                                            # made up
FAR = (600003, 700003, "Future series", "International", "1st ODI", "ODI", ms(2026, 10, 3, 9, 0), "India/IND", "England/ENG")
AUS_ODI3 = (147909, 11595, "Australia tour of South Africa, 2026", "International", "3rd ODI", "ODI", ms(2026, 9, 30, 17, 0),
            "South Africa/RSA", "Australia/AUS")                   # real; Ellis hurt his side in the 2nd ODI
SCHEDULE = [sched_page(WI_ODI2, AUS_ODI3, EARLY, FAR)]
SERIES_11595 = next_page(
    {"matchInfo": {**info(147898, "Australia tour of South Africa, 2026", "2nd ODI", "ODI", 1790496000000, "complete", "",
                          "South Africa/RSA", "Australia/AUS")["matchInfo"], "seriesId": 11595}})
SERIES_11902 = next_page(   # the series' match list: 1st ODI done, 2nd ODI to come
    {"matchInfo": {**info(151532, "West Indies tour of India, 2026", "1st ODI", "ODI", 1790497800000, "complete", "",
                          "West Indies/WI", "India/IND")["matchInfo"], "seriesId": 11902}},
    {"matchInfo": {**info(151543, "West Indies tour of India, 2026", "2nd ODI", "ODI", ms(2026, 9, 30, 14, 0), "Preview", "",
                          "India/IND", "West Indies/WI")["matchInfo"], "seriesId": 11902}})
SQUAD_PAGES.update({"600001": next_page(squads("GAW", "Squad", [(8435, "Akeal Hosein")])),
                    "600002": next_page(squads("AFG", "Squad", [(15452, "Noor Ahmad")])),
                    "600003": next_page(squads("IND", "Squad", [(11813, "Ruturaj Gaikwad")])),
                    "147898": next_page(squads("AUS", "playing XI", [(15480, "Nathan Ellis")]))})   # real: in the 2nd ODI XI
def match_get(url, timeout=20):
    if url == cfg["live_scores_url"]: return LIVE_PAGE
    if url == cfg["schedule_url"]: return SCHEDULE[0]
    if "/cricket-series/11902/" in url: return SERIES_11902
    if "/cricket-series/11595/" in url: return SERIES_11595
    return SQUAD_PAGES.get(url.rsplit("/", 1)[1], "")             # 151543: no squad yet
t.http_get = match_get
real_now, NOW = t.datetime, [NIGHT]
class FakeNow(datetime):
    @classmethod
    def now(cls, tz=None): return NOW[0]
t.datetime = FakeNow
mdb = t.db_connect(os.path.join(tmp, "match.db"))
sent[:], silent[:] = [], []
t.check_matches(cfg, mdb, roster, True)                       # 01:44
t.check_matches(cfg, mdb, roster, True)                       # 01:54: nothing repeats
assert len(sent) == 2, sent
assert sent[0].startswith("🏏 PLAYING NOW: Dewald Brevis (Paarl Royals, playing XI)\nStarted 27 Sep 14:00 IST\n"
                          "Paarl Royals vs MI Cape Town, 5th Match · T20\n"), sent[0]
# a match before the 8 AM digest is announced at once; 09:30 and tomorrow's wait for the digest
assert sent[1].startswith("📅 NEW MATCH FOR CSK PLAYERS\n\nTODAY (Tue 29 Sep)\n• Guyana Amazon Warriors vs Trinbago "
                          "Knight Riders · T20 · starts 06:00 IST\n   Akeal Hosein (Guyana Amazon Warriors, in squad)"), sent[1]
assert "Kamboj" not in sent[1] and "TOMORROW" not in sent[1]

NOW[0] = datetime(2026, 9, 29, 8, 5, tzinfo=t.IST)            # morning digest: today + tomorrow
t.mark_seen(mdb, "Ellis, Davies ruled out to further deplete Aussies - cricket.com.au",    # real, 28 Sep
            datetime.now(t.IST).replace(hour=12) - timedelta(days=1))                     # published yesterday
t.check_matches(cfg, mdb, roster, True)
digest = sent[2]
assert digest.startswith("📅 CSK PLAYERS' MATCHES\n\nTODAY (Tue 29 Sep)\n"), digest
for part in ["Dewald Brevis (Paarl Royals, playing XI)", "LIVE now",                  # live match
             "Ayush Mhatre (India U19, playing XI)", "Stumps",                        # Test between days: still today
             "• India A vs Australia A · TEST · starts 09:30 IST\n   Anshul Kamboj (India A, in squad)",
             "TOMORROW (Wed 30 Sep)\n• India vs West Indies · ODI · starts 14:00 IST\n"
             "   Ruturaj Gaikwad (India, expected, squad not out yet)\n   2nd ODI, West Indies tour of India, 2026",
             # injury news is shown next to the player, who stays listed
             "• South Africa vs Australia · ODI · starts 17:00 IST\n   Nathan Ellis (Australia, expected, squad not out yet)\n"
             "      ⚠️ injury news: “Ellis, Davies ruled out to further deplete Aussies” ("]:
    assert part in digest, (part, digest)
assert "Sanju" not in digest and "England" not in digest and "Guyana" not in digest   # unknown state, 3 days out, started
assert digest.index("TODAY") < digest.index("TOMORROW")

NOW[0] = datetime(2026, 9, 29, 15, 0, tzinfo=t.IST)           # after the digest a new match turns up for tomorrow
SCHEDULE[0] = sched_page(WI_ODI2, FAR, LATER)
t.check_matches(cfg, mdb, roster, True)
t.check_matches(cfg, mdb, roster, True)
assert len(sent) == 4 and sent[3].startswith("📅 NEW MATCH FOR CSK PLAYERS\n\nTOMORROW (Wed 30 Sep)\n• Bangladesh vs "
                                             "Afghanistan · T20 · starts 18:00 IST\n   Noor Ahmad (Afghanistan, in squad)"), sent[3]
samples = list(sent)

sent[:], silent[:] = [], []                                   # a quiet day: one silent digest line
t.check_matches(cfg, t.db_connect(os.path.join(tmp, "quiet_day.db")), [], True)
assert sent == ["📅 No CSK player has a match today or tomorrow."] and silent == [True], sent
t.datetime = real_now
print("match alerts OK: PLAYING NOW, morning digest (today + tomorrow), expected squads, new-match alerts")

# ---------- matches only the news mentions (real: Ayush Mhatre's practice match, not on Cricbuzz) ----------
TOI_URL = ("https://timesofindia.indiatimes.com/sports/cricket/news/back-from-hamstring-injury-ayush-mhatre-to-play-"
           "his-first-competitive-match-in-5-months/articleshow/1.cms")
TOI_PAGE = ("<html><body><div>Back from hamstring injury, Ayush Mhatre to play his first competitive match in 5 months | "
            "Cricket News - The Times of India " + "Menu Cricket Asian Games IND Vs AFG IND Vs WI NFL NBA NHL " * 10 +
            "TOI Sports Desk / Updated: Sep 28, 2026, 12:09 IST Today</div>"   # real page header: no full stops
            "<p>In a major boost to Mumbai ahead of the 2026-27 Ranji Trophy season, opener Ayush Mhatre, out "
            "of action since he suffered a bad hamstring injury while playing for the Chennai Super Kings during IPL-2026, "
            "will play his first competitive match in almost five months when he will turn out in a practice match amongst "
            "Mumbai's Ranji Trophy probables at the Cricket Club of India on Tuesday.</p><p>\"He will play in a two-day "
            "practice match, organised by the Mumbai Cricket Association for Mumbai's Ranji Trophy probables, at the "
            "Brabourne Stadium from Tuesday,\" Ayush's father Yogesh Mhatre told TOI on Monday.</p>"
            "<div>Videos: Speaker Gets October 10 Deadline</div></body></html>")      # sidebar date must be ignored
TOI_RSS = ("<rss><channel><item><title>Back from hamstring injury, Ayush Mhatre to play his first competitive match in "
           f"5 months</title><link>{TOI_URL}</link><pubDate>Mon, 28 Sep 2026 06:39:00 GMT</pubDate>"   # 12:09 IST
           "<description>In a major boost to Mumbai ahead of the 2026-27 Ranji Trophy season, opener Ayush Mhatre, out "
           "of action since he suffered a bad hamstring injury while playing for the Chennai…</description>"
           "</item></channel></rss>")
fetched = []
def news_get(url, timeout=20):
    fetched.append(url)
    if url == TOI_URL: return TOI_PAGE
    if url in (cfg["live_scores_url"], cfg["schedule_url"]): return next_page()     # nothing on Cricbuzz
    raise OSError(url)
t.http_get = news_get
ndb, sent[:] = t.db_connect(os.path.join(tmp, "news_match.db")), []
NOW[0] = datetime(2026, 9, 28, 20, 0, tzinfo=t.IST)           # Monday evening: the story arrives
t.datetime = FakeNow
t.process_items(cfg, ndb, t.parse_rss(TOI_RSS), names, True, [], first_run=False)
t.process_items(cfg, ndb, t.parse_rss(TOI_RSS), names, True, [], first_run=False)   # seen again: not re-read
assert fetched.count(TOI_URL) == 1, fetched
assert ndb.execute("select player, day, days from news_matches").fetchall() == [("Ayush Mhatre", "2026-09-29", 2)]
ndb.execute("insert into kv values('digest:2026-09-28', '1')"); ndb.commit()   # Monday's 8 AM digest already went out
sent[:] = []
t.check_matches(cfg, ndb, roster, True)                        # 20:00 Monday: found after the digest -> sent now
t.check_matches(cfg, ndb, roster, True)
assert len(sent) == 1 and sent[0].startswith(
    "📅 NEW MATCH FOR CSK PLAYERS\n\nTOMORROW (Tue 29 Sep)\n• Match per news, not an official fixture, 2-day match from "
    "Tue 29 Sep\n   Ayush Mhatre\n   “Back from hamstring injury, Ayush Mhatre to play his first competitive match in "
    "5 months” (timesofindia.indiatimes.com)\n   https://timesofindia.indiatimes.com/"), sent
NOW[0] = datetime(2026, 9, 29, 3, 0, tzinfo=t.IST)
t.check_matches(cfg, ndb, roster, True)                        # 03:00: no repeat
NOW[0] = datetime(2026, 9, 29, 8, 5, tzinfo=t.IST)
t.check_matches(cfg, ndb, roster, True)                        # 08:05 digest lists it under today
assert len(sent) == 2 and sent[1].startswith("📅 CSK PLAYERS' MATCHES\n\nTODAY (Tue 29 Sep)\n• Match per news"), sent
NOW[0] = datetime(2026, 9, 30, 8, 5, tzinfo=t.IST)            # day 2 of the practice match: still listed today
t.check_matches(cfg, ndb, roster, True)
assert len(sent) == 3 and "TODAY (Wed 30 Sep)\n• Match per news" in sent[2] and "Ayush Mhatre" in sent[2], sent[2]
hit = t.news_fixture_hits(cfg, ndb, NOW[0])[0]
assert t.describe(hit, NOW[0])[1] == "PLAYING TODAY, per news (not an official fixture)"

# The article is updated (real: the practice match became a Kanga League game on Friday): one correction.
TOI_PAGE = ("<html><body><p>MUMBAI: In a major boost to Mumbai ahead of the 2026-27 Ranji Trophy season, opener Ayush "
            "Mhatre, out of action since he suffered a bad hamstring injury, will play his first competitive match in "
            "almost five months.</p><p>He will turn out for Sainath Cricket Club in a Kanga League 'B Division game on "
            "Friday,\" Ayush's father Yogesh Mhatre told TOI on Monday.</p></body></html>")
NOW[0] = datetime(2026, 9, 30, 12, 0, tzinfo=t.IST)
t.check_matches(cfg, ndb, roster, True)
NOW[0] = datetime(2026, 9, 30, 13, 0, tzinfo=t.IST)
t.check_matches(cfg, ndb, roster, True)                        # no repeat
assert len(sent) == 4 and sent[3].startswith(
    "✏️ CORRECTION: Ayush Mhatre\nEarlier alert said: match per news, 2-day from Tue 29 Sep.\n"
    "The article now says: Fri 2 Oct.\n“Back from hamstring injury, Ayush Mhatre to play his first competitive "
    "match in 5 months” (timesofindia.indiatimes.com)\n" + TOI_URL), sent[3:]
NOW[0] = datetime(2026, 10, 1, 8, 5, tzinfo=t.IST)            # Thursday's digest: Friday's game, no extra NEW MATCH
t.check_matches(cfg, ndb, roster, True)
assert len(sent) == 5 and sent[4].startswith("📅 CSK PLAYERS' MATCHES\n\nTOMORROW (Fri 02 Oct)\n• Match per news"), sent[4:]
# The article drops the match altogether: a second correction.
TOI_PAGE = "<html><body><p>Ayush Mhatre has been training at the Mumbai Cricket Association's facility.</p></body></html>"
NOW[0] = datetime(2026, 10, 1, 12, 0, tzinfo=t.IST)
t.check_matches(cfg, ndb, roster, True)
assert len(sent) == 6 and sent[5].startswith(
    "✏️ CORRECTION: Ayush Mhatre\nEarlier alert said: match per news, Fri 2 Oct.\n"
    "The article no longer mentions this match, so ignore the earlier alert."), sent[5:]
assert t.news_fixture_hits(cfg, ndb, NOW[0]) == []
t.datetime = real_now
samples += [sent[0], sent[3]]
print("news-only matches OK: found from the article, held for the digest, both days listed, corrections sent")

# GitHub's saved state from before corrections (old news_fixtures table) is carried over, including what was told.
odb_path = os.path.join(tmp, "old_state.db")
old = t.sqlite3.connect(odb_path)
old.execute("create table news_fixtures(player text, day text, days integer, title text, link text, source text,"
            " primary key(player, day))")
old.execute("create table kv(k text primary key, v text)")
old.execute("insert into news_fixtures values('Ayush Mhatre', '2026-09-29', 2, 'Back from hamstring injury', ?, 'TOI')",
            (TOI_URL,))
old.execute("insert into kv values('announced:news:Ayush Mhatre:2026-09-29', '1')"); old.commit(); old.close()
assert t.db_connect(odb_path).execute("select player, day, days, told, checked, status from news_matches").fetchall() == \
    [("Ayush Mhatre", "2026-09-29", 2, "2026-09-29|2", 0, "active")]
print("old saved news-only matches carried over")

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
assert calls == [cfg["live_scores_url"], cfg["schedule_url"]], calls   # squads are cached too
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

# Quiet check-in: one silent "no new updates" message per 3 quiet hours, never after a run that sent alerts.
qdb, sent[:], silent[:] = t.db_connect(os.path.join(tmp, "quiet.db")), [], []
t0, H = 1790630000.0, 3600
t.quiet_check_in(cfg, qdb, t0, t.alerts_sent, True)                   # first run: only starts the clock
t.quiet_check_in(cfg, qdb, t0 + 2 * H, t.alerts_sent, True)           # 2 quiet hours: nothing yet
assert sent == []
t.quiet_check_in(cfg, qdb, t0 + 3 * H, t.alerts_sent, True)           # 3 quiet hours: one silent message
assert len(sent) == 1 and silent == [True] and sent[0].startswith("✅ No new updates since 29 Sep 02:43 IST"), sent
before = t.alerts_sent; t.send_alert(cfg, "real alert", True)         # a real alert restarts the clock
t.quiet_check_in(cfg, qdb, t0 + 5 * H, before, True)
t.quiet_check_in(cfg, qdb, t0 + 7 * H, t.alerts_sent, True)           # only 2 quiet hours since that alert
assert len(sent) == 2, sent
t.quiet_check_in(cfg, qdb, t0 + 8 * H, t.alerts_sent, True)
assert len(sent) == 3 and silent[-1], sent
print("quiet check-in OK (silent, every 3 quiet hours)")

# Dashboard news = the last 24 hours only, like the alerts
fresh_item = {"title": "Fresh story", "link": "http://x/f", "when": datetime.now(t.IST) - timedelta(hours=2),
              "tags": ["CSK"], "importance": "minor", "rumour": False, "summary": ""}
stale_item = {**fresh_item, "title": "Day-old story", "link": "http://x/s", "when": datetime.now(t.IST) - timedelta(hours=30)}
t.write_dashboard(roster, [], [fresh_item, stale_item], db, os.path.join(tmp, "dash_age.html"))
page = open(os.path.join(tmp, "dash_age.html"), encoding="utf-8").read()
assert "Fresh story" in page and "Day-old story" not in page and "News, last 24 hours" in page
print("dashboard shows only the last 24 hours of news")

t.write_dashboard(roster, hits, latest, db, os.path.join(tmp, "dashboard.html"))
print("dashboard written", os.path.getsize(os.path.join(tmp, "dashboard.html")), "bytes")
print("\nSAMPLE ALERTS:\n" + "\n\n".join(samples))
