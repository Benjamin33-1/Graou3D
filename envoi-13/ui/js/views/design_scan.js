/* Conception (5.8.5) : scan 3D par photos. 30 à 80 photos tout autour d'un objet (figurine, pièce, maquette…)
   deviennent un volume fermé imprimable. Les photos viennent du téléphone par le wifi (Graou3D sur le téléphone,
   menu Scan 3D) ou d'un dossier du PC. Le calcul tourne sur le PC (backend/design/photo_scan.py) : positions des
   photos, profondeur vue par chaque photo, volume. Posé sur la feuille de repère, l'objet sort à la bonne taille. */
(function () {
  "use strict";
  const { h } = BC;
  const D = BC.design;
  const view = D.view;

  const sc = (view.scan = {
    info: null, list: null, id: "", cur: null, thumbs: [], quality: "normal", show: "photos",
    opts: { size_mm: "", axis: "z", base_mm: 0, smooth: "2", faces: "2" }, mergeWith: "", timer: null, aiTimer: null, loading: false,
  });
  const SMOOTH = { 0: "Aucun", 1: "Léger", 2: "Conseillé", 3: "Fort" };   // « Moyen » se traduit mal (moyen de paiement)
  const SMOOTH_HELP = {
    0: "Surface brute, telle que calculée.",
    1: "Enlève le grain le plus fin, garde les arêtes vives.",
    2: "Surface propre, détails gardés.",
    3: "Très lisse : les petits détails et les arêtes s'arrondissent.",
  };
  const QUALITY_HELP = {
    draft: "Brouillon : 5 à 10 min, détails de 1 mm environ.",
    normal: "Normale : 10 à 20 min, détails de 0,5 mm environ.",
    fine: "Fine : 30 à 60 min, détails de 0,3 mm, beaucoup de mémoire.",
  };
  const STATE = { photos: "Photos prêtes", running: "Calcul en cours", done: "Terminé", error: "Échec", cancelled: "Annulé" };

  async function load() {
    if (!sc.info) {
      const r = await BC.call("scan_info");
      if (r.ok !== false) sc.info = r;
    }
    const res = await BC.call("scan_list");
    if (res.ok !== false) {
      sc.list = res.scans || [];
      if (!sc.id && sc.list.length) sc.id = sc.list[0].id;
      sc.cur = sc.list.find((x) => x.id === sc.id) || null;
      if (sc.cur) await loadThumbs();
    }
    poll();
    BC.render("design");
  }
  async function loadThumbs() {
    const res = await BC.call("scan_thumbs", sc.id);
    sc.thumbs = res.ok !== false ? res.thumbs || [] : [];
  }
  async function refresh() {
    if (!sc.id) return;
    const res = await BC.call("scan_status", sc.id);
    if (res.ok === false) return;
    const before = sc.cur || {};
    sc.cur = res.scan;
    if (sc.cur.count !== before.count) await loadThumbs();
    if (before.state === "running" && sc.cur.state === "done") {
      if (sc.cur.merge_msg && sc.cur.merge_msg !== before.merge_msg) BC.toast(sc.cur.merge_msg, "danger");
      else if (sc.cur.merged && (!before.merged || before.merged.at !== sc.cur.merged.at)) { BC.toast("Les deux faces sont combinées.", "success"); sc.opts.faces = "2"; }
      else BC.toast("Scan 3D terminé.", "success");
      sc.show = "photos";
    }
    if (before.state === "running" && sc.cur.state === "error") BC.toast(sc.cur.error || "Le scan a échoué.", "danger");
    if (view.tab === "scan") BC.render("design");
  }
  function poll() {
    clearInterval(sc.timer);
    sc.timer = setInterval(() => {
      const c = sc.cur;
      if (BC.state.view !== "design" || view.tab !== "scan" || !c || !(c.state === "running" || c.receiving)) return;
      refresh();
    }, 1500);
  }

  /* ------------------------------------------------------------- panneau */
  function listBlock() {
    const list = sc.list || [];
    return h`<div class="row tight wrap">
      ${list.length ? h`<select class="input grow" data-change="scan-pick">${BC.join(list, (x) => h`<option value="${x.id}" ${x.id === sc.id ? "selected" : ""}>${x.title} · ${x.count} photo(s) · ${STATE[x.state] || ""}</option>`)}</select>` : ""}
      <button class="btn sm" data-action="scan-new">${BC.icon("plus", "sm")}Nouveau scan</button></div>`;
  }

  function photosBlock(c) {
    const rx = c.receiving;
    const running = c.state === "running";
    return h`<div class="stack tight"><strong class="small">2. Les photos (${c.count})</strong>
      ${rx && rx.open ? h`<div class="design-cut stack tight scan-receive">
          <div class="row between"><strong class="small">${BC.icon("phone", "sm")}Réception ouverte</strong>
            <button class="btn ghost sm" data-action="scan-receive-stop">Fermer</button></div>
          <div class="tiny">Sur le téléphone : Graou3D › <b>Scan 3D</b>, puis tapez ce code :</div>
          <div class="scan-code">${rx.code.slice(0, 3)} ${rx.code.slice(3)}</div>
          <div class="tiny faint">${rx.paired ? "Téléphone connecté. " : ""}Adresse du PC si le téléphone ne le trouve pas : ${(rx.addresses || []).join(" ou ") || "?"} (port ${rx.port}).
            Le téléphone et le PC doivent être sur le même wifi ; Windows peut demander d'autoriser Graou3D sur le réseau privé.</div>
        </div>` : h`<div class="row tight wrap">
          <button class="btn sm primary" data-action="scan-receive" ${running ? "disabled" : ""}>${BC.icon("phone", "sm")}Recevoir du téléphone</button>
          <button class="btn sm" data-action="scan-files" ${running ? "disabled" : ""}>${BC.icon("folder", "sm")}Photos du PC…</button></div>`}
      ${c.count && c.count < (sc.info && sc.info.min_photos || 12) ? h`<div class="tiny warn-text">${BC.icon("alert", "sm")}Il faut au moins ${sc.info.min_photos || 12} photos (30 à 80 conseillées).</div>` : ""}
    </div>`;
  }

  function runBlock(c) {
    const q = sc.info && sc.info.qualities || { draft: "Brouillon", normal: "Normale", fine: "Fine" };
    if (c.state === "running") {
      return h`<div class="stack tight"><strong class="small">3. Reconstruction</strong>
        <div class="progress"><i style="width:${c.pct || 0}%"></i></div>
        <div class="tiny">${c.msg || "Calcul…"} (${c.pct || 0} %)</div>
        <div class="tiny faint">Vous pouvez continuer à utiliser Graou3D. Le calcul se poursuit même si vous changez d'onglet.</div>
        <button class="btn ghost sm" data-action="scan-cancel">${BC.icon("stop", "sm")}Annuler</button></div>`;
    }
    return h`<div class="stack tight"><strong class="small">3. Reconstruction</strong>
      <div class="seg">${BC.join(Object.entries(q), ([k, label]) => h`<button type="button" class="${sc.quality === k ? "active" : ""}" data-action="scan-quality" data-q="${k}">${label}</button>`)}</div>
      <div class="tiny faint">${QUALITY_HELP[sc.quality]}</div>
      ${aiBlock()}
      ${c.state === "error" ? h`<div class="notice warn">${BC.icon("alert")}<div class="small">${c.error}</div></div>` : ""}
      ${c.msg && c.state !== "done" ? h`<div class="tiny faint">${c.msg}</div>` : ""}
      <button class="btn ${c.result ? "" : "primary"}" data-action="scan-start" ${c.count >= (sc.info && sc.info.min_photos || 12) ? "" : "disabled"}>${BC.icon("play", "sm")}${c.result ? "Relancer la reconstruction" : "Lancer la reconstruction"}</button></div>`;
  }

  /* 5.8.8 : IA de profondeur (facultative, 95 Mo) : silhouette et relief des objets unis, blancs ou peu texturés */
  function aiBlock() {
    const a = sc.info && sc.info.ai;
    if (!a) return "";
    const ins = a.install || {};
    if (ins.running) {
      const pct = ins.total ? Math.round((100 * (ins.done || 0)) / ins.total) : 0;
      return h`<div class="design-cut stack tight"><strong class="small">${BC.icon("download", "sm")}Téléchargement de l'IA de profondeur</strong>
        <div class="progress"><i style="width:${pct}%"></i></div>
        <div class="tiny">${ins.step === "check" ? "Vérification du fichier…" : `${pct} % de ${a.mb} Mo`}</div>
        <button class="btn ghost sm" data-action="scan-ai-cancel">Annuler</button></div>`;
    }
    if (!a.installed) {
      return h`<div class="design-cut stack tight"><strong class="small">${BC.icon("sparkle", "sm")}IA de profondeur (conseillée)</strong>
        <div class="tiny">Devine le relief de chaque photo, même sur une surface unie ou blanche : la silhouette de l'objet est bien plus juste. Gratuite, elle tourne sur ce PC (environ 1 s par photo).</div>
        ${ins.error ? h`<div class="tiny warn-text">${BC.icon("alert", "sm")}${ins.error}</div>` : ""}
        <button class="btn sm" data-action="scan-ai-install">${BC.icon("download", "sm")}Installer (${a.mb} Mo)</button></div>`;
    }
    return h`<div class="row tight wrap between"><span class="small">${BC.icon("sparkle", "sm")}IA de profondeur</span>
      <div class="seg">${BC.join([[true, "Activée"], [false, "Coupée"]], ([k, l]) => h`<button type="button" class="${a.enabled === k ? "active" : ""}" data-action="scan-ai-enable" data-on="${k ? "1" : "0"}">${l}</button>`)}</div>
      <button class="btn ghost sm" data-action="scan-ai-remove" title="Effacer l'IA de ce PC">${BC.icon("trash", "sm")}</button></div>`;
  }

  function resultBlock(c) {
    if (!c.result) return "";
    const i = c.info || {};
    const o = sc.opts;
    return h`<div class="stack tight"><strong class="small">4. Le modèle</strong>
      <div class="row wrap tight tiny">
        <span class="badge ${i.placed >= 0.9 * i.photos ? "ok" : "warn"}">${i.placed}/${i.photos} photos placées</span>
        ${i.scaled ? h`<span class="badge ok">À l'échelle : ${i.sheet_dots} pastilles</span>` : h`<span class="badge warn">Échelle à donner</span>`}
        ${i.depth_ai ? h`<span class="badge ok">IA de profondeur : ${i.depth_ai} photos</span>` : ""}
        ${i.seconds ? h`<span class="badge">${Math.round(i.seconds / 60)} min</span>` : ""}</div>
      ${i.scaled ? h`<div class="tiny faint">Taille réelle mesurée grâce à la feuille de repère. Pour imprimer plus grand ou plus petit, donnez une taille ci-dessous.</div>`
        : h`<div class="tiny faint">Sans la feuille de repère, la forme est juste mais pas la taille : donnez une longueur réelle (voiture : sa longueur ; figurine : sa hauteur) ou la taille voulue à l'impression.</div>`}
      <div class="row tight wrap">
        <label class="field grow"><span>${i.scaled ? "Taille voulue (mm, facultatif)" : "Longueur réelle ou voulue (mm)"}</span>
          <input class="input" type="number" min="1" max="5000" step="0.1" value="${o.size_mm}" data-change="scan-opt" data-k="size_mm" /></label>
        <label class="field"><span>Mesurée sur</span><select class="input" data-change="scan-opt" data-k="axis">
          ${BC.join([["z", "la hauteur"], ["x", "la longueur (x)"], ["y", "la largeur (y)"]], ([k, l]) => h`<option value="${k}" ${o.axis === k ? "selected" : ""}>${l}</option>`)}</select></label></div>
      ${facesBlock(c)}
      <div class="field"><span>Lissage de la surface (enlève le grain des photos)</span>
        <div class="seg">${BC.join(Object.entries(SMOOTH), ([k, label]) => h`<button type="button" class="${String(o.smooth) === k ? "active" : ""}" data-action="scan-smooth" data-s="${k}">${label}</button>`)}</div>
        <div class="tiny faint">${SMOOTH_HELP[o.smooth] || ""}</div></div>
      <label class="field"><span>Raboter la base (mm) : socle bien plat, sans le reste de la table</span>
        <input class="input" type="number" min="0" max="50" step="0.2" value="${o.base_mm}" data-change="scan-opt" data-k="base_mm" /></label>
      <div class="row wrap tight">
        <button class="btn primary" data-action="scan-open" ${!i.scaled && !(Number(o.size_mm) > 0) ? "disabled" : ""}>${BC.icon("cube", "sm")}Ouvrir le modèle en 3D</button>
        <button class="btn" data-action="scan-stl" ${!i.scaled && !(Number(o.size_mm) > 0) ? "disabled" : ""}>${BC.icon("download", "sm")}Télécharger le .stl</button></div>
      <div class="tiny faint">Le .stl est enregistré dans le dossier du scan, avec la taille, le lissage et la base choisis ci-dessus. Dans l'aperçu 3D : Échelle, Découpe (s'il dépasse du plateau), Texte & QR, puis Slicer Express comme pour tout modèle.</div></div>`;
  }

  /* 5.8.7 : scan en deux faces. Une série pièce posée normalement, une série pièce retournée : chaque série bouche
     ce qu'elle ne voit pas, Graou3D recale les deux et garde le vrai dessous. */
  function facesBlock(c) {
    const m = c.merged;
    const list = (sc.list || []).filter((x) => x.id !== c.id && x.result);
    if (c.face_of) {
      const top = (sc.list || []).find((x) => x.id === c.face_of);
      return h`<div class="design-cut stack tight"><strong class="small">Face retournée</strong>
        <div class="tiny faint">Ce scan montre le dessous de « ${top ? top.title : "l'autre scan"} ». Combinez-les pour avoir la pièce entière.</div>
        <button class="btn sm" data-action="scan-merge-from" ${top && top.result ? "" : "disabled"}>${BC.icon("layers", "sm")}Combiner avec « ${top ? top.title : "?"} »</button></div>`;
    }
    if (m) {
      return h`<div class="design-cut stack tight"><strong class="small">Deux faces</strong>
        <div class="row wrap tight tiny"><span class="badge ok">Combiné avec « ${m.other_title || "la face retournée"} »</span>
          <span class="badge">recalage à ${String(m.rms_mm).replace(".", ",")} mm</span></div>
        <div class="seg">${BC.join([["2", "Pièce entière"], ["1", "Ce scan seul"]], ([k, l]) => h`<button type="button" class="${String(sc.opts.faces) === k ? "active" : ""}" data-action="scan-faces" data-f="${k}">${l}</button>`)}</div></div>`;
    }
    return h`<details class="design-notice" id="scan-faces"><summary class="small">Le dessous manque ? Scannez l'autre face</summary>
      <div class="stack tight">
        <div class="tiny faint">Retournez la pièce sur la feuille et faites une deuxième série de photos. Graou3D recale les deux scans sur leurs flancs et garde le vrai dessus de l'un et le vrai dessous de l'autre. La feuille de repère doit se voir sur les deux séries.</div>
        ${c.merge_msg ? h`<div class="notice warn tiny">${BC.icon("alert", "sm")}<div>${c.merge_msg}</div></div>` : ""}
        <button class="btn sm" data-action="scan-other-face">${BC.icon("camera", "sm")}Scanner l'autre face (pièce retournée)</button>
        ${list.length ? h`<div class="row tight wrap"><select class="input grow" data-change="scan-merge-pick">
            <option value="">Ou combiner avec un scan déjà fait…</option>
            ${BC.join(list, (x) => h`<option value="${x.id}" ${sc.mergeWith === x.id ? "selected" : ""}>${x.title}</option>`)}</select>
          <button class="btn sm" data-action="scan-merge" ${sc.mergeWith ? "" : "disabled"}>Combiner</button></div>` : ""}
      </div></details>`;
  }

  function guide() {
    return h`<details class="design-notice" id="scan-guide"><summary class="small">1. Bien prendre les photos</summary>
      <ul class="tiny tight-list">
        <li>Objet posé au milieu de la <b>feuille de repère</b> : il sortira à la bonne taille. Imprimez-la à 100 % (taille réelle), jamais « ajuster à la page ».</li>
        <li>Objet <b>blanc ou très clair</b> : prenez la feuille à cadre noir, l'objet se détache mieux du papier.</li>
        <li>Tournez autour : une photo tous les 10 à 15°, sur 3 hauteurs : <b>au ras de la table</b> (téléphone presque posé, c'est ce tour qui montre le bas de l'objet), à 45°, presque d'au-dessus.</li>
        <li>Approchez-vous : l'objet doit remplir presque toute la photo. Petit objet plat (voiture miniature) : 60 à 80 photos.</li>
        <li>L'objet net et assez grand sur chaque photo ; une ou deux photos où toute la feuille se voit.</li>
        <li>Lumière douce et égale, pas de flash. Objet brillant, transparent ou tout uni : un voile de bombe mate (ou de talc) aide beaucoup.</li>
        <li>Ne bougez pas l'objet pendant la série. Gros objet (voiture) : tournez autour à pied, même règles.</li>
      </ul>
      <div class="row tight wrap">
        <button class="btn sm" data-action="scan-sheet" data-f="A4">${BC.icon("file", "sm")}Feuille A4</button>
        <button class="btn sm" data-action="scan-sheet" data-f="A3" title="Pour un objet plus grand que 15 × 21 cm">Feuille A3</button>
        <button class="btn sm" data-action="scan-sheet" data-f="A4" data-dark="1" title="Pour un objet blanc ou très clair">A4 cadre noir</button>
        <button class="btn sm" data-action="scan-sheet" data-f="A3" data-dark="1">A3 cadre noir</button></div></details>`;
  }

  D.tabs.scan = {
    render: () => {
      if (!sc.list) return h`<div class="empty"><span class="btn ghost busy"></span>Lecture des scans…</div>`;
      if (sc.info && !sc.info.available) {
        return h`<div class="stack"><h3>Scan 3D par photos</h3><div class="notice warn">${BC.icon("alert")}<div class="small">${sc.info.missing}</div></div></div>`;
      }
      const c = sc.cur;
      return h`<div class="stack">
        <div class="row between"><h3>Scan 3D par photos</h3>${BC.tip("Des photos tout autour d'un objet deviennent un modèle 3D imprimable. Tout est calculé sur ce PC, sans service en ligne.")}</div>
        ${listBlock()}
        ${c ? h`${guide()}
          <div class="row tight"><input class="input grow" maxlength="60" value="${c.title}" data-change="scan-title" title="Nom du scan" />
            <button class="btn ghost sm icon-only" data-action="scan-folder" title="Ouvrir le dossier du scan (photos et résultat)">${BC.icon("folder", "sm")}</button>
            <button class="btn ghost sm icon-only" data-action="scan-delete" title="Supprimer ce scan">${BC.icon("trash", "sm")}</button></div>
          ${photosBlock(c)}${runBlock(c)}${resultBlock(c)}` : h`<div class="tiny faint">Créez un scan, puis envoyez-lui les photos.</div>`}
      </div>`;
    },
    stage: () => {
      const c = sc.cur;
      if (!c || sc.show === "3d") return null;
      if (!sc.thumbs.length) {
        return h`<div class="card design-stage"><div class="empty">${BC.icon("camera")}<div>Les photos du scan s'afficheront ici au fur et à mesure.</div></div></div>`;
      }
      return h`<div class="card design-stage scan-stage">
        <div class="design-stage-head"><div class="grow"><strong>${c.title}</strong> <span class="tiny faint">${c.count} photo(s)</span></div>
          ${view.current ? h`<button class="btn ghost sm" data-action="scan-show" data-s="3d">Voir la 3D</button>` : ""}</div>
        <div class="scan-thumbs">${BC.join(sc.thumbs, (t) => h`<figure><img src="data:image/jpeg;base64,${t.jpeg}" alt="" />
          ${c.state !== "running" ? h`<button class="btn ghost sm icon-only" data-action="scan-photo-del" data-n="${t.name}" title="Retirer cette photo">${BC.icon("x", "sm")}</button>` : ""}</figure>`)}</div></div>`;
    },
    onShow: () => { sc.list = null; load(); },
  };

  /* ------------------------------------------------------------- actions */
  BC.actions["scan-new"] = async (el) => {
    const res = await BC.busy(el, () => BC.run("scan_new", ""));
    if (!res) return;
    sc.id = res.scan.id;
    sc.show = "photos";
    sc.opts = { size_mm: "", axis: "z", base_mm: 0, smooth: "2", faces: "2" };
    await load();
  };
  BC.changes["scan-pick"] = async (el) => {
    sc.id = el.value;
    sc.show = "photos";
    await load();
  };
  BC.changes["scan-title"] = async (el) => {
    const res = await BC.run("scan_rename", sc.id, el.value);
    if (res) { sc.cur = res.scan; await load(); }
  };
  BC.actions["scan-delete"] = async () => {
    if (!sc.cur) return;
    const ok = await BC.confirm(`Supprimer le scan « ${sc.cur.title} » et ses ${sc.cur.count} photo(s) ?`, { ok: "Supprimer", danger: true });
    if (!ok) return;
    if (await BC.run("scan_delete", sc.id)) { sc.id = ""; sc.cur = null; await load(); }
  };
  BC.actions["scan-receive"] = async (el) => {
    const res = await BC.busy(el, () => BC.run("scan_receive", sc.id));
    if (res) { sc.cur = res.scan; poll(); BC.render("design"); }
  };
  BC.actions["scan-receive-stop"] = async () => { await BC.run("scan_receive_stop"); await refresh(); };
  BC.actions["scan-files"] = async (el) => {
    const res = await BC.busy(el, () => BC.run("scan_add_files", sc.id));
    if (!res) return;
    if (res.added) BC.toast(`${res.added} photo(s) ajoutée(s)${res.skipped ? `, ${res.skipped} ignorée(s)` : ""}.`, "success");
    await loadThumbs();
    await refresh();
  };
  BC.actions["scan-photo-del"] = async (el) => {
    if (await BC.run("scan_remove_photo", sc.id, el.dataset.n)) { await loadThumbs(); await refresh(); }
  };
  BC.actions["scan-sheet"] = (el) => BC.busy(el, () => BC.run("design_photo_sheet", el.dataset.dark === "1", true, el.dataset.f));
  BC.actions["scan-folder"] = () => BC.run("scan_open_folder", sc.id);
  BC.actions["scan-faces"] = (el) => { sc.opts.faces = el.dataset.f; BC.render("design"); };
  BC.changes["scan-merge-pick"] = (el) => { sc.mergeWith = el.value; BC.render("design"); };
  BC.actions["scan-other-face"] = async (el) => {
    const res = await BC.busy(el, () => BC.run("scan_other_face", sc.id));
    if (!res) return;
    sc.id = res.scan.id;
    sc.show = "photos";
    await load();
    const rx = await BC.run("scan_receive", sc.id);
    if (rx) { sc.cur = rx.scan; poll(); BC.render("design"); }
    BC.toast("Retournez la pièce sur la feuille, puis prenez la deuxième série avec le téléphone.", "info");
  };
  async function merge(el, top, bottom) {
    const res = await BC.busy(el, () => BC.run("scan_merge", top, bottom));
    if (!res) return;
    sc.id = top;
    sc.show = "photos";
    await load();
    sc.cur = res.scan;
    poll();
    BC.render("design");
  }
  BC.actions["scan-merge"] = (el) => merge(el, sc.id, sc.mergeWith);
  BC.actions["scan-merge-from"] = (el) => merge(el, sc.cur.face_of, sc.id);
  BC.actions["scan-smooth"] = (el) => { sc.opts.smooth = el.dataset.s; BC.render("design"); };
  async function aiCall(name, ...args) {
    const res = await BC.run(name, ...args);
    if (res && res.ai && sc.info) sc.info.ai = res.ai;
    BC.render("design");
    return res;
  }
  function aiWatch() {
    clearInterval(sc.aiTimer);
    sc.aiTimer = setInterval(async () => {
      const res = await BC.call("scan_ai_status");
      if (res.ok === false || !sc.info) return clearInterval(sc.aiTimer);
      sc.info.ai = res.ai;
      const ins = res.ai.install || {};
      if (!ins.running) {
        clearInterval(sc.aiTimer);
        if (ins.step === "done") BC.toast("IA de profondeur installée : elle servira aux prochains calculs.", "success");
        if (ins.step === "error") BC.toast(ins.error || "Téléchargement impossible.", "danger");
      }
      if (view.tab === "scan") BC.render("design");
    }, 1000);
  }
  BC.actions["scan-ai-install"] = async () => { await aiCall("scan_ai_install"); aiWatch(); };
  BC.actions["scan-ai-cancel"] = () => aiCall("scan_ai_cancel");
  BC.actions["scan-ai-enable"] = (el) => aiCall("scan_ai_enable", el.dataset.on === "1");
  BC.actions["scan-ai-remove"] = async () => {
    if (!(await BC.confirm("Effacer l'IA de profondeur de ce PC ? Vous pourrez la réinstaller.")))  return;
    const res = await aiCall("scan_ai_remove");
    if (res && res.msg) BC.toast(res.msg, "success");
  };
  BC.actions["scan-quality"] = (el) => { sc.quality = el.dataset.q; BC.render("design"); };
  BC.actions["scan-start"] = async (el) => {
    const res = await BC.busy(el, () => BC.run("scan_start", sc.id, sc.quality));
    if (res) { sc.cur = res.scan; poll(); BC.render("design"); }
  };
  BC.actions["scan-cancel"] = async () => { const res = await BC.run("scan_cancel", sc.id); if (res) { sc.cur = res.scan; BC.render("design"); } };
  BC.changes["scan-opt"] = (el) => {
    sc.opts[el.dataset.k] = el.dataset.k === "axis" ? el.value : el.value;
    BC.render("design");
  };
  BC.actions["scan-show"] = (el) => { sc.show = el.dataset.s; BC.render("design"); };
  const scanOpts = () => { const o = sc.opts; return { size_mm: Number(o.size_mm) || 0, axis: o.axis, base_mm: Number(o.base_mm) || 0, smooth: String(o.smooth), faces: String(o.faces) }; };
  BC.actions["scan-stl"] = (el) => BC.busy(el, () => BC.run("scan_export_stl", sc.id, scanOpts(), true));
  BC.actions["scan-open"] = async (el) => {
    const res = await BC.busy(el, () => BC.run("scan_open", sc.id, scanOpts()));
    if (!res) return;
    sc.show = "3d";
    D.setCurrent(res.view);
  };
})();
