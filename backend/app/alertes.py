"""État des articles (péremption, quantité) et message d'alerte WhatsApp quotidien."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

from . import db, whatsapp

log = logging.getLogger("inventaire")

LIBELLES = {"perime": "Périmé", "remplacer": "À remplacer", "surveiller": "À surveiller", "ok": "À jour",
            "a_saisir": "Date à saisir", "manquant": "Incomplet"}
GRAVITE = ["perime", "remplacer", "a_saisir", "manquant", "surveiller", "ok"]


def jours_restants(peremption: str, today: date | None = None) -> int:
    return (date.fromisoformat(peremption) - (today or date.today())).days


def etat_lot(jours: int, delai_remplacement: int, delai_surveillance: int) -> str:
    if jours < 0:
        return "perime"
    if jours <= delai_remplacement:
        return "remplacer"
    if jours <= delai_surveillance:
        return "surveiller"
    return "ok"


def inventaire(vehicule_id: int | None = None, today: date | None = None) -> list[dict]:
    """Articles avec leurs lots en place, l'état de chaque lot et l'état de l'article."""
    rempl, surv = db.int_setting("delai_remplacement"), db.int_setting("delai_surveillance")
    lots_par_article: dict[int, list[dict]] = {}
    for lot in db.active_lots(vehicule_id):
        j = jours_restants(lot["peremption"], today)
        lots_par_article.setdefault(lot["article_id"], []).append(
            {"id": lot["id"], "peremption": lot["peremption"], "numero_lot": lot["numero_lot"],
             "quantite": lot["quantite"], "jours": j, "etat": etat_lot(j, rempl, surv),
             "ajoute_le": lot["ajoute_le"], "ajoute_par": lot["ajoute_par"]})
    out = []
    for a in db.list_articles(vehicule_id):
        lots = lots_par_article.get(a["id"], [])
        en_place = sum(l["quantite"] or 0 for l in lots) if any(l["quantite"] for l in lots) else None
        if not a["perissable"]:
            etat = "ok"
        elif not lots:
            etat = "a_saisir"
        else:
            etat = min((l["etat"] for l in lots), key=GRAVITE.index)
            if en_place is not None and en_place < a["quantite"] and GRAVITE.index(etat) > GRAVITE.index("manquant"):
                etat = "manquant"
        out.append({**a, "perissable": bool(a["perissable"]), "lots": lots, "en_place": en_place, "etat": etat})
    return out


def resume(articles: list[dict]) -> dict:
    counts = {k: 0 for k in LIBELLES}
    for a in articles:
        if a["perissable"]:
            counts[a["etat"]] += 1
    return counts


def fmt_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


# ---- Alertes WhatsApp ----------------------------------------------------------

def paliers() -> list[int]:
    out = []
    for part in (db.get_setting("alertes_paliers", "30,7,0") or "").split(","):
        try:
            out.append(max(0, int(part.strip())))
        except ValueError:
            pass
    return sorted(set(out), reverse=True) or [30, 7, 0]


def lots_a_signaler(today: date | None = None, rappel: bool = False) -> tuple[list[dict], dict[int, list[str]]]:
    """Lots qui ont atteint un palier pas encore signalé (J-30, J-7, J-0, puis « périmé »).
    Un lot saisi tard (déjà à J-5) n'est signalé qu'une fois : tous ses paliers dépassés sont
    marqués ensemble. Avec `rappel`, les lots périmés toujours en place sont repris."""
    today = today or date.today()
    steps = paliers()
    vehicules = {v["id"]: v["nom"] for v in db.list_vehicules()}
    articles = {a["id"]: a for a in db.list_articles()}
    items, to_mark = [], {}
    for lot in db.active_lots():
        art = articles.get(lot["article_id"])
        if not art or not art["perissable"]:
            continue
        j = jours_restants(lot["peremption"], today)
        done = {p for p in lot["alertes_envoyees"].split(",") if p}
        due = [str(s) for s in steps if j <= s and str(s) not in done]
        if j < 0 and "perime" not in done:
            due.append("perime")
        if due or (rappel and j < 0):
            items.append({"vehicule": vehicules.get(art["vehicule_id"], "?"), "designation": art["designation"],
                          "section": art["section"], "peremption": lot["peremption"], "numero_lot": lot["numero_lot"],
                          "quantite": lot["quantite"], "jours": j, "lot_id": lot["id"], "rappel": not due})
            if due:
                to_mark[lot["id"]] = due
    items.sort(key=lambda x: (x["vehicule"], x["jours"]))
    return items, to_mark


