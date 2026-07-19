"""Tavily 搜索 + DeepSeek 汇总"""
import json, re, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
from core.config import (
    DEEPSEEK_KEY, DEEPSEEK_URL, TAVILY_KEY, TAVILY_URL,
    QUANT_DOMAINS, QUAL_DOMAINS, CALIBRATE_DOMAINS, TEAM_EN,
)


def _deepseek_chat(system_prompt: str, user_content: str,
                   api_key: str = "", model_cfg: dict | None = None,
                   fallback_cfgs: list[dict] | None = None) -> str:
    from core.config import MODEL_DEFAULT
    cfgs = [model_cfg or MODEL_DEFAULT]
    if fallback_cfgs: cfgs += fallback_cfgs

    key = api_key or DEEPSEEK_KEY
    last_error = ""
    for i, cfg in enumerate(cfgs):
        if i > 0:
            st.toast(f"🔄 主模型失败，回退到 {cfg.get('model','?')}...", icon="⚠️")
        payload = {"model": cfg["model"], "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]}
        if "reasoning_effort" in cfg:
            payload["reasoning_effort"] = cfg["reasoning_effort"]
        if "max_tokens" in cfg:
            payload["max_tokens"] = cfg["max_tokens"]
        timeout = 120 if "reasoning_effort" in cfg else 90
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            r = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=timeout)
            d = r.json()
            if "error" in d: last_error = str(d["error"]); continue
            if "choices" not in d or not d["choices"]:
                last_error = f"格式异常: {str(d)[:200]}"; continue
            return d["choices"][0]["message"].get("content", "")
        except requests.exceptions.Timeout:
            last_error = f"超时 ({cfg['model']})"; continue
        except Exception as e:
            last_error = str(e)[:80]; continue
    st.error(f"所有模型均失败: {last_error}")
    return ""


def _tavily_search(query: str, include_domains: list[str] | None = None,
                   max_results: int = 8) -> str:
    if not TAVILY_KEY:
        return "__ERR__Tavily API Key 未配置"
    payload = {"api_key": TAVILY_KEY, "query": query, "search_depth": "advanced",
               "max_results": max_results}
    if include_domains: payload["include_domains"] = include_domains
    try:
        r = requests.post(TAVILY_URL, json=payload,
                          headers={"Content-Type": "application/json"}, timeout=20)
        if r.status_code != 200:
            err = r.json().get("error", r.text[:200])
            return f"__ERR__Tavily [{r.status_code}]: {err}"
        items = []
        for item in r.json().get("results", []):
            c = item.get("content", "")
            if len(c) > 20: items.append(f"- {item.get('title','')}: {c[:600]}")
        return "\n".join(items)
    except requests.exceptions.Timeout:
        return "__ERR__Tavily 超时"
    except Exception as e:
        return f"__ERR__Tavily 连接: {e}"


def _team_names(query: str) -> tuple[str, str]:
    """用已知队名列表解析对阵 → ('France','Senegal')"""
    from core.rules import parse_teams
    t1, t2 = parse_teams(query)
    if t1 and t2:
        return TEAM_EN.get(t1, t1), TEAM_EN.get(t2, t2)
    # fallback: 用分隔符
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s+对阵\s+', r'\s+对\s+']:
        parts = re.split(sep, query, re.IGNORECASE)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
    return query.strip(), ""


def _search_with(topic: str, rounds: list[str]) -> str:
    """搜索，成功返回内容，全失败则抛出异常"""
    errors = ""
    results = ""
    with ThreadPoolExecutor(max_workers=min(len(rounds), 8)) as ex:
        futures = {ex.submit(_tavily_search, q): q for q in rounds}
        for f in as_completed(futures):
            r = f.result()
            if not r: continue
            if r.startswith("__ERR__"):
                errors += f"[{topic}] {r[7:]}\n"
            else:
                results += r + "\n"
    if not results:
        raise RuntimeError(errors.strip() if errors.strip() else f"{topic}搜索全部为空")
    return results


_SPORT = " football match soccer World Cup European Championship "


def _football(kw: str) -> str:
    return kw + _SPORT


def _match_db_odds(match_query: str) -> str | None:
    """查手动数据库，有赔率则返回格式化文本"""
    try:
        from core.match_db import get_match as _gm
        m = _gm(match_query)
        if m and m.get("odds_h") and m.get("odds_d") and m.get("odds_a"):
            oh, od, oa = m["odds_h"], m["odds_d"], m["odds_a"]
            txt = f"赔率: 主胜 {oh} | 平局 {od} | 客胜 {oa}"
            if m.get("tournament"):
                txt = f"[{m['tournament']}] {txt}"
            return txt
    except Exception:
        pass
    return None


