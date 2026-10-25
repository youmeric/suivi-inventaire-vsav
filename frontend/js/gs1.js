/* Lecture des codes DataMatrix GS1 des boîtes de médicaments et du lecteur caméra.
   Un code de boîte contient, sans séparateur visible : (01) GTIN sur 14 chiffres,
   (17) date de péremption AAMMJJ, (10) numéro de lot, (21) numéro de série.
   Les champs de longueur variable (lot, série) se terminent par le caractère GS (ASCII 29). */
(function () {
  "use strict";

  const GS = "\u001d";
  // longueur fixe des identifiants courants (le reste est variable, terminé par GS)
  const FIXED = { "00": 18, "01": 14, "02": 14, "11": 6, "12": 6, "13": 6, "15": 6, "16": 6, "17": 6, "20": 2 };
  const VARIABLE = ["10", "21", "22", "240", "241", "30", "710", "711", "712", "713", "714"];

  function expiryToIso(yymmdd) {
    if (!/^\d{6}$/.test(yymmdd)) return null;
    const y = 2000 + Number(yymmdd.slice(0, 2));
    const m = Number(yymmdd.slice(2, 4));
    let d = Number(yymmdd.slice(4, 6));
    if (m < 1 || m > 12) return null;
    const last = new Date(y, m, 0).getDate();
    if (d === 0) d = last; // « 00 » = fin du mois
    if (d > last) return null;
    return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }

  /** Texte du code -> { gtin, peremption (AAAA-MM-JJ), lot, serie } ou null si ce n'est pas un code GS1. */
  function parse(raw) {
    let s = String(raw || "");
    s = s.replace(/^\]d2|^\]C1|^\]Q3/, ""); // préfixe d'identification ajouté par certains lecteurs
    s = s.replace(/\(\s*(\d{2,4})\s*\)/g, (m, ai) => GS + ai); // forme lisible « (01)… (17)… »
    s = s.replace(/^\u001d+/, "");
    const out = {};
    let i = 0;
    let guard = 0;
    while (i < s.length && guard++ < 20) {
      if (s[i] === GS) { i++; continue; }
      const ai2 = s.slice(i, i + 2);
      const ai3 = s.slice(i, i + 3);
      if (FIXED[ai2] !== undefined) {
        const v = s.slice(i + 2, i + 2 + FIXED[ai2]);
        out[ai2] = v;
        i += 2 + FIXED[ai2];
        continue;
      }
      const ai = VARIABLE.includes(ai3) ? ai3 : VARIABLE.includes(ai2) ? ai2 : null;
      if (!ai) break;
      const end = s.indexOf(GS, i + ai.length);
      const v = s.slice(i + ai.length, end < 0 ? s.length : end);
      out[ai] = v;
      i = end < 0 ? s.length : end + 1;
    }
    if (!out["01"] && !out["17"]) return null;
    return {
      gtin: /^\d{14}$/.test(out["01"] || "") ? out["01"] : "",
      peremption: out["17"] ? expiryToIso(out["17"]) : null,
      lot: (out["10"] || "").trim(),
      serie: (out["21"] || "").trim(),
    };
  }

  // ---- caméra : BarcodeDetector quand le navigateur l'a (Android), sinon ZXing (iPhone) ----

  let zxingPromise = null;
  function loadZxing() {
    if (window.ZXing) return Promise.resolve(window.ZXing);
    if (!zxingPromise) {
      zxingPromise = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "/vendor/zxing/zxing.min.js";
        s.onload = () => resolve(window.ZXing);
        s.onerror = () => { zxingPromise = null; reject(new Error("Lecteur de code introuvable")); };
        document.head.appendChild(s);
      });
    }
    return zxingPromise;
  }

  async function nativeDetector() {
    if (!("BarcodeDetector" in window)) return null;
    try {
      const formats = await window.BarcodeDetector.getSupportedFormats();
      return formats.includes("data_matrix") ? new window.BarcodeDetector({ formats: ["data_matrix"] }) : null;
    } catch { return null; }
  }

  /** Lance la caméra dans `video` ; `onCode(texte)` à la première lecture. Renvoie une fonction d'arrêt. */
  async function startCamera(video, onCode) {
    let stopped = false;
    const detector = await nativeDetector();
    if (detector) {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
      video.srcObject = stream;
      await video.play();
      const tick = async () => {
        if (stopped) return;
        try {
          const codes = await detector.detect(video);
          if (codes.length && codes[0].rawValue) { onCode(codes[0].rawValue); return; }
        } catch { /* image pas encore prête */ }
        setTimeout(tick, 150);
      };
      tick();
      return () => { stopped = true; stream.getTracks().forEach((t) => t.stop()); video.srcObject = null; };
    }
    const ZX = await loadZxing();
    const hints = new Map([[ZX.DecodeHintType.POSSIBLE_FORMATS, [ZX.BarcodeFormat.DATA_MATRIX]], [ZX.DecodeHintType.TRY_HARDER, true]]);
    const reader = new ZX.BrowserMultiFormatReader(hints, 150);
    await reader.decodeFromConstraints({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false }, video, (result) => {
      if (result && !stopped) { stopped = true; onCode(result.getText()); }
    });
    return () => { stopped = true; reader.reset(); };
  }

  /** Lecture d'une photo (secours si la caméra en direct ne marche pas). */
  async function decodeImage(file) {
    const url = URL.createObjectURL(file);
    try {
      const detector = await nativeDetector();
      if (detector) {
        const img = await createImageBitmap(file);
        const codes = await detector.detect(img);
        if (codes.length) return codes[0].rawValue;
      }
      const ZX = await loadZxing();
      const hints = new Map([[ZX.DecodeHintType.POSSIBLE_FORMATS, [ZX.BarcodeFormat.DATA_MATRIX]], [ZX.DecodeHintType.TRY_HARDER, true]]);
      const reader = new ZX.BrowserMultiFormatReader(hints);
      const result = await reader.decodeFromImageUrl(url);
      return result.getText();
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  window.GS1 = { parse, expiryToIso, startCamera, decodeImage };
})();
