import os
import time
import requests
import datetime as dt
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import telebot

# Load .env when running locally (Render will use environment variables)
load_dotenv()

# -------- CONFIG ----------
API_KEY = os.getenv("API_FOOTBALL_KEY")  # RapidAPI or API-FOOTBALL key
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

RUN_EVERY_MINUTES = int(os.getenv("RUN_EVERY_MINUTES", "60"))
MIN_HOME_ODDS = float(os.getenv("MIN_HOME_ODDS", "2.0"))
MAX_GA_PER_MATCH = float(os.getenv("MAX_GA_PER_MATCH", "1.0"))
MIN_WINS_LAST5 = int(os.getenv("MIN_WINS_LAST5", "3"))
MIDTABLE_MIN = int(os.getenv("MIDTABLE_MIN", "5"))
MIDTABLE_MAX = int(os.getenv("MIDTABLE_MAX", "12"))
H2H_SAMPLE = int(os.getenv("H2H_SAMPLE", "6"))
H2H_HOME_WIN_PCT = float(os.getenv("H2H_HOME_WIN_PCT", "0.5"))

# If you want to explicitly exclude some countries, set comma separated list in env
COUNTRIES_EXCLUDE = [c.strip() for c in os.getenv("COUNTRIES_EXCLUDE", "").split(",") if c.strip()]

# Defaults: exclude Africa + South America countries (covers most)
DEFAULT_EXCLUDE = set([
    # Africa (common football countries)
    "Algeria","Angola","Benin","Botswana","Burkina Faso","Burundi","Cameroon","Cape Verde",
    "Central African Republic","Chad","Comoros","Congo","Congo DR","Côte d'Ivoire","Djibouti",
    "Egypt","Equatorial Guinea","Eritrea","Eswatini","Ethiopia","Gabon","Gambia","Ghana",
    "Guinea","Guinea-Bissau","Kenya","Lesotho","Liberia","Libya","Madagascar","Malawi","Mali",
    "Mauritania","Mauritius","Mayotte","Morocco","Mozambique","Namibia","Niger","Nigeria","Rwanda",
    "Sao Tome and Principe","Senegal","Seychelles","Sierra Leone","Somalia","South Africa","South Sudan",
    "Sudan","Tanzania","Togo","Tunisia","Uganda","Zambia","Zimbabwe"
]) | set([
    # South America
    "Argentina","Bolivia","Brazil","Chile","Colombia","Ecuador","Guyana","Paraguay","Peru","Suriname","Uruguay","Venezuela"
])

# Combine config exclude list
if COUNTRIES_EXCLUDE:
    EXCLUDE_COUNTRIES = set(COUNTRIES_EXCLUDE) | DEFAULT_EXCLUDE
else:
    EXCLUDE_COUNTRIES = DEFAULT_EXCLUDE

# API base
BASE_URL = "https://v3.football.api-sports.io"

# Headers: try to support both common API headers
HEADERS = {}
if API_KEY:
    # Use both common header names so either key works
    HEADERS = {
        "x-apisports-key": API_KEY,
        "x-rapidapi-key": API_KEY,
        "x-rapidapi-host": "v3.football.api-sports.io",
        "Accept": "application/json"
    }

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# Helper: convert fixture UTC -> Kenya time (EAT UTC+3)
def to_kenya_time(utc_str):
    try:
        # API returns ISO date; parse then convert to Africa/Nairobi
        dt_utc = dt.datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        kenya = dt_utc.astimezone(ZoneInfo("Africa/Nairobi"))
        return kenya.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return utc_str

# Small helpers for filtering & analysis
def median(lst):
    if not lst:
        return None
    s = sorted(lst)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0

def fetch_fixtures_window(hours_ahead=48):
    now = dt.datetime.utcnow()
    end = now + dt.timedelta(hours=hours_ahead)
    params = {"from": now.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d")}
    r = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("response", [])

def get_team_last_matches(team_id, last=6):
    r = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params={"team": team_id, "last": last}, timeout=10)
    r.raise_for_status()
    return r.json().get("response", [])

def get_team_stats(league_id, season, team_id):
    r = requests.get(f"{BASE_URL}/teams/statistics", headers=HEADERS, params={"league": league_id, "season": season, "team": team_id}, timeout=10)
    r.raise_for_status()
    return r.json().get("response", {})

