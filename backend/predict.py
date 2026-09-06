"""
predict.py — Technical Analysis & Trade-Planning Engine (v4)

Pure-Python (no numpy) implementation of the indicator toolkit a
discretionary swing/position trader actually uses, organised the way a
bull thinks: TREND → MOMENTUM → MONEY FLOW → RELATIVE STRENGTH → SETUP.

Indicators
  Trend      : SMA 20/50/150/200 (+200 slope), EMA ribbon 8/13/21/34/55,
               Supertrend(10,3), Ichimoku (properly displaced cloud + Chikou),
               ADX/DI, Parabolic SAR, Golden/Death cross
  Momentum   : RSI(14) + divergence, MACD(12,26,9), Stochastic(14,3) with a
               real %D series, CCI(20), Williams %R(14), ROC(20), Aroon(25),
               TTM Squeeze (Bollinger inside Keltner) + momentum, 1M/3M/6M/1Y
  Money flow : OBV + divergence, MFI(14), Chaikin Money Flow(20), rolling
               VWAP(20), accumulation/distribution day count, volume climax
  Rel. str.  : 1M/3M excess return vs Nifty 50, RS-line slope, beta, corr.
  Setup      : Donchian 20/55 breakouts, 52-week position & proximity,
               pullback-to-EMA21 in uptrend, higher-high / higher-low
               market structure, Minervini trend template (8 rules),
               volatility contraction, Larry Williams "Blast Off" coil,
               candlestick patterns (engulfing, hammer, doji, inside bar)

Outputs
  score (0-100) built from weighted categories, verdict, setup label,
  a concrete TRADE PLAN (entry / stop / T1 / T2 / R:R / position size) and a
  FORECAST built from the stock's own history: at ~45 sample points over the
  last year we compute the same score and the forward 21-day return, then
  report the average outcome for days that looked like today.

Everything degrades gracefully — every indicator returns None when there is
not enough data and the scorer simply skips it.
"""

import asyncio
import math
import time
from datetime import datetime
from typing import Optional

import httpx

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ════════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ════════════════════════════════════════════════════════════════════════

