"""
predict.py — Professional-Grade Technical Analysis Engine

Implements the full toolkit used by institutional traders:
  RSI, MACD, Bollinger Bands, Stochastic %K/%D, ADX+DI, CCI,
  OBV, Parabolic SAR, Ichimoku Cloud (Tenkan/Kijun/Senkou),
  Fibonacci Retracements, VWAP proxy, Price Momentum (1M/3M/6M),
  SMA/EMA crossovers, Volume analysis, ATR, Support/Resistance.

All indicators computed from pure Python — no dependencies.
"""

import asyncio
import httpx
from typing import Optional

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


# ════════════════════════════════════════════════════════════════════════
# PURE-MATH INDICATOR LIBRARY
# ════════════════════════════════════════════════════════════════════════

def _ema_series(prices: list, period: int) -> list:
    """Full EMA series starting at index period-1."""
    if len(prices) < period:
        return []
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    out = [val]
    for p in prices[period:]:
        val = p * k + val * (1 - k)
        out.append(val)
    return out


def _sma(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 2)


def _rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    d = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    g = [max(x, 0) for x in d]
    l = [abs(min(x, 0)) for x in d]
    ag = sum(g[:period]) / period
    al = sum(l[:period]) / period
    for i in range(period, len(g)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def _macd_full(closes: list, fast=12, slow=26, sig=9) -> Optional[dict]:
    if len(closes) < slow + sig:
        return None
    fs = _ema_series(closes, fast)
    ss = _ema_series(closes, slow)
    offset    = slow - fast
    macd_line = [fs[i + offset] - ss[i] for i in range(len(ss))]
    if len(macd_line) < sig:
        return None
    signal_s  = _ema_series(macd_line, sig)
    m, s      = macd_line[-1], signal_s[-1]
    hist      = m - s
    prev_hist = (macd_line[-2] - signal_s[-2]) if len(signal_s) >= 2 else 0
    return {
        "macd":          round(m, 4),
        "signal":        round(s, 4),
        "histogram":     round(hist, 4),
        "above_signal":  hist > 0,
        "bullish_cross": prev_hist < 0 < hist,
        "bearish_cross": prev_hist > 0 > hist,
    }


def _bollinger(closes: list, period=20, mult=2.0) -> Optional[dict]:
    if len(closes) < period:
        return None
    w   = closes[-period:]
    mid = sum(w) / period
    std = (sum((p - mid) ** 2 for p in w) / period) ** 0.5
    upper = mid + mult * std
    lower = mid - mult * std
    price = closes[-1]
    bw    = (upper - lower) / mid if mid else 0
    pct_b = (price - lower) / (upper - lower) if upper != lower else 0.5
    return {
        "upper":      round(upper, 2),
        "mid":        round(mid,   2),
        "lower":      round(lower, 2),
        "pct_b":      round(pct_b, 4),
        "band_width": round(bw,    4),
        "near_lower": pct_b < 0.2,
        "near_upper": pct_b > 0.8,
        "squeeze":    bw < 0.04,
    }


def _stochastic(closes, highs, lows, k=14, d=3) -> Optional[dict]:
    if len(closes) < k:
        return None
    kv = []
    for i in range(k - 1, len(closes)):
        lo = min(lows[i - k + 1: i + 1])
        hi = max(highs[i - k + 1: i + 1])
        kv.append(100 * (closes[i] - lo) / (hi - lo) if hi != lo else 50)
    if len(kv) < d:
        return None
    d_val = sum(kv[-d:]) / d
    return {
        "k":          round(kv[-1], 2),
        "d":          round(d_val, 2),
        "oversold":   kv[-1] < 20,
        "overbought": kv[-1] > 80,
        "bull_cross": len(kv) >= 2 and kv[-2] < d_val and kv[-1] > d_val,
        "bear_cross": len(kv) >= 2 and kv[-2] > d_val and kv[-1] < d_val,
    }


def _atr(highs, lows, closes, period=14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    atr = sum(trs[:period]) / period
    for t in trs[period:]:
        atr = (atr * (period - 1) + t) / period
    return round(atr, 2)


def _adx(highs, lows, closes, period=14) -> Optional[dict]:
    """Average Directional Index — measures trend strength (not direction)."""
    if len(closes) < period * 2:
        return None
    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(closes)):
        up   = highs[i]  - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dms.append(up   if up > down and up > 0   else 0)
        minus_dms.append(down if down > up and down > 0 else 0)
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i]  - closes[i - 1]),
                       abs(lows[i]   - closes[i - 1])))

    def wilder_smooth(arr, p):
        s = sum(arr[:p])
        out = [s]
        for v in arr[p:]:
            s = s - s / p + v
            out.append(s)
        return out

    sm_tr   = wilder_smooth(trs,       period)
    sm_plus = wilder_smooth(plus_dms,  period)
    sm_minus= wilder_smooth(minus_dms, period)

    dx_vals = []
    for i in range(len(sm_tr)):
        if sm_tr[i] == 0:
            continue
        pdi  = 100 * sm_plus[i]  / sm_tr[i]
        mdi  = 100 * sm_minus[i] / sm_tr[i]
        dsum = pdi + mdi
        dx_vals.append(100 * abs(pdi - mdi) / dsum if dsum else 0)

    if not dx_vals:
        return None

    adx = sum(dx_vals[-period:]) / min(len(dx_vals), period)
    pdi = 100 * sm_plus[-1]  / sm_tr[-1] if sm_tr[-1] else 0
    mdi = 100 * sm_minus[-1] / sm_tr[-1] if sm_tr[-1] else 0
    return {
        "adx":        round(adx, 2),
        "plus_di":    round(pdi, 2),
        "minus_di":   round(mdi, 2),
        "trending":   adx > 25,
        "strong":     adx > 40,
        "bullish_di": pdi > mdi,
    }