def message_alerte(items: list[dict], today: date | None = None) -> str:
    today = today or date.today()
    centre = (db.get_setting("nom_centre", "") or "").strip()
    lines = [f"🚑 *Suivi inventaire{' — ' + centre if centre else ''}* · {today.strftime('%d/%m/%Y')}"]
    current = None
    for it in items:
        if it["vehicule"] != current:
            current = it["vehicule"]
            lines += ["", f"*{current}*"]
        j = it["jours"]
        if j < 0:
            head = f"🔴 Périmé depuis {-j} j"
        elif j == 0:
            head = "🔴 Périme aujourd'hui"
        elif j <= 7:
            head = f"🟠 J-{j}"
        else:
            head = f"🟡 J-{j}"
        extra = "".join([f", lot {it['numero_lot']}" if it["numero_lot"] else "",
                         f", {it['quantite']} u." if it["quantite"] else ""])
        lines.append(f"{head} : {it['designation']} ({it['section']}) — {fmt_date(it['peremption'])}{extra}")
    url = (db.get_setting("url_site", "") or "").rstrip("/")
    if url:
        lines += ["", f"👉 {url}"]
    return "\n".join(lines)


def destinataires() -> list[tuple[str, str]]:
    """(libellé, identifiant WhatsApp) : le groupe réglé, puis les personnes qui reçoivent les alertes."""
    out: list[tuple[str, str]] = []
    groupe = (db.get_setting("whatsapp_groupe", "") or "").strip()
    if groupe and whatsapp.whatsapp_id(groupe):
        out.append(("Groupe WhatsApp", whatsapp.whatsapp_id(groupe)))
    for p in db.list_personnes():
        to = whatsapp.whatsapp_id(p["telephone"])
        if p["actif"] and p["alertes"] and to and all(to != x[1] for x in out):
            out.append((p["nom"], to))
    return out


def envoyer(message: str) -> tuple[list[str], list[str]]:
    """Envoie à tous les destinataires ; renvoie (réussis, erreurs)."""
    url = db.get_setting("whatsapp_bot_url", "") or ""
    if not url:
        raise whatsapp.BotError("Adresse du bot WhatsApp non réglée", 409)
    key = db.get_setting("whatsapp_bot_key", "") or ""
    dest = destinataires()
    if not dest:
        raise whatsapp.BotError("Aucun destinataire : réglez un groupe ou cochez « reçoit les alertes » sur une personne", 409)
    ok, errors = [], []
    for i, (label, to) in enumerate(dest):
        if i:
            time.sleep(2)  # comme pour les calendriers : on n'enchaîne pas les messages
        try:
            whatsapp.send(url, key, to, message)
            ok.append(label)
        except whatsapp.BotError as err:
            errors.append(f"{label} : {err}")
    return ok, errors


def executer(force: bool = False, today: date | None = None) -> dict:
    """Tournée d'alerte du jour. Ne marque les paliers comme envoyés que si au moins un
    destinataire a reçu le message, pour réessayer plus tard si le bot était coupé."""
    today = today or date.today()
    rappel = db.get_setting("alertes_rappel_lundi", "1") == "1" and today.weekday() == 0
    items, to_mark = lots_a_signaler(today, rappel=rappel)
    if not items:
        db.set_setting("alertes_derniere", today.isoformat())
        return {"envoye": False, "lots": 0, "message": "", "reussis": [], "erreurs": []}
    message = message_alerte(items, today)
    ok, errors = envoyer(message)
    if ok:
        db.mark_alerts(to_mark)
        db.set_setting("alertes_derniere", today.isoformat())
        db.log("Alertes automatiques" if not force else "Administrateur", "alerte.envoi", f"{len(items)} lot(s)",
               f"envoyé à {', '.join(ok)}" + (f" ; échecs : {'; '.join(errors)}" if errors else ""))
    else:
        db.set_setting("alertes_dernier_echec", str(int(time.time())))
        db.log("Alertes automatiques", "alerte.echec", f"{len(items)} lot(s)", "; ".join(errors))
    return {"envoye": bool(ok), "lots": len(items), "message": message, "reussis": ok, "erreurs": errors}


def doit_tourner(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if db.get_setting("alertes_actives", "1") != "1" or not db.get_setting("whatsapp_bot_url", ""):
        return False
    if now.hour < db.int_setting("alertes_heure"):
        return False
    if db.get_setting("alertes_derniere", "") == now.date().isoformat():
        return False
    # bot coupé : on réessaie au plus une fois par heure
    return time.time() - int(db.get_setting("alertes_dernier_echec", "0") or 0) >= 3600
