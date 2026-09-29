"""Regression suite for the CSK tracker: offline (no network), fixed clock, real headlines and real Cricbuzz data
captured on 27-29 Sep 2026. Covers news, roster, matches, news-only matches, Telegram alerts, the dashboard,
polling, config and the GitHub workflow. Each test gets its own empty state.db and a fake web.

Run:  python test_offline.py        (python test_offline.py -v lists every test)
"""
import hashlib, io, json, os, re, sqlite3, sys, tempfile, unittest, urllib.parse
from datetime import datetime, timedelta
from email.utils import format_datetime
from unittest import mock

import tracker as t

REAL_HTTP_GET = t.http_get                                   # Base swaps in a fake web; TestHttp tests the real one

ROOT = os.path.dirname(os.path.abspath(__file__))
T0 = datetime(2026, 9, 29, 8, 5, tzinfo=t.IST)        # "now" in every test unless the test moves the clock
EMPTY_RSS = "<rss><channel></channel></rss>"
CLOCK = [T0]


class FakeDatetime(datetime):
    """tracker.datetime with a clock the tests control."""
    @classmethod
    def now(cls, tz=None):
        return CLOCK[0] if tz is None else CLOCK[0].astimezone(tz)


class FakeTime:
    """tracker.time with the same clock (tracker stores time.time() stamps in state.db)."""
    @staticmethod
    def time():
        return CLOCK[0].timestamp()

    slept = []

    @staticmethod
    def sleep(seconds):                                   # no real waiting: the clock just moves on
        FakeTime.slept.append(seconds)
        CLOCK[0] += timedelta(seconds=seconds)


def at(*a):
    return datetime(*a, tzinfo=t.IST)


# ---------- fixture builders (formats as Cricbuzz / Google News / Bing / YouTube serve them) ----------
def rss(titles, when=None, link=None, source=None, summary=None):
    """RSS with one <item> per title, published at `when` (default: now)."""
    date, items = format_datetime(when or CLOCK[0]), ""
    for ti in titles:
        url = link or "http://example.test/" + hashlib.sha1(ti.encode()).hexdigest()[:10]
        items += (f"<item><title>{t.html.escape(ti)}</title><link>{t.html.escape(url)}</link><pubDate>{date}</pubDate>"
                  + (f"<source>{source}</source>" if source else "")
                  + (f"<description>{t.html.escape(summary)}</description>" if summary else "") + "</item>")
    return f"<rss><channel>{items}</channel></rss>"


def next_page(*objs):
    """How Cricbuzz (Next.js) embeds data: JSON text in self.__next_f.push([1,"..."]) script tags."""
    return "".join('<script>self.__next_f.push([1,%s])</script>' % json.dumps(json.dumps(o, separators=(",", ":")))
                   for o in objs)


def info(mid, series, desc, fmt, start, state, status, t1, t2, sid=None):
    """A Cricbuzz matchInfo object; t1/t2 are "Full name/SHORT" (squad pages use the short name)."""
    team = lambda x: dict(zip(("teamName", "teamSName"), x.split("/")))
    mi = {"matchId": mid, "seriesName": series, "matchDesc": desc, "matchFormat": fmt, "startDate": start,
          "state": state, "status": status, "team1": team(t1), "team2": team(t2)}
    if sid:
        mi["seriesId"] = sid
    return {"matchInfo": mi}


def squads(team, group, players):
    return {"players": {group: [{"id": i, "name": n, "teamName": team} for i, n in players],
                        "support staff": [{"id": 308, "name": "Gautam Gambhir", "teamName": team}]}}


def ms(*a):
    return str(int(at(*a).timestamp() * 1000))


def sched_page(*entries):
    """Cricbuzz schedule page (cricket-schedule/upcoming-series/all): matchScheduleMap -> series -> matchInfo list."""
    return next_page({"matchScheduleMap": [{"scheduleAdWrapper": {"date": "TUE, SEP 29 2026", "matchScheduleList": [
        {"seriesName": series, "seriesId": sid, "seriesCategory": cat, "matchInfo": [
            {"matchId": mid, "seriesId": sid, "matchDesc": desc, "matchFormat": fmt, "startDate": start,
             "team1": dict(zip(("teamName", "teamSName"), t1.split("/"))),
             "team2": dict(zip(("teamName", "teamSName"), t2.split("/")))}]}
        for mid, sid, series, cat, desc, fmt, start, t1, t2 in entries]}}]})


def bat(pid, name, runs, balls, fours, sixes, out="", code="", bowler=0, f1=0, f2=0):
    return {"batId": pid, "batName": name, "runs": runs, "balls": balls, "fours": fours, "sixes": sixes, "outDesc": out,
            "wicketCode": code, "bowlerId": bowler, "fielderId1": f1, "fielderId2": f2, "fielderId3": 0}


def bowl(pid, name, overs, maidens, runs, wickets):
    return {"bowlerId": pid, "bowlName": name, "overs": overs, "maidens": maidens, "runs": runs, "wickets": wickets}


def innings(team, batters, bowlers):
    return {"batTeamDetails": {"batTeamName": team, "batsmenData": {f"bat_{i}": b for i, b in enumerate(batters, 1)}},
            "bowlTeamDetails": {"bowlersData": {f"bowl_{i}": w for i, w in enumerate(bowlers, 1)}}}


def scorecard(state, status, *inns):
    """A Cricbuzz scorecard page (live-cricket-scorecard/{id}): scorecardApiData with matchHeader and innings."""
    return next_page({"scorecardApiData": {"scoreCard": list(inns), "matchHeader": {"state": state, "status": status}}})


# Real figures (Cricbuzz, 27-29 Sep 2026). Opponent ids that weren't needed are made up (9xxxx).
CARD_151532 = scorecard("Complete", "India won by 8 wkts",                       # WI vs IND, 1st ODI
                        innings("West Indies", [bat(8431, "John Campbell", 62, 60, 6, 3, "c Prasidh Krishna b Kuldeep Yadav",
                                                    "CAUGHT", 8292, 10551)], [bowl(8292, "Kuldeep Yadav", 10, 1, 40, 4)]),
                        innings("India", [bat(11813, "Ruturaj Gaikwad", 13, 19, 0, 0, "not out")], []))
CARD_IPL37 = scorecard("Complete", "Gujarat Titans won by 8 wkts",               # IPL 2026, 37th match, CSK vs GT
                       innings("Chennai Super Kings", [bat(8271, "Sanju Samson", 11, 15, 2, 0, "c Jos Buttler b Kagiso Rabada",
                                                           "CAUGHT", 90001, 90002)], []),
                       innings("Gujarat Titans", [bat(11808, "Shubman Gill", 40, 30, 4, 1, "st Sanju Samson b Noor Ahmad",
                                                      "STUMPED", 15452, 8271)], [bowl(15452, "Noor Ahmad", 4, 0, 29, 1)]))
LIVE_155422 = lambda overs, maidens, runs, wkts: scorecard(                      # India A vs Australia A, live
    "In Progress", "Day 1: 2nd Session - Australia A opt to bat",
    innings("Australia A", [bat(90003, "Sam Konstas", 20, 60, 2, 0, "batting")],
            [bowl(14598, "Anshul Kamboj", overs, maidens, runs, wkts)]))


# Real matchInfo values from the live-scores page, 29 Sep 2026 01:44 IST (+ an in-play and an unknown-state match).
LIVE_PAGE = next_page(
    info(151532, "West Indies tour of India, 2026", "1st ODI", "ODI", 1790497800000, "Complete", "India won by 8 wkts",
         "West Indies/WI", "India/IND"),
    info(155499, "Australia U19 tour of India 2026", "1st unofficial Test", "TEST", 1790481600000, "Stumps",
         "Day 2: Stumps - India U19 lead by 213 runs", "Australia U19/AUSU19", "India U19/INDU19"),
    info(155422, "Australia A tour of India 2026", "2nd unofficial Test", "TEST", "1790654400000", "Preview",
         "Match starts at Sep 29, 04:00 GMT", "India A/INDA", "Australia A/AUSA"),
    info(900001, "SA20 2027", "5th Match", "T20", 1790497800000, "In Progress", "Paarl Royals opt to bat",
         "Paarl Royals/PR", "MI Cape Town/MICT"),
    info(900002, "Test league", "1st Match", "TEST", 1790497800000, "Weird State", "?", "Team A/A", "Team B/B"),
) + '<a href="/live-cricket-scores/151532/wi-vs-ind-1st-odi-west-indies-tour-of-india-2026">x</a>'

SQUAD_PAGES = {   # real: only Ruturaj played the WI ODI; Kamboj is in India A's squad (pre-toss); rest made up
    "151532": next_page(squads("IND", "playing XI", [(576, "Rohit Sharma"), (11813, "Ruturaj Gaikwad")]),
                        squads("IND", "bench", [(13940, "Yashasvi Jaiswal")]),
                        squads("WI", "playing XI", [(8431, "John Campbell")])),
    "155422": next_page(squads("INDA", "Squad", [(13088, "Devdutt Padikkal"), (14598, "Anshul Kamboj")])),
    "155499": next_page(squads("INDU19", "playing XI", [(1431163, "Ayush Mhatre")])),
    "900001": next_page(squads("PR", "playing XI", [(20538, "Dewald Brevis")]), squads("MICT", "bench", [(15452, "Noor Ahmad")])),
    "900002": next_page(squads("A", "playing XI", [(8271, "Sanju Samson")])),
    "147898": next_page(squads("AUS", "playing XI", [(15480, "Nathan Ellis")])),       # real: in the 2nd ODI XI
    "600001": next_page(squads("GAW", "Squad", [(8435, "Akeal Hosein")])),
    "600002": next_page(squads("AFG", "Squad", [(15452, "Noor Ahmad")])),
    "600003": next_page(squads("IND", "Squad", [(11813, "Ruturaj Gaikwad")])),
    "151543": "", "147909": "",                                                          # real: not published yet
}
WI_ODI2 = (151543, 11902, "West Indies tour of India, 2026", "International", "2nd ODI", "ODI", ms(2026, 9, 30, 14, 0),
           "India/IND", "West Indies/WI")
AUS_ODI3 = (147909, 11595, "Australia tour of South Africa, 2026", "International", "3rd ODI", "ODI",
            ms(2026, 9, 30, 17, 0), "South Africa/RSA", "Australia/AUS")
EARLY = (600001, 700001, "Caribbean Premier League 2026", "League", "20th Match", "T20", ms(2026, 9, 29, 6, 0),
         "Guyana Amazon Warriors/GAW", "Trinbago Knight Riders/TKR")
