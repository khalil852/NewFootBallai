"""赛后校准：xG + 长期记忆 + 定律树分叉"""
import json, re
from datetime import datetime, timedelta, timezone

import streamlit as st
from core.config import (
    DEEPSEEK_KEY, DEEPSEEK_URL, SUPABASE_URL, SUPABASE_KEY,
    MODEL_FAST, MODEL_CALIBRATE, CALIBRATE_FALLBACKS, PROMPT_CALIBRATE,
)
from core.search import _deepseek_chat, search_post_match, extract_odds
from core.models import MatchPrediction, CalibrationResult
from core.database import update_law_stats
from core.rules import parse_teams


def _now_beijing() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)


def _parse_match_time(s: str) -> datetime | None:
    """解析开赛时间字符串"""
    if not s:
        return None
    s = s.strip()
    if re.search(r'[Zz]$', s):
        s = s[:-1] + "+00:00"
    s = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', s)
    tz = None
    tzm = re.search(r'([+-]\d{2}:\d{2})$', s)
    if tzm:
        s = s[:tzm.start()]
        tz = tzm.group(1)
    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"]:
        try:
            dt = datetime.strptime(s, fmt)
            if tz:
                sign = 1 if tz[0] == '+' else -1
                h_off, m_off = int(tz[1:3]), int(tz[4:6])
                dt = dt - sign * timedelta(hours=h_off, minutes=m_off) \
                     + timedelta(hours=8)
            return dt
        except ValueError:
            continue
    return None


def can_calibrate(match_time_str: str | None) -> tuple[bool, str]:
    """判断是否可以校准"""
    now = _now_beijing()
    if not match_time_str:
        return False, "未找到开赛时间（若比赛已有明确时间，请重试搜索）"
    # 必须带时区
    if not re.search(r'[+-]\d{2}:\d{2}', str(match_time_str)):
        return False, f"开赛时间缺少时区信息 ({match_time_str})"
    mt = _parse_match_time(match_time_str)
    if not mt:
        return False, f"无法解析开赛时间 ({match_time_str})"
    end_earliest = mt + timedelta(minutes=120)
    if now < mt:
        return False, f"尚未开始 ({mt.strftime('%Y-%m-%d %H:%M')})"
    if now < end_earliest:
        return False, f"可能仍在进行中 (预计 {end_earliest.strftime('%Y-%m-%d %H:%M')} 结束)"
    return True, "已结束，可以校准"


def _extract_actual_score(post_report: str) -> tuple[int | None, int | None,
                                                       float | None, float | None]:
    """从赛后报告提取比分和 xG"""
    # 先尝试 LLM 提取
    ah, aa, xg_h, xg_a = None, None, None, None

    try:
        prompt = (
            "从赛后报告中提取90分钟常规时间最终比分和xG。仅输出JSON。\n"
            '{"actual_h":2,"actual_a":1,"xG_h":1.8,"xG_a":0.7}\n'
            f"报告:\n{post_report[:4000]}"
        )
        r = _deepseek_chat("", prompt, DEEPSEEK_KEY, MODEL_FAST)
        m = re.search(r'\{[\s\S]*\}', r)
        if m:
            d = json.loads(m.group())
            ah = d.get("actual_h")
            aa = d.get("actual_a")
            xg_h = d.get("xG_h")
            xg_a = d.get("xG_a")
    except Exception:
        pass

    # 正则回退 — 三级力度
    if ah is None or aa is None:
        # 第1级: 紧跟在比分关键词后 ≤20 字符
        m = re.search(
            r'(?:最终比分|全场比赛结束|Full[-\s]?time|FT|Result|Final score)[:\s]*'
            r'(\d+)\s*[-:]\s*(\d+)',
            post_report, re.IGNORECASE)
        if m:
            ah, aa = int(m.group(1)), int(m.group(2))

    if ah is None or aa is None:
        # 第2级: 比分关键词附近 (可跨行)
        m = re.search(
            r'(?:最终比分|Full.time|FT|final).{0,60}?'
            r'(\d+)\s*[-:]\s*(\d+)',
            post_report, re.IGNORECASE | re.DOTALL)
        if m:
            ah, aa = int(m.group(1)), int(m.group(2))

    if ah is None or aa is None:
        # 第3级: 全文扫，取第一个合理比分 (≤10球)，排除年份
        for m in re.finditer(r'(?<!\d)(\d)\s*[-:]\s*(\d)(?!\d)', post_report[:3000]):
            a, b = int(m.group(1)), int(m.group(2))
            if a <= 10 and b <= 10:
                ah, aa = a, b
                break

    return ah, aa, xg_h, xg_a