def _match_db_report(match_query: str) -> str | None:
    """查手动数据库，有原始search_report直接返回，没有则拼装"""
    try:
        from core.match_db import get_match as _gm
        m = _gm(match_query)
        if not m:
            return None
        # 原始粘贴文本优先
        raw = m.get("search_report")
        if raw and len(raw.strip()) > 20:
            return raw.strip()
        # 回退拼装
        lines = []
        if m.get("home_team") and m.get("away_team"):
            lines.append(f"### {match_query}")
        if m.get("tournament"):
            lines.append(f"赛事: {m['tournament']}")
        if m.get("home_injuries"):
            lines.append(f"**{m['home_team']}伤病:** {m['home_injuries']}")
        if m.get("away_injuries"):
            lines.append(f"**{m['away_team']}伤病:** {m['away_injuries']}")
        if m.get("home_coach"):
            lines.append(f"**{m['home_team']}教练:** {m['home_coach']}")
        if m.get("away_coach"):
            lines.append(f"**{m['away_team']}教练:** {m['away_coach']}")
        if m.get("home_formation"):
            lines.append(f"**{m['home_team']}阵型:** {m['home_formation']}")
        if m.get("away_formation"):
            lines.append(f"**{m['away_team']}阵型:** {m['away_formation']}")
        if m.get("match_time"):
            lines.append(f"开赛时间: {m['match_time']}")
        return "\n".join(lines)
    except Exception:
        pass
    return None


def search_quantitative(match_query: str) -> str:
    db = _match_db_odds(match_query)
    if db:
        return db
    en_home, en_away = _team_names(match_query)
    rounds = [
        _football(f"{en_home} {en_away} match odds 1X2 over under"),
        _football(f"{en_home} vs {en_away} betting odds winner draw"),
        f"{match_query} 足球 赔率 欧赔 亚盘",
    ]
    return _search_with("赔率", rounds)


def search_qualitative(match_query: str) -> str:
    db = _match_db_report(match_query)
    if db:
        return db
    en_home, en_away = _team_names(match_query)
    rounds = [
        _football(f"{en_home} {en_away} injury news squad latest"),
        _football(f"{en_home} vs {en_away} starting lineup predicted XI formation"),
        _football(f"{en_home} {en_away} coach press conference tactics preview"),
        _football(f"{en_home} {en_away} recent form group standings"),
        _football(f"{en_home} {en_away} key players preview analysis"),
        f"{match_query} 足球 伤病 首发 阵容 教练 战术",
        f"{match_query} 足球 近期战绩 出线形势",
    ]
    return _search_with("新闻", rounds)


def search_post_match(match_query: str) -> str:
    # 查手动数据库，有原始赛后文本优先
    try:
        from core.match_db import get_match as _gm
        m = _gm(match_query)
        if not m:
            en_home, en_away = _team_names(match_query)
        elif m.get("post_report"):
            return m["post_report"]
        elif m.get("actual_h") is not None and m.get("actual_a") is not None:
            return f"最终比分: {m.get('home_team','主')} {m['actual_h']}-{m['actual_a']} {m.get('away_team','客')}"
    except Exception:
        pass
    en_home, en_away = _team_names(match_query)
    rounds = [
        _football(f"{en_home} {en_away} final score result match report"),
        _football(f"{en_home} vs {en_away} xG expected goals match statistics"),
        _football(f"{en_home} {en_away} goalscorers highlights"),
        f"{match_query} 足球 最终比分 赛后技术统计 xG",
    ]
    return _search_with("赛后", rounds)


def extract_odds(report_text: str) -> dict:
    """从搜索文本提取赔率。只在包含赔率关键词的区域搜索，避免误匹配。"""
    result = {}

    text = report_text
    # 限范围: 只在赔率/odds 密集区域搜
    odds_sections = []
    for kw in ["赔率", "odds", "胜赔", "欧赔", "1X2", "Home", "Draw", "Away"]:
        idx = text.lower().find(kw.lower())
        if idx >= 0:
            odds_sections.append(text[max(0, idx - 100):idx + 300])

    search_text = "\n".join(odds_sections) if odds_sections else text[:2000]

    patterns = [
        (r'(?:Home|主胜|主胜赔)[:\s]*(\d+\.?\d{0,2})', "odds_h"),
        (r'(?:Away|客胜|客胜赔)[:\s]*(\d+\.?\d{0,2})', "odds_a"),
        (r'(?:Draw|平局|平赔|和)[:\s]*(\d+\.?\d{0,2})', "odds_d"),
        (r'Over\s*2\.5[:\s]*(\d+\.?\d{0,2})', "over"),
        (r'Under\s*2\.5[:\s]*(\d+\.?\d{0,2})', "under"),
        (r'总进球.*?大[:\s]*(\d+\.?\d{0,2})', "over"),
        (r'总进球.*?小[:\s]*(\d+\.?\d{0,2})', "under"),
    ]
    for pattern, key in patterns:
        m = re.search(pattern, search_text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 1.1 < val < 100.0:
                    result[key] = val
            except ValueError: pass
    return result


def extract_structured(llm_response: str) -> dict | None:
    m = re.search(r'<!--STRUCTURED-->(.*?)<!--END-->', llm_response, re.DOTALL)
    if not m: return None
    try: return json.loads(m.group(1).strip())
    except json.JSONDecodeError: return None


def clean_report(llm_response: str) -> str:
    return re.sub(r'<!--STRUCTURED-->.*?<!--END-->', '', llm_response,
                  flags=re.DOTALL).strip()