def get_odds(fixture_id):
    r = requests.get(f"{BASE_URL}/odds", headers=HEADERS, params={"fixture": fixture_id}, timeout=10)
    r.raise_for_status()
    return r.json().get("response", [])

def get_standings(league_id, season):
    r = requests.get(f"{BASE_URL}/standings", headers=HEADERS, params={"league": league_id, "season": season}, timeout=10)
    r.raise_for_status()
    return r.json().get("response", [])

def get_h2h(home_id, away_id, last=6):
    r = requests.get(f"{BASE_URL}/fixtures/headtohead", headers=HEADERS, params={"h2h": f"{home_id}-{away_id}", "last": last}, timeout=10)
    r.raise_for_status()
    return r.json().get("response", [])

def get_injuries(team_id):
    try:
        r = requests.get(f"{BASE_URL}/injuries", headers=HEADERS, params={"team": team_id}, timeout=10)
        r.raise_for_status()
        return r.json().get("response", [])
    except Exception:
        return []

def extract_median_odds(odds_blocks):
    home_vals, draw_vals, away_vals = [], [], []
    for block in odds_blocks:
        for bk in block.get("bookmakers", []):
            for bet in bk.get("bets", []):
                name = (bet.get("name") or "").lower()
                if "match winner" in name or "1x2" in name or "win-draw-win" in name:
                    vals = {v.get("value"): v.get("odd") for v in bet.get("values", [])}
                    h = vals.get("Home") or vals.get("1")
                    d = vals.get("Draw") or vals.get("X")
                    a = vals.get("Away") or vals.get("2")
                    try:
                        if h: home_vals.append(float(h))
                        if d: draw_vals.append(float(d))
                        if a: away_vals.append(float(a))
                    except Exception:
                        pass
    if not home_vals:
        return None, None, None, False
    h_m = median(home_vals); d_m = median(draw_vals) if draw_vals else None; a_m = median(away_vals) if away_vals else None
    fav = (h_m is not None) and (d_m is not None) and (a_m is not None) and (h_m < d_m and h_m < a_m)
    return h_m, d_m, a_m, fav

