"""python gen_vapid.py -> два рядки для змінних середовища VAPID_PUBLIC_KEY і VAPID_PRIVATE_KEY"""
import base64
from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric import ec

k = ec.generate_private_key(ec.SECP256R1())
b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
print("VAPID_PUBLIC_KEY=" + b(k.public_key().public_bytes(s.Encoding.X962, s.PublicFormat.UncompressedPoint)))
print("VAPID_PRIVATE_KEY=" + b(k.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())))
