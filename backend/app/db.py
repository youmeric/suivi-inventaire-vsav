"""Accès SQLite : véhicules, articles de l'inventaire, lots avec date de péremption,
vérifications, personnes (codes d'accès, alertes WhatsApp), réglages et journal."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .inventaire import ASUP1

DB_PATH = os.environ.get("DB_PATH", "db/inventaire.db")

MOTIFS = {"perime": "périmé", "utilise": "utilisé en intervention", "erreur": "erreur de saisie", "autre": "autre"}
CATEGORIES = {"medicament", "materiel"}

DEFAULT_SETTINGS = {
    "delai_remplacement": "30",   # jours : « à remplacer » (orange)
    "delai_surveillance": "90",   # jours : « à surveiller » (jaune)
    "alertes_actives": "1",
    "alertes_paliers": "30,7,0",  # un message WhatsApp quand un lot atteint J-30, J-7, puis le jour même
    "alertes_heure": "8",
    "alertes_rappel_lundi": "1",  # chaque lundi, rappel des lots périmés toujours en place
    "alertes_verification": "1",  # message quand une vérification signale du matériel manquant
    "whatsapp_bot_url": "",
    "whatsapp_bot_key": "",
    "whatsapp_groupe": "",
    "url_site": "",
    "nom_centre": "",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vehicules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                ordre INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicule_id INTEGER NOT NULL REFERENCES vehicules(id) ON DELETE CASCADE,
                section TEXT NOT NULL,
                designation TEXT NOT NULL,
                quantite INTEGER NOT NULL DEFAULT 1,
                observation TEXT NOT NULL DEFAULT '',
                perissable INTEGER NOT NULL DEFAULT 0,
                categorie TEXT NOT NULL DEFAULT 'materiel',
                gtin TEXT NOT NULL DEFAULT '',
                ordre INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS articles_vehicule ON articles(vehicule_id);
            CREATE TABLE IF NOT EXISTS lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                peremption TEXT NOT NULL,
                numero_lot TEXT NOT NULL DEFAULT '',
                quantite INTEGER,
                ajoute_le TEXT NOT NULL,
                ajoute_par TEXT NOT NULL DEFAULT '',
                retire_le TEXT,
                retire_par TEXT,
                motif TEXT,
                alertes_envoyees TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS lots_article ON lots(article_id);
            CREATE TABLE IF NOT EXISTS verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicule_id INTEGER NOT NULL REFERENCES vehicules(id) ON DELETE CASCADE,
                faite_le TEXT NOT NULL,
                faite_par TEXT NOT NULL,
                manquants TEXT NOT NULL DEFAULT '[]',
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS personnes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                telephone TEXT NOT NULL DEFAULT '',
                code TEXT,
                alertes INTEGER NOT NULL DEFAULT 0,
                gestion INTEGER NOT NULL DEFAULT 0,
                actif INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quand TEXT NOT NULL,
                qui TEXT NOT NULL,
                action TEXT NOT NULL,
                objet TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT ''
            );
            """
        )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        if not conn.execute("SELECT 1 FROM vehicules LIMIT 1").fetchone():
            cur = conn.execute("INSERT INTO vehicules (nom, description, ordre) VALUES (?, ?, 0)",
                               ("ASUP 1", "VSSUAP — inventaire de novembre 2025"))
            _insert_inventory(conn, cur.lastrowid, ASUP1)
        conn.commit()


def _insert_inventory(conn, vehicule_id: int, sections) -> None:
    ordre = 0
    for section, lignes in sections:
        for designation, quantite, observation, perissable, categorie in lignes:
            ordre += 1
            conn.execute(
                "INSERT INTO articles (vehicule_id, section, designation, quantite, observation, perissable, categorie, ordre)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (vehicule_id, section, designation, quantite, observation, int(perissable), categorie, ordre),
            )


# ---- Réglages ------------------------------------------------------------------

def get_or_create_secret() -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'session_secret'").fetchone()
        if row:
            return row[0]
        value = secrets.token_hex(32)
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('session_secret', ?)", (value,))
        conn.commit()
        return conn.execute("SELECT value FROM settings WHERE key = 'session_secret'").fetchone()[0]


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def int_setting(key: str) -> int:
    try:
        return int(get_setting(key, DEFAULT_SETTINGS.get(key, "0")) or 0)
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, "0"))


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def all_settings() -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE key != 'session_secret'").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---- Véhicules ------------------------------------------------------------------

def list_vehicules() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM vehicules ORDER BY ordre, id").fetchall()
    return [dict(r) for r in rows]


