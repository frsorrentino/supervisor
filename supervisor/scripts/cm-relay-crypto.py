#!/usr/bin/env python3
"""La parte crittografica di cm-relay (fase 1 dell'app polso, design 12/09/2026 §1 e §5).

- Ogni documento sul bus e' {"v": 1, "enc": "<base64>"}: AES-256-GCM sul JSON intero (nonce 12 byte + ciphertext
  e tag), AAD fissa `claude-master-relay-v1`. Firebase vede solo timestamp e dimensioni.
- La chiave (32 byte, hex) sta in <relay.dir>/key, 0600; nasce dal pairing: X25519 da entrambe le parti,
  HKDF-SHA256 sul segreto condiviso; `check_code` (HMAC della chiave sul codice a 6 cifre) prova che le due
  parti hanno derivato la stessa chiave senza mostrarla.
- Il service account di Firebase (JSON, 0600, mai nel repo) firma un JWT RS256 e lo scambia con un access
  token OAuth2 (cache su file): niente SDK.

Dipendenza morbida: `cryptography` (AES-GCM, X25519, HKDF, RSA). Se manca: ImportError con messaggio chiaro.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path

AAD = b"claude-master-relay-v1"
HKDF_INFO = b"claude-master-relay-v1"
SCOPES = "https://www.googleapis.com/auth/firebase.database https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/firebase.messaging"


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.asymmetric import x25519, padding
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes, serialization
    except ImportError as e:
        raise ImportError("cm-relay: serve il modulo Python `cryptography` (pip install cryptography / apt install python3-cryptography)") from e
    return AESGCM, x25519, padding, HKDF, hashes, serialization


# ------------------------------------------------------------------ chiave
def new_key():
    return secrets.token_bytes(32)


def key_path(directory):
    return Path(directory) / "key"


def load_key(directory):
    """La chiave dal file, o None se manca o e' rotta."""
    try:
        raw = key_path(directory).read_text().strip()
        k = bytes.fromhex(raw)
        return k if len(k) == 32 else None
    except (OSError, ValueError):
        return None


def save_key(directory, key):
    p = key_path(directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.hex() + "\n")
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    return p


# ------------------------------------------------------------------ blob
def encrypt(obj, key):
    AESGCM = _crypto()[0]
    nonce = secrets.token_bytes(12)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    ct = AESGCM(key).encrypt(nonce, data, AAD)
    return {"v": 1, "enc": base64.b64encode(nonce + ct).decode()}


def encrypt_raw(data, key):
    """1.34: byte grezzi nella stessa busta {v, enc} (AES-GCM, stesso AAD), senza JSON ne' base64 interni: i pezzi di
    /file/<id>/parts/<k>."""
    AESGCM = _crypto()[0]
    nonce = secrets.token_bytes(12)
    return {"v": 1, "enc": base64.b64encode(nonce + AESGCM(key).encrypt(nonce, bytes(data), AAD)).decode()}


def decrypt_raw(doc, key):
    AESGCM = _crypto()[0]
    if not isinstance(doc, dict) or doc.get("v") != 1 or not doc.get("enc"):
        raise ValueError("documento non cifrato nella forma {v:1, enc}")
    try:
        raw = base64.b64decode(doc["enc"])
        return AESGCM(key).decrypt(raw[:12], raw[12:], AAD)
    except Exception as e:
        raise ValueError(f"decifratura fallita: {e.__class__.__name__}") from e


def decrypt(doc, key):
    """L'oggetto in chiaro; ValueError se la chiave e' sbagliata o il documento non e' nella forma attesa."""
    AESGCM = _crypto()[0]
    if not isinstance(doc, dict) or doc.get("v") != 1 or not doc.get("enc"):
        raise ValueError("documento non cifrato nella forma {v:1, enc}")
    try:
        raw = base64.b64decode(doc["enc"])
        data = AESGCM(key).decrypt(raw[:12], raw[12:], AAD)
    except Exception as e:   # InvalidTag, binascii.Error
        raise ValueError(f"decifratura fallita: {e.__class__.__name__}") from e
    return json.loads(data.decode())


# ------------------------------------------------------------------ pairing
def pair_keys():
    """(chiave privata X25519, pubblica in base64) di questa parte."""
    _, x25519, _, _, _, serialization = _crypto()
    priv = x25519.X25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, base64.b64encode(pub).decode()


def shared_key(priv, peer_pub_b64):
    """La chiave AES concordata: HKDF-SHA256 sul segreto X25519 (32 byte)."""
    _, x25519, _, HKDF, hashes, _ = _crypto()
    peer = x25519.X25519PublicKey.from_public_bytes(base64.b64decode(peer_pub_b64))
    secret = priv.exchange(peer)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(secret)


def check_code(key, code):
    """HMAC-SHA256(chiave, codice)[:16] in hex: l'orologio lo manda, il PC lo confronta."""
    return hmac.new(key, str(code).encode(), hashlib.sha256).hexdigest()[:16]


# ------------------------------------------------------------------ token OAuth2 del service account
def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sa_token(sa_path, token_url, cache_path, now=None):
    """L'access token per RTDB e FCM dal service account (JWT RS256 → token endpoint); cache su file finche'
    manca piu' di un minuto alla scadenza. Torna la stringa; solleva OSError/ValueError se il file manca."""
    now = now or time.time()
    cache_path = Path(cache_path)
    try:
        c = json.loads(cache_path.read_text())
        if c.get("token") and float(c.get("exp") or 0) - now > 60:
            return c["token"]
    except (OSError, ValueError):
        pass
    sa = json.loads(Path(sa_path).read_text())
    _, _, padding, _, hashes, serialization = _crypto()
    priv = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    iat = int(now)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({"iss": sa["client_email"], "scope": SCOPES, "aud": token_url, "iat": iat, "exp": iat + 3600}).encode())
    signing = f"{header}.{claims}".encode()
    sig = priv.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    jwt = f"{header}.{claims}.{_b64url(sig)}"
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt}).encode()
    req = urllib.request.Request(token_url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode())
    token = d["access_token"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"token": token, "exp": now + float(d.get("expires_in") or 3600)}, f)
    return token
