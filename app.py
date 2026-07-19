import json, math, re, os
from datetime import datetime

import streamlit as st
from core.auth import login_user, register_user, restore_login, logout
from core.config import DEEPSEEK_KEY, SUPABASE_KEY, SUPABASE_URL
from core.supabase_client import get_supabase
from core.models import LambdaModifiers, MatchPrediction
from core.engine import predict_match, predict_branched, odds_to_lambda
from core.rules import run_rules, detect_knockout
from core.search import (
    search_quantitative, search_qualitative,
    _deepseek_chat, extract_odds, extract_structured, clean_report,
)
from core.database import (
    save_record, load_history, load_laws, load_record_to_session,
    delete_record, clear_calibration,
)
from core.calibrate import (
    calibrate_record, can_calibrate,
    calculate_accuracy, analyze_global_bias,
)
from core.quota import get_quota, can_predict, deduct_quota
from core.ui import flag_img, score_card_html
from core.config import PROMPT_SEARCH, PROMPT_ANALYSIS

# ── Page setup ──
st.set_page_config(
    page_title="全维推演工厂", page_icon="⚽",
    layout="wide", initial_sidebar_state="collapsed",
)

# ── CSS ──
st.markdown("""
<style>
@keyframes fadeSlideUp{0%{opacity:0;transform:translateY(20px)}100%{opacity:1;transform:translateY(0)}}
@keyframes fadeIn{0%{opacity:0}100%{opacity:1}}
@keyframes glowPulse{0%{box-shadow:0 0 10px rgba(74,140,255,0)}50%{box-shadow:0 0 30px rgba(74,140,255,0.3)}100%{box-shadow:0 0 10px rgba(74,140,255,0)}}
@keyframes barGrow{0%{transform:scaleX(0);transform-origin:left}100%{transform:scaleX(1);transform-origin:left}}
.block-container{animation:fadeIn 0.2s ease-out}
.score-card{animation:fadeSlideUp 0.6s ease-out;text-align:center;margin-bottom:1.2em;background:linear-gradient(145deg,#1a2340,#0f1528);border:1px solid #2a3a5a;border-radius:20px;padding:1.8em 1.5em;box-shadow:0 8px 40px rgba(0,0,0,0.4),inset 0 1px 0 rgba(74,140,255,0.1);position:relative;overflow:hidden}
.score-card.result-new{animation:fadeSlideUp 0.6s ease-out,glowPulse 2s ease-out 0.3s}
.score-card .score{font-size:2.8rem;font-weight:800;letter-spacing:2px;line-height:1.2}
.score-fade{background:linear-gradient(135deg,#fff 0%,#90caf9 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.score-card .probs{font-size:.95rem;margin-top:.6rem;color:#8899bb}
.prob-bar{display:flex;height:6px;border-radius:3px;overflow:hidden;margin:.8em auto 0;max-width:400px;background:#1a2340}
.seg-home{background:#4a8cff;animation:barGrow 0.8s ease-out 0.3s both}
.seg-draw{background:#64b5f6;animation:barGrow 0.8s ease-out 0.4s both}
.seg-away{background:#2a3a5a;animation:barGrow 0.8s ease-out 0.5s both}
.badge{display:inline-block;font-size:.75rem;font-weight:600;padding:2px 10px;border-radius:20px;background:rgba(74,140,255,.15);color:#90caf9;margin:2px}
.law-grade-s{background:linear-gradient(135deg,#ff6b35,#ff8a65)!important;color:#fff!important}
.law-grade-d{background:#3a4a6a!important;color:#8899bb!important}
.stApp{background:#0a0e17}
.stButton button{border-radius:10px;font-weight:600;transition:all .2s;border:none;min-height:44px}
.stButton button:hover{transform:translateY(-1px);box-shadow:0 4px 14px rgba(74,140,255,.3)}
.stTextInput input{border-radius:10px!important;border:1px solid #2a3a5a!important;background:#141b2d!important;color:#e8edf5!important}
section[data-testid="stSidebar"]>div:nth-child(1){background:#0d1220}
@media(max-width:768px){
.score-card{padding:1.2em .8em;border-radius:14px;margin-bottom:1em}
.score-card .score{font-size:2rem}
.score-card .probs{font-size:.8rem}
}
</style>""", unsafe_allow_html=True)

def _render_calibration_result(result, ac: int):
    """校准完成后渲染分步报告"""
    ms = result.get("math_score", {})
    st.markdown("---")
    st.subheader("📊 赛后校准报告")

    # ── Step 1: 准确率评分 ──
    st.markdown("##### ① 准确率评分")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("准确率", f"{ms.get('accuracy',0)}/100")
    c2.metric("推演比分", ms.get("predicted","?-?"))
    c3.metric("实际比分", ms.get("actual","?-?"))
    c4.metric("进球偏差", f"{ms.get('deviation',0)}球")
    st.caption(
        f"比分命中: {'✅ 是' if ms.get('score_match') else '❌ 否'} | "
        f"胜负命中: {'✅ 是' if ms.get('result_match') else '❌ 否'} | "
        f"xG: {ms.get('xG','?-?')}"
    )
    if st.session_state.pop("calibration_hit", False):
        st.success("🎯 校准命中！奖励 +1 次")

    # ── Step 2: 数据总结 ──
    ds = result.get("data_summary", "")
    if ds:
        st.markdown("##### ② 赛后技术统计")
        st.markdown(ds)

    # ── Step 3: 偏差分析 ──
    ba = result.get("bias_analysis", "")
    if ba:
        st.markdown("##### ③ 偏差分析")
        st.markdown(ba)

    # ── Step 4: 定律更新 ──
    lu = result.get("law_updates", {})
    if lu:
        st.markdown("##### ④ 定律更新")
        if lu.get("new"):
            st.success(f"新增 {len(lu['new'])} 条: {', '.join(lu['new'])}")
        if lu.get("modified"):
            st.info(f"修改 {len(lu['modified'])} 条")
        if lu.get("degraded"):
            st.warning(f"降级 {len(lu['degraded'])} 条: {', '.join(lu['degraded'])}")
        if ac > 0:
            st.caption(f"已自动保存 {ac} 条定律更新到定律库")