def _r(v, n=2):
    """Round that tolerates None / NaN."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return round(v, n)
    except Exception:
        return None


def _sma(vals: list, period: int) -> Optional[float]:
    if len(vals) < period or period <= 0:
        return None
    return sum(vals[-period:]) / period


def _sma_series(vals: list, period: int) -> list:
    """Same length as vals, None until enough data."""
    out = [None] * len(vals)
    if len(vals) < period:
        return out
    s = sum(vals[:period])
    out[period - 1] = s / period
    for i in range(period, len(vals)):
        s += vals[i] - vals[i - period]
        out[i] = s / period
    return out


def _ema_full(vals: list, period: int) -> list:
    """EMA series, same length as vals, None until period-1."""
    out = [None] * len(vals)
    if len(vals) < period:
        return out
    k = 2.0 / (period + 1)
    e = sum(vals[:period]) / period
    out[period - 1] = e
    for i in range(period, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def _std(vals: list) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return (sum((v - m) ** 2 for v in vals) / n) ** 0.5


def _linreg_slope(vals: list) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    xm = (n - 1) / 2
    ym = sum(vals) / n
    num = sum((i - xm) * (v - ym) for i, v in enumerate(vals))
    den = sum((i - xm) ** 2 for i in range(n))
    return num / den if den else 0.0


def _tr_series(highs, lows, closes) -> list:
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    return trs


def _atr_series(highs, lows, closes, period=14) -> list:
    """Wilder ATR series (same length, None until period)."""
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out
    trs = _tr_series(highs, lows, closes)
    atr = sum(trs[1:period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def _pivots(vals: list, left=5, right=5, mode="low") -> list:
    """Indexes of swing lows/highs (bar is extreme of its window)."""
    out = []
    for i in range(left, len(vals) - right):
        w = vals[i - left:i + right + 1]
        if mode == "low" and vals[i] == min(w):
            out.append(i)
        elif mode == "high" and vals[i] == max(w):
            out.append(i)
    return out


# ════════════════════════════════════════════════════════════════════════
# INDICATORS
# ════════════════════════════════════════════════════════════════════════

def _rsi_series(closes: list, period: int = 14) -> list:
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _macd(closes, fast=12, slow=26, sig=9) -> Optional[dict]:
    if len(closes) < slow + sig + 5:
        return None
    ef, es = _ema_full(closes, fast), _ema_full(closes, slow)
    macd_line = [ef[i] - es[i] if ef[i] is not None and es[i] is not None else None
                 for i in range(len(closes))]
    valid = [m for m in macd_line if m is not None]
    sig_s = _ema_full(valid, sig)
    if sig_s[-1] is None or sig_s[-2] is None:
        return None
    m, s = valid[-1], sig_s[-1]
    hist = m - s
    prev_hist = valid[-2] - sig_s[-2]
    prev2_hist = (valid[-3] - sig_s[-3]) if sig_s[-3] is not None else prev_hist
    return {
        "macd": _r(m, 4), "signal": _r(s, 4), "histogram": _r(hist, 4),
        "above_signal": hist > 0,
        "above_zero": m > 0,
        "hist_rising": hist > prev_hist > prev2_hist,
        "bullish_cross": prev_hist <= 0 < hist,
        "bearish_cross": prev_hist >= 0 > hist,
    }


def _bollinger(closes, period=20, mult=2.0) -> Optional[dict]:
    if len(closes) < period + 5:
        return None
    w = closes[-period:]
    mid = sum(w) / period
    sd = _std(w)
    upper, lower = mid + mult * sd, mid - mult * sd
    price = closes[-1]
    bw = (upper - lower) / mid if mid else 0
    pct_b = (price - lower) / (upper - lower) if upper != lower else 0.5
    # bandwidth percentile over the last 120 bars → squeeze detection
    bws = []
    for j in range(max(period, len(closes) - 120), len(closes) + 1):
        ww = closes[j - period:j]
        mm = sum(ww) / period
        bws.append(2 * mult * _std(ww) / mm if mm else 0)
    rank = sum(1 for b in bws if b <= bw) / len(bws) if bws else 0.5
    return {
        "upper": _r(upper), "mid": _r(mid), "lower": _r(lower),
        "pct_b": _r(pct_b, 4), "band_width": _r(bw, 4),
        "bw_percentile": _r(rank * 100, 1),
        "near_lower": pct_b < 0.2, "near_upper": pct_b > 0.8,
        "squeeze": rank <= 0.15,
    }


def _stochastic(closes, highs, lows, k=14, d=3) -> Optional[dict]:
    if len(closes) < k + d + 2:
        return None
    ks = []
    for i in range(k - 1, len(closes)):
        lo = min(lows[i - k + 1:i + 1]); hi = max(highs[i - k + 1:i + 1])
        ks.append(100 * (closes[i] - lo) / (hi - lo) if hi != lo else 50.0)
    ds = _sma_series(ks, d)
    kk, dd = ks[-1], ds[-1]
    pk, pd = ks[-2], ds[-2]
    return {
        "k": _r(kk), "d": _r(dd),
        "oversold": kk < 20, "overbought": kk > 80,
        "bull_cross": pk <= pd and kk > dd,
        "bear_cross": pk >= pd and kk < dd,
    }


def _adx(highs, lows, closes, period=14) -> Optional[dict]:
    n = len(closes)
    if n < period * 3:
        return None
    trs, pdm, mdm = [], [], []
    for i in range(1, n):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    # Wilder smoothing
    s_tr, s_p, s_m = sum(trs[:period]), sum(pdm[:period]), sum(mdm[:period])
    dxs = []
    pdi = mdi = 0.0
    for i in range(period, len(trs)):
        s_tr = s_tr - s_tr / period + trs[i]
        s_p = s_p - s_p / period + pdm[i]
        s_m = s_m - s_m / period + mdm[i]
        pdi = 100 * s_p / s_tr if s_tr else 0
        mdi = 100 * s_m / s_tr if s_tr else 0
        dsum = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / dsum if dsum else 0)
    if len(dxs) < period:
        return None
    adx = sum(dxs[:period]) / period
    adx_hist = [adx]
    for v in dxs[period:]:
        adx = (adx * (period - 1) + v) / period
        adx_hist.append(adx)
    return {
        "adx": _r(adx), "plus_di": _r(pdi), "minus_di": _r(mdi),
        "trending": adx > 25, "strong": adx > 40,
        "bullish_di": pdi > mdi,
        "rising": len(adx_hist) > 5 and adx > adx_hist[-6],
    }


def _cci(highs, lows, closes, period=20) -> Optional[float]:
    if len(closes) < period:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes) - period, len(closes))]
    m = sum(tp) / period
    md = sum(abs(p - m) for p in tp) / period
    return _r((tp[-1] - m) / (0.015 * md)) if md else 0.0


def _williams_r(highs, lows, closes, period=14) -> Optional[float]:
    if len(closes) < period:
        return None
    hh, ll = max(highs[-period:]), min(lows[-period:])
    return _r((hh - closes[-1]) / (hh - ll) * -100) if hh != ll else -50.0


def _roc(closes, period=20) -> Optional[float]:
    if len(closes) < period + 1 or not closes[-period - 1]:
        return None
    return _r((closes[-1] / closes[-period - 1] - 1) * 100)


def _aroon(highs, lows, period=25) -> Optional[dict]:
    if len(highs) < period + 1:
        return None
    hw, lw = highs[-period - 1:], lows[-period - 1:]
    since_hi = period - hw.index(max(hw))
    since_lo = period - lw.index(min(lw))
    up = (period - since_hi) / period * 100
    dn = (period - since_lo) / period * 100
    return {"up": _r(up), "down": _r(dn), "bullish": up > 70 and dn < 30,
            "bearish": dn > 70 and up < 30, "osc": _r(up - dn)}


def _obv(closes, vols) -> Optional[dict]:
    if len(closes) < 25 or not any(vols[-20:]):
        return None
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - vols[i])
        else:
            obv.append(obv[-1])
    rising = obv[-1] > obv[-20]
    price_up = closes[-1] > closes[-20]
    slope = _linreg_slope(obv[-20:])
    return {"obv": round(obv[-1]), "rising": rising, "slope_up": slope > 0,
            "bullish_divergence": (not price_up) and rising,
            "bearish_divergence": price_up and (not rising)}


def _mfi(highs, lows, closes, vols, period=14) -> Optional[float]:
    if len(closes) < period + 1 or not any(vols[-period:]):
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    pos = neg = 0.0
    for i in range(len(closes) - period, len(closes)):
        mf = tp[i] * vols[i]
        if tp[i] > tp[i - 1]:
            pos += mf
        elif tp[i] < tp[i - 1]:
            neg += mf
    if neg == 0:
        return 100.0
    return _r(100 - 100 / (1 + pos / neg))


def _cmf(highs, lows, closes, vols, period=20) -> Optional[float]:
    if len(closes) < period or not any(vols[-period:]):
        return None
    num = den = 0.0
    for i in range(len(closes) - period, len(closes)):
        rng = highs[i] - lows[i]
        mult = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / rng if rng else 0
        num += mult * vols[i]
        den += vols[i]
    return _r(num / den, 3) if den else None


def _vwap(highs, lows, closes, vols, period=20) -> Optional[float]:
    if len(closes) < period or not any(vols[-period:]):
        return None
    num = den = 0.0
    for i in range(len(closes) - period, len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        num += tp * vols[i]
        den += vols[i]
    return _r(num / den) if den else None


def _psar(highs, lows, closes, af0=0.02, af_max=0.2) -> Optional[dict]:
    n = len(closes)
    if n < 10:
        return None
    bull = closes[1] > closes[0]
    sar = lows[0] if bull else highs[0]
    ep = highs[0] if bull else lows[0]
    af = af0
    bars_since_flip = 0
    for i in range(1, n):
        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)
        if bull:
            sar = min(sar, lows[i - 1], lows[i - 2] if i >= 2 else lows[i - 1])
            if lows[i] < sar:
                bull, sar, ep, af = False, ep, lows[i], af0
                bars_since_flip = 0
            else:
                bars_since_flip += 1
                if highs[i] > ep:
                    ep, af = highs[i], min(af + af0, af_max)
        else:
            sar = max(sar, highs[i - 1], highs[i - 2] if i >= 2 else highs[i - 1])
            if highs[i] > sar:
                bull, sar, ep, af = True, ep, highs[i], af0
                bars_since_flip = 0
            else:
                bars_since_flip += 1
                if lows[i] < ep:
                    ep, af = lows[i], min(af + af0, af_max)
    return {"sar": _r(sar), "bullish": bull, "bars_since_flip": bars_since_flip}


def _supertrend(highs, lows, closes, period=10, mult=3.0) -> Optional[dict]:
    n = len(closes)
    atr = _atr_series(highs, lows, closes, period)
    if n < period + 5 or atr[period] is None:
        return None
    f_up = f_dn = None
    trend_up = True
    line = None
    flips = []
    for i in range(period, n):
        hl2 = (highs[i] + lows[i]) / 2
        up = hl2 + mult * atr[i]
        dn = hl2 - mult * atr[i]
        if f_up is None:
            f_up, f_dn = up, dn
            line = dn
            continue
        f_up_new = up if (up < f_up or closes[i - 1] > f_up) else f_up
        f_dn_new = dn if (dn > f_dn or closes[i - 1] < f_dn) else f_dn
        if trend_up:
            if closes[i] < f_dn_new:
                trend_up = False; flips.append(i)
        else:
            if closes[i] > f_up_new:
                trend_up = True; flips.append(i)
        f_up, f_dn = f_up_new, f_dn_new
        line = f_dn if trend_up else f_up
    bars_since = (n - 1 - flips[-1]) if flips else n
    return {"line": _r(line), "bullish": trend_up, "bars_since_flip": bars_since,
            "fresh_flip": bars_since <= 3}


def _keltner_squeeze(highs, lows, closes, period=20, kc_mult=1.5, bb_mult=2.0) -> Optional[dict]:
    """TTM-style squeeze: Bollinger bands inside Keltner channel."""
    n = len(closes)
    if n < period + 10:
        return None
    atr = _atr_series(highs, lows, closes, period)
    ema = _ema_full(closes, period)
    sq_hist = []
    for i in range(n - 8, n):
        w = closes[i - period + 1:i + 1]
        mid = sum(w) / period
        sd = _std(w)
        bb_u, bb_l = mid + bb_mult * sd, mid - bb_mult * sd
        if ema[i] is None or atr[i] is None:
            sq_hist.append(False); continue
        kc_u, kc_l = ema[i] + kc_mult * atr[i], ema[i] - kc_mult * atr[i]
        sq_hist.append(bb_u < kc_u and bb_l > kc_l)
    # momentum: close minus midpoint of (Donchian mid + SMA), linreg over period
    moms = []
    for i in range(n - period, n):
        w_h, w_l, w_c = highs[i - period + 1:i + 1], lows[i - period + 1:i + 1], closes[i - period + 1:i + 1]
        mid = ((max(w_h) + min(w_l)) / 2 + sum(w_c) / period) / 2
        moms.append(closes[i] - mid)
    mom = _linreg_slope(moms) * period + (sum(moms) / len(moms))
    prev_mom = _linreg_slope(moms[:-1]) * (period - 1) + (sum(moms[:-1]) / max(1, len(moms) - 1))
    squeezing = sq_hist[-1]
    was_squeezing = any(sq_hist[:-1])
    return {
        "squeezing": squeezing,
        "fired": (not squeezing) and was_squeezing,
        "fired_bullish": (not squeezing) and was_squeezing and mom > 0,
        "momentum": _r(mom, 3),
        "mom_rising": mom > prev_mom,
        "squeeze_bars": sum(sq_hist),
    }


def _ichimoku(highs, lows, closes, tenkan=9, kijun=26, senkou=52) -> Optional[dict]:
    n = len(closes)
    if n < senkou + kijun + 2:
        return None

    def mid(h, l, end, p):
        return (max(h[end - p:end]) + min(l[end - p:end])) / 2

    tk = mid(highs, lows, n, tenkan)
    kj = mid(highs, lows, n, kijun)
    # Cloud that applies to TODAY was computed `kijun` bars ago
    past = n - kijun
    sa_now = (mid(highs, lows, past, tenkan) + mid(highs, lows, past, kijun)) / 2
    sb_now = mid(highs, lows, past, senkou)
    # Cloud being drawn ahead (future) from today's values
    sa_fut = (tk + kj) / 2
    sb_fut = mid(highs, lows, n, senkou)
    price = closes[-1]
    top, bot = max(sa_now, sb_now), min(sa_now, sb_now)
    chikou_bull = price > closes[-kijun - 1]
    return {
        "tenkan": _r(tk), "kijun": _r(kj),
        "senkou_a": _r(sa_now), "senkou_b": _r(sb_now),
        "cloud_top": _r(top), "cloud_bot": _r(bot),
        "above_cloud": price > top, "below_cloud": price < bot,
        "in_cloud": bot <= price <= top,
        "bullish_cloud": sa_now > sb_now,
        "future_bullish": sa_fut > sb_fut,
        "tk_above_kj": tk > kj,
        "chikou_bullish": chikou_bull,
        "price_above_kijun": price > kj,
    }


def _fibonacci(closes, highs, lows, lookback=60) -> Optional[dict]:
    lb = min(lookback, len(closes))
    sh, sl = max(highs[-lb:]), min(lows[-lb:])
    diff = sh - sl
    price = closes[-1]
    levels = {
        "0": _r(sl), "23.6": _r(sh - 0.236 * diff), "38.2": _r(sh - 0.382 * diff),
        "50.0": _r(sh - 0.5 * diff), "61.8": _r(sh - 0.618 * diff),
        "78.6": _r(sh - 0.786 * diff), "100": _r(sh),
    }
    nearest = min(levels.items(), key=lambda x: abs(x[1] - price))
    return {"levels": levels, "nearest_pct": nearest[0], "nearest_val": nearest[1],
            "swing_high": _r(sh), "swing_low": _r(sl),
            "retrace_pct": _r((sh - price) / diff * 100, 1) if diff else None}


def _momentum_returns(closes) -> dict:
    n = len(closes); p = closes[-1]

    def ret(days):
        idx = n - days - 1
        if idx < 0:
            return None
        old = closes[idx]
        return _r((p - old) / old * 100) if old else None
    return {"ret_1w": ret(5), "ret_1m": ret(21), "ret_3m": ret(63),
            "ret_6m": ret(126), "ret_1y": ret(252)}


def _donchian(highs, lows, closes) -> Optional[dict]:
    if len(closes) < 56:
        return None
    price = closes[-1]
    h20, l20 = max(highs[-21:-1]), min(lows[-21:-1])
    h55, l55 = max(highs[-56:-1]), min(lows[-56:-1])
    return {
        "high20": _r(h20), "low20": _r(l20), "high55": _r(h55), "low55": _r(l55),
        "breakout20": price > h20, "breakout55": price > h55,
        "breakdown20": price < l20, "breakdown55": price < l55,
        "dist_to_high20_pct": _r((h20 - price) / price * 100),
    }


def _structure(highs, lows, closes, lookback=120) -> Optional[dict]:
    """Higher-high / higher-low market structure from 5-bar pivots."""
    if len(closes) < 60:
        return None
    h = highs[-lookback:]; l = lows[-lookback:]
    ph = _pivots(h, 5, 5, "high"); pl = _pivots(l, 5, 5, "low")
    if len(ph) < 2 or len(pl) < 2:
        return None
    hh = h[ph[-1]] > h[ph[-2]]
    hl = l[pl[-1]] > l[pl[-2]]
    label = ("Uptrend (HH+HL)" if hh and hl else
             "Downtrend (LH+LL)" if (not hh and not hl) else
             "Transition (HL only)" if hl else "Transition (HH only)")
    return {"higher_high": hh, "higher_low": hl, "label": label,
            "last_swing_high": _r(h[ph[-1]]), "last_swing_low": _r(l[pl[-1]]),
            "uptrend": hh and hl, "downtrend": (not hh) and (not hl)}


def _rsi_divergence(closes, rsi_s, lookback=60) -> dict:
    """Bullish: price lower-low but RSI higher-low. Bearish: the reverse."""
    out = {"bullish": False, "bearish": False}
    if len(closes) < lookback + 10:
        return out
    c = closes[-lookback:]; r = rsi_s[-lookback:]
    if any(v is None for v in r):
        return out
    pl = _pivots(c, 4, 4, "low")
    if len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        if c[b] < c[a] and r[b] > r[a] + 2 and (len(c) - 1 - b) <= 15:
            out["bullish"] = True
    ph = _pivots(c, 4, 4, "high")
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        if c[b] > c[a] and r[b] < r[a] - 2 and (len(c) - 1 - b) <= 15:
            out["bearish"] = True
    return out


def _patterns(opens, highs, lows, closes) -> list:
    """Simple but reliable candlestick / bar patterns on the last two bars."""
    if len(closes) < 3:
        return []
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    po, ph, pl, pc = opens[-2], highs[-2], lows[-2], closes[-2]
    rng = h - l or 1e-9
    body = abs(c - o)
    upper = h - max(o, c); lower = min(o, c) - l
    found = []
    if c > o and pc < po and c >= po and o <= pc and body > abs(pc - po):
        found.append({"name": "Bullish Engulfing", "bias": 1})
    if c < o and pc > po and c <= po and o >= pc and body > abs(pc - po):
        found.append({"name": "Bearish Engulfing", "bias": -1})
    if lower > 2 * body and upper < body and body / rng < 0.35:
        found.append({"name": "Hammer / Pin bar", "bias": 1})
    if upper > 2 * body and lower < body and body / rng < 0.35:
        found.append({"name": "Shooting Star", "bias": -1})
    if body / rng < 0.1:
        found.append({"name": "Doji (indecision)", "bias": 0})
    if h < ph and l > pl:
        found.append({"name": "Inside Bar (coil)", "bias": 0})
    if len(closes) >= 4 and all(closes[-i] > closes[-i - 1] for i in range(1, 4)) and \
            all(closes[-i] > opens[-i] for i in range(1, 4)):
        found.append({"name": "Three White Soldiers", "bias": 1})
    return found


def _blast_off(opens, highs, lows, closes) -> Optional[dict]:
    """Larry Williams' Blast-Off: tiny body vs range = coiled spring."""
    if len(closes) < 2:
        return None
    rng = highs[-1] - lows[-1]
    if rng <= 0:
        return None
    val = abs(closes[-1] - opens[-1]) / rng * 100
    return {"value": _r(val, 1), "coiled": val < 20}


