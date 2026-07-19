"""手动比赛数据库 — CRUD"""
import requests as _req
from core.config import SUPABASE_URL, SUPABASE_KEY

_H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
       "Content-Type": "application/json"}
_B = SUPABASE_URL.rstrip("/")


def list_matches(username: str = "admin") -> list[dict]:
    try:
        r = _req.get(f"{_B}/rest/v1/match_data?username=eq.{username}&order=created_at.desc", headers=_H, timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def get_match(match_name: str) -> dict | None:
    try:
        r = _req.get(f"{_B}/rest/v1/match_data?match_name=eq.{match_name}&limit=1",
                     headers=_H, timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]
    except Exception:
        pass
    return None


def save_match(data: dict) -> bool:
    try:
        data = {k: v for k, v in data.items()
                if v is not None and v != "" and not k.startswith("_")}
        existing = get_match(data.get("match_name", ""))
        if existing:
            r = _req.patch(f"{_B}/rest/v1/match_data?id=eq.{existing['id']}",
                           headers=_H, json=data, timeout=10)
            if r.status_code not in (200, 204):
                print(f"[save_match] PATCH {r.status_code}: {r.text[:200]}")
            return r.status_code in (200, 204)
        else:
            r = _req.post(f"{_B}/rest/v1/match_data", headers=_H, json=data, timeout=10)
            if r.status_code not in (200, 201):
                print(f"[save_match] POST {r.status_code}: {r.text[:200]}")
            return r.status_code in (200, 201)
    except Exception as e:
        print(f"[save_match] EXCEPTION: {e}")
        return False


def delete_match(match_id: int) -> bool:
    try:
        r = _req.delete(f"{_B}/rest/v1/match_data?id=eq.{match_id}", headers=_H, timeout=10)
        return r.status_code in (200, 204)
    except Exception:
        return False
