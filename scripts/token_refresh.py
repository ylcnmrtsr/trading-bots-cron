import requests, base64, os, re
from nacl import encoding, public

gh_token = os.environ.get("GH_PAT", "")
# Mevcut token'ı al - script her calıstigında yeni token inject edilmiş olacak
current_token = os.environ.get("BASE44_SERVICE_TOKEN", "")

if not current_token:
    print("BASE44_SERVICE_TOKEN bulunamadi!")
    exit(1)

print(f"Token alindi: {current_token[:40]}...")

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
sealed = public.SealedBox(pub_key).encrypt(current_token.encode())
enc = base64.b64encode(sealed).decode()

# GitHub Secret guncelle
for secret in ["BASE44_API_KEY", "BASE44_SERVICE_TOKEN"]:
    resp = requests.put(
        f"https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/actions/secrets/{secret}",
        headers=headers,
        json={"encrypted_value": enc, "key_id": pk["key_id"]}
    )
    print(f"{secret}: HTTP {resp.status_code} {'OK' if resp.status_code in [201,204] else 'FAIL'}")

# Script dosyalarindaki hardcode token'i de guncelle
scripts = ["scripts/bot2_runner.py", "scripts/btc_signal_bot.py"]
for script in scripts:
    url = f"https://api.github.com/repos/ylcnmrtsr/trading-bots-cron/contents/{script}"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(f"{script}: dosya bulunamadi, atlaniyor")
        continue
    d = r.json()
    content = base64.b64decode(d["content"]).decode()
    match = re.search(r'BASE44_TOKEN = "([^"]+)"', content)
    if match:
        new_content = content.replace(match.group(0), f'BASE44_TOKEN = "{current_token}"')
        new_b64 = base64.b64encode(new_content.encode()).decode()
        resp = requests.put(url, headers=headers, json={
            "message": "chore: auto-refresh BASE44 token [skip ci]",
            "content": new_b64,
            "sha": d["sha"]
        })
        print(f"{script}: HTTP {resp.status_code} {'OK' if resp.status_code in [200,201] else 'FAIL'}")
    else:
        print(f"{script}: BASE44_TOKEN pattern bulunamadi")

print("Token yenileme tamamlandi!")