# Main scan for one run
def run_scan():
    try:
        fixtures = fetch_fixtures_window(hours_ahead=48)
    except Exception as e:
        print("Error fetching fixtures:", e)
        return

    picks = []
    for f in fixtures:
        try:
            league = f.get("league", {})
            country = league.get("country")
            if country in EXCLUDE_COUNTRIES:
                continue

            # only upcoming scheduled fixtures (not live/finished)
            status = (f.get("fixture") or {}).get("status", {}).get("short")
            if status not in ("NS", "TBD"):
                continue

            home = f["teams"]["home"]; away = f["teams"]["away"]
            if not home or not away: 
                continue

            home_id = home["id"]; away_id = away["id"]; fixture_id = f["fixture"]["id"]
            season = league.get("season") or None
            league_id = league.get("id") or None

            # team stats (goals against avg)
            stats = {}
            if league_id and season and home_id:
                try:
                    stats = get_team_stats(league_id, season, home_id) or {}
                except Exception:
                    stats = {}

            # form (last 5)
            last_matches = get_team_last_matches(home_id, last=5)
            wins_last5 = 0
            for m in last_matches:
                # winner field for home or away
                winner = None
                if m.get("teams", {}).get("home", {}).get("id") == home_id:
                    winner = m.get("teams", {}).get("home", {}).get("winner")
                else:
                    winner = m.get("teams", {}).get("away", {}).get("winner")
                if winner is True:
                    wins_last5 += 1

            if wins_last5 < MIN_WINS_LAST5:
                continue

            # check last played was a win
            last_all = get_team_last_matches(home_id, last=1)
            if last_all:
                last = last_all[0]
                last_winner = None
                if last.get("teams", {}).get("home", {}).get("id") == home_id:
                    last_winner = last["teams"]["home"]["winner"]
                else:
                    last_winner = last["teams"]["away"]["winner"]
                if last_winner is not True:
                    continue
            else:
                continue

            # goals against average from stats if available
            ga_avg = None
            try:
                ga_avg = float(((stats.get("goals") or {}).get("against") or {}).get("average", {}).get("total"))
            except Exception:
                ga_avg = None

            if ga_avg is not None and ga_avg > MAX_GA_PER_MATCH:
                continue

            # upward trend: simple heuristic using last 5 (avg last3 > avg last5)
            last5 = get_team_last_matches(home_id, last=5)
            pts = []
            for m in last5[:5]:
                if m.get("teams", {}).get("home", {}).get("id") == home_id:
                    win = m["teams"]["home"]["winner"]
                else:
                    win = m["teams"]["away"]["winner"]
                if win is True:
                    pts.append(3)
                elif win is None:
                    pts.append(1)
                else:
                    pts.append(0)
            if len(pts) < 5:
                continue
            avg3 = sum(pts[:3]) / 3.0
            avg5 = sum(pts[:5]) / 5.0
            if not (avg3 > avg5):
                continue

            # standings: check mid-table
            if league_id and season:
                try:
                    st = get_standings(league_id, season)
                    standings_rows = st[0]["league"]["standings"][0]
                    pos = next((int(r.get("rank")) for r in standings_rows if (r.get("team") or {}).get("id") == home_id), None)
                    if pos is None:
                        continue
                    if not (MIDTABLE_MIN <= pos <= MIDTABLE_MAX):
                        continue
                except Exception:
                    # if standings fail, skip pick (we want mid-table)
                    continue

            # odds
            try:
                odds_blocks = get_odds(fixture_id)
                h_m, d_m, a_m, fav = extract_median_odds(odds_blocks)
                if h_m is None:
                    continue
                if not fav:
                    continue
                if h_m < MIN_HOME_ODDS:
                    continue
            except Exception:
                continue

            # H2H
            try:
                h2h_list = get_h2h(home_id, away_id, last=H2H_SAMPLE)
                wins = 0; total = 0
                for m in h2h_list:
                    total += 1
                    if (m.get("teams", {}) or {}).get("home", {}).get("id") == home_id and m["teams"]["home"]["winner"] is True:
                        wins += 1
                h2h_pct = (wins / total) if total else 0
                if total and h2h_pct < H2H_HOME_WIN_PCT:
                    continue
            except Exception:
                continue

            # injuries
            injuries = get_injuries(home_id)
            inj_short = []
            for it in injuries[:6]:
                player = (it.get("player") or {}).get("name")
                reason = (it.get("player") or {}).get("reason") or ""
                if player:
                    inj_short.append(f"{player} ({reason})" if reason else player)
            injuries_txt = ", ".join(inj_short) if inj_short else "None"

            # build message
            kickoff_local = to_kenya_time(f.get("fixture", {}).get("date", ""))
            msg_lines = [
                "⚽️ *Home Pick Alert*",
                f"*{home.get('name')}* vs *{away.get('name')}*",
                f"League: {league.get('name')} — {league.get('country')}",
                f"Kickoff (Kenya): {kickoff_local}",
                "",
                "*Why selected:*",
                f"• Home form: {wins_last5}W in last 5",
                f"• Won last match: ✅",
                f"• GA avg: {ga_avg if ga_avg is not None else 'N/A'}",
                f"• Upward trend: {'Yes' if avg3 > avg5 else 'No'}",
                f"• Table pos: {pos}",
                f"• Odds median H: {h_m:.2f} (favoured: {'Yes' if fav else 'No'})",
                f"• H2H home wins: {wins}/{total}",
                f"• Injuries / concerns: {injuries_txt}",
                "",
                "*Suggested:* Home win"
            ]
            picks.append("\n".join(msg_lines))
        except Exception as exc:
            # ignore this fixture; continue scanning
            print("Fixture error:", exc)
            continue

    # Send picks via Telegram
    for p in picks:
        try:
            # send as plain text (Telegram will show it)
            bot.send_message(CHAT_ID, p)
            time.sleep(0.3)
        except Exception as e:
            print("Telegram send error:", e)

def main_loop():
    print("Starting bot loop. Scanning every", RUN_EVERY_MINUTES, "minutes.")
    while True:
        try:
            run_scan()
        except Exception as e:
            print("Scan failed:", e)
        time.sleep(RUN_EVERY_MINUTES * 60)

if __name__ == "__main__":
    main_loop()
