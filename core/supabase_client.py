from supabase import create_client, Client

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        from core.config import SUPABASE_URL, SUPABASE_KEY
        if not SUPABASE_URL:
            raise RuntimeError("SUPABASE_URL 未配置，请在 Streamlit Secrets 中设置")
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase
