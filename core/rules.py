"""教练库 + 定律树匹配 → λ 修正因子"""
import re
import streamlit as st
from core.config import TEAM_EN
from core.models import LambdaModifiers
from core.supabase_client import supabase


def _parse_teams(query: str) -> tuple[str | None, str | None]:
    if not query:
        return None, None
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s*vs\s*', r'\s*v\s*',
                r'\s+对阵\s+', r'\s+对\s+']:
        parts = re.split(sep, query, re.IGNORECASE)
        if len(parts) == 2:
            left = [w.strip() for w in parts[0].split()
                    if re.search(r'[一-鿿\w]', w)]
            right = [w.strip() for w in parts[1].split()
                     if re.search(r'[一-鿿\w]', w)]
            if left and right and left[-1] != right[0]:
                return left[-1], right[0]
    return None, None


def _lookup_coach(team_cn: str) -> dict:
    """按中文队名查教练表"""
    team_en = TEAM_EN.get(team_cn, team_cn)
    try:
        d = supabase.table("coaches").select("*").eq("team", team_en).execute()
        return d.data[0] if d.data else {}
    except Exception:
        return {}


def detect_knockout(search_report: str, structured: dict | None = None) -> bool:
    """判断是否淘汰赛"""
    if structured and structured.get("scenario"):
        for s in structured["scenario"]:
            if any(k in s.lower() for k in ["淘汰赛", "决赛", "半决赛", "knockout", "final", "semi"]):
                return True

    text = (search_report or "").lower()
    return any(k in text for k in [
        "淘汰赛", "决赛", "半决赛", "1/4决赛", "1/8决赛",
        "十六强", "八强", "四强", "knockout", "quarterfinal",
        "semi-final", "semi final", "final", "round of 16",
    ])


def run_rules(search_report: str, structured: dict | None,
              match_query: str, laws: list) -> dict:
    """
    运行定律引擎:
      Step A: 教练库查表
      Step B: 遍历定律树匹配

    返回: {"modifiers": LambdaModifiers, "triggered": list[dict],
            "coach_info": dict, "has_uncertainty": bool, "uncertainty": list}
    """
    mods = LambdaModifiers()
    triggered: list[dict] = []
    coach_info: dict = {}

    t1, t2 = _parse_teams(match_query)

    # ── Step A: 教练库查表 ──
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

    # ── Step B: 遍历定律树 ──
    # 按树分组，子节点优先
    trees: dict[str, list[dict]] = {}
    for law in laws:
        if law.get("status", "active") != "active":
            continue
        tree_name = law.get("tree", "通用")
        trees.setdefault(tree_name, []).append(law)

    for tree_name, tree_laws in trees.items():
        # 子节点排前面（parent_id 非空的先处理）
        sorted_laws = sorted(tree_laws,
                             key=lambda l: (0 if l.get("parent_id") else 1))
        parent_triggered = set()

        for law in sorted_laws:
            if _match_law(law, search_report, structured, t1, t2, coach_info):
                parent = law.get("parent_id")
                if parent:
                    # 子节点触发，标记父节点
                    parent_triggered.add(parent)
                elif law["id"] in parent_triggered:
                    # 父节点的子节点已触发，父节点跳过
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

    # 保存触发的定律名列表（用于数据库存储）
    st.session_state["last_triggered_laws"] = [t["name"] for t in triggered]

    # ── 分支检测 ──
    uncertainty = []
    if structured and structured.get("uncertainty"):
        for u in structured["uncertainty"]:
            if u.get("player") and u.get("scenario_a") and u.get("scenario_b"):
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
    """判断单条定律是否触发"""
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
        if want_ko is not None:
            return is_ko == want_ko
        return False

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
            # 兼容旧定律: trigger_config 可能是老的格式
            old_kw = law.get("trigger_keywords") or []
            if old_kw:
                keywords = old_kw
        if not keywords:
            return False

        match_all = config.get("match_all", False)
        # 搜索结构化 JSON + 报告全文
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
    """叠加修正因子到 LambdaModifiers"""
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
    """S/A/B/C/D 评级: 正确率 × log(使用次数)"""
    if triggers == 0:
        return "D"
    accuracy = correct / triggers
    score = accuracy * (min(triggers, 50) / 10)  # 简化的评分
    if score >= 0.30:
        return "S"
    if score >= 0.20:
        return "A"
    if score >= 0.10:
        return "B"
    if score >= 0.05:
        return "C"
    return "D"