LATER = (600002, 700002, "Afghanistan tour of Bangladesh", "International", "1st T20I", "T20", ms(2026, 9, 30, 18, 0),
         "Bangladesh/BAN", "Afghanistan/AFG")
FAR = (600003, 700003, "Future series", "International", "1st ODI", "ODI", ms(2026, 10, 3, 9, 0), "India/IND", "England/ENG")
SERIES_11902 = next_page(
    info(151532, "West Indies tour of India, 2026", "1st ODI", "ODI", 1790497800000, "complete", "", "West Indies/WI",
         "India/IND", sid=11902),
    info(151543, "West Indies tour of India, 2026", "2nd ODI", "ODI", ms(2026, 9, 30, 14, 0), "Preview", "", "India/IND",
         "West Indies/WI", sid=11902))
SERIES_11595 = next_page(info(147898, "Australia tour of South Africa, 2026", "2nd ODI", "ODI", 1790496000000, "complete",
                              "", "South Africa/RSA", "Australia/AUS", sid=11595))

# The Times of India story about Ayush Mhatre's practice match (not on Cricbuzz), as first published.
TOI_URL = ("https://timesofindia.indiatimes.com/sports/cricket/news/back-from-hamstring-injury-ayush-mhatre-to-play-his-"
           "first-competitive-match-in-5-months/articleshow/1.cms")
TOI_TITLE = "Back from hamstring injury, Ayush Mhatre to play his first competitive match in 5 months"
TOI_PAGE = ("<html><body><div>" + TOI_TITLE + " | Cricket News - The Times of India "
            + "Menu Cricket Asian Games IND Vs AFG IND Vs WI NFL NBA NHL " * 10
            + "TOI Sports Desk / Updated: Sep 28, 2026, 12:09 IST Today</div>"       # real page header: no full stops
            "<p>In a major boost to Mumbai ahead of the 2026-27 Ranji Trophy season, opener Ayush Mhatre, out of action "
            "since he suffered a bad hamstring injury while playing for the Chennai Super Kings during IPL-2026, will play "
            "his first competitive match in almost five months when he will turn out in a practice match amongst Mumbai's "
            "Ranji Trophy probables at the Cricket Club of India on Tuesday.</p><p>\"He will play in a two-day practice "
            "match, organised by the Mumbai Cricket Association for Mumbai's Ranji Trophy probables, at the Brabourne "
            "Stadium from Tuesday,\" Ayush's father Yogesh Mhatre told TOI on Monday.</p>"
            "<div>Videos: Speaker Gets October 10 Deadline</div></body></html>")      # sidebar date must be ignored
TOI_PAGE_UPDATED = ("<html><body><p>MUMBAI: In a major boost to Mumbai ahead of the 2026-27 Ranji Trophy season, opener "
                    "Ayush Mhatre, out of action since he suffered a bad hamstring injury, will play his first competitive "
                    "match in almost five months.</p><p>He will turn out for Sainath Cricket Club in a Kanga League 'B "
                    "Division game on Friday,\" Ayush's father Yogesh Mhatre told TOI on Monday.</p></body></html>")
TOI_RSS = (f"<rss><channel><item><title>{TOI_TITLE}</title><link>{TOI_URL}</link>"
           "<pubDate>Mon, 28 Sep 2026 06:39:00 GMT</pubDate>"                                  # 12:09 IST
           "<description>In a major boost to Mumbai ahead of the 2026-27 Ranji Trophy season, opener Ayush Mhatre, out "
           "of action since he suffered a bad hamstring injury while playing for the Chennai…</description>"
           "</item></channel></rss>")


class Base(unittest.TestCase):
    """Fresh state.db, fake web (self.pages: url or url fragment -> page), fixed clock, recorded alerts."""
    patch_send = True

    def setUp(self):
        CLOCK[0] = T0
        FakeTime.slept = []
        self.tmp = tempfile.mkdtemp()
        self.cfg = t.load_config()
        self.roster = self.cfg["roster"]
        self.names = [p["name"] for p in self.roster]
        self.pages, self.fetched, self.sent = {}, [], []
        self.out, self.err = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(t, "datetime", FakeDatetime), mock.patch.object(t, "time", FakeTime),
                   mock.patch.object(t, "http_get", self.fake_get), mock.patch.object(t, "HERE", self.tmp),
                   mock.patch.object(t, "alerts_sent", 0), mock.patch.object(t, "last_send", 0.0),
                   mock.patch.object(t, "outbox_db", None), mock.patch("sys.stdout", self.out),
                   mock.patch("sys.stderr", self.err)]
        if self.patch_send:
            patches.append(mock.patch.object(t, "send_alert", self.fake_send))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.db = t.db_connect(os.path.join(self.tmp, "state.db"))
        self.addCleanup(self.db.close)
        t.set_other_names(self.cfg, self.db, self.names)

    def fake_get(self, url, timeout=20, **kw):
        self.fetched.append(url)
        self.last_kw = kw
        for key, page in self.pages.items():
            if key == url or key in url:
                return page() if callable(page) else page
        raise OSError(f"offline test: no page for {url}")

    def fake_send(self, cfg, text, dry_run=False, silent=False):
        t.alerts_sent += 1
        self.sent.append({"text": text, "silent": silent, "dry_run": dry_run})

    def texts(self):
        return [s["text"] for s in self.sent]

    def process(self, feed, first_run=False, names=None):
        latest = []
        items = t.parse_rss(feed) if isinstance(feed, str) else feed
        t.process_items(self.cfg, self.db, items, names or self.names, True, latest, first_run)
        return latest

    def cricbuzz(self, schedule=None):
        """Serve the Cricbuzz fixtures: live scores, schedule, series lists and squad pages."""
        self.pages.update({self.cfg["live_scores_url"]: LIVE_PAGE,
                           self.cfg["schedule_url"]: lambda: self.schedule,
                           "/cricket-series/11902/": SERIES_11902, "/cricket-series/11595/": SERIES_11595})
        self.pages.update({f"cricket-match-squads/{k}": v for k, v in SQUAD_PAGES.items()})
        self.schedule = sched_page(*(schedule if schedule is not None else (WI_ODI2, AUS_ODI3, EARLY, FAR)))


# =====================================================================================================
class TestConfig(Base):
    def test_required_keys(self):
        for k in ("alert", "intervals_seconds", "team_queries", "news_search_urls", "extra_rss_feeds", "official_feeds",
                  "roster_sources", "live_scores_url", "schedule_url", "squads_url", "series_matches_url",
                  "ipl_archive_url", "muted_players", "namesakes", "roster"):
            self.assertIn(k, self.cfg)

    def test_roster_has_all_30_players_with_unique_cricbuzz_ids(self):
        ids = [p["id"] for p in self.roster]
        self.assertEqual(len(self.roster), 30)
        self.assertEqual(len(set(ids)), 30)
        self.assertTrue(all(i.isdigit() for i in ids))
        for name in ("MS Dhoni", "Ruturaj Gaikwad", "Aman Khan", "Spencer Johnson", "Gurjapneet Singh", "Nathan Ellis"):
            self.assertIn(name, self.names)

    def test_url_templates(self):
        for u in self.cfg["news_search_urls"]:
            self.assertIn("{q}", u)
        self.assertIn("{id}", self.cfg["squads_url"])
        self.assertIn("{sid}", self.cfg["series_matches_url"])
        self.assertIn("{year}", self.cfg["ipl_archive_url"])
        self.assertIn("{id}", self.cfg["scorecard_url"])
        self.assertEqual(self.cfg["intervals_seconds"]["performance"], 3600)          # live figures every hour

    def test_alert_settings(self):
        a = self.cfg["alert"]
        self.assertEqual((a["telegram_bot_token_env"], a["telegram_chat_id_env"]), ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))
        self.assertIn(a["min_importance"], t.RANKS)
        self.assertGreater(a["quiet_update_hours"], 0)
        self.assertIn(a["match_digest_hour"], range(24))

    def test_namesakes_are_other_people(self):
        self.assertFalse(set(self.cfg["namesakes"]) & set(self.names))


class TestRepoHygiene(unittest.TestCase):
    def read(self, *path):
        with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
            return f.read()

    def test_no_bot_token_in_any_committed_file(self):
        for name in ("tracker.py", "config.json", "README.md", "CLAUDE.md", "test_offline.py", ".gitignore",
                     os.path.join(".github", "workflows", "tracker.yml")):
            self.assertIsNone(re.search(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}", self.read(name)), name)

    def test_state_and_dashboard_are_not_committed(self):
        ignored = self.read(".gitignore").split()
        self.assertIn("state.db", ignored)
        self.assertIn("dashboard.html", ignored)

    def test_workflow(self):
        y = self.read(".github", "workflows", "tracker.yml")
        self.assertIn('cron: "*/10 * * * *"', y)
        self.assertIn("workflow_dispatch", y)
        self.assertRegex(y, r"run: python tracker\.py --once\s*\n")            # a real run, never --dry-run
        self.assertIn("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}", y)
        self.assertIn("TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}", y)
        self.assertRegex(y, r"path: state\.db\s*\n\s*key: state-\$\{\{ github\.run_id \}\}\s*\n\s*restore-keys: state-")
        self.assertIsNone(re.search(r"\{[^}\n]*\$\{\{", y), "a {...} mapping holding ${{ }} breaks the YAML (29 Sep bug)")
        for action in ("actions/checkout@v7", "actions/setup-python@v7", "actions/cache@v6"):    # Node 24 versions
            self.assertIn(action, y)

    def test_test_workflow_runs_the_suite_on_every_push(self):
        y = self.read(".github", "workflows", "tests.yml")
        self.assertRegex(y, r"on:\s*\n\s*push:")
        self.assertIn("python test_offline.py", y)
        self.assertNotIn("TELEGRAM", y)                                        # tests never get the real bot


# =====================================================================================================
class TestRssParsing(Base):
    def test_rss_item(self):
        [it] = t.parse_rss(rss(["Nathan Ellis suffered a side issue - Cricinfo"], when=at(2026, 9, 27, 17, 14),
                               source="Cricinfo"))
        self.assertEqual((it["title"], it["source"], it["when"]), ("Nathan Ellis suffered a side issue - Cricinfo",
                                                                     "Cricinfo", at(2026, 9, 27, 17, 14)))

    def test_atom_entry_from_youtube(self):
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Hitting the Right Areas with Jamie Overton</title>'
                '<link rel="alternate" href="https://www.youtube.com/watch?v=bOYRjyFr3J8"/>'
                '<published>2026-09-27T09:12:00+00:00</published></entry></feed>')
        [it] = t.parse_rss(feed)
        self.assertEqual(it["link"], "https://www.youtube.com/watch?v=bOYRjyFr3J8")
        self.assertEqual(it["when"], at(2026, 9, 27, 14, 42))

    def test_bing_redirect_link_source_and_summary(self):
        feed = ('<rss xmlns:News="https://www.bing.com/news/search?q=x&amp;format=rss"><channel><item>'
                '<title>Different challenge: Big shoes for Zaheer Khan to fill as Chennai Super Kings coach</title>'
                '<link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.telegraphindia.com'
                '%2fsports%2fcricket%2fzaheer&amp;c=9</link><description>There were several names doing the rounds as '
                'probable replacements once Fleming left the job after IPL 2026. More text.</description>'
                '<pubDate>Sat, 26 Sep 2026 02:59:00 GMT</pubDate><News:Source>Telegraph India</News:Source></item>'
                '</channel></rss>')
        [it] = t.parse_rss(feed)
        self.assertEqual(it["link"], "https://www.telegraphindia.com/sports/cricket/zaheer")
        self.assertEqual(it["source"], "Telegraph India")
        self.assertEqual(it["summary"], "There were several names doing the rounds as probable replacements once "
                                        "Fleming left the job after IPL 2026.")

    def test_google_description_that_repeats_the_headline_is_no_summary(self):
        feed = ('<rss><channel><item><title>chennai super kings - cricbuzz.com</title><source>cricbuzz.com</source>'
                '<description>&lt;a href="https://news.google.com/x"&gt;chennai super kings&lt;/a&gt;&amp;nbsp;'
                '&lt;font&gt;cricbuzz.com&lt;/font&gt;</description></item></channel></rss>')
        self.assertEqual(t.parse_rss(feed)[0]["summary"], "")

    def test_long_summary_is_cut_to_one_short_line(self):
        s = t.one_line("In a dramatic turn of events at the 2026 Asian Games, India's men's cricket team has reached the "
                       "semi-finals after their quarter-final against Hong Kong, China was called off due to rain. Set to "
                       "clash", "India through to Asian Games semis", "TOI")
        self.assertTrue(s.startswith("In a dramatic turn of events") and len(s) <= 180 and "Set to clash" not in s)

    def test_bad_date_is_none(self):
        self.assertIsNone(t.parse_rss("<rss><channel><item><title>X Y</title><pubDate>not a date</pubDate></item>"
                                      "</channel></rss>")[0]["when"])


