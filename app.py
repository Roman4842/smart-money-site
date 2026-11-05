"""Закритий сайт Smart Money. Вхід за паролем, сейф ключів, бот у фоні, PWA-пуші."""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session

import push
from runner import Runner
from vault import Vault

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIRM = "ТОРГУВАТИ НА РЕАЛЬНІ ГРОШІ"
KNOWN = ["bybit", "binance", "okx", "bitget", "gate", "mexc", "kucoin", "htx"]


def pwd_hash(p, salt):
    return hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 300_000)


def create_app(testing=False, runner_factory=None, start_runner=True):
    app = Flask(__name__, static_folder=None)
    key = os.getenv("SECRET_KEY", "")
    pw = os.getenv("SITE_PASSWORD", "")
    if not testing and (len(key) < 32 or len(pw) < 10):
        raise SystemExit("Задай SECRET_KEY (від 32 символів) і SITE_PASSWORD (від 10 символів).")
    key, pw = key or "t" * 40, pw or "test-password-1"
    app.secret_key = key
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=not testing,
                      PERMANENT_SESSION_LIFETIME=12 * 3600, MAX_CONTENT_LENGTH=20_000)
    ph = pwd_hash(pw, key)
    data = os.getenv("DATA_DIR", os.path.join(HERE, "data"))
    os.makedirs(data, exist_ok=True)
    vault = Vault(os.path.join(data, "vault.bin"), key)
    runner = (runner_factory or Runner)(vault, data)
    if start_runner:
        runner.start()
    app.vault, app.runner = vault, runner
    fails, allowed = defaultdict(list), [x.strip() for x in os.getenv("ALLOWED_IPS", "").split(",") if x.strip()]

    def ip():
        if os.getenv("TRUST_PROXY") == "1":
            return (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()
        return request.remote_addr

    @app.before_request
    def guard():
        if allowed and ip() not in allowed:
            abort(404)
        if request.method in ("POST", "DELETE") and request.path != "/login":
            if not session.get("ok") or not hmac.compare_digest(request.headers.get("X-CSRF", ""), session.get("csrf", "x")):
                abort(403)

    @app.after_request
    def headers(r):
        r.headers.update({
            "Content-Security-Policy": "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow", "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Cache-Control": "no-store"})
        return r

    def authed():
        return bool(session.get("ok"))

    LOGIN = """<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вхід</title><link rel="stylesheet" href="/static/app.css"><body class="login"><form method="post" action="/login" class="card loginbox">
<h1>Smart Money</h1><p class="mut">Закритий доступ</p><input type="password" name="p" placeholder="пароль" autofocus autocomplete="current-password">
<button class="btn">Увійти</button><p class="err">%s</p></form></body></html>"""

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return LOGIN % ""
        now, a = time.time(), fails[ip()]
        a[:] = [t for t in a if now - t < 900]
        if len(a) >= 5:
            return LOGIN % "Забагато спроб. Спробуй за 15 хвилин.", 429
        if hmac.compare_digest(pwd_hash(request.form.get("p", ""), key), ph):
            session.clear()
            session.update(ok=True, csrf=secrets.token_urlsafe(24))
            session.permanent = True
            return redirect("/")
        a.append(now)
        time.sleep(0.6)
        return LOGIN % "Невірний пароль.", 401

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.route("/")
    def index():
        return send_from_directory(os.path.join(HERE, "static"), "index.html") if authed() else redirect("/login")

    @app.route("/static/<path:f>")
    def static_files(f):
        if f not in ("app.css",) and not authed():
            abort(404)
        return send_from_directory(os.path.join(HERE, "static"), f)

    @app.route("/sw.js")
    def sw():
        return send_from_directory(os.path.join(HERE, "static"), "sw.js", mimetype="text/javascript")

    def need():
        if not authed():
            abort(401)

    @app.route("/api/state")
    def state():
        need()
        sg = runner.signals()
        S = vault.settings()
        edge = None
        for x in reversed(sg):
            if x.get("tm"):
                p, q = x["tm"]["p_tp"], x["tm"]["p_sl"]
                edge = round(100 * p / (p + q)) if p + q else None
                break
        st = dict(runner.status)
        return jsonify(csrf=session["csrf"], settings=S, status=st, next_in=int(runner.next_in()), edge=edge,
                       signals=sg[-40:][::-1], history=runner.history()[-60:],
                       exchanges=[dict(name=n, trade=True) for n in vault.exchanges()], known=KNOWN,
                       push=dict(key=push.public_key(), subs=len(vault.subs())),
                       live_allowed=os.getenv("ALLOW_LIVE") == "yes")

    @app.route("/api/candles")
    def candles():
        need()
        sym, tf = request.args.get("symbol", "BTC/USDT:USDT"), request.args.get("tf", "1h")
        name = request.args.get("exchange", "bybit")
        if tf not in ("15m", "1h", "4h", "1d") or name not in KNOWN or len(sym) > 24:
            abort(400)
        try:
            c = runner.chart_ex(name).candles(sym, tf, 120)
        except Exception:
            return jsonify(error="нема даних"), 502
        return jsonify(candles=[[x["t"], x["o"], x["h"], x["l"], x["c"]] for x in c])

    @app.route("/api/exchange", methods=["POST"])
    def add_ex():
        j = request.get_json(silent=True) or {}
        n = str(j.get("name", "")).lower().strip()
        if n not in KNOWN or not j.get("key") or not j.get("secret") or len(str(j["key"])) > 200 or len(str(j["secret"])) > 400:
            abort(400)
        vault.set_exchange(n, str(j["key"]).strip(), str(j["secret"]).strip(), str(j.get("password", ""))[:100])
        return jsonify(ok=True)

    @app.route("/api/exchange/<n>", methods=["DELETE"])
    def del_ex(n):
        vault.delete_exchange(n.lower())
        return jsonify(ok=True)

    @app.route("/api/settings", methods=["POST"])
    def settings():
        j = request.get_json(silent=True) or {}
        cur = vault.settings()
        new = {**cur, **{k: v for k, v in j.items() if k in cur}}
        if new["mode"] == "live" and cur["mode"] != "live":
            if os.getenv("ALLOW_LIVE") != "yes" or j.get("confirm") != CONFIRM:
                return jsonify(error="live потребує ALLOW_LIVE=yes на сервері і фрази підтвердження"), 400
        if new["mode"] == "live" and new["auto"] and os.getenv("I_ACCEPT_LIVE_AUTO") != "yes":
            return jsonify(error="автоторгівля на live потребує I_ACCEPT_LIVE_AUTO=yes на сервері"), 400
        j.pop("confirm", None)
        try:
            return jsonify(settings=vault.update_settings(j))
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400

    @app.route("/api/stop", methods=["POST"])
    def stop():
        return jsonify(settings=vault.update_settings({"stop": not vault.settings()["stop"]}))

    @app.route("/api/scan", methods=["POST"])
    def scan():
        runner.kick.set()
        return jsonify(ok=True)

    @app.route("/api/push/subscribe", methods=["POST"])
    def sub():
        j = request.get_json(silent=True) or {}
        if not str(j.get("endpoint", "")).startswith("https://"):
            abort(400)
        vault.add_sub(j)
        return jsonify(ok=True)

    @app.route("/api/test_notify", methods=["POST"])
    def test_notify():
        import notify
        notify.send("Smart Money", "Тестове сповіщення")
        return jsonify(sent=push.send_all(vault.subs(), "Smart Money", "Тестове сповіщення"))

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
