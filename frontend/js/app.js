/* Suivi inventaire VSAV — application (équipiers et gestion). */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const ETATS = {
    perime: "Périmé", remplacer: "À remplacer", surveiller: "À surveiller", ok: "À jour",
    a_saisir: "Date à saisir", manquant: "Incomplet",
  };
  const A_TRAITER = new Set(["perime", "remplacer", "a_saisir", "manquant"]);

  const state = {
    me: null,
    vehicules: [],
    vehiculeId: null,
    view: "tableau",
    inventaire: null,
    filtre: "perissables",
    recherche: "",
    openForm: null,     // id de l'article dont le formulaire d'ajout est ouvert
    prefill: null,      // { article_id, peremption, lot, gtin } après un scan
    gestionTab: "personnes",
  };

  // ---------------------------------------------------------------- outils

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    let resp;
    try {
      resp = await fetch(path, init);
    } catch {
      throw new Error("Pas de connexion au serveur. Vérifiez le réseau puis réessayez.");
    }
    if (resp.status === 401 && path !== "/api/login") {
      showLogin();
      throw new Error("Connexion requise");
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      let detail = data.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join(" ; ");
      throw new Error(detail || `Erreur ${resp.status}`);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("err", isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 5000 : 2600);
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.slice(0, 10).split("-");
    return `${d}/${m}/${y}`;
  }
  function fmtDateTime(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString("fr-FR") + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function joursLabel(j) {
    if (j < 0) return `périmé depuis ${-j} j`;
    if (j === 0) return "périme aujourd'hui";
    if (j < 62) return `J-${j}`;
    return `${Math.floor(j / 30.44)} mois`;
  }
  function pill(etat) { return `<span class="pill ${etat}">${ETATS[etat] || etat}</span>`; }

  /** « 06/2027 » -> dernier jour du mois ; « 15/06/2027 », « 2027-06-15 » acceptés. */
  function parseDate(str) {
    const s = String(str || "").trim().replace(/[.\-\s]/g, "/");
    let m, y, mo, d;
    if ((m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/))) { d = +m[1]; mo = +m[2]; y = +m[3]; }
    else if ((m = s.match(/^(\d{1,2})\/(\d{2}|\d{4})$/))) { mo = +m[1]; y = +m[2]; d = 0; }
    else if ((m = s.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/))) { y = +m[1]; mo = +m[2]; d = +m[3]; }
    else return null;
    if (y < 100) y += 2000;
    if (mo < 1 || mo > 12 || y < 2000 || y > 2100) return null;
    const last = new Date(y, mo, 0).getDate();
    if (d === 0) d = last;
    if (d < 1 || d > last) return null;
    return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }

  function confirmDialog(title, text, okLabel = "Confirmer") {
    const dlg = $("#dlg-confirm");
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    $("#confirm-ok").textContent = okLabel;
    dlg.returnValue = "";
    dlg.showModal();
    return new Promise((resolve) => {
      dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    });
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("Copié"); }
    catch { window.prompt("Copiez :", text); }
  }

  document.addEventListener("click", (e) => {
    const close = e.target.closest("[data-close]");
    if (close) close.closest("dialog").close();
  });

  // ---------------------------------------------------------------- connexion

  function showLogin() {
    $("#app-view").hidden = true;
    $("#login-view").hidden = false;
    setTimeout(() => $("#login-code").focus(), 50);
  }

  $("#login-toggle").addEventListener("click", () => {
    const admin = $("#login-admin").hidden;
    $("#login-admin").hidden = !admin;
    $("#login-code").hidden = admin;
    $("#login-toggle").textContent = admin ? "Connexion avec un code" : "Connexion administrateur";
    (admin ? $("#login-password") : $("#login-code")).focus();
  });

  $("#login-code").addEventListener("input", (e) => {
    const raw = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);
    e.target.value = raw.length > 4 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : raw;
  });

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#login-error");
    err.hidden = true;
    const admin = !$("#login-admin").hidden;
    try {
      await api("/api/login", { method: "POST", body: admin ? { mot_de_passe: $("#login-password").value } : { code: $("#login-code").value } });
      $("#login-password").value = "";
      await start();
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    }
  });

  $("#logout").addEventListener("click", async () => {
    if (!(await confirmDialog("Se déconnecter ?", "Il faudra ressaisir votre code pour revenir.", "Se déconnecter"))) return;
    await api("/api/logout", { method: "POST" }).catch(() => {});
    state.me = null;
    showLogin();
  });

  // ---------------------------------------------------------------- démarrage

  async function start() {
    state.me = await api("/api/moi");
    $("#login-view").hidden = true;
    $("#app-view").hidden = false;
    $("#who").textContent = `${state.me.nom}${state.me.centre ? " · " + state.me.centre : ""}`;
    $("#tab-gestion").hidden = !state.me.gestion;
    await loadVehicules();
    const fromHash = (location.hash.match(/^#(tableau|inventaire|verification|commande|gestion)$/) || [])[1];
    show(fromHash && (fromHash !== "gestion" || state.me.gestion) ? fromHash : "tableau");
  }

  async function loadVehicules() {
    state.vehicules = (await api("/api/vehicules")).vehicules;
    let saved = null;
    try { saved = Number(localStorage.getItem("vehicule")); } catch { /* stockage indisponible */ }
    state.vehiculeId = state.vehicules.some((v) => v.id === saved) ? saved
      : state.vehicules.some((v) => v.id === state.vehiculeId) ? state.vehiculeId : state.vehicules[0]?.id;
    const sel = $("#vehicule-select");
    sel.innerHTML = state.vehicules.map((v) => `<option value="${v.id}">${esc(v.nom)}</option>`).join("");
    sel.value = String(state.vehiculeId);
    sel.hidden = state.vehicules.length < 2;
  }

  $("#vehicule-select").addEventListener("change", (e) => {
    state.vehiculeId = Number(e.target.value);
    try { localStorage.setItem("vehicule", String(state.vehiculeId)); } catch { /* ignoré */ }
    state.openForm = null;
    state.prefill = null;
    show(state.view);
  });

  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]");
    if (b) show(b.dataset.view);
  });

  function show(view) {
    state.view = view;
    $$("#tabs button").forEach((b) => b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false"));
    $$("[data-view-panel]").forEach((p) => { p.hidden = p.dataset.viewPanel !== view; });
    $("#fab-scan").hidden = !(view === "inventaire" || view === "tableau");
    history.replaceState(null, "", view === "tableau" ? location.pathname : `#${view}`);
    window.scrollTo(0, 0);
    ({ tableau: renderTableau, inventaire: renderInventaire, verification: renderVerification,
       commande: renderCommande, gestion: renderGestion })[view]().catch((ex) => toast(ex.message, true));
  }

  // ---------------------------------------------------------------- tableau de bord

  async function renderTableau() {
    const box = $("#v-tableau");
    const data = await api("/api/tableau");
    let totalAlertes = 0;
    const vehs = [...data.vehicules].sort((a, b) => (a.id === state.vehiculeId ? -1 : b.id === state.vehiculeId ? 1 : 0));
    box.innerHTML = `<div style="display:grid;gap:16px">${vehs.map((v) => {
      const r = v.resume;
      totalAlertes += r.perime + r.remplacer;
      const rows = v.lots.map((l) => `
        <button type="button" class="alert-row ${l.etat}" data-goto="${v.id}:${l.article_id}">
          <span class="what"><strong>${esc(l.designation)}</strong>
            <small><b class="date">${fmtDate(l.peremption)}</b> · ${joursLabel(l.jours)}${l.numero_lot ? " · lot " + esc(l.numero_lot) : ""}</small>
            <small>${esc(l.section)}</small></span>
          ${pill(l.etat)}
        </button>`).join("") + v.incomplets.map((a) => `
        <button type="button" class="alert-row manquant" data-goto="${v.id}:${a.id}">
          <span class="what"><strong>${esc(a.designation)}</strong>
            <small><b class="num">${a.en_place} sur ${a.quantite}</b> en place</small>
            <small>${esc(a.section)}</small></span>
          ${pill("manquant")}
        </button>`).join("");
      const dv = v.derniere_verification;
      return `<article class="card card-pad veh-card">
        <div class="veh-head">
          <h2>${esc(v.nom)}</h2>
          <span class="muted small">${dv ? `Vérifié le ${fmtDateTime(dv.faite_le)} par ${esc(dv.faite_par)}${dv.manquants.length ? ` · ${dv.manquants.length} manquant(s)` : ""}` : "Pas encore de vérification"}</span>
        </div>
        <div class="counts">
          ${["perime", "remplacer", "surveiller", "ok", "a_saisir"].map((k) => `
            <button type="button" class="count ${k}" data-filtre="${v.id}:${k}"><b>${r[k]}</b><span>${k === "remplacer" ? `Sous ${data.seuils.remplacement} j` : k === "surveiller" ? `Sous ${data.seuils.surveillance} j` : ETATS[k]}</span></button>`).join("")}
        </div>
        ${rows ? `<div class="alert-list">${rows}</div>` : `<div class="all-good">Rien à remplacer dans les ${data.seuils.remplacement} prochains jours.</div>`}
        ${r.a_saisir ? `<p class="muted small" style="margin:0">${r.a_saisir} produit(s) suivis n'ont pas encore de date. Scannez les boîtes ou saisissez les dates dans l'inventaire.</p>` : ""}
      </article>`;
    }).join("")}</div>`;
    const badge = $("#badge-alertes");
    badge.textContent = totalAlertes;
    badge.hidden = !totalAlertes;
  }

  $("#v-tableau").addEventListener("click", (e) => {
    const go = e.target.closest("[data-goto]");
    const f = e.target.closest("[data-filtre]");
    if (go) {
      const [vid, aid] = go.dataset.goto.split(":").map(Number);
      selectVehicule(vid);
      state.filtre = "tout";
      state.recherche = "";
      state.openForm = null;
      gotoArticle = aid;
      show("inventaire");
    } else if (f) {
      const [vid, k] = f.dataset.filtre.split(":");
      selectVehicule(Number(vid));
      state.filtre = k === "ok" || k === "surveiller" ? "perissables" : "a_traiter";
      show("inventaire");
    }
  });

  function selectVehicule(id) {
    if (id === state.vehiculeId) return;
    state.vehiculeId = id;
    $("#vehicule-select").value = String(id);
    try { localStorage.setItem("vehicule", String(id)); } catch { /* ignoré */ }
  }

  // ---------------------------------------------------------------- inventaire

  let gotoArticle = null;

  async function loadInventaire() {
    state.inventaire = await api(`/api/vehicules/${state.vehiculeId}/inventaire`);
    return state.inventaire;
  }

  async function renderInventaire() {
    const box = $("#v-inventaire");
    if (!state.inventaire || state.inventaire.vehicule.id !== state.vehiculeId) {
      box.innerHTML = `<div class="empty">Chargement…</div>`;
    }
    const inv = await loadInventaire();
    const q = state.recherche.trim().toLowerCase();
    const keep = (a) => {
      if (q && !(`${a.designation} ${a.section} ${a.observation}`.toLowerCase().includes(q))) return false;
      if (state.filtre === "a_traiter") return a.perissable && A_TRAITER.has(a.etat);
      if (state.filtre === "perissables") return a.perissable;
      if (state.filtre === "medicaments") return a.categorie === "medicament";
      return true;
    };
    const sections = [];
    for (const a of inv.articles) {
      if (!keep(a)) continue;
      let s = sections.find((x) => x.nom === a.section);
      if (!s) sections.push((s = { nom: a.section, articles: [] }));
      s.articles.push(a);
    }
    const nTraiter = inv.articles.filter((a) => a.perissable && A_TRAITER.has(a.etat)).length;
    const chips = [["a_traiter", `À traiter (${nTraiter})`], ["perissables", "Péremptions"], ["medicaments", "Médicaments"], ["tout", "Tout l'inventaire"]];
    box.innerHTML = `
      <div style="display:grid;gap:14px">
        <div class="section-title"><h2>${esc(inv.vehicule.nom)}</h2>
          ${state.me.gestion ? `<button class="btn small" type="button" id="add-article">+ Ajouter un article</button>` : ""}</div>
        <div class="toolbar">
          <input class="search" type="search" id="inv-search" placeholder="Rechercher (Anapen, électrode, pochette rouge…)" value="${esc(state.recherche)}" aria-label="Rechercher">
        </div>
        <div class="chips">${chips.map(([k, l]) => `<button type="button" class="chip" data-chip="${k}" aria-pressed="${state.filtre === k}">${l}</button>`).join("")}</div>
        ${sections.length ? sections.map((s) => `
          <div class="section-block">
            <h3>${esc(s.nom)}</h3>
            <div class="card">${s.articles.map(renderArticle).join("")}</div>
          </div>`).join("") : `<div class="card empty">${state.filtre === "a_traiter" ? "Rien à traiter : toutes les dates sont saisies et à jour." : "Aucun article ne correspond."}</div>`}
      </div>`;
    if (gotoArticle) {
      const el = box.querySelector(`[data-article="${gotoArticle}"]`);
      gotoArticle = null;
      if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("flash"); }
    }
    if (state.openForm) {
      const input = box.querySelector(`[data-article="${state.openForm}"] input[name="peremption"]`);
      if (input) { input.closest(".article").scrollIntoView({ block: "center" }); if (!state.prefill) input.focus(); }
    }
  }

  function renderArticle(a) {
    const lots = a.lots.map((l) => `
      <div class="lot">
        <span class="dot ${l.etat}"></span>
        <span class="info"><span class="date">${fmtDate(l.peremption)}</span>${l.numero_lot ? ` · lot ${esc(l.numero_lot)}` : ""}${l.quantite ? ` · ${l.quantite} u.` : ""}</span>
        <span class="left">${joursLabel(l.jours)}</span>
        <button class="btn ghost small" type="button" data-retirer="${l.id}" aria-label="Retirer le lot du ${fmtDate(l.peremption)}">Retirer</button>
      </div>`).join("");
    const qty = a.perissable && a.en_place !== null ? `${a.en_place}/${a.quantite}` : `× ${a.quantite}`;
    return `<div class="article" data-article="${a.id}">
      <div class="art-head">
        <div class="art-name">
          <strong>${esc(a.designation)}</strong> <span class="qty">${qty}</span>
          ${a.observation ? `<div class="obs">${esc(a.observation)}</div>` : ""}
        </div>
        <div class="row" style="flex-wrap:nowrap">
          ${a.perissable ? pill(a.etat) : ""}
          ${state.me.gestion ? `<button class="btn ghost small" type="button" data-edit="${a.id}" aria-label="Modifier l'article">Modifier</button>` : ""}
        </div>
      </div>
      ${a.perissable ? `<div class="lots">${lots}</div>${state.openForm === a.id ? renderLotForm(a) : `
        <div class="row"><button class="btn small" type="button" data-open="${a.id}">${a.lots.length ? "+ Ajouter un lot" : "+ Saisir la péremption"}</button>
        <button class="btn ghost small" type="button" data-scan-for="${a.id}">Scanner</button></div>`}` : ""}
    </div>`;
  }

  function renderLotForm(a) {
    const p = state.prefill && state.prefill.article_id === a.id ? state.prefill : null;
    const replaceable = a.lots.filter((l) => l.etat === "perime" || l.etat === "remplacer");
    return `<form class="lot-form" data-lot-form="${a.id}">
      ${p ? `<div class="small"><strong>Lu sur la boîte</strong>${p.peremption ? ` : péremption ${fmtDate(p.peremption)}` : ""}${p.lot ? `, lot ${esc(p.lot)}` : ""}. Vérifiez puis enregistrez.</div>` : ""}
      <div class="fields">
        <label class="field">Péremption <input name="peremption" placeholder="MM/AAAA" inputmode="numeric" autocomplete="off" value="${p && p.peremption ? fmtDate(p.peremption) : ""}" required></label>
        <label class="field">N° de lot <input name="numero_lot" autocomplete="off" value="${esc(p ? p.lot : "")}" maxlength="40"></label>
        <label class="field">Qté <input name="quantite" type="number" min="1" max="9999" value="${a.quantite || ""}"></label>
      </div>
      ${replaceable.length ? `<div style="display:grid;gap:6px">${replaceable.map((l) => `
        <label class="check"><input type="checkbox" name="remplace" value="${l.id}" checked> Retirer en même temps le lot du ${fmtDate(l.peremption)} (${ETATS[l.etat].toLowerCase()})</label>`).join("")}</div>` : ""}
      <div class="error" hidden></div>
      <div class="row">
        <button class="btn primary" type="submit">Enregistrer</button>
        <button class="btn ghost" type="button" data-cancel>Annuler</button>
      </div>
    </form>`;
  }

  $("#v-inventaire").addEventListener("input", (e) => {
    if (e.target.id !== "inv-search") return;
    state.recherche = e.target.value;
    clearTimeout(renderInventaire.t);
    renderInventaire.t = setTimeout(() => {
      const pos = e.target.selectionStart;
      renderInventaire().then(() => { const s = $("#inv-search"); s.focus(); s.setSelectionRange(pos, pos); });
    }, 250);
  });

  $("#v-inventaire").addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.chip) { state.filtre = t.dataset.chip; renderInventaire(); }
    else if (t.dataset.open) { state.openForm = Number(t.dataset.open); state.prefill = null; renderInventaire(); }
    else if (t.dataset.cancel !== undefined) { state.openForm = null; state.prefill = null; renderInventaire(); }
    else if (t.dataset.retirer) openRetrait(Number(t.dataset.retirer));
    else if (t.dataset.scanFor) openScan(Number(t.dataset.scanFor));
    else if (t.dataset.edit) openArticle(Number(t.dataset.edit));
    else if (t.id === "add-article") openArticle(null);
  });

  $("#v-inventaire").addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-lot-form]");
    if (!form) return;
    e.preventDefault();
    const aid = Number(form.dataset.lotForm);
    const err = form.querySelector(".error");
    const iso = parseDate(form.peremption.value);
    if (!iso) {
      err.textContent = "Date non reconnue. Écrivez par exemple 06/2027 (fin du mois) ou 15/06/2027.";
      err.hidden = false;
      form.peremption.focus();
      return;
    }
    const p = state.prefill && state.prefill.article_id === aid ? state.prefill : null;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api(`/api/articles/${aid}/lots`, { method: "POST", body: {
        peremption: iso, numero_lot: form.numero_lot.value.trim(),
        quantite: form.quantite.value ? Number(form.quantite.value) : null,
        gtin: p && p.memo ? p.gtin : "",
        remplace: $$("input[name=remplace]:checked", form).map((x) => Number(x.value)),
      } });
      state.openForm = null;
      state.prefill = null;
      gotoArticle = aid;
      toast("Lot enregistré");
      renderInventaire();
    } catch (ex) {
      btn.disabled = false;
      err.textContent = ex.message;
      err.hidden = false;
    }
  });

  let retraitId = null;
  function openRetrait(lid) {
    retraitId = lid;
    let lot = null, art = null;
    for (const a of state.inventaire.articles) {
      const l = a.lots.find((x) => x.id === lid);
      if (l) { lot = l; art = a; break; }
    }
    $("#retrait-info").textContent = art ? `${art.designation}, péremption ${fmtDate(lot.peremption)}${lot.numero_lot ? ", lot " + lot.numero_lot : ""}.` : "";
    const form = $("#retrait-form");
    form.motif.value = lot && lot.jours < 0 ? "perime" : "utilise";
    $("#dlg-retrait").showModal();
  }

  $("#retrait-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/lots/${retraitId}/retirer`, { method: "POST", body: { motif: e.target.motif.value } });
      $("#dlg-retrait").close();
      toast("Lot retiré");
      renderInventaire();
    } catch (ex) { toast(ex.message, true); }
  });

  // ---------------------------------------------------------------- scan

  let stopCamera = null;
  let scanFor = null; // article visé depuis son bouton « Scanner », sinon recherche par code

  $("#fab-scan").addEventListener("click", () => openScan(null));

  async function openScan(articleId) {
    scanFor = articleId;
    const dlg = $("#dlg-scan");
    $("#scan-error").hidden = true;
    $("#scan-video-wrap").hidden = false;
    dlg.showModal();
    try {
      stopCamera = await GS1.startCamera($("#scan-video"), onScanned);
    } catch (ex) {
      $("#scan-video-wrap").hidden = true;
      showScanError(ex && ex.name === "NotAllowedError"
        ? "La caméra est refusée. Autorisez-la dans les réglages du navigateur, ou prenez une photo du code."
        : "Impossible d'ouvrir la caméra en direct. Prenez une photo du code à la place.");
    }
  }

  function showScanError(msg) {
    const el = $("#scan-error");
    el.textContent = msg;
    el.hidden = false;
  }

  $("#dlg-scan").addEventListener("close", () => {
    if (stopCamera) { stopCamera(); stopCamera = null; }
  });

  $("#scan-photo").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      onScanned(await GS1.decodeImage(file));
    } catch {
      showScanError("Aucun code lu sur la photo. Rapprochez-vous du petit carré, bien net et éclairé.");
    }
  });

  async function onScanned(text) {
    const info = GS1.parse(text);
    if (!info || !info.peremption) {
      showScanError("Ce code ne contient pas de date de péremption. Scannez le DataMatrix (petit carré) de la boîte, ou saisissez la date à la main.");
      if (stopCamera) { stopCamera(); stopCamera = null; }
      setTimeout(() => { if ($("#dlg-scan").open) openScan(scanFor); }, 2500);
      return;
    }
    if (navigator.vibrate) navigator.vibrate(60);
    $("#dlg-scan").close();
    if (scanFor) return prefillArticle(scanFor, info, true);
    let matches = [];
    if (info.gtin) {
      try { matches = (await api(`/api/vehicules/${state.vehiculeId}/gtin/${info.gtin}`)).articles; } catch { /* recherche manuelle */ }
    }
    if (matches.length === 1) return prefillArticle(matches[0].id, info, false);
    chooseArticle(info, matches);
  }

  async function chooseArticle(info, matches) {
    const inv = state.inventaire && state.inventaire.vehicule.id === state.vehiculeId ? state.inventaire : await loadInventaire();
    const options = matches.length ? matches : inv.articles.filter((a) => a.perissable);
    $("#choix-info").textContent = (matches.length
      ? "Ce produit est rangé à plusieurs endroits. Où le mettez-vous ?"
      : "Code pas encore connu. Choisissez le produit une fois, il sera reconnu ensuite.")
      + ` Péremption lue : ${fmtDate(info.peremption)}${info.lot ? ", lot " + info.lot : ""}.`;
    let html = "", section = null;
    for (const a of options) {
      if (a.section !== section) { if (section !== null) html += "</optgroup>"; section = a.section; html += `<optgroup label="${esc(section)}">`; }
      html += `<option value="${a.id}">${esc(a.designation)}</option>`;
    }
    $("#choix-article").innerHTML = html + (section !== null ? "</optgroup>" : "");
    $("#choix-memo").closest("label").hidden = !info.gtin || matches.length > 0;
    $("#dlg-choix").showModal();
    $("#choix-form").onsubmit = (e) => {
      e.preventDefault();
      $("#dlg-choix").close();
      prefillArticle(Number($("#choix-article").value), info, info.gtin && !matches.length && $("#choix-memo").checked);
    };
  }

  function prefillArticle(articleId, info, memo) {
    state.openForm = articleId;
    state.prefill = { article_id: articleId, peremption: info.peremption, lot: info.lot, gtin: info.gtin, memo: !!memo };
    state.filtre = "tout";
    state.recherche = "";
    gotoArticle = articleId;
    if (state.view !== "inventaire") show("inventaire");
    else renderInventaire();
  }

  // ---------------------------------------------------------------- articles (gestion)

  let articleId = null;
  async function openArticle(id) {
    articleId = id;
    const inv = state.inventaire;
    const a = id ? inv.articles.find((x) => x.id === id) : null;
    $("#article-title").textContent = a ? "Modifier l'article" : `Nouvel article · ${inv.vehicule.nom}`;
    $("#af-sections").innerHTML = [...new Set(inv.articles.map((x) => x.section))].map((s) => `<option value="${esc(s)}">`).join("");
    $("#af-section").value = a ? a.section : "";
    $("#af-designation").value = a ? a.designation : "";
    $("#af-quantite").value = a ? a.quantite : 1;
    $("#af-categorie").value = a ? a.categorie : "materiel";
    $("#af-observation").value = a ? a.observation : "";
    $("#af-perissable").checked = a ? a.perissable : true;
    $("#af-gtin").value = a ? a.gtin : "";
    $("#af-delete").hidden = !a;
    $("#af-error").hidden = true;
    $("#dlg-article").showModal();
  }

  $("#article-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      section: $("#af-section").value.trim(), designation: $("#af-designation").value.trim(),
      quantite: Number($("#af-quantite").value), categorie: $("#af-categorie").value,
      observation: $("#af-observation").value.trim(), perissable: $("#af-perissable").checked,
      gtin: $("#af-gtin").value.trim(),
    };
    try {
      if (articleId) await api(`/api/admin/articles/${articleId}`, { method: "PUT", body });
      else articleId = (await api(`/api/admin/vehicules/${state.vehiculeId}/articles`, { method: "POST", body })).id;
      $("#dlg-article").close();
      gotoArticle = articleId;
      toast("Article enregistré");
      renderInventaire();
    } catch (ex) {
      $("#af-error").textContent = ex.message;
      $("#af-error").hidden = false;
    }
  });

  $("#af-delete").addEventListener("click", async () => {
    $("#dlg-article").close();
    if (!(await confirmDialog("Supprimer l'article ?", "L'article et l'historique de ses lots seront effacés.", "Supprimer"))) return;
    try {
      await api(`/api/admin/articles/${articleId}`, { method: "DELETE" });
      toast("Article supprimé");
      renderInventaire();
    } catch (ex) { toast(ex.message, true); }
  });

  // ---------------------------------------------------------------- vérification

  async function renderVerification() {
    const box = $("#v-verification");
    const [inv, hist] = await Promise.all([loadInventaire(), api(`/api/vehicules/${state.vehiculeId}/verifications`)]);
    const sections = [];
    for (const a of inv.articles) {
      let s = sections.find((x) => x.nom === a.section);
      if (!s) sections.push((s = { nom: a.section, articles: [] }));
      s.articles.push(a);
    }
    const last = hist.verifications.slice(0, 5);
    box.innerHTML = `<form id="verif-form" style="display:grid;gap:14px">
      <div class="section-title"><h2>Vérification ${esc(inv.vehicule.nom)}</h2>
        <span class="muted small">Cochez ce qui est présent et complet.</span></div>
      ${sections.map((s, i) => `
        <div class="section-block">
          <div class="section-title"><h3 class="muted small" style="text-transform:uppercase;letter-spacing:.05em">${esc(s.nom)}</h3>
            <button class="btn ghost small" type="button" data-all="${i}">Tout cocher</button></div>
          <div class="card" data-section="${i}">${s.articles.map((a) => `
            <label class="verif-item">
              <input type="checkbox" value="${a.id}">
              <span class="grow"><strong>${esc(a.designation)}</strong> <span class="qty">× ${a.quantite}</span>
                ${a.observation ? `<small>${esc(a.observation)}</small>` : ""}</span>
              ${a.perissable && a.etat !== "ok" ? pill(a.etat) : ""}
            </label>`).join("")}</div>
        </div>`).join("")}
      <label class="field">Remarque (facultatif)<textarea id="verif-note" maxlength="1000" placeholder="Ex. : bouteille O2 5 l à 50 bars, prévenir la pharmacie"></textarea></label>
      <div class="sticky-bar">
        <span id="verif-count" class="num"></span>
        <button class="btn primary" type="submit">Valider la vérification</button>
      </div>
      ${last.length ? `<div class="card card-pad" style="display:grid;gap:8px"><h3 style="font-size:15px">Dernières vérifications</h3>
        ${last.map((v) => `<div class="small"><strong>${fmtDateTime(v.faite_le)}</strong> · ${esc(v.faite_par)} ·
          ${v.manquants.length ? `${v.manquants.length} manquant(s) : ${v.manquants.map((m) => esc(m.designation)).join(", ")}` : "complet"}
          ${v.note ? `<div class="muted">${esc(v.note)}</div>` : ""}</div>`).join("")}</div>` : ""}
    </form>`;
    updateVerifCount();
  }

  function updateVerifCount() {
    const all = $$("#verif-form .verif-item input");
    const done = all.filter((x) => x.checked).length;
    all.forEach((x) => x.closest(".verif-item").classList.toggle("checked", x.checked));
    const c = $("#verif-count");
    if (c) c.textContent = `${done} / ${all.length} cochés`;
  }

  $("#v-verification").addEventListener("change", updateVerifCount);
  $("#v-verification").addEventListener("click", (e) => {
    const b = e.target.closest("[data-all]");
    if (!b) return;
    const boxes = $$(`[data-section="${b.dataset.all}"] input`);
    const allOn = boxes.every((x) => x.checked);
    boxes.forEach((x) => { x.checked = !allOn; });
    b.textContent = allOn ? "Tout cocher" : "Tout décocher";
    updateVerifCount();
  });

  $("#v-verification").addEventListener("submit", async (e) => {
    e.preventDefault();
    const missing = $$("#verif-form .verif-item input").filter((x) => !x.checked).map((x) => Number(x.value));
    if (missing.length && !(await confirmDialog("Valider avec des manquants ?",
      `${missing.length} article(s) non coché(s) seront signalés manquants${state.me.gestion ? "" : " à la gestion"}.`, "Valider"))) return;
    try {
      const res = await api("/api/verifications", { method: "POST", body: { vehicule_id: state.vehiculeId, manquants: missing, note: $("#verif-note").value } });
      toast(res.manquants ? `Vérification enregistrée : ${res.manquants} manquant(s)${res.whatsapp ? ", prévenu sur WhatsApp" : ""}` : "Vérification enregistrée : tout est complet");
      renderVerification();
    } catch (ex) { toast(ex.message, true); }
  });

  // ---------------------------------------------------------------- commande

  let commandeJours = 45;
  async function renderCommande() {
    const box = $("#v-commande");
    const data = await api(`/api/commande?jours=${commandeJours}`);
    box.innerHTML = `<div style="display:grid;gap:14px">
      <div class="section-title"><h2>À commander</h2>
        <div class="row">
          <label class="muted small row" for="cmd-jours">Péremption sous
            <select id="cmd-jours">${[15, 30, 45, 60, 90].map((j) => `<option value="${j}" ${j === commandeJours ? "selected" : ""}>${j} jours</option>`).join("")}</select></label>
          <a class="btn small" href="/api/commande.csv?jours=${commandeJours}">Exporter (Excel)</a>
        </div>
      </div>
      <p class="muted small" style="margin:0">Tous les véhicules : lots périmés ou qui périment dans la période, et quantités incomplètes. Les médicaments sont en premier.</p>
      ${data.lignes.length ? `<div class="card table-wrap"><table>
        <thead><tr><th>Produit</th><th class="num">Qté</th><th>Véhicule · emplacement</th><th>Raison</th></tr></thead>
        <tbody>${data.lignes.map((l) => `<tr>
          <td><strong>${esc(l.designation)}</strong>${l.categorie === "medicament" ? ` <span class="pill a_saisir">Médicament</span>` : ""}</td>
          <td class="num">${l.quantite}</td>
          <td>${esc(l.vehicule)}<div class="muted small">${esc(l.section)}</div></td>
          <td>${l.jours !== null && l.jours < 0 ? `<span class="pill perime">${esc(l.raison)}</span>` : esc(l.raison)}</td>
        </tr>`).join("")}</tbody></table></div>` : `<div class="card empty">Rien à commander pour les ${commandeJours} prochains jours.</div>`}
    </div>`;
  }

  $("#v-commande").addEventListener("change", (e) => {
    if (e.target.id === "cmd-jours") { commandeJours = Number(e.target.value); renderCommande(); }
  });

  // ---------------------------------------------------------------- gestion

  const GTABS = [["personnes", "Personnes"], ["alertes", "Alertes WhatsApp"], ["vehicules", "Véhicules"], ["journal", "Journal"], ["sauvegardes", "Sauvegardes"]];

  async function renderGestion() {
    const box = $("#v-gestion");
    box.innerHTML = `<div style="display:grid;gap:14px">
      <div class="subtabs chips">${GTABS.map(([k, l]) => `<button type="button" class="chip" data-gtab="${k}" aria-pressed="${state.gestionTab === k}">${l}</button>`).join("")}</div>
      <div id="gestion-body"><div class="empty">Chargement…</div></div>
    </div>`;
    await ({ personnes: gPersonnes, alertes: gAlertes, vehicules: gVehicules, journal: gJournal, sauvegardes: gSauvegardes })[state.gestionTab]($("#gestion-body"));
  }

  $("#v-gestion").addEventListener("click", (e) => {
    const b = e.target.closest("[data-gtab]");
    if (b) { state.gestionTab = b.dataset.gtab; renderGestion().catch((ex) => toast(ex.message, true)); }
  });

  // ---- personnes

  let personnes = [];
  let editPersonne = null; // id, ou "new"

  function accessLink(p) { return `${location.origin}/#acces=${p.code}`; }
  function fmtCode(c) { return c ? `${c.slice(0, 4)}-${c.slice(4)}` : ""; }

  async function gPersonnes(body) {
    personnes = (await api("/api/admin/personnes")).personnes;
    const form = (p) => `<form class="lot-form" data-pform="${p ? p.id : "new"}" style="margin:0 14px 12px">
      <div class="form-grid">
        <label class="field">Nom <input name="nom" required maxlength="80" value="${esc(p ? p.nom : "")}"></label>
        <label class="field">Téléphone (WhatsApp) <input name="telephone" type="tel" maxlength="30" value="${esc(p ? p.telephone : "")}" placeholder="06 12 34 56 78"></label>
      </div>
      <label class="check"><input type="checkbox" name="alertes" ${p && p.alertes ? "checked" : ""}> Reçoit les alertes de péremption par WhatsApp</label>
      <label class="check"><input type="checkbox" name="gestion" ${p && p.gestion ? "checked" : ""}> Gestion : modifie l'inventaire, les personnes et les alertes</label>
      ${p ? `<label class="check"><input type="checkbox" name="actif" ${p.actif ? "checked" : ""}> Accès actif</label>` : ""}
      <div class="row"><button class="btn primary" type="submit">Enregistrer</button><button class="btn ghost" type="button" data-pcancel>Annuler</button>
        ${p ? `<span class="grow"></span><button class="btn danger small" type="button" data-pdel="${p.id}">Supprimer</button>` : ""}</div>
    </form>`;
    body.innerHTML = `<div style="display:grid;gap:12px">
      <div class="section-title"><h2>Personnes</h2><button class="btn small" type="button" data-padd>+ Ajouter</button></div>
      <p class="muted small" style="margin:0">Chaque personne a un code personnel. Envoyez-lui son lien par WhatsApp : en l'ouvrant, son téléphone reste connecté.</p>
      <div class="card">
        ${editPersonne === "new" ? `<div class="person" style="display:block"><strong>Nouvelle personne</strong></div>${form(null)}` : ""}
        ${personnes.length ? personnes.map((p) => `
          <div class="person">
            <div style="min-width:0">
              <strong>${esc(p.nom)}</strong>${p.gestion ? ` <span class="pill a_saisir">Gestion</span>` : ""}${p.alertes ? ` <span class="pill ok">Alertes</span>` : ""}${!p.actif ? ` <span class="pill perime">Désactivé</span>` : ""}
              <div class="small muted">${esc(p.telephone || "pas de téléphone")} · code <span class="code">${fmtCode(p.code)}</span></div>
            </div>
            <div class="row" style="justify-content:flex-end">
              <button class="btn small" type="button" data-psend="${p.id}" ${p.whatsapp_ok ? "" : "disabled"}>Envoyer l'accès</button>
              <button class="btn ghost small" type="button" data-pcopy="${p.id}">Copier le lien</button>
              <button class="btn ghost small" type="button" data-pcode="${p.id}">Nouveau code</button>
              <button class="btn ghost small" type="button" data-pedit="${p.id}">Modifier</button>
            </div>
          </div>${editPersonne === p.id ? form(p) : ""}`).join("") : (editPersonne === "new" ? "" : `<div class="empty">Ajoutez les équipiers qui font l'inventaire et ceux qui reçoivent les alertes.</div>`)}
      </div>
    </div>`;
  }

  $("#v-gestion").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b || !$("#gestion-body")) return;
    const body = $("#gestion-body");
    const p = (id) => personnes.find((x) => x.id === Number(id));
    try {
      if (b.dataset.padd !== undefined) { editPersonne = "new"; gPersonnes(body); }
      else if (b.dataset.pedit) { editPersonne = Number(b.dataset.pedit); gPersonnes(body); }
      else if (b.dataset.pcancel !== undefined) { editPersonne = null; gPersonnes(body); }
      else if (b.dataset.pcopy) copyText(accessLink(p(b.dataset.pcopy)));
      else if (b.dataset.psend) {
        b.disabled = true;
        await api(`/api/admin/personnes/${b.dataset.psend}/envoyer-acces`, { method: "POST" });
        toast(`Accès envoyé à ${p(b.dataset.psend).nom}`);
        b.disabled = false;
      } else if (b.dataset.pcode) {
        if (!(await confirmDialog("Nouveau code ?", `L'ancien code de ${p(b.dataset.pcode).nom} ne marchera plus et son téléphone sera déconnecté.`, "Changer le code"))) return;
        await api(`/api/admin/personnes/${b.dataset.pcode}/code`, { method: "POST" });
        toast("Nouveau code créé : pensez à l'envoyer");
        gPersonnes(body);
      } else if (b.dataset.pdel) {
        if (!(await confirmDialog("Supprimer cette personne ?", `${p(b.dataset.pdel).nom} n'aura plus accès. Son nom reste dans le journal.`, "Supprimer"))) return;
        await api(`/api/admin/personnes/${b.dataset.pdel}`, { method: "DELETE" });
        editPersonne = null;
        gPersonnes(body);
      }
    } catch (ex) { b.disabled = false; toast(ex.message, true); }
  });

  $("#v-gestion").addEventListener("submit", async (e) => {
    const f = e.target.closest("[data-pform]");
    if (!f) return;
    e.preventDefault();
    const id = f.dataset.pform;
    const body = { nom: f.nom.value.trim(), telephone: f.telephone.value.trim(), alertes: f.alertes.checked,
                   gestion: f.gestion.checked, actif: f.actif ? f.actif.checked : true };
    try {
      if (id === "new") await api("/api/admin/personnes", { method: "POST", body });
      else await api(`/api/admin/personnes/${id}`, { method: "PUT", body });
      editPersonne = null;
      toast("Enregistré");
      gPersonnes($("#gestion-body"));
    } catch (ex) { toast(ex.message, true); }
  });

  // ---- alertes WhatsApp

  async function gAlertes(body) {
    const [r, etat, apercu] = await Promise.all([api("/api/admin/reglages"), api("/api/admin/whatsapp/etat"), api("/api/admin/alertes/apercu")]);
    body.innerHTML = `<form id="reglages-form" style="display:grid;gap:14px">
      <div class="card card-pad" style="display:grid;gap:12px">
        <h2 style="font-size:17px">Bot WhatsApp</h2>
        <div class="status-line"><span class="dot ${etat.pret ? "ok" : "perime"}"></span>${etat.pret ? "Connecté et prêt" : esc(etat.erreur)}</div>
        <div class="form-grid">
          <label class="field">Adresse du bot vue depuis le serveur <input name="whatsapp_bot_url" type="url" value="${esc(r.whatsapp_bot_url)}" placeholder="http://host.docker.internal:3000"></label>
          <label class="field">Clé API (X-API-Key) <input name="whatsapp_bot_key" type="password" placeholder="${r.whatsapp_bot_key_definie ? "•••••• (inchangée)" : "aucune"}" autocomplete="new-password"></label>
        </div>
        <label class="field">Groupe WhatsApp (identifiant …@g.us, facultatif) <input name="whatsapp_groupe" value="${esc(r.whatsapp_groupe)}" placeholder="120363000000000000@g.us"></label>
        <p class="muted small" style="margin:0">Destinataires : ${r.destinataires.length ? esc(r.destinataires.join(", ")) : "aucun (réglez un groupe ou cochez « reçoit les alertes » sur une personne)"}.</p>
      </div>
      <div class="card card-pad" style="display:grid;gap:12px">
        <h2 style="font-size:17px">Quand prévenir</h2>
        <label class="check"><input type="checkbox" name="alertes_actives" ${r.alertes_actives ? "checked" : ""}> Envoyer les alertes automatiquement chaque jour</label>
        <div class="form-grid">
          <label class="field">Heure d'envoi <select name="alertes_heure">${Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === r.alertes_heure ? "selected" : ""}>${h} h</option>`).join("")}</select></label>
          <label class="field">Paliers (jours avant péremption) <input name="alertes_paliers" value="${esc(r.alertes_paliers)}" placeholder="30,7,0"></label>
        </div>
        <label class="check"><input type="checkbox" name="alertes_rappel_lundi" ${r.alertes_rappel_lundi ? "checked" : ""}> Chaque lundi, rappeler les lots périmés toujours en place</label>
        <label class="check"><input type="checkbox" name="alertes_verification" ${r.alertes_verification ? "checked" : ""}> Prévenir quand une vérification signale du matériel manquant</label>
        <div class="form-grid">
          <label class="field">« À remplacer » sous (jours) <input name="delai_remplacement" type="number" min="1" max="365" value="${r.delai_remplacement}"></label>
          <label class="field">« À surveiller » sous (jours) <input name="delai_surveillance" type="number" min="1" max="730" value="${r.delai_surveillance}"></label>
        </div>
        <div class="form-grid">
          <label class="field">Nom du centre (titre des messages) <input name="nom_centre" maxlength="80" value="${esc(r.nom_centre)}"></label>
          <label class="field">Adresse du site (lien dans les messages) <input name="url_site" type="url" value="${esc(r.url_site)}" placeholder="https://inventaire.exemple.fr"></label>
        </div>
        <div class="row"><button class="btn primary" type="submit">Enregistrer les réglages</button>
          <span class="muted small">${r.alertes_derniere ? `Dernière tournée : ${fmtDate(r.alertes_derniere)}` : ""}</span></div>
      </div>
      <div class="card card-pad" style="display:grid;gap:12px">
        <h2 style="font-size:17px">Prochain message</h2>
        ${apercu.lots ? `<pre class="preview">${esc(apercu.message)}</pre>` : `<p class="muted" style="margin:0">Rien de nouveau à signaler aujourd'hui.</p>`}
        <div class="row">
          <button class="btn" type="button" id="alerte-test">Envoyer un message de test</button>
          <button class="btn" type="button" id="alerte-run" ${apercu.lots ? "" : "disabled"}>Envoyer l'alerte maintenant</button>
        </div>
      </div>
    </form>`;
  }

  $("#v-gestion").addEventListener("submit", async (e) => {
    if (e.target.id !== "reglages-form") return;
    e.preventDefault();
    const f = e.target;
    const body = {
      delai_remplacement: Number(f.delai_remplacement.value), delai_surveillance: Number(f.delai_surveillance.value),
      alertes_actives: f.alertes_actives.checked, alertes_paliers: f.alertes_paliers.value, alertes_heure: Number(f.alertes_heure.value),
      alertes_rappel_lundi: f.alertes_rappel_lundi.checked, alertes_verification: f.alertes_verification.checked,
      whatsapp_bot_url: f.whatsapp_bot_url.value.trim(), whatsapp_bot_key: f.whatsapp_bot_key.value ? f.whatsapp_bot_key.value : null,
      whatsapp_groupe: f.whatsapp_groupe.value.trim(), url_site: f.url_site.value.trim(), nom_centre: f.nom_centre.value.trim(),
    };
    try {
      await api("/api/admin/reglages", { method: "PUT", body });
      toast("Réglages enregistrés");
      gAlertes($("#gestion-body"));
    } catch (ex) { toast(ex.message, true); }
  });

  $("#v-gestion").addEventListener("click", async (e) => {
    const b = e.target.closest("#alerte-test, #alerte-run");
    if (!b) return;
    b.disabled = true;
    try {
      const res = await api(b.id === "alerte-test" ? "/api/admin/alertes/test" : "/api/admin/alertes/executer", { method: "POST" });
      if (res.erreurs && res.erreurs.length) toast(`Échecs : ${res.erreurs.join(" ; ")}`, true);
      else toast(`Envoyé à ${res.reussis.join(", ")}`);
      if (b.id === "alerte-run") gAlertes($("#gestion-body"));
    } catch (ex) { toast(ex.message, true); }
    b.disabled = false;
  });

  // ---- véhicules

  async function gVehicules(body) {
    await loadVehicules();
    body.innerHTML = `<div style="display:grid;gap:12px">
      <div class="section-title"><h2>Véhicules</h2></div>
      <div class="card">${state.vehicules.map((v) => `
        <form class="person" data-vform="${v.id}">
          <div class="form-grid" style="min-width:0">
            <label class="field">Nom <input name="nom" required maxlength="60" value="${esc(v.nom)}"></label>
            <label class="field">Description <input name="description" maxlength="200" value="${esc(v.description)}"></label>
          </div>
          <div class="row" style="justify-content:flex-end">
            <button class="btn small" type="submit">Renommer</button>
            ${state.vehicules.length > 1 ? `<button class="btn ghost small danger" type="button" data-vdel="${v.id}">Supprimer</button>` : ""}
          </div>
        </form>`).join("")}</div>
      <form class="card card-pad" id="vadd-form" style="display:grid;gap:12px">
        <h3 style="font-size:16px">Ajouter un véhicule</h3>
        <div class="form-grid">
          <label class="field">Nom <input name="nom" required maxlength="60" placeholder="ASUP 2"></label>
          <label class="field">Matériel de départ
            <select name="copie_de">${state.vehicules.map((v) => `<option value="${v.id}">Copie de ${esc(v.nom)} (sans les lots)</option>`).join("")}
              <option value="">Inventaire ASUP de référence (novembre 2025)</option></select></label>
        </div>
        <div><button class="btn primary" type="submit">Ajouter</button></div>
      </form>
    </div>`;
  }

  $("#v-gestion").addEventListener("submit", async (e) => {
    const vf = e.target.closest("[data-vform]");
    if (!vf && e.target.id !== "vadd-form") return;
    e.preventDefault();
    try {
      if (vf) {
        await api(`/api/admin/vehicules/${vf.dataset.vform}`, { method: "PUT", body: { nom: vf.nom.value.trim(), description: vf.description.value.trim() } });
        toast("Véhicule renommé");
      } else {
        const f = e.target;
        const res = await api("/api/admin/vehicules", { method: "POST", body: { nom: f.nom.value.trim(), copie_de: f.copie_de.value ? Number(f.copie_de.value) : null } });
        selectVehicule(res.id);
        toast("Véhicule ajouté");
      }
      await gVehicules($("#gestion-body"));
    } catch (ex) { toast(ex.message, true); }
  });

  $("#v-gestion").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-vdel]");
    if (!b) return;
    const v = state.vehicules.find((x) => x.id === Number(b.dataset.vdel));
    if (!(await confirmDialog(`Supprimer ${v.nom} ?`, "Son inventaire, ses lots et ses vérifications seront effacés. Les sauvegardes du jour les gardent.", "Supprimer"))) return;
    try {
      await api(`/api/admin/vehicules/${v.id}`, { method: "DELETE" });
      toast("Véhicule supprimé");
      await gVehicules($("#gestion-body"));
    } catch (ex) { toast(ex.message, true); }
  });

  // ---- journal

  const ACTIONS = {
    "lot.ajout": "Lot ajouté", "lot.retrait": "Lot retiré", verification: "Vérification", connexion: "Connexion",
    "connexion.echec": "Connexion refusée", "alerte.envoi": "Alerte envoyée", "alerte.echec": "Alerte non envoyée",
    "alerte.test": "Test WhatsApp", "article.ajout": "Article ajouté", "article.modif": "Article modifié",
    "article.suppression": "Article supprimé", "vehicule.ajout": "Véhicule ajouté", "vehicule.modif": "Véhicule modifié",
    "vehicule.suppression": "Véhicule supprimé", "personne.ajout": "Personne ajoutée", "personne.modif": "Personne modifiée",
    "personne.suppression": "Personne supprimée", "personne.code": "Nouveau code", "personne.envoi_acces": "Accès envoyé",
    reglages: "Réglages", sauvegarde: "Sauvegarde",
  };
  let journalQ = "";

  async function gJournal(body, append = false) {
    const lines = body.querySelectorAll("tbody tr[data-id]");
    const before = append && lines.length ? lines[lines.length - 1].dataset.id : "";
    const data = await api(`/api/admin/journal?limit=100&q=${encodeURIComponent(journalQ)}${before ? "&before=" + before : ""}`);
    const rows = data.lignes.map((l) => `<tr data-id="${l.id}">
      <td class="small" style="white-space:nowrap">${fmtDateTime(l.quand)}</td><td>${esc(l.qui)}</td>
      <td><strong>${esc(ACTIONS[l.action] || l.action)}</strong>${l.objet ? ` · ${esc(l.objet)}` : ""}<div class="muted small">${esc(l.details)}</div></td></tr>`).join("");
    if (append) {
      body.querySelector("tbody").insertAdjacentHTML("beforeend", rows);
      body.querySelector("#journal-more").hidden = data.lignes.length < 100;
      return;
    }
    body.innerHTML = `<div style="display:grid;gap:12px">
      <div class="section-title"><h2>Journal</h2>
        <input type="search" id="journal-q" placeholder="Rechercher (nom, produit…)" value="${esc(journalQ)}" aria-label="Rechercher dans le journal"></div>
      <div class="card table-wrap"><table><thead><tr><th>Quand</th><th>Qui</th><th>Quoi</th></tr></thead><tbody>${rows || `<tr><td colspan="3" class="empty">Rien pour l'instant.</td></tr>`}</tbody></table></div>
      <div><button class="btn small" type="button" id="journal-more" ${data.lignes.length < 100 ? "hidden" : ""}>Plus ancien</button></div>
    </div>`;
  }

  $("#v-gestion").addEventListener("input", (e) => {
    if (e.target.id !== "journal-q") return;
    journalQ = e.target.value;
    clearTimeout(gJournal.t);
    gJournal.t = setTimeout(() => gJournal($("#gestion-body")).then(() => { const q = $("#journal-q"); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }), 300);
  });
  $("#v-gestion").addEventListener("click", (e) => {
    if (e.target.id === "journal-more") gJournal($("#gestion-body"), true).catch((ex) => toast(ex.message, true));
  });

  // ---- sauvegardes

  async function gSauvegardes(body) {
    const data = await api("/api/admin/sauvegardes");
    body.innerHTML = `<div style="display:grid;gap:12px">
      <div class="section-title"><h2>Sauvegardes</h2><button class="btn small" type="button" id="backup-now">Sauvegarder maintenant</button></div>
      <p class="muted small" style="margin:0">Une sauvegarde par jour, les ${data.garde} dernières sont gardées sur le serveur (dossier <code>data/db/sauvegardes</code>).</p>
      <div class="card">${data.sauvegardes.length ? data.sauvegardes.map((s) => `
        <div class="person"><div><strong>${esc(s.nom)}</strong><div class="muted small">${fmtDateTime(s.date)} · ${Math.round(s.taille / 1024)} Ko</div></div>
        <a class="btn ghost small" href="/api/admin/sauvegardes/${encodeURIComponent(s.nom)}">Télécharger</a></div>`).join("")
        : `<div class="empty">Pas encore de sauvegarde.</div>`}</div>
    </div>`;
  }

  $("#v-gestion").addEventListener("click", async (e) => {
    if (e.target.id !== "backup-now") return;
    try {
      await api("/api/admin/sauvegardes", { method: "POST" });
      toast("Sauvegarde créée");
      gSauvegardes($("#gestion-body"));
    } catch (ex) { toast(ex.message, true); }
  });

  // ---------------------------------------------------------------- lancement

  async function boot() {
    const m = location.hash.match(/^#acces=([A-Za-z0-9-]{8,9})$/);
    if (m) {
      history.replaceState(null, "", location.pathname);
      try { await api("/api/login", { method: "POST", body: { code: m[1] } }); }
      catch (ex) {
        showLogin();
        $("#login-error").textContent = ex.message;
        $("#login-error").hidden = false;
        return;
      }
    }
    try { await start(); }
    catch { showLogin(); }
  }

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  document.addEventListener("visibilitychange", () => {
    // revenu sur l'application après un moment : on rafraîchit l'écran affiché
    // (seulement l'accueil : ailleurs on ne perd pas une saisie ou une vérification en cours)
    if (document.visibilityState === "visible" && state.me && state.view === "tableau" && !document.querySelector("dialog[open]")) {
      renderTableau().catch(() => {});
    }
  });
  boot();
})();