class TestArticleFilter(Base):
    def test_pages_that_are_not_stories(self):
        for title in ["chennai super kings - Cricbuzz", "CSK vs SRH, 63rd Match, Indian Premier League 2026 - Squads - Cricbuzz",
                      "GT vs CSK Live Full Scorecard", "CSK Squad IPL 2026",
                      "Live Cricket Score, Schedule, Latest News, Stats & Videos - cricbuzz.com",
                      "Chennai Super Kings Cricket Team News & Matches", "INDIA CRICKET TEAM NEWS",
                      "South Africa Cricket Team News"]:
            self.assertFalse(t.is_article(title), title)

    def test_real_stories_are_kept(self):
        for title in ["India cricket team news: Gill ruled out of 2nd ODI",
                      "India vs West Indies 2026: Full Schedule, Squads, Live Streaming And All You Need To Know",
                      "Nathan Ellis suffered a side issue - Cricinfo"]:
            self.assertTrue(t.is_article(title), title)


class TestRanking(Base):
    def test_breaking(self):
        for title in ["MS Dhoni retires from IPL, CSK confirm", "Ellis, Davies ruled out to further deplete Aussies",
                      "Different challenge: Big shoes for Zaheer Khan to fill as Chennai Super Kings coach",
                      "Zaheer Khan Reveals How He Became CSK's Head Coach For IPL 2027",
                      "‘The game gave me many chapters’ - Zaheer Khan reflects on journey after CSK appointment"]:
            self.assertEqual(t.classify(title), ("breaking", False), title)

    def test_important(self):
        for title in ["Nathan Ellis suffered a side issue", "Ellis goes down as Aussies fall under another spin spell",
                      "Back from hamstring injury, Ayush Mhatre to play his first competitive match in 5 months",
                      "South Africa announce 18-member squad for 1st Test vs Australia: Brevis dropped, 3 injured pacers unavailable"]:
            self.assertEqual(t.classify(title)[0], "important", title)

    def test_minor_and_the_appointment_exception(self):
        self.assertEqual(t.classify("Mohammad Yousuf names MS Dhoni appointment as decisive moment in Indian cricket's rise"),
                         ("minor", False))
        self.assertEqual(t.classify("Proteas coach questions Dewald Brevis’ suitability")[0], "minor")

    def test_rumours_are_labelled_and_never_breaking(self):
        self.assertEqual(t.classify("Sanju Samson reportedly set to be named CSK vice-captain"), ("important", True))
        self.assertEqual(t.classify("Dhoni could retire after IPL 2027"), ("important", True))

    def test_clickbait_style_is_kept_and_standout_performances_rank_important(self):
        self.assertEqual(t.classify("SHOCKING: Dhoni viral video breaks the internet"), ("minor", False))
        self.assertEqual(t.classify("WATCH: Matt Short’s Jaw-Dropping One-Handed Catch To Dismiss Labuschagne")[0],
                         "important")
        self.assertEqual(t.classify("Noor Ahmad takes hat-trick in SA20 opener")[0], "important")


class TestTagging(Base):
    def tags(self, title):
        return t.tag_item(title, self.names, set())

    def test_full_name_and_team(self):
        self.assertEqual(self.tags("Moeen Ali Reveals Why MS Dhoni and Stephen Fleming Made CSK the Dream Combo"),
                         ["CSK", "MS Dhoni"])

    def test_surname_alone(self):
        self.assertEqual(self.tags("Ellis, Davies ruled out to further deplete Aussies - cricket.com.au"), ["Nathan Ellis"])
        self.assertEqual(self.tags("South Africa announce squad: Brevis dropped"), ["Dewald Brevis"])
        self.assertEqual(self.tags("Cox and Overton collide"), ["Jamie Overton"])

    def test_middle_name_and_spelling_variants(self):
        self.assertIn("Macneil Noronha", self.tags("IPL 2026: Meet Macneil Hadley Noronha, UAE-raised Karnataka all-rounder"))
        self.assertEqual(self.tags("Rob Walter on preparing Zak Foulkes to play an allrounder role"), ["Zakary Foulkes"])
        self.assertEqual(self.tags("WATCH: Matt Short’s Jaw-Dropping One-Handed Catch To Dismiss Labuschagne"),
                         ["Matthew Short"])                              # not Matt Henry

    def test_other_cricketers_full_names_are_not_ours(self):
        for title in ["Kuldeep Yadav Overtakes Venkatesh Prasad To Become India's 9th Highest Wicket-Taker In ODIs",
                      "Rhythm, control and a Kuldeep Yadav masterclass", "KL Rahul guides Delhi to third straight IPL victory",
                      "England Call Up Hat-Trick Hero Henry Crocombe For Sri Lanka ODI Series"]:
            self.assertEqual(self.tags(title), [], title)
        self.assertEqual(self.tags("Ex-India Fast Bowler Zaheer Khan Appointed Chennai Super Kings Head Coach"), ["CSK"])

    def test_first_name_alone_only_in_a_cricket_headline(self):
        self.assertEqual(self.tags("3rd T20I: Noor, Naib out as Afghanistan elect to bowl against India"), ["Noor Ahmad"])
        self.assertIn("Ruturaj Gaikwad", self.tags("Iyer's absence, Ruturaj's gain: CSK skipper cashing in on WI ODIs"))
        self.assertEqual(self.tags("England twins Jamie and Craig Overton will play Big Bash League"), ["Jamie Overton"])
        self.assertIn("Prashant Veer", self.tags("UPT20 2026: Prashant Veers all round show help Noida Kings win"))
        self.assertEqual(self.tags("\"He Was The Perfect Pro Wrestler\": Matt Cardona Mourns PAC’s Death At 40"), [])

    def test_nicknames_only_in_a_cricket_headline(self):
        self.assertEqual(self.tags("Mahi bhai back in the nets ahead of IPL 2027"), ["MS Dhoni"])
        self.assertEqual(self.tags("Thala Ajith's new film to release on Diwali"), [])
        self.assertIn("MS Dhoni", self.tags("POV: Thala smiled, and suddenly everything feels better! #WhistlePodu"))

    def test_passing_mentions_are_not_about_the_player(self):
        for title in ["IND vs WI: Shubman Gill slams 10th ODI century, joins MS Dhoni in elite list",
                      "Temba Bavuma equals MS Dhoni's record with fantastic century vs Australia",
                      "David Miller equals ODI middle-order century record with stunning 142; surpasses MS Dhoni",
                      "Is Dhruv Jurel the Next MS Dhoni? India's Wicketkeeper Succession Explained",
                      "‘Dhoni’s 2011 six inspired me to take up cricket,’ says Aashirwad Swain",
                      "Star player from MS Dhoni’s T20 World Cup winning team applies for selector's job",
                      "Virat Kohli Rapid Fire: Rohit, Dhoni, Bumrah, Steyn Named",
                      "\"He Will Always Be My Captain\": Virat Kohli Shares His Feelings Towards MS Dhoni"]:
            self.assertNotIn("MS Dhoni", self.tags(title), title)

    def test_muted_players_and_dhoni_always_tracked(self):
        self.assertEqual(t.tag_item("Nathan Ellis suffered a side issue", self.names, {"Nathan Ellis"}), [])
        self.assertEqual(t.tag_item("MS Dhoni smashes one out of sight in the CSK nets", [], set()), ["CSK", "MS Dhoni"])


