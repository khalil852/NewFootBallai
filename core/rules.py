"""教练库 + 定律树匹配 → λ 修正因子"""
import re
import streamlit as st
from core.config import TEAM_EN
from core.models import LambdaModifiers
from core.supabase_client import get_supabase

# 从 TEAM_EN 构建已知队名列表（按长度降序，优先匹配长队名）
_KNOWN_TEAMS = sorted(TEAM_EN.keys(), key=len, reverse=True)


def parse_teams(query: str) -> tuple[str | None, str | None]:
    """从对阵字符串提取两队名。

    优先用已知队名列表匹配，回退到分隔符分割。
    已知列表解决"法国 U20 vs 阿根廷"中 U20 被误解析的问题。
    """
    if not query:
        return None, None

    # 先按分隔符大致分成左右半区
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s+对阵\s+', r'\s+对\s+',
                r'\s*vs\s*', r'\s*v\s*']:
        parts = re.split(sep, query, re.IGNORECASE)
        if len(parts) != 2:
            continue

        left_raw = parts[0].strip()
        right_raw = parts[1].strip()

        # 优先：从已知队名列表匹配
        t1 = _match_known_team(left_raw)
        t2 = _match_known_team(right_raw)

        if t1 and t2 and t1 != t2:
            return t1, t2

        # 回退：按 CJK/字母 字符过滤取词
        left_words = [w.strip() for w in left_raw.split()
                      if re.search(r'[一-鿿\w]', w)]
        right_words = [w.strip() for w in right_raw.split()
                       if re.search(r'[一-鿿\w]', w)]

        if left_words and right_words:
            # 从已知列表中取匹配
            t1 = _match_known_team(" ".join(left_words))
            t2 = _match_known_team(" ".join(right_words))
            if not t1:
                t1 = left_words[-1]  # 取最后一个看起来像队名的词
            if not t2:
                t2 = right_words[0]  # 取第一个看起来像队名的词
            if t1 and t2 and t1 != t2:
                return t1, t2

    return None, None


def _match_known_team(text: str) -> str | None:
    """在给定文本中匹配已知队名，返回队名或 None"""
    for team in _KNOWN_TEAMS:
        if team in text:
            return team
    return None


def _lookup_coach(team_cn: str) -> dict:
    team_en = TEAM_EN.get(team_cn, team_cn)
    try:
        d = get_supabase().table("coaches").select("*").eq("team", team_en).execute()
        return d.data[0] if d.data else {}
    except Exception:
        return {}


def detect_knockout(search_report: str, structured: dict | None = None) -> bool:
    if structured and structured.get("scenario"):
        for s in structured["scenario"]:
            if s and isinstance(s, str) and any(
                k in s.lower() for k in [
                    "淘汰赛", "决赛", "半决赛", "knockout", "final", "semi"
                ]):
                return True
    text = (search_report or "").lower()
    return any(k in text for k in [
        "淘汰赛", "决赛", "半决赛", "1/4决赛", "1/8决赛",
        "十六强", "八强", "四强", "knockout", "quarterfinal",
        "semi-final", "semi final", "final", "round of 16",
    ])