def _trend_template(closes, highs, lows, sma50, sma150, sma200, sma200_slope_up, rs_score) -> dict:
    """Mark Minervini's 8-point trend template."""
    price = closes[-1]
    h52 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    l52 = min(lows[-252:]) if len(lows) >= 252 else min(lows)
    checks = []
    if sma150 and sma200:
        checks.append(("Price above 150 & 200 DMA", price > sma150 and price > sma200))
        checks.append(("150 DMA above 200 DMA", sma150 > sma200))
    if sma200:
        checks.append(("200 DMA trending up (≥1 month)", bool(sma200_slope_up)))
    if sma50 and sma150 and sma200:
        checks.append(("50 DMA above 150 & 200 DMA", sma50 > sma150 and sma50 > sma200))
        checks.append(("Price above 50 DMA", price > sma50))
    checks.append(("≥30% above 52-week low", price >= l52 * 1.3))
    checks.append(("Within 25% of 52-week high", price >= h52 * 0.75))
    if rs_score is not None:
        checks.append(("Outperforming Nifty (3M)", rs_score > 0))
    passed = sum(1 for _, ok in checks if ok)
    return {"checks": [{"rule": r, "ok": ok} for r, ok in checks],
            "passed": passed, "total": len(checks),
            "pass": len(checks) >= 7 and passed >= 7}