# =====================================================================================================
class TestNewsPipeline(Base):
    def test_telegram_news_alert_format(self):
        when = at(2026, 9, 29, 6, 0)
        latest = self.process(rss(["Nathan Ellis suffered a side issue"], when=when, link="https://x.test/ellis",
                                  source="Cricinfo", summary="Ellis left the field. He will have scans."))
        self.assertEqual(len(latest), 1)
        self.assertEqual(self.texts(), ["⚠️ IMPORTANT | Nathan Ellis\nNathan Ellis suffered a side issue\n"
                                        "Ellis left the field.\nPublished 29 Sep 06:00 IST\nCricinfo\nhttps://x.test/ellis"])

    def test_rumour_flag_in_alert(self):
        self.process(rss(["Sanju Samson reportedly set to be named CSK vice-captain"]))
        self.assertTrue(self.texts()[0].startswith("⚠️ IMPORTANT [RUMOUR] | CSK, Sanju Samson\n"))

    def test_story_ages(self):
        latest = self.process(rss(["Shivam Dube hits fifty"], when=T0 - timedelta(days=10))
                              .replace("</channel></rss>", "")
                              + rss(["Ruturaj Gaikwad poised to strengthen India middle order"],
                                    when=T0 - timedelta(hours=30)).replace("<rss><channel>", ""))
        self.assertEqual((latest, self.sent), ([], []))
        self.assertFalse(t.is_duplicate(self.db, "Shivam Dube hits fifty"))                 # over 3 days: ignored
        self.assertTrue(t.is_duplicate(self.db, "Ruturaj Gaikwad poised to strengthen India middle order"))  # recorded

    def test_first_run_records_without_alerting(self):
        latest = self.process(rss(["Nathan Ellis suffered a side issue"]), first_run=True)
        self.assertEqual((len(latest), self.sent), (1, []))
        self.assertTrue(t.is_duplicate(self.db, "Nathan Ellis suffered a side issue"))

    def test_min_importance(self):
        self.cfg["alert"]["min_importance"] = "important"
        latest = self.process(rss(["Nathan Ellis suffered a side issue", "Jamie Overton - Older, wiser and still bowling fast"]))
        self.assertEqual([i["title"] for i in latest], ["Nathan Ellis suffered a side issue"])
        self.assertEqual(len(self.sent), 1)

    def test_irrelevant_stories_and_pages_are_dropped(self):
        latest = self.process(rss(["Unrelated news about football", "chennai super kings - Cricbuzz"]))
        self.assertEqual((latest, self.sent), ([], []))

    def test_highlight_clips_first_report_only(self):
        latest = self.process(rss(["WATCH: Matt Short’s Jaw-Dropping One-Handed Catch To Dismiss Marnus Labuschagne",
                                   "Matt Short takes stunning one-hand return catch to dismiss Marnus Labuschagne"]))
        self.assertEqual([i["title"][:16] for i in latest], ["WATCH: Matt Shor"])   # the copy is grouped away
        self.assertEqual(len(self.sent), 1)

    def test_official_youtube_posts(self):
        items = [{**i, "official": "CSK (official)"} for i in t.parse_rss(
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            f'<entry><title>Mood for today ✨ #WhistlePodu #Yellove</title><link href="https://yt/1"/>'
            f'<published>{T0.isoformat()}</published></entry>'
            f'<entry><title>Looks familiar \U0001f914 Think inside out, Superfans! #WhistlePodu #Yellove</title>'
            f'<link href="https://yt/2"/><published>{T0.isoformat()}</published></entry></feed>')]
        latest = self.process(items)
        self.assertEqual([i["tags"] for i in latest], [["CSK (official)"], ["CSK (official)"]])   # hashtags don't merge them
        self.assertFalse(any(i["rumour"] for i in latest))


class TestDuplicates(Base):
    ELLIS = ["Ellis and Davies out of the South Africa tour with injury - Cricinfo",
             "Nathan Ellis and Joel Davies out of final ODI against South Africa - thenewsmill.com",
             "Ellis, Davies ruled out to further deplete Aussies - cricket.com.au",
             "Injured Ellis ruled out of third ODI against South Africa - Cricbuzz",
             "AUS vs SA: Nathan Ellis, Joel Davies ruled out of third ODI in Potchefstroom - Yardbarker",
             "Nathan Ellis’ 3-fer vs SA | 1st ODI - Cricbuzz",
             "Nathan Ellis, Joel Davies leave Australia’s South Africa tour injured - Cricket Addictor",
             "Nathan Ellis, Joel Davies ruled out of 3rd ODI vs South Africa with injuries - CricTracker",
             "Australia suffer Nathan Ellis blow ahead of 3rd SA ODI - NewsBytes",
             "Double injury blow for Australia as Ellis, Davies ruled out of SA ODI",
             "Nathan Ellis ruled out of third Australia vs South Africa ODI; Joel Davies also sent home",
             "Australia faces setback ahead of final ODI against South Africa as Ellis and Davis withdraw"]

    def test_exact_and_near_duplicate_headlines(self):
        latest = self.process(rss(["MS Dhoni retires from IPL, CSK confirm - Cricbuzz",
                                   "MS Dhoni retirement from IPL: CSK confirm - ESPN",
                                   "MS Dhoni retires from IPL, CSK confirm - Cricbuzz"]))
        self.assertEqual(len(latest), 1)

    def test_publisher_suffix_bug(self):
        latest = self.process(rss(["Jamie Overton - Older, wiser and still bowling fast",
                                   "Jamie Overton - Older, wiser and still bowling fast - Cricbuzz"]))
        self.assertEqual(len(latest), 1)

    def test_same_story_from_twelve_publishers(self):
        latest = self.process(rss(self.ELLIS))
        self.assertEqual([i["title"] for i in latest], [self.ELLIS[0], self.ELLIS[2], self.ELLIS[5]])   # + higher rank
        self.assertEqual(len(self.sent), 3)                                  # Telegram gets exactly what the dashboard shows

    def test_grouping_ends_after_24_hours(self):
        self.process(rss(self.ELLIS[:1]))
        CLOCK[0] = T0 + timedelta(hours=25)
        self.assertEqual(len(self.process(rss(self.ELLIS[1:2]))), 1)

    def test_team_only_official_and_unclassified_stories_are_never_grouped(self):
        latest = self.process(rss(["IPL 2027 transfers: List of confirmed swaps, trade rumours as Hardik Pandya links with CSK",
                                   "Washington Sundar CSK Trade Rumor: Is It Actually Happening?"]))
        self.assertEqual(len(latest), 2)

    MOHIT = ["Mohit Sharma set to join CSK as bowling coach - The Times of India",                  # real, 29 Sep 2026
             "Mohit Sharma to join CSK as bowling coach - Cricbuzz",
             "Mohit Sharma joins Chennai Super Kings as bowling coach - CricTracker",
             "Mohit Sharma set for CSK return in new role ahead of 2027 IPL season - cricketaddictor.com",
             "India star Mohit Sharma to join Dhoni's CSK as new bowling coach for IPL 2027 - News24Online",
             "Mohit Sharma likely to become CSK bowling coach ahead of IPL 2027 - Sports Tiger",
             "Mohit Sharma set to join CSK in this role: Details - NewsBytes",
             "Mohit Sharma likely to join Chennai Super Kings as bowling coach ahead of IPL 2027",
             "IPL: Mohit Sharma set to join CSK as bowling coach"]

    def test_team_stories_about_one_person_are_grouped(self):
        latest = self.process(rss(self.MOHIT))
        self.assertEqual([i["title"] for i in latest], [self.MOHIT[0], self.MOHIT[2]])   # first report + "joins"
        self.assertEqual([s["text"].splitlines()[0] for s in self.sent],
                         ["⚠️ IMPORTANT [RUMOUR] | CSK", "🚨 BREAKING | CSK"])

    def test_who_a_team_story_is_about(self):
        self.assertEqual(t.story_person("IPL: Mohit Sharma set to join CSK as bowling coach", self.names), "Mohit Sharma")
        self.assertEqual(t.story_person("Washington Sundar CSK Trade Rumor: Is It Actually Happening?", self.names),
                         "Washington Sundar")
        self.assertEqual(t.story_person("Chennai Super Kings Announce Zaheer Khan As New Head Coach", self.names),
                         "Zaheer Khan")
        self.assertIsNone(t.story_person("IPL 2027 transfers: List of confirmed swaps, trade rumours", self.names))
        self.assertEqual(t.tag_item(self.MOHIT[4], self.names, set()), ["CSK"])        # "Dhoni's CSK" is CSK news
        self.assertEqual(t.story_event(self.MOHIT[1]), "coach")

    def test_event_kinds(self):
        self.assertNotEqual(t.story_event("Virat Kohli names his 'ideal ODI batter', leaves out Rohit Sharma and MS Dhoni"),
                            "injury")
        self.assertEqual(t.story_event("South Africa announce 18-member squad for 1st Test vs Australia: Brevis dropped, "
                                       "3 injured pacers unavailable"), "selection")
        self.assertEqual(t.story_event("Australia suffer Nathan Ellis blow ahead of 3rd SA ODI"), "injury")
        self.assertEqual(t.story_event("Nathan Ellis’ 3-fer vs SA | 1st ODI"), "performance")


