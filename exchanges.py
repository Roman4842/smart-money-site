"""Біржі через ccxt. Ключі приходять з сейфа сайту, не з коду."""
import time

ALIAS = {"binance": "binanceusdm", "kucoin": "kucoinfutures"}
TF_MS = {"15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}


class Exchange:
    def __init__(self, name, key=None, secret=None, password=None, sandbox=True, max_notional=500.0, ccxt_module=None):
        if ccxt_module is None:
            import ccxt as ccxt_module
        self.name, self.sandbox, self.max_notional = name.lower(), bool(sandbox), float(max_notional)
        opts = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
        self.can_trade = bool(key and secret)
        if self.can_trade:
            opts.update(apiKey=key, secret=secret)
            if password:
                opts["password"] = password
        self.ex = getattr(ccxt_module, ALIAS.get(self.name, self.name))(opts)
        if self.sandbox:
            self.ex.set_sandbox_mode(True)
        self.ex.load_markets()

    def universe(self, min_turnover=5e6, limit=300):
        rows = []
        for s, t in self.ex.fetch_tickers().items():
            m = self.ex.markets.get(s) or {}
            if m.get("swap") and m.get("linear") and m.get("quote") == "USDT" and m.get("active", True):
                v = t.get("quoteVolume") or 0
                if v >= min_turnover:
                    rows.append((v, s))
        return [s for _, s in sorted(rows, reverse=True)[:limit]]

    def candles(self, sym, tf, limit):
        ms, data = TF_MS[tf], []
        since = int(time.time() * 1000) - limit * ms
        while len(data) < limit:
            chunk = self.ex.fetch_ohlcv(sym, tf, since=since, limit=min(1000, limit))
            if not chunk:
                break
            data += chunk
            since = chunk[-1][0] + 1
            if len(chunk) < 2:
                break
        seen, out = set(), []
        for t, o, h, l, c, *_ in data:
            if t not in seen and t + ms <= time.time() * 1000:
                seen.add(t)
                out.append(dict(t=int(t), o=o, h=h, l=l, c=c))
        return out[-limit:]

    def equity(self):
        return float((self.ex.fetch_balance().get("total") or {}).get("USDT") or 0)

    def has_exposure(self, sym):
        pos = [p for p in self.ex.fetch_positions([sym]) if abs(float(p.get("contracts") or 0)) > 0]
        return bool(pos) or bool(self.ex.fetch_open_orders(sym))

    def place(self, sym, side, entry, sl, tp, risk_usd):
        m = self.ex.market(sym)
        cs = m.get("contractSize") or 1
        amount = float(self.ex.amount_to_precision(sym, risk_usd / abs(entry - sl) / cs))
        mn = ((m.get("limits") or {}).get("amount") or {}).get("min")
        if mn and amount < mn:
            raise ValueError("розмір %s менший за мінімум біржі %s" % (amount, mn))
        if amount * cs * entry > self.max_notional:
            raise ValueError("позиція більша за ліміт розміру %s" % self.max_notional)
        params = {"stopLoss": {"triggerPrice": float(self.ex.price_to_precision(sym, sl))},
                  "takeProfit": {"triggerPrice": float(self.ex.price_to_precision(sym, tp))}}
        if self.ex.id == "okx":
            params["tdMode"] = "isolated"
        o = self.ex.create_order(sym, "limit", "buy" if side == "long" else "sell", amount,
                                 float(self.ex.price_to_precision(sym, entry)), params)
        return o.get("id"), amount