def _render_calibration_json(cal_data):
    """从历史记录渲染校准报告——支持新旧两种格式"""
    try:
        cd = json.loads(cal_data) if isinstance(cal_data, str) else cal_data
    except (json.JSONDecodeError, TypeError):
        st.markdown(cal_data)
        return

    if isinstance(cd, dict) and "math_score" in cd:
        # 新版结构化
        ms = cd.get("math_score", {})
        st.markdown(f"**准确率: {ms.get('accuracy',0)}/100** "
                    f"| 推演 {ms.get('predicted','?-?')} → 实际 {ms.get('actual','?-?')} "
                    f"| 偏差 {ms.get('deviation',0)}球")
        ds = cd.get("data_summary", "")
        if ds:
            st.markdown("**技术统计**")
            st.markdown(ds)
        ba = cd.get("bias_analysis", "")
        if ba:
            st.markdown(ba)
    else:
        # 旧版文本
        st.markdown(str(cd))


# ── Session init ──
_defaults = {
    "logged_in": False, "username": "", "email": "", "auth_user_id": None,
    "view": "predict", "search_report": "", "analysis_report": "",
    "current_match": "", "current_match_time": "", "math_json": "",
    "training_mode": False, "is_knockout": False,
    "coach_info": {}, "last_triggered_laws": [], "triggered_details": [],
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

restore_login()

# 数据库操作统一用 service_role key，不走用户 session

# ── Auth Gate ──
if not st.session_state.logged_in:
    st.title("⚽ 全维推演工厂 - 登录")

    tl, tr = st.tabs(["登录", "注册"])
    with tl:
        le = st.text_input("邮箱 / 昵称", key="le")
        lp = st.text_input("密码", type="password", key="lp")
        if st.button("登录", use_container_width=True):
            if login_user(le, lp):
                st.session_state.logged_in = True
                st.success("登录成功！")
                st.rerun()
            else:
                st.error("邮箱或密码错误。")

    with tr:
        re_ = st.text_input("邮箱", key="re")
        ru = st.text_input("用户名", key="ru")
        rp = st.text_input("密码", type="password", key="rp")
        rc = st.text_input("确认密码", type="password", key="rc")
        agree = st.checkbox("我已阅读并同意用户协议", key="reg_agree")
        if st.button("注册", use_container_width=True):
            if rp != rc:
                st.error("两次密码不一致。")
            elif len(ru) < 2:
                st.error("用户名至少2个字符。")
            elif len(rp) < 6:
                st.error("密码至少6个字符。")
            elif "@" not in re_:
                st.error("请输入有效邮箱地址。")
            elif not agree:
                st.error("请先阅读并同意用户协议。")
            else:
                ok, msg = register_user(re_, rp, ru)
                if ok:
                    st.success(msg)
                else:
                    st.error(f"注册失败: {msg}")
    st.stop()

# ── Load data ──
laws = load_laws(st.session_state.username)
history = load_history(st.session_state.username)

# ── Sidebar ──
st.sidebar.markdown(
    f'<div style="text-align:center;padding:.5rem 0;font-size:1.1rem;font-weight:700">'
    f'⚽ 全维推演工厂</div>'
    f'<div style="text-align:center;font-size:.85rem;color:#8899bb;margin-bottom:.5rem">'
    f'👤 {st.session_state.username}</div>',
    unsafe_allow_html=True,
)

# Quota
q = get_quota(st.session_state.username)
rem = q["remaining"]
bar_p = max(0, min(100, rem / max(q["daily_limit"], 1) * 100))
st.sidebar.markdown(
    f'<div style="background:#141b2d;border-radius:10px;padding:.6em .8em;margin-bottom:.8em;font-size:.8rem">'
    f'<div style="display:flex;justify-content:space-between;color:#8899bb">'
    f'<span>{"👑 会员" if q["tier"]=="paid" else "🎫 免费"}</span>'
    f'<span>今日 <strong style="color:#4a8cff">{rem}</strong>/{q["daily_limit"]+q["bonus_quota"]} 次</span>'
    f'</div>'
    f'<div style="height:4px;background:#1a2340;border-radius:2px;margin-top:4px;overflow:hidden">'
    f'<div style="height:100%;width:{bar_p:.0f}%;background:#4a8cff;border-radius:2px"></div>'
    f'</div></div>',
    unsafe_allow_html=True,
)

# Accuracy
avg_acc, acc_recs = calculate_accuracy(history)
if avg_acc is not None:
    st.sidebar.markdown("### 📊 准确率")
    c1, c2 = st.sidebar.columns(2)
    c1.metric("综合", f"{avg_acc}/100")
    c2.metric("已校准", len(acc_recs))

    if len(acc_recs) >= 3:
        bias = analyze_global_bias(history)
        if bias and abs(bias["avg_deviation"]) >= 0.4:
            emoji = "🔴" if abs(bias["avg_deviation"]) >= 0.6 else "🟡"
            st.sidebar.warning(f"{emoji} {bias['bias_desc']}")

# Logout
if st.sidebar.button("🚪 登出", use_container_width=True):
    logout()

# ── Main Header ──
c1, c2, c3, c4 = st.columns(4)
with c1:
    if st.button("⚽ 推演", use_container_width=True,
                 type="primary" if st.session_state.view == "predict" else "secondary"):
        st.session_state.view = "predict"; st.rerun()
with c2:
    if st.button("📚 历史", use_container_width=True,
                 type="primary" if st.session_state.view == "history" else "secondary"):
        st.session_state.view = "history"; st.rerun()
with c3:
    if st.button("⚙️ 定律", use_container_width=True,
                 type="primary" if st.session_state.view == "laws" else "secondary"):
        st.session_state.view = "laws"; st.rerun()
with c4:
    if st.button("📋 比赛库", use_container_width=True,
                 type="primary" if st.session_state.view == "match_db" else "secondary"):
        st.session_state.view = "match_db"; st.rerun()

st.divider()


def _do_prediction(match: str, prog=None) -> str:
    """执行一条推演，返回 'ok' / 'skip' / 'fail'"""
    ok, msg = can_predict(st.session_state.username)
    if not ok:
        st.session_state._last_error = f"❌ 今日推演次数已用完"
        return "skip"

    laws = load_laws(st.session_state.username)

    from core.match_db import get_match as _gm
    manual_odds = None
    try:
        _manual = _gm(match)
        if _manual:
            oh = _manual.get("odds_h"); od = _manual.get("odds_d"); oa = _manual.get("odds_a")
            if oh and od and oa:
                manual_odds = (oh, od, oa)
    except Exception:
        pass

    if prog: prog.progress(0, text=f"⏳ {match} 🔍")
    quant_error = qual_error = ""
    try:
        quant_data = search_quantitative(match)
    except Exception as e:
        quant_error = str(e)[:80]; quant_data = ""
    try:
        qual_data = search_qualitative(match)
    except Exception as e:
        qual_error = str(e)[:80]; qual_data = ""

    combined = quant_data + "\n" + qual_data
    if not combined.strip() and not manual_odds:
        st.session_state._last_error = f"⚠ 🔍 没有结果 {quant_error} {qual_error}"
        return "skip"

        from core.config import MODEL_FAST, MODEL_PRO
    sr = _deepseek_chat(PROMPT_SEARCH,
        f"为 {match} 搜集赛前信息并输出结构化数据。\n定量数据(赔率等):\n{quant_data}\n\n定性数据(伤病/阵容):\n{qual_data}",
        DEEPSEEK_KEY, MODEL_FAST, fallback_cfgs=[MODEL_PRO])
    if not sr:
        st.session_state._last_error = f"⚠️ {match}: DeepSeek 搜索汇总返回空，请稍后重试"
        return "skip"

    search_report = clean_report(sr)
    structured = extract_structured(sr)
    is_ko = detect_knockout(search_report, structured)

    if prog: prog.progress(30, text=f"🔍 {match} 匹配定律...")
    rules_result = run_rules(search_report, structured, match, laws)
    modifiers = rules_result["modifiers"]
    triggered = rules_result["triggered"]
    coach_info = rules_result["coach_info"]
    has_branches = rules_result["has_uncertainty"]
    uncertainty = rules_result["uncertainty"]

    if prog: prog.progress(50, text=f"🧮 {match} 计算中...")
    odds_data = extract_odds(quant_data) if quant_data else {}
    lam_h0, lam_a0 = 1.5, 1.2
    odds_tuple = None
    if manual_odds:
        oh, od, oa = manual_odds
        s = 1/oh+1/od+1/oa
        lam_h0, lam_a0 = odds_to_lambda((1/oh)/s, (1/od)/s, (1/oa)/s)
        odds_tuple = manual_odds
    elif odds_data.get("odds_h") and odds_data.get("odds_d") and odds_data.get("odds_a"):
        oh, od, oa = odds_data["odds_h"], odds_data["odds_d"], odds_data["odds_a"]
        p_h = (1/oh)/(1/oh+1/od+1/oa); p_a = (1/oa)/(1/oh+1/od+1/oa); p_d = (1/od)/(1/oh+1/od+1/oa)
        lam_h0, lam_a0 = odds_to_lambda(p_h, p_d, p_a)
        odds_tuple = (oh, od, oa)

    from core.rules import parse_teams as _parse_teams
    parsed = _parse_teams(match); t1 = parsed[0] or "" if parsed else ""; t2 = parsed[1] or "" if parsed else ""

    if has_branches:
        branches = []
        for u in uncertainty:
            pnl = float(u.get("effect", 0.85))
            ma = LambdaModifiers(attack=modifiers.attack, defense=modifiers.defense, tactical=modifiers.tactical, coach_intent=modifiers.coach_intent, scenario=modifiers.scenario, home_adv=modifiers.home_adv, confidence=modifiers.confidence)
            mb = LambdaModifiers(attack=modifiers.attack*pnl, defense=modifiers.defense, tactical=modifiers.tactical, coach_intent=modifiers.coach_intent, scenario=modifiers.scenario, home_adv=modifiers.home_adv, confidence=modifiers.confidence)
            branches.append({"label":u.get("scenario_a","首发"),"weight":u.get("weight_a",0.5),"modifiers":ma})
            branches.append({"label":u.get("scenario_b","替补"),"weight":u.get("weight_b",0.5),"modifiers":mb})
        pred = predict_branched(t1, t2, lam_h0, lam_a0, branches).blended
    else:
        pred = predict_match(t1, t2, lam_h0, lam_a0, modifiers, odds_tuple)

    if prog: prog.progress(75, text=f"📝 {match} 生成报告...")
    math_json = json.dumps({"淘汰赛":is_ko, **pred.to_json(), "定律修正因子":{"attack":round(modifiers.attack,3),"defense":round(modifiers.defense,3),"tactical":round(modifiers.tactical,3),"coach_intent":round(modifiers.coach_intent,3),"scenario":round(modifiers.scenario,3),"home_adv":round(modifiers.home_adv,3)},"教练信息":coach_info,"触发定律":triggered,"赔率推导λ":f"{lam_h0:.2f}/{lam_a0:.2f}"}, ensure_ascii=False)

    report_prompt = f"赛前数据:\n{search_report[:6000]}\n\n数学计算结果:\n{json.dumps(pred.to_json(),ensure_ascii=False)}\n修正因子:{json.dumps({k:round(v,3) for k,v in modifiers.__dict__.items()},ensure_ascii=False)}\n触发的定律:{json.dumps([t['name'] for t in triggered],ensure_ascii=False)}\n"
    analysis = _deepseek_chat(PROMPT_ANALYSIS, report_prompt, DEEPSEEK_KEY, MODEL_FAST, fallback_cfgs=[MODEL_PRO])
    if not analysis:
        analysis = _deepseek_chat(PROMPT_ANALYSIS, report_prompt, DEEPSEEK_KEY, MODEL_FAST)

    mt_str = ""
    if structured and structured.get("match_time"):
        mt = structured["match_time"]
        if "YYYY" not in mt and "?" not in mt:
            mt_str = mt
    saved = save_record(match=match, search_report=search_report, analysis_report=analysis, math_json=math_json, triggered_laws=triggered, is_knockout=is_ko, match_time=mt_str)
    deduct_quota(st.session_state.username)

    # 写 session_state（用于单场推演展示）
    st.session_state.search_report = search_report
    st.session_state.analysis_report = analysis
    st.session_state.math_json = math_json
    st.session_state.math_prediction = pred
    st.session_state.coach_info = coach_info
    st.session_state.triggered_details = triggered
    st.session_state.current_match = match
    st.session_state.current_match_time = mt_str
    st.session_state.is_knockout = is_ko

    return "ok" if saved else "fail"







def _parse_match_texts(pre_text, post_text=""):
    """简单解析：只提取队名，原始文本直接存 search_report"""
    import re
    if not pre_text.strip():
        return []

    def _ps(text):
        r = {"home_team":"","away_team":"","tournament":"","search_report":text.strip(),"post_report":"",
             "actual_h":None,"actual_a":None}
        if not text.strip(): return None
        lines = text.split("\n")
        for line in lines:
            clean = line.lstrip("#").strip()
            m = re.search(r'(.+?)\s*(?:vs|VS|vs\.|V|对)\s*(.+)', clean)
            if m:
                r["home_team"] = m.group(1).strip()
                r["away_team"] = m.group(2).strip()
                break
        if not r["home_team"]:
            r["home_team"] = ""; r["away_team"] = ""
        # 赛事
        m = re.search(r'赛事[：:\s]*(.+)', text)
        if m:
            v = m.group(1).strip()
            if "2022" in v: r["tournament"] = "世界杯 2022"
            elif "2024" in v: r["tournament"] = "欧洲杯 2024"
            else: r["tournament"] = v
        return r

    blocks = re.split(r'\n\s*---+\s*\n|\n{3,}', pre_text.strip())
    results = []
    for block in blocks:
        if not block.strip(): continue
        parsed = _ps(block)
        if parsed: results.append(parsed)
    if not results:
        single = _ps(pre_text)
        if single: results = [single]

    if post_text.strip():
        post_blocks = re.split(r'\n\s*---+\s*\n|\n{3,}', post_text.strip())
        for i, pr in enumerate(results):
            if i < len(post_blocks):
                raw = post_blocks[i].strip()
                if raw: pr["post_report"] = raw
    return results

    # Split by --- or blank lines
    blocks = re.split(r'\n\s*---+\s*\n|\n{3,}', pre_text.strip())
    results = []
    for block in blocks:
        if not block.strip(): continue
        parsed = _ps(block)
        if parsed and (parsed["home_team"] or parsed["home_injuries"] or parsed["odds_h"]):
            results.append(parsed)
    if not results:
        single = _ps(pre_text)
        if single: results = [single]

    # Post data
    if post_text.strip():
        post_blocks = re.split(r'\n\s*---+\s*\n|\n{3,}', post_text.strip())
        for i, pr in enumerate(results):
            if i < len(post_blocks):
                raw = post_blocks[i].strip()
                if raw:
                    pr["post_report"] = raw
                m = re.search(r'(?:最终比分|比分)[：:\s]*(\d+)\s*[-:]\s*(\d+)', raw)
                if m:
                    try:
                        pr["actual_h"] = int(m.group(1))
                        pr["actual_a"] = int(m.group(2))
                    except: pass
    return results

def _save_to_match_db(match_name, t1, t2, odds_tuple, is_ko, match_time, analysis="", triggered=None):
    """自动将推演数据保存到比赛数据库"""
    try:
        from core.match_db import get_match as _gm, save_match as _sm
        exists = _gm(match_name)
        if exists:
            return  # 已有则不覆盖
        tournament = "淘汰赛" if is_ko else "小组赛"
        odds_d = {"odds_h": odds_tuple[0], "odds_d": odds_tuple[1], "odds_a": odds_tuple[2]} if odds_tuple else {}
        import re
        t_match = re.search(r'(?:202[2-6])', match_time or "")
        if t_match:
            tournament += f" {t_match.group()}"
        _sm({"match_name": match_name, "home_team": t1, "away_team": t2,
             "tournament": tournament, "match_time": match_time or "",
             **odds_d, "username": st.session_state.username})
    except Exception:
        pass


# ===================================================================
#  PREDICT TAB
# ===================================================================
if st.session_state.view == "predict":
    # 显示持久化的错误消息
    if st.session_state.get("_last_error"):
        st.error(st.session_state._last_error)

    # ── Single Predict ──
    match = st.text_input("比赛对阵", placeholder="法国 vs 塞内加尔", key="match_input")
    c1, c2 = st.columns([3, 1])
    with c1:
        if st.button("⚡ 一键推演", use_container_width=True, type="primary", key="do_predict"):
            if not match:
                st.warning("请先输入比赛名称")
            else:
                prog = st.progress(0, text="⏳ 搜索赛前数据...")
                result = _do_prediction(match, prog)
                prog.empty()
                if result == "ok":
                    st.success("✅ 推演完成！")
                    st.session_state._last_error = ""
                    st.session_state.fresh_result = True
                    st.session_state.current_match = match
                    st.rerun()
                elif result == "skip":
                    st.session_state._last_error = "⚠️ 搜索未返回有效数据，请检查比赛名称或稍后重试"
                    st.rerun()
                else:
                    st.warning("⚠️ 推演失败，请重试")
    with c2:
        add_queue = st.button("📋 加入队列", use_container_width=True, key="add_queue")

    # ── Batch Queue ──
    if "_predict_queue" not in st.session_state:
        st.session_state._predict_queue = []

    if add_queue and match.strip():
        if match.strip() not in st.session_state._predict_queue:
            st.session_state._predict_queue.append(match.strip())
            st.toast(f"已加入: {match.strip()}", icon="📋")
        else:
            st.toast("已在队列中")

    if st.session_state._predict_queue:
        st.divider()
        st.caption(f"📋 待推演队列 ({len(st.session_state._predict_queue)} 场)")
        cols = st.columns([6, 1, 1])
        for i, m in enumerate(st.session_state._predict_queue):
            with cols[0]:
                st.write(f"{i+1}. {m}")
            with cols[1]:
                if st.button("🗑️", key=f"rmq_{i}"):
                    st.session_state._predict_queue.pop(i)
                    st.rerun()
            with cols[2]:
                if st.button("⚡", key=f"runq_{i}"):
                    result = _do_prediction(m, st.progress(0, text=f"⏳ {m}"))
                    if result == "ok":
                        st.success(f"✅ {m} 推演完成！")
                        st.session_state._predict_queue.pop(i)
                        st.session_state.fresh_result = True
                        st.session_state.current_match = m
                        st.rerun()
                    elif result == "skip":
                        st.warning(f"⚠️ {m}: 搜索未返回有效数据")
                    else:
                        st.error(f"❌ {m}: 保存失败")
                    st.rerun()

        c_ra, c_cl = st.columns(2)
        with c_ra:
            if st.button("⚡ 推演队列全部", use_container_width=True, type="primary",
                         disabled=len(st.session_state._predict_queue) == 0):
                queue = list(st.session_state._predict_queue)
                bar = st.progress(0, text="⏳ 队列批量推演...")
                total = len(queue); okn = skn = fln = 0
                for i, m in enumerate(queue):
                    bar.progress((i+1)/total, text=f"[{i+1}/{total}] {m}")
                    r = _do_prediction(m, None)
                    if r == "ok": okn += 1
                    elif r == "skip": skn += 1
                    else: fln += 1
                bar.empty()
                st.session_state._predict_queue.clear()
                msg = f"完成 {total} 场: 成功 {okn}"
                if skn: msg += f", 跳过 {skn}"
                if fln: msg += f", 失败 {fln}"
                st.success(msg) if not fln else st.warning(msg)
                if okn > 0: st.rerun()
        with c_cl:
            if st.button("🗑️ 清空队列", use_container_width=True):
                st.session_state._predict_queue.clear()
                st.rerun()

    # ── Display Results ──
    _fresh = st.session_state.pop("fresh_result", False)
    if st.session_state.math_json:
        try:
            mj = json.loads(st.session_state.math_json) if isinstance(
                st.session_state.math_json, str) else st.session_state.math_json
        except json.JSONDecodeError:
            mj = {}

        home = mj.get("主队", "?") or "?"
        away = mj.get("客队", "?") or "?"
        score = mj.get("锁定比分", "?-?") or "?-?"
        hw = mj.get("主胜概率", "?") or "?"
        dw = mj.get("平局概率", "?") or "?"
        aw = mj.get("客胜概率", "?") or "?"
        conf = mj.get("模型置信度", "?") or "?"

        # ① Score Card
        st.markdown(score_card_html(home, away, score, hw, dw, aw, conf, _fresh),
                    unsafe_allow_html=True)

        # ② Coach Info
        ci = mj.get("教练信息", {})
        if ci:
            lines = []
            styles_map = {"defensive": "防守", "balanced": "均衡", "aggressive": "激进"}
            def_line_map = {"low": "低位", "mid": "中位", "high": "高位"}
            for cn, cd in ci.items():
                if cd.get("name") and cd["name"] != "?":
                    lines.append(
                        f'<span class="badge">教练</span> {cd.get("name","?")}'
                        f' · {cd.get("formation","?")}'
                        f' · {styles_map.get(cd.get("style",""),"?")}'
                        f' · {def_line_map.get(cd.get("def_line",""),"?")}'
                        f' · 定位球{cd.get("set_piece","?")}'
                    )
            if lines:
                st.markdown(
                    f'<div style="background:#141b2d;border:1px solid #2a3a5a;'
                    f'border-radius:14px;padding:.8em 1.2em;margin-bottom:1em;'
                    f'font-size:.9rem;color:#8899bb;line-height:1.8">'
                    f'🧑‍🏫 {"<br>".join(lines)}</div>',
                    unsafe_allow_html=True,
                )

        # ③ Triggered Laws
        td = mj.get("触发定律", [])
        if td:
            law_cards = []
            for t in td:
                name = t.get("name", "?")
                grade = t.get("grade", "D")
                grade_cls = "law-grade-s" if grade in ("S", "A") else (
                    "law-grade-d" if grade == "D" else "")
                mm = t.get("modifier_map", {})
                effects = " · ".join(f"{k} ×{v}" for k, v in mm.items())
                tc = t.get("triggers_count", 0)
                cc = t.get("correct_count", 0)
                acc_str = f"{round(cc/tc*100)}%" if tc > 0 else "新定律"
                law_cards.append(
                    f'<div style="display:inline-block;background:#141b2d;'
                    f'border:1px solid #2a3a5a;border-radius:12px;'
                    f'padding:.7em 1em;margin:4px;font-size:.85rem;color:#8899bb">'
                    f'<span class="badge law-grade {grade_cls}" '
                    f'style="margin-right:6px">{grade}</span>'
                    f'<strong style="color:#e8edf5">{name}</strong>'
                    f'<br><span style="font-size:.8rem">{effects}</span>'
                    f'<br><span style="font-size:.75rem;color:#5a7a9a">'
                    f'使用 {tc}次 · 准确率 {acc_str}</span></div>'
                )
            st.markdown(f'<div style="margin-bottom:1em">📜 {" ".join(law_cards)}</div>',
                        unsafe_allow_html=True)

        # ④ Branch Results
        br = st.session_state.get("branch_result")
        if br and br.blended:
            st.markdown("#### 🔀 分支推演")
            cols = st.columns(len(br.branches))
            for i, b in enumerate(br.branches):
                p = b["prediction"]
                with cols[i]:
                    st.markdown(
                        f'<div style="background:#141b2d;border:1px solid #2a3a5a;'
                        f'border-radius:12px;padding:.8em;text-align:center;font-size:.85rem">'
                        f'<strong>{b["label"]}</strong> ({b["weight"]:.0%})<br>'
                        f'<span style="font-size:1.2rem;color:#90caf9">'
                        f'{p.locked_h}-{p.locked_a}</span><br>'
                        f'<span style="color:#8899bb">'
                        f'{p.home_win:.0%}|{p.draw:.0%}|{p.away_win:.0%}</span>'
                        f'</div>', unsafe_allow_html=True,
                    )
            st.caption(
                f"加权综合: {br.blended.home_team or '主'} "
                f"{br.blended.exp_h:.1f}-{br.blended.exp_a:.1f} "
                f"{br.blended.away_team or '客'}"
            )

        # ⑤ Reports (collapsed)
        if st.session_state.search_report:
            with st.expander("📡 赛前数据报告", expanded=False):
                st.markdown(st.session_state.search_report)
        if st.session_state.analysis_report:
            with st.expander("🧠 推演报告", expanded=False):
                st.markdown(st.session_state.analysis_report)

    # ── Calibration ──
    has_report = bool(st.session_state.analysis_report)
    match_time_str = st.session_state.get("current_match_time")

    if has_report:
        st.markdown("---")
        st.subheader("📊 赛后校准")

        cc_ok, cc_msg = can_calibrate(match_time_str)

        if cc_ok:
            if st.button("🔍 搜集赛后数据并校准", use_container_width=True, type="primary"):
                with st.spinner("校准中..."):
                    recs = load_history(st.session_state.username)
                    match_rec = next(
                        (r for r in recs if r.get("match") == st.session_state.current_match),
                        None,
                    )
                    if match_rec:
                        recent = [r for r in recs if r.get("calibration")]
                        bias_rpt = analyze_global_bias(recs)
                        laws_now = load_laws(st.session_state.username)
                        ok, result, ac = calibrate_record(
                            match_rec, laws_now, recent, bias_rpt)
                        if ok:
                            _render_calibration_result(result, ac)
                            st.rerun()
                        else:
                            st.warning(result)
        else:
            st.info(f"⏳ {cc_msg}")

    st.stop()


# ===================================================================
#  HISTORY TAB
# ===================================================================
if st.session_state.view == "history":
    if not history:
        st.info("暂无推演记录。")
        st.stop()

    # 初始化选中列表
    if "_sel_cal" not in st.session_state:
        st.session_state._sel_cal = []
    if "_sel_clr" not in st.session_state:
        st.session_state._sel_clr = []

    uncalibrated = [r for r in history if not r.get("calibration")]
    calibrated = [r for r in history if r.get("calibration")]

    # ── Toolbar ──
    match_options = {r.get("match",""): i for i, r in enumerate(history)}
    match_list = list(match_options.keys())

    uncal_list = [m for m in match_list if not history[match_options[m]].get("calibration")]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"共 **{len(history)}** 条 · ⚪未校准 **{len(uncal_list)}** · 🟢已校准 **{len(history)-len(uncal_list)}**")
    with c2:
        all_uncal_btn, cal_sel_btn = st.columns(2)
        with all_uncal_btn:
            if st.button("⚡ 一键校准全部未校准", use_container_width=True, type="primary",
                         disabled=len(uncal_list)==0):
                bar = st.progress(0, text="批量校准中...")
                laws_now = load_laws(st.session_state.username)
                all_recs = history
                recent_cal = [r for r in all_recs if r.get("calibration")]
                bias_rpt = analyze_global_bias(all_recs)
                ok, sk = 0, 0
                for i, mk in enumerate(uncal_list):
                    bar.progress((i+1)/len(uncal_list), text=f"校准 {mk[:25]}...")
                    rec = history[match_options[mk]]
                    s, _, _ = calibrate_record(rec, laws_now, recent_cal, bias_rpt)
                    if s: ok += 1; recent_cal.append(rec)
                    else: sk += 1
                bar.empty()
                st.success(f"完成: 成功 {ok}，跳过 {sk}")
                st.rerun()

        with cal_sel_btn:
            selected = st.multiselect(
                "校准选中", uncal_list,
                placeholder=f"选比赛 ({len(uncal_list)}条未校准)",
                label_visibility="collapsed", key="cal_select"
            )
            if selected and st.button("⚡ 校准以上", use_container_width=True):
                bar = st.progress(0, text="批量校准中...")
                laws_now = load_laws(st.session_state.username)
                all_recs = history
                recent_cal = [r for r in all_recs if r.get("calibration")]
                bias_rpt = analyze_global_bias(all_recs)
                ok, sk = 0, 0
                for i, mk in enumerate(selected):
                    bar.progress((i+1)/len(selected), text=f"校准 {mk[:25]}...")
                    rec = history[match_options[mk]]
                    s, _, _ = calibrate_record(rec, laws_now, recent_cal, bias_rpt)
                    if s: ok += 1; recent_cal.append(rec)
                    else: sk += 1
                bar.empty()
                st.success(f"完成: 成功 {ok}，跳过 {sk}")
                st.rerun()

    st.divider()

    # ── Card List ──
    for i, rec in enumerate(history):
        is_calibrated = bool(rec.get("calibration"))
        mk = rec.get("match", "?")

        # 解析 math_json 取比分
        display_score = "?-?"
        home_team, away_team = "?", "?"
        hw_s, dw_s, aw_s = "?", "?", "?"
        mj = {}
        try:
            mj = json.loads(rec.get("math_json", "{}")) \
                if isinstance(rec.get("math_json"), str) \
                else rec.get("math_json", {})
        except json.JSONDecodeError:
            pass
        if mj:
            display_score = mj.get("锁定比分", "?-?")
            home_team = mj.get("主队", "?") or "?"
            away_team = mj.get("客队", "?") or "?"
            hw_s = mj.get("主胜概率", "?") or "?"
            dw_s = mj.get("平局概率", "?") or "?"
            aw_s = mj.get("客胜概率", "?") or "?"

        cal_badge = "🟢 已校准" if is_calibrated else "⚪ 未校准"
        title = (f"{display_score.replace('-',':')} "
                 f"{home_team} vs {away_team} | "
                 f"{rec.get('timestamp','')[:16]} | {cal_badge}")

        with st.expander(title, expanded=False):
            # Score Card (rendered from math_json)
            hf = flag_img(home_team)
            af = flag_img(away_team)
            st.markdown(
                f'<div style="background:#141b2d;border:1px solid #2a3a5a;'
                f'border-radius:14px;padding:1em 1.2em;margin-bottom:.6em;text-align:center">'
                f'<div style="font-size:2rem;font-weight:700;letter-spacing:2px;line-height:1.2;margin-bottom:.3em">'
                f'<span style="font-size:1.3rem">{hf}{home_team}</span> '
                f'{display_score.replace("-", ":")} '
                f'<span style="font-size:1.3rem">{af}{away_team}</span>'
                f'</div>'
                f'<div style="font-size:.85rem;color:#8899bb">'
                f'{hw_s} | {dw_s} | {aw_s}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Triggered Laws
            tl = mj.get("触发定律") or mj.get("triggered_laws", [])
            if tl:
                cards = []
                for t in tl:
                    if isinstance(t, dict):
                        name = t.get("name", "?")
                        grade = t.get("grade", "D")
                        cls = "law-grade-s" if grade in ("S", "A") else ("law-grade-d" if grade == "D" else "")
                        mm = t.get("modifier_map", {})
                        effects = " . ".join(f"{k} x{v}" for k, v in mm.items())
                        tc = t.get("triggers_count", 0)
                        cc = t.get("correct_count", 0)
                        acc = f"{round(cc/tc*100)}%" if tc > 0 else "新"
                        cards.append(
                            f'<span class="badge law-grade {cls}">{grade}</span>'
                            f'<strong>{name}</strong>'
                            f'<span style="font-size:.75rem;color:#8899bb"> {effects} | {tc}次·{acc}</span>'
                        )
                    else:
                        cards.append(f'<span class="badge">{t}</span>')
                st.markdown("<br>".join(cards), unsafe_allow_html=True)

            # Buttons
            bcols = st.columns([1, 1, 1, 1])
            with bcols[0]:
                if st.button("📂 加载", key=f"ld_{rec.get('id',i)}",
                             use_container_width=True):
                    load_record_to_session(rec)
                    st.session_state.view = "predict"
                    st.rerun()
            with bcols[1]:
                if st.button("🗑️ 删除", key=f"del_{rec.get('id',i)}",
                             use_container_width=True):
                    if delete_record(rec["id"]):
                        st.success("已删除")
                        st.rerun()
            with bcols[2]:
                if is_calibrated:
                    if st.button("🗑️ 删校准", key=f"clc_{rec.get('id',i)}",
                                 use_container_width=True):
                        if clear_calibration(rec["id"]):
                            st.success("已清空校准")
                            st.rerun()
                else:
                    cc_o, cc_m = can_calibrate(rec.get("match_time"))
                    if cc_o:
                        if st.button("🔍 校准", key=f"cal_{rec.get('id',i)}",
                                     use_container_width=True):
                            with st.spinner(f"校准 {mk}..."):
                                laws_now = load_laws(st.session_state.username)
                                recent_cal = [r for r in history if r.get("calibration")]
                                bias_rpt = analyze_global_bias(history)
                                ok, result, ac = calibrate_record(
                                    rec, laws_now, recent_cal, bias_rpt)
                                if ok:
                                    _render_calibration_result(result, ac)
                                    st.rerun()
                                else:
                                    st.warning(result)
                    else:
                        st.caption(f"⏳ {cc_m}")

            # Reports
            st.markdown("#### 📡 赛前数据")
            st.markdown(rec.get("search_report", "")[:2000])
            if rec.get("analysis_report"):
                with st.expander("🧠 推演报告", expanded=False):
                    st.markdown(rec["analysis_report"])
            if rec.get("calibration"):
                with st.expander("📊 校准报告", expanded=False):
                    _render_calibration_json(rec["calibration"])

    st.stop()


# ===================================================================
#  LAWS TAB
# ===================================================================
if st.session_state.view == "laws":
    st.markdown("#### ⚙️ 定律库")

    if "min_score" not in st.session_state:
        st.session_state.min_score = 0.0

    min_sc = st.slider("最低评分", 0.0, 0.5, st.session_state.min_score, 0.05,
                       help="只显示评分 >= 此值的定律", key="law_filter")
    st.session_state.min_score = min_sc

    if not laws:
        st.info("暂无定律。赛后校准会自动添加。")
        st.stop()

    # 计算评分并排序
    scored = []
    for l in laws:
        tc = l.get("triggers_count") or 0
        cc = l.get("correct_count") or 0
        ac = cc / tc if tc > 0 else 0
        sc = round(ac * (min(tc, 50) / 10), 2) if tc > 0 else 0
        scored.append((sc, l))

    scored.sort(key=lambda x: x[0], reverse=True)
    scored = [(sc, l) for sc, l in scored if sc >= min_sc or sc == 0]

    if not scored:
        st.caption("没有符合条件的定律。")

    # 按树分组
    from collections import defaultdict
    trees: dict[str, list] = defaultdict(list)
    for sc, l in scored:
        trees[l.get("tree", "通用")].append((sc, l))

    for tree_name, tree_laws in trees.items():
        st.markdown(f"##### 🌳 {tree_name}")
        for sc, law in tree_laws:
            if sc >= 0.30:
                g, cls = "S", "law-grade-s"
            elif sc >= 0.20:
                g, cls = "A", ""
            elif sc >= 0.10:
                g, cls = "B", ""
            elif sc >= 0.05:
                g, cls = "C", ""
            else:
                g, cls = "D", "law-grade-d"

            c1, c2, c3 = st.columns([1, 8, 1])
            with c1:
                active = law.get("status", "active") == "active"
                ns = st.toggle("🟢" if active else "🔴",
                               value=active,
                               key=f"lt_{law['id']}")
                if ns != active:
                    law["status"] = "active" if ns else "inactive"
                    from core.database import save_law
                    save_law(law)
                    st.rerun()
            with c2:
                parent = law.get("parent_id")
                prefix = "  └─ " if parent else ""
                name = law.get("name", "?")
                st.markdown(
                    f'<span class="badge law-grade {cls}">{g}</span> '
                    f'{prefix}<strong>{name}</strong> '
                    f'<span style="font-size:.8rem;color:#8899bb">'
                    f'{law.get("trigger_mode","?")} · </span>',
                    unsafe_allow_html=True,
                )
                mm = law.get("modifier_map") or {}
                effects = " · ".join(f"{k} ×{v}" for k, v in mm.items())
                if effects:
                    st.caption(effects)

                tc = law.get("triggers_count") or 0
                cc = law.get("correct_count") or 0
                if tc > 0:
                    st.caption(
                        f"使用 {tc}次 · 准确率 {round(cc/tc*100)}% · 评分 {sc:.2f}"
                    )
                else:
                    st.caption(f"新定律 · 待验证")
            with c3:
                if st.button("🗑️", key=f"dl_{law['id']}"):
                    from core.database import delete_law
                    if delete_law(law["id"]):
                        st.toast(f"已删除: {law['name']}", icon="🗑️")
                        st.rerun()
            st.divider()


# ===================================================================
#  MATCH DB TAB
# ===================================================================
if st.session_state.view == "match_db":
    st.markdown("#### 比赛数据库")
    from core.match_db import list_matches, delete_match, save_match

    pre_text = st.text_area("粘贴赛前数据（DeepSeek 输出）", height=120, key="db_pre")
    post_text = st.text_area("粘贴赛后数据（可选）", height=80, key="db_post")
    if st.button("解析并保存", use_container_width=True, type="primary"):
        pl = _parse_match_texts(pre_text, post_text)
        if not pl:
            st.error("无法解析")
        else:
            ok = 0
            for p in pl:
                if p.get("home_team") and p.get("away_team"):
                    if save_match({"match_name":f"{p['home_team']} vs {p['away_team']}",
                        "username":st.session_state.username,"home_team":p["home_team"],"away_team":p["away_team"],
                        "tournament":p.get("tournament",""),"search_report":p.get("search_report",""),
                        "post_report":p.get("post_report","")}):
                        ok += 1
            st.success(f"已保存 {ok}/{len(pl)} 场")
            st.rerun()

    st.divider()

    db_matches = list_matches(st.session_state.username)
    if not db_matches:
        st.info("暂无数据，上方粘贴后保存")

    for m in db_matches:
        h = m.get("home_team","?"); a = m.get("away_team","?")
        pre_len = len(m.get("search_report") or "")
        post_len = len(m.get("post_report") or "")
        act = f"实际 {m['actual_h']}-{m['actual_a']}" if m.get("actual_h") is not None else "未赛"
        with st.expander(f"{h} vs {a} — {act} | 赛前{pre_len}字 赛后{post_len}字", expanded=False):
            if m.get("search_report"):
                with st.expander("赛前原始数据", expanded=False):
                    st.text(m["search_report"][:2000])
            post_in = st.text_area("赛后数据补充", height=80, key=f"post_{m['id']}")
            c1,c2 = st.columns(2)
            with c1:
                if st.button("保存赛后", key=f"sp_{m['id']}"):
                    if post_in.strip():
                        save_match({"match_name":m["match_name"],"post_report":post_in.strip(),"username":m["username"]})
                        st.success("已更新"); st.rerun()
            with c2:
                if st.button("删除", key=f"del_{m['id']}"):
                    delete_match(m["id"]); st.rerun()