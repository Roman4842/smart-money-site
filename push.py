"""Web Push на телефон (PWA): працює, коли вкладку закрито. Потрібен pywebpush і VAPID-ключі (gen_vapid.py)."""
import json
import os


def public_key():
    return os.getenv("VAPID_PUBLIC_KEY", "")


def send_all(subs, title, body):
    priv = os.getenv("VAPID_PRIVATE_KEY")
    if not (priv and subs):
        return 0
    try:
        from pywebpush import webpush
    except Exception:
        return 0
    n = 0
    for s in subs:
        try:
            webpush(subscription_info=s, data=json.dumps({"title": title, "body": body}), vapid_private_key=priv,
                    vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:owner@example.com")})
            n += 1
        except Exception:
            pass
    return n