def _relative_strength(closes, nifty_closes) -> Optional[dict]:
    if not nifty_closes or len(nifty_closes) < 70 or len(closes) < 70:
        return None
    m = min(len(closes), len(nifty_closes))
    s, nfy = closes[-m:], nifty_closes[-m:]

    def ret(arr, d):
        return (arr[-1] / arr[-d - 1] - 1) * 100 if len(arr) > d and arr[-d - 1] else None

    rs1 = ret(s, 21); rs3 = ret(s, 63); n1 = ret(nfy, 21); n3 = ret(nfy, 63)
    rs6 = ret(s, 126); n6 = ret(nfy, 126)
    ratio = [s[i] / nfy[i] for i in range(-63, 0) if nfy[i]]
    rs_line_up = len(ratio) >= 21 and ratio[-1] > ratio[-21]
    # beta / correlation over last 126 sessions
    k = min(126, m - 1)
    rs_ = [s[i] / s[i - 1] - 1 for i in range(-k, 0)]
    rn_ = [nfy[i] / nfy[i - 1] - 1 for i in range(-k, 0)]
    ms, mn = sum(rs_) / k, sum(rn_) / k
    cov = sum((rs_[i] - ms) * (rn_[i] - mn) for i in range(k)) / k
    var_n = sum((x - mn) ** 2 for x in rn_) / k
    sd_s = (sum((x - ms) ** 2 for x in rs_) / k) ** 0.5
    sd_n = var_n ** 0.5
    beta = cov / var_n if var_n else None
    corr = cov / (sd_s * sd_n) if sd_s and sd_n else None
    return {
        "excess_1m": _r(rs1 - n1) if rs1 is not None and n1 is not None else None,
        "excess_3m": _r(rs3 - n3) if rs3 is not None and n3 is not None else None,
        "excess_6m": _r(rs6 - n6) if rs6 is not None and n6 is not None else None,
        "nifty_1m": _r(n1), "nifty_3m": _r(n3),
        "rs_line_rising": rs_line_up,
        "beta": _r(beta), "correlation": _r(corr),
        "leader": (rs3 is not None and n3 is not None and rs3 - n3 > 5 and rs_line_up),
    }


def _risk_stats(closes, highs, lows, atr) -> dict:
    n = len(closes)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(max(1, n - 252), n) if closes[i - 1]]
    vol = _std(rets) * (252 ** 0.5) * 100 if rets else None
    peak = closes[max(0, n - 252)]; mdd = 0.0
    for c in closes[max(0, n - 252):]:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return {"atr_pct": _r(atr / closes[-1] * 100) if atr else None,
            "annual_volatility_pct": _r(vol, 1),
            "max_drawdown_1y_pct": _r(mdd * 100, 1),
            "avg_daily_move_pct": _r(sum(abs(x) for x in rets) / len(rets) * 100) if rets else None}


# ════════════════════════════════════════════════════════════════════════
# CORE ANALYSIS
# ════════════════════════════════════════════════════════════════════════

CATEGORY_WEIGHTS = {"trend": 0.32, "momentum": 0.23, "flow": 0.15, "rs": 0.10, "setup": 0.20}

VERDICTS = [(75, "Strong Buy", "#00d4aa"), (62, "Buy", "#4caf50"),
            (45, "Neutral", "#ffd700"), (30, "Sell", "#ff9800"), (-1, "Strong Sell", "#ff4d6d")]


