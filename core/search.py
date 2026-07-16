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
    """调用 DeepSeek，支持自动回退"""
    from core.config import MODEL_DEFAULT
    cfgs = [model_cfg or MODEL_DEFAULT]
    if fallback_cfgs:
        cfgs += fallback_cfgs

    key = api_key or DEEPSEEK_KEY
    last_error = ""

    for i, cfg in enumerate(cfgs):
        if i > 0:
            st.toast(f"🔄 主模型失败，回退到 {cfg.get('model','?')}...", icon="⚠️")

        payload = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if "reasoning_effort" in cfg:
            payload["reasoning_effort"] = cfg["reasoning_effort"]
        if "max_tokens" in cfg:
            payload["max_tokens"] = cfg["max_tokens"]

        timeout = 120 if "reasoning_effort" in cfg else 90
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            r = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=timeout)
            d = r.json()
            if "error" in d:
                last_error = str(d["error"])
                continue
            if "choices" not in d or not d["choices"]:
                last_error = f"格式异常: {str(d)[:200]}"
                continue
            return d["choices"][0]["message"].get("content", "")
        except requests.exceptions.Timeout:
            last_error = f"超时 ({cfg['model']})"
            continue
        except Exception as e:
            last_error = str(e)[:80]
            continue

    st.error(f"所有模型均失败: {last_error}")
    return ""


@st.cache_data(ttl=3600, show_spinner=False)
def _tavily_search(query: str, include_domains: list[str] | None = None,
                   max_results: int = 8) -> str:
    if not TAVILY_KEY:
        return ""
    payload = {
        "api_key": TAVILY_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    try:
        r = requests.post(TAVILY_URL, json=payload,
                          headers={"Content-Type": "application/json"}, timeout=20)
        items = []
        for item in r.json().get("results", []):
            content = item.get("content", "")
            if len(content) > 20:
                items.append(f"- {item.get('title', '')}: {content[:600]}")
        return "\n".join(items)
    except Exception:
        return ""


def _to_english(query: str) -> str:
    """把中文队名转英文 → 'France vs Senegal'"""
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s+对阵\s+', r'\s+对\s+',
                r'\s*vs\s*', r'\s*v\s*']:
        parts = re.split(sep, query, re.IGNORECASE)
        if len(parts) == 2:
            left_words = [w.strip() for w in parts[0].split()
                          if re.search(r'[一-鿿\w]', w)]
            right_words = [w.strip() for w in parts[1].split()
                           if re.search(r'[一-鿿\w]', w)]
            if left_words and right_words:
                t1 = left_words[-1]
                t2 = right_words[0]
                en1 = TEAM_EN.get(t1, t1)
                en2 = TEAM_EN.get(t2, t2)
                return f"{en1} vs {en2}"
    return query.strip()


def _to_chinese_pair(query: str) -> tuple[str, str]:
    """返回 (队1中文, 队2中文)，用于构造中文搜索词"""
    for sep in [r'\s+vs\s+', r'\s+v\s+', r'\s+对阵\s+', r'\s+对\s+',
                r'\s*vs\s*', r'\s*v\s*']:
        parts = re.split(sep, query, re.IGNORECASE)
        if len(parts) == 2:
            left = [w.strip() for w in parts[0].split()
                    if re.search(r'[一-鿿\w]', w)]
            right = [w.strip() for w in parts[1].split()
                     if re.search(r'[一-鿿\w]', w)]
            if left and right:
                return left[-1], right[0]
    return query, ""


def search_quantitative(match_query: str) -> str:
    """赔率搜索 — 英文关键词 + 解锁域名"""
    en = _to_english(match_query)
    t1, t2 = _to_chinese_pair(match_query)

    rounds = [
        f"{en} match odds 1X2 over under World Cup 2026",
        f"{en} betting odds winner draw 2026",
        f"{t1} {t2} 赔率 2026 世界杯",
    ]
    results = ""
    with ThreadPoolExecutor(max_workers=len(rounds)) as ex:
        futures = [ex.submit(_tavily_search, q) for q in rounds]
        for f in as_completed(futures):
            r = f.result()
            if r:
                results += r + "\n"
    return results


def search_qualitative(match_query: str) -> str:
    """定性搜索 — 英文 + 中文 各维度"""
    en = _to_english(match_query)
    t1, t2 = _to_chinese_pair(match_query)

    rounds = [
        # 伤病 / 阵容
        f"{en} injury news squad latest 2026 World Cup",
        f"{en} starting lineup predicted XI formation 2026",
        f"{t1} {t2} 伤病 首发 阵容 2026",
        # 教练 / 战术
        f"{en} coach press conference tactics preview 2026",
        f"{t1} {t2} 教练 赛前 发布会 战术 2026",
        # 状态 / 形势
        f"{en} recent form results group standings 2026",
        f"{t1} {t2} 近期 战绩 出线 形势 2026",
        # 关键球员 / 看点
        f"{en} key players to watch preview analysis 2026",
    ]
    results = ""
    with ThreadPoolExecutor(max_workers=min(len(rounds), 6)) as ex:
        futures = [ex.submit(_tavily_search, q) for q in rounds]
        for f in as_completed(futures):
            r = f.result()
            if r:
                results += r + "\n"
    return results


def search_post_match(match_query: str) -> str:
    """赛后搜索"""
    en = _to_english(match_query)
    t1, t2 = _to_chinese_pair(match_query)

    rounds = [
        f"{en} final score result match report 2026 World Cup",
        f"{en} xG expected goals match statistics 2026",
        f"{en} goalscorers assists highlights 2026",
        f"{t1} {t2} 最终 比分 技术 统计 2026",
    ]
    results = ""
    with ThreadPoolExecutor(max_workers=len(rounds)) as ex:
        futures = [ex.submit(_tavily_search, q) for q in rounds]
        for f in as_completed(futures):
            r = f.result()
            if r:
                results += r + "\n"
    return results


def extract_odds(report_text: str) -> dict:
    result = {}
    text = report_text.lower()
    patterns = [
        (r'(?:Home|主胜|主)[^0-9]*?(\d+\.?\d*)', "odds_h"),
        (r'(?:Away|客胜|客)[^0-9]*?(\d+\.?\d*)', "odds_a"),
        (r'(?:Draw|平局|平|和)[^0-9]*?(\d+\.?\d*)', "odds_d"),
        (r'Over\s*2\.5[^0-9]*?(\d+\.?\d*)', "over"),
        (r'Under\s*2\.5[^0-9]*?(\d+\.?\d*)', "under"),
        (r'总进球.*?大[^0-9]*?(\d+\.?\d*)', "over"),
        (r'总进球.*?小[^0-9]*?(\d+\.?\d*)', "under"),
    ]
    for pattern, key in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                if 1.0 < val < 50.0:
                    result[key] = val
            except ValueError:
                pass
    return result


def extract_structured(llm_response: str) -> dict | None:
    m = re.search(r'<!--STRUCTURED-->(.*?)<!--END-->', llm_response, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return None


def clean_report(llm_response: str) -> str:
    return re.sub(r'<!--STRUCTURED-->.*?<!--END-->', '', llm_response,
                  flags=re.DOTALL).strip()
