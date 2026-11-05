"""Історія: той самий рушій по минулих свічках. python backtest.py bybit BTC/USDT:USDT"""
import sys
from engine import Cfg, analyze


def evaluate(sig, future, max_bars=96):
    d = 1 if sig["side"] == "long" else -1
    for x in future[:max_bars]:
        if (x["l"] <= sig["sl"]) if d == 1 else (x["h"] >= sig["sl"]):
            return "loss", -1.0
        if (x["h"] >= sig["tp1"]) if d == 1 else (x["l"] <= sig["tp1"]):
            return "win", sig_rr(sig)
    return "open", 0.0


def sig_rr(sig):
    return abs(sig["tp1"] - sig["entry"]) / abs(sig["entry"] - sig["sl"])


def replay(sym, ltf, htf, cfg=None, start=250):
    cfg, state, out, j = cfg or Cfg(), {}, [], 0
    for i in range(start, len(ltf)):
        tc = ltf[i]["t"] + 900000
        while j < len(htf) and htf[j]["t"] + 14400000 <= tc:
            j += 1
        r = analyze(sym, ltf[max(0, i - 259):i + 1], htf[:j], state, cfg)
        if r["signal"] and not r["fomo"]:
            res, rr = evaluate(r["signal"], ltf[i + 1:])
            out.append(dict(t=ltf[i]["t"], side=r["signal"]["side"], res=res, r=rr))
    return out


def stats(sigs):
    w, l_ = [s for s in sigs if s["res"] == "win"], [s for s in sigs if s["res"] == "loss"]
    return dict(n=len(sigs), wins=len(w), losses=len(l_), open=len(sigs) - len(w) - len(l_), total_r=sum(s["r"] for s in sigs))


if __name__ == "__main__":
    import exchanges
    ex = exchanges.Exchange(sys.argv[1] if len(sys.argv) > 1 else "bybit")
    sym = sys.argv[2] if len(sys.argv) > 2 else "BTC/USDT:USDT"
    ltf, htf = ex.candles(sym, "15m", 1000), ex.candles(sym, "4h", 1000)
    print(stats(replay(sym, ltf, htf)))