# =====================================================================================================
class TestTelegram(Base):
    """The real send_alert: what reaches api.telegram.org."""
    patch_send = False

    def setUp(self):
        super().setUp()
        self.calls = []
        for p in (mock.patch.object(t.urllib.request, "urlopen", self.fake_urlopen),
                  mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:TESTTOKEN", "TELEGRAM_CHAT_ID": "42"})):
            p.start()
            self.addCleanup(p.stop)

    def fake_urlopen(self, url, data=None, timeout=None):
        self.calls.append((url, urllib.parse.parse_qs(data.decode())))

    def test_message_payload(self):
        t.send_alert(self.cfg, "📅 PLAYING TODAY: X")
        url, p = self.calls[0]
        self.assertEqual(url, "https://api.telegram.org/bot123:TESTTOKEN/sendMessage")
        self.assertEqual(p["chat_id"], ["42"])
        self.assertEqual(p["text"], ["📅 PLAYING TODAY: X\n🕒 29 Sep 08:05 IST"])
        self.assertEqual(p["disable_notification"], ["false"])
        self.assertEqual(t.alerts_sent, 1)

    def test_silent_message(self):
        t.send_alert(self.cfg, "✅ No new updates", silent=True)
        self.assertEqual(self.calls[0][1]["disable_notification"], ["true"])

    def test_dry_run_and_missing_settings_never_send(self):
        t.send_alert(self.cfg, "dry", dry_run=True)
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": ""}):
            t.send_alert(self.cfg, "no chat id")
        self.assertEqual(self.calls, [])
        self.assertIn("not sent: dry-run or Telegram not configured", self.out.getvalue())

    def test_failure_is_reported_not_raised(self):
        with mock.patch.object(t.urllib.request, "urlopen", side_effect=OSError("HTTP Error 401: Unauthorized")):
            t.send_alert(self.cfg, "x")
        self.assertIn("Telegram send failed: HTTP Error 401", self.err.getvalue())

    def http_error(self, code, body=b""):
        return t.urllib.error.HTTPError("https://api.telegram.org", code, "err", {}, io.BytesIO(body))

    def test_messages_are_paced_one_per_second(self):
        t.send_alert(self.cfg, "one")
        t.send_alert(self.cfg, "two")
        self.assertEqual(len(self.calls), 2)
        self.assertAlmostEqual(sum(FakeTime.slept), 1.1, places=3)

    def test_rate_limit_waits_and_retries(self):
        replies = [self.http_error(429, b'{"ok":false,"parameters":{"retry_after":7}}'), None]
        def urlopen(url, data=None, timeout=None):
            r = replies.pop(0)
            if r:
                raise r
            self.calls.append((url, urllib.parse.parse_qs(data.decode())))
        with mock.patch.object(t.urllib.request, "urlopen", urlopen):
            t.send_alert(self.cfg, "busy")
        self.assertEqual(len(self.calls), 1)
        self.assertIn(7, FakeTime.slept)

    def test_failed_message_waits_in_the_outbox_and_is_delivered_next_run(self):
        t.outbox_db = self.db
        with mock.patch.object(t.urllib.request, "urlopen", side_effect=OSError("network down")):
            t.send_alert(self.cfg, "📅 PLAYING TODAY: X")
        self.assertEqual(self.db.execute("select text from outbox").fetchall(), [("📅 PLAYING TODAY: X\n🕒 29 Sep 08:05 IST",)])
        t.flush_outbox(self.cfg, self.db)                                    # next run, Telegram reachable again
        self.assertEqual(self.calls[0][1]["text"], ["📅 PLAYING TODAY: X\n🕒 29 Sep 08:05 IST"])
        self.assertEqual(self.db.execute("select count(*) from outbox").fetchone(), (0,))

    def test_bad_request_is_not_retried(self):
        t.outbox_db = self.db
        tries = []
        def urlopen(url, data=None, timeout=None):
            tries.append(1)
            raise self.http_error(400)
        with mock.patch.object(t.urllib.request, "urlopen", urlopen):
            t.send_alert(self.cfg, "x")
        self.assertEqual(len(tries), 1)

    def test_long_messages_are_split(self):
        text = "\n".join(f"• line {i} " + "x" * 90 for i in range(100))       # about 10,000 characters
        t.send_alert(self.cfg, text)
        parts = [c[1]["text"][0] for c in self.calls]
        self.assertGreaterEqual(len(parts), 3)
        self.assertTrue(all(len(p) <= 4096 for p in parts))
        self.assertEqual("\n".join(parts), text + "\n🕒 29 Sep 08:05 IST")      # nothing lost, stamp at the end

    def test_news_alert_end_to_end(self):
        latest = []
        t.process_items(self.cfg, self.db, t.parse_rss(rss(["Nathan Ellis suffered a side issue"], link="https://x.test/e")),
                        self.names, False, latest, False)
        self.assertEqual(self.calls[0][1]["text"][0], "⚠️ IMPORTANT | Nathan Ellis\nNathan Ellis suffered a side issue\n"
                                                      "Published 29 Sep 08:05 IST\nhttps://x.test/e\n🕒 29 Sep 08:05 IST")


# =====================================================================================================
class TestRoster(Base):
    def team_page(self, roster):
        return "".join(f'<a href="/profiles/{p["id"]}/x" title="{p["name"]}"></a>' for p in roster)

    def setUp(self):
        super().setUp()
        missing = {"19473", "13143", "15727", "50444", "29181", "13537"}           # not on Cricbuzz's team page (real)
        self.page = self.team_page([p for p in self.roster if p["id"] not in missing])
        ids = {"CSK": 58, "GT": 59, "RR": 64}
        ipl = lambda mid, state, t1, t2: {"matchInfo": {"matchId": mid, "seriesId": 9241, "matchDesc": "M", "state": state,
                                                        "team1": {"teamId": ids[t1], "teamSName": t1},
                                                        "team2": {"teamId": ids[t2], "teamSName": t2}}}
        csk = [(int(p["id"]), p["name"]) for p in self.roster]
        self.pages.update({
            self.cfg["roster_sources"][0]: lambda: self.page,
            "scorecard-archives": '<a href="/cricket-series/9241/indian-premier-league-2026/matches">IPL</a>'
                                  '<a href="/cricket-series/9249/indian-premier-league-2027/matches">IPL</a>',
            "9249/indian-premier-league-2027/matches": next_page(ipl(160001, "Preview", "CSK", "GT")),   # not started
            "9241/indian-premier-league-2026/matches": next_page(ipl(149640, "complete", "RR", "CSK"),
                                                                 ipl(152229, "complete", "GT", "CSK")),
            "cricket-match-squads/149640": next_page(squads("CSK", "playing XI", csk[:11]), squads("CSK", "bench", csk[11:])),
            "cricket-match-squads/152229": next_page(squads("CSK", "playing XI", csk[:11]))})

    def test_parse_roster_page(self):
        page = ('<a href="/profiles/265/ms-dhoni" title="MS Dhoni"></a><a href="/profiles/24391/zakary-foulkes" '
                'title="Zakary Foulkes"></a><a href="/profiles/265/ms-dhoni" title="MS Dhoni"></a>')
        self.assertEqual(t.parse_roster_page(page), [{"id": "265", "name": "MS Dhoni"}, {"id": "24391", "name": "Zakary Foulkes"}])

    def test_team_page_plus_ipl_season_squads(self):
        got = t.refresh_roster(self.cfg, self.db, True, False)
        self.assertEqual({p["id"] for p in got}, {p["id"] for p in self.roster})
        self.assertEqual(self.sent, [])                                     # first fetch: nothing to compare with

    def test_squad_change_alert_and_released_players(self):
        t.refresh_roster(self.cfg, self.db, True, False)
        self.page = self.page.replace('title="Sanju Samson"', 'title="x"').replace("/profiles/8271/", "/profiles/0/")
        got = t.refresh_roster(self.cfg, self.db, True, False)
        self.assertNotIn("Sanju Samson", {p["name"] for p in got})          # not kept via old season squads
        self.assertTrue(self.texts()[0].startswith("🔁 CSK SQUAD CHANGE\nAdded: x\nRemoved: Sanju Samson"))

    def test_bad_source_keeps_what_we_have(self):
        t.refresh_roster(self.cfg, self.db, True, False)
        self.page = self.team_page(self.roster[:5])                          # fails the 15-40 sanity check
        self.assertEqual(len(t.refresh_roster(self.cfg, self.db, True, False)), 30)
        self.pages.clear()
        fresh = t.db_connect(os.path.join(self.tmp, "fresh.db"))
        self.assertEqual(t.refresh_roster(self.cfg, fresh, True, False), self.roster)   # nothing saved: the seed

    def test_ipl_season_helpers(self):
        self.assertEqual(t.ipl_series_urls('/cricket-series/9241/indian-premier-league-2026 /cricket-series/9249/'
                                           'indian-premier-league-2027'),
                         ["https://www.cricbuzz.com/cricket-series/9249/indian-premier-league-2027/matches",
                          "https://www.cricbuzz.com/cricket-series/9241/indian-premier-league-2026/matches"])
        self.assertEqual([m["id"] for m in t.team_matches(self.pages["9241/indian-premier-league-2026/matches"], 58)],
                         ["149640", "152229"])
        self.assertEqual(t.team_matches(self.pages["9249/indian-premier-league-2027/matches"], 58), [])


# =====================================================================================================
class TestMatchParsing(Base):
    def test_match_states(self):
        for state, phase in [("In Progress", "live"), ("Innings Break", "live"), ("Stumps", "paused"), ("Preview", "upcoming"),
                             ("Toss", "upcoming"), ("Scheduled", "upcoming"), ("Complete", "finished"),
                             ("complete", "finished"), ("Abandon", "finished"), ("Weird State", "unknown"), (None, "unknown")]:
            self.assertEqual(t.match_phase(state), phase, state)

    def test_live_scores_page(self):
        m = {x["id"]: x for x in t.parse_live_scores(LIVE_PAGE)}
        self.assertEqual({k: v["phase"] for k, v in m.items()}, {"151532": "finished", "155499": "paused",
                                                                "155422": "upcoming", "900001": "live", "900002": "unknown"})
        self.assertEqual(t.ist(m["151532"]["start"]), "27 Sep 14:00 IST")
        self.assertEqual(t.ist(m["155422"]["start"]), "29 Sep 09:30 IST")
        self.assertTrue(m["151532"]["url"].endswith("/151532/wi-vs-ind-1st-odi-west-indies-tour-of-india-2026"))
        self.assertEqual((m["155422"]["format"], m["155422"]["desc"]), ("TEST", "2nd unofficial Test"))

    def test_schedule_page(self):
        m = {x["id"]: x for x in t.parse_schedule(sched_page(WI_ODI2, FAR))}
        self.assertEqual((m["151543"]["phase"], m["151543"]["series_id"], t.ist(m["151543"]["start"])),
                         ("upcoming", "11902", "30 Sep 14:00 IST"))

    def test_squads_page(self):
        sq = t.parse_squads(SQUAD_PAGES["151532"])
        self.assertEqual(sq["11813"], {"name": "Ruturaj Gaikwad", "team": "IND", "group": "playing XI"})
        self.assertNotIn("308", sq)                                          # support staff left out

    def test_players_team_and_opponent(self):
        matches = t.parse_live_scores(LIVE_PAGE)
        hits = t.players_in_matches(matches, self.roster, {k: t.parse_squads(v) for k, v in SQUAD_PAGES.items()})
        k = next(h for h in hits if h["player"] == "Anshul Kamboj")
        self.assertEqual((k["team"], k["opponent"], k["role"]), ("India A", "Australia A", "squad"))
        roles = {h["player"]: h["role"] for h in hits}
        self.assertEqual((roles["Ruturaj Gaikwad"], roles["Noor Ahmad"], roles["Dewald Brevis"]), ("xi", "bench", "xi"))

    def test_squads_are_cached(self):
        self.pages["cricket-match-squads/155422"] = SQUAD_PAGES["155422"]
        m = {"id": "155422", "state": "Preview"}
        t.get_squads(self.cfg, self.db, m)
        t.get_squads(self.cfg, self.db, m)
        self.assertEqual(len(self.fetched), 1)                               # within 30 min, same state
        t.get_squads(self.cfg, self.db, {"id": "155422", "state": "Toss"})
        self.assertEqual(len(self.fetched), 2)                               # state changed

    def test_day_labels(self):
        self.assertEqual(t.day_label(at(2026, 9, 29, 23, 0), T0), "TODAY")
        self.assertEqual(t.day_label(at(2026, 9, 30, 5, 30), T0), "TOMORROW")   # not "today" just because it's < 24 h


class TestMatchAlerts(Base):
    def setUp(self):
        super().setUp()
        self.cricbuzz()

    def run_at(self, *when):
        CLOCK[0] = at(*when)
        return t.check_matches(self.cfg, self.db, self.roster, True)

    def test_playing_now_once_and_only_live_xi(self):
        self.run_at(2026, 9, 29, 1, 44)
        self.run_at(2026, 9, 29, 1, 54)
        now = [x for x in self.texts() if x.startswith("🏏")]
        self.assertEqual(len(now), 1)
        self.assertTrue(now[0].startswith("🏏 PLAYING NOW: Dewald Brevis (Paarl Royals, playing XI)\nStarted 27 Sep 14:00 IST\n"
                                          "Paarl Royals vs MI Cape Town, 5th Match · T20\nSA20 2027\nPaarl Royals opt to bat"))
        self.assertNotIn("Noor Ahmad", now[0])                               # bench
        self.assertNotIn("Sanju Samson", " ".join(self.texts()))             # unknown state: never live

    def test_before_the_digest_only_early_matches_are_announced(self):
        self.schedule = sched_page(WI_ODI2, AUS_ODI3, EARLY, FAR)
        self.run_at(2026, 9, 29, 1, 44)
        new = [x for x in self.texts() if x.startswith("📅 NEW MATCH")]
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0].startswith("📅 NEW MATCH FOR CSK PLAYERS\n\nTODAY (Tue 29 Sep)\n• Guyana Amazon Warriors vs "
                                          "Trinbago Knight Riders · T20 · starts 06:00 IST\n   Akeal Hosein (Guyana Amazon "
                                          "Warriors, in squad)"))
        self.assertNotIn("Kamboj", new[0])
        self.assertNotIn("TOMORROW", new[0])

    def test_morning_digest(self):
        t.mark_seen(self.db, "Ellis, Davies ruled out to further deplete Aussies - cricket.com.au", at(2026, 9, 28, 19, 34))
        self.run_at(2026, 9, 29, 8, 5)
        [digest] = [x for x in self.texts() if x.startswith("📅 CSK PLAYERS' MATCHES")]
        for part in ["TODAY (Tue 29 Sep)\n", "Dewald Brevis (Paarl Royals, playing XI)", "LIVE now",
                     "Ayush Mhatre (India U19, playing XI)", "Stumps",
                     "• India A vs Australia A · TEST · starts 09:30 IST\n   Anshul Kamboj (India A, in squad)",
                     "TOMORROW (Wed 30 Sep)\n• India vs West Indies · ODI · starts 14:00 IST\n   Ruturaj Gaikwad (India, "
                     "expected, squad not out yet)\n   2nd ODI, West Indies tour of India, 2026",
                     "• South Africa vs Australia · ODI · starts 17:00 IST\n   Nathan Ellis (Australia, expected, squad not "
                     "out yet)\n      ⚠️ injury news: “Ellis, Davies ruled out to further deplete Aussies” (28 Sep)"]:
            self.assertIn(part, digest)
        for absent in ("Sanju", "England", "Guyana"):                         # unknown state, 3 days out, already started
            self.assertNotIn(absent, digest)
        self.assertLess(digest.index("TODAY"), digest.index("TOMORROW"))
        self.run_at(2026, 9, 29, 8, 15)
        self.assertEqual(sum(x.startswith("📅 CSK PLAYERS'") for x in self.texts()), 1)   # once a day

    def test_new_match_after_the_digest(self):
        self.run_at(2026, 9, 29, 8, 5)
        self.schedule = sched_page(WI_ODI2, FAR, LATER)
        self.run_at(2026, 9, 29, 15, 0)
        self.run_at(2026, 9, 29, 15, 10)
        new = [x for x in self.texts() if x.startswith("📅 NEW MATCH")]
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0].startswith("📅 NEW MATCH FOR CSK PLAYERS\n\nTOMORROW (Wed 30 Sep)\n• Bangladesh vs Afghanistan "
                                          "· T20 · starts 18:00 IST\n   Noor Ahmad (Afghanistan, in squad)"))

    def test_quiet_day_is_one_silent_line(self):
        t.check_matches(self.cfg, self.db, [], True)
        self.assertEqual(self.sent, [{"text": "📅 No CSK player has a match today or tomorrow.", "silent": True,
                                      "dry_run": True}])

    def test_cricbuzz_down_does_not_stop_match_alerts(self):
        self.pages = {}
        self.assertEqual(t.check_matches(self.cfg, self.db, self.roster, True), [])
        self.assertIn("no match data on Cricbuzz", self.err.getvalue())

    def test_expected_player_missing_from_the_published_squad_gets_a_correction(self):
        self.run_at(2026, 9, 29, 8, 5)                                       # digest: Ellis and Ruturaj "expected"
        self.pages["cricket-match-squads/147909"] = next_page(               # real: Ellis ruled out of the 3rd ODI
            squads("AUS", "Squad", [(90010, "Mitchell Marsh")]), squads("RSA", "Squad", [(90011, "Temba Bavuma")]))
        self.pages["cricket-match-squads/151543"] = next_page(squads("IND", "Squad", [(11813, "Ruturaj Gaikwad")]))
        self.run_at(2026, 9, 29, 12, 0)
        self.run_at(2026, 9, 29, 12, 40)
        fixes = [x for x in self.texts() if x.startswith("✏️")]
        self.assertEqual(fixes, ["✏️ CORRECTION: Nathan Ellis\nEarlier alert said: expected for South Africa vs Australia, "
                                 "3rd ODI (Australia), starts 30 Sep 17:00 IST.\nThe squad is now published and doesn't "
                                 "include him.\nhttps://www.cricbuzz.com/live-cricket-scores/147909"])   # once; not Ruturaj

    def test_series_lists_are_cached(self):
        m = {x["id"]: x for x in t.parse_schedule(sched_page(WI_ODI2))}["151543"]
        t.expected_squads(self.cfg, self.db, m)
        t.expected_squads(self.cfg, self.db, m)
        self.assertEqual(sum("/cricket-series/11902/" in u for u in self.fetched), 1)
        CLOCK[0] += timedelta(minutes=31)
        t.expected_squads(self.cfg, self.db, m)
        self.assertEqual(sum("/cricket-series/11902/" in u for u in self.fetched), 2)

    def test_badges(self):
        hits = self.run_at(2026, 9, 29, 8, 5)
        by = {h["player"]: t.describe(h, CLOCK[0])[1] for h in hits}
        self.assertEqual(by["Dewald Brevis"], "PLAYING NOW (started 27 Sep 14:00 IST)")
        self.assertEqual(by["Anshul Kamboj"], "PLAYING TODAY, starts 29 Sep 09:30 IST, in squad")
        self.assertEqual(by["Ruturaj Gaikwad"], "PLAYING TOMORROW, starts 30 Sep 14:00 IST, expected, squad not out yet")
        self.assertEqual(by["Sanju Samson"], "Cricbuzz state 'Weird State': ?")


