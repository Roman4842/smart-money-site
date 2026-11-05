"""Рушій Smart Money (порт n8n-рушія): sweep -> MSS -> FVG в OTE -> дотик -> свічка реакції -> kill zone,
плюс FOMO-фільтр. Свічка: dict t(ms), o, h, l, c. Лише ЗАКРИТІ свічки."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


@dataclass
class Cfg:
    kz_ny: tuple = ((2, 5), (7, 11))
    nsw: int = 2
    fvg_min: float = 0.3
    sl_buf: float = 0.2
    max_risk: float = 6.0
    min_rr: float = 2.0
    lookback: int = 60
    expire: int = 16
    atrlen: int = 14
    fib_on: bool = True
    fib_lo: float = 0.62
    fib_hi: float = 0.79
    sl_mode: str = "ote"
    reaction_on: bool = True
    pending_bars: int = 32
    react_window: int = 4
    fomo_on: bool = True
    fomo_run_atr: float = 2.5      # за 3 свічки ціна вже пробігла стільки ATR у бік угоди
    fomo_risk_mult: float = 1.5    # вхід реакції далі за 1.5x початкового ризику = запізнився
    fomo_range_pct: float = 0.85   # вхід у верхніх/нижніх 15% діапазону 40 свічок
    deposit: float = 1000.0
    risk_pct: float = 1.0


def _nth_sunday(y, m, n):
    d = datetime(y, m, 1, tzinfo=timezone.utc)
    return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))


def is_us_dst(ms):
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return _nth_sunday(d.year, 3, 2).replace(hour=7) <= d < _nth_sunday(d.year, 11, 1).replace(hour=6)


def ny_time(ms):
    return datetime.fromtimestamp(ms / 1000 - (4 if is_us_dst(ms) else 5) * 3600, tz=timezone.utc)


def ny_hour(ms):
    t = ny_time(ms)
    return t.hour + t.minute / 60


def atr(R, n):
    out, tr = [], []
    for i, x in enumerate(R):
        v = x["h"] - x["l"]
        if i:
            v = max(v, abs(x["h"] - R[i - 1]["c"]), abs(x["l"] - R[i - 1]["c"]))
        tr.append(v)
        out.append(float("nan") if i < n - 1 else sum(tr[i - n + 1:i + 1]) / n)
    return out


def swings(h, l, n):
    hi, lo = [], []
    for i in range(n, len(h) - n):
        if h[i] > max(h[i - n:i]) and h[i] >= max(h[i + 1:i + n + 1]):
            hi.append(i)
        if l[i] < min(l[i - n:i]) and l[i] <= min(l[i + 1:i + n + 1]):
            lo.append(i)
    return hi, lo


def htf_state(R, cfg):
    h, l = [x["h"] for x in R], [x["l"] for x in R]
    hi, lo = swings(h, l, cfg.nsw)
    if len(hi) < 2 or len(lo) < 2:
        return 0, None
    up = h[hi[-1]] > h[hi[-2]] and l[lo[-1]] > l[lo[-2]]
    dn = h[hi[-1]] < h[hi[-2]] and l[lo[-1]] < l[lo[-2]]
    a, b = l[lo[-1]], h[hi[-1]]
    return (1 if up else -1 if dn else 0), (min(a, b), max(a, b))


def find_long(R, rng, cfg):
    N = len(R)
    if N < cfg.lookback or rng is None:
        return None
    h, l, c = [x["h"] for x in R], [x["l"] for x in R], [x["c"] for x in R]
    a = atr(R, cfg.atrlen)
    if a[-1] != a[-1]:
        return None
    hi, lo = swings(h, l, cfg.nsw)
    for s in range(N - 2, N - cfg.lookback, -1):
        lows = [i for i in lo if i + cfg.nsw < s]
        highs = [i for i in hi if i + cfg.nsw < s]
        if not lows or not highs:
            continue
        lvl = l[lows[-1]]
        if not (l[s] < lvl and c[s] > lvl):
            continue
        mss = h[highs[-1]]
        m = next((j for j in range(s + 1, N) if c[j] > mss), -1)
        if m < 0 or N - 1 - m > cfg.expire or min(l[s + 1:]) < l[s]:
            continue
        H = max(h[s:])
        leg = H - l[s]
        if leg <= 0:
            continue
        ote_hi, ote_lo, l786 = H - cfg.fib_lo * leg, H - cfg.fib_hi * leg, H - 0.786 * leg
        for k in range(min(m - 1, N - 3), s - 2, -1):
            if k < 0:
                continue
            gap = l[k + 2] - h[k]
            if not (gap > 0 and gap >= cfg.fvg_min * a[k + 1]):
                continue
            e = (l[k + 2] + h[k]) / 2
            if cfg.fib_on and not (ote_lo <= e <= ote_hi):
                continue
            if any(l[j] <= e for j in range(k + 3, N)):
                continue
            sl = (min(h[k], l786) if cfg.sl_mode == "ote" else l[s]) - cfg.sl_buf * a[s]
            risk = e - sl
            if risk <= 0 or risk > cfg.max_risk * a[-1] or e > (rng[0] + rng[1]) / 2:
                continue
            tp1 = e + cfg.min_rr * risk
            above = [h[i] for i in hi if h[i] > tp1]
            mr = mss - l[s]
            return dict(entry=e, sl=sl, tp1=tp1, tp2=min(above) if above else tp1, fvg_low=h[k], fvg_high=l[k + 2],
                        sweep=lvl, key=str(R[s]["t"]), ote_low=ote_lo, ote_high=ote_hi, depth=(H - e) / leg,
                        fib1=mss + mr, fib2=mss + 2 * mr, fib3=mss + 2.5 * mr)
    return None


def _mirror(R):
    return [dict(t=x["t"], o=-x["o"], h=-x["l"], l=-x["h"], c=-x["c"]) for x in R]


def find_setup(R, bias, rng, cfg):
    if bias == 1:
        return find_long(R, rng, cfg), "long"
    if bias == -1:
        r = find_long(_mirror(R), None if rng is None else (-rng[1], -rng[0]), cfg)
        if r:
            for k in ("entry", "sl", "tp1", "tp2", "sweep", "fib1", "fib2", "fib3"):
                r[k] = -r[k]
            r["fvg_low"], r["fvg_high"] = -r["fvg_high"], -r["fvg_low"]
            r["ote_low"], r["ote_high"] = -r["ote_high"], -r["ote_low"]
        return r, "short"
    return None, ""


def fomo_reason(side, entry, risk0, risk2, R, atr_now, cfg):
    """Книга: не входь тільки бо рух уже йде, не входь запізно, FOMO-входи біля вершини/дна."""
    d = 1 if side == "long" else -1
    if len(R) >= 4 and atr_now == atr_now and d * (R[-1]["c"] - R[-4]["c"]) > cfg.fomo_run_atr * atr_now:
        return "FOMO: за 3 свічки ціна вже пробігла %.1f ATR у бік угоди" % (d * (R[-1]["c"] - R[-4]["c"]) / atr_now)
    if risk0 > 0 and risk2 > cfg.fomo_risk_mult * risk0:
        return "FOMO: вхід запізнився, ризик виріс у %.1f раза" % (risk2 / risk0)
    seg = R[-40:]
    hi, lo = max(x["h"] for x in seg), min(x["l"] for x in seg)
    if hi > lo:
        pos = (entry - lo) / (hi - lo)
        if side == "long" and pos > cfg.fomo_range_pct:
            return "FOMO: вхід у верхніх %d%% діапазону" % round((1 - cfg.fomo_range_pct) * 100)
        if side == "short" and pos < 1 - cfg.fomo_range_pct:
            return "FOMO: вхід у нижніх %d%% діапазону" % round((1 - cfg.fomo_range_pct) * 100)
    return None


def analyze(sym, ltf, htf, state, cfg=None):
    """Повертає dict: signal (None або угода), bias, kz, fomo (причина блоку), pending."""
    cfg = cfg or Cfg()
    seen, pend = state.setdefault("seen", {}), state.setdefault("pending", {})
    out = dict(symbol=sym, signal=None, bias="none", kz=False, fomo=None, pending=0)
    if len(ltf) < cfg.lookback + 10 or len(htf) < 30:
        out["error"] = "мало даних"
        return out
    bias, rng = htf_state(htf[-120:], cfg)
    out["bias"] = {1: "bull", -1: "bear", 0: "none"}[bias]
    now = ltf[-1]["t"]
    tc = now + 15 * 60000
    hr = ny_hour(tc)
    kz = any(a <= hr < b for a, b in cfg.kz_ny)
    out.update(kz=kz, ny_hour=round(hr, 2), friday=ny_time(tc).weekday() == 4)
    win = ltf[-200:]
    setup, side = find_setup(win, bias, rng, cfg)
    fire = None
    if setup:
        pk = "%s:%s" % (sym, setup["key"])
        if pk not in seen:
            seen[pk] = now
            if cfg.reaction_on:
                pend[pk] = dict(setup, side=side, symbol=sym, created=ltf[-1]["t"])
            elif kz:
                fire = dict(setup=setup, side=side, stage="limit")
    if cfg.reaction_on:
        a = atr(win, cfg.atrlen)[-1]
        for pk in list(pend):
            p = pend[pk]
            if p["symbol"] != sym:
                continue
            bars = [x for x in ltf[-60:] if x["t"] > p["created"]]
            if len(bars) > cfg.pending_bars:
                del pend[pk]
                continue
            if not bars:
                continue
            d = 1 if p["side"] == "long" else -1
            touch, dead = -1, False
            for i, x in enumerate(bars):
                if touch < 0 and ((x["l"] <= p["entry"]) if d == 1 else (x["h"] >= p["entry"])):
                    touch = i
                if (x["c"] < p["sl"]) if d == 1 else (x["c"] > p["sl"]):
                    dead = True
                    break
            if dead:
                del pend[pk]
                continue
            if touch < 0:
                continue
            lb = bars[-1]
            ok = (lb["c"] > p["entry"] and lb["c"] > lb["o"]) if d == 1 else (lb["c"] < p["entry"] and lb["c"] < lb["o"])
            if not ok or len(bars) - 1 - touch > cfg.react_window or not kz:
                continue
            e2, risk0 = lb["c"], abs(p["entry"] - p["sl"])
            risk2 = abs(e2 - p["sl"])
            if risk2 <= 0 or a != a or risk2 > cfg.max_risk * a:
                del pend[pk]
                continue
            tp1 = e2 + d * cfg.min_rr * risk2
            fire = dict(setup=dict(p, entry=e2, tp1=tp1, tp2=tp1, zone_ce=p["entry"]), side=p["side"], stage="reaction")
            del pend[pk]
            if cfg.fomo_on:
                out["fomo"] = fomo_reason(p["side"], e2, risk0, risk2, win, a, cfg)
            break
    for k in [k for k, v in seen.items() if now - v > 3 * 86400000]:
        del seen[k]
    for k in [k for k, v in pend.items() if now - v["created"] > 2 * 86400000]:
        del pend[k]
    out["pending"] = sum(1 for v in pend.values() if v["symbol"] == sym)
    if fire:
        s = fire["setup"]
        risk_usd = cfg.deposit * cfg.risk_pct / 100
        out["signal"] = dict(side=fire["side"], stage=fire["stage"], entry=s["entry"], sl=s["sl"], tp1=s["tp1"],
                             tp2=s["tp2"], fib=(s["fib1"], s["fib2"], s["fib3"]), depth=s["depth"], risk_usd=risk_usd,
                             size=risk_usd / abs(s["entry"] - s["sl"]), friday=out["friday"], t=now)
    return out
