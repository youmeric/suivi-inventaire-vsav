"""Tests de l'API : python -m pytest tests (depuis la racine du dépôt)."""
from __future__ import annotations

import importlib
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "inventaire.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-admin")
    monkeypatch.setenv("BACKGROUND_TASKS", "0")
    from app import auth, db, alertes, main
    importlib.reload(db)
    db.DB_PATH = os.environ["DB_PATH"]
    auth._secret = None
    auth._attempts.clear()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        yield client, db, alertes


def login_admin(client):
    assert client.post("/api/login", json={"mot_de_passe": "secret-admin"}).status_code == 200


def article(client, vid, text):
    arts = client.get(f"/api/vehicules/{vid}/inventaire").json()["articles"]
    return next(a for a in arts if text in a["designation"])


def test_login_and_permissions(app_env):
    client, db, _ = app_env
    assert client.get("/api/tableau").status_code == 401
    assert client.post("/api/login", json={"mot_de_passe": "faux"}).status_code == 401
    login_admin(client)
    p = client.post("/api/admin/personnes", json={"nom": "Léa Martin", "telephone": "06 12 34 56 78"}).json()["personne"]
    client.post("/api/logout")
    client.cookies.clear()
    code = p["code"]
    assert client.post("/api/login", json={"code": f"{code[:4]}-{code[4:].lower()}"}).status_code == 200
    me = client.get("/api/moi").json()
    assert me["nom"] == "Léa Martin" and me["gestion"] is False
    assert client.get("/api/admin/personnes").status_code == 403
    assert client.get("/api/tableau").status_code == 200
    # un nouveau code coupe la session de l'équipier
    db.regenerate_code(p["id"])
    assert client.get("/api/moi").status_code == 401


def test_seed_inventory(app_env):
    client, _, _ = app_env
    login_admin(client)
    inv = client.get("/api/vehicules/1/inventaire").json()
    meds = [a["designation"] for a in inv["articles"] if a["categorie"] == "medicament"]
    assert any("Anapen 150" in m for m in meds) and any("Nyxoid" in m for m in meds)
    assert sum(1 for m in meds if "Ipratropium" in m or "Salbutamol" in m) == 4
    assert inv["resume"]["a_saisir"] == sum(1 for a in inv["articles"] if a["perissable"])


def test_lot_lifecycle_and_gtin(app_env):
    client, _, _ = app_env
    login_admin(client)
    a = article(client, 1, "Anapen 300")
    soon = (date.today() + timedelta(days=10)).isoformat()
    lid = client.post(f"/api/articles/{a['id']}/lots", json={"peremption": soon, "numero_lot": "AB1", "quantite": 2,
                                                             "gtin": "03400930000123"}).json()["id"]
    a = article(client, 1, "Anapen 300")
    assert a["etat"] == "remplacer" and a["gtin"] == "03400930000123" and a["en_place"] == 2
    assert [x["id"] for x in client.get("/api/vehicules/1/gtin/03400930000123").json()["articles"]] == [a["id"]]
    # nouveau lot qui remplace l'ancien en une fois
    later = (date.today() + timedelta(days=400)).isoformat()
    client.post(f"/api/articles/{a['id']}/lots", json={"peremption": later, "quantite": 2, "remplace": [lid]})
    a = article(client, 1, "Anapen 300")
    assert a["etat"] == "ok" and len(a["lots"]) == 1
    assert client.post(f"/api/lots/{lid}/retirer", json={"motif": "perime"}).status_code == 404
    # un véhicule copié reconnaît le même code
    vid = client.post("/api/admin/vehicules", json={"nom": "ASUP 2", "copie_de": 1}).json()["id"]
    assert len(client.get(f"/api/vehicules/{vid}/gtin/03400930000123").json()["articles"]) == 1
    assert article(client, vid, "Anapen 300")["lots"] == []


def test_incomplete_quantity(app_env):
    client, _, _ = app_env
    login_admin(client)
    a = article(client, 1, "Salbutamol 5 mg")
    client.post(f"/api/articles/{a['id']}/lots", json={"peremption": "2030-01-31", "quantite": 3})
    a = article(client, 1, "Salbutamol 5 mg")
    assert a["etat"] == "manquant" and a["en_place"] == 3
    lignes = client.get("/api/commande").json()["lignes"]
    assert any(l["article_id"] == a["id"] and l["quantite"] == 2 for l in lignes)


def test_alert_stages(app_env, monkeypatch):
    client, db, alertes = app_env
    login_admin(client)
    sent = []
    monkeypatch.setattr(alertes.whatsapp, "send", lambda url, key, to, msg: sent.append((to, msg)))
    monkeypatch.setattr(alertes.time, "sleep", lambda s: None)
    db.set_setting("whatsapp_bot_url", "http://bot:3000")
    db.set_setting("whatsapp_groupe", "120363000000000000@g.us")
    db.set_setting("alertes_rappel_lundi", "0")
    a = article(client, 1, "Nyxoid")
    today = date(2026, 10, 1)
    client.post(f"/api/articles/{a['id']}/lots", json={"peremption": "2026-10-25", "numero_lot": "N1"})
    # J-24 : palier 30 atteint
    res = alertes.executer(today=today)
    assert res["envoye"] and len(sent) == 1 and "J-24" in sent[0][1] and sent[0][0] == "120363000000000000@g.us"
    # le lendemain : rien de nouveau
    assert alertes.executer(today=today + timedelta(days=1))["lots"] == 0
    # J-7 puis J-0 puis périmé : un message à chaque palier
    for d, txt in ((18, "J-6"), (24, "aujourd'hui"), (25, "Périmé depuis 1 j")):
        res = alertes.executer(today=today + timedelta(days=d))
        assert res["lots"] == 1 and txt in sent[-1][1]
    assert len(sent) == 4
    assert alertes.executer(today=today + timedelta(days=26))["lots"] == 0
    # rappel du lundi pour un lot périmé toujours en place
    db.set_setting("alertes_rappel_lundi", "1")
    monday = date(2026, 11, 2)
    assert monday.weekday() == 0
    assert alertes.executer(today=monday)["lots"] == 1


def test_alert_retry_when_bot_down(app_env, monkeypatch):
    client, db, alertes = app_env
    login_admin(client)

    def down(*_args):
        raise alertes.whatsapp.BotError("Bot WhatsApp injoignable", 504)

    monkeypatch.setattr(alertes.whatsapp, "send", down)
    db.set_setting("whatsapp_bot_url", "http://bot:3000")
    db.set_setting("whatsapp_groupe", "120363000000000000@g.us")
    a = article(client, 1, "Anapen 150")
    client.post(f"/api/articles/{a['id']}/lots", json={"peremption": (date.today() + timedelta(days=3)).isoformat()})
    res = alertes.executer()
    assert not res["envoye"] and res["erreurs"]
    # rien n'est marqué : le lot sera signalé au prochain essai
    assert alertes.lots_a_signaler()[0]
    assert db.get_setting("alertes_derniere", "") != date.today().isoformat()


def test_whatsapp_ids():
    from app.whatsapp import whatsapp_id
    assert whatsapp_id("06 12 34 56 78") == "33612345678@c.us"
    assert whatsapp_id("+33 6 12 34 56 78") == "33612345678@c.us"
    assert whatsapp_id("120363000000000000@g.us") == "120363000000000000@g.us"
    assert whatsapp_id("12") is None