# =====================================================================================================
class TestNewsOnlyMatches(Base):
    PUB = at(2026, 9, 28, 12, 9)                                             # Monday

    def test_finding_the_day(self):
        self.assertIsNone(t.news_match_days("", "Ayush Mhatre", self.PUB))
        cases = [("Ruturaj Gaikwad set to play 2nd ODI tomorrow", "Ruturaj Gaikwad", (at(2026, 9, 29).date(), 1)),
                 ("Dhoni will play on 30 September at Chepauk", "MS Dhoni", (at(2026, 9, 30).date(), 1)),
                 ("Sanju Samson will play today", "Sanju Samson", (at(2026, 9, 28).date(), 1)),
                 ("Sanju Samson named in India squad for Asia Cup", "Sanju Samson", None),         # no day
                 ("Shivam Dube will play on Friday", "Sanju Samson", None)]                        # someone else
        for text, player, want in cases:
            self.assertEqual(t.news_match_days(text, player, self.PUB), want, text)

    def test_article_with_header_sidebar_and_two_day_match(self):
        self.pages[TOI_URL] = TOI_PAGE
        self.assertEqual(t.news_match_days(t.article_text(TOI_URL), "Ayush Mhatre", self.PUB), (at(2026, 9, 29).date(), 2))

    def ayush(self, page=TOI_PAGE):
        self.pages.update({TOI_URL: lambda: self.page, self.cfg["live_scores_url"]: next_page(),
                           self.cfg["schedule_url"]: next_page()})
        self.page = page

    def run_at(self, *when):
        CLOCK[0] = at(*when)
        t.check_matches(self.cfg, self.db, self.roster, True)

    def test_found_announced_and_listed_on_both_days(self):
        self.ayush()
        CLOCK[0] = at(2026, 9, 28, 20, 0)
        self.process(TOI_RSS)
        self.process(TOI_RSS)
        self.assertEqual(self.fetched.count(TOI_URL), 1)                     # each story read once
        self.db.execute("insert into kv values('digest:2026-09-28', '1')")  # Monday's digest already went out
        self.run_at(2026, 9, 28, 20, 0)
        self.run_at(2026, 9, 29, 3, 0)
        new = [x for x in self.texts() if x.startswith("📅 NEW MATCH")]
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0].startswith(
            "📅 NEW MATCH FOR CSK PLAYERS\n\nTOMORROW (Tue 29 Sep)\n• Match per news, not an official fixture, 2-day match "
            f"from Tue 29 Sep\n   Ayush Mhatre\n   “{TOI_TITLE}” (timesofindia.indiatimes.com)\n   {TOI_URL}"))
        self.run_at(2026, 9, 29, 8, 5)
        self.assertIn("TODAY (Tue 29 Sep)\n• Match per news", self.texts()[-1])
        self.run_at(2026, 9, 30, 8, 5)
        self.assertIn("TODAY (Wed 30 Sep)\n• Match per news", self.texts()[-1])   # day 2
        hit = t.news_fixture_hits(self.cfg, self.db, CLOCK[0])[0]
        self.assertEqual(t.describe(hit, CLOCK[0])[1], "PLAYING TODAY, per news (not an official fixture)")
        CLOCK[0] = at(2026, 10, 1, 8, 5)
        self.assertEqual(t.news_fixture_hits(self.cfg, self.db, CLOCK[0]), [])

    def test_corrections(self):
        self.ayush()
        CLOCK[0] = at(2026, 9, 28, 20, 0)
        self.process(TOI_RSS)
        self.run_at(2026, 9, 29, 8, 5)                                       # digest announces the practice match
        self.page = TOI_PAGE_UPDATED                                         # real: now a Kanga League game on Friday
        self.run_at(2026, 9, 29, 12, 0)
        self.run_at(2026, 9, 29, 13, 0)
        fixes = [x for x in self.texts() if x.startswith("✏️")]
        self.assertEqual(fixes, [f"✏️ CORRECTION: Ayush Mhatre\nEarlier alert said: match per news, 2-day from Tue 29 Sep.\n"
                                 f"The article now says: Fri 2 Oct.\n“{TOI_TITLE}” (timesofindia.indiatimes.com)\n"
                                 f"{TOI_URL}"])
        self.run_at(2026, 10, 1, 8, 5)
        self.assertIn("TOMORROW (Fri 02 Oct)\n• Match per news", self.texts()[-1])
        self.assertEqual(sum(x.startswith("📅 NEW MATCH") for x in self.texts()), 0)   # the correction covered it
        self.page = "<html><p>Ayush Mhatre has been training at the Mumbai Cricket Association's facility.</p></html>"
        self.run_at(2026, 10, 1, 12, 0)
        self.assertTrue(self.texts()[-1].startswith("✏️ CORRECTION: Ayush Mhatre\nEarlier alert said: match per news, "
                                                    "Fri 2 Oct.\nThe article no longer mentions this match, so ignore the "
                                                    "earlier alert."))
        self.assertEqual(t.news_fixture_hits(self.cfg, self.db, CLOCK[0]), [])

    def test_change_before_anyone_was_told_is_silent(self):
        self.ayush()
        CLOCK[0] = at(2026, 9, 28, 1, 0)
        self.process(TOI_RSS.replace("Mon, 28 Sep 2026 06:39:00 GMT", "Sun, 27 Sep 2026 19:00:00 GMT"))
        self.page = TOI_PAGE_UPDATED
        self.run_at(2026, 9, 28, 5, 0)                                       # before any digest mentioned it
        self.assertFalse(any(x.startswith("✏️") for x in self.texts()))

    def test_google_news_links_are_never_opened(self):
        self.ayush()
        CLOCK[0] = at(2026, 9, 28, 20, 0)
        self.process(TOI_RSS.replace(TOI_URL, "https://news.google.com/rss/articles/CBMi123"))
        self.assertFalse(any("news.google.com" in u for u in self.fetched))

    def test_old_saved_state_is_carried_over(self):
        path = os.path.join(self.tmp, "old.db")
        old = sqlite3.connect(path)
        old.execute("create table news_fixtures(player text, day text, days integer, title text, link text, source text,"
                    " primary key(player, day))")
        old.execute("create table kv(k text primary key, v text)")
        old.execute("insert into news_fixtures values('Ayush Mhatre','2026-09-29',2,'Back from hamstring injury',?,'TOI')",
                    (TOI_URL,))
        old.execute("insert into kv values('announced:news:Ayush Mhatre:2026-09-29', '1')")
        old.commit()
        old.close()
        db = t.db_connect(path)
        self.assertEqual(db.execute("select player, day, days, told, checked, status from news_matches").fetchall(),
                         [("Ayush Mhatre", "2026-09-29", 2, "2026-09-29|2", 0, "active")])
        db.close()