def _calc_accuracy(pred: MatchPrediction, ah: int, aa: int,
                   xg_h: float | None = None,
                   xg_a: float | None = None) -> CalibrationResult:
    """计算校准评分"""
    score_match = (pred.locked_h == ah and pred.locked_a == aa)

    pred_result = "home" if pred.home_win > max(pred.draw, pred.away_win) \
        else "draw" if pred.draw > max(pred.home_win, pred.away_win) else "away"
    actual_result = "home" if ah > aa else "draw" if ah == aa else "away"
    result_match = pred_result == actual_result

    dev = abs(pred.locked_h - ah) + abs(pred.locked_a - aa)

    score = 100.0 - dev * 15
    if not result_match:
        score -= 25
    if pred.confidence < 0.5:
        score -= 10

    # xG 调整：区分模型错误 vs 运气
    if xg_h is not None and xg_a is not None:
        model_err = abs(pred.exp_h - xg_h) + abs(pred.exp_a - xg_a)
        luck = abs(xg_h - ah) + abs(xg_a - aa)
        if luck > 1.5:
            score += min(15, luck * 5)
        if model_err > 1.0:
            score -= min(15, model_err * 5)

    score = max(0, min(100, score))

    return CalibrationResult(
        accuracy_score=round(score, 1),
        score_match=score_match,
        result_match=result_match,
        goal_deviation=round(dev, 2),
        xG_h=xg_h,
        xG_a=xg_a,
    )


