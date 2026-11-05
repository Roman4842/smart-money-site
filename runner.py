"""Фоновий потік: кожні 15 хв скан усіх монет, Машина часу, FOMO, сповіщення, (за дозволом) торгівля."""
import json
import os
import threading
import time

import notify
import push
import timemachine
from engine import Cfg, analyze


class Runner(threading.Thread):
    def __init__(self, vault, data_dir, factory=None):
        super().__init__(daemon=True)
        self.vault, self.dir, self.factory = vault, data_dir, factory
        self.kick, self.lock = threading.Event(), threading.Lock()
        self.status = dict(last_cycle=None, scanned=0, error="", running=False, exchange="", sentiment=dict(bull=0, bear=0, none=0))
        self.pub = {}

    # ---- журнал сигналів
    def _sigs_path(self):
        return os.path.join(self.dir, "signals.json")

    def signals(self):
        try:
            with open(self._sigs_path()) as f:
                return json.load(f)
        except Exception:
            return []

    def _log(self, rec):
        with self.lock:
            s = (self.signals() + [rec])[-200:]
            with open(self._sigs_path(), "w") as f:
                json.dump(s, f)

    def _notify(self, title, text):
        notify.send(title, text)
        push.send_all(self.vault.subs(), title, text)

    def _make(self, name, cfgx, ex_cfg, sandbox):
        if self.factory:
            return self.factory(name, ex_cfg, sandbox)
        from exchanges import Exchange
        return Exchange(name, ex_cfg.get("key"), ex_cfg.get("secret"), ex_cfg.get("password"), sandbox, cfgx["max_notional"])

    # ---- один цикл по біржі
    def cycle(self, name, ex_cfg, st, ex):
        S = self.vault.settings()
        cfg = Cfg(deposit=S["deposit"], risk_pct=S["risk_pct"])
        path = os.path.join(self.dir, "engine_%s.json" % name)
        try:
            with open(path) as f:
                eng = json.load(f)
        except Exception:
            eng = {}
        syms = ex.universe(S["min_turnover"], S["max_symbols"])
        for sym in syms:
            try:
                ltf, htf = ex.candles(sym, "15m", 300), ex.candles(sym, "4h", 200)
                r = analyze(sym, ltf, htf, eng, cfg)
            except Exception:
                continue
            self.status["scanned"] += 1
            self.status["sentiment"][{"bull": "bull", "bear": "bear"}.get(r["bias"], "none")] += 1
            s = r["signal"]
            if not s:
                continue
            rec = dict(t=int(time.time()), exchange=name, symbol=sym, side=s["side"], entry=s["entry"], sl=s["sl"],
                       tp=s["tp1"], fib=s["fib"], size=s["size"], risk_usd=s["risk_usd"], friday=s["friday"], action="")
            if r["fomo"]:
                rec.update(action="FOMO-блок", note=r["fomo"])
                self._log(rec)
                self._notify("FOMO-блок %s [%s]" % (sym, name), r["fomo"])
                continue
            tm = timemachine.simulate(ltf[-1000:], s["side"], s["entry"], s["sl"], s["tp1"])
            ok, why = timemachine.verdict(tm, S["min_edge"])
            rec.update(tm=dict(p_tp=tm["p_tp"], p_sl=tm["p_sl"], p_open=tm["p_open"]), note=why)
            word = "🟢 КУПИТИ" if s["side"] == "long" else "🔴 ПРОДАТИ"
            self._notify("%s %s [%s]" % (word, sym, name),
                         "Вхід %.6g SL %.6g TP %.6g (2R)\nРизик $%.2f\n%s" % (s["entry"], s["sl"], s["tp1"], s["risk_usd"], why))
            rec["action"] = "сповіщення"
            if ok and S["auto"] and ex.can_trade and not S["stop"]:
                rec["action"] = self._trade(S, ex, st, sym, s)
            self._log(rec)
        with open(path, "w") as f:
            json.dump(eng, f)

    def _trade(self, S, ex, st, sym, s):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if st.get("day") != day:
            st.update(day=day, trades=0, equity0=ex.equity())
        if st["trades"] >= S["max_trades_day"]:
            return "ліміт угод на день"
        if st.get("equity0") and ex.equity() <= st["equity0"] * (1 - S["daily_loss_pct"] / 100):
            return "денний ліміт збитку"
        if S["mode"] == "paper":
            st["trades"] += 1
            return "PAPER-угода"
        if os.getenv("ALLOW_LIVE") != "yes":
            return "live заборонено (ALLOW_LIVE)"
        try:
            if ex.has_exposure(sym):
                return "вже є позиція"
            oid, amt = ex.place(sym, s["side"], s["entry"], s["sl"], s["tp1"], s["risk_usd"])
            st["trades"] += 1
            return "ордер %s обсяг %s" % (oid, amt)
        except Exception as e:
            return "ордер НЕ виставлено: %s" % str(e)[:120]

    def next_in(self):
        return 900 - (time.time() % 900) + 20

    def run_once(self):
        S = self.vault.settings()
        exs = self.vault.exchanges() or {"bybit": {}}          # без ключів: лише скан публічних даних
        self.status.update(running=True, scanned=0, error="", sentiment=dict(bull=0, bear=0, none=0))
        for name, cfgx in exs.items():
            try:
                self.status["exchange"] = name
                ex = self._make(name, S, cfgx, S["sandbox"])
                self.cycle(name, cfgx, self.__dict__.setdefault("_st", {}).setdefault(name, {}), ex)
            except Exception as e:
                self.status["error"] = "%s: %s" % (name, str(e)[:160])
        eq = S["deposit"]
        try:
            for name, cfgx in (self.vault.exchanges() or {}).items():
                ex = self._make(name, S, cfgx, S["sandbox"])
                if ex.can_trade:
                    eq = ex.equity()
                    break
        except Exception:
            pass
        sg = self.signals()
        day = int(time.time()) - 7 * 86400
        self._hist(dict(t=int(time.time()), equity=eq, scanned=self.status["scanned"],
                        sig7=sum(1 for x in sg if x["t"] >= day), **self.status["sentiment"]))
        self.status.update(running=False, last_cycle=int(time.time()))

    def _hist(self, pt):
        p = os.path.join(self.dir, "history.json")
        try:
            with open(p) as f:
                h = json.load(f)
        except Exception:
            h = []
        with open(p, "w") as f:
            json.dump((h + [pt])[-500:], f)

    def history(self):
        try:
            with open(os.path.join(self.dir, "history.json")) as f:
                return json.load(f)
        except Exception:
            return []

    def chart_ex(self, name):
        if name not in self.pub:
            self.pub[name] = self._make(name, self.vault.settings(), {}, False)
        return self.pub[name]

    def run(self):
        while True:
            self.run_once()
            self.kick.wait(timeout=self.next_in())
            self.kick.clear()