# =====================================================================================================
class TestPollingAndCheckIns(Base):
    def setUp(self):
        super().setUp()
        self.cricbuzz()
        for key in ("news.google.com", "bing.com", "espncricinfo.com", "timesofindia.indiatimes.com/rssfeeds", "youtube.com"):
            self.pages[key] = EMPTY_RSS
        self.pages[self.cfg["roster_sources"][0]] = "".join(
            f'<a href="/profiles/{p["id"]}/x" title="{p["name"]}"></a>' for p in self.roster[:24])
        self.pages["scorecard-archives"] = ""

    def test_timers_survive_between_separate_runs(self):
        t.run_cycle(self.cfg, self.db, {}, True, False)
        self.assertTrue(any("news.google.com" in u for u in self.fetched))
        self.assertIn(self.cfg["roster_sources"][0], self.fetched)
        self.fetched.clear()
        CLOCK[0] += timedelta(minutes=5)
        t.run_cycle(self.cfg, self.db, {}, True, False)                    # a fresh process, like each GitHub run
        self.assertEqual(self.fetched[:2], [self.cfg["live_scores_url"], self.cfg["schedule_url"]])
        self.assertFalse([u for u in self.fetched if "news.google.com" in u or "bing.com" in u
                          or u in self.cfg["roster_sources"]])              # news and roster not due again

    def test_player_news_less_often_than_team_news(self):
        t.run_cycle(self.cfg, self.db, {}, True, False)
        self.fetched.clear()
        CLOCK[0] += timedelta(minutes=10)
        t.run_cycle(self.cfg, self.db, {}, True, False)
        queries = [urllib.parse.unquote_plus(u) for u in self.fetched if "news.google.com" in u]
        self.assertEqual(len(queries), len(self.cfg["team_queries"]))        # player searches wait for 15 min
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "dashboard.html")))

    def test_quiet_check_in(self):
        t0 = CLOCK[0].timestamp()
        t.quiet_check_in(self.cfg, self.db, t0, t.alerts_sent, True)                 # first run: starts the clock
        t.quiet_check_in(self.cfg, self.db, t0 + 2 * 3600, t.alerts_sent, True)
        self.assertEqual(self.sent, [])
        t.quiet_check_in(self.cfg, self.db, t0 + 3 * 3600, t.alerts_sent, True)
        self.assertEqual(self.sent, [{"text": "✅ No new updates since 29 Sep 08:05 IST.\nTracker is running normally.",
                                      "silent": True, "dry_run": True}])
        before = t.alerts_sent
        t.send_alert(self.cfg, "a real alert", True)                             # a real alert restarts the clock
        t.quiet_check_in(self.cfg, self.db, t0 + 5 * 3600, before, True)
        t.quiet_check_in(self.cfg, self.db, t0 + 7 * 3600, t.alerts_sent, True)
        self.assertEqual(len(self.sent), 2)

    def test_full_run_through_main(self):
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as src, \
                open(os.path.join(self.tmp, "config.json"), "w", encoding="utf-8") as dst:
            dst.write(src.read())                                           # main() reads config.json next to tracker
        with mock.patch.object(sys, "argv", ["tracker.py", "--once", "--dry-run"]), \
                mock.patch.object(t.urllib.request, "urlopen", side_effect=AssertionError("no real network")):
            t.main()
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "state.db")))
        with open(os.path.join(self.tmp, "dashboard.html"), encoding="utf-8") as f:
            self.assertIn("<h2>Squad (24)</h2>", f.read())
        self.assertTrue(all(s["dry_run"] for s in self.sent))
        self.assertTrue(any(x.startswith("📅 CSK PLAYERS' MATCHES") for x in self.texts()))


# =====================================================================================================
class TestDashboard(Base):
    def page(self, hits=(), latest=(), min_importance="minor"):
        for it in latest:                                   # what process_items saves for the dashboard
            self.db.execute("insert into news_feed values(?,?,?,?,?,?,?,?,?,?)",
                            (it["title"], it["title"], it["link"], "", it["summary"], it["when"].timestamp(),
                             it["when"].timestamp(), json.dumps(it["tags"]), it["importance"], int(it["rumour"])))
        path = os.path.join(self.tmp, "d.html")
        t.write_dashboard(self.roster, list(hits), self.db, path, min_importance)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def item(self, title, imp="minor", hours=1, rumour=False, summary=""):
        return {"title": title, "link": "https://x.test/?a=1&b=2", "when": T0 - timedelta(hours=hours), "tags": ["CSK"],
                "importance": imp, "rumour": rumour, "summary": summary, "source": ""}

    def test_news_survives_a_restart(self):
        self.process(rss(["Nathan Ellis suffered a side issue"]))
        self.db.close()
        self.db = t.db_connect(os.path.join(self.tmp, "state.db"))        # a new process, same state.db
        self.assertIn("Nathan Ellis suffered a side issue", self.page())

    def test_min_importance_applies_to_the_news_list(self):
        html = self.page(latest=[self.item("Minor story"), self.item("Big story", "breaking")], min_importance="important")
        self.assertIn("Big story", html)
        self.assertNotIn("Minor story", html)

    def test_squad_badges_and_order(self):
        self.cricbuzz()
        hits = t.check_matches(self.cfg, self.db, self.roster, True)
        html = self.page(hits)
        self.assertIn("<h2>Squad (30)</h2>", html)
        rows = re.findall(r'<tr class="(\w*)"><td>([^<]*)</td>', html)
        self.assertEqual(rows[0], ("live", "Dewald Brevis"))                   # live first
        self.assertEqual([c for c, _ in rows[:4]], ["live", "soon", "soon", "soon"])   # then grey: stumps, bench...
        self.assertEqual(rows[4][0], "off")
        self.assertIn('<span class="b soon">PLAYING TOMORROW, starts 30 Sep 17:00 IST, expected, squad not out yet</span>', html)
        self.assertIn('<a href="https://www.cricbuzz.com/live-cricket-scores/155422">India A vs Australia A · TEST</a>', html)
        self.assertIn(("", "Aman Khan"), rows)                                  # everyone is listed

    def test_injury_flag_and_news_only_badges(self):
        self.cricbuzz()
        t.mark_seen(self.db, "Ellis, Davies ruled out to further deplete Aussies", at(2026, 9, 28, 19, 34))
        self.pages.update({TOI_URL: TOI_PAGE})
        CLOCK[0] = at(2026, 9, 28, 20, 0)
        self.process(TOI_RSS)
        CLOCK[0] = T0
        html = self.page(t.check_matches(self.cfg, self.db, self.roster, True))
        self.assertIn("expected, squad not out yet ⚠️ injury news</span>", html)
        self.assertIn('<span class="b soon">PLAYING TODAY, per news (not an official fixture)</span>', html)

    def test_news_list(self):
        html = self.page(latest=[self.item("Minor story", "minor", 1), self.item("Old story", "breaking", 30),
                                 self.item("Breaking story", "breaking", 5), self.item("Newer minor", "minor", 0.5),
                                 self.item("Rumour story", "important", 2, rumour=True, summary="One line.")])
        titles = re.findall(r'<a href="[^"]*">([^<]*)</a> <small>', html)
        self.assertEqual(titles, ["Breaking story", "Rumour story", "Newer minor", "Minor story"])   # rank, then newest
        self.assertNotIn("Old story", html)                                     # over 24 hours
        self.assertIn("<b>IMPORTANT</b> <em>(rumour)</em>", html)
        self.assertIn("<br><small>One line.</small>", html)
        self.assertIn("<h2>News, last 24 hours</h2>", html)

    def test_html_is_escaped_and_page_refreshes(self):
        html = self.page(latest=[self.item("<script>alert(1)</script> & more")])
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; more", html)
        self.assertIn('href="https://x.test/?a=1&amp;b=2"', html)
        self.assertIn("<meta http-equiv=refresh content=120>", html)
        self.assertIn("Updated 29 Sep 2026 08:05 IST", html)

    def test_empty_news(self):
        self.assertIn("<li>Nothing new yet.</li>", self.page())


