"""配额管理"""
import requests
from datetime import date
import streamlit as st
from core.config import SUPABASE_URL, SUPABASE_KEY
from core.supabase_client import get_supabase


_H = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
_B = SUPABASE_URL.rstrip("/")

TIERS = {
    "free": {"label": "免费版", "daily_limit": 1, "price": 0},
    "paid": {"label": "会员", "daily_limit": 5, "price": 19},
}


def _ensure_quota(username: str) -> dict:
    try:
        data = get_supabase().table("user_quotas").select("*")\
            .eq("username", username).execute()
        if data.data:
            return data.data[0]

        init = {
            "username": username,
            "tier": "free",
            "daily_limit": TIERS["free"]["daily_limit"],
            "used_today": 0,
            "last_reset_date": str(date.today()),
            "bonus_quota": 0,
        }
        get_supabase().table("user_quotas").insert(init).execute()
        return init
    except Exception:
        return {"tier": "free", "daily_limit": 1, "used_today": 0,
                "bonus_quota": 0, "last_reset_date": str(date.today())}


def _check_reset(data: dict) -> dict:
    today = str(date.today())
    if data.get("last_reset_date") != today:
        data["used_today"] = 0
        data["last_reset_date"] = today
        try:
            get_supabase().table("user_quotas").update(
                {"used_today": 0, "last_reset_date": today}
            ).eq("username", data["username"]).execute()
        except Exception:
            pass
    return data


def get_quota(username: str) -> dict:
    d = _check_reset(_ensure_quota(username))
    return {
        "tier": d.get("tier", "free"),
        "daily_limit": d.get("daily_limit", 1),
        "used_today": d.get("used_today", 0),
        "bonus_quota": d.get("bonus_quota", 0),
        "remaining": (d.get("daily_limit", 1) + d.get("bonus_quota", 0)
                      - d.get("used_today", 0)),
    }


def can_predict(username: str) -> tuple[bool, str]:
    q = get_quota(username)
    r = q["remaining"]
    if r > 0:
        return True, f"今日剩余 {r} 次"
    return False, f"今日次数已用完 ({q['tier']} {q['daily_limit']}次)"


def deduct_quota(username: str) -> None:
    d = _check_reset(_ensure_quota(username))
    new_used = d.get("used_today", 0) + 1
    try:
        get_supabase().table("user_quotas").update({"used_today": new_used})\
            .eq("username", username).execute()
    except Exception:
        pass


def add_bonus(username: str, amount: int = 1) -> None:
    d = _ensure_quota(username)
    new_bonus = d.get("bonus_quota", 0) + amount
    try:
        get_supabase().table("user_quotas").update({"bonus_quota": new_bonus})\
            .eq("username", username).execute()
    except Exception:
        pass


def init_quota(username: str) -> None:
    _ensure_quota(username)


def upgrade_tier(username: str, tier: str = "paid") -> bool:
    dl = TIERS.get(tier, TIERS["free"])["daily_limit"]
    try:
        get_supabase().table("user_quotas").update(
            {"tier": tier, "daily_limit": dl, "used_today": 0}
        ).eq("username", username).execute()
        return True
    except Exception:
        return False
