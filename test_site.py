import json, os, sys, tempfile, unittest, random
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.update(SECRET_KEY="k" * 40, SITE_PASSWORD="correct horse battery", DATA_DIR=tempfile.mkdtemp())
import app as A, runner as R


class FakeRunner:
    def __init__(self, vault, data):
        self.vault, self.kick = vault, __import__("threading").Event()
        self.status = dict(last_cycle=None, scanned=3, error="", running=False, exchange="bybit", sentiment=dict(bull=2, bear=1, none=0))
    def start(self): pass
    def signals(self): return [dict(t=1, exchange="bybit", symbol="BTC/USDT:USDT", side="long", entry=100, sl=98, tp=104, tm=dict(p_tp=.4, p_sl=.3, p_open=.3), action="сповіщення")]
    def history(self): return [dict(t=1, equity=1000, sig7=1), dict(t=2, equity=1010, sig7=2)]
    def next_in(self): return 600
    def chart_ex(self, n):
        class E:
            def candles(s, sym, tf, n): return [dict(t=i * 3600000, o=1, h=2, l=.5, c=1.5) for i in range(5)]
        return E()


def client():
    app = A.create_app(testing=True, runner_factory=FakeRunner, start_runner=False)
    return app, app.test_client()


def login(c, pw="correct horse battery"):
    return c.post("/login", data={"p": pw})


class T(unittest.TestCase):
    def test_closed_without_login(self):
        _, c = client()
        self.assertEqual(c.get("/").status_code, 302)
        self.assertEqual(c.get("/api/state").status_code, 401)
        self.assertEqual(c.get("/static/app.js").status_code, 404)
        self.assertEqual(c.get("/static/app.css").status_code, 200)

    def test_login_flow_and_csrf(self):
        app, c = client()
        self.assertEqual(login(c, "wrong").status_code, 401)
        self.assertEqual(login(c).status_code, 302)
        st = c.get("/api/state").get_json()
        self.assertIn("csrf", st)
        self.assertEqual(c.post("/api/stop").status_code, 403)                     # без CSRF
        self.assertEqual(c.post("/api/stop", headers={"X-CSRF": "bad"}).status_code, 403)
        r = c.post("/api/stop", headers={"X-CSRF": st["csrf"]})
        self.assertTrue(r.get_json()["settings"]["stop"])

    def test_bruteforce_lock(self):
        _, c = client()
        codes = [login(c, "x%d" % i).status_code for i in range(7)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)
        self.assertEqual(login(c).status_code, 429)                                   # навіть правильний пароль заблоковано

    def test_vault_encrypted_and_not_leaked(self):
        app, c = client()
        login(c)
        csrf = c.get("/api/state").get_json()["csrf"]
        secret = "SUPERSECRETAPISECRET123456"
        r = c.post("/api/exchange", json=dict(name="bybit", key="KEY1234567890", secret=secret), headers={"X-CSRF": csrf})
        self.assertEqual(r.status_code, 200)
        raw = open(os.path.join(os.environ["DATA_DIR"], "vault.bin"), "rb").read()
        self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(b"KEY1234567890", raw)
        body = c.get("/api/state").get_data(as_text=True)
        self.assertNotIn(secret, body)
        self.assertNotIn("KEY1234567890", body)
        self.assertEqual(c.post("/api/exchange", json=dict(name="evil", key="a", secret="b"), headers={"X-CSRF": csrf}).status_code, 400)

    def test_settings_validation_and_live_gate(self):
        app, c = client()
        login(c)
        csrf = c.get("/api/state").get_json()["csrf"]
        h = {"X-CSRF": csrf}
        self.assertEqual(c.post("/api/settings", json=dict(risk_pct=50), headers=h).status_code, 400)
        self.assertEqual(c.post("/api/settings", json=dict(mode="live"), headers=h).status_code, 400)
        os.environ["ALLOW_LIVE"] = "yes"
        self.assertEqual(c.post("/api/settings", json=dict(mode="live", confirm="ні"), headers=h).status_code, 400)
        self.assertEqual(c.post("/api/settings", json=dict(mode="live", confirm=A.CONFIRM), headers=h).status_code, 200)
        self.assertEqual(c.post("/api/settings", json=dict(auto=True), headers=h).status_code, 400)   # live+auto без I_ACCEPT
        os.environ.pop("ALLOW_LIVE")
        self.assertEqual(c.post("/api/settings", json=dict(mode="paper"), headers=h).status_code, 200)

    def test_headers_candles_and_ip_allowlist(self):
        app, c = client()
        login(c)
        r = c.get("/api/state")
        self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(len(c.get("/api/candles?symbol=BTC/USDT:USDT&tf=1h").get_json()["candles"]), 5)
        self.assertEqual(c.get("/api/candles?tf=7d").status_code, 400)
        os.environ["ALLOWED_IPS"] = "10.1.1.1"
        _, c2 = client()
        self.assertEqual(c2.get("/login").status_code, 404)
        os.environ.pop("ALLOWED_IPS")

    def test_runner_cycle_with_fake_exchange(self):
        import vault as V
        d = tempfile.mkdtemp()
        v = V.Vault(os.path.join(d, "v.bin"), "z" * 40)
        def walk(seed, n, step, vol):
            r, p, out = random.Random(seed), 100.0, []
            for i in range(n):
                o = p; p = o * (1 + r.gauss(0, vol))
                out.append(dict(t=1_790_000_000_000 + i * step, o=o, h=max(o, p) * 1.001, l=min(o, p) * .999, c=p))
            return out
        class FakeEx:
            can_trade, name = False, "fake"
            def universe(s, a, b): return ["A/USDT:USDT", "B/USDT:USDT"]
            def candles(s, sym, tf, n): return walk(1, n, 900000, .004) if tf == "15m" else walk(2, n, 14400000, .012)
            def equity(s): return 1000
        rn = R.Runner(v, d, factory=lambda name, cfg, sb: FakeEx())
        rn.run_once()
        self.assertEqual(rn.status["scanned"], 2)
        self.assertEqual(sum(rn.status["sentiment"].values()), 2)
        self.assertEqual(len(rn.history()), 1)
        self.assertEqual(rn.status["error"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