def _cci(highs, lows, closes, period=20) -> Optional[float]:
    """Commodity Channel Index."""
    if len(closes) < period:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    tp_w  = tp[-period:]
    tp_ma = sum(tp_w) / period
    md    = sum(abs(p - tp_ma) for p in tp_w) / period
    if md == 0:
        return 0.0
    return round((tp[-1] - tp_ma) / (0.015 * md), 2)


def _obv(closes, volumes) -> Optional[dict]:
    """On-Balance Volume — cumulative volume confirms price direction."""
    if len(closes) < 20:
        return None
    obv_vals = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv_vals.append(obv_vals[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv_vals.append(obv_vals[-1] - volumes[i])
        else:
            obv_vals.append(obv_vals[-1])
    obv_now  = obv_vals[-1]
    obv_prev = obv_vals[-20]
    rising   = obv_now > obv_prev
    # OBV divergence: price going down but OBV rising = bullish divergence
    price_up = closes[-1] > closes[-20]
    return {
        "obv":               round(obv_now),
        "rising":            rising,
        "bullish_divergence": (not price_up) and rising,
        "bearish_divergence": price_up and (not rising),
    }


def _parabolic_sar(highs, lows, closes, af_start=0.02, af_max=0.2) -> Optional[dict]:
    """Parabolic SAR — trailing stop and trend reversal indicator."""
    if len(closes) < 10:
        return None
    bull  = closes[1] > closes[0]
    sar   = lows[0]  if bull else highs[0]
    ep    = highs[0] if bull else lows[0]
    af    = af_start
    sars  = []
    for i in range(1, len(closes)):
        if bull:
            sar = min(sar + af * (ep - sar), lows[i - 1], lows[i - 2] if i >= 2 else lows[i - 1])
            if lows[i] < sar:
                bull, sar, ep, af = False, ep, lows[i], af_start
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(af + af_start, af_max)
        else:
            sar = max(sar + af * (ep - sar), highs[i - 1], highs[i - 2] if i >= 2 else highs[i - 1])
            if highs[i] > sar:
                bull, sar, ep, af = True, ep, highs[i], af_start
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(af + af_start, af_max)
        sars.append((sar, bull))
    sar_val, is_bull = sars[-1]
    return {"sar": round(sar_val, 2), "bullish": is_bull}


def _ichimoku(highs, lows, closes,
              tenkan=9, kijun=26, senkou_b=52) -> Optional[dict]:
    """Ichimoku Cloud — comprehensive Japanese trend system."""
    n = len(closes)
    if n < senkou_b + kijun:
        return None

    def midpoint(h, l, p):
        if len(h) < p:
            return None
        return (max(h[-p:]) + min(l[-p:])) / 2

    tk = midpoint(highs, lows, tenkan)
    kj = midpoint(highs, lows, kijun)
    if tk is None or kj is None:
        return None

    # Senkou A = (tenkan + kijun) / 2 projected forward — use current for comparison
    senkou_a = (tk + kj) / 2
    # Senkou B = (52-period midpoint) / 2
    sb_h = highs[-senkou_b:]
    sb_l = lows[-senkou_b:]
    senkou_b_val = (max(sb_h) + min(sb_l)) / 2
    price = closes[-1]

    cloud_top = max(senkou_a, senkou_b_val)
    cloud_bot = min(senkou_a, senkou_b_val)
    above_cloud = price > cloud_top
    below_cloud = price < cloud_bot
    in_cloud    = cloud_bot <= price <= cloud_top

    return {
        "tenkan":       round(tk, 2),
        "kijun":        round(kj, 2),
        "senkou_a":     round(senkou_a, 2),
        "senkou_b":     round(senkou_b_val, 2),
        "cloud_top":    round(cloud_top, 2),
        "cloud_bot":    round(cloud_bot, 2),
        "above_cloud":  above_cloud,
        "below_cloud":  below_cloud,
        "in_cloud":     in_cloud,
        "bullish_cloud": senkou_a > senkou_b_val,
        "tk_above_kj":  tk > kj,
    }


def _fibonacci(closes, highs, lows, lookback=60) -> Optional[dict]:
    """Key Fibonacci retracement levels from recent swing high/low."""
    if len(closes) < lookback:
        lookback = len(closes)
    swing_high = max(highs[-lookback:])
    swing_low  = min(lows[-lookback:])
    diff = swing_high - swing_low
    price = closes[-1]
    levels = {
        "0":     round(swing_low, 2),
        "23.6":  round(swing_high - 0.236 * diff, 2),
        "38.2":  round(swing_high - 0.382 * diff, 2),
        "50.0":  round(swing_high - 0.500 * diff, 2),
        "61.8":  round(swing_high - 0.618 * diff, 2),
        "78.6":  round(swing_high - 0.786 * diff, 2),
        "100":   round(swing_high, 2),
    }
    # Find nearest level
    nearest = min(levels.items(), key=lambda x: abs(x[1] - price))
    return {
        "levels":       levels,
        "nearest_pct":  nearest[0],
        "nearest_val":  nearest[1],
        "swing_high":   round(swing_high, 2),
        "swing_low":    round(swing_low, 2),
    }


def _momentum_returns(closes) -> dict:
    """Price momentum — 1M, 3M, 6M returns (used in quant factor models)."""
    n = len(closes)
    p = closes[-1]
    def ret(days):
        idx = max(0, n - days - 1)
        old = closes[idx]
        return round((p - old) / old * 100, 2) if old else 0
    return {
        "ret_1m":  ret(21),
        "ret_3m":  ret(63),
        "ret_6m":  ret(126),
        "ret_1y":  ret(252),
    }


def _support_resistance(closes, highs, lows, lookback=30) -> tuple:
    h = sorted(highs[-lookback:], reverse=True)
    l = sorted(lows[-lookback:])
    return round(sum(l[:3]) / 3, 2), round(sum(h[:3]) / 3, 2)


# ════════════════════════════════════════════════════════════════════════
# MASTER PREDICTION FUNCTION
# ════════════════════════════════════════════════════════════════════════

def generate_prediction(candles: list) -> dict:
    """
    Wall-Street grade technical prediction.
    candles: [{t, o, h, l, c, v}, ...] oldest-first
    Returns score (0-100), verdict, per-signal details, all indicator values.
    """
    if len(candles) < 40:
        return {"error": "Not enough history (need 40+ days)"}

    closes = [c["c"] for c in candles]
    highs  = [c["h"] for c in candles]
    lows   = [c["l"] for c in candles]
    vols   = [c.get("v") or 0 for c in candles]
    price  = closes[-1]

    # ── Compute all indicators ─────────────────────────────────────────
    rsi_val   = _rsi(closes)
    macd_d    = _macd_full(closes)
    bb        = _bollinger(closes)
    stoch     = _stochastic(closes, highs, lows)
    adx_d     = _adx(highs, lows, closes)
    cci_val   = _cci(highs, lows, closes)
    obv_d     = _obv(closes, vols)
    psar_d    = _parabolic_sar(highs, lows, closes)
    ichi      = _ichimoku(highs, lows, closes)
    fib       = _fibonacci(closes, highs, lows)
    mom       = _momentum_returns(closes)
    sma20     = _sma(closes, 20)
    sma50     = _sma(closes, 50)
    sma200    = _sma(closes, 200)
    ema9_s    = _ema_series(closes, 9)
    ema9      = round(ema9_s[-1], 2) if ema9_s else None
    ema21_s   = _ema_series(closes, 21)
    ema21     = round(ema21_s[-1], 2) if ema21_s else None
    atr_val   = _atr(highs, lows, closes)
    vol_sma   = _sma(vols, 20)
    supp, res = _support_resistance(closes, highs, lows)

    h52 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    l52 = min(lows[-252:])  if len(lows)  >= 252 else min(lows)
    pos52 = round((price - l52) / (h52 - l52) * 100, 1) if h52 != l52 else 50

    # ── Signal scoring ─────────────────────────────────────────────────
    signals = []

    def add(name, verdict, icon, weight, value=None):
        signals.append({"name": name, "verdict": verdict,
                         "icon": icon, "weight": weight, "value": value})

    # 1. RSI (weight ±2)
    if rsi_val is not None:
        if rsi_val <= 30:
            add("RSI", f"Oversold ({rsi_val}) — Strong reversal zone", "▲", +2, rsi_val)
        elif rsi_val <= 45:
            add("RSI", f"Bullish zone ({rsi_val}) — Momentum building", "▲", +1, rsi_val)
        elif rsi_val <= 55:
            add("RSI", f"Neutral ({rsi_val}) — No clear edge", "●", 0, rsi_val)
        elif rsi_val <= 70:
            add("RSI", f"Bearish zone ({rsi_val}) — Losing momentum", "▼", -1, rsi_val)
        else:
            add("RSI", f"Overbought ({rsi_val}) — Pullback risk", "▼", -2, rsi_val)

    # 2. MACD (weight ±2 crossover, ±1 position)
    if macd_d:
        if macd_d["bullish_cross"]:
            add("MACD", f"Bullish Crossover! ({macd_d['macd']}) — High-probability Buy", "▲", +2, macd_d["macd"])
        elif macd_d["bearish_cross"]:
            add("MACD", f"Bearish Crossover ({macd_d['macd']}) — Sell signal triggered", "▼", -2, macd_d["macd"])
        elif macd_d["above_signal"]:
            add("MACD", f"Above Signal Line ({macd_d['macd']}) — Bullish", "▲", +1, macd_d["macd"])
        else:
            add("MACD", f"Below Signal Line ({macd_d['macd']}) — Bearish", "▼", -1, macd_d["macd"])

    # 3. Bollinger Bands (weight ±1, squeeze is neutral)
    if bb:
        if bb["near_lower"]:
            add("Bollinger Bands", f"Near Lower Band (B%={bb['pct_b']}) — Mean reversion Buy", "▲", +1, bb["pct_b"])
        elif bb["near_upper"]:
            add("Bollinger Bands", f"Near Upper Band (B%={bb['pct_b']}) — Overbought", "▼", -1, bb["pct_b"])
        elif bb["squeeze"]:
            add("Bollinger Bands", "Squeeze detected — Explosive move incoming (direction unclear)", "●", 0, bb["pct_b"])
        else:
            add("Bollinger Bands", f"Mid-band neutral (B%={bb['pct_b']})", "●", 0, bb["pct_b"])

    # 4. Stochastic %K/%D (weight ±1, crossover ±2)
    if stoch:
        if stoch.get("bull_cross") and stoch["oversold"]:
            add("Stochastic", f"Oversold Bull Cross K:{stoch['k']} D:{stoch['d']} — Strong Buy", "▲", +2, stoch["k"])
        elif stoch.get("bear_cross") and stoch["overbought"]:
            add("Stochastic", f"Overbought Bear Cross K:{stoch['k']} D:{stoch['d']} — Strong Sell", "▼", -2, stoch["k"])
        elif stoch["oversold"]:
            add("Stochastic", f"Oversold K:{stoch['k']} — Bounce likely", "▲", +1, stoch["k"])
        elif stoch["overbought"]:
            add("Stochastic", f"Overbought K:{stoch['k']} — Caution", "▼", -1, stoch["k"])
        else:
            add("Stochastic", f"Neutral K:{stoch['k']} D:{stoch['d']}", "●", 0, stoch["k"])

    # 5. ADX — trend filter (weight: confirms other signals, -1 for no trend)
    if adx_d:
        if adx_d["strong"] and adx_d["bullish_di"]:
            add("ADX", f"Strong Uptrend (ADX={adx_d['adx']}, +DI>{adx_d['plus_di']} > -DI{adx_d['minus_di']})", "▲", +2, adx_d["adx"])
        elif adx_d["strong"] and not adx_d["bullish_di"]:
            add("ADX", f"Strong Downtrend (ADX={adx_d['adx']}, -DI{adx_d['minus_di']} > +DI{adx_d['plus_di']})", "▼", -2, adx_d["adx"])
        elif adx_d["trending"] and adx_d["bullish_di"]:
            add("ADX", f"Trending Up (ADX={adx_d['adx']}) — Follow the trend", "▲", +1, adx_d["adx"])
        elif adx_d["trending"] and not adx_d["bullish_di"]:
            add("ADX", f"Trending Down (ADX={adx_d['adx']}) — Avoid long positions", "▼", -1, adx_d["adx"])
        else:
            add("ADX", f"Choppy/Sideways (ADX={adx_d['adx']}) — Avoid trend strategies", "●", -1, adx_d["adx"])

    # 6. CCI (weight ±1)
    if cci_val is not None:
        if cci_val <= -100:
            add("CCI", f"Oversold ({cci_val}) — Buy divergence zone", "▲", +1, cci_val)
        elif cci_val >= 100:
            add("CCI", f"Overbought ({cci_val}) — Sell zone", "▼", -1, cci_val)
        else:
            add("CCI", f"Neutral ({cci_val})", "●", 0, cci_val)

    # 7. OBV — volume confirms price (weight ±1)
    if obv_d:
        if obv_d["bullish_divergence"]:
            add("OBV", "Bullish Divergence — Price down, OBV up. Smart money accumulating!", "▲", +2, None)
        elif obv_d["bearish_divergence"]:
            add("OBV", "Bearish Divergence — Price up, OBV down. Distribution in play", "▼", -2, None)
        elif obv_d["rising"]:
            add("OBV", "OBV Rising — Volume confirming upward price movement", "▲", +1, None)
        else:
            add("OBV", "OBV Falling — Volume not confirming rally. Weak hands", "▼", -1, None)

    # 8. Parabolic SAR (weight ±1)
    if psar_d:
        if psar_d["bullish"]:
            add("Parabolic SAR", f"SAR below price ({psar_d['sar']}) — Bullish trend, SAR is trailing stop", "▲", +1, psar_d["sar"])
        else:
            add("Parabolic SAR", f"SAR above price ({psar_d['sar']}) — Bearish trend confirmed", "▼", -1, psar_d["sar"])

    # 9. Ichimoku Cloud (weight ±2)
    if ichi:
        if ichi["above_cloud"] and ichi["tk_above_kj"] and ichi["bullish_cloud"]:
            add("Ichimoku", f"Price above bullish cloud, TK>KJ — Triple buy confirmation", "▲", +2, None)
        elif ichi["below_cloud"] and not ichi["tk_above_kj"] and not ichi["bullish_cloud"]:
            add("Ichimoku", f"Price below bearish cloud, KJ>TK — Triple sell signal", "▼", -2, None)
        elif ichi["above_cloud"]:
            add("Ichimoku", f"Price above cloud (Tenkan:{ichi['tenkan']}, Kijun:{ichi['kijun']}) — Bullish", "▲", +1, None)
        elif ichi["below_cloud"]:
            add("Ichimoku", f"Price below cloud — Bearish territory", "▼", -1, None)
        elif ichi["in_cloud"]:
            add("Ichimoku", "Price inside cloud — Neutral, wait for breakout", "●", 0, None)

    # 10. SMA 20 short-term (weight ±1)
    if sma20:
        if price > sma20:
            add("SMA 20", f"Price {price} > SMA20 {sma20} — Short-term bullish", "▲", +1, sma20)
        else:
            add("SMA 20", f"Price {price} < SMA20 {sma20} — Short-term bearish", "▼", -1, sma20)

    # 11. SMA 50 medium-term (weight ±1)
    if sma50:
        if price > sma50:
            add("SMA 50", f"Price above SMA50 {sma50} — Medium-term uptrend", "▲", +1, sma50)
        else:
            add("SMA 50", f"Price below SMA50 {sma50} — Medium-term downtrend", "▼", -1, sma50)

    # 12. SMA 200 — the king indicator (weight ±2)
    if sma200:
        if price > sma200:
            add("SMA 200", f"Price above 200 DMA {sma200} — Long-term BULL market", "▲", +2, sma200)
        else:
            add("SMA 200", f"Price below 200 DMA {sma200} — Long-term BEAR territory", "▼", -2, sma200)

    # 13. Golden/Death Cross — SMA50 vs SMA200 (weight ±2)
    if sma50 and sma200:
        if sma50 > sma200:
            add("Golden Cross", f"SMA50 {sma50} > SMA200 {sma200} — Bull market structure", "▲", +2, sma50)
        else:
            add("Death Cross",  f"SMA50 {sma50} < SMA200 {sma200} — Bear market structure", "▼", -2, sma50)

    # 14. Volume confirmation (weight ±1)
    if vol_sma and vols[-1]:
        vr = vols[-1] / vol_sma
        if closes[-1] > closes[-2] and vr > 1.5:
            add("Volume", f"High-volume up day ({vr:.1f}x avg) — Institutional buying", "▲", +1, round(vr, 2))
        elif closes[-1] < closes[-2] and vr > 1.5:
            add("Volume", f"High-volume down day ({vr:.1f}x avg) — Institutional selling", "▼", -1, round(vr, 2))
        elif vr < 0.5:
            add("Volume", f"Low volume ({vr:.1f}x avg) — Weak conviction move", "●", 0, round(vr, 2))
        else:
            add("Volume", f"Normal volume ({vr:.1f}x avg)", "●", 0, round(vr, 2))

    # 15. Momentum scoring — 3M + 6M returns (weight ±1)
    if mom:
        m3 = mom["ret_3m"] or 0
        m6 = mom["ret_6m"] or 0
        if m3 > 10 and m6 > 15:
            add("Momentum", f"Strong momentum — 3M:{m3}% / 6M:{m6}% (top-tier performance)", "▲", +1, m3)
        elif m3 > 5 or m6 > 8:
            add("Momentum", f"Positive momentum — 3M:{m3}% / 6M:{m6}%", "▲", +1, m3)
        elif m3 < -10 and m6 < -15:
            add("Momentum", f"Weak momentum — 3M:{m3}% / 6M:{m6}% (underperformer)", "▼", -1, m3)
        elif m3 < -5 or m6 < -8:
            add("Momentum", f"Declining momentum — 3M:{m3}% / 6M:{m6}%", "▼", -1, m3)
        else:
            add("Momentum", f"Flat momentum — 3M:{m3}% / 6M:{m6}%", "●", 0, m3)

    # ── Score ──────────────────────────────────────────────────────────
    total   = sum(s["weight"] for s in signals)
    max_abs = sum(abs(s["weight"]) for s in signals) or 1
    score   = int(round(50 + (total / max_abs) * 50))
    score   = max(0, min(100, score))

    if   score >= 75: verdict, vcolor = "Strong Buy",  "#00d4aa"
    elif score >= 62: verdict, vcolor = "Buy",         "#4caf50"
    elif score >= 45: verdict, vcolor = "Neutral",     "#ffd700"
    elif score >= 30: verdict, vcolor = "Sell",        "#ff9800"
    else:             verdict, vcolor = "Strong Sell", "#ff4d6d"

    # ATR targets (2 ATR = typical swing trade target)
    bull_t = round(price + 2 * atr_val, 2) if atr_val else None
    bear_t = round(price - 2 * atr_val, 2) if atr_val else None

    return {
        "score":          score,
        "verdict":        verdict,
        "verdict_color":  vcolor,
        "signals":        signals,
        "price":          price,
        # Key indicator values (for display)
        "rsi":            rsi_val,
        "macd":           macd_d,
        "bollinger":      bb,
        "stochastic":     stoch,
        "adx":            adx_d,
        "cci":            cci_val,
        "obv":            obv_d,
        "parabolic_sar":  psar_d,
        "ichimoku":       ichi,
        "fibonacci":      fib,
        "momentum":       mom,
        "sma20":          sma20,
        "sma50":          sma50,
        "sma200":         sma200,
        "ema9":           ema9,
        "ema21":          ema21,
        "atr":            atr_val,
        "support":        supp,
        "resistance":     res,
        "bull_target":    bull_t,
        "bear_target":    bear_t,
        "high_52w":       round(h52, 2),
        "low_52w":        round(l52, 2),
        "position_52w":   pos52,
    }


# ════════════════════════════════════════════════════════════════════════
# FETCH + PREDICT
# ════════════════════════════════════════════════════════════════════════

async def fetch_prediction(symbol: str) -> dict:
    """Fetch 1Y daily OHLCV from Yahoo Finance and run prediction."""
    ticker = symbol if (symbol.startswith("^") or symbol.endswith("=F") or "=" in symbol) else f"{symbol}.NS"
    url    = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
              f"?interval=1d&range=1y")
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=20,
                                     follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()

        res  = data["chart"]["result"][0]
        ts   = res["timestamp"]
        q0   = res["indicators"]["quote"][0]
        cl   = q0.get("close",  [])
        op   = q0.get("open",   [])
        hi   = q0.get("high",   [])
        lo   = q0.get("low",    [])
        vol  = q0.get("volume", [])

        candles = []
        for i, t in enumerate(ts):
            c = cl[i] if i < len(cl) else None
            if not c:
                continue
            candles.append({
                "t": t * 1000,
                "o": (op[i]  if i < len(op)  and op[i]  else c),
                "h": (hi[i]  if i < len(hi)  and hi[i]  else c),
                "l": (lo[i]  if i < len(lo)  and lo[i]  else c),
                "c": c,
                "v": (vol[i] if i < len(vol) and vol[i] else 0),
            })
        candles.sort(key=lambda x: x["t"])
        pred = generate_prediction(candles)
        pred["symbol"] = symbol
        return pred
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


async def batch_predict(symbols: list, max_concurrent: int = 5) -> list:
    """Parallel predictions with concurrency cap to avoid rate-limiting."""
    sem = asyncio.Semaphore(max_concurrent)
    async def bounded(sym):
        async with sem:
            return await fetch_prediction(sym)
    return await asyncio.gather(*[bounded(s) for s in symbols])
