import re
from datetime import date


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def safe_float(value, fallback: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except Exception:
        return fallback


def get_today() -> str:
    return date.today().isoformat()


def calc_pnl(invested: float, current: float) -> dict:
    if invested == 0:
        return {"pnl": 0.0, "pnl_pct": 0.0, "direction": "up"}
    pnl = round(current - invested, 2)
    pnl_pct = round((pnl / invested) * 100, 2)
    return {"pnl": pnl, "pnl_pct": pnl_pct,
            "direction": "up" if pnl >= 0 else "down"}
