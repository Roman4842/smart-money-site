"""Сейф: усе (ключі бірж, налаштування, підписки на пуші) шифрується Fernet і лежить в одному файлі.
Ключ шифрування виводиться з SECRET_KEY (PBKDF2). Ключі бірж назовні не віддаються ніколи."""
import base64
import hashlib
import json
import os
import threading

from cryptography.fernet import Fernet

DEFAULTS = dict(mode="paper", auto=False, sandbox=True, stop=False, min_edge=0.40, deposit=1000.0, risk_pct=1.0,
                max_notional=500.0, max_trades_day=3, daily_loss_pct=3.0, min_turnover=5_000_000.0, max_symbols=300)
RANGES = dict(min_edge=(0, 1), deposit=(10, 1e7), risk_pct=(0.05, 2.0), max_notional=(10, 1e7), max_trades_day=(1, 50),
              daily_loss_pct=(0.5, 20), min_turnover=(0, 1e10), max_symbols=(5, 1000))


class Vault:
    def __init__(self, path, secret):
        k = hashlib.pbkdf2_hmac("sha256", secret.encode(), b"smartbot-vault-v1", 200_000)
        self.f, self.path, self.lock = Fernet(base64.urlsafe_b64encode(k)), path, threading.Lock()

    def _load(self):
        try:
            with open(self.path, "rb") as fh:
                return json.loads(self.f.decrypt(fh.read()))
        except FileNotFoundError:
            return {}

    def _save(self, d):
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(self.f.encrypt(json.dumps(d).encode()))
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def settings(self):
        return {**DEFAULTS, **self._load().get("settings", {})}

    def update_settings(self, patch):
        with self.lock:
            d = self._load()
            s = {**DEFAULTS, **d.get("settings", {})}
            for k, v in patch.items():
                if k not in DEFAULTS:
                    continue
                if isinstance(DEFAULTS[k], bool):
                    s[k] = bool(v)
                elif k == "mode":
                    if v not in ("paper", "live"):
                        raise ValueError("mode")
                    s[k] = v
                else:
                    lo, hi = RANGES[k]
                    v = float(v)
                    if not lo <= v <= hi:
                        raise ValueError("%s поза межами %s..%s" % (k, lo, hi))
                    s[k] = int(v) if isinstance(DEFAULTS[k], int) else v
            d["settings"] = s
            self._save(d)
            return s

    def exchanges(self):
        return self._load().get("exchanges", {})

    def set_exchange(self, name, key, secret, password=""):
        with self.lock:
            d = self._load()
            d.setdefault("exchanges", {})[name] = dict(key=key, secret=secret, password=password)
            self._save(d)

    def delete_exchange(self, name):
        with self.lock:
            d = self._load()
            d.get("exchanges", {}).pop(name, None)
            self._save(d)

    def subs(self):
        return self._load().get("push", [])

    def add_sub(self, sub):
        with self.lock:
            d = self._load()
            p = [s for s in d.get("push", []) if s.get("endpoint") != sub.get("endpoint")]
            d["push"] = (p + [sub])[-10:]
            self._save(d)
