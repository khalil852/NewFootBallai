"""数据库操作：REST API"""
import json, requests
from datetime import datetime
import streamlit as st
from core.config import SUPABASE_URL, SUPABASE_KEY

_REST_H = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
_BASE = SUPABASE_URL.rstrip("/")


def _username() -> str:
    return st.session_state.get("username", "")


# ── History ──

def save_record(match: str, search_report: str, analysis_report: str,
                math_json: str, triggered_laws: list[dict],
                is_knockout: bool = False, match_time: str | None = None) -> bool:
    """保存推演记录"""
    # 新字段塞进 math_json（表里没有这些列）
    import json as _json
    mj = _json.loads(math_json) if isinstance(math_json, str) else math_json
    mj["triggered_laws"] = triggered_laws
    mj["is_knockout"] = is_knockout
    math_json_str = _json.dumps(mj, ensure_ascii=False)

    rec = {
        "username": _username(),
        "match": match,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "match_time": match_time,
        "search_report": search_report,
        "analysis_report": analysis_report,
        "math_json": math_json_str,
    }
    try:
        r = requests.post(f"{_BASE}/rest/v1/history", headers=_REST_H,
                          json=rec, timeout=10)
        if r.status_code in (200, 201):
            return True
        st.error(f"保存失败 [{r.status_code}]: {r.text[:300]}")
        return False
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False


def load_history(username: str) -> list[dict]:
    try:
        r = requests.get(
            f"{_BASE}/rest/v1/history?"
            f"username=eq.{username}&order=timestamp.desc",
            headers=_REST_H, timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def load_record_to_session(rec: dict) -> None:
    st.session_state.search_report = rec.get("search_report", "")
    st.session_state.analysis_report = rec.get("analysis_report", "")
    st.session_state.current_match = rec.get("match", "")
    st.session_state.math_json = rec.get("math_json", "")
    st.session_state.current_record_id = rec.get("id")
    st.session_state.current_match_time = rec.get("match_time")

    mj = {}
    try:
        mj = json.loads(rec.get("math_json", "{}")) if isinstance(rec.get("math_json"), str) else rec.get("math_json", {})
    except (json.JSONDecodeError, TypeError):
        pass
    st.session_state.training_mode = rec.get("training_mode", False)
    st.session_state.is_knockout = rec.get("is_knockout") or mj.get("is_knockout", False)

    tl = mj.get("triggered_laws", [])
    st.session_state["last_triggered_laws"] = [
        t.get("name", t) if isinstance(t, dict) else t for t in tl
    ]


def delete_record(record_id: int) -> bool:
    try:
        r = requests.delete(f"{_BASE}/rest/v1/history?id=eq.{record_id}",
                            headers=_REST_H, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        return False


def clear_calibration(record_id: int) -> bool:
    try:
        r = requests.patch(f"{_BASE}/rest/v1/history?id=eq.{record_id}",
                           headers=_REST_H, json={"calibration": None}, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        return False


# ── Laws ──

def load_laws(username: str) -> list[dict]:
    try:
        r = requests.get(
            f"{_BASE}/rest/v1/laws?username=eq.{username}",
            headers=_REST_H, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def save_law(law: dict) -> bool:
    try:
        law["username"] = _username()
        r = requests.post(f"{_BASE}/rest/v1/laws", headers=_REST_H,
                          json=law, timeout=10)
        return r.status_code in (200, 201)
    except Exception:
        return False


def delete_law(law_id: str) -> bool:
    try:
        r = requests.delete(
            f"{_BASE}/rest/v1/laws?id=eq.{requests.utils.quote(law_id, safe='')}",
            headers=_REST_H, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        return False


def update_law_stats(law_id: str, delta_trigger: int = 0,
                     delta_correct: int = 0) -> None:
    try:
        r = requests.get(
            f"{_BASE}/rest/v1/laws?id=eq.{requests.utils.quote(law_id, safe='')}"
            f"&select=triggers_count,correct_count",
            headers=_REST_H, timeout=10)
        if r.status_code == 200 and r.json():
            l = r.json()[0]
            requests.patch(
                f"{_BASE}/rest/v1/laws?id=eq.{requests.utils.quote(law_id, safe='')}",
                headers=_REST_H,
                json={
                    "triggers_count": (l.get("triggers_count") or 0) + delta_trigger,
                    "correct_count": (l.get("correct_count") or 0) + delta_correct,
                }, timeout=10)
    except Exception:
        pass
