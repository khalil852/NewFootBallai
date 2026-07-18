"""football-data.org API — 赛后比分、赛程"""
import json, requests
from datetime import datetime, timedelta

API_KEY = "ee1828bc0a9f4e72932471b67446c3eb"
BASE = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_KEY}

_calls_today = 0
_cache: dict[str, dict] = {}

# football-data.org 队名 → 已知队名（中文/英文常见名）
FB_TEAMS = {
    "Germany": "德国", "Scotland": "苏格兰", "Hungary": "匈牙利", "Switzerland": "瑞士",
    "Spain": "西班牙", "Croatia": "克罗地亚", "Italy": "意大利", "Albania": "阿尔巴尼亚",
    "Slovenia": "斯洛文尼亚", "Denmark": "丹麦", "Serbia": "塞尔维亚",
    "England": "英格兰",
    "Poland": "波兰", "Netherlands": "荷兰", "Austria": "奥地利", "France": "法国",
    "Romania": "罗马尼亚", "Ukraine": "乌克兰", "Belgium": "比利时", "Slovakia": "斯洛伐克",
    "Turkey": "土耳其", "Georgia": "格鲁吉亚", "Portugal": "葡萄牙", "Czechia": "捷克",
    "Qatar": "卡塔尔", "Ecuador": "厄瓜多尔", "Senegal": "塞内加尔",
    "USA": "美国", "Wales": "威尔士", "Iran": "伊朗", "South Korea": "韩国",
    "Saudi Arabia": "沙特", "Mexico": "墨西哥", "Costa Rica": "哥斯达黎加",
    "Morocco": "摩洛哥", "Brazil": "巴西", "Cameroon": "喀麦隆",
    "Uruguay": "乌拉圭", "Ghana": "加纳",
    "Australia": "澳大利亚", "Tunisia": "突尼斯",
    "Japan": "日本", "Canada": "加拿大",
    "Argentina": "阿根廷",
}


def _get(path: str, params: dict | None = None) -> dict:
    global _calls_today
    if _calls_today >= 10:
        raise RuntimeError("配额用尽 (10次/天)")
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=15)
    _calls_today += 1
    if r.status_code == 429:
        raise RuntimeError("API rate limit (429)")
    if r.status_code == 403:
        raise RuntimeError("API Key invalid")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def _match_key(home_cn: str, away_cn: str) -> str:
    """根据中文队名算匹配 key"""
    return f"{home_cn}|{away_cn}"


def _find_match(all_matches: list, home_cn: str, away_cn: str) -> dict | None:
    """在比赛列表中匹配中文队名"""
    for m in all_matches:
        fb_home = (m.get("homeTeam") or {}).get("name", "")
        fb_away = (m.get("awayTeam") or {}).get("name", "")
        cn_home = FB_TEAMS.get(fb_home, "")
        cn_away = FB_TEAMS.get(fb_away, "")
        if cn_home == home_cn and cn_away == away_cn:
            return m
        # 回退：部分匹配
        if (home_cn.lower() in fb_home.lower() or home_cn.lower() in cn_home.lower()) and \
           (away_cn.lower() in fb_away.lower() or away_cn.lower() in cn_away.lower()):
            return m
    return None


def get_match(home_team_cn: str, away_team_cn: str, date_str: str | None = None, tournament: str = "EC") -> dict | None:
    """查比赛结果。tournament: EC=欧洲杯, WC=世界杯, WCO=世界杯资格赛"""
    if not home_team_cn or not away_team_cn:
        return None

    # 根据比赛决定查哪个 competition
    comp_id = None
    if tournament == "EC":
        comp_id = 2018
    elif tournament == "WC":
        comp_id = 2000  # 需要付费
    elif tournament == "WC2022":
        # 2022 世界杯在 free 不开放，回退 memory
        return None

    if not comp_id:
        return None

    key = _match_key(home_team_cn, away_team_cn)
    if key in _cache:
        return _cache[key]

    params = {}
    if date_str:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            params["dateFrom"] = dt.strftime("%Y-%m-%d")
            params["dateTo"] = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    try:
        data = _get(f"/competitions/{comp_id}/matches", params)
    except RuntimeError:
        return None

    match = _find_match(data.get("matches", []), home_team_cn, away_team_cn)
    if not match:
        return None

    score = match.get("score", {})
    ft = score.get("fullTime", {})
    h, a = ft.get("home"), ft.get("away")
    if h is None or a is None:
        return None

    result = {"home_goals": h, "away_goals": a, "status": match.get("status", "")}
    _cache[key] = result
    return result


def available_calls() -> int:
    return 10 - _calls_today


def reset():
    global _calls_today
    _calls_today = 0