def get_vehicule(vid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM vehicules WHERE id = ?", (vid,)).fetchone()
    return dict(row) if row else None


def add_vehicule(nom: str, description: str, copie_de: int | None = None) -> int:
    with get_conn() as conn:
        ordre = conn.execute("SELECT COALESCE(MAX(ordre), 0) + 1 FROM vehicules").fetchone()[0]
        vid = conn.execute("INSERT INTO vehicules (nom, description, ordre) VALUES (?, ?, ?)",
                           (nom, description, ordre)).lastrowid
        if copie_de:
            # on reprend la liste du matériel (et les codes des boîtes), jamais les lots
            conn.execute(
                "INSERT INTO articles (vehicule_id, section, designation, quantite, observation, perissable, categorie, gtin, ordre)"
                " SELECT ?, section, designation, quantite, observation, perissable, categorie, gtin, ordre"
                " FROM articles WHERE vehicule_id = ? ORDER BY ordre, id", (vid, copie_de))
        else:
            _insert_inventory(conn, vid, ASUP1)
        conn.commit()
    return vid


def update_vehicule(vid: int, nom: str, description: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE vehicules SET nom = ?, description = ? WHERE id = ?", (nom, description, vid))
        conn.commit()


def delete_vehicule(vid: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM vehicules WHERE id = ?", (vid,))
        conn.commit()


# ---- Articles et lots -------------------------------------------------------------

def list_articles(vehicule_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM articles" + (" WHERE vehicule_id = ?" if vehicule_id else "") + " ORDER BY vehicule_id, ordre, id"
    with get_conn() as conn:
        rows = conn.execute(sql, (vehicule_id,) if vehicule_id else ()).fetchall()
    return [dict(r) for r in rows]


def get_article(aid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
    return dict(row) if row else None


def add_article(vehicule_id: int, data: dict) -> int:
    with get_conn() as conn:
        # placé à la fin de sa section (ou à la fin de l'inventaire pour une nouvelle section)
        row = conn.execute("SELECT MAX(ordre) FROM articles WHERE vehicule_id = ? AND section = ?",
                           (vehicule_id, data["section"])).fetchone()
        if row[0] is not None:
            ordre = row[0] + 1
            conn.execute("UPDATE articles SET ordre = ordre + 1 WHERE vehicule_id = ? AND ordre >= ?", (vehicule_id, ordre))
        else:
            ordre = conn.execute("SELECT COALESCE(MAX(ordre), 0) + 1 FROM articles WHERE vehicule_id = ?",
                                 (vehicule_id,)).fetchone()[0]
        aid = conn.execute(
            "INSERT INTO articles (vehicule_id, section, designation, quantite, observation, perissable, categorie, gtin, ordre)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vehicule_id, data["section"], data["designation"], data["quantite"], data["observation"],
             int(data["perissable"]), data["categorie"], data.get("gtin", ""), ordre)).lastrowid
        conn.commit()
    return aid


def update_article(aid: int, data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET section = ?, designation = ?, quantite = ?, observation = ?, perissable = ?,"
            " categorie = ?, gtin = ? WHERE id = ?",
            (data["section"], data["designation"], data["quantite"], data["observation"], int(data["perissable"]),
             data["categorie"], data.get("gtin", ""), aid))
        conn.commit()


def set_article_gtin(aid: int, gtin: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE articles SET gtin = ? WHERE id = ?", (gtin, aid))
        conn.commit()


def delete_article(aid: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (aid,))
        conn.commit()


def find_by_gtin(gtin: str, vehicule_id: int) -> list[dict]:
    """Articles du véhicule portant ce code ; à défaut, ceux qui ont la même désignation
    qu'un article déjà associé à ce code dans un autre véhicule."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM articles WHERE vehicule_id = ? AND gtin = ? ORDER BY ordre",
                            (vehicule_id, gtin)).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT * FROM articles WHERE vehicule_id = ? AND perissable = 1 AND designation IN"
                " (SELECT designation FROM articles WHERE gtin = ?) ORDER BY ordre", (vehicule_id, gtin)).fetchall()
    return [dict(r) for r in rows]


def active_lots(vehicule_id: int | None = None) -> list[dict]:
    sql = ("SELECT l.*, a.vehicule_id FROM lots l JOIN articles a ON a.id = l.article_id WHERE l.retire_le IS NULL"
           + (" AND a.vehicule_id = ?" if vehicule_id else "") + " ORDER BY l.peremption, l.id")
    with get_conn() as conn:
        rows = conn.execute(sql, (vehicule_id,) if vehicule_id else ()).fetchall()
    return [dict(r) for r in rows]


def get_lot(lid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM lots WHERE id = ?", (lid,)).fetchone()
    return dict(row) if row else None


def add_lot(article_id: int, peremption: str, numero_lot: str, quantite: int | None, par: str) -> int:
    with get_conn() as conn:
        lid = conn.execute(
            "INSERT INTO lots (article_id, peremption, numero_lot, quantite, ajoute_le, ajoute_par) VALUES (?, ?, ?, ?, ?, ?)",
            (article_id, peremption, numero_lot, quantite, now_iso(), par)).lastrowid
        conn.commit()
    return lid


def retire_lot(lid: int, motif: str, par: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE lots SET retire_le = ?, retire_par = ?, motif = ? WHERE id = ? AND retire_le IS NULL",
                     (now_iso(), par, motif, lid))
        conn.commit()


def mark_alerts(sent: dict[int, list[str]]) -> None:
    with get_conn() as conn:
        for lid, paliers in sent.items():
            row = conn.execute("SELECT alertes_envoyees FROM lots WHERE id = ?", (lid,)).fetchone()
            if not row:
                continue
            done = {p for p in row[0].split(",") if p} | set(paliers)
            conn.execute("UPDATE lots SET alertes_envoyees = ? WHERE id = ?", (",".join(sorted(done)), lid))
        conn.commit()


# ---- Vérifications -----------------------------------------------------------------

def add_verification(vehicule_id: int, par: str, manquants: list[dict], note: str) -> int:
    with get_conn() as conn:
        vid = conn.execute(
            "INSERT INTO verifications (vehicule_id, faite_le, faite_par, manquants, note) VALUES (?, ?, ?, ?, ?)",
            (vehicule_id, now_iso(), par, json.dumps(manquants, ensure_ascii=False), note)).lastrowid
        conn.commit()
    return vid


def list_verifications(vehicule_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM verifications WHERE vehicule_id = ? ORDER BY id DESC LIMIT ?",
                            (vehicule_id, limit)).fetchall()
    return [{**dict(r), "manquants": json.loads(r["manquants"])} for r in rows]


# ---- Personnes ------------------------------------------------------------------------

def new_code() -> str:
    # 8 caractères sans 0/O ni 1/I pour éviter les confusions à la saisie
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if not find_personne_by_code(code):
            return code


def list_personnes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM personnes ORDER BY nom COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def get_personne(pid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM personnes WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def find_personne_by_code(code: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM personnes WHERE code = ? AND actif = 1", (code,)).fetchone()
    return dict(row) if row else None


def add_personne(data: dict) -> int:
    with get_conn() as conn:
        pid = conn.execute(
            "INSERT INTO personnes (nom, telephone, code, alertes, gestion, actif) VALUES (?, ?, ?, ?, ?, ?)",
            (data["nom"], data["telephone"], new_code(), int(data["alertes"]), int(data["gestion"]), int(data["actif"]))
        ).lastrowid
        conn.commit()
    return pid


def update_personne(pid: int, data: dict) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE personnes SET nom = ?, telephone = ?, alertes = ?, gestion = ?, actif = ? WHERE id = ?",
                     (data["nom"], data["telephone"], int(data["alertes"]), int(data["gestion"]), int(data["actif"]), pid))
        conn.commit()


def regenerate_code(pid: int) -> str:
    code = new_code()
    with get_conn() as conn:
        conn.execute("UPDATE personnes SET code = ? WHERE id = ?", (code, pid))
        conn.commit()
    return code


def delete_personne(pid: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM personnes WHERE id = ?", (pid,))
        conn.commit()


# ---- Journal --------------------------------------------------------------------------

def log(qui: str, action: str, objet: str = "", details: str = "") -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO journal (quand, qui, action, objet, details) VALUES (?, ?, ?, ?, ?)",
                     (now_iso(), qui, action, objet, details))
        conn.commit()


def list_journal(limit: int = 200, before: int | None = None, q: str = "") -> list[dict]:
    sql, args = "SELECT * FROM journal WHERE 1 = 1", []
    if before:
        sql += " AND id < ?"
        args.append(before)
    if q:
        sql += " AND (qui LIKE ? OR action LIKE ? OR objet LIKE ? OR details LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# ---- Sauvegardes ----------------------------------------------------------------------

BACKUP_KEEP = 30


def backup_dir() -> str:
    return os.environ.get("BACKUP_DIR") or os.path.join(os.path.dirname(DB_PATH) or ".", "sauvegardes")


def make_backup(tag: str | None = None) -> str:
    """Copie cohérente de la base (API de sauvegarde SQLite), même pendant l'utilisation."""
    os.makedirs(backup_dir(), exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    name = f"inventaire-{stamp}{'-' + tag if tag else ''}.db"
    with get_conn() as conn:
        dest = sqlite3.connect(os.path.join(backup_dir(), name))
        try:
            conn.backup(dest)
        finally:
            dest.close()
    prune_backups()
    return name


def list_backups() -> list[dict]:
    folder = backup_dir()
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder), reverse=True):
        if name.startswith("inventaire-") and name.endswith(".db"):
            st = os.stat(os.path.join(folder, name))
            out.append({"nom": name, "taille": st.st_size,
                        "date": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return out


def prune_backups(keep: int = BACKUP_KEEP) -> None:
    for item in list_backups()[keep:]:
        try:
            os.remove(os.path.join(backup_dir(), item["nom"]))
        except OSError:
            pass


def backup_done_today() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    return any(b["nom"].startswith(f"inventaire-{today}") for b in list_backups())
