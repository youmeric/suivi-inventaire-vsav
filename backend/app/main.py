from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import alertes, auth, db, whatsapp
from .models import (ArticleIn, LoginIn, LotIn, PersonneIn, ReglagesIn, RetraitIn, VehiculeIn, VerificationIn)

mimetypes.add_type("application/manifest+json", ".webmanifest")
log = logging.getLogger("inventaire")


def _resolve_frontend_dir() -> Path:
    env = os.environ.get("FRONTEND_DIR")
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve()
    candidates += [here.parent.parent.parent / "frontend", here.parent.parent / "frontend"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


FRONTEND_DIR = _resolve_frontend_dir()


async def _background() -> None:
    """Toutes les 10 minutes : sauvegarde du jour (30 gardées) et alertes WhatsApp à l'heure réglée."""
    await asyncio.sleep(30)  # laisse le serveur démarrer
    while True:
        try:
            if os.environ.get("AUTO_BACKUP", "1") != "0" and not await asyncio.to_thread(db.backup_done_today):
                log.info("Sauvegarde créée : %s", await asyncio.to_thread(db.make_backup))
        except Exception:  # une sauvegarde ratée ne doit jamais arrêter le site
            log.exception("Sauvegarde automatique impossible")
        try:
            if await asyncio.to_thread(alertes.doit_tourner):
                res = await asyncio.to_thread(alertes.executer)
                log.info("Alertes : %s lot(s), envoyé=%s", res["lots"], res["envoye"])
        except whatsapp.BotError as err:
            db.set_setting("alertes_dernier_echec", str(int(datetime.now().timestamp())))
            log.warning("Alertes non envoyées : %s", err)
        except Exception:
            log.exception("Tournée d'alerte impossible")
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    task = asyncio.create_task(_background()) if os.environ.get("BACKGROUND_TASKS", "1") != "0" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Suivi inventaire VSAV", lifespan=lifespan)


def _asset_version() -> str:
    """Empreinte des scripts et styles : change à chaque mise à jour du site."""
    h = hashlib.sha1()
    for path in sorted(FRONTEND_DIR.glob("**/*")):
        if path.suffix in {".js", ".css", ".html", ".webmanifest"} and path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version() if FRONTEND_DIR.exists() else "dev"


@app.middleware("http")
async def no_stale_files(request: Request, call_next):
    # les navigateurs revérifient pages et scripts à chaque fois (304 léger si rien n'a changé)
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _log(user: dict, action: str, objet: str = "", details: str = "") -> None:
    db.log(user["nom"], action, objet, details)


# ---- Connexion ------------------------------------------------------------------

def _set_session(response: Response, personne: dict | None) -> None:
    response.set_cookie(auth.COOKIE_NAME, auth.make_token(personne), max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite="lax", secure=auth.COOKIE_SECURE)


@app.post("/api/login")
def login(payload: LoginIn, request: Request, response: Response):
    ip = auth.client_ip(request)
    if auth.too_many_attempts(ip):
        raise HTTPException(status_code=429, detail="Trop d'essais ratés. Réessayez dans 10 minutes.")
    if payload.mot_de_passe:
        if not auth.check_password(payload.mot_de_passe):
            auth.record_failure(ip)
            db.log("?", "connexion.echec", "mot de passe principal", ip)
            raise HTTPException(status_code=401, detail="Mot de passe incorrect")
        _set_session(response, None)
        db.log(auth.PRINCIPAL_NAME, "connexion", "", ip)
        return {"ok": True}
    code = "".join(ch for ch in payload.code.upper() if ch.isalnum())
    person = db.find_personne_by_code(code) if len(code) == 8 else None
    if not person:
        auth.record_failure(ip)
        raise HTTPException(status_code=401, detail="Code inconnu. Vérifiez les 8 caractères ou demandez un nouveau code.")
    _set_session(response, person)
    db.log(person["nom"], "connexion", "", ip)
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@app.get("/api/moi")
def me(user: dict = Depends(auth.require_user)):
    return {**user, "centre": db.get_setting("nom_centre", "") or ""}


# ---- Inventaire -----------------------------------------------------------------

def _vehicule_or_404(vid: int) -> dict:
    v = db.get_vehicule(vid)
    if not v:
        raise HTTPException(status_code=404, detail="Véhicule inconnu")
    return v


def _article_or_404(aid: int) -> dict:
    a = db.get_article(aid)
    if not a:
        raise HTTPException(status_code=404, detail="Article inconnu")
    return a


def _seuils() -> dict:
    return {"remplacement": db.int_setting("delai_remplacement"), "surveillance": db.int_setting("delai_surveillance")}


@app.get("/api/tableau")
def dashboard(_: dict = Depends(auth.require_user)):
    """Par véhicule : compteurs, lots à traiter et dernière vérification."""
    out = []
    for v in db.list_vehicules():
        arts = alertes.inventaire(v["id"])
        lots = [{**l, "article_id": a["id"], "designation": a["designation"], "section": a["section"]}
                for a in arts if a["perissable"] for l in a["lots"] if l["etat"] in {"perime", "remplacer"}]
        lots.sort(key=lambda x: x["jours"])
        verifs = db.list_verifications(v["id"], 1)
        out.append({**v, "resume": alertes.resume(arts), "lots": lots,
                    "incomplets": [{"id": a["id"], "designation": a["designation"], "section": a["section"],
                                    "en_place": a["en_place"], "quantite": a["quantite"]}
                                   for a in arts if a["etat"] == "manquant"],
                    "derniere_verification": verifs[0] if verifs else None})
    return {"vehicules": out, "seuils": _seuils(), "aujourdhui": date.today().isoformat()}


@app.get("/api/vehicules")
def vehicules(_: dict = Depends(auth.require_user)):
    return {"vehicules": db.list_vehicules()}


@app.get("/api/vehicules/{vid}/inventaire")
def vehicle_inventory(vid: int, _: dict = Depends(auth.require_user)):
    v = _vehicule_or_404(vid)
    arts = alertes.inventaire(vid)
    return {"vehicule": v, "articles": arts, "resume": alertes.resume(arts), "seuils": _seuils()}


@app.get("/api/vehicules/{vid}/gtin/{gtin}")
def lookup_gtin(vid: int, gtin: str, _: dict = Depends(auth.require_user)):
    return {"articles": [{"id": a["id"], "designation": a["designation"], "section": a["section"]}
                         for a in db.find_by_gtin(gtin, vid)]}


@app.post("/api/articles/{aid}/lots")
def add_lot(aid: int, payload: LotIn, user: dict = Depends(auth.require_user)):
    a = _article_or_404(aid)
    for old in payload.remplace:
        lot = db.get_lot(old)
        if lot and lot["article_id"] == aid and not lot["retire_le"]:
            db.retire_lot(old, "perime", user["nom"])
    lid = db.add_lot(aid, payload.peremption.isoformat(), payload.numero_lot.strip(), payload.quantite, user["nom"])
    if payload.gtin and payload.gtin != a["gtin"]:
        db.set_article_gtin(aid, payload.gtin)
    details = ", ".join(x for x in [f"périme le {alertes.fmt_date(payload.peremption.isoformat())}",
                                    f"lot {payload.numero_lot}" if payload.numero_lot else "",
                                    f"{payload.quantite} u." if payload.quantite else "",
                                    f"remplace {len(payload.remplace)} lot(s)" if payload.remplace else "",
                                    "scanné" if payload.gtin else ""] if x)
    _log(user, "lot.ajout", _label(a), details)
    return {"id": lid}


@app.post("/api/lots/{lid}/retirer")
def remove_lot(lid: int, payload: RetraitIn, user: dict = Depends(auth.require_user)):
    lot = db.get_lot(lid)
    if not lot or lot["retire_le"]:
        raise HTTPException(status_code=404, detail="Lot déjà retiré ou inconnu")
    if payload.motif not in db.MOTIFS:
        raise HTTPException(status_code=422, detail="Motif inconnu")
    db.retire_lot(lid, payload.motif, user["nom"])
    a = db.get_article(lot["article_id"]) or {"designation": "?", "vehicule_id": 0}
    _log(user, "lot.retrait", _label(a),
         f"{db.MOTIFS[payload.motif]} (péremption {alertes.fmt_date(lot['peremption'])}"
         f"{', lot ' + lot['numero_lot'] if lot['numero_lot'] else ''})")
    return {"ok": True}


def _label(a: dict) -> str:
    v = db.get_vehicule(a.get("vehicule_id") or 0)
    return f"{v['nom'] + ' · ' if v else ''}{a['designation']}"


# ---- Vérifications ----------------------------------------------------------------

@app.post("/api/verifications")
def add_verification(payload: VerificationIn, user: dict = Depends(auth.require_user)):
    v = _vehicule_or_404(payload.vehicule_id)
    arts = {a["id"]: a for a in db.list_articles(v["id"])}
    manquants = [{"id": i, "designation": arts[i]["designation"], "section": arts[i]["section"]}
                 for i in payload.manquants if i in arts]
    db.add_verification(v["id"], user["nom"], manquants, payload.note.strip())
    _log(user, "verification", v["nom"],
         f"{len(manquants)} manquant(s)" + (f" : {', '.join(m['designation'] for m in manquants[:10])}" if manquants else "")
         + (f" — {payload.note.strip()}" if payload.note.strip() else ""))
    sent = None
    if manquants and db.get_setting("alertes_verification", "1") == "1" and db.get_setting("whatsapp_bot_url", ""):
        lines = [f"📋 *Vérification {v['nom']}* par {user['nom']} : {len(manquants)} manquant(s)"]
        lines += [f"• {m['designation']} ({m['section']})" for m in manquants]
        if payload.note.strip():
            lines += ["", payload.note.strip()]
        try:
            ok, _errors = alertes.envoyer("\n".join(lines))
            sent = bool(ok)
        except whatsapp.BotError:
            sent = False
    return {"ok": True, "manquants": len(manquants), "whatsapp": sent}


@app.get("/api/vehicules/{vid}/verifications")
def vehicle_verifications(vid: int, _: dict = Depends(auth.require_user)):
    _vehicule_or_404(vid)
    return {"verifications": db.list_verifications(vid)}


# ---- Liste de commande ------------------------------------------------------------

def _commande(jours: int) -> list[dict]:
    """À commander : lots qui périment dans `jours` jours (ou déjà périmés), quantités incomplètes."""
    vehicules = {v["id"]: v["nom"] for v in db.list_vehicules()}
    out = []
    for a in alertes.inventaire():
        if not a["perissable"]:
            continue
        for l in a["lots"]:
            if l["jours"] <= jours:
                out.append({"vehicule": vehicules.get(a["vehicule_id"], "?"), "article_id": a["id"],
                            "designation": a["designation"], "section": a["section"], "categorie": a["categorie"],
                            "quantite": l["quantite"] or a["quantite"], "raison": "périmé" if l["jours"] < 0 else
                            f"périme le {alertes.fmt_date(l['peremption'])}", "jours": l["jours"]})
        if a["en_place"] is not None and a["en_place"] < a["quantite"]:
            out.append({"vehicule": vehicules.get(a["vehicule_id"], "?"), "article_id": a["id"],
                        "designation": a["designation"], "section": a["section"], "categorie": a["categorie"],
                        "quantite": a["quantite"] - a["en_place"], "raison": "quantité incomplète", "jours": None})
    out.sort(key=lambda x: (x["categorie"] != "medicament", x["designation"].lower(), x["vehicule"]))
    return out


@app.get("/api/commande")
def order_list(jours: int = 45, _: dict = Depends(auth.require_user)):
    return {"lignes": _commande(max(0, min(jours, 365))), "jours": jours}


@app.get("/api/commande.csv")
def order_csv(jours: int = 45, _: dict = Depends(auth.require_user)):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Catégorie", "Désignation", "Quantité", "Véhicule", "Emplacement", "Raison"])
    for l in _commande(max(0, min(jours, 365))):
        w.writerow(["Médicament" if l["categorie"] == "medicament" else "Matériel", l["designation"], l["quantite"],
                    l["vehicule"], l["section"], l["raison"]])
    name = f"commande-{date.today().isoformat()}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---- Gestion : véhicules et articles ---------------------------------------------------

@app.post("/api/admin/vehicules")
def admin_add_vehicle(payload: VehiculeIn, user: dict = Depends(auth.require_gestion)):
    src = _vehicule_or_404(payload.copie_de) if payload.copie_de else None
    vid = db.add_vehicule(payload.nom.strip(), payload.description.strip(), src["id"] if src else None)
    _log(user, "vehicule.ajout", payload.nom, f"copie de {src['nom']}" if src else "inventaire ASUP de référence")
    return {"id": vid}


@app.put("/api/admin/vehicules/{vid}")
def admin_update_vehicle(vid: int, payload: VehiculeIn, user: dict = Depends(auth.require_gestion)):
    v = _vehicule_or_404(vid)
    db.update_vehicule(vid, payload.nom.strip(), payload.description.strip())
    _log(user, "vehicule.modif", v["nom"], f"→ {payload.nom}" if payload.nom != v["nom"] else "")
    return {"ok": True}


@app.delete("/api/admin/vehicules/{vid}")
def admin_delete_vehicle(vid: int, user: dict = Depends(auth.require_gestion)):
    v = _vehicule_or_404(vid)
    if len(db.list_vehicules()) <= 1:
        raise HTTPException(status_code=409, detail="Il faut garder au moins un véhicule")
    db.delete_vehicule(vid)
    _log(user, "vehicule.suppression", v["nom"])
    return {"ok": True}


@app.post("/api/admin/vehicules/{vid}/articles")
def admin_add_article(vid: int, payload: ArticleIn, user: dict = Depends(auth.require_gestion)):
    v = _vehicule_or_404(vid)
    aid = db.add_article(vid, payload.model_dump())
    _log(user, "article.ajout", f"{v['nom']} · {payload.designation}", payload.section)
    return {"id": aid}


@app.put("/api/admin/articles/{aid}")
def admin_update_article(aid: int, payload: ArticleIn, user: dict = Depends(auth.require_gestion)):
    a = _article_or_404(aid)
    new = payload.model_dump()
    db.update_article(aid, new)
    changes = [f"{k} : {a[k]} → {new[k]}" for k in ("section", "designation", "quantite", "perissable", "categorie", "gtin")
               if str(int(a[k]) if k == "perissable" else a[k]) != str(int(new[k]) if k == "perissable" else new[k])]
    _log(user, "article.modif", _label(a), "; ".join(changes))
    return {"ok": True}


@app.delete("/api/admin/articles/{aid}")
def admin_delete_article(aid: int, user: dict = Depends(auth.require_gestion)):
    a = _article_or_404(aid)
    db.delete_article(aid)
    _log(user, "article.suppression", _label(a))
    return {"ok": True}


# ---- Gestion : personnes ---------------------------------------------------------------

def _personne_view(p: dict) -> dict:
    return {**p, "alertes": bool(p["alertes"]), "gestion": bool(p["gestion"]), "actif": bool(p["actif"]),
            "whatsapp_ok": bool(whatsapp.whatsapp_id(p["telephone"]))}


def _personne_or_404(pid: int) -> dict:
    p = db.get_personne(pid)
    if not p:
        raise HTTPException(status_code=404, detail="Personne inconnue")
    return p


@app.get("/api/admin/personnes")
def admin_people(_: dict = Depends(auth.require_gestion)):
    return {"personnes": [_personne_view(p) for p in db.list_personnes()]}


@app.post("/api/admin/personnes")
def admin_add_person(payload: PersonneIn, user: dict = Depends(auth.require_gestion)):
    pid = db.add_personne(payload.model_dump())
    _log(user, "personne.ajout", payload.nom)
    return {"personne": _personne_view(db.get_personne(pid))}


@app.put("/api/admin/personnes/{pid}")
def admin_update_person(pid: int, payload: PersonneIn, user: dict = Depends(auth.require_gestion)):
    p = _personne_or_404(pid)
    db.update_personne(pid, payload.model_dump())
    _log(user, "personne.modif", p["nom"])
    return {"personne": _personne_view(db.get_personne(pid))}


@app.delete("/api/admin/personnes/{pid}")
def admin_delete_person(pid: int, user: dict = Depends(auth.require_gestion)):
    p = _personne_or_404(pid)
    db.delete_personne(pid)
    _log(user, "personne.suppression", p["nom"])
    return {"ok": True}


@app.post("/api/admin/personnes/{pid}/code")
def admin_new_code(pid: int, user: dict = Depends(auth.require_gestion)):
    p = _personne_or_404(pid)
    db.regenerate_code(pid)
    _log(user, "personne.code", p["nom"], "nouveau code (l'ancien ne marche plus)")
    return {"personne": _personne_view(db.get_personne(pid))}


@app.post("/api/admin/personnes/{pid}/envoyer-acces")
def admin_send_access(pid: int, request: Request, user: dict = Depends(auth.require_gestion)):
    p = _personne_or_404(pid)
    to = whatsapp.whatsapp_id(p["telephone"])
    if not to:
        raise HTTPException(status_code=422, detail="Pas de numéro de téléphone valable")
    url = db.get_setting("whatsapp_bot_url", "") or ""
    if not url:
        raise HTTPException(status_code=409, detail="Adresse du bot WhatsApp non réglée")
    site = (db.get_setting("url_site", "") or str(request.base_url)).rstrip("/")
    code = p["code"] or ""
    msg = (f"Bonjour {p['nom'].split(' ')[0]} 👋\n\nVoici ton accès au suivi de l'inventaire des VSAV :\n"
           f"{site}/#acces={code}\n\nOu saisis le code *{code[:4]}-{code[4:]}* sur la page de connexion.\n"
           "Il est personnel : ne le transfère pas.")
    try:
        whatsapp.send(url, db.get_setting("whatsapp_bot_key", "") or "", to, msg)
    except whatsapp.BotError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err
    _log(user, "personne.envoi_acces", p["nom"], f"par WhatsApp au {p['telephone']}")
    return {"ok": True}


# ---- Gestion : réglages, alertes, journal, sauvegardes ----------------------------------

def _reglages_view() -> dict:
    s = db.all_settings()
    return {
        "delai_remplacement": int(s.get("delai_remplacement", 30)), "delai_surveillance": int(s.get("delai_surveillance", 90)),
        "alertes_actives": s.get("alertes_actives") == "1", "alertes_paliers": s.get("alertes_paliers", ""),
        "alertes_heure": int(s.get("alertes_heure", 8)), "alertes_rappel_lundi": s.get("alertes_rappel_lundi") == "1",
        "alertes_verification": s.get("alertes_verification") == "1",
        "whatsapp_bot_url": s.get("whatsapp_bot_url", ""), "whatsapp_bot_key_definie": bool(s.get("whatsapp_bot_key")),
        "whatsapp_groupe": s.get("whatsapp_groupe", ""), "url_site": s.get("url_site", ""),
        "nom_centre": s.get("nom_centre", ""), "alertes_derniere": s.get("alertes_derniere", ""),
        "destinataires": [label for label, _ in alertes.destinataires()],
    }


@app.get("/api/admin/reglages")
def admin_settings(_: dict = Depends(auth.require_gestion)):
    return _reglages_view()


@app.put("/api/admin/reglages")
def admin_save_settings(payload: ReglagesIn, user: dict = Depends(auth.require_gestion)):
    before = db.all_settings()
    data = payload.model_dump()
    key = data.pop("whatsapp_bot_key")
    values = {k: ("1" if v else "0") if isinstance(v, bool) else str(v).strip() for k, v in data.items()}
    if key is not None:
        values["whatsapp_bot_key"] = key.strip()
    changes = []
    for k, v in values.items():
        if before.get(k, "") != v:
            db.set_setting(k, v)
            changes.append(k if k == "whatsapp_bot_key" else f"{k} : {before.get(k, '')} → {v}")
    if changes:
        _log(user, "reglages", "", "; ".join(changes))
    return _reglages_view()


@app.get("/api/admin/whatsapp/etat")
def admin_bot_state(_: dict = Depends(auth.require_gestion)):
    url = db.get_setting("whatsapp_bot_url", "") or ""
    if not url:
        return {"configure": False, "pret": False, "erreur": "Adresse du bot non réglée"}
    try:
        h = whatsapp.health(url, db.get_setting("whatsapp_bot_key", "") or "")
    except whatsapp.BotError as err:
        return {"configure": True, "pret": False, "erreur": str(err)}
    ready = bool(h.get("ready", h.get("ok")))
    return {"configure": True, "pret": ready,
            "erreur": "" if ready else "Le bot répond mais WhatsApp n'est pas connecté (QR à rescanner ?)"}


@app.get("/api/admin/alertes/apercu")
def admin_alert_preview(_: dict = Depends(auth.require_gestion)):
    items, _marks = alertes.lots_a_signaler()
    return {"lots": len(items), "message": alertes.message_alerte(items) if items else "",
            "destinataires": [label for label, _ in alertes.destinataires()]}


@app.post("/api/admin/alertes/test")
def admin_alert_test(user: dict = Depends(auth.require_gestion)):
    try:
        ok, errors = alertes.envoyer("✅ Test du suivi d'inventaire VSAV : les alertes de péremption arriveront ici.")
    except whatsapp.BotError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err
    _log(user, "alerte.test", "", f"reçu par {', '.join(ok) or 'personne'}" + (f" ; échecs : {'; '.join(errors)}" if errors else ""))
    return {"reussis": ok, "erreurs": errors}


@app.post("/api/admin/alertes/executer")
def admin_alert_run(user: dict = Depends(auth.require_gestion)):
    try:
        res = alertes.executer(force=True)
    except whatsapp.BotError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err
    if not res["lots"]:
        _log(user, "alerte.envoi", "", "rien à signaler")
    return res


@app.get("/api/admin/journal")
def admin_journal(before: int | None = None, q: str = "", limit: int = 200, _: dict = Depends(auth.require_gestion)):
    return {"lignes": db.list_journal(max(1, min(limit, 500)), before, q.strip())}


@app.get("/api/admin/sauvegardes")
def admin_backups(_: dict = Depends(auth.require_gestion)):
    return {"sauvegardes": db.list_backups(), "garde": db.BACKUP_KEEP}


@app.post("/api/admin/sauvegardes")
def admin_backup_now(user: dict = Depends(auth.require_gestion)):
    name = db.make_backup("manuelle")
    _log(user, "sauvegarde", name)
    return {"nom": name, "sauvegardes": db.list_backups()}


@app.get("/api/admin/sauvegardes/{name}")
def admin_download_backup(name: str, _: dict = Depends(auth.require_gestion)):
    if not name.startswith("inventaire-") or not name.endswith(".db") or "/" in name or ".." in name:
        raise HTTPException(status_code=404, detail="Sauvegarde inconnue")
    path = Path(db.backup_dir()) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Sauvegarde inconnue")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


# ---- Pages ---------------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index_page():
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8").replace("__V__", ASSET_VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/api/sante", include_in_schema=False)
def health():
    return {"ok": True}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
