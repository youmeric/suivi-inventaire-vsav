"""Connexion : mot de passe principal (ADMIN_PASSWORD) ou code personnel de chaque équipier.

Un équipier saisit et retire des lots et fait les vérifications ; une personne « gestion »
(et le compte principal) gère aussi l'inventaire, les personnes, les alertes et le journal.
Session : cookie signé. Régénérer un code ou changer ADMIN_PASSWORD coupe les sessions."""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request

from . import db

COOKIE_NAME = "inventaire_session"
SESSION_SECONDS = 180 * 24 * 3600  # un téléphone de garde reste connecté
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
PRINCIPAL_NAME = "Administrateur"

_secret: str | None = None


def _get_secret() -> bytes:
    global _secret
    if _secret is None:
        _secret = os.environ.get("SECRET_KEY") or db.get_or_create_secret()
    return _secret.encode()


def admin_password() -> str | None:
    return os.environ.get("ADMIN_PASSWORD") or None


def check_password(candidate: str) -> bool:
    expected = admin_password()
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def _fingerprint(secret_part: str) -> str:
    return hashlib.sha256(secret_part.encode()).hexdigest()[:12]


def _sign(payload: str) -> str:
    return hmac.new(_get_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(personne: dict | None = None) -> str:
    """Compte principal : « 0.<empreinte du mot de passe> » ; équipier : « <id>.<empreinte du code> »."""
    expires = int(time.time()) + SESSION_SECONDS
    if personne:
        payload = f"{personne['id']}.{_fingerprint(personne['code'] or '')}.{expires}"
    else:
        payload = f"0.{_fingerprint(admin_password() or '')}.{expires}"
    return f"{payload}.{_sign(payload)}"


def current_user(request: Request) -> dict | None:
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    parts = (request.cookies.get(COOKIE_NAME) or "").split(".")
    if len(parts) == 4:
        payload = ".".join(parts[:3])
        try:
            pid, expires = int(parts[0]), int(parts[2])
        except ValueError:
            pid, expires = -1, 0
        if expires > time.time() and hmac.compare_digest(parts[3], _sign(payload)):
            if pid == 0:
                if admin_password() and parts[1] == _fingerprint(admin_password() or ""):
                    user = {"id": 0, "nom": PRINCIPAL_NAME, "gestion": True, "principal": True}
            else:
                p = db.get_personne(pid)
                if p and p["actif"] and p["code"] and parts[1] == _fingerprint(p["code"]):
                    user = {"id": p["id"], "nom": p["nom"], "gestion": bool(p["gestion"]), "principal": False}
    request.state.user = user
    return user


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Connexion requise")
    return user


def require_gestion(request: Request) -> dict:
    user = require_user(request)
    if not user["gestion"]:
        raise HTTPException(status_code=403, detail="Réservé aux personnes chargées de la gestion")
    return user


# ---- limite des essais ratés (codes et mot de passe) ----

_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 10
ATTEMPT_WINDOW = 600  # 10 essais ratés par 10 minutes et par adresse IP


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _attempts.get(ip, []) if now - t < ATTEMPT_WINDOW]
    _attempts[ip] = recent
    return len(recent) >= MAX_ATTEMPTS


def record_failure(ip: str) -> None:
    _attempts.setdefault(ip, []).append(time.time())


def client_ip(request: Request) -> str:
    # derrière Nginx Proxy Manager, l'adresse réelle arrive dans X-Forwarded-For
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
