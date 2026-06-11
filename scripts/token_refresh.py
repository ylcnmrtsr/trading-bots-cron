import requests, base64, os
from nacl import encoding, public

gh_token = os.environ.get("GH_PAT", "")
service_token = os.environ.get("BASE44_SERVICE_TOKEN", "")
# Static 32-char API key — JWT DEĞİL
STATIC_API_KEY = "d1e53ae9295b46a0bd197d93627ca7a0"

if not service_token:
    print("BASE44_SERVICE_TOKEN bulunamadi!")
    exit(1)

print(f"Token alindi: {service_token[:40]}...")

headers = {
    "Authorization": f"Bearer {gh_token}",
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json"
}

# GitHub public key al
pk_r = requests.get("https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/actions/secrets/public-key", headers=headers)
pk = pk_r.json()

def encrypt_secret(value, pub_key_b64, key_id):
    pub_key = public.PublicKey(base64.b64decode(pub_key_b64))
    sealed = public.SealedBox(pub_key).encrypt(value.encode())
    return base64.b64encode(sealed).decode()

# BASE44_API_KEY secret'a STATIC KEY yaz (JWT değil!)
enc_api = encrypt_secret(STATIC_API_KEY, pk["key"], pk["key_id"])
r1 = requests.put(
    "https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/actions/secrets/BASE44_API_KEY",
    headers=headers,
    json={"encrypted_value": enc_api, "key_id": pk["key_id"]}
)
print(f"BASE44_API_KEY: HTTP {r1.status_code} {'OK' if r1.status_code in [201,204] else 'FAIL'}")

# BASE44_SERVICE_TOKEN secret'a JWT yaz (ayrı tutuluyor)
enc_svc = encrypt_secret(service_token, pk["key"], pk["key_id"])
r2 = requests.put(
    "https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/actions/secrets/BASE44_SERVICE_TOKEN",
    headers=headers,
    json={"encrypted_value": enc_svc, "key_id": pk["key_id"]}
)
print(f"BASE44_SERVICE_TOKEN: HTTP {r2.status_code} {'OK' if r2.status_code in [201,204] else 'FAIL'}")

print("Token yenileme tamamlandi!")