def run_rules(search_report: str, structured: dict | None,
              match_query: str, laws: list) -> dict:
    mods = LambdaModifiers()
    triggered: list[dict] = []
    coach_info: dict = {}

    t1, t2 = parse_teams(match_query)

    for team_cn in (t1, t2):
        if not team_cn:
            continue
        co = _lookup_coach(team_cn)
        if not co:
            coach_info[team_cn] = {"name": "?", "formation": "?", "style": "?",
                                    "def_line": "?", "set_piece": "?"}
            continue
        coach_info[team_cn] = {
            "name": co.get("name", "?"),
            "formation": co.get("formation", "?"),
            "style": co.get("style", "?"),
            "def_line": co.get("def_line", "?"),
            "set_piece": co.get("set_piece", "?"),
        }

    st.session_state["coach_info"] = coach_info

    trees: dict[str, list[dict]] = {}
    for law in laws:
        if law.get("status", "active") != "active":
            continue
        tree_name = law.get("tree", "通用")
        trees.setdefault(tree_name, []).append(law)

    for tree_name, tree_laws in trees.items():
        sorted_laws = sorted(tree_laws,
                             key=lambda l: (0 if l.get("parent_id") else 1))
        parent_triggered = set()
        for law in sorted_laws:
            if _match_law(law, search_report, structured, t1, t2, coach_info):
                parent = law.get("parent_id")
                if parent:
                    parent_triggered.add(parent)
                elif law["id"] in parent_triggered:
                    continue

                mm = law.get("modifier_map") or {}
                for target, value in mm.items():
                    if isinstance(value, (int, float)):
                        _apply_modifier(mods, target, float(value))

                law_grade = _calc_grade(
                    law.get("triggers_count", 0),
                    law.get("correct_count", 0),
                )
                triggered.append({
                    "id": law["id"],
                    "name": law.get("name", "?"),
                    "tree": tree_name,
                    "trigger_mode": law.get("trigger_mode", "?"),
                    "modifier_map": mm,
                    "grade": law_grade,
                    "triggers_count": law.get("triggers_count", 0),
                    "correct_count": law.get("correct_count", 0),
                })

    st.session_state["last_triggered_laws"] = [t["name"] for t in triggered]

    uncertainty = []
    if structured and structured.get("uncertainty"):
        unc = structured["uncertainty"]
        if not isinstance(unc, list):
            unc = []
        for u in unc:
            if isinstance(u, dict) and u.get("player") and u.get("scenario_a") and u.get("scenario_b"):
                uncertainty.append(u)

    if triggered:
        names = [t["name"] for t in triggered[:5]]
        st.info(f"📋 触发 {len(triggered)} 条定律: {', '.join(names)}")

    return {
        "modifiers": mods,
        "triggered": triggered,
        "coach_info": coach_info,
        "has_uncertainty": len(uncertainty) > 0,
        "uncertainty": uncertainty,
    }


def _match_law(law: dict, search_report: str, structured: dict | None,
               t1: str | None, t2: str | None,
               coach_info: dict) -> bool:
    mode = law.get("trigger_mode", "keyword")
    config = law.get("trigger_config") or {}

    if mode == "always":
        return True
    if mode == "team":
        team = config.get("team", "")
        return team == t1 or team == t2
    if mode == "match_type":
        is_ko = detect_knockout(search_report, structured)
        want_ko = config.get("is_knockout")
        return is_ko == want_ko if want_ko is not None else False
    if mode == "coach":
        for team_cn in (t1, t2):
            ci = coach_info.get(team_cn, {})
            if not ci or ci.get("name") == "?":
                continue
            match = True
            if "style" in config and ci.get("style") != config["style"]:
                match = False
            if "def_line" in config and ci.get("def_line") != config["def_line"]:
                match = False
            ag = ci.get("aggression")
            if ag is not None:
                if "aggression_gte" in config and float(ag) < config["aggression_gte"]:
                    match = False
                if "aggression_lte" in config and float(ag) > config["aggression_lte"]:
                    match = False
            if match:
                return True
        return False
    if mode == "keyword":
        keywords = config.get("keywords") or []
        if not keywords:
            old_kw = law.get("trigger_keywords") or []
            if old_kw:
                keywords = old_kw
        if not keywords:
            return False
        match_all = config.get("match_all", False)
        search_text = search_report or ""
        if structured:
            search_text += " " + str(structured)
        search_lower = search_text.lower()
        if match_all:
            return all(
                any(k.lower() in search_lower for k in kw_set)
                if isinstance(kw_set, list) else kw_set.lower() in search_lower
                for kw_set in keywords
            )
        else:
            for kw in keywords:
                if isinstance(kw, list):
                    if any(k.lower() in search_lower for k in kw):
                        return True
                elif kw.lower() in search_lower:
                    return True
            return False
    return False


def _apply_modifier(mods: LambdaModifiers, target: str, value: float) -> None:
    if target == "attack":
        mods.attack *= value
    elif target == "defense":
        mods.defense *= value
    elif target == "tactical":
        mods.tactical *= value
    elif target == "coach_intent":
        mods.coach_intent *= value
    elif target == "scenario":
        mods.scenario *= value
    elif target == "confidence":
        mods.confidence *= value


def _calc_grade(triggers: int, correct: int) -> str:
    if triggers == 0:
        return "D"
    accuracy = correct / triggers
    score = accuracy * (min(triggers, 50) / 10)
    if score >= 0.30: return "S"
    if score >= 0.20: return "A"
    if score >= 0.10: return "B"
    if score >= 0.05: return "C"
    return "D"
