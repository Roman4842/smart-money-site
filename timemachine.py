"""Машина часу (ідея з day-trader-skill, власна реалізація під крипто): 10 000 симуляцій того, що буде далі,
зі свічок самої монети (за тією ж годиною доби) + історія: як цей же рушій відпрацьовував раніше."""
import numpy as np


def simulate(ltf, side, entry, sl, tp, horizon=32, sims=10000, seed=19, demean=True):
    t = np.array([x["t"] for x in ltf], dtype=np.int64)
    o, h, l, c = (np.array([x[k] for x in ltf], dtype=float) for k in "ohlc")
    rh, rl, rc = h / o - 1, l / o - 1, c / o - 1
    hour = (t // 3600000) % 24
    by_hour = {k: np.where(hour == k)[0] for k in range(24)}
    if demean:                               # прибираємо дрейф вибірки (і по годинах): симуляція без тренду
        rh, rl, rc = rh.copy(), rl.copy(), rc.copy()
        groups = [g for g in by_hour.values() if len(g) >= 8] + [np.arange(len(t))]
        for g in groups[:-1]:
            m = rc[g].mean()
            rh[g], rl[g], rc[g] = rh[g] - m, rl[g] - m, rc[g] - m
        rest = np.setdiff1d(np.arange(len(t)), np.concatenate(groups[:-1])) if len(groups) > 1 else groups[-1]
        if len(rest):
            m = rc[rest].mean()
            rh[rest], rl[rest], rc[rest] = rh[rest] - m, rl[rest] - m, rc[rest] - m
    rng = np.random.default_rng(seed)
    d = 1 if side == "long" else -1
    price = np.full(sims, float(entry))
    res = np.zeros(sims, dtype=int)           # +1 ціль, -1 стоп
    alive = np.ones(sims, dtype=bool)
    done_at = np.full(sims, horizon)
    for k in range(horizon):
        hh = ((int(t[-1]) + (k + 1) * 900000) // 3600000) % 24
        idx = by_hour[hh] if len(by_hour[hh]) >= 8 else np.arange(len(t))
        pick = rng.choice(idx, size=sims)
        hi, lo, cl = price * (1 + rh[pick]), price * (1 + rl[pick]), price * (1 + rc[pick])
        hit_sl = (lo <= sl) if d == 1 else (hi >= sl)
        hit_tp = (hi >= tp) if d == 1 else (lo <= tp)
        sl_now = alive & hit_sl                # стоп першим (консервативно)
        tp_now = alive & hit_tp & ~hit_sl
        res[sl_now], res[tp_now] = -1, 1
        done_at[sl_now | tp_now] = k + 1
        alive &= ~(sl_now | tp_now)
        price = cl
    r = abs(tp - entry) / abs(entry - sl)
    p_tp, p_sl = float((res == 1).mean()), float((res == -1).mean())
    return dict(p_tp=p_tp, p_sl=p_sl, p_open=float(alive.mean()), rr=r,
                edge=p_tp / (p_tp + p_sl) if p_tp + p_sl else 0.0, ev_r=p_tp * r - p_sl, sims=sims, horizon=horizon)


def verdict(tm, min_edge=0.40):
    """Гейт ризику: беззбиток для 2R це 33%; вимагаємо запас (за замовчуванням 40% серед завершених)."""
    if tm["edge"] >= min_edge and tm["ev_r"] > 0:
        return True, "Машина часу: ціль першою %.0f%%, стоп %.0f%%, відкрита %.0f%%" % (tm["p_tp"] * 100, tm["p_sl"] * 100, tm["p_open"] * 100)
    return False, "Машина часу блокує: ціль %.0f%% проти стопа %.0f%% (треба edge >= %.0f%%)" % (tm["p_tp"] * 100, tm["p_sl"] * 100, min_edge * 100)