# =====================================================================================================
class TestHttp(Base):
    """The real page fetcher: one retry after a dropped connection or server error, none after 403/404."""
    def fetch(self, replies):
        calls = []
        def urlopen(req, timeout=None):
            calls.append(req.get_header("User-agent"))
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return io.BytesIO(r.encode())
        with mock.patch.object(t.urllib.request, "urlopen", urlopen):
            try:
                return REAL_HTTP_GET("https://example.test/"), calls
            except Exception as e:
                return e, calls

    def http_err(self, code):
        return t.urllib.error.HTTPError("https://example.test/", code, "x", {}, io.BytesIO(b""))

    def test_retries_after_server_error_or_dropped_connection(self):
        self.assertEqual(self.fetch([self.http_err(503), "page"])[0], "page")
        self.assertEqual(self.fetch([ConnectionResetError("IncompleteRead"), "page"])[0], "page")

    def test_no_retry_after_not_found(self):
        result, calls = self.fetch([self.http_err(404), "page"])
        self.assertIsInstance(result, t.urllib.error.HTTPError)
        self.assertEqual(len(calls), 1)

    def test_blocked_article_is_retried_with_the_other_identity(self):
        def page():
            if self.last_kw.get("ua") != t.BROWSER_UA:
                raise t.urllib.error.HTTPError(TOI_URL, 403, "Forbidden", {}, io.BytesIO(b""))
            return TOI_PAGE
        self.pages[TOI_URL] = page
        self.assertIn("Brabourne Stadium", t.article_text(TOI_URL))
        self.assertEqual(self.fetched, [TOI_URL, TOI_URL])


class TestStateCleanup(Base):
    def test_old_entries_are_pruned_and_recent_ones_kept(self):
        old, now = T0.timestamp() - 15 * 86400, T0.timestamp()
        db = self.db
        db.executemany("insert into seen(key, title, ts) values(?,?,?)", [("a", "old story", old), ("b", "new story", now)])
        db.executemany("insert into kv values(?,?)", [("alert:live:1", str(old)), ("alert:live:2", str(now)),
                                                      ("announced:3", "1"), ("last:news", str(old))])
        db.execute("insert into story_events values('Nathan Ellis','injury',1,?)", (old,))
        db.execute("insert into outbox(text, silent, created) values('x', 0, ?)", (old,))
        t.prune_state(db)
        self.assertEqual(db.execute("select title from seen").fetchall(), [("new story",)])
        self.assertEqual(sorted(k for (k,) in db.execute("select k from kv")),
                         ["alert:live:2", "announced:3", "last:news"])       # old-style "1" and timers are kept
        self.assertEqual(db.execute("select count(*) from story_events").fetchone(), (0,))
        self.assertEqual(db.execute("select count(*) from outbox").fetchone(), (0,))


# =====================================================================================================
class TestPerformance(Base):
    """Scorecards for the players we said are playing: hourly while live, a final summary when it ends."""
    def setUp(self):
        super().setUp()
        self.cricbuzz()
        self.card = LIVE_155422(7, 4, 7, 0)                                 # real, 29 Sep ~12:50 IST
        self.pages["live-cricket-scorecard/155422"] = lambda: self.card

    def test_figures_from_real_scorecards(self):
        _, inns = t.parse_scorecard(CARD_151532)
        self.assertEqual(t.figures_text(t.player_figures(inns, "11813"), True),
                         ["Batting: 13 runs (19 balls, 0 fours, 0 sixes), not out", "Bowling: did not bowl",
                          "Fielding: 0 catches, 0 stumpings"])
        _, inns = t.parse_scorecard(CARD_IPL37)
        samson, noor = t.player_figures(inns, "8271"), t.player_figures(inns, "15452")
        self.assertEqual(t.figures_text(samson, True),
                         ["Batting: 11 runs (15 balls, 2 fours, 0 sixes), c Jos Buttler b Kagiso Rabada",
                          "Bowling: did not bowl", "Fielding: 0 catches, 1 stumping"])
        self.assertEqual(t.figures_text(noor, True)[1], "Bowling: 4 overs, 0 maidens, 29 runs, 1 wicket")
        self.assertEqual((t.figures_short(samson), t.figures_short(noor)), ("11 (15b, 2x4, 0x6) · 1 st", "4-0-29-1"))
        self.assertFalse(t.player_figures(inns, "265")["played"])           # Dhoni wasn't in that match

    def test_catches_caught_and_bowled_run_outs_and_multiple_innings(self):   # made-up Test match
        inns = [innings("India", [bat(1, "A", 45, 60, 5, 1, "c Z b Y", "CAUGHT", 2, 3), bat(9, "Me", 30, 40, 3, 0,
                                  "b Q", "BOWLED", 4)], [bowl(9, "Me", 12.3, 2, 40, 3)]),
                innings("England", [bat(5, "B", 10, 12, 1, 0, "c and b Me", "CAUGHTBOWLED", 9),
                                    bat(6, "C", 0, 1, 0, 0, "run out (Me/X)", "RUNOUT", 0, 9, 7),
                                    bat(7, "D", 2, 5, 0, 0, "c Me b R", "CAUGHT", 8, 9)], []),
                innings("India", [bat(9, "Me", 12, 20, 1, 1, "not out")], [bowl(9, "Me", 5, 1, 11, 0)])]
        f = t.player_figures(inns, 9)
        self.assertEqual(t.figures_text(f, True),
                         ["Batting: 1st inns: 30 runs (40 balls, 3 fours, 0 sixes), b Q; 2nd inns: 12 runs (20 balls, "
                          "1 four, 1 six), not out",
                          "Bowling: 1st inns: 12.3 overs, 2 maidens, 40 runs, 3 wickets; 2nd inns: 5 overs, 1 maiden, "
                          "11 runs, 0 wickets",
                          "Fielding: 2 catches, 0 stumpings, 1 run out"])

    def poll_at(self, *when):
        CLOCK[0] = at(*when)
        t.poll_performances(self.cfg, self.db, True)

    def test_live_updates_every_hour_then_a_final_summary(self):
        t.check_matches(self.cfg, self.db, self.roster, True)              # 08:05 digest: Kamboj plays today, 09:30
        self.assertEqual(json.loads(self.db.execute("select players from tracked_matches where match_id='155422'")
                                    .fetchone()[0]), {"14598": {"name": "Anshul Kamboj", "team": "India A"}})
        self.poll_at(2026, 9, 29, 9, 0)                                    # not started: no fetch
        self.assertNotIn("live-cricket-scorecard/155422", " ".join(self.fetched))
        self.poll_at(2026, 9, 29, 12, 50)
        updates = [x for x in self.texts() if x.startswith("📊")]
        self.assertEqual(updates, ["📊 LIVE UPDATE: India A vs Australia A, 2nd unofficial Test · TEST\nDay 1: 2nd Session "
                                   "- Australia A opt to bat\n\n• Anshul Kamboj (India A)\n   Batting: yet to bat\n   "
                                   "Bowling: 7 overs, 4 maidens, 7 runs, 0 wickets\n   Fielding: 0 catches, 0 stumpings\n"
                                   "https://www.cricbuzz.com/live-cricket-scorecard/155422"])
        self.poll_at(2026, 9, 29, 13, 20)                                  # within the hour: no fetch
        self.poll_at(2026, 9, 29, 13, 51)                                  # an hour on, nothing changed: no message
        self.assertEqual(sum("scorecard/155422" in u for u in self.fetched), 2)
        self.assertEqual(sum(x.startswith("📊") for x in self.texts()), 1)
        self.card = LIVE_155422(12, 5, 20, 1)                              # a wicket
        self.poll_at(2026, 9, 29, 14, 55)
        self.assertIn("Bowling: 12 overs, 5 maidens, 20 runs, 1 wicket", self.texts()[-1])
        self.card = scorecard("Complete", "India A won by 7 wkts",
                              innings("Australia A", [], [bowl(14598, "Anshul Kamboj", 22, 8, 51, 3)]),
                              innings("India A", [bat(14598, "Anshul Kamboj", 34, 41, 4, 1, "not out")], []))
        self.poll_at(2026, 10, 2, 16, 0)
        self.assertEqual(self.texts()[-1], "📊 FINAL: India A vs Australia A, 2nd unofficial Test · TEST\nIndia A won by 7 "
                                           "wkts\n\n• Anshul Kamboj (India A)\n   Batting: 34 runs (41 balls, 4 fours, 1 six), "
                                           "not out\n   Bowling: 22 overs, 8 maidens, 51 runs, 3 wickets\n   Fielding: 0 "
                                           "catches, 0 stumpings\nhttps://www.cricbuzz.com/live-cricket-scorecard/155422")
        n = sum("scorecard/155422" in u for u in self.fetched)
        self.poll_at(2026, 10, 2, 18, 0)                                   # finished: no more polling
        self.assertEqual(sum("scorecard/155422" in u for u in self.fetched), n)

    def test_player_left_out_and_abandoned_match(self):
        t.check_matches(self.cfg, self.db, self.roster, True)              # Ruturaj "expected" for the 2nd ODI
        self.pages["live-cricket-scorecard/151543"] = scorecard(
            "Complete", "India won by 5 wkts", innings("India", [bat(576, "Rohit Sharma", 80, 70, 8, 2, "not out")], []))
        self.pages["live-cricket-scorecard/147909"] = scorecard("Abandon", "Match abandoned due to rain (No toss)")
        self.poll_at(2026, 9, 30, 23, 0)
        finals = {x.splitlines()[1]: x for x in self.texts() if x.startswith("📊 FINAL")}
        self.assertIn("• Ruturaj Gaikwad (India): did not play", finals["India won by 5 wkts"])
        self.assertIn("No play in this match.", finals["Match abandoned due to rain (No toss)"])

    def test_live_matches_announced_before_are_still_followed(self):
        self.db.execute("insert into kv values('alert:live:900001', '1')")   # PLAYING NOW went out on an earlier run
        t.check_matches(self.cfg, self.db, self.roster, True)
        self.assertTrue(self.db.execute("select 1 from tracked_matches where match_id='900001'").fetchone())

    def test_bench_players_are_not_followed(self):
        t.check_matches(self.cfg, self.db, self.roster, True)              # PLAYING NOW for the live SA20 match
        players = json.loads(self.db.execute("select players from tracked_matches where match_id='900001'").fetchone()[0])
        self.assertEqual(list(players), ["20538"])                           # Brevis (XI), not Noor (bench)

    def test_dashboard_shows_the_latest_figures(self):
        t.check_matches(self.cfg, self.db, self.roster, True)
        self.poll_at(2026, 9, 29, 12, 50)
        path = os.path.join(self.tmp, "d.html")
        t.write_dashboard(self.roster, [], self.db, path)
        with open(path, encoding="utf-8") as f:
            self.assertIn("<td>Anshul Kamboj</td><td><br><small>📊 Live: 7-4-7-0</small>", f.read())


if __name__ == "__main__":
    unittest.main()
