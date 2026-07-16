"""认证：邮箱/昵称登录"""
import requests
import streamlit as st
from core.config import SUPABASE_URL, SUPABASE_KEY
from core.supabase_client import get_supabase


_AUTH_H = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _resolve_username(access_token: str) -> str | None:
    """从 access_token 解析用户名"""
    try:
        h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {access_token}"}
        uid_resp = requests.get(f"{SUPABASE_URL}/auth/v1/user", headers=h, timeout=10)
        if uid_resp.status_code != 200:
            return None
        uid = uid_resp.json().get("id", "")
        pr = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles?select=username&id=eq.{uid}",
            headers=h, timeout=10)
        if pr.status_code == 200 and pr.json():
            return pr.json()[0]["username"]
    except Exception:
        pass
    return None


def _set_session(at: str, rt: str, un: str, em: str) -> None:
    st.session_state.update(
        logged_in=True, username=un, email=em,
        access_token=at, refresh_token=rt,
        saved_username=un, saved_email=em,
        auth_user_id=None,
    )


def login_user(login_id: str, password: str) -> bool:
    """邮箱或昵称登录"""
    try:
        email = login_id
        if "@" not in login_id:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/profiles"
                f"?select=email&username=eq.{login_id}",
                headers=_AUTH_H, timeout=10)
            if r.status_code == 200 and r.json():
                email = r.json()[0]["email"]
            else:
                st.error("未找到该用户")
                return False

        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers=_AUTH_H,
            json={"email": email, "password": password}, timeout=10)
        if r.status_code != 200:
            st.error("邮箱或密码错误")
            return False

        d = r.json()
        at = d["access_token"]
        un = _resolve_username(at) or login_id.split("@")[0]
        _set_session(at, d.get("refresh_token", ""), un, email)
        return True
    except Exception as e:
        st.error(f"登录失败: {e}")
        return False


def register_user(email: str, password: str, username: str) -> tuple[bool, str]:
    """注册新用户"""
    try:
        # 检查用户名
        chk = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles?select=id&username=eq.{username}",
            headers=_AUTH_H, timeout=10)
        if chk.status_code == 200 and chk.json():
            return False, "用户名已存在"

        # Admin API 创建用户（唯一需要 service_role key 的地方）
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=_AUTH_H,
            json={"email": email, "password": password, "email_confirm": True},
            timeout=10)
        if r.status_code not in (200, 201):
            msg = r.json().get("msg", "") or r.text[:80]
            return False, f"注册失败: {msg}"

        uid = r.json()["id"]
        requests.post(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=_AUTH_H,
            json={"id": uid, "username": username, "email": email},
            timeout=10)

        # 初始化配额
        from core.quota import init_quota
        init_quota(username)

        # 从 admin 复制定律
        laws_r = requests.get(
            f"{SUPABASE_URL}/rest/v1/laws?username=eq.admin",
            headers=_AUTH_H, timeout=10)
        if laws_r.status_code == 200:
            for law in laws_r.json():
                law["username"] = username
                law["id"] = f"{username}_{law.get('id', '')}"
                requests.post(
                    f"{SUPABASE_URL}/rest/v1/laws",
                    headers=_AUTH_H, json=law, timeout=10)

        return True, "注册成功！请登录。"
    except Exception as e:
        return False, f"注册失败: {e}"


def _update_auth() -> None:
    """同步 token 到 supabase client"""
    at = st.session_state.get("access_token")
    if at:
        try:
            get_supabase().auth.set_session(at, st.session_state.get("refresh_token", ""))
        except Exception:
            pass


def restore_login() -> bool:
    """从 query_params 或 session 恢复登录状态"""
    if st.session_state.get("logged_in"):
        return True
    try:
        at = (st.session_state.get("access_token")
              or st.query_params.get("at", ""))
        rt = (st.session_state.get("refresh_token")
              or st.query_params.get("rt", ""))
        un = (st.session_state.get("saved_username")
              or st.query_params.get("un", ""))
        em = (st.session_state.get("saved_email")
              or st.query_params.get("em", ""))
        if not at:
            return False
        un2 = _resolve_username(at)
        if un2:
            _set_session(at, rt, un2, em or "")
            return True
        if un and em:
            _set_session(at, rt, un, em)
            return True
    except Exception:
        pass
    return False


def logout() -> None:
    try:
        get_supabase().auth.sign_out()
    except Exception:
        pass
    for k in ("logged_in", "username", "email", "access_token",
              "refresh_token", "auth_user_id"):
        st.session_state[k] = "" if k != "logged_in" else False
    st.query_params.clear()
    st.rerun()
