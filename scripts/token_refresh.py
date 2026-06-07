import requests, base64, json, os, re
from nacl import encoding, public

email = os.environ.get("BASE44_EMAIL", "")
password = os.environ.get("BASE44_PASSWORD", "")
gh_token = os.environ.get("GH_TOKEN", "")

# Base44'e login ol
r = requests.post("https://app.base44.com/api/auth/login",
    json={"email": email, "password": password},
    headers={"Content-Type": "application/json"})

if r.status_code != 200:
    print(f"Login failed: {r.status_code} {r.text}")
    exit(1)

data = r.json()
token = data.get("token") or data.get("access_token") or data.get("service_token") or data.get("sessionToken")
if not token:
    print(f"Token bulunamadi. Response keys: {list(data.keys())}")
    print(f"Response: {str(data)[:200]}")
    exit(1)

print(f"Taze token alindi: {token[:40]}...")

headers = {
    "Authorization": f"Bearer {gh_token}",
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json"
}

# GitHub public key al
pk_r = requests.get("https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/actions/secrets/public-key", headers=headers)
pk = pk_r.json()

# Token encrypt et
pub_key = public.PublicKey(base64.b64decode(pk["key"]))
sealed = public.SealedBox(pub_key).encrypt(token.encode())
enc = base64.b64encode(sealed).decode()

# GitHub Secret guncelle
for secret in ["BASE44_API_KEY", "BASE44_SERVICE_TOKEN"]:
    resp = requests.put(
        f"https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/actions/secrets/{secret}",
        headers=headers,
        json={"encrypted_value": enc, "key_id": pk["key_id"]}
    )
    print(f"{secret}: HTTP {resp.status_code} {'OK' if resp.status_code in [201,204] else 'FAIL'}")

# Script dosyalarindaki hardcode token'i da guncelle
scripts = ["scripts/bot2_runner.py", "scripts/btc_signal_bot.py"]
for script in scripts:
    url = f"https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/contents/{script}"
    r = requests.get(url, headers=headers)
    d = r.json()
    content = base64.b64decode(d["content"]).decode()
    match = re.search(r'BASE44_TOKEN = "([^"]+)"', content)
    if match:
        new_content = content.replace(match.group(0), f'BASE44_TOKEN = "{token}"')
        new_b64 = base64.b64encode(new_content.encode()).decode()
        resp = requests.put(url, headers=headers, json={
            "message": "chore: auto-refresh BASE44 token [skip ci]",
            "content": new_b64,
            "sha": d["sha"]
        })
        print(f"{script}: HTTP {resp.status_code} {'OK' if resp.status_code in [200,201] else 'FAIL'}")

print("Token yenileme tamamlandi!")