def _analyse(candles: list, nifty_closes: Optional[list] = None) -> dict:
    """Compute every indicator + score for a candle list (oldest first)."""
    if len(candles) < 60:
        return {"error": "Not enough history (need 60+ days)"}

    opens = [c["o"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]
    vols = [c.get("v") or 0 for c in candles]
    price = closes[-1]
    n = len(closes)

    # ── Indicators ──────────────────────────────────────────────────────
    rsi_s = _rsi_series(closes)
    rsi = _r(rsi_s[-1]) if rsi_s[-1] is not None else None
    rsi_prev = rsi_s[-2] if len(rsi_s) > 1 else None
    macd = _macd(closes)
    bb = _bollinger(closes)
    stoch = _stochastic(closes, highs, lows)
    adx = _adx(highs, lows, closes)
    cci = _cci(highs, lows, closes)
    wr = _williams_r(highs, lows, closes)
    roc = _roc(closes)
    aroon = _aroon(highs, lows)
    obv = _obv(closes, vols)
    mfi = _mfi(highs, lows, closes, vols)
    cmf = _cmf(highs, lows, closes, vols)
    vwap = _vwap(highs, lows, closes, vols)
    psar = _psar(highs, lows, closes)
    st = _supertrend(highs, lows, closes)
    sq = _keltner_squeeze(highs, lows, closes)
    ichi = _ichimoku(highs, lows, closes)
    fib = _fibonacci(closes, highs, lows)
    mom = _momentum_returns(closes)
    don = _donchian(highs, lows, closes)
    struct = _structure(highs, lows, closes)
    div = _rsi_divergence(closes, rsi_s)
    pats = _patterns(opens, highs, lows, closes)
    blast = _blast_off(opens, highs, lows, closes)
    rs = _relative_strength(closes, nifty_closes)

    sma20 = _sma(closes, 20); sma50 = _sma(closes, 50)
    sma150 = _sma(closes, 150); sma200 = _sma(closes, 200)
    sma200_s = _sma_series(closes, 200) if n >= 222 else None
    sma200_up = bool(sma200_s and sma200_s[-1] and sma200_s[-22] and sma200_s[-1] > sma200_s[-22])
    sma50_s = _sma_series(closes, 50) if n >= 60 else None
    sma50_up = bool(sma50_s and sma50_s[-1] and sma50_s[-11] and sma50_s[-1] > sma50_s[-11])
    emas = {p: _ema_full(closes, p)[-1] for p in (8, 13, 21, 34, 55)}
    ema_vals = [emas[p] for p in (8, 13, 21, 34, 55)]
    ribbon_bull = all(v is not None for v in ema_vals) and all(ema_vals[i] > ema_vals[i + 1] for i in range(4))
    ribbon_bear = all(v is not None for v in ema_vals) and all(ema_vals[i] < ema_vals[i + 1] for i in range(4))
    atr_s = _atr_series(highs, lows, closes)
    atr = atr_s[-1]
    vol_sma20 = _sma(vols, 20)
    vr = (vols[-1] / vol_sma20) if vol_sma20 else None
    # accumulation vs distribution days (last 20)
    acc = dist = 0
    if vol_sma20:
        for i in range(n - 20, n):
            if vols[i] > vol_sma20 * 1.2:
                if closes[i] > closes[i - 1]: acc += 1
                elif closes[i] < closes[i - 1]: dist += 1
    h52 = max(highs[-252:]) if n >= 252 else max(highs)
    l52 = min(lows[-252:]) if n >= 252 else min(lows)
    pos52 = _r((price - l52) / (h52 - l52) * 100, 1) if h52 != l52 else 50
    dist_high52 = _r((h52 - price) / price * 100)
    # volatility contraction: ATR(10) vs ATR(50)
    atr10 = _sma(_tr_series(highs, lows, closes)[-10:], 10)
    atr50 = _sma(_tr_series(highs, lows, closes)[-50:], 50)
    vcp = bool(atr10 and atr50 and atr10 < atr50 * 0.75)
    supp = _r(sum(sorted(lows[-30:])[:3]) / 3)
    res = _r(sum(sorted(highs[-30:], reverse=True)[:3]) / 3)
    tt = _trend_template(closes, highs, lows, sma50, sma150, sma200, sma200_up,
                         rs["excess_3m"] if rs else None)
    risk = _risk_stats(closes, highs, lows, atr)

    uptrend = bool(sma50 and sma200 and price > sma50 > sma200) or ribbon_bull
    downtrend = bool(sma50 and sma200 and price < sma50 < sma200) or ribbon_bear
    strong_trend = bool(adx and adx["trending"] and adx["bullish_di"])

    # ── Signals & scoring ────────────────────────────────────────────────
    signals = []
    cat_pts = {k: 0.0 for k in CATEGORY_WEIGHTS}
    cat_max = {k: 0.0 for k in CATEGORY_WEIGHTS}

    def add(cat, name, text, weight, max_w=None, value=None):
        icon = "▲" if weight > 0 else "▼" if weight < 0 else "●"
        signals.append({"name": name, "verdict": text, "icon": icon,
                        "weight": weight, "category": cat, "value": value})
        cat_pts[cat] += weight
        cat_max[cat] += abs(max_w if max_w is not None else weight) or 1

    # TREND ───────────────────────────────────────────────────────────
    if sma200:
        if price > sma200:
            add("trend", "SMA 200", f"Price above 200 DMA ₹{_r(sma200)} — long-term bull regime"
                + (" · 200 DMA rising" if sma200_up else ""), 2 if sma200_up else 1.5, 2, _r(sma200))
        else:
            add("trend", "SMA 200", f"Price below 200 DMA ₹{_r(sma200)} — long-term bear regime", -2, 2, _r(sma200))
    if sma50 and sma200:
        if sma50 > sma200:
            add("trend", "Golden Cross", f"SMA50 ₹{_r(sma50)} > SMA200 ₹{_r(sma200)} — bullish structure", 2, 2)
        else:
            add("trend", "Death Cross", f"SMA50 ₹{_r(sma50)} < SMA200 ₹{_r(sma200)} — bearish structure", -2, 2)
    if sma50:
        add("trend", "SMA 50", f"Price {'above' if price > sma50 else 'below'} 50 DMA ₹{_r(sma50)}"
            + (" (rising)" if sma50_up else ""), 1 if price > sma50 else -1, 1, _r(sma50))
    if sma20:
        add("trend", "SMA 20", f"Price {'above' if price > sma20 else 'below'} 20 DMA ₹{_r(sma20)}",
            1 if price > sma20 else -1, 1, _r(sma20))
    if all(v is not None for v in ema_vals):
        if ribbon_bull:
            add("trend", "EMA Ribbon", "EMA 8>13>21>34>55 perfectly stacked — textbook uptrend", 2, 2)
        elif ribbon_bear:
            add("trend", "EMA Ribbon", "EMA ribbon inverted (8<13<21<34<55) — textbook downtrend", -2, 2)
        else:
            add("trend", "EMA Ribbon", "EMA ribbon tangled — trend transition / consolidation", 0, 2)
    if st:
        if st["bullish"]:
            add("trend", "Supertrend", f"Bullish · line ₹{st['line']} acts as trailing stop"
                + (" · FRESH FLIP" if st["fresh_flip"] else f" · {st['bars_since_flip']} bars"),
                2 if st["fresh_flip"] else 1.5, 2, st["line"])
        else:
            add("trend", "Supertrend", f"Bearish · resistance at ₹{st['line']}"
                + (" · fresh flip down" if st["fresh_flip"] else ""), -2 if st["fresh_flip"] else -1.5, 2, st["line"])
    if ichi:
        if ichi["above_cloud"] and ichi["tk_above_kj"] and ichi["chikou_bullish"]:
            add("trend", "Ichimoku", "Above cloud + Tenkan>Kijun + Chikou confirms — triple bullish", 2, 2)
        elif ichi["below_cloud"] and not ichi["tk_above_kj"] and not ichi["chikou_bullish"]:
            add("trend", "Ichimoku", "Below cloud + Tenkan<Kijun + Chikou bearish — triple bearish", -2, 2)
        elif ichi["above_cloud"]:
            add("trend", "Ichimoku", f"Above cloud (Kijun ₹{ichi['kijun']}) — bullish", 1, 2)
        elif ichi["below_cloud"]:
            add("trend", "Ichimoku", "Below cloud — bearish territory", -1, 2)
        else:
            add("trend", "Ichimoku", "Inside cloud — no trend, wait for a break", 0, 2)
    if adx:
        if adx["trending"] and adx["bullish_di"]:
            add("trend", "ADX / DI", f"Uptrend with ADX {adx['adx']} (+DI {adx['plus_di']} > -DI {adx['minus_di']})"
                + (" · strengthening" if adx["rising"] else ""), 2 if adx["strong"] else 1, 2, adx["adx"])
        elif adx["trending"]:
            add("trend", "ADX / DI", f"Downtrend with ADX {adx['adx']} (-DI dominant)", -2 if adx["strong"] else -1, 2, adx["adx"])
        else:
            add("trend", "ADX / DI", f"ADX {adx['adx']} — no established trend (range-bound)", 0, 2, adx["adx"])
    if psar:
        add("trend", "Parabolic SAR", f"SAR {'below' if psar['bullish'] else 'above'} price at ₹{psar['sar']}",
            1 if psar["bullish"] else -1, 1, psar["sar"])

    # MOMENTUM ────────────────────────────────────────────────────────
    if rsi is not None:
        rising = rsi_prev is not None and rsi > rsi_prev
        if rsi < 30:
            add("momentum", "RSI", f"Oversold at {rsi}" + (" and turning up — bounce setup" if rising else " — falling knife, wait for turn"),
                1.5 if rising else 0.5, 2, rsi)
        elif rsi < 40:
            add("momentum", "RSI", f"{rsi} — weak, near oversold" + (" (turning up)" if rising else ""), 0.5 if rising else -0.5, 2, rsi)
        elif rsi < 50:
            add("momentum", "RSI", f"{rsi} — neutral-soft" + (" · healthy pullback zone in uptrend" if uptrend else ""), 0.5 if uptrend else 0, 2, rsi)
        elif rsi < 62:
            add("momentum", "RSI", f"{rsi} — bullish zone, room to run", 1.5, 2, rsi)
        elif rsi < 72:
            add("momentum", "RSI", f"{rsi} — strong momentum" + (" (bull trend: overbought can stay overbought)" if strong_trend else ""), 1 if strong_trend else 0.5, 2, rsi)
        elif rsi < 80:
            add("momentum", "RSI", f"{rsi} — overbought, expect consolidation", -0.5 if strong_trend else -1, 2, rsi)
        else:
            add("momentum", "RSI", f"{rsi} — extremely overbought, chase risk high", -2, 2, rsi)
    if macd:
        if macd["bullish_cross"]:
            add("momentum", "MACD", f"Bullish crossover ({macd['macd']}) — fresh buy trigger", 2, 2, macd["macd"])
        elif macd["bearish_cross"]:
            add("momentum", "MACD", f"Bearish crossover ({macd['macd']}) — momentum rolled over", -2, 2, macd["macd"])
        elif macd["above_signal"]:
            add("momentum", "MACD", f"Above signal · histogram {'expanding' if macd['hist_rising'] else 'flat/contracting'}"
                + (" · above zero" if macd["above_zero"] else ""), 1.5 if macd["hist_rising"] else 1, 2, macd["macd"])
        else:
            add("momentum", "MACD", "Below signal line — bearish momentum", -1, 2, macd["macd"])
    if stoch:
        if stoch["bull_cross"] and stoch["oversold"]:
            add("momentum", "Stochastic", f"Oversold bull cross K {stoch['k']} / D {stoch['d']} — reversal trigger", 2, 2, stoch["k"])
        elif stoch["bear_cross"] and stoch["overbought"]:
            add("momentum", "Stochastic", f"Overbought bear cross K {stoch['k']} / D {stoch['d']} — pullback risk", -2, 2, stoch["k"])
        elif stoch["bull_cross"]:
            add("momentum", "Stochastic", f"Bull cross K {stoch['k']} > D {stoch['d']}", 1, 2, stoch["k"])
        elif stoch["bear_cross"]:
            add("momentum", "Stochastic", f"Bear cross K {stoch['k']} < D {stoch['d']}", -1, 2, stoch["k"])
        elif stoch["oversold"]:
            add("momentum", "Stochastic", f"Oversold K {stoch['k']} — watch for turn", 0.5, 2, stoch["k"])
        elif stoch["overbought"]:
            add("momentum", "Stochastic", f"Overbought K {stoch['k']}" + (" — fine in strong trend" if strong_trend else ""), 0 if strong_trend else -0.5, 2, stoch["k"])
        else:
            add("momentum", "Stochastic", f"Neutral K {stoch['k']} / D {stoch['d']}", 0, 2, stoch["k"])
    if cci is not None:
        add("momentum", "CCI", f"{cci} — " + ("oversold zone" if cci <= -100 else "overbought zone" if cci >= 100 else "neutral"),
            1 if cci <= -100 else (-1 if cci >= 150 else (0.5 if cci >= 100 else 0)), 1, cci)
    if wr is not None:
        add("momentum", "Williams %R", f"{wr} — " + ("oversold" if wr <= -80 else "overbought" if wr >= -20 else "mid-range"),
            1 if wr <= -80 else (0 if wr >= -20 and strong_trend else -0.5 if wr >= -20 else 0), 1, wr)
    if roc is not None:
        add("momentum", "ROC (20)", f"20-day rate of change {roc:+.2f}%", 1 if roc > 3 else -1 if roc < -3 else 0, 1, roc)
    if aroon:
        add("momentum", "Aroon", f"Up {aroon['up']} / Down {aroon['down']} — " + ("new highs dominating" if aroon["bullish"] else "new lows dominating" if aroon["bearish"] else "mixed"),
            1 if aroon["bullish"] else -1 if aroon["bearish"] else 0, 1, aroon["osc"])
    if sq:
        if sq["fired_bullish"]:
            add("momentum", "TTM Squeeze", f"Squeeze FIRED bullish after {sq['squeeze_bars']} bars — expansion phase", 2, 2, sq["momentum"])
        elif sq["squeezing"]:
            add("momentum", "TTM Squeeze", "Squeeze ON — energy building" + (" · momentum rising" if sq["mom_rising"] else ""), 0.5 if sq["mom_rising"] else 0, 2, sq["momentum"])
        elif sq["fired"]:
            add("momentum", "TTM Squeeze", "Squeeze fired bearish — expansion to the downside", -1.5, 2, sq["momentum"])
        else:
            add("momentum", "TTM Squeeze", f"No squeeze · momentum {'rising' if sq['mom_rising'] else 'falling'}", 0.5 if (sq["momentum"] or 0) > 0 and sq["mom_rising"] else -0.5 if (sq["momentum"] or 0) < 0 else 0, 2, sq["momentum"])
    m3, m6 = mom["ret_3m"] or 0, mom["ret_6m"] or 0
    if mom["ret_3m"] is not None:
        if m3 > 10 and m6 > 15:
            add("momentum", "Momentum", f"Strong — 3M {m3:+.1f}% / 6M {m6:+.1f}%", 2, 2, m3)
        elif m3 > 4 or m6 > 8:
            add("momentum", "Momentum", f"Positive — 3M {m3:+.1f}% / 6M {m6:+.1f}%", 1, 2, m3)
        elif m3 < -10 and m6 < -15:
            add("momentum", "Momentum", f"Weak — 3M {m3:+.1f}% / 6M {m6:+.1f}%", -2, 2, m3)
        elif m3 < -4 or m6 < -8:
            add("momentum", "Momentum", f"Declining — 3M {m3:+.1f}% / 6M {m6:+.1f}%", -1, 2, m3)
        else:
            add("momentum", "Momentum", f"Flat — 3M {m3:+.1f}% / 6M {m6:+.1f}%", 0, 2, m3)

    # MONEY FLOW ──────────────────────────────────────────────────────
    if obv:
        if obv["bullish_divergence"]:
            add("flow", "OBV", "Bullish divergence — price down, OBV up: quiet accumulation", 2, 2)
        elif obv["bearish_divergence"]:
            add("flow", "OBV", "Bearish divergence — price up, OBV down: distribution", -2, 2)
        elif obv["rising"]:
            add("flow", "OBV", "Rising — volume confirms the advance", 1, 2)
        else:
            add("flow", "OBV", "Falling — volume not supporting price", -1, 2)
    if mfi is not None:
        add("flow", "MFI", f"{mfi} — " + ("oversold money flow" if mfi < 20 else "overbought money flow" if mfi > 80 else "positive flow" if mfi > 55 else "negative flow" if mfi < 45 else "balanced"),
            1 if mfi < 20 else -1 if mfi > 85 else 0.5 if mfi > 55 else -0.5 if mfi < 45 else 0, 1, mfi)
    if cmf is not None:
        add("flow", "Chaikin MF", f"{cmf:+.3f} — " + ("buyers in control" if cmf > 0.1 else "sellers in control" if cmf < -0.1 else "balanced"),
            1 if cmf > 0.1 else -1 if cmf < -0.1 else 0, 1, cmf)
    if vwap:
        add("flow", "VWAP (20d)", f"Price {'above' if price > vwap else 'below'} 20-day VWAP ₹{vwap}", 1 if price > vwap else -1, 1, vwap)
    if vol_sma20 and vr is not None:
        if closes[-1] > closes[-2] and vr > 1.5:
            add("flow", "Volume", f"Up day on {vr:.1f}x average volume — institutional buying", 1.5, 2, _r(vr))
        elif closes[-1] < closes[-2] and vr > 1.5:
            add("flow", "Volume", f"Down day on {vr:.1f}x average volume — institutional selling", -1.5, 2, _r(vr))
        elif vr < 0.5:
            add("flow", "Volume", f"Volume dried up ({vr:.1f}x avg)" + (" — quiet pullback, constructive" if uptrend and closes[-1] < closes[-2] else ""), 0.5 if uptrend and closes[-1] < closes[-2] else 0, 2, _r(vr))
        else:
            add("flow", "Volume", f"Normal volume ({vr:.1f}x avg)", 0, 2, _r(vr))
    if vol_sma20:
        add("flow", "Accum / Dist days", f"Last 20 sessions: {acc} accumulation vs {dist} distribution days",
            1 if acc - dist >= 2 else -1 if dist - acc >= 2 else 0, 1, acc - dist)

    # RELATIVE STRENGTH ───────────────────────────────────────────────
    if rs:
        e3, e1 = rs["excess_3m"], rs["excess_1m"]
        if e3 is not None:
            add("rs", "RS vs Nifty (3M)", f"{e3:+.1f}% vs Nifty over 3 months" + (" — LEADER" if rs["leader"] else ""),
                2 if e3 > 8 else 1 if e3 > 0 else -1 if e3 > -8 else -2, 2, e3)
        if e1 is not None:
            add("rs", "RS vs Nifty (1M)", f"{e1:+.1f}% vs Nifty over 1 month", 1 if e1 > 0 else -1, 1, e1)
        add("rs", "RS Line", "RS line rising — outperformance accelerating" if rs["rs_line_rising"] else "RS line falling — lagging the index",
            1 if rs["rs_line_rising"] else -1, 1)

    # SETUP / STRUCTURE ───────────────────────────────────────────────
    if don:
        if don["breakout55"]:
            add("setup", "Donchian", f"55-day BREAKOUT above ₹{don['high55']}" + (f" on {vr:.1f}x volume" if vr and vr > 1.3 else " (volume light)"), 3 if vr and vr > 1.3 else 2, 3)
        elif don["breakout20"]:
            add("setup", "Donchian", f"20-day breakout above ₹{don['high20']}" + (f" on {vr:.1f}x volume" if vr and vr > 1.3 else ""), 2 if vr and vr > 1.3 else 1.5, 3)
        elif don["breakdown20"]:
            add("setup", "Donchian", f"20-day breakdown below ₹{don['low20']}", -2, 3)
        elif don["dist_to_high20_pct"] is not None and don["dist_to_high20_pct"] < 2:
            add("setup", "Donchian", f"Within {don['dist_to_high20_pct']}% of 20-day high ₹{don['high20']} — breakout watch", 0.5, 3)
        else:
            add("setup", "Donchian", f"Range: 20d ₹{don['low20']} – ₹{don['high20']}", 0, 3)
    pullback = bool(uptrend and emas[21] and abs(price - emas[21]) / price < 0.025 and closes[-1] >= closes[-2] and 38 <= (rsi or 50) <= 58)
    if pullback:
        add("setup", "Pullback", f"Pullback to EMA21 ₹{_r(emas[21])} in an uptrend with price stabilising — high-quality entry", 2, 2)
    if struct:
        add("setup", "Structure", struct["label"] + f" · swing low ₹{struct['last_swing_low']} / swing high ₹{struct['last_swing_high']}",
            2 if struct["uptrend"] else -2 if struct["downtrend"] else 0, 2)
    if div["bullish"]:
        add("setup", "RSI Divergence", "Bullish divergence — price lower-low, RSI higher-low: selling exhausted", 2, 2)
    elif div["bearish"]:
        add("setup", "RSI Divergence", "Bearish divergence — price higher-high, RSI lower-high: rally tiring", -2, 2)
    if pos52 is not None:
        if dist_high52 is not None and dist_high52 <= 3:
            add("setup", "52-Week", f"At / within 3% of 52-week high ₹{_r(h52)} — strength begets strength", 1.5, 2, pos52)
        elif pos52 >= 70:
            add("setup", "52-Week", f"Upper part of 52-week range ({pos52}%)", 1, 2, pos52)
        elif pos52 <= 15:
            add("setup", "52-Week", f"Near 52-week low ₹{_r(l52)} ({pos52}% of range) — only for reversal setups", -1, 2, pos52)
        else:
            add("setup", "52-Week", f"{pos52}% of 52-week range (₹{_r(l52)} – ₹{_r(h52)})", 0, 2, pos52)
    if tt["total"] >= 7:
        add("setup", "Trend Template", f"Minervini template {tt['passed']}/{tt['total']} rules passed" + (" — PASS" if tt["pass"] else ""),
            2 if tt["pass"] else 1 if tt["passed"] >= 5 else -1 if tt["passed"] <= 2 else 0, 2, tt["passed"])
    if vcp and uptrend:
        add("setup", "Volatility Contraction", "Range tightening in uptrend (ATR10 < 75% of ATR50) — spring loaded", 1, 1)
    if blast and blast["coiled"]:
        add("setup", "Blast Off", f"Body only {blast['value']}% of range — coiled, explosive move likely next session", 1 if uptrend else 0.5, 1, blast["value"])
    for p in pats:
        if p["bias"]:
            add("setup", "Candle", p["name"], 1 if p["bias"] > 0 else -1, 1)

    # ── Combine categories ───────────────────────────────────────────
    categories = {}
    total = 0.0
    for cat, w in CATEGORY_WEIGHTS.items():
        norm = (cat_pts[cat] / cat_max[cat]) if cat_max[cat] else 0.0
        norm = max(-1.0, min(1.0, norm))
        categories[cat] = {"score": int(round(50 + norm * 50)), "points": _r(cat_pts[cat], 1),
                           "max": _r(cat_max[cat], 1), "weight": w}
        total += w * norm
    raw_score = 50 + total * 50
    notes = []
    reversal_setup = bool((rsi is not None and rsi < 40) and (div["bullish"] or (stoch and stoch["bull_cross"]) or any(p["bias"] > 0 for p in pats) or (macd and macd["bullish_cross"])))
    if downtrend and not reversal_setup:
        if raw_score > 55:
            notes.append("Capped at 55: price below falling averages (bear regime)")
        raw_score = min(raw_score, 55)
    elif downtrend and reversal_setup:
        raw_score = min(raw_score, 66)
        notes.append("Counter-trend reversal setup — speculative, size small")
    if adx and adx["adx"] < 15 and not (sq and sq["fired_bullish"]):
        raw_score = 50 + (raw_score - 50) * 0.8
        notes.append("ADX < 15: choppy tape, signals discounted 20%")
    score = int(round(max(0, min(100, raw_score))))
    verdict, vcolor = next((v, c) for th, v, c in VERDICTS if score >= th)

    # ── Setup label ──────────────────────────────────────────────────
    if don and (don["breakout55"] or don["breakout20"]) and price > (sma50 or 0) and vr and vr > 1.2:
        setup = "Breakout"
    elif rs and rs["leader"] and dist_high52 is not None and dist_high52 <= 8 and tt["passed"] >= 6:
        setup = "Momentum Leader"
    elif pullback:
        setup = "Pullback Buy"
    elif sq and sq["fired_bullish"] and uptrend:
        setup = "Squeeze Breakout"
    elif reversal_setup and not uptrend and score >= 42:
        setup = "Oversold Reversal"
    elif uptrend and score >= 62:
        setup = "Trend Continuation"
    elif downtrend or score < 35:
        setup = "Avoid — Downtrend"
    elif adx and adx["adx"] < 18:
        setup = "Wait — Choppy"
    else:
        setup = "Neutral"

    # ── Trade plan (long side) ───────────────────────────────────────
    plan = None
    if atr:
        cands = [price - 2.2 * atr]
        swing_low10 = min(lows[-10:])
        if swing_low10 < price:
            cands.append(swing_low10 - 0.3 * atr)
        if st and st["bullish"] and st["line"] < price:
            cands.append(st["line"] - 0.1 * atr)
        # tightest sensible stop that is still at least 1 ATR away
        valid = [s for s in cands if price - s >= 1.0 * atr]
        stop = max(valid) if valid else price - 2.2 * atr
        risk_per_share = price - stop
        # Targets: never below 1.5R / 3R (a bull does not take sub-1:1 trades);
        # nearby resistance / 52w high are reported as levels to watch, not used to shrink targets.
        t1 = price + max(2 * atr, 1.5 * risk_per_share)
        t2 = price + max(4 * atr, 3 * risk_per_share)
        first_res = None
        if res and price < res < t2:
            first_res = _r(res)
        elif h52 > price and h52 < t2:
            first_res = _r(h52)
        rr = (t1 - price) / risk_per_share if risk_per_share else None
        plan = {
            "bias": "Long" if score >= 55 else "No trade" if score >= 45 else "Avoid / Short-bias",
            "entry": _r(price),
            "entry_note": (f"Buy on strength above ₹{_r(don['high20'])}" if don and not don["breakout20"] and don["dist_to_high20_pct"] is not None and don["dist_to_high20_pct"] < 3
                           else f"Buy near EMA21 ₹{_r(emas[21])}" if pullback else "Buy at market / on intraday dips"),
            "stop": _r(stop), "stop_pct": _r((stop / price - 1) * 100),
            "target1": _r(t1), "target1_pct": _r((t1 / price - 1) * 100),
            "target2": _r(t2), "target2_pct": _r((t2 / price - 1) * 100),
            "risk_reward": _r(rr, 1),
            "first_resistance": first_res,
            "trail": f"Trail stop with Supertrend ₹{st['line']}" if st and st["bullish"] else "Trail with 20 DMA once T1 hit",
            "horizon": "2–6 weeks (swing)" if setup in ("Breakout", "Squeeze Breakout", "Pullback Buy", "Oversold Reversal") else "1–3 months (position)",
            "risk_per_share": _r(risk_per_share),
        }

    return {
        "score": score, "verdict": verdict, "verdict_color": vcolor,
        "setup": setup, "categories": categories, "notes": notes,
        "signals": signals, "price": _r(price),
        "bullish_count": sum(1 for s in signals if s["weight"] > 0),
        "bearish_count": sum(1 for s in signals if s["weight"] < 0),
        "neutral_count": sum(1 for s in signals if s["weight"] == 0),
        # indicator values
        "rsi": rsi, "macd": macd, "bollinger": bb, "stochastic": stoch, "adx": adx,
        "cci": cci, "williams_r": wr, "roc20": roc, "aroon": aroon,
        "obv": obv, "mfi": mfi, "cmf": cmf, "vwap20": vwap,
        "parabolic_sar": psar, "supertrend": st, "squeeze": sq, "ichimoku": ichi,
        "fibonacci": fib, "momentum": mom, "donchian": don, "structure": struct,
        "divergence": div, "patterns": pats, "blast_off": blast,
        "relative_strength": rs, "trend_template": tt, "risk": risk,
        "sma20": _r(sma20), "sma50": _r(sma50), "sma150": _r(sma150), "sma200": _r(sma200),
        "sma200_rising": sma200_up,
        "ema": {str(k): _r(v) for k, v in emas.items()}, "ema9": _r(_ema_full(closes, 9)[-1]), "ema21": _r(emas[21]),
        "ema_ribbon": "bullish" if ribbon_bull else "bearish" if ribbon_bear else "mixed",
        "atr": _r(atr), "volume_ratio": _r(vr), "accum_days": acc, "dist_days": dist,
        "support": supp, "resistance": res,
        "bull_target": _r(price + 2 * atr) if atr else None,
        "bear_target": _r(price - 2 * atr) if atr else None,
        "high_52w": _r(h52), "low_52w": _r(l52), "position_52w": pos52, "dist_to_52w_high_pct": dist_high52,
        "uptrend": uptrend, "downtrend": downtrend, "vcp": vcp,
        "trade_plan": plan,
    }


# ════════════════════════════════════════════════════════════════════════
# FORECAST (self-backtest)
# ════════════════════════════════════════════════════════════════════════

def _forecast(candles: list, nifty_closes: Optional[list], current_score: int,
              horizon: int = 21, step: int = 5, min_hist: int = 230) -> Optional[dict]:
    """Score the stock at past dates; measure forward returns for 'days like today'."""
    n = len(candles)
    if n < min_hist + horizon + 10:
        return None
    samples = []
    start = max(min_hist, n - 260 - horizon)
    for i in range(start, n - horizon, step):
        sub = candles[max(0, i - 320):i + 1]
        nsub = nifty_closes[:len(nifty_closes) - (n - 1 - i)] if nifty_closes else None
        r = _analyse(sub, nsub)
        if "error" in r:
            continue
        fwd = candles[i + horizon]["c"] / candles[i]["c"] - 1
        samples.append((r["score"], fwd * 100))
    if len(samples) < 8:
        return None
    similar = [f for s, f in samples if abs(s - current_score) <= 8]
    if len(similar) < 6:
        similar = [f for s, f in samples if (s >= 60) == (current_score >= 60)]
    basis = "similar-score days" if len(similar) >= 6 else "all sampled days"
    if len(similar) < 6:
        similar = [f for _, f in samples]
    allf = [f for _, f in samples]
    srt = sorted(similar)
    med = srt[len(srt) // 2]
    return {
        "horizon_days": horizon,
        "expected_return_pct": _r(sum(similar) / len(similar)),
        "median_return_pct": _r(med),
        "hit_rate_pct": _r(sum(1 for f in similar if f > 0) / len(similar) * 100, 0),
        "best_pct": _r(max(similar)), "worst_pct": _r(min(similar)),
        "samples": len(similar), "basis": basis,
        "baseline_return_pct": _r(sum(allf) / len(allf)),
        "baseline_hit_rate_pct": _r(sum(1 for f in allf if f > 0) / len(allf) * 100, 0),
        "score_edge_pct": _r(sum(similar) / len(similar) - sum(allf) / len(allf)),
    }


def generate_prediction(candles: list, nifty_closes: Optional[list] = None,
                        with_forecast: bool = True, capital: Optional[float] = None) -> dict:
    """Public entry: full analysis + forecast + position sizing."""
    res = _analyse(candles, nifty_closes)
    if "error" in res:
        return res
    if with_forecast:
        try:
            res["forecast"] = _forecast(candles, nifty_closes, res["score"])
        except Exception as e:  # never let the forecast kill the response
            res["forecast"] = None
            res.setdefault("notes", []).append(f"forecast unavailable: {e}")
    else:
        res["forecast"] = None
    plan = res.get("trade_plan")
    if plan and capital and plan.get("risk_per_share"):
        risk_amt = capital * 0.01
        qty = int(risk_amt // plan["risk_per_share"]) if plan["risk_per_share"] > 0 else 0
        max_qty = int((capital * 0.20) // plan["entry"]) if plan["entry"] else qty
        qty = max(0, min(qty, max_qty))
        plan["position"] = {"capital": capital, "risk_pct": 1.0, "risk_amount": _r(risk_amt),
                            "quantity": qty, "value": _r(qty * plan["entry"]),
                            "note": "Risk 1% of capital per trade, max 20% of capital in one name"}
    # Profit projection in words (bullish framing, honest numbers)
    fc = res.get("forecast")
    if plan:
        res["profit_projection"] = {
            "target1_gain_pct": plan["target1_pct"], "target2_gain_pct": plan["target2_pct"],
            "max_loss_pct": plan["stop_pct"],
            "model_1m_pct": fc["expected_return_pct"] if fc else None,
            "model_hit_rate_pct": fc["hit_rate_pct"] if fc else None,
        }
    return res


# ════════════════════════════════════════════════════════════════════════
# DATA FETCH + CACHE
# ════════════════════════════════════════════════════════════════════════

_HIST_CACHE: dict = {}          # ticker -> (expires_at, candles)
_HIST_TTL = 600                 # 10 minutes
_NIFTY_TICKER = "^NSEI"


def _yahoo_ticker(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.startswith("^") or "=" in s or s.endswith(".NS") or s.endswith(".BO"):
        return s
    try:
        from market_data import resolve_symbol
        s = resolve_symbol(s)
    except Exception:
        pass
    return f"{s}.NS"


async def fetch_candles(symbol: str, range_: str = "2y", client: Optional[httpx.AsyncClient] = None) -> list:
    """Daily OHLCV candles (oldest first) with a short in-memory cache."""
    ticker = _yahoo_ticker(symbol)
    key = (ticker, range_)
    hit = _HIST_CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range={range_}"
    own = client is None
    if own:
        client = httpx.AsyncClient(headers=_HEADERS, timeout=20, follow_redirects=True)
    try:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
        res = data["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q0 = res["indicators"]["quote"][0]
        cl, op, hi, lo, vol = (q0.get(k, []) for k in ("close", "open", "high", "low", "volume"))
        candles = []
        for i, t in enumerate(ts):
            c = cl[i] if i < len(cl) else None
            if not c:
                continue
            candles.append({
                "t": t * 1000,
                "o": (op[i] if i < len(op) and op[i] else c),
                "h": (hi[i] if i < len(hi) and hi[i] else c),
                "l": (lo[i] if i < len(lo) and lo[i] else c),
                "c": c,
                "v": (vol[i] if i < len(vol) and vol[i] else 0),
            })
        candles.sort(key=lambda x: x["t"])
        if candles:
            _HIST_CACHE[key] = (time.time() + _HIST_TTL, candles)
        return candles
    finally:
        if own:
            await client.aclose()


async def fetch_nifty_closes(client: Optional[httpx.AsyncClient] = None) -> Optional[list]:
    try:
        c = await fetch_candles(_NIFTY_TICKER, "2y", client)
        return [x["c"] for x in c] if c else None
    except Exception:
        return None


def _align_nifty(candles: list, nifty_candles_closes: Optional[list]) -> Optional[list]:
    return nifty_candles_closes


async def fetch_prediction(symbol: str, with_forecast: bool = True,
                           capital: Optional[float] = None,
                           nifty_closes: Optional[list] = None,
                           client: Optional[httpx.AsyncClient] = None) -> dict:
    """Fetch 2Y daily OHLCV and run the full engine (CPU work off the event loop)."""
    try:
        if nifty_closes is None and not (symbol.startswith("^") or "=" in symbol):
            nifty_closes = await fetch_nifty_closes(client)
        candles = await fetch_candles(symbol, "2y", client)
        if len(candles) < 60:
            return {"symbol": symbol, "error": "Not enough history (need 60+ days)"}
        pred = await asyncio.to_thread(generate_prediction, candles, nifty_closes, with_forecast, capital)
        pred["symbol"] = symbol
        pred["as_of"] = datetime.fromtimestamp(candles[-1]["t"] / 1000).strftime("%Y-%m-%d")
        pred["candles"] = len(candles)
        pred["last_candle"] = {k: _r(candles[-1][k]) if k != "t" else candles[-1][k] for k in ("t", "o", "h", "l", "c", "v")}
        return pred
    except httpx.HTTPStatusError as e:
        return {"symbol": symbol, "error": f"Yahoo Finance {e.response.status_code} — symbol may be invalid"}
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


async def batch_predict(symbols: list, max_concurrent: int = 8, with_forecast: bool = True,
                        capital: Optional[float] = None) -> list:
    """Parallel predictions with concurrency cap; Nifty fetched once and shared."""
    symbols = [s for s in dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip())]
    if not symbols:
        return []
    sem = asyncio.Semaphore(max_concurrent)
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20, follow_redirects=True) as client:
        nifty = await fetch_nifty_closes(client)

        async def bounded(sym):
            async with sem:
                return await fetch_prediction(sym, with_forecast, capital, nifty, client)
        return await asyncio.gather(*[bounded(s) for s in symbols])