def calibrate_record(record: dict, laws: list[dict],
                     recent_calibrations: list[dict],
                     bias_report: dict | None,
                     api_key: str = "") -> tuple[bool, str, int]:
    """
    校准一条记录。
    返回: (success, calibration_report_text, auto_law_count)
    """
    from core.models import MatchPrediction

    match_name = record.get("match", "")
    search_report = record.get("search_report", "")
    analysis_report = record.get("analysis_report", "")
    math_json_raw = record.get("math_json", "{}")

    key = api_key or DEEPSEEK_KEY

    # ── 赛后搜索 ──
    post_data = search_post_match(match_name)

    # ── 提取实际比分 ──
    post_summary = ""
    if post_data:
        post_summary = _deepseek_chat(
            PROMPT_CALIBRATE,
            f"汇总以下赛后数据:\n{post_data}\n对阵: {match_name}",
            key,
        )
    else:
        post_summary = _deepseek_chat(
            PROMPT_CALIBRATE,
            f"**【硬性规则】** {match_name} 已结束，直接给出最终比分。\n"
            f"赛前数据:\n{search_report[:3000]}",
            key, MODEL_CALIBRATE,
        )

    if "比赛尚未开始" in post_summary:
        return False, post_summary, 0

    ah, aa, xg_h, xg_a = _extract_actual_score(post_summary)
    if ah is None or aa is None:
        ah, aa = 0, 0

    # ── 重建 MatchPrediction ──
    pred = None
    try:
        mj = json.loads(math_json_raw) if isinstance(math_json_raw, str) \
            else math_json_raw
        teams = mj.get("主队", ""), mj.get("客队", "")
        score_str = mj.get("锁定比分", "0-0")
        parts = score_str.split("-")
        lh, la = int(parts[0]) if parts[0].isdigit() else 0, \
            int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        hw = float(str(mj.get("主胜概率", "50%")).rstrip("%")) / 100
        dw = float(str(mj.get("平局概率", "20%")).rstrip("%")) / 100
        aw = float(str(mj.get("客胜概率", "30%")).rstrip("%")) / 100
        eh = float(mj.get("期望进球", "0-0").split("-")[0])
        ea = float(mj.get("期望进球", "0-0").split("-")[1])
        pred = MatchPrediction(
            home_team=teams[0], away_team=teams[1],
            locked_h=lh, locked_a=la,
            home_win=hw, draw=dw, away_win=aw,
            exp_h=eh, exp_a=ea,
            confidence=float(str(mj.get("模型置信度", "50%")).rstrip("%")) / 100,
        )
    except Exception:
        pred = MatchPrediction(locked_h=0, locked_a=0)

    # ── 数学评分 ──
    cal = _calc_accuracy(pred, ah, aa, xg_h, xg_a)

    # ── 更新定律统计 ──
    # 从 math_json 里取 triggered_laws
    mj = {}
    try:
        mj = json.loads(record.get("math_json", "{}")) if isinstance(record.get("math_json"), str) else record.get("math_json", {})
    except (json.JSONDecodeError, TypeError):
        pass
    triggered_names = mj.get("触发定律") or mj.get("triggered_laws", [])

    if triggered_names and cal is not None:
        is_correct = cal.result_match or cal.score_match
        for t_name in triggered_names:
            name = t_name.get("name", t_name) if isinstance(t_name, dict) \
                else t_name
            matching = [l for l in laws if l.get("name") == name]
            if matching:
                update_law_stats(matching[0]["id"],
                                 delta_trigger=1,
                                 delta_correct=1 if is_correct else 0)

    # ── 校准命中奖励 ──
    if cal and (cal.score_match or cal.result_match):
        try:
            from core.quota import add_bonus
            add_bonus(st.session_state.username, 1)
            st.session_state["calibration_hit"] = True
        except Exception:
            pass

    # ── 构建记忆上下文 ──
    recent_summary = "\n".join(
        f"{r.get('match','')}: {r.get('score_summary','')}"
        for r in recent_calibrations[-10:]
    ) if recent_calibrations else "(无)"

    bias_text = ""
    if bias_report:
        bias_text = (
            f"全局({bias_report['total_matches']}场): "
            f"{bias_report['bias_direction']}, "
            f"场均偏差 {bias_report['avg_deviation']}球, "
            f"比分准确率 {bias_report['score_accuracy']}%, "
            f"胜负准确率 {bias_report['result_accuracy']}%"
        )

    # ── 提取赛后技术统计 ──
    stats_html = ""
    try:
        stats_prompt = (
            "从赛后数据中提取技术统计。仅输出JSON，字段用英文key，数值不用带单位。\n"
            '{"stats": {"shots_h":10,"shots_a":8,"shots_on_target_h":4,"shots_on_target_a":3,'
            '"possession_h":55,"possession_a":45,"xG_h":1.8,"xG_a":0.7,'
            '"corners_h":6,"corners_a":3,"yellow_cards_h":2,"yellow_cards_a":1,"red_cards_h":0,"red_cards_a":0,'
            '"fouls_h":12,"fouls_a":14,"passes_h":480,"passes_a":390}}\n'
            f"赛后数据:\n{post_summary[:4000]}"
        )
        stats_raw = _deepseek_chat("", stats_prompt, key, MODEL_FAST)
        sm = re.search(r'\{[\s\S]*\}', stats_raw)
        if sm:
            stats_data = json.loads(sm.group()).get("stats", {})
            if stats_data:
                teams = (parse_teams(match_name)
                         or (record.get("home","主队"), record.get("away","客队")))
                home_name = teams[0] or "主队"
                away_name = teams[1] or "客队"
                labels = {
                    "shots": "射门", "shots_on_target": "射正",
                    "possession": "控球率%", "xG": "xG",
                    "corners": "角球", "yellow_cards": "黄牌",
                    "red_cards": "红牌", "fouls": "犯规", "passes": "传球",
                }
                rows = ["| 项目 | {} | {} |".format(home_name, away_name),
                        "|------|-----|-----|"]
                for key, label in labels.items():
                    hv = stats_data.get(f"{key}_h", "-")
                    av = stats_data.get(f"{key}_a", "-")
                    if hv != "-" or av != "-":
                        rows.append(f"| {label} | {hv} | {av} |")
                stats_html = "\n".join(rows)
    except Exception:
        pass

    # ── Step 3: 偏差分析 ──
    calibrate_prompt = (
        f"## 全局偏差趋势\n{bias_text}\n\n"
        f"## 近期校准记录\n{recent_summary}\n\n"
        f"## 当前定律库\n{json.dumps(laws, ensure_ascii=False)}\n\n"
        f"## 本场校准\n"
        f"赛后数据:\n{post_summary[:4000]}\n\n"
        f"赛前推演:\n推演比分: {pred.locked_h}-{pred.locked_a}\n"
        f"实际: {ah}-{aa}\n"
        f"数学评分: {cal.accuracy_score}/100\n"
        f"偏差: {cal.goal_deviation}球\n"
        f"比分命中: {'是' if cal.score_match else '否'}\n"
        f"胜负命中: {'是' if cal.result_match else '否'}\n"
        f"xG: {xg_h or '?'}-{xg_a or '?'}\n"
        f"技术统计:\n{stats_html}\n"
        f"触发定律: {json.dumps(triggered_names, ensure_ascii=False)}\n\n"
        f"赛前报告:\n{analysis_report[:3000]}"
    )

    cr = _deepseek_chat(PROMPT_CALIBRATE, calibrate_prompt, key,
                        MODEL_CALIBRATE, fallback_cfgs=CALIBRATE_FALLBACKS)

    # ── 提取偏差分析文本 ──
    bias_analysis = re.sub(r'```json[\s\S]*?```', '', cr).strip()

    # ── 提取新定律 ──
    new_laws = []
    modified_laws = []
    degraded_laws = []
    auto_count = 0
    try:
        for block in re.finditer(r'```json\s*(.*?)\s*```', cr, re.DOTALL):
            data = json.loads(block.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue

                has_id = bool(item.get("id"))

                # 修改已有定律
                if has_id and item.get("trigger_config") is not None:
                    from core.database import save_law
                    save_law({
                        "id": item["id"],
                        "username": st.session_state.username,
                        "trigger_config": item.get("trigger_config", {}),
                        "modifier_map": item.get("modifier_map", {}),
                        "name": item.get("name", ""),
                    })
                    modified_laws.append(item.get("name", item["id"]))
                    auto_count += 1
                    continue

                # 查重 — 同名定律已存在则跳过
                existing_names = {l.get("name","") for l in laws}
                if item.get("name","") in existing_names:
                    st.toast(f"跳过定律: '{item['name']}' 已存在", icon="⚠️")
                    continue

                # 新增定律 — 必须通过校验
                if _validate_trigger(item):
                    lid = f"auto_{datetime.now().strftime('%Y%m%d%H%M%S')}_{auto_count}"
                    item["id"] = lid
                    item["username"] = st.session_state.username
                    item.setdefault("status", "active")
                    item.setdefault("auto_generated", True)
                    from core.database import save_law
                    save_law(item)
                    new_laws.append(item.get("name", lid))
                    auto_count += 1

        degrade_block = re.search(
            r'(?:建议降级|降级).*?```json\s*(.*?)\s*```', cr, re.DOTALL)
        if degrade_block:
            degrade_ids = json.loads(degrade_block.group(1))
            if isinstance(degrade_ids, list):
                for did in degrade_ids:
                    from core.database import save_law
                    save_law({"id": did, "username": st.session_state.username,
                              "status": "inactive"})
                    degraded_laws.append(did)
    except Exception:
        pass

    # ── 构建结构化校准结果 ──
    cal_result = {
        "math_score": {
            "accuracy": cal.accuracy_score,
            "predicted": f"{pred.locked_h}-{pred.locked_a}",
            "actual": f"{ah}-{aa}",
            "deviation": cal.goal_deviation,
            "score_match": cal.score_match,
            "result_match": cal.result_match,
            "xG": f"{xg_h or '?'}-{xg_a or '?'}",
        },
        "data_summary": stats_html,
        "bias_analysis": bias_analysis,
        "law_updates": {
            "new": new_laws,
            "modified": modified_laws,
            "degraded": degraded_laws,
        },
    }

    # ── 保存校准结果（序列化整个 dict） ──
    try:
        from core.supabase_client import get_supabase
        get_supabase().table("history").update(
            {"calibration": json.dumps(cal_result, ensure_ascii=False)}
        ).eq("id", record["id"]).execute()
    except Exception:
        pass

    return True, cal_result, auto_count


FORBIDDEN_WORDS = [
    "实际", "赛后", "射门", "控球", "xG", "xG",
    "上半场", "下半场", "进球", "失球", "比赛中",
    "比分", "结果", "最终", "绝杀", "逆转",
    "爆冷", "输球", "赢了", "输了", "取胜", "获胜",
]


def _validate_trigger(law: dict) -> bool:
    """校验新定律触发条件的合法性"""
    config_str = json.dumps(law.get("trigger_config", {}), ensure_ascii=False)
    trigger_mode = law.get("trigger_mode", "")

    # 1. trigger_mode 必须合法
    if trigger_mode not in ("keyword", "team", "coach", "match_type", "always"):
        return False

    # 2. 不含赛后词汇
    for w in FORBIDDEN_WORDS:
        if w in config_str:
            st.toast(f"跳过定律: 含赛后词汇 '{w}'", icon="⚠️")
            return False

    # 3. config 不能是空对象（unless always mode）
    if trigger_mode != "always" and len(config_str) < 6:
        return False

    # 4. modifier_map 值必须在 0.70-1.30 范围内
    mm = law.get("modifier_map", {})
    for k, v in mm.items():
        try:
            fv = float(v)
            if fv < 0.70 or fv > 1.30:
                st.toast(f"跳过定律: modifier {k}={fv} 超出范围", icon="⚠️")
                return False
        except (ValueError, TypeError):
            return False

    # 5. keyword 模式的 keywords 不能全是赛后词汇
    if trigger_mode == "keyword":
        keywords = law.get("trigger_config", {}).get("keywords", [])
        if isinstance(keywords, list):
            all_post = all(
                any(w in kw.lower() for w in FORBIDDEN_WORDS[:8])
                for kw in keywords
            )
            if all_post:
                st.toast("跳过定律: 所有关键词都是赛后词汇", icon="⚠️")
                return False
            # 至少一个关键词长度 > 1
            if not any(len(kw) > 1 for kw in keywords):
                return False

    return True


def analyze_global_bias(calibrated_records: list[dict]) -> dict | None:
    """全局偏差分析"""
    n = len(calibrated_records)
    if n < 3:
        return None

    samples = []
    for rec in calibrated_records:
        ct = rec.get("calibration", "")

        # 新版 JSON dict
        try:
            cd = json.loads(ct) if isinstance(ct, str) else ct
            if isinstance(cd, dict) and "math_score" in cd:
                actual_parts = cd["math_score"].get("actual", "0-0").split("-")
                cal_h, cal_a = int(actual_parts[0]), int(actual_parts[1])
            else:
                continue
        except (json.JSONDecodeError, TypeError, ValueError):
            # 旧版文本格式
            am = re.search(r'实际[：:\s]*(\d+)\s*[-:]\s*(\d+)', str(ct))
            if not am:
                continue
            cal_h, cal_a = int(am.group(1)), int(am.group(2))

        try:
            mj = json.loads(rec["math_json"]) \
                if isinstance(rec.get("math_json"), str) else rec.get("math_json", {})
        except Exception:
            continue

        score_str = mj.get("锁定比分", "0-0")
        pm = re.match(r'(\d+)\s*[-:]\s*(\d+)', str(score_str))
        if not pm:
            continue

        pred_h, pred_a = int(pm.group(1)), int(pm.group(2))
        is_ko = mj.get("淘汰赛", rec.get("is_knockout", False))
        hwp_str = mj.get("主胜概率", "50%")
        hwp = float(str(hwp_str).rstrip("%")) / 100

        samples.append({
            "goal_dev": (cal_h + cal_a) - (pred_h + pred_a),
            "pred_total": pred_h + pred_a,
            "actual_total": cal_h + cal_a,
            "score_match": pred_h == cal_h and pred_a == cal_a,
            "result_match": (
                (pred_h > pred_a and cal_h > cal_a)
                or (pred_h < pred_a and cal_h < cal_a)
                or (pred_h == pred_a and cal_h == cal_a)
            ),
            "is_ko": is_ko,
            "fav_prob": hwp,
        })

    if len(samples) < 3:
        return None

    deviations = [s["goal_dev"] for s in samples]
    avg_dev = sum(deviations) / len(deviations)
    score_hits = sum(1 for s in samples if s["score_match"])
    result_hits = sum(1 for s in samples if s["result_match"])

    if avg_dev > 0.3:
        bias_dir = "保守偏差"
        bias_desc = f"场均低估 {avg_dev:.1f} 球"
        suggestion = "建议上调 λ 基准或降低防守修正"
    elif avg_dev < -0.3:
        bias_dir = "激进偏差"
        bias_desc = f"场均高估 {abs(avg_dev):.1f} 球"
        suggestion = "建议下调 λ 基准"
    else:
        bias_dir = "无系统性偏差"
        bias_desc = f"偏差均值 {avg_dev:.1f} 球"
        suggestion = ""

    ko = [s for s in samples if s["is_ko"]]
    grp = [s for s in samples if not s["is_ko"]]
    fav = [s for s in samples if s["fav_prob"] > 0.40]
    even = [s for s in samples if s["fav_prob"] <= 0.40]

    def _avg(l, key):
        return round(sum(s[key] for s in l) / len(l), 2) if l else None

    return {
        "total_matches": n,
        "score_accuracy": round(score_hits / n * 100, 1),
        "result_accuracy": round(result_hits / n * 100, 1),
        "avg_deviation": round(avg_dev, 2),
        "bias_direction": bias_dir,
        "bias_desc": bias_desc,
        "suggestion": suggestion,
        "ko_deviation": _avg(ko, "goal_dev"),
        "grp_deviation": _avg(grp, "goal_dev"),
        "fav_deviation": _avg(fav, "goal_dev"),
        "even_deviation": _avg(even, "goal_dev"),
    }


def calculate_accuracy(history: list[dict]) -> tuple[float | None, list[dict]]:
    """从历史记录统计准确率"""
    cal_recs = [r for r in history if r.get("calibration")]
    scores = []
    records = []
    for rec in cal_recs:
        ct = rec["calibration"]
        # 新版结构化 JSON
        try:
            cd = json.loads(ct) if isinstance(ct, str) else ct
            if isinstance(cd, dict) and "math_score" in cd:
                s = cd["math_score"].get("accuracy", 0)
                scores.append(s)
                records.append({
                    "match": rec["match"],
                    "timestamp": rec["timestamp"],
                    "score": s,
                })
                continue
        except (json.JSONDecodeError, TypeError):
            pass
        # 旧版文本格式
        m = re.search(r'准确率[评分]*[：:]\s*(\d+)', str(ct)) or \
            re.search(r'(\d+)\s*/\s*100', str(ct))
        if m:
            s = int(m.group(1))
            scores.append(s)
            records.append({
                "match": rec["match"],
                "timestamp": rec["timestamp"],
                "score": s,
            })

    if not scores:
        return None, []

    return round(sum(scores) / len(scores), 1), records
