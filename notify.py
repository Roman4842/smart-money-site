"""Сповіщення на телефон, навіть коли ти спиш: Telegram і/або ntfy.sh (застосунок ntfy, push без акаунта)."""
import os
import requests


def send(title, text):
    ok = False
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            ok = requests.post("https://api.telegram.org/bot%s/sendMessage" % tok,
                               json={"chat_id": chat, "text": title + "\n" + text}, timeout=15).ok
        except Exception:
            pass
    topic = os.getenv("NTFY_TOPIC")
    if topic:
        try:
            ok = requests.post("https://ntfy.sh/" + topic, data=text.encode("utf8"),
                               headers={"Title": title.encode("utf8"), "Priority": "high"}, timeout=15).ok or ok
        except Exception:
            pass
    return ok
