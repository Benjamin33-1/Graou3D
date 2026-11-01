/* =============================================================================
   demo_api.js — un atelier entièrement fictif : bobines, journal, entretien, amis
   et une fausse imprimante qui imprime (progression, températures, couches, temps restant).

   Sert à deux choses :
   - le mode démo de l'application (demo.js) : remplace l'API Python côté interface ;
   - l'aperçu développeur (ui/dev/mock_api.js, index.html?dev=1).
   Tout reste en mémoire : rien n'est écrit dans le compte ni envoyé à une imprimante.

   Ajouter des méthodes fictives pour une nouvelle fonction (devis, entretien…) :
     G3D_FAKE.extend((ctx) => ({
       async quote_list() { return ctx.ok({ quotes: ctx.db.quotes || [] }); },
     }));
   à appeler au chargement d'un script (avant ou après celui-ci), ou dans la section en bas de ce fichier.
   `ctx` donne db (données), tele (télémétrie), sim (imprimante), ok, wait, find, remove, newId, now, ago, push (événement),
   api (méthodes déjà définies, pour en envelopper une), opts (options de create) et jobs (impressions fictives).
   ========================================================================== */
(function () {
  "use strict";
  const extensions = [];
  const G3D_FAKE = (window.G3D_FAKE = {
    extend(fn) { if (typeof fn === "function") extensions.push(fn); },
    create,
  });

  const NOZZLES = ["0.2mm Inox", "0.4mm Inox", "0.4mm Acier Trempé", "0.6mm Acier Trempé", "0.8mm Acier Trempé"];
  const MATERIALS = ["PLA Basic", "PLA Matte", "PLA Tough", "PLA Silk", "PETG HF", "PETG Basic", "TPU 95A", "ABS", "ASA", "PA / PAHT", "PC"];
  G3D_FAKE.NOZZLES = NOZZLES;
  G3D_FAKE.MATERIALS = MATERIALS;

  // Températures sous 250 °C : au-delà, un secret du Graoully se déclenche.
  // Impressions que la fausse imprimante enchaîne : nom, couches, durée (min), grammes, emplacement AMS, °C buse / plateau.
  const JOBS = [
    { name: "Engrenage planétaire", layers: 210, minutes: 166, grams: 140, slot: 3, nozzle: 240, bed: 70 },
    { name: "Benchy", layers: 120, minutes: 45, grams: 12, slot: 1, nozzle: 220, bed: 60 },
    { name: "Support casque", layers: 160, minutes: 192, grams: 62, slot: 1, nozzle: 220, bed: 60 },
    { name: "Mirabelle géante", layers: 90, minutes: 54, grams: 18, slot: 4, nozzle: 225, bed: 60 },
  ];

  /**
   * Crée une fausse API.
   * opts : empty (compte vide), tame (série de 10 réussites), loggedIn (session ouverte, vrai par défaut),
   *        profile (remplace le profil), onboarding (réglage de l'assistant), speed (% d'avancement par seconde),
   *        autoRestart (relance une impression après une pause, vrai par défaut).
   *        Options lues par les extensions en bas de fichier (ctx.opts) : demo (vrai mode démo de l'application),
   *        features (Réglages › Fonctions), stockRules (règles de stock d'exemple), updates ("available", "uptodate", "off"),
   *        support (signalement de problème), migration (table des devis absente), pushTopic (canal ntfy).
   */
  function create(opts = {}) {
    const EMPTY = !!opts.empty;
    const wait = (ms = 120) => new Promise((r) => setTimeout(r, ms));
    let nextId = 100;
    const now = () => new Date().toISOString();
    const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
    const nowS = () => Math.floor(Date.now() / 1000);

    function withCaps(pr) { return Object.assign({}, pr, { caps: caps(pr.protocol) }); }
    function caps(p) {
      const bambu = p === "BAMBU_LAN" || p === "BAMBU_CLOUD", lan = p !== "BAMBU_CLOUD";
      return { bambu, files: lan, storage: bambu ? "Carte SD" : p === "PRUSALINK" ? "Clé USB" : "Fichiers", upload_types: bambu ? [".3mf", ".gcode"] : [".gcode", ".bgcode"],
        timelapses: p === "BAMBU_LAN" || p === "KLIPPER", camera: p === "BAMBU_CLOUD" ? "" : bambu ? "stream" : "webcam", gcode: p !== "PRUSALINK", light: bambu, ams: bambu,
        fans: bambu ? ["part", "aux", "chamber"] : p === "PRUSALINK" ? [] : ["part"], speed: p !== "PRUSALINK", skip_object: p === "BAMBU_LAN" || p === "KLIPPER" };
    }

    const db = {
      loggedIn: opts.loggedIn !== false,
      profile: Object.assign({ id: "u-1", pseudo: "Eragon_33", friend_code: "BMB-K7QX-M3PA", avatar_data: "", email: "maker@exemple.fr" }, opts.profile || {}),
      settings: {
        theme: opts.theme || "dark", active_nozzle: "0.4mm Acier Trempé",
        features: { trophies: true, filament_check: true, stock_alerts: false, quotes: false, accounting: false, real_cost: false, presentation: false, slicer_watch: false },
        nozzle_wear: { "0.2mm Inox": 3.5, "0.4mm Inox": 120, "0.4mm Acier Trempé": 310.4, "0.6mm Acier Trempé": 12, "0.8mm Acier Trempé": 0 },
        poop_chute: { current_grams: 96, max_capacity_grams: 150, flushes_count: 14 },
        stats: { success_streak: opts.tame ? 10 : 3, prints_success: 37, prints_failed: 4, total_grams_printed: 4210, total_hours_printed: 96.5, maint_completed_count: 3 },
        eggs_found: ["graoully", "spaghetti", "benchy"],
        alerts: { email_finish: false, email_failure: true, email_clog: true, email_defect: true, sound: true },
        thermal: { band_c: 4, window_s: 120, ratio: 0.35, z_tolerance_mm: 1, repeat_count: 2 },
        ai: { enabled: true, interval_s: 60, notify_level1: true, notify_photo: true, autopause: false, autopause_confidence: 0.85, autopause_frames: 3 },
        slicer: { bambu_studio_path: "", printer_model: "A1", nozzle: "0.4" },
        quote: { spool_price: 23, kwh_price: 0.25, wear_per_hour: 0.35, margin_pct: 10, watts: 95, labor_rate: 25, setup_fee: 5,
          hardware: [{ label: "Insert fileté laiton M3", unit: "pièce", price: 0.08 }, { label: "Insert fileté laiton M4", unit: "pièce", price: 0.12 },
            { label: "Aimant néodyme 6 × 3 mm", unit: "pièce", price: 0.1 }, { label: "Aimant néodyme 10 × 3 mm", unit: "pièce", price: 0.25 },
            { label: "Vis M3 inox", unit: "pièce", price: 0.05 }, { label: "Vis M4 inox", unit: "pièce", price: 0.08 },
            { label: "Apprêt et peinture", unit: "pièce", price: 1.5 }, { label: "Temps de post-traitement", unit: "h", price: 20 }] },
        offpeak: { start: "22:00", end: "06:00", kwh_price: 0.2068 }, scheduled_prints: [],
        remote: { enabled: false, cmd_topic: "" },
        drying: EMPTY ? [] : [{ id: "d1", spool_id: 3, label: "PETG HF Bleu cobalt", temp: 65, hours: 8, start_at: nowS() - 3 * 3600, end_at: nowS() + 5 * 3600, color_hex: "#1F5FBF" }],
        print_log: EMPTY ? [] : [
          { id: "j1", ts: nowS() - 3600, file: "Support casque", success: true, hours: 3.2, grams: 62, cost: 3.17, material: "PLA Matte", color_hex: "#8E9089", spool: "Bambu Lab Gris cendre", layer: 0, z: 0, photo: true },
          { id: "j2", ts: nowS() - 86400, file: "Vase spirale", success: false, hours: 1.4, grams: 31, cost: 1.24, material: "PETG HF", color_hex: "#1F5FBF", spool: "", layer: 141, z: 28.2, photo: false },
          { id: "j3", ts: nowS() - 3 * 86400, file: "Mirabelle géante", success: true, hours: 0.9, grams: 18, cost: 0.76, material: "PLA Silk", color_hex: "#D4A537", spool: "Sunlu Or", layer: 0, z: 0, photo: true },
          { id: "j4", ts: nowS() - 6 * 86400, file: "Benchy", success: true, hours: 0.75, grams: 12, cost: 0.56, material: "PLA Basic", color_hex: "#00AE42", spool: "Bambu Lab Vert Bambu", layer: 0, z: 0, photo: false },
        ],
      },
      spools: EMPTY ? [] : [
        { id: 1, brand: "Bambu Lab", material: "PLA Matte", color_name: "Gris cendre", color_hex: "#8E9089", weight_initial: 1000, weight_remaining: 640, ams_slot: 1, k_factor: 0.02, flow_ratio: 0.98, max_volumetric_speed: 21, z_offset: 0, days_open: 9 },
        { id: 2, brand: "Bambu Lab", material: "PLA Basic", color_name: "Vert Bambu", color_hex: "#00AE42", weight_initial: 1000, weight_remaining: 85, ams_slot: 2, k_factor: 0.02, flow_ratio: 0.98, max_volumetric_speed: 21, z_offset: 0, days_open: 40 },
        { id: 3, brand: "Bambu Lab", material: "PETG HF", color_name: "Bleu cobalt", color_hex: "#1F5FBF", weight_initial: 1000, weight_remaining: 910, ams_slot: 3, k_factor: 0.024, flow_ratio: 0.97, max_volumetric_speed: 18, z_offset: 0, days_open: 21, needs_drying: true },
        { id: 4, brand: "Sunlu", material: "PLA Silk", color_name: "Or", color_hex: "#D4A537", weight_initial: 1000, weight_remaining: 450, ams_slot: 4, k_factor: 0.02, flow_ratio: 0.98, max_volumetric_speed: 16, z_offset: 0, days_open: 3 },
        { id: 5, brand: "Bambu Lab", material: "TPU 95A", color_name: "Noir", color_hex: "#1B1B1B", weight_initial: 1000, weight_remaining: 1000, ams_slot: 0, k_factor: 0.02, flow_ratio: 0.98, max_volumetric_speed: 3.6, z_offset: 0, days_open: 0 },
        { id: 6, brand: "Elegoo", material: "PLA Basic", color_name: "Blanc", color_hex: "#F2F2F2", weight_initial: 1000, weight_remaining: 70, ams_slot: 0, k_factor: 0.02, flow_ratio: 0.98, max_volumetric_speed: 21, z_offset: 0, days_open: 60 },
      ],
      tasks: EMPTY ? [] : [
        { id: 11, title: "Support casque", material: "PLA Matte", estimated_grams: 62, required_nozzle: "0.4mm", makerworld_url: "https://makerworld.com/fr/models/1", status: "todo" },
        { id: 12, title: "Clip de câble ×10", material: "PLA Basic", estimated_grams: 18, required_nozzle: "0.4mm", makerworld_url: "", status: "todo" },
        { id: 13, title: "Engrenage planétaire", material: "PETG HF", estimated_grams: 140, required_nozzle: "0.6mm", makerworld_url: "", status: "printing" },
        { id: 14, title: "Benchy", material: "PLA Basic", estimated_grams: 12, required_nozzle: "0.4mm", makerworld_url: "", status: "done" },
      ],
      maintenance: EMPTY ? [] : [
        { id: 21, title: "Graisser l'axe Y", interval_hours: 100, last_done_hours: 20 },
        { id: 22, title: "Nettoyer la buse (cold pull)", interval_hours: 50, last_done_hours: 60 },
      ],
      shopping: EMPTY ? [] : [
        { id: 31, name: "Buse 0.4 acier trempé", category: "Pièce", priority: "Urgent", done: false },
        { id: 32, name: "PLA Matte noir ×2", category: "Filament", priority: "Normale", done: false },
        { id: 33, name: "Inserts M3", category: "Quincaillerie", priority: "Normale", done: true },
      ],
      projects: EMPTY ? [] : [{ id: 41, name: "Bras robotique", total_parts: 9, printed_parts: 4, material: "PLA Tough", total_grams: 380, bom: ["8× vis M3x12", "8× inserts M3", "2× roulements 608ZZ"] }],
      bookmarks: EMPTY ? [] : [
        { id: 51, title: "Boîte à outils modulaire", author: "Maker42", category: "Rangement", url: "https://makerworld.com/fr/models/2" },
        { id: 52, title: "Dragon articulé", author: "McGybeer", category: "Jouets", url: "https://www.printables.com/model/3" },
      ],
      printer: { configured: true, protocol: "BAMBU_LAN", name: "Bambu Lab A1", host: "192.168.1.52", serial: "03919C461801192", port: null, camera_url: "", cloud_email: "", has_secret: true, pinned_ports: ["8883", "990"] },
      friends: EMPTY ? [] : [
        { id: "u-2", pseudo: "Sayou", friend_code: "BMB-PL4N-77RT", avatar_data: "", since: ago(9000), stats: { prints_success: 52, prints_failed: 3, total_grams_printed: 6120 },
          spools: [{ id: 201, brand: "Bambu Lab", material: "PLA Matte", color_name: "Rouge", color_hex: "#C0392B", weight_remaining: 720, ams_slot: 1 },
            { id: 202, brand: "Bambu Lab", material: "PETG HF", color_name: "Transparent", color_hex: "#D9E4EC", weight_remaining: 300, ams_slot: 0 }] },
        { id: "u-3", pseudo: "Venoxy", friend_code: "BMB-QQ2Z-H8KM", avatar_data: "", since: ago(20000), stats: { prints_success: 18, prints_failed: 6, total_grams_printed: 1900 },
          spools: [{ id: 203, brand: "Polymaker", material: "PLA Pro", color_name: "Orange", color_hex: "#F28C28", weight_remaining: 860, ams_slot: 2 }] },
      ],
      incoming: EMPTY ? [] : [{ id: 301, user_id: "u-4", pseudo: "Captain", friend_code: "BMB-CAPT-9X2L", avatar_data: "", created_at: ago(30) }],
      outgoing: EMPTY ? [] : [{ id: 302, user_id: "u-5", pseudo: "endertales", friend_code: "BMB-END3-TL55", created_at: ago(300) }],
      feed: [],
      borrows: EMPTY ? [] : [
        { id: 501, direction: "incoming", requester: "Venoxy", owner: "", spool_label: "Bambu Lab PLA Matte Gris cendre", grams: 120, message: "Pour finir mon dragon !", status: "pending", created_at: ago(45) },
        { id: 502, direction: "outgoing", requester: "", owner: "Sayou", spool_label: "Bambu Lab PETG HF Transparent", grams: 80, message: "", status: "accepted", created_at: ago(3000) },
      ],
      incidents: EMPTY ? [] : [
        { id: 601, kind: "thermal_oscillation", file_name: "Engrenage planétaire", layer_num: 88, z_mm: 17.6, created_at: ago(200) },
        { id: 602, kind: "failed", file_name: "Vase spirale", layer_num: 141, z_mm: 28.2, created_at: ago(4000) },
        { id: 603, kind: "failed", file_name: "Vase spirale v2", layer_num: 143, z_mm: 28.6, created_at: ago(9000) },
        { id: 604, kind: "defect_ai", file_name: "Support casque", layer_num: 12, z_mm: 2.4, created_at: ago(15000) },
      ],
    };
    if (opts.onboarding) db.settings.onboarding = Object.assign({}, opts.onboarding);
    db.borrows.forEach((b) => { if (b.direction === "incoming") b.owner = db.profile.pseudo; else b.requester = db.profile.pseudo; });
    if (!EMPTY) db.feed = [
      { id: 401, user_id: "u-2", pseudo: "Sayou", avatar_data: "", title: "Lampe lithophane", material: "PLA Basic", grams: 96, duration_h: 4.5, notes: "Remplissage 100 %, 0,12 mm.\nRendu génial avec une LED chaude.", makerworld_url: "https://makerworld.com/fr/models/4", image_data: "", preset_name: "lithophane.json", has_preset: true, created_at: ago(120), likes: 3, liked: false, mine: false },
      { id: 402, user_id: "u-1", pseudo: db.profile.pseudo, avatar_data: "", title: "Engrenage planétaire", material: "PETG HF", grams: 140, duration_h: 6, notes: "", makerworld_url: "", image_data: "", preset_name: "", has_preset: false, created_at: ago(1500), likes: 5, liked: true, mine: true },
    ];

    /* ------------------------------------------------- Fausse imprimante */
    const tele = {
      connected: true, mode: "BAMBU_LAN", state: "RUNNING", gcode_state: "RUNNING", nozzle_temp: 239.6, nozzle_target: 240, bed_temp: 70, bed_target: 70,
      progress: 42, remaining_min: 96, layer_num: 88, total_layer_num: 210, subtask_name: "Engrenage planétaire", speed_level: 2, chamber_light: true,
      ams_trays: [
        { slot: 1, material: "PLA Matte", type: "PLA", color_hex: "#8E9089", color_name: "PLA Matte", k: 0.02, remain: 64 },
        { slot: 2, material: "PLA Basic", type: "PLA", color_hex: "#00AE42", color_name: "PLA Basic", k: 0.02, remain: 9 },
        { slot: 3, material: "PETG HF", type: "PETG", color_hex: "#1F5FBF", color_name: "PETG HF", k: 0.024, remain: 91 },
        { slot: 4, material: "PLA Silk", type: "PLA", color_hex: "#D4A537", color_name: "PLA Silk", k: 0.02, remain: 45 },
      ],
      ams_firmware: "00.00.08.15", printer_firmware: "01.04.00.00", active_tray: 3, hms: [], printing: true, camera: false,
    };
    const events = [];
    const SPEED = opts.speed || 0.35;                      // % d'avancement par seconde réelle
    const sim = { job: Object.assign({}, JOBS[0]), progress: 42, last: Date.now(), idleSince: 0, next: 1, t: 0 };
    // Courbe de température : 120 mesures, la buse a été un peu instable il y a quelques minutes.
    const curve = [];
    for (let i = 0; i < 120; i++) curve.push([-(600 - i * 5), 240 + Math.sin(i / 3) * (i > 70 && i < 100 ? 5.5 : 1.2) + (Math.random() - 0.5), 240]);

    function thermal() {
      const last = curve.slice(-60).filter((c) => c[2] > 0);
      const rms = last.length ? Math.sqrt(last.reduce((s, c) => s + (c[1] - c[2]) ** 2, 0) / last.length) : 0;
      const level = rms > 2.5 ? "warning" : "ok";
      return { level, label: level === "ok" ? "Stable" : "Légère instabilité", band_c: 4, deviation_rms: Math.round(rms * 10) / 10, curve };
    }
    const approach = (v, target, rate, dt) => (Math.abs(target - v) <= rate * dt ? target : v + Math.sign(target - v) * rate * dt);

    function tick() {
      const t = Date.now();
      const dt = Math.min(10, (t - sim.last) / 1000);
      sim.last = t;
      sim.t += dt;
      const st = tele.gcode_state;
      const nozzleGoal = tele.nozzle_target || 26, bedGoal = tele.bed_target || 24;
      if (st === "RUNNING") {
        sim.progress = Math.min(100, sim.progress + dt * SPEED * (sim.rate || 1));   // rate : vitesse Sieste → Furie
        tele.nozzle_temp = +(tele.nozzle_target + Math.sin(sim.t / 6) * 0.9 + (Math.random() - 0.5) * 0.9).toFixed(1);
        tele.bed_temp = +(tele.bed_target + (Math.random() - 0.5) * 0.4).toFixed(1);
        if (sim.progress >= 100) finish();
      } else {
        tele.nozzle_temp = +approach(tele.nozzle_temp, nozzleGoal, tele.nozzle_target ? 7 : 3, dt).toFixed(1);
        tele.bed_temp = +approach(tele.bed_temp, bedGoal, tele.bed_target ? 2.5 : 0.8, dt).toFixed(1);
        if (st === "PREPARE" && Math.abs(tele.nozzle_temp - tele.nozzle_target) < 1 && Math.abs(tele.bed_temp - tele.bed_target) < 1) setState("RUNNING");
        if ((st === "FINISH" || st === "FAILED" || st === "IDLE") && opts.autoRestart !== false && tele.connected && Date.now() - sim.idleSince > 45000) {
          start(JOBS[sim.next % JOBS.length]);
          sim.next++;
        }
      }
      const j = sim.job;
      tele.progress = Math.floor(sim.progress);
      tele.layer_num = Math.max(st === "RUNNING" ? 1 : 0, Math.ceil(sim.progress / 100 * j.layers));
      tele.total_layer_num = j.layers;
      tele.remaining_min = Math.max(0, Math.round((100 - sim.progress) / 100 * j.minutes / (sim.rate || 1)));
      tele.subtask_name = j.name;
      if (dt > 0) {
        curve.push([0, tele.nozzle_temp, tele.nozzle_target]);
        curve.shift();
        curve.forEach((c) => (c[0] -= 2));
      }
    }
    function setState(s) {
      tele.gcode_state = tele.state = s;
      tele.printing = s === "RUNNING" || s === "PREPARE";
    }
    function start(job, slot) {
      sim.job = Object.assign({}, job, slot ? { slot } : {});
      sim.progress = 0;
      tele.nozzle_target = sim.job.nozzle;
      tele.bed_target = sim.job.bed;
      tele.active_tray = sim.job.slot;
      setState("PREPARE");
      events.push({ type: "print_started", file: sim.job.name });
    }
    const spoolAt = (slot) => db.spools.find((s) => Number(s.ams_slot) === Number(slot));
    function logPrint(success) {
      const j = sim.job, sp = spoolAt(j.slot);
      const share = success ? 1 : sim.progress / 100;
      const grams = Math.max(1, Math.round(j.grams * share)), hours = Math.round(j.minutes * share / 60 * 100) / 100;
      const q = db.settings.quote;
      const cost = Math.round((grams / 1000 * q.spool_price + hours * q.watts / 1000 * q.kwh_price + hours * q.wear_per_hour) * 100) / 100;
      let emptied = false;
      if (sp) {
        const old = sp.weight_remaining;
        sp.weight_remaining = Math.max(0, old - grams);
        emptied = old > 0 && sp.weight_remaining === 0;
        const tray = tele.ams_trays.find((x) => x.slot === Number(j.slot));
        if (tray) tray.remain = Math.round(sp.weight_remaining / (sp.weight_initial || 1000) * 100);
      }
      const entry = "j" + nextId++;
      db.settings.print_log.unshift({ id: entry, ts: nowS(), file: j.name, success, hours, grams, cost, material: sp ? sp.material : "",
        color_hex: sp ? sp.color_hex : "#9CA3AF", spool: sp ? `${sp.brand} ${sp.color_name}` : "", layer: success ? 0 : tele.layer_num,
        z: success ? 0 : Math.round(tele.layer_num * 0.2 * 10) / 10, photo: true });
      const s = db.settings.stats;
      if (success) {
        s.prints_success++; s.success_streak++; s.total_grams_printed += grams;
        s.total_hours_printed = Math.round((s.total_hours_printed + hours) * 100) / 100;
        const n = db.settings.active_nozzle;
        db.settings.nozzle_wear[n] = Math.round(((db.settings.nozzle_wear[n] || 0) + hours) * 100) / 100;
      } else { s.prints_failed++; s.success_streak = 0; }
      const task = db.tasks.find((x) => x.status === "printing" && x.title === j.name);
      if (task && success) task.status = "done";
      const usage = sp ? [{ slot: Number(j.slot), spool_id: sp.id, label: `${sp.material} ${sp.color_name}`, material: sp.material, color_hex: sp.color_hex, grams }] : [];
      return { grams, emptied, usage, entry };
    }
    function finish() {
      sim.progress = 100;
      setState("FINISH");
      tele.nozzle_target = 0;
      tele.bed_target = 0;
      sim.idleSince = Date.now();
      const r = logPrint(true);
      events.push({ type: "print_finished", file: sim.job.name, grams: r.grams, emptied: r.emptied, usage: r.usage, entry: r.entry, source: "file" });
    }
    function fail() {
      setState("FAILED");
      tele.nozzle_target = 0;
      tele.bed_target = 0;
      sim.idleSince = Date.now();
      logPrint(false);
      events.push({ type: "print_failed", file: sim.job.name });
    }
    const busy = () => ["RUNNING", "PREPARE", "PAUSE"].includes(tele.gcode_state);
    function startFile(name, slot) {
      if (!tele.connected) return { ok: false, msg: "Imprimante non connectée." };
      if (busy()) return { ok: false, msg: "Une impression est déjà en cours." };
      const title = String(name || "Impression").split("/").pop().replace(/(\.gcode)?\.(3mf|gcode|bgcode|stl|obj)$/i, "").replace(/_/g, " ");
      const known = JOBS.find((j) => j.name.toLowerCase() === title.toLowerCase());
      start(Object.assign({}, known || { name: title, layers: 140, minutes: 75, grams: 40, slot: 1, nozzle: 220, bed: 60 }), Number(slot) || undefined);
      return null;
    }

    /* ------------------------------------------------- Caméra et vignettes dessinées */
    const frames = {};
    function cameraFrame() {
      const key = tele.gcode_state === "RUNNING" || tele.gcode_state === "PAUSE" ? Math.floor(sim.progress / 4) : "x" + tele.gcode_state;
      if (frames[key]) return frames[key];
      const c = document.createElement("canvas");
      c.width = 640; c.height = 360;
      const g = c.getContext("2d");
      const grad = g.createLinearGradient(0, 0, 0, 360);
      grad.addColorStop(0, "#2a2f35"); grad.addColorStop(1, "#111417");
      g.fillStyle = grad; g.fillRect(0, 0, 640, 360);
      g.fillStyle = "#151a1f"; g.fillRect(80, 280, 480, 26);
      const tray = tele.ams_trays.find((x) => x.slot === tele.active_tray) || {};
      const color = tray.color_hex || "#1F5FBF";
      const hgt = Math.round(140 * Math.min(1, sim.progress / 100));
      g.fillStyle = color; g.beginPath(); g.ellipse(320, 280, 70, 14, 0, 0, Math.PI * 2); g.fill();
      if (hgt > 0) { g.fillRect(250, 280 - hgt, 140, hgt); g.fillStyle = "#ffffff"; g.globalAlpha = 0.18; g.beginPath(); g.ellipse(320, 280 - hgt, 70, 14, 0, 0, Math.PI * 2); g.fill(); g.globalAlpha = 1; }
      const y = 280 - hgt - 70;
      g.fillStyle = "#d9dde1"; g.fillRect(290, y, 60, 60); g.fillStyle = "#00AE42"; g.fillRect(290, y + 45, 60, 8);
      g.fillStyle = "#c9a24a"; g.beginPath(); g.moveTo(312, y + 60); g.lineTo(328, y + 60); g.lineTo(322, y + 72); g.lineTo(318, y + 72); g.fill();
      g.fillStyle = "#7f8a94"; g.font = "14px Consolas, monospace"; g.fillText("DÉMO — caméra simulée", 16, 24);
      frames[key] = c.toDataURL("image/jpeg", 0.85);
      return frames[key];
    }
    function thumb(seed) {
      const c = document.createElement("canvas");
      c.width = 160; c.height = 160;
      const g = c.getContext("2d");
      g.fillStyle = "#1c2026"; g.fillRect(0, 0, 160, 160);
      const colors = ["#DA5A04", "#1F5FBF", "#00AE42", "#D4A537"];
      g.fillStyle = colors[seed % colors.length];
      g.beginPath(); g.moveTo(80, 28); g.lineTo(132, 120); g.lineTo(28, 120); g.closePath(); g.fill();
      g.fillStyle = "rgba(255,255,255,.18)"; g.fillRect(28, 120, 104, 10);
      return c.toDataURL("image/png");
    }

    const ok = (extra = {}) => Object.assign({ ok: true }, extra);
    const live = () => Object.assign({}, tele, { thermal: thermal(), ai: aiStatus() });
    const dashboard = () => ok({ profile: db.profile, settings: db.settings, spools: db.spools, tasks: db.tasks, maintenance: db.maintenance,
      shopping: db.shopping, projects: db.projects, bookmarks: db.bookmarks, printer: withCaps(db.printer), telemetry: live() });
    let aiLast = { status: "warning", confidence: 0.72, defects: [{ type: "stringing", severity: "low", description: "Fins fils entre les dents de l'engrenage.", label: "Fils (stringing)" }], frame_id: "a1b2c3", sources: ["règles", "apprentissage"],
      should_pause: false, advice: "Augmentez un peu la rétraction ou séchez la bobine de PETG.", summary: "Impression correcte avec un léger stringing." };
    const engine = { ready: true, missing: "", model: "", model_error: "", onnx_available: true, learned_ok: 5, learned_defect: 2,
      min_per_class: 8, learned_active: false, learned_accuracy: null, frames_dir: "C:/Users/demo/AppData/Local/Graou3D/vision/frames" };
    const aiStatus = () => ({ busy: false, last_run: Date.now() / 1000 - 90, last_result: aiLast, last_error: "", engine });
    const find = (list, id) => list.find((x) => String(x.id) === String(id));
    const remove = (list, id) => { const i = list.findIndex((x) => String(x.id) === String(id)); if (i >= 0) list.splice(i, 1); };
    const after = (res, extra) => res || ok(extra);

    let slicerJob = null;
    let anim = true;
    const api = {
      async set_animations(on) { anim = on === true; return ok({ anim }); },
      async auth_restore() { await wait(300); return db.loggedIn ? ok({ profile: db.profile, settings: db.settings }) : { ok: false, msg: "" }; },
      async auth_login(email, pw) { await wait(400); if (!pw) return { ok: false, msg: "Mot de passe manquant." }; db.loggedIn = true; return ok({ profile: db.profile, settings: db.settings }); },
      async auth_register(email) { await wait(400); return ok({ verification_required: true, email, msg: "Compte créé. Entrez le code reçu par email." }); },
      async auth_verify() { db.loggedIn = true; return ok({ profile: db.profile, settings: db.settings }); },
      async auth_resend() { return ok({ msg: "Nouveau code envoyé." }); },
      async auth_reset_start() { return ok({ msg: "Si ce compte existe, un code a été envoyé par email." }); },
      async auth_reset_finish() { db.loggedIn = true; return ok({ profile: db.profile, settings: db.settings }); },
      async auth_logout() { db.loggedIn = false; return ok(); },
      async profile_save(pseudo, avatar) { db.profile.pseudo = pseudo; if (avatar) db.profile.avatar_data = avatar; return ok({ profile: db.profile }); },
      async password_change() { return ok({ msg: "Mot de passe modifié." }); },
      async dashboard() { await wait(); tick(); return dashboard(); },
      async live() { tick(); return ok({ telemetry: live(), events: events.splice(0) }); },
      async settings_save(patch) { for (const [k, v] of Object.entries(patch || {})) db.settings[k] = v && typeof v === "object" && !Array.isArray(v) ? Object.assign({}, db.settings[k], v) : v; return ok({ settings: db.settings }); },
      async push_test() { await wait(400); return ok({ msg: "Notification envoyée : regardez votre téléphone." }); },
      async desktop_test() { await wait(300); return ok({ msg: "Dans la démo, aucune notification Windows n'est affichée." }); },
      async data_export_all() { return { ok: false, msg: "Dans la démo, il n'y a pas de données à exporter." }; },
      async client_export() { return { ok: false, msg: "Dans la démo, il n'y a pas de données à exporter." }; },
      async client_anonymize() { return { ok: false, msg: "Pas d'anonymisation dans la démo." }; },
      async account_delete() { return { ok: false, msg: "Le compte de démonstration ne peut pas être supprimé." }; },
      async schedule_add(path, slot, mode, at) {
        const startAt = mode === "at" ? new Date(at).getTime() / 1000 : (() => { const d = new Date(); d.setHours(22, 0, 0, 0); return d.getTime() / 1000; })();
        const item = { id: String(nextId++), path, name: path.split("/").pop(), slot, mode, start_at: startAt, grams: 42, hours: 3.5 };
        db.settings.scheduled_prints = [...(db.settings.scheduled_prints || []), item];
        return ok({ item, scheduled_prints: db.settings.scheduled_prints, msg: `« ${item.name} » programmé. Laissez l'application ouverte.` });
      },
      async schedule_cancel(id) { db.settings.scheduled_prints = (db.settings.scheduled_prints || []).filter((x) => x.id !== id); return ok({ scheduled_prints: db.settings.scheduled_prints, msg: "Programmation annulée." }); },
      async alert_test() { await wait(500); return ok({ msg: "Email de test envoyé à l'adresse de votre compte." }); },
      async egg_found(id) { if (!db.settings.eggs_found.includes(id)) db.settings.eggs_found.push(id); return ok({ eggs_found: db.settings.eggs_found }); },
      async print_outcome(success, grams) { const s = db.settings.stats; if (success) { s.prints_success++; s.total_grams_printed += grams; } else s.prints_failed++; return ok({ settings: db.settings, emptied: false }); },
      async print_usage_fix(entry, before, after) {
        const sum = (rows) => (rows || []).reduce((a, r) => a + (Number(r.grams) || 0), 0);
        const delta = {};
        for (const r of before || []) if (r.spool_id) delta[r.spool_id] = (delta[r.spool_id] || 0) - Number(r.grams || 0);
        for (const r of after || []) if (r.spool_id) delta[r.spool_id] = (delta[r.spool_id] || 0) + Number(r.grams || 0);
        let emptied = false;
        for (const [id, g] of Object.entries(delta)) {
          const s = db.spools.find((x) => String(x.id) === String(id));
          if (!s || !g) continue;
          const old = s.weight_remaining;
          s.weight_remaining = Math.max(0, old - g);
          emptied = emptied || (old > 0 && s.weight_remaining === 0);
        }
        const total = sum(after);
        db.settings.stats.total_grams_printed = Math.max(0, db.settings.stats.total_grams_printed + total - sum(before));
        const e = db.settings.print_log.find((x) => x.id === entry);
        if (e) { e.grams = total; if ((after || []).length > 1) e.colors = after.map((r) => [r.color_hex, r.grams]); }
        return ok({ emptied, msg: `Filament enregistré : ${total} g.` });
      },
      async parts_save(d, id) {
        const parts = db.settings.parts = db.settings.parts || [];
        const p = { id: id || "p" + nextId++, name: String(d.name || "").trim().slice(0, 60), category: d.category || "other",
          qty: Math.max(0, Math.round(Number(d.qty) || 0)), min: Math.max(0, Math.round(Number(d.min) || 0)), note: String(d.note || "").slice(0, 120) };
        if (!p.name) return { ok: false, msg: "Donnez un nom à la pièce." };
        const i = parts.findIndex((x) => x.id === p.id);
        if (i >= 0) parts[i] = p; else parts.push(p);
        return ok({ settings: db.settings, msg: `« ${p.name} » enregistrée.` });
      },
      async parts_adjust(id, delta) {
        const p = (db.settings.parts || []).find((x) => x.id === id);
        if (!p) return { ok: false, msg: "Pièce introuvable." };
        p.qty = Math.max(0, p.qty + Number(delta));
        return ok({ settings: db.settings, part: p, low: p.min > 0 && p.qty < p.min });
      },
      async parts_delete(id) { db.settings.parts = (db.settings.parts || []).filter((x) => x.id !== id); return ok({ settings: db.settings }); },
      async forecast_to_shopping() { return ok({ shopping: db.shopping, forecast: [], msg: "Dans la démo, le stock suffit pour la file d'attente." }); },
      async parts_to_shopping(id) {
        const p = (db.settings.parts || []).find((x) => x.id === id);
        if (!p) return { ok: false, msg: "Pièce introuvable." };
        db.shopping.unshift({ id: nextId++, name: p.name, category: "Pièce", priority: p.qty ? "Normale" : "Urgent", done: false });
        return ok({ msg: `« ${p.name} » ajoutée à la liste d'achats.` });
      },
      async print_add_grams(g) { db.settings.stats.total_grams_printed += g; return ok({ emptied: false, msg: `${g} g retirés de la bobine active.` }); },
      async incidents() { return ok({ incidents: db.incidents }); },
      async spool_add(d) { const q = Number(d.quantity) || 1; for (let i = 0; i < q; i++) db.spools.push(Object.assign({ id: nextId++, days_open: 0, weight_initial: Number(d.weight), weight_remaining: Number(d.weight) }, d, { ams_slot: i ? 0 : Number(d.ams_slot) })); return ok({ msg: `${q} bobine(s) ajoutée(s).` }); },
      async spool_update(id, d) {
        const s = Object.assign(find(db.spools, id), d);
        if ("weight" in d) s.weight_initial = Number(d.weight);
        for (const k of ["weight_remaining", "ams_slot", "k_factor", "flow_ratio", "max_volumetric_speed"]) if (k in d) s[k] = Number(d[k]);
        return ok();
      },
      async spool_adjust(id, delta) { const s = find(db.spools, id); const old = s.weight_remaining; s.weight_remaining = Math.max(0, old + delta); return ok({ weight_remaining: s.weight_remaining, emptied: old > 0 && s.weight_remaining === 0 }); },
      async spool_delete(id) { remove(db.spools, id); return ok(); },
      async spool_dried(id) { const s = find(db.spools, id); s.days_open = 0; s.needs_drying = false; return ok(); },
      async ams_import() { return ok({ msg: "0 bobine ajoutée, 4 bobines placées dans l'AMS." }); },
      async purge_add(g) { db.settings.poop_chute.current_grams += g; return ok({ poop_chute: db.settings.poop_chute }); },
      async purge_empty() { db.settings.poop_chute.current_grams = 0; return ok({ poop_chute: db.settings.poop_chute }); },
      async nozzle_set(n) { db.settings.active_nozzle = n; return ok({ settings: db.settings }); },
      async nozzle_hours_set(n, h) { db.settings.nozzle_wear[n] = Math.max(0, Number(h) || 0); return ok({ settings: db.settings }); },
      async nozzle_reset(n) { db.settings.nozzle_wear[n] = 0; return ok({ settings: db.settings }); },
      async task_add(d) { const t = Object.assign({ id: nextId++, status: "todo" }, d, { estimated_grams: Number(d.estimated_grams) || 0, required_nozzle: d.required_nozzle || "0.4mm" }); db.tasks.unshift(t); return ok({ task: t }); },
      async task_move(id, to) { const t = find(db.tasks, id); const was = t.status; t.status = to; return ok({ finished: to === "done" && was !== "done", emptied: false }); },
      async task_delete(id) { remove(db.tasks, id); return ok(); },
      async maint_add(title, interval) { db.maintenance.push({ id: nextId++, title, interval_hours: interval, last_done_hours: db.settings.stats.total_hours_printed }); return ok(); },
      async maint_done(id) { find(db.maintenance, id).last_done_hours = db.settings.stats.total_hours_printed; db.settings.stats.maint_completed_count++; return ok(); },
      async maint_delete(id) { remove(db.maintenance, id); return ok(); },
      async shop_add(name, category, priority) { db.shopping.unshift({ id: nextId++, name, category, priority, done: false }); return ok(); },
      async shop_toggle(id) { const it = find(db.shopping, id); it.done = !it.done; return ok(); },
      async shop_delete(id) { remove(db.shopping, id); return ok(); },
      async project_add(d) { db.projects.unshift({ id: nextId++, name: d.name, total_parts: Number(d.total_parts), printed_parts: 0, material: d.material, total_grams: Number(d.total_grams), bom: String(d.bom || "").split(",").map((s) => s.trim()).filter(Boolean) }); return ok(); },
      async project_step(id, d) { const p = find(db.projects, id); p.printed_parts = Math.max(0, Math.min(p.total_parts, p.printed_parts + d)); return ok(); },
      async project_to_shopping(id) { return ok({ msg: `${find(db.projects, id).bom.length} article(s) ajouté(s) à la liste d'achats.` }); },
      async project_delete(id) { remove(db.projects, id); return ok(); },
      async bookmark_add(d) { db.bookmarks.unshift(Object.assign({ id: nextId++ }, d)); return ok(); },
      async bookmark_delete(id) { remove(db.bookmarks, id); return ok(); },
      async open_url() { return ok({ msg: "Dans la démo, les liens ne s'ouvrent pas." }); },
      async social() { await wait(200); return ok({ overview: { me: Object.assign({ stats: db.settings.stats }, db.profile), friends: db.friends, incoming: db.incoming, outgoing: db.outgoing }, feed: db.feed, borrows: db.borrows }); },
      async friend_request(code) { await wait(300); if (code === db.profile.friend_code) return { ok: false, msg: "C'est votre propre code ami." }; db.outgoing.unshift({ id: nextId++, pseudo: "Nouveau maker", friend_code: code, created_at: now() }); return ok({ msg: "Demande envoyée à Nouveau maker." }); },
      async friend_respond(id, accept) { const r = find(db.incoming, id); remove(db.incoming, id); if (accept && r) db.friends.push({ id: r.user_id, pseudo: r.pseudo, friend_code: r.friend_code, avatar_data: "", since: now(), stats: { prints_success: 9, prints_failed: 1, total_grams_printed: 800 }, spools: [] }); return ok({ accepted: accept }); },
      async friend_cancel(id) { remove(db.outgoing, id); return ok(); },
      async friend_remove(id) { remove(db.friends, id); return ok(); },
      async post_publish(d) { db.feed.unshift(Object.assign({ id: nextId++, user_id: "u-1", pseudo: db.profile.pseudo, avatar_data: db.profile.avatar_data, created_at: now(), likes: 0, liked: false, mine: true, has_preset: !!d.preset_json }, d)); return ok(); },
      async post_delete(id) { remove(db.feed, id); return ok(); },
      async post_like(id) { const p = find(db.feed, id); p.liked = !p.liked; p.likes += p.liked ? 1 : -1; return ok({ liked: p.liked }); },
      async post_preset_save() { return ok({ msg: "Réglages enregistrés : Téléchargements\\Graou3D\\lithophane.json" }); },
      async borrow_request() { return ok({ msg: "Demande de prêt envoyée." }); },
      async borrow_respond(id, accept) { find(db.borrows, id).status = accept ? "accepted" : "declined"; return ok(); },
      async borrow_cancel(id) { find(db.borrows, id).status = "cancelled"; return ok(); },
      async printer_config() { return ok({ printer: withCaps(db.printer) }); },
      async printer_connect(cfg) { await wait(600); Object.assign(db.printer, { configured: true, protocol: cfg.protocol, name: cfg.name || db.printer.name, host: cfg.host || "" }); tele.connected = true; return ok({ msg: `Connecté à ${db.printer.name}.`, printer: withCaps(db.printer) }); },
      async printer_disconnect() { tele.connected = false; return ok({ msg: "Imprimante déconnectée." }); },
      async printer_reset_pin() { return ok({ msg: "Empreinte réinitialisée." }); },
      async cloud_login() { await wait(500); return ok({ devices: [{ serial: "03919C461801192", name: "A1 du salon", model: "Bambu Lab A1", online: true, lan_ready: true }] }); },
      async cloud_use_lan(serial, host) {
        await wait(900);
        if (!host) return ok({ ok: false, need_host: true, msg: "Imprimante introuvable sur le réseau. Entrez son adresse IP (écran de l'imprimante › Réglages › Réseau)." });
        db.printer = Object.assign({}, db.printer, { protocol: "BAMBU_LAN", host, serial }); tele.connected = true;
        return ok({ msg: "Connecté à A1 du salon.", printer: withCaps(db.printer), host });
      },
      async printer_discover() { await wait(1200); return ok({ printers: [{ serial: "03919C461801192", host: "192.168.1.52", name: "A1 du salon", model: "N2S" }] }); },
      async remote_set(on) { db.settings.remote = { enabled: !!on, cmd_topic: on ? "graouli-cmd-0123456789abcdef01234567" : db.settings.remote.cmd_topic }; return ok({ settings: db.settings }); },
      async remote_test() { return ok({ msg: "Notification envoyée avec ses boutons : regardez votre téléphone." }); },
      async drying_start(id, temp, hours) {
        const sp = db.spools.find((x) => String(x.id) === String(id)); const t = nowS();
        db.settings.drying = db.settings.drying.filter((d) => String(d.spool_id) !== String(id)).concat([{ id: "d" + nextId++, spool_id: sp.id, label: `${sp.material} ${sp.color_name}`, temp, hours, start_at: t, end_at: t + hours * 3600, color_hex: sp.color_hex }]);
        return ok({ msg: `Séchage lancé : ${sp.material} ${sp.color_name}, ${hours} h à ${temp} °C.` });
      },
      async drying_cancel(id) { db.settings.drying = db.settings.drying.filter((d) => d.id !== id); return ok({ msg: "Minuteur de séchage annulé." }); },
      async journal_photo(id) { await wait(300); const e = db.settings.print_log.find((x) => x.id === id); return ok({ photo: e && e.photo ? cameraFrame() : "" }); },
      async journal_delete(id) { db.settings.print_log = db.settings.print_log.filter((e) => e.id !== id); return ok({ msg: "Impression retirée du journal." }); },
      async timelapse_list() { await wait(700); return ok({ items: [
        { name: "video_2026-10-15_21-04-11.mp4", is_dir: false, size_mb: 38.2, date: "15 Oct 21:04", path: "/timelapse/video_2026-10-15_21-04-11.mp4", thumb: "/timelapse/thumbnail/video_2026-10-15_21-04-11.jpg" },
        { name: "video_2026-10-12_09-30-02.mp4", is_dir: false, size_mb: 112.6, date: "12 Oct 09:30", path: "/timelapse/video_2026-10-12_09-30-02.mp4", thumb: "" }] }); },
      async timelapse_thumb() { await wait(400); return ok({ src: cameraFrame() }); },
      async timelapse_download(path) { await wait(900); return ok({ msg: "Vidéo enregistrée : Téléchargements\\Graou3D\\timelapses\\" + path.split("/").pop() }); },
      async timelapse_delete(path) { await wait(300); return ok({ msg: `« ${path.split("/").pop()} » supprimé de la carte SD.` }); },
      async open_folder() { return ok(); },
      async makerworld_open() { return ok(); },
      async makerworld_keep_login(keep) { return ok({ keep_login: keep === true, msg: keep === true ? "Connexion gardée à partir du prochain démarrage." : "Plus rien n'est gardé à partir du prochain démarrage." }); },
      async makerworld_logout() { return ok({ msg: "Vous serez déconnecté de MakerWorld au prochain démarrage de Graou3D." }); },
      async makerworld_downloads() { await wait(300); return ok({ folder: "C:\\Users\\maker\\Downloads", items: [
        { token: "mw1", name: "Dragon articulé - Graoully edition.3mf", size: 6_200_000, mtime: nowS() - 600, sliced: false },
        { token: "mw2", name: "Clip câble.gcode.3mf", size: 900_000, mtime: nowS() - 86400, sliced: true },
        { token: "mw3", name: "support_casque.stl", size: 3_100_000, mtime: nowS() - 5 * 86400, sliced: false }] }); },
      async makerworld_thumb(token) { await wait(250); return ok({ thumb: token === "mw2" ? "" : thumb(token === "mw1" ? 0 : 1) }); },
      async sd_upload_picked() { await wait(800); return ok({ msg: "« Clip câble.gcode.3mf » envoyé sur la carte SD." }); },
      async cloud_send_code() { return ok({ msg: "Code envoyé par Bambu Lab à cette adresse." }); },
      async gcode_send() { return ok({ msg: "Commande envoyée." }); },
      async jog() { return ok({ msg: "Commande envoyée." }); },
      async temperature_set(target, value) {
        const v = Math.max(0, Math.min(target === "bed" ? 110 : 300, Number(value) || 0));
        if (target === "bed") tele.bed_target = v; else tele.nozzle_target = v;
        return ok({ msg: "Commande envoyée." });
      },
      async job_control(a) {
        const st = tele.gcode_state;
        if (a === "pause" && (st === "RUNNING" || st === "PREPARE")) setState("PAUSE");
        if (a === "resume" && st === "PAUSE") setState("RUNNING");
        if (a === "stop" && busy()) fail();
        return ok({ msg: { pause: "Pause envoyée.", resume: "Reprise envoyée.", stop: "Arrêt envoyé." }[a] });
      },
      async ams_refresh() { return ok({ msg: "Demande envoyée : l'AMS se met à jour dans quelques secondes." }); },
      async light_toggle() { tele.chamber_light = !tele.chamber_light; return ok({ on: tele.chamber_light }); },
      async melody_play() { return ok({ msg: "Commande envoyée." }); },
      async camera_start() { tele.camera = true; return ok(); },
      async camera_stop() { tele.camera = false; return ok(); },
      async camera_frame() { return ok({ streaming: true, frame: cameraFrame(), error: "" }); },
      async camera_snapshot() { return ok({ msg: "Cliché enregistré : Téléchargements\\Graou3D\\cliche.jpg" }); },
      async ai_status() { return ok({ engine }); },
      async ai_label(id, v) { if (v === "ok") engine.learned_ok++; else engine.learned_defect++; return ok({ engine, msg: "Merci, c'est noté." }); },
      async ai_forget() { engine.learned_ok = 0; engine.learned_defect = 0; return ok({ engine, msg: "Apprentissage effacé." }); },
      async ai_model_install() { engine.model = "Graoully-Vision v1"; return ok({ engine, msg: "Modèle installé : Graoully-Vision v1" }); },
      async ai_model_remove() { engine.model = ""; return ok({ engine, msg: "Modèle retiré." }); },
      async ai_open_frames() { return ok({}); },
      async ai_analyze_now() { await wait(1500); return ok({ result: aiLast }); },
      async ai_analyze_image() {
        await wait(1500);
        aiLast = { status: "failure", confidence: 0.91, should_pause: true, defects: [{ type: "spaghetti", severity: "high", description: "Fil emmêlé autour de la pièce, elle s'est décollée.", label: "Spaghetti (impression décrochée)" }],
          advice: "Arrêtez l'impression, nettoyez le plateau à l'alcool et vérifiez la première couche.", summary: "Défaut probable : spaghetti", frame_id: "d4e5f6", sources: ["apprentissage"] };
        return ok({ result: aiLast });
      },
      async sd_list(dir) {
        await wait(500);
        return ok({ directory: dir, items: dir === "/timelapse" ? [{ name: "video_2026-10-15_21-04.mp4", is_dir: false, size_mb: 38.2, date: "15 Oct 21:04", path: "/timelapse/video_2026-10-15_21-04.mp4" }] :
          [{ name: "cache", is_dir: true, size_mb: 0, date: "12 Oct 10:00", path: "/cache" }, { name: "timelapse", is_dir: true, size_mb: 0, date: "15 Oct 21:04", path: "/timelapse" },
            { name: "Engrenage_planetaire.gcode.3mf", is_dir: false, size_mb: 4.1, date: "16 Oct 08:10", path: "/Engrenage_planetaire.gcode.3mf" }, { name: "Benchy.gcode.3mf", is_dir: false, size_mb: 1.2, date: "02 Oct 19:40", path: "/Benchy.gcode.3mf" }] });
      },
      async sd_download() { await wait(600); return ok({ msg: "Fichier enregistré : Téléchargements\\Graou3D\\Benchy.gcode.3mf" }); },
      async sd_upload(name, data, startNow, slot) {
        await wait(800);
        if (startNow) return after(startFile(name, slot), { msg: `« ${name} » envoyé et impression lancée.` });
        return ok({ msg: `« ${name} » envoyé sur la carte SD.` });
      },
      async sd_print(path, slot) { return after(startFile(path, slot), { msg: "Impression lancée." }); },
      async sd_delete(path) { await wait(300); return ok({ msg: `« ${path.split("/").pop()} » supprimé de la carte SD.` }); },
      async file_analyze(name) { await wait(500); return ok({ analysis: { filename: name, title: name.replace(/(\.gcode)?\.(3mf|gcode)$/i, ""), grams: 48.6, duration_h: 2.35, nozzle: "0.4 mm", material: "PLA", layer_height: "0.2", thumbnail: "", stock: { ok: true, message: "Stock suffisant : 49 g nécessaires, 640 g sur Bambu Lab PLA Matte Gris cendre." } } }); },
      async slicer_info() {
        const orca = db.settings.slicer.engine === "orca";
        const paths = Object.assign({ bambu: "", orca: "" }, db.slicerPaths || {});
        if (orca) return ok({ paths, engine: "orca", engine_label: "OrcaSlicer", exe: "C:\\Program Files\\OrcaSlicer\\orca-slicer.exe", installed: { bambu: true, orca: true }, machines: ["Creality K1 (0.4 nozzle)", "Prusa MK4 0.4 nozzle", "Voron 2.4 350 0.4 nozzle", "Elegoo Neptune 4 Pro (0.4 nozzle)"], machine: db.settings.slicer.machine || "", found: !!db.settings.slicer.machine, qualities: ["0.12mm Fine @Creality K1 (0.4 nozzle)", "0.20mm Standard @Creality K1 (0.4 nozzle)", "0.28mm Draft @Creality K1 (0.4 nozzle)"] });
        return ok({ paths, engine: "bambu", engine_label: "Bambu Studio", installed: { bambu: true, orca: true }, exe: "C:\\Program Files\\Bambu Studio\\bambu-studio.exe", printer_model: "A1", nozzle: "0.4", found: true, machine: "Bambu Lab A1 0.4 nozzle",
          qualities: ["0.08mm Extra Fine @BBL A1", "0.12mm Fine @BBL A1", "0.16mm Optimal @BBL A1", "0.20mm Standard @BBL A1", "0.24mm Draft @BBL A1", "0.28mm Extra Draft @BBL A1"] });
      },
      async slicer_pick_file() { return ok({ file: { token: "t1", name: "dragon_articule.stl", size: 5_400_000 } }); },
      async slicer_pick_exe(engine) {
        const orca = engine === "orca";
        db.slicerPaths = Object.assign(db.slicerPaths || {}, { [orca ? "orca" : "bambu"]: orca ? "C:\\Program Files\\OrcaSlicer\\orca-slicer.exe" : "C:\\Program Files\\Bambu Studio\\bambu-studio.exe" });
        db.settings.slicer.engine = orca ? "orca" : "bambu";
        return ok({ settings: db.settings, path: db.slicerPaths[orca ? "orca" : "bambu"] });
      },
      async slicer_start(source, quality, slot) {
        slicerJob = { id: "job1", source: (source && source.name) || "dragon_articule.stl", status: "running", step: 1, steps: ["Préparation", "Découpe", "Vérification du stock", "Prêt"], message: "Découpe en cours dans Bambu Studio…", analysis: null, stock: null, elapsed_s: 0, slot, t0: Date.now() };
        return ok({ job: slicerJob });
      },
      async slicer_job() {
        const j = slicerJob;
        if (!j) return { ok: false, msg: "Aucune découpe en cours." };
        j.elapsed_s = Math.round((Date.now() - j.t0) / 1000);
        if (j.elapsed_s >= 3 && j.status === "running") {
          Object.assign(j, { status: "ready", step: 3, message: "Découpe terminée.", analysis: { grams: 74.2, duration_h: 3.1, layer_height: "0.2", plates: 1, thumbnail: "" },
            stock: j.slot === 2 ? { ok: false, message: "Stock insuffisant : 74 g nécessaires, seulement 85 g sur Bambu Lab PLA Basic Vert Bambu." } : { ok: true, message: "Stock suffisant : 74 g nécessaires, 640 g sur Bambu Lab PLA Matte Gris cendre." } });
        } else if (j.elapsed_s >= 2 && j.status === "running") j.step = 2;
        return ok({ job: j });
      },
      async slicer_send(id, print) {
        await wait(900);
        if (print) { const refused = startFile(slicerJob.source, slicerJob.slot); if (refused) return refused; }
        Object.assign(slicerJob, { status: print ? "printing" : "done", message: print ? "Impression lancée." : "Fichier envoyé sur la carte SD." });
        return ok({ job: slicerJob, msg: slicerJob.message });
      },
      async slicer_bambu_connect() { await wait(400); return ok({ msg: "Démo : Bambu Connect s'ouvrirait ici avec le fichier découpé." }); },
      async slicer_save() { return ok({ msg: "Fichier enregistré : Téléchargements\\Graou3D\\dragon_articule.gcode.3mf" }); },
      async slicer_discard() { return ok(); },
      async ai_add_photos(c) { await wait(400); return ok({ added: 12, msg: `12 photo(s) ajoutée(s) à « ${c} ».` }); },
      async filament_consensus() { await wait(200); return ok({ consensus: { makers: 14, k_factor: 0.022, flow_ratio: 0.975, max_volumetric_speed: 18.5 } }); },
      async filament_share() { await wait(300); return ok({ consensus: { makers: 15, k_factor: 0.022, flow_ratio: 0.975, max_volumetric_speed: 18.5 }, msg: "Merci ! Vos réglages sont partagés anonymement." }); },
      async labels_export_page() { await wait(300); return ok({ msg: "Page mobile prête : C:\\Users\\demo\\Downloads\\Graou3D\\page-mobile" }); },
      async preview_model() { return { ok: false, msg: "L'aperçu 3D n'est pas disponible dans la démo." }; },
      async preview_job() { return { ok: false, msg: "L'aperçu 3D n'est pas disponible dans la démo." }; },
      async legacy_import() { await wait(700); return ok({ token: "lg", name: "local_companion_store.json", summary: { spools: 6, tasks: 1, maintenance: 0, projects: 0, shopping: 0, bookmarks: 0, printer: true, contains_password: true } }); },
      async legacy_delete() { return ok({ msg: "Ancien fichier supprimé." }); },
    };

    const ctx = { db, tele, sim, ok, wait, find, remove, newId: () => nextId++, now, ago, push: (ev) => events.push(ev), cameraFrame, startFile, withCaps, get anim() { return anim; },
      api, opts, jobs: JOBS };
    // Les extensions enregistrées plus tard (script chargé après celui-ci) sont ajoutées par sync().
    let applied = 0;
    const sync = () => { while (applied < extensions.length) Object.assign(api, extensions[applied++](ctx) || {}); };
    sync();
    return { api, db, tele, ctx, sync };
  }

  /* -----------------------------------------------------------------------------
     Méthodes fictives des autres fonctions (devis, entretien, mises à jour…) :
     ajoutez ici un bloc G3D_FAKE.extend((ctx) => ({ ... })) par fonction.
     En mode démo (ctx.opts.demo), rien n'est écrit sur le disque : les méthodes qui
     enregistreraient un fichier répondent « réussi » avec un message qui le dit.
     --------------------------------------------------------------------------- */
  const r2 = (v) => Math.round((Number(v) + Number.EPSILON) * 100) / 100;
  const NO_FILE = "Dans la démo, aucun fichier n'est écrit sur votre PC.";
  const DATA = () => window.G3D_FAKE_DATA || {};

  /* ---- Fonctions facultatives (Réglages › Fonctions) et options du compte fictif */
  G3D_FAKE.extend((ctx) => {
    const s = ctx.db.settings, o = ctx.opts;
    Object.assign(s.features, o.features || {});
    Object.assign(s.alerts, { push_topic: o.pushTopic || "", push_maintenance: true, ui_maintenance: true, push_stock: true });
    Object.assign(s.ai, { alert_confidence: 0.6, skip_layers: 2 });
    s.stock_rules = o.stockRules && !o.empty ? [{ id: "r1", material: "PLA Basic", color: "", unit: "spools", min: 3 }, { id: "r2", material: "PETG HF", color: "", unit: "grams", min: 500 }] : [];
    s.stock_notified = {};
    s.maint_notified = {};
    return {
      async settings_save(patch) {
        for (const [k, v] of Object.entries(patch || {})) {
          if (k === "stock_rules") s.stock_rules = (Array.isArray(v) ? v : []).map((r) => Object.assign({ id: r.id || "r" + ctx.newId() }, r, { min: Math.max(1, Math.round(Number(r.min) || 1)) }));
          else s[k] = v && typeof v === "object" && !Array.isArray(v) ? Object.assign({}, s[k], v) : v;
        }
        return ctx.ok({ settings: s });
      },
    };
  });

  /* ---- 5.8 : GraouWeld (les calculs de soudure se font dans l'application installée) */
  G3D_FAKE.extend(() => {
    const MSG = "Démo : GraouWeld calcule dans Graou3D installé sur votre PC (Réglages › Fonctions).";
    const no = async () => ({ ok: false, msg: MSG });
    return {
      weld_info: no,
      weld_compute: no,
      weld_report: no,
      weld_coping_pdf: no,
      weld_tool: no,
      conception_inbox: async () => ({ ok: true, items: [] }),
      conception_take: no,
    };
  });

  /* ---- 5.8 : calibration sans caméra (les calculs se font dans l'application installée) */
  G3D_FAKE.extend((ctx) => {
    const NOZZLE = { PLA: [190, 230], PETG: [230, 260], TPU: [210, 240], ABS: [240, 270], ASA: [240, 270] };
    const fam = (m) => (/PETG/i.test(m) ? "PETG" : /TPU/i.test(m) ? "TPU" : /ABS/i.test(m) ? "ABS" : /ASA/i.test(m) ? "ASA" : "PLA");
    const CALC = "Démo : les calculs de calibration se font dans Graou3D installé sur votre PC.";
    return {
      async calib_info(id) {
        const sp = ctx.db.spools.find((x) => String(x.id) === String(id));
        if (!sp) return { ok: false, msg: "Bobine introuvable." };
        const f = fam(sp.material);
        return ctx.ok({ spool: sp, family: f, k_range: [0, 0.08, 0.005], vol_range: [5, 35, 0.5], nozzle_range: NOZZLE[f],
          entry: { at: {} }, printer: { key: "DEMO", label: "Bambu Lab A1 (démo)", model: "A1", firmware: "bambu", guess: "bambu" },
          printer_entry: { at: {} }, history: [], sheet: [], parts: false, firmwares: ["bambu", "klipper", "marlin"] });
      },
      async calib_compute() { return { ok: false, msg: CALC }; },
      async calib_save() { return { ok: false, msg: CALC }; },
      async calib_firmware(fw) { return ctx.ok({ firmware: fw }); },
      async calib_part() { return { ok: false, msg: "Les pièces de test se créent dans Graou3D installé sur votre PC." }; },
      async calib_preset() { return ctx.ok({ msg: NO_FILE }); },
    };
  });

  /* ---- Entretien conseillé et alertes (entretien dû, stock bas) ----------------- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, s = db.settings;
    // Un rappel déjà dû pour montrer le bandeau « Entretien à faire ».
    const coldPull = db.maintenance.find((m) => m.id === 22);
    if (coldPull) coldPull.last_done_hours = 40;
    const families = () => DATA().maintPresets || [];
    const norm = (t) => String(t).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    function detect() {
      const name = `${db.printer.name || ""} ${s.slicer.printer_model || ""}`.toLowerCase();
      if (/a1 ?mini/.test(name)) return "bambu_a1mini";
      if (/\ba1\b/.test(name)) return "bambu_a1";
      if (/p1[ps]/.test(name)) return "bambu_p1";
      if (/x1/.test(name)) return "bambu_x1";
      return db.printer.protocol === "KLIPPER" ? "voron" : db.printer.protocol === "OCTOPRINT" ? "octoprint" : "";
    }
    // Imitation de BambuBackendAPI._check_alerts : une seule alerte par échéance.
    function checkAlerts() {
      const hours = s.stats.total_hours_printed;
      const fresh = db.maintenance.filter((m) => hours - m.last_done_hours >= m.interval_hours && s.maint_notified[m.id] !== m.last_done_hours);
      fresh.forEach((m) => { s.maint_notified[m.id] = m.last_done_hours; });
      if (fresh.length && s.alerts.ui_maintenance !== false) {
        ctx.push({ type: "alert", kind: "maintenance", level: "warning", title: "Entretien à faire",
          message: fresh.length === 1 ? `« ${fresh[0].title} » est à faire (${hours} h au compteur). Cochez « Fait » dans Entretien une fois terminé.`
            : `${fresh.length} entretiens sont à faire : ${fresh.map((m) => `« ${m.title} »`).join(", ")}. Cochez « Fait » dans Entretien une fois terminés.` });
      }
      if (!s.features.stock_alerts) return;
      for (const r of s.stock_rules || []) {
        const key = `${r.material}|${r.color}|${r.unit}`.toLowerCase();
        const mine = db.spools.filter((x) => x.material.toLowerCase() === r.material.toLowerCase() && (!r.color || x.color_name.toLowerCase() === r.color.toLowerCase()));
        const have = r.unit === "grams" ? mine.reduce((a, x) => a + x.weight_remaining, 0) : mine.filter((x) => x.weight_remaining >= 50).length;
        if (have >= r.min) { delete s.stock_notified[key]; continue; }
        if (s.stock_notified[key]) continue;
        s.stock_notified[key] = true;
        const label = `${r.material} ${r.color || ""}`.trim();
        if (!db.shopping.some((it) => !it.done && it.name.toLowerCase() === `filament ${label}`.toLowerCase())) db.shopping.unshift({ id: ctx.newId(), name: `Filament ${label}`, category: "Filament", priority: have ? "Normale" : "Urgent", done: false });
        ctx.push({ type: "alert", kind: "stock", level: "warning", title: `Stock bas : ${label}`,
          message: r.unit === "grams" ? `Il reste ${have} g de ${label} (seuil : ${r.min} g). Ajouté à la liste d'achats.` : `Il reste ${have} bobine(s) de ${label} (seuil : ${r.min}). Ajouté à la liste d'achats.` });
      }
    }
    const dashboard = ctx.api.dashboard;
    return {
      async dashboard() { const res = await dashboard(); checkAlerts(); return res; },
      async maint_presets() { await ctx.wait(150); return ctx.ok({ families: families(), detected: detect() }); },
      async maint_apply_presets(family) {
        const fam = families().find((f) => f.key === family);
        if (!fam) return { ok: false, msg: "Modèle d'imprimante inconnu." };
        const have = new Set(db.maintenance.map((m) => norm(m.title)));
        const add = fam.tasks.filter((t) => !have.has(norm(t.title)));
        for (const t of add) db.maintenance.push({ id: ctx.newId(), title: t.title, interval_hours: t.hours, last_done_hours: s.stats.total_hours_printed });
        return ctx.ok({ added: add.length, msg: add.length ? `${add.length} rappel(s) ajouté(s) pour ${fam.label}.` : `Les rappels conseillés pour ${fam.label} sont déjà tous dans la liste.` });
      },
    };
  });

  /* ---- Ventilateurs et vitesse d'impression (Sieste, Balade, Envol, Furie) ------ */
  G3D_FAKE.extend((ctx) => {
    const RATES = { 1: 0.5, 2: 1, 3: 1.24, 4: 1.66 };
    Object.assign(ctx.tele, { fan_part: 100, fan_aux: 40, fan_chamber: 20, speed_level: 2 });
    const online = () => ctx.tele.connected;
    return {
      async speed_set(level) {
        await ctx.wait(150);
        if (!online()) return { ok: false, msg: "Imprimante hors ligne." };
        const l = Math.max(1, Math.min(4, Math.round(Number(level)) || 2));
        ctx.tele.speed_level = l;
        ctx.sim.rate = RATES[l];      // la fausse impression avance plus ou moins vite, le temps restant suit
        return ctx.ok({});
      },
      async fan_set(which, pct) {
        await ctx.wait(150);
        if (!online()) return { ok: false, msg: "Imprimante hors ligne." };
        if (!["part", "aux", "chamber"].includes(String(which))) return { ok: false, msg: "Ventilateur inconnu." };
        ctx.tele["fan_" + which] = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
        return ctx.ok({});
      },
    };
  });

  /* ---- « Assez de filament ? » : grammes d'un fichier --------------------------- */
  G3D_FAKE.extend((ctx) => {
    const key = (n) => String(n || "").split("/").pop().replace(/(\.gcode)?\.(3mf|gcode|bgcode)$/i, "").replace(/[_-]+/g, " ").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const known = (n) => { const j = ctx.jobs.find((x) => key(x.name) === key(n)); return j ? j.grams : 0; };
    return {
      async file_need(name) { await ctx.wait(200); return ctx.ok({ grams: known(name) || 105, filaments: [] }); },
      async file_need_known(path) { return ctx.ok({ grams: known(path) }); },
    };
  });

  /* ---- Devis, export comptable et coût réel ------------------------------------- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts;
    const seller = { name: "Élodie Martin", company: "Atelier Graoully 3D", address: "12 rue des Mirabelles\n57000 Metz", email: "contact@graoully.fr", phone: "06 12 34 56 78",
      siret: "12345678900012", vat_mode: "franchise", vat_rate: 20, valid_days: 30, conditions: "Acompte de 30 % à la commande, solde à la livraison.", mentions: "" };
    let sellerLogo = "", quoteSeq = 5, nextLink = null;
    // Même calcul que backend/quotes.py (compute_quote) : pièces, quincaillerie, prestations, options et grille dégressive.
    const roundStep = (raw, step) => (step === 0.01 ? r2(raw) : r2(Math.ceil(raw / step - 1e-9) * step));
    function totalsOf(subtotal, cost, d) {
      const pct = Math.max(0, Math.min(100, Number(d.discount_pct) || 0)), discount = r2(subtotal * pct / 100), ht = r2(subtotal - discount);
      const franchise = d.vat_mode === "franchise", rate = franchise ? 0 : Number(d.vat_rate ?? 20), vat = r2(ht * rate / 100);
      return { subtotal: r2(subtotal), discount_pct: pct, discount, ht, vat_rate: rate, vat, ttc: r2(ht + vat), franchise, cost: r2(cost), margin: r2(ht - r2(cost)) };
    }
    function computeQuote(d) {
      const p = Object.assign({ kwh_price: 0.25, watts: 95, wear_per_hour: 0.35, margin_pct: 10, rounding: "0.01" }, d.params || {});
      const tiers = p.tiers && (p.tiers.rows || []).length >= 2 ? p.tiers : null;
      const step = Number(p.rounding) || 0.01, lineStep = tiers ? 0.01 : step;   // grille : arrondi sur le prix du palier seulement
      const lines = (d.lines || []).map((l) => {
        const x = Object.assign({}, l);
        let unit;
        x._cost = 0;
        if (x.kind === "piece") {
          const matter = (Number(x.grams) || 0) / 1000 * (Number(x.price_kg) || 23), power = (Number(x.hours) || 0) * p.watts / 1000 * p.kwh_price, wear = (Number(x.hours) || 0) * p.wear_per_hour;
          const c = matter + power + wear;
          unit = roundStep(c * (1 + p.margin_pct / 100), lineStep);
          x.cost_unit = r2(c); x.cost_detail = { matter: r2(matter), power: r2(power), wear: r2(wear) }; x._cost = c;
        } else if (x.kind === "hw") {
          const c = Number(x.unit_cost) || 0;
          unit = roundStep(c * (1 + p.margin_pct / 100), lineStep);
          x.cost_unit = c; x._cost = c;
        } else unit = r2(x.unit_price || 0);
        x.unit_price = unit; x.total = r2(unit * (Number(x.qty) || 0));
        return x;
      });
      const sum = (arr, f) => arr.reduce((a, x) => a + f(x), 0);
      const costOf = (x) => x._cost * (Number(x.qty) || 0);
      const base = totalsOf(sum(lines, (x) => x.total), sum(lines, costOf), d);
      const opts = (p.options || []).length >= 2 ? p.options : [];
      const done = (totals) => { lines.forEach((x) => { delete x._cost; }); return { lines, totals }; };
      if (!opts.length && !tiers) return done(base);
      const rows = tiers ? [...tiers.rows].sort((a, b) => a.qty - b.qty) : [];
      const variants = (opts.length ? opts.map((_, k) => k) : [null]).map((g) => {
        const sel = lines.filter((x) => g === null || x.opt === null || x.opt === undefined || x.opt === g);
        const v = { option: g };
        if (g !== null) Object.assign(v, { name: opts[g].name, note: opts[g].note || "" });
        if (!tiers) { v.totals = totalsOf(sum(sel, (x) => x.total), sum(sel, costOf), d); return v; }
        const unitBase = sum(sel.filter((x) => x.kind !== "free"), (x) => x.total), unitCost = sum(sel, costOf);
        const fixed = Number(tiers.setup_fee || 0) + Number(tiers.prep_hours || 0) * Number(tiers.labor_rate || 0) + sum(sel.filter((x) => x.kind === "free"), (x) => x.total);
        let first = null;
        Object.assign(v, { unit_base: r2(unitBase), fixed: r2(fixed) });
        v.tiers = rows.map((r) => {
          const q = Math.max(1, Math.round(Number(r.qty) || 1)), disc = Number(r.discount_pct) || 0;
          const unit = roundStep((fixed / q + unitBase) * (1 - disc / 100), step);
          const t = Object.assign(totalsOf(unit * q, unitCost * q, d), { qty: q, tier_discount_pct: disc, unit_price: unit, saving_pct: first ? Math.round((1 - unit / first) * 1000) / 10 : 0 });
          first = first || (unit > 0 ? unit : null);
          return t;
        });
        return v;
      });
      const ch = p.choice || {};
      const oi = opts.length && Number.isInteger(ch.option) && ch.option < opts.length ? ch.option : null;
      const ti = tiers && Number.isInteger(ch.tier) && ch.tier < rows.length ? ch.tier : null;
      const v = variants[oi || 0], picked = tiers ? v.tiers[ti || 0] : v.totals;
      const totals = {};
      Object.keys(base).forEach((k) => { totals[k] = picked[k]; });
      totals.variants = variants;
      totals.choice = { option: oi, tier: ti, made: (!opts.length || oi !== null) && (!tiers || ti !== null) };
      return done(totals);
    }
    function sample(id, n, status, client, title, lines, extra = {}) {
      const d = Object.assign({ id, number: `D-2026-${String(n).padStart(4, "0")}`, status, issued_on: new Date(Date.now() - (10 - n) * 86400000).toISOString().slice(0, 10), valid_days: 30,
        client: { name: client, address: "", email: "" }, title, lines, discount_pct: 0, vat_mode: "franchise", vat_rate: 20, notes: "",
        params: { margin_pct: 40, rounding: "0.5", kwh_price: 0.25, watts: 95, wear_per_hour: 0.35 }, seller }, extra);
      return Object.assign(d, computeQuote(d));
    }
    const quotes = o.empty ? [] : [
      sample(701, 1, "accepted", "Mairie de Metz", "Signalétique de l'atelier", [{ kind: "piece", label: "Plaque de porte", qty: 6, material: "PLA Matte", grams: 48, hours: 1.6, price_kg: 23 }, { kind: "free", label: "Modélisation", qty: 2, unit: "h", unit_price: 35 }]),
      sample(702, 2, "sent", "Club de robotique de Nancy", "Pièces du robot 2027", [{ kind: "piece", label: "Support de casque", qty: 1, material: "PLA Matte", grams: 62, hours: 3.2, price_kg: 23 }, { kind: "piece", label: "Engrenage planétaire", qty: 4, material: "PETG HF", grams: 35, hours: 1.5, price_kg: 27 }, { kind: "free", label: "Frais de port", qty: 1, unit: "", unit_price: 7.9 }], { discount_pct: 5 }),
      sample(703, 3, "draft", "Sophie L.", "", [{ kind: "piece", label: "Figurine Graoully", qty: 1, material: "PLA Silk", grams: 120, hours: 6, price_kg: 25 }]),
      // 3.7 : devis à trois options (Éco / Standard / Renforcée) et devis à grille dégressive.
      sample(704, 4, "sent", "Lycée Fabert", "Boîtiers de capteurs météo", [
        { kind: "piece", label: "Boîtier de capteur", qty: 4, material: "PLA Basic", grams: 58, hours: 2.1, price_kg: 23, opt: 0 },
        { kind: "piece", label: "Boîtier de capteur", qty: 4, material: "PLA Matte", grams: 66, hours: 3.4, price_kg: 25, opt: 1 },
        { kind: "piece", label: "Boîtier de capteur", qty: 4, material: "PETG HF", grams: 74, hours: 3.2, price_kg: 27, opt: 2 },
        { kind: "hw", label: "Insert fileté laiton M3", qty: 16, unit: "pièce", unit_cost: 0.08, opt: null },
        { kind: "free", label: "Modélisation", qty: 1, unit: "h", unit_price: 35, opt: null }],
      { params: { margin_pct: 40, rounding: "0.5", kwh_price: 0.25, watts: 95, wear_per_hour: 0.35,
        options: [{ name: "Éco", note: "Couches de 0,24 mm" }, { name: "Standard", note: "Couches de 0,16 mm" }, { name: "Renforcée", note: "PETG, parois renforcées" }] } }),
      sample(705, 5, "sent", "Salon Made in Moselle", "Porte-clés Graoully", [
        { kind: "piece", label: "Porte-clés Graoully", qty: 1, material: "PLA Silk", grams: 12, hours: 0.6, price_kg: 25 },
        { kind: "hw", label: "Anneau de porte-clés", qty: 1, unit: "pièce", unit_cost: 0.15 },
        { kind: "free", label: "Personnalisation du logo", qty: 1, unit: "forfait", unit_price: 20 }],
      { params: { margin_pct: 60, rounding: "0.01", kwh_price: 0.25, watts: 95, wear_per_hour: 0.35,
        tiers: { rows: [{ qty: 1, discount_pct: 0 }, { qty: 10, discount_pct: 0 }, { qty: 50, discount_pct: 5 }, { qty: 100, discount_pct: 10 }], setup_fee: 5, prep_hours: 0.5, labor_rate: 25 } } }),
    ];
    // Coût réel : la dernière impression du journal pesée par l'AMS et rattachée au devis D-2026-0002.
    const j1 = db.settings.print_log.find((e) => e.id === "j1");
    if (j1) Object.assign(j1, { grams: 66, cost: 2.72, real: true, grams_source: "ams", est_grams: 62, offpeak_h: 1.5, cost_detail: { matter: 1.52, power: 0.08, wear: 1.12 },
      quote_id: 702, quote_number: "D-2026-0002", client: "Club de robotique de Nancy", quote_line: 0, quote_label: "Support de casque", quote_pieces: 1, quote_cost: 2.62, quote_price: 4, price_kg: 23 });
    const byId = (id) => quotes.find((x) => String(x.id) === String(id));
    function quoteReal(qt) {
      const linked = db.settings.print_log.filter((e) => String(e.quote_id) === String(qt.id));
      const ch = qt.totals.choice || {}, tier = ch.tier !== null && ch.tier !== undefined ? ((qt.totals.variants || [])[ch.option || 0] || {}).tiers[ch.tier] : null;
      const hw = linked.length ? r2(qt.lines.filter((l) => l.kind === "hw" && (l.opt === null || l.opt === undefined || ch.option === null || ch.option === undefined || l.opt === ch.option))
        .reduce((a, l) => a + l.unit_cost * l.qty, 0) * (tier ? tier.qty : 1)) : 0;
      const real = r2(linked.reduce((a, e) => a + (e.cost || 0), 0) + hw);
      return { prints: linked.length, failed: linked.filter((e) => !e.success).length, estimated_cost: qt.totals.cost, real_cost: real, hardware: hw, real_margin: r2(qt.totals.ht - real) };
    }
    function linkFields(qt, line, pieces) {
      const l = qt.lines[line] || qt.lines.find((x) => x.kind === "piece");
      if (!l) return null;
      const n = pieces || l.qty;
      return { quote_id: qt.id, quote_number: qt.number, client: qt.client.name, quote_line: qt.lines.indexOf(l), quote_label: l.label, quote_pieces: n, quote_cost: r2(l.cost_unit * n), quote_price: r2(l.unit_price * n), price_kg: l.price_kg };
    }
    // Chaque impression fictive terminée reçoit le détail du coût réel, et le devis choisi au lancement.
    const live = ctx.api.live;
    async function liveWithCost() {
      const res = await live();
      if ((res.events || []).some((e) => e.type === "print_finished" || e.type === "print_failed")) {
        const e = db.settings.print_log[0], q = db.settings.quote;
        if (e && !e.cost_detail) {
          const matter = r2(e.grams / 1000 * q.spool_price), power = r2(e.hours * q.watts / 1000 * q.kwh_price), wear = r2(e.hours * q.wear_per_hour);
          Object.assign(e, { real: true, grams_source: "ams", est_grams: e.grams, cost_detail: { matter, power, wear } }, nextLink || {});
          nextLink = null;
        }
      }
      return res;
    }
    const pdfName = (qt) => `${qt.number} ${(qt.client || {}).name || ""}.pdf`.replace(" .pdf", ".pdf");
    return {
      live: liveWithCost,
      async quotes_list() {
        await ctx.wait(200);
        if (o.migration) return { ok: false, msg: "Les devis ne sont pas encore activés sur votre compte en ligne : exécutez supabase/migrations/003_graou3d_v36_devis.sql dans Supabase › SQL Editor, puis réessayez." };
        return ctx.ok({ quotes: quotes.map((x) => Object.assign({}, x, { real: quoteReal(x) })), seller, logo: sellerLogo });
      },
      async quote_seller_save(profile, logo) { Object.assign(seller, profile || {}); if (logo !== null && logo !== undefined) sellerLogo = logo; return ctx.ok({ seller, logo: sellerLogo, msg: "Coordonnées enregistrées." }); },
      async quote_preview(d) { return ctx.ok(computeQuote(d || {})); },
      async quote_save(d) {
        const c = computeQuote(d);
        let qt = d.id ? byId(d.id) : null;
        if (qt) Object.assign(qt, d, c, { number: qt.number });
        else { quoteSeq += 1; qt = Object.assign({}, d, c, { id: 700 + quoteSeq, number: `D-2026-${String(quoteSeq).padStart(4, "0")}`, seller: Object.assign({}, seller) }); quotes.unshift(qt); }
        return ctx.ok({ quote: qt, msg: `Devis ${qt.number} enregistré.` });
      },
      async quote_status(id, status, choice) {
        const qt = byId(id);
        if (!qt) return { ok: false, msg: "Devis introuvable." };
        if ((qt.totals.variants || []).length) {
          const params = Object.assign({}, qt.params, { choice: status === "accepted" && choice ? choice : null });
          const c = computeQuote(Object.assign({}, qt, { params }));
          if (status === "accepted" && !c.totals.choice.made) return { ok: false, msg: "Indiquez l'option et la quantité choisies par le client." };
          Object.assign(qt, c, { params });
        }
        qt.status = status;
        return ctx.ok({ quote: qt });
      },
      async quote_delete(id) { ctx.remove(quotes, id); return ctx.ok({ msg: "Devis supprimé." }); },
      async quote_pdf(id, open) {
        await ctx.wait(300);
        const qt = byId(id);
        if (!qt) return { ok: false, msg: "Devis introuvable." };
        const name = pdfName(qt);
        if (o.demo) return open ? ctx.ok({ name, msg: "Dans la démo, le PDF n'est pas créé : rien n'est écrit sur votre PC." }) : ctx.ok({ name, path: NO_FILE });
        return ctx.ok({ name, path: "C:\\Users\\demo\\Downloads\\Graou3D\\devis\\" + name, msg: open ? "" : "PDF enregistré." });
      },
      async accounting_export() {
        await ctx.wait(300);
        const d = new Date().toISOString().slice(0, 10) + "_1430", dir = "C:\\Users\\demo\\Downloads\\Graou3D\\exports";
        const names = [`impressions_${d}.csv`, `devis_${d}.csv`, `recapitulatif_${d}.csv`];
        if (o.demo) return ctx.ok({ folder: NO_FILE, paths: names });
        return ctx.ok({ folder: dir, paths: names.map((n) => `${dir}\\${n}`), msg: "3 fichiers CSV enregistrés." });
      },
      async print_quote_link(id, line, pieces) {
        const qt = id ? byId(id) : null;
        nextLink = qt ? linkFields(qt, line, pieces) : null;
        return ctx.ok({ msg: qt ? `La prochaine impression sera rattachée au devis ${qt.number}.` : "" });
      },
      async journal_link_quote(entryId, id, line, pieces) {
        const e = db.settings.print_log.find((x) => x.id === entryId);
        if (!e) return { ok: false, msg: "Impression introuvable." };
        const keys = ["quote_id", "quote_number", "client", "quote_line", "quote_label", "quote_pieces", "quote_cost", "quote_price", "price_kg"];
        if (!id) { keys.forEach((k) => delete e[k]); return ctx.ok({ settings: db.settings, msg: "Impression détachée du devis." }); }
        const qt = byId(id), f = qt && linkFields(qt, line, pieces);
        if (!f) return { ok: false, msg: "Ce devis n'a pas de pièce à imprimer." };
        Object.assign(e, f);
        return ctx.ok({ settings: db.settings, msg: `Impression rattachée au devis ${qt.number}.` });
      },
      async journal_set_grams(entryId, g) {
        const e = db.settings.print_log.find((x) => x.id === entryId);
        if (!e) return { ok: false, msg: "Impression introuvable." };
        const d = e.cost_detail || { power: 0.1, wear: r2((e.hours || 0) * 0.35) };
        const matter = r2(g / 1000 * (e.price_kg || 23));
        Object.assign(e, { grams: g, grams_source: "weighed", cost_detail: Object.assign({}, d, { matter }), cost: r2(matter + d.power + d.wear) });
        return ctx.ok({ settings: db.settings, msg: "Poids corrigé." });
      },
    };
  });

  /* ---- Mode entreprise : atelier partagé (3 membres) et commandes clients (4) ---- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts, s = db.settings;
    Object.assign(s.features, { enterprise: false, workshop: false, orders: false }, o.features || {});
    const on = (k) => !!(s.features.enterprise && s.features[k]);
    const me = db.profile;
    let ws = o.empty ? null : { id: 9, name: "Atelier Graoully", owner_id: me.id };
    let members = [
      { user_id: me.id, pseudo: me.pseudo, avatar_data: "", role: "owner", status: "active", me: true },
      { user_id: "u-2", pseudo: "Sayou", avatar_data: "", role: "manage", status: "active", me: false },
      { user_id: "u-3", pseudo: "Venoxy", avatar_data: "", role: "read", status: "active", me: false },
    ];
    const invites = [];
    const role = () => (members.find((m) => m.me) || {}).role || null;
    const manage = () => ["owner", "manage"].includes(role());
    // Lignes partagées : deux de mes bobines, deux bobines et un achat de Sayou, un rappel commun.
    const mark = (list, ids) => list.filter((r) => ids.includes(r.id)).forEach((r) => Object.assign(r, { shared: true }));
    if (!o.empty) {
      mark(db.spools, [1, 3]);
      db.spools.push(
        { id: 61, brand: "Prusament", material: "PETG Basic", color_name: "Orange Prusa", color_hex: "#FA6831", weight_initial: 1000, weight_remaining: 780, ams_slot: 0, k_factor: 0.02, flow_ratio: 0.98, max_volumetric_speed: 16, z_offset: 0, days_open: 5, shared: true, mine: false, owner_pseudo: "Sayou" },
        { id: 62, brand: "Polymaker", material: "ASA", color_name: "Gris galaxie", color_hex: "#4A4F55", weight_initial: 1000, weight_remaining: 320, ams_slot: 0, k_factor: 0.02, flow_ratio: 0.98, max_volumetric_speed: 18, z_offset: 0, days_open: 26, shared: true, mine: false, owner_pseudo: "Sayou" });
      db.shopping.push({ id: 63, name: "Plateau PEI texturé", category: "Pièce", priority: "Normale", done: false, shared: true, mine: false, owner_pseudo: "Sayou" });
      mark(db.shopping, [31]);
      mark(db.maintenance, [21]);
      mark(db.tasks, [13]);
    }
    const printers = () => (ws ? [
      ...(db.printer.workshop ? [{ id: 1, name: db.printer.name, protocol: db.printer.protocol, host: db.printer.host, mine: true, shared: true }] : []),
      Object.assign({ id: 2, name: "Voron 2.4 de Sayou", protocol: "KLIPPER", host: "192.168.1.60", port: 7125, serial: "", camera_url: "", mine: false, shared: true, owner_pseudo: "Sayou" },
        on("multi_printer") ? { in_fleet: db.wsAdopted.has("192.168.1.60") } : {}),
    ] : []);
    db.wsAdopted = new Set();   // adresses des imprimantes de l'atelier ajoutées à « mes imprimantes » (bloc plus bas)
    db.printer.workshop = !o.empty;
    const info = () => ({ workshop: ws, role: ws ? role() : null, members: ws ? members : [], invites });
    // Sans atelier (fonction coupée ou atelier quitté) : seulement mes lignes, sans pastille.
    const scoped = (list) => (on("workshop") && ws ? list.map((r) => Object.assign({ mine: true, shared: false }, r, { can_edit: r.mine === false || r.shared ? manage() : true }))
      : list.filter((r) => r.mine !== false).map((r) => Object.assign({}, r, { shared: false, mine: true, can_edit: true })));
    const dashboard = ctx.api.dashboard;
    const out = (msg) => ctx.ok({ workshop: info(), printers: printers(), my_printer: { name: db.printer.name, protocol: db.printer.protocol, shared: !!db.printer.workshop }, msg });
    const need = (k) => (on(k) ? null : { ok: false, msg: `Activez d'abord « ${k === "orders" ? "Commandes clients" : "Atelier partagé"} » dans Réglages › Fonctions (mode entreprise).` });
    const sharedFlag = (list, wanted) => { const r = list[0]; if (r && wanted && ws && manage()) r.shared = true; };

    /* Commandes */
    const day = (n) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);
    const orders = o.empty ? [] : [
      { id: 801, user_id: me.id, quote_id: 701, quote_number: "D-2026-0001", title: "Signalétique de l'atelier", client: { name: "Mairie de Metz", email: "" }, status: "printing", due_on: day(3), price_ht: 85, price_ttc: 85, estimated_cost: 10.21, real_cost: 0, prints: [], notes: "", workshop_id: 9, created_at: ctx.ago(4 * 1440) },
      { id: 802, user_id: me.id, quote_id: null, quote_number: "", title: "Porte-clés logo ×50", client: { name: "Boulangerie Lorraine", email: "" }, status: "todo", due_on: day(-2), price_ht: 95, price_ttc: 95, estimated_cost: 31, real_cost: 0, prints: [], notes: "Couleurs : jaune et bleu.", workshop_id: 9, created_at: ctx.ago(9 * 1440) },
      { id: 803, user_id: me.id, quote_id: null, quote_number: "", title: "Supports de casque ×4", client: { name: "Club de robotique de Nancy", email: "" }, status: "delivered", due_on: day(-6), delivered_at: new Date(Date.now() - 5 * 86400000).toISOString(), price_ht: 64, price_ttc: 64, estimated_cost: 24, real_cost: 2.72,
        prints: [{ id: "j1", file: "Support casque", ts: Math.floor(Date.now() / 1000) - 3600, grams: 66, cost: 2.72, success: true }], notes: "", workshop_id: null, created_at: ctx.ago(12 * 1440) },
      { id: 804, user_id: "u-2", quote_id: null, quote_number: "", title: "Maquette de la gare de Metz", client: { name: "Office de tourisme", email: "" }, status: "paid", due_on: day(-3), delivered_at: new Date(Date.now() - 4 * 86400000).toISOString(), paid_at: new Date(Date.now() - 86400000).toISOString(), price_ht: 180, price_ttc: 180, estimated_cost: 52, real_cost: 47.8,
        prints: [{ id: "s1", file: "Gare — façade", ts: Math.floor(Date.now() / 1000) - 6 * 86400, grams: 410, cost: 47.8, success: true }], notes: "", workshop_id: 9, created_at: ctx.ago(20 * 1440) },
    ];
    const r2 = (v) => Math.round((Number(v) + Number.EPSILON) * 100) / 100;
    const OPEN = ["todo", "printing", "done"];
    function decorate(x) {
      const prints = x.prints || [], real = r2(prints.reduce((a, p) => a + (p.cost || 0), 0));
      return Object.assign({}, x, { late: !!(x.due_on && x.due_on < day(0) && OPEN.includes(x.status)), mine: x.user_id === me.id, shared: x.workshop_id != null,
        owner_pseudo: x.user_id === me.id ? "" : (members.find((m) => m.user_id === x.user_id) || {}).pseudo || "",
        summary: { prints: prints.length, failed: prints.filter((p) => p.success === false).length, real_cost: real, estimated_cost: x.estimated_cost,
          margin: r2(x.price_ht - (prints.length ? real : x.estimated_cost)), margin_real: prints.length > 0 } });
    }
    function payload(msg) {
      const visible = orders.filter((x) => x.user_id === me.id || (on("workshop") && ws && x.workshop_id === ws.id)).map(decorate);
      const month = day(0).slice(0, 7);
      const doneMonth = visible.filter((x) => ["delivered", "paid"].includes(x.status) && String(x.delivered_at || x.paid_at || "").slice(0, 7) === month);
      const open = visible.filter((x) => OPEN.includes(x.status)), unpaid = visible.filter((x) => x.status === "delivered");
      const sum = (l, f) => r2(l.reduce((a, x) => a + f(x), 0));
      return ctx.ok({ orders: visible.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))), can_share: !!(on("workshop") && ws && manage()),
        summary: { open: open.length, late: visible.filter((x) => x.late).length, month_count: doneMonth.length, month_revenue: sum(doneMonth, (x) => x.price_ht),
          month_margin: sum(doneMonth, (x) => x.summary.margin), open_value: sum(open, (x) => x.price_ht), unpaid: unpaid.length, unpaid_value: sum(unpaid, (x) => x.price_ht) },
        journal: s.print_log.map((e) => ({ id: e.id, file: e.file, ts: e.ts, cost: e.cost })), carriers: CARRIERS, msg });
    }
    // 5.5 : expédition (mêmes transporteurs que backend/catalog.py)
    const CARRIERS = [["colissimo", "Colissimo"], ["lettre_suivie", "Lettre suivie (La Poste)"], ["mondial_relay", "Mondial Relay"], ["chronopost", "Chronopost"],
      ["relais_colis", "Relais Colis"], ["dpd", "DPD"], ["ups", "UPS"], ["dhl", "DHL"], ["gls", "GLS"], ["hand", "Remise en main propre"], ["other", "Autre transporteur"]];
    const shipMessage = (x) => {
      const sh = x.shipping || {}, c = (CARRIERS.find((k) => k[0] === sh.carrier) || CARRIERS[10])[1];
      return [`Bonjour ${(x.client || {}).name || ""},`, "", `Bonne nouvelle : votre commande « ${x.title} » est partie.`,
        `Transporteur : ${c}${sh.tracking ? `, numéro de suivi : ${sh.tracking}` : ""}.`, "", "Merci pour votre confiance et à bientôt !"].join("\n");
    };
    const mineOrder = (id) => orders.find((x) => String(x.id) === String(id) && x.user_id === me.id);
    const wrap = (name, after) => { const base = ctx.api[name]; return async (...a) => { const res = await base(...a); if (res && res.ok) after(...a); return res; }; };

    return {
      async dashboard() {
        const res = await dashboard();
        return Object.assign(res, { workshop: on("workshop") ? info() : null, spools: scoped(res.spools), shopping: scoped(res.shopping),
          tasks: scoped(res.tasks), maintenance: scoped(res.maintenance) });
      },
      spool_add: wrap("spool_add", (d) => { const r = db.spools[db.spools.length - 1]; if (d && d.scope === "workshop" && r) r.shared = true; }),
      spool_update: wrap("spool_update", (id, d) => { const r = ctx.find(db.spools, id); if (r && d && d.scope) { r.shared = d.scope === "workshop"; delete r.scope; } }),
      shop_add: wrap("shop_add", (n, c, p, shared) => sharedFlag(db.shopping, shared === true)),
      task_add: wrap("task_add", (d) => sharedFlag(db.tasks, !!(d && d.shared))),
      maint_add: wrap("maint_add", (t, i, shared) => { const r = db.maintenance[db.maintenance.length - 1]; if (r && shared === true && ws && manage()) r.shared = true; }),
      async workshop_info() { await ctx.wait(150); return need("workshop") || out(); },
      async workshop_create(name) { if (ws) return { ok: false, msg: "Vous faites déjà partie d'un atelier : quittez-le avant d'en créer un." }; ws = { id: 10, name: String(name).trim(), owner_id: me.id }; members = [members[0]]; members[0].role = "owner"; return out("Atelier créé : invitez vos amis."); },
      async workshop_rename(name) { if (role() !== "owner") return { ok: false, msg: "Seul le propriétaire de l'atelier peut le renommer." }; ws.name = String(name).trim() || ws.name; return out("Atelier renommé."); },
      async workshop_invite(target, r) {
        const t = String(target).trim().toLowerCase();
        const f = db.friends.find((x) => x.pseudo.toLowerCase() === t || x.friend_code.toLowerCase() === t);
        if (!f) return { ok: false, msg: "Aucun ami avec ce code ou ce pseudo. Ajoutez-le d'abord dans Amis." };
        if (members.some((m) => m.user_id === f.id)) return { ok: false, msg: `${f.pseudo} est déjà membre ou déjà invité.` };
        members.push({ user_id: f.id, pseudo: f.pseudo, avatar_data: "", role: r === "manage" ? "manage" : "read", status: "pending", me: false });
        return out(`Invitation envoyée à ${f.pseudo}. Elle apparaîtra dans son onglet Atelier.`);
      },
      async workshop_respond(id, accept) { const i = invites.findIndex((x) => x.workshop_id === Number(id)); if (i >= 0) invites.splice(i, 1); return out(accept ? "Bienvenue dans l'atelier !" : "Invitation refusée."); },
      async workshop_set_role(uid, r) { const m = members.find((x) => x.user_id === uid && x.role !== "owner"); if (!m) return { ok: false, msg: "Membre introuvable." }; m.role = r === "manage" ? "manage" : "read"; return out("Rôle modifié."); },
      async workshop_remove(uid) { members = members.filter((x) => x.user_id !== uid || x.role === "owner"); return out("Membre retiré : il n'a plus accès à l'atelier."); },
      async workshop_leave() { if (role() === "owner") return { ok: false, msg: "Le propriétaire ne quitte pas son atelier : supprimez-le si vous n'en avez plus besoin." }; ws = null; return out("Vous avez quitté l'atelier."); },
      async workshop_delete(confirmed) { if (confirmed !== true) return { ok: false, msg: "Confirmez la suppression de l'atelier." }; ws = null; return out("Atelier supprimé : chacun garde ses propres données."); },
      async workshop_share_printer(shared) { db.printer.workshop = shared === true; return out(shared === true ? "Imprimante partagée avec l'atelier (sans son code d'accès)." : "Imprimante reprise : elle n'est plus partagée."); },
      async orders_list() { await ctx.wait(150); return need("orders") || payload(); },
      quotes_list: (() => { const base = ctx.api.quotes_list; return async () => {
        const res = await base();
        if (res.ok && on("orders")) res.quotes.forEach((q) => { const x = orders.find((y) => y.user_id === me.id && String(y.quote_id) === String(q.id)); q.order_id = x ? x.id : null; });
        return res;
      }; })(),
      async order_from_quote(id, due, shared) {
        const res = await ctx.api.quotes_list();
        const q = res.ok && res.quotes.find((x) => String(x.id) === String(id));
        if (!q) return { ok: false, msg: "Devis introuvable." };
        if (q.status !== "accepted") return { ok: false, msg: "Seul un devis accepté devient une commande : changez d'abord son statut." };
        if (orders.some((x) => String(x.quote_id) === String(id) && x.user_id === me.id)) return { ok: false, msg: `Une commande existe déjà pour le devis ${q.number}.` };
        const ch = (q.totals || {}).choice || {}, p = q.params || {}, bits = [];   // même titre que backend/orders.from_quote
        if (ch.made === false) return { ok: false, msg: "Indiquez d'abord l'option et la quantité choisies par le client (statut « Accepté » du devis)." };
        if (ch.option != null && (p.options || [])[ch.option]) bits.push(p.options[ch.option].name);
        if (ch.tier != null && p.tiers && p.tiers.rows[ch.tier]) bits.push(p.tiers.rows[ch.tier].qty > 1 ? `${p.tiers.rows[ch.tier].qty} exemplaires` : "1 exemplaire");
        const title = (q.title || `Devis ${q.number}`) + (bits.length ? ` (${bits.join(" · ")})` : "");
        orders.unshift({ id: ctx.newId(), user_id: me.id, quote_id: q.id, quote_number: q.number, title: title.slice(0, 120), client: { name: q.client.name, email: q.client.email || "" }, status: "todo", due_on: due || null,
          price_ht: q.totals.ht, price_ttc: q.totals.ttc, estimated_cost: q.totals.cost, real_cost: 0, prints: [], notes: "", workshop_id: shared === true && ws ? ws.id : null, created_at: ctx.now() });
        return payload(`Commande créée depuis le devis ${q.number}.`);
      },
      async order_save(d) {
        d = d || {};
        const x = d.id ? mineOrder(d.id) : { id: ctx.newId(), user_id: me.id, quote_id: null, quote_number: "", status: "todo", estimated_cost: 0, real_cost: 0, prints: [], workshop_id: null, created_at: ctx.now() };
        if (!x) return { ok: false, msg: "Commande introuvable (seul son créateur peut la modifier)." };
        Object.assign(x, { title: d.title, client: { name: d.client_name, email: d.client_email || "" }, due_on: d.due_on || null, notes: d.notes || "" });
        if ("price_ht" in d) Object.assign(x, { price_ht: Number(d.price_ht) || 0, price_ttc: Number(d.price_ht) || 0 });
        if ("shared" in d) x.workshop_id = d.shared && ws ? ws.id : null;
        if (!d.id) orders.unshift(x);
        return payload(d.id ? "Commande enregistrée." : "Commande créée.");
      },
      async order_status(id, st) {
        const x = mineOrder(id);
        if (!x) return { ok: false, msg: "Commande introuvable (seul son créateur peut la modifier)." };
        x.status = st;
        if (["delivered", "paid"].includes(st) && !x.delivered_at) x.delivered_at = ctx.now();
        if (st === "paid" && !x.paid_at) x.paid_at = ctx.now();
        if (!["delivered", "paid"].includes(st)) x.delivered_at = null;
        if (st !== "paid") x.paid_at = null;
        return payload();
      },
      async order_link_print(id, entryId, linked) {
        const x = mineOrder(id), e = s.print_log.find((j) => j.id === entryId);
        if (!x) return { ok: false, msg: "Commande introuvable (seul son créateur peut la modifier)." };
        x.prints = (x.prints || []).filter((p) => p.id !== entryId);
        if (linked && e) x.prints.unshift({ id: e.id, file: e.file, ts: e.ts, grams: e.grams, cost: e.cost, success: e.success !== false });
        return payload(linked ? "Impression liée à la commande." : "Impression retirée de la commande.");
      },
      async order_shipping(id, d, deliver) {
        const x = mineOrder(id);
        if (!x) return { ok: false, msg: "Seul l'auteur de la commande prépare son expédition." };
        d = d || {};
        x.shipping = { carrier: CARRIERS.some((c) => c[0] === d.carrier) ? d.carrier : "other", tracking: String(d.tracking || "").replace(/[^\w-]/g, "").slice(0, 40),
          shipped_on: d.shipped_on || day(0), weight_g: Math.max(0, Math.round(Number(d.weight_g) || 0)), packages: Math.max(1, Math.round(Number(d.packages) || 1)),
          address: String(d.address || "").slice(0, 300), note: String(d.note || "").slice(0, 300) };
        if (deliver === true) { x.status = "delivered"; x.delivered_at = new Date().toISOString(); }
        return Object.assign(payload("Expédition enregistrée." + (deliver === true ? " Commande marquée comme livrée." : "")), { message: shipMessage(x) });
      },
      async order_shipping_message(id) { const x = mineOrder(id); return x ? ctx.ok({ message: shipMessage(x), tracking_url: "" }) : { ok: false, msg: "Commande introuvable." }; },
      async order_delivery_note() { return { ok: true, msg: "Bon de livraison prêt. " + NO_FILE }; },
      async order_delete(id) { const i = orders.findIndex((x) => String(x.id) === String(id) && x.user_id === me.id); if (i >= 0) orders.splice(i, 1); return payload("Commande supprimée."); },
    };
  });

  /* ---- Factures et récapitulatif URSSAF (3.9) : même logique que backend/invoices.py et backend/urssaf.py ---- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts, s = db.settings;
    Object.assign(s.features, { invoices: false, urssaf: false }, o.features || {});
    s.urssaf = s.urssaf || {};
    const on = (k) => !!(s.features.enterprise && s.features[k]);
    const r2 = (v) => Math.round((Number(v) + Number.EPSILON) * 100) / 100;
    const day = (n) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);
    const need = (k) => (on(k) ? null : { ok: false, msg: `Activez d'abord « ${k === "urssaf" ? "Déclaration URSSAF" : "Factures"} » dans Réglages › Fonctions (mode entreprise).` });
    const seq = { F: 0, A: 0 };
    const year = new Date().getFullYear();
    function compute(lines, discountPct, vatMode, vatRate) {
      const out = (lines || []).map((l) => { const qty = Number(l.qty) || 0, up = r2(Number(l.unit_price) || 0);
        return { label: String(l.label || "").slice(0, 120) || "Article", qty, unit: l.unit || "", unit_price: up, cat: l.cat === "services" ? "services" : "goods", total: r2(qty * up) }; });
      const subtotal = r2(out.reduce((a, l) => a + l.total, 0)), pct = Math.min(100, Math.max(0, Number(discountPct) || 0));
      const discount = r2(subtotal * pct / 100), ht = r2(subtotal - discount), fr = vatMode === "franchise", rate = fr ? 0 : Number(vatRate) || 0;
      const vat = r2(ht * rate / 100), services = out.filter((l) => l.cat === "services").reduce((a, l) => a + l.total, 0);
      const servicesHt = subtotal ? r2(services * ht / subtotal) : 0;
      return { lines: out, totals: { subtotal, discount_pct: pct, discount, ht, vat_rate: rate, vat, ttc: r2(ht + vat), franchise: fr, services_ht: servicesHt, goods_ht: r2(ht - servicesHt) } };
    }
    const invoices = [];
    function issue(values, kind) {
      const L = kind === "credit" ? "A" : "F";
      seq[L] += 1;
      const row = Object.assign({ id: ctx.newId(), kind, number: `${L}-${year}-${String(seq[L]).padStart(4, "0")}`, issued_on: day(0), paid_on: null, payment_method: "", created_at: ctx.now() }, values);
      invoices.unshift(row);
      return row;
    }
    if (!o.empty) {
      const b = compute([{ label: "Figurine Graoully", qty: 1, unit_price: 28, cat: "goods" }, { label: "Modélisation", qty: 1, unit: "h", unit_price: 35, cat: "services" }], 0, "franchise", 20);
      Object.assign(issue({ client: { name: "Sophie L.", address: "8 place Saint-Louis\n57000 Metz" }, title: "Figurine sur mesure", lines: b.lines, totals: b.totals,
        vat_mode: "franchise", vat_rate: 0, discount_pct: 0, sale_date: day(-40), due_on: day(-40) }, "invoice"), { issued_on: day(-40), paid_on: day(-38), payment_method: "card" });
      const a = compute([{ label: "Supports de casque", qty: 4, unit: "pièce", unit_price: 16, cat: "goods" }], 0, "franchise", 20);
      issue({ client: { name: "Club de robotique de Nancy", address: "3 rue Jeanne d'Arc\n54000 Nancy", pro: true, siren: "812345678" }, title: "Supports de casque ×4", lines: a.lines, totals: a.totals,
        vat_mode: "franchise", vat_rate: 0, discount_pct: 0, sale_date: day(-6), due_on: day(-1), order_id: 803 }, "invoice");
    }
    function decorate() {
      return invoices.map((x) => {
        if (x.kind !== "invoice") return Object.assign({}, x);
        const credited = r2(invoices.filter((c) => c.kind === "credit" && c.credit_of === x.id).reduce((a, c) => a + c.totals.ttc, 0));
        const cancelled = credited >= x.totals.ttc;
        return Object.assign({}, x, { credited, cancelled, overdue: !!(x.due_on < day(0) && !x.paid_on && !cancelled) });
      });
    }
    async function seller() { const r = await ctx.api.quotes_list(); return (r && r.seller) || {}; }
    const missing = (sl) => [!(sl.name || sl.company) && "votre nom ou celui de l'entreprise", !sl.address && "votre adresse", !sl.siret && "votre SIRET"].filter(Boolean);
    async function payload(extra) {
      const rows = decorate(), y = String(year), sl = await seller();
      const inv = rows.filter((r) => r.kind === "invoice"), unpaid = inv.filter((r) => !r.paid_on && !r.cancelled);
      const yearHt = r2(rows.filter((r) => r.issued_on.slice(0, 4) === y).reduce((a, r) => a + (r.kind === "credit" ? -1 : 1) * r.totals.ht, 0));
      return ctx.ok(Object.assign({ invoices: rows, seller: sl, missing: missing(sl),
        summary: { count: inv.length, year_ht: yearHt, unpaid: unpaid.length, overdue: unpaid.filter((r) => r.overdue).length, unpaid_ttc: r2(unpaid.reduce((a, r) => a + r.totals.ttc - r.credited, 0)) } }, extra || {}));
    }
    const find = (id) => invoices.find((x) => String(x.id) === String(id));
    const billed = (key, id) => invoices.find((x) => x.kind === "invoice" && String(x[key]) === String(id));

    /* URSSAF : encaissements des factures payées et des commandes payées sans facture */
    const QUARTERS = ["1er", "2e", "3e", "4e"], MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
    const conf = () => Object.assign({ period: "quarter", rate_goods: 12.3, rate_services: 21.2, vl: false }, s.urssaf || {});
    const periodOf = (d, p) => { const y = d.slice(0, 4), m = Number(d.slice(5, 7)); return p === "month" ? [`${y}-${d.slice(5, 7)}`, `${MONTHS[m - 1]} ${y}`] : [`${y}-T${Math.ceil(m / 3)}`, `${QUARTERS[Math.ceil(m / 3) - 1]} trimestre ${y}`]; };
    const deadline = (key, p) => { let y = Number(key.slice(0, 4)), m = (p === "month" ? Number(key.slice(5, 7)) : Number(key.slice(-1)) * 3) + 1; if (m > 12) { y += 1; m = 1; } return new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10); };
    async function receipts() {
      const out = [];
      if (on("invoices")) {
        for (const x of invoices) {
          if (!x.paid_on) continue;
          const t = x.totals, sign = x.kind === "credit" ? -1 : 1;
          out.push({ date: x.paid_on, goods: sign * t.goods_ht, services: sign * t.services_ht, label: `${x.kind === "credit" ? "Avoir" : "Facture"} ${x.number}`, source: x.kind });
        }
      }
      if (on("orders")) {
        const r = await ctx.api.orders_list();
        for (const x of (r.ok && r.orders) || []) {
          if (!x.mine || x.status !== "paid" || !x.paid_at || billed("order_id", x.id) || (x.quote_id && billed("quote_id", x.quote_id))) continue;
          const sv = x.activity === "services";
          out.push({ date: String(x.paid_at).slice(0, 10), goods: sv ? 0 : x.price_ht, services: sv ? x.price_ht : 0, label: x.title, source: "order" });
        }
      }
      return out.sort((a, b) => a.date.localeCompare(b.date));
    }

    const quotesList = ctx.api.quotes_list, ordersList = ctx.api.orders_list;
    return {
      async quotes_list() {
        const res = await quotesList();
        if (res.ok && on("invoices")) res.quotes.forEach((q) => { const x = billed("quote_id", q.id); q.invoice_number = x ? x.number : ""; });
        return res;
      },
      async orders_list() {
        const res = await ordersList();
        if (res.ok && on("invoices")) res.orders.forEach((q) => { const x = billed("order_id", q.id) || (q.quote_id && billed("quote_id", q.quote_id)); q.invoice_number = x ? x.number : ""; });
        return res;
      },
      async invoices_list() { await ctx.wait(150); return need("invoices") || payload(); },
      async invoice_preview(d) { d = d || {}; const c = compute(d.lines, d.discount_pct, d.vat_mode, d.vat_rate); return ctx.ok(Object.assign(c, { vat_mode: d.vat_mode === "franchise" ? "franchise" : "rate" })); },
      async invoice_draft(source, id) {
        if (need("invoices")) return need("invoices");
        if (source === "quote") {
          const q = ((await quotesList()).quotes || []).find((x) => String(x.id) === String(id));
          if (!q || q.status !== "accepted") return { ok: false, msg: "Seul un devis accepté se facture : changez d'abord son statut." };
          if (billed("quote_id", q.id)) return { ok: false, msg: `Ce devis a déjà la facture ${billed("quote_id", q.id).number}.` };
          const lines = q.lines.filter((l) => !(q.params.options || []).length || l.opt === null || l.opt === undefined || l.opt === (q.totals.choice || {}).option)
            .map((l) => ({ label: l.label, qty: l.qty, unit: l.unit || "", unit_price: l.unit_price, cat: l.kind === "free" ? "services" : "goods" }));
          return ctx.ok({ draft: { quote_id: q.id, title: q.title || `Devis ${q.number}`, client: Object.assign({}, q.client), lines, discount_pct: q.discount_pct, vat_mode: q.vat_mode, vat_rate: q.vat_rate } });
        }
        const x = ((await ordersList()).orders || []).find((y) => String(y.id) === String(id) && y.mine);
        if (!x) return { ok: false, msg: "Commande introuvable (seul son créateur peut la modifier)." };
        const done = billed("order_id", x.id) || (x.quote_id && billed("quote_id", x.quote_id));
        if (done) return { ok: false, msg: `Cette commande a déjà la facture ${done.number}.` };
        return ctx.ok({ draft: { order_id: x.id, quote_id: x.quote_id, title: x.title, client: Object.assign({}, x.client),
          lines: [{ label: x.title, qty: 1, unit: "", unit_price: x.price_ht, cat: x.activity === "services" ? "services" : "goods" }] } });
      },
      async invoice_issue(d) {
        d = d || {};
        const sl = await seller(), miss = missing(sl), c = d.client || {};
        if (miss.length) return { ok: false, msg: `Avant la première facture, complétez vos coordonnées : ${miss.join(", ")}.` };
        if (!String(c.name || "").trim()) return { ok: false, msg: "Indiquez le nom du client." };
        if (!String(c.address || "").trim()) return { ok: false, msg: "Indiquez l'adresse du client : elle est obligatoire sur une facture." };
        if (c.pro && !/^\d{9}(\d{5})?$/.test(String(c.siren || "").replace(/[\s.]/g, ""))) return { ok: false, msg: "Client professionnel : indiquez son SIREN (obligatoire sur les factures)." };
        const k = compute(d.lines, d.discount_pct, d.vat_mode, d.vat_rate);
        if (k.totals.ttc <= 0) return { ok: false, msg: "Le montant de la facture doit être supérieur à zéro." };
        const row = issue({ client: Object.assign({}, c), title: d.title || "", lines: k.lines, totals: k.totals, vat_mode: d.vat_mode, vat_rate: k.totals.vat_rate, discount_pct: k.totals.discount_pct,
          sale_date: d.sale_date || day(0), due_on: d.due_on || day(0), notes: d.notes || "", quote_id: d.quote_id || null, order_id: d.order_id || null, seller: Object.assign({}, sl) }, "invoice");
        return payload({ invoice: row, msg: `Facture ${row.number} émise.` });
      },
      async invoice_credit(id, d) {
        const x = find(id);
        if (!x || x.kind !== "invoice") return { ok: false, msg: "Un avoir se fait sur une facture, pas sur un autre avoir." };
        d = d || {};
        const k = d.full ? compute(x.lines, x.discount_pct, x.vat_mode, x.vat_rate) : compute(d.lines, 0, x.vat_mode, x.vat_rate);
        const left = r2(x.totals.ttc - invoices.filter((c) => c.kind === "credit" && c.credit_of === x.id).reduce((a, c) => a + c.totals.ttc, 0));
        if (k.totals.ttc <= 0) return { ok: false, msg: "Le montant de l'avoir doit être supérieur à zéro." };
        if (k.totals.ttc > left) return { ok: false, msg: `L'avoir dépasse ce qui reste sur la facture (${BC.n ? BC.n(left, 2) : left} €).` };
        const row = issue({ credit_of: x.id, credit_of_number: x.number, client: x.client, title: d.reason || (d.full ? "Annulation de la facture" : "Correction de la facture"),
          lines: k.lines, totals: k.totals, vat_mode: x.vat_mode, vat_rate: x.vat_rate, sale_date: x.sale_date, due_on: day(0), order_id: x.order_id, quote_id: x.quote_id }, "credit");
        return payload({ invoice: row, msg: `Avoir ${row.number} émis sur la facture ${x.number}.` });
      },
      async invoice_payment(id, d) {
        const x = find(id);
        if (!x) return { ok: false, msg: "Facture introuvable." };
        const paid = (d && d.paid_on) || "";
        if (paid && paid > day(0)) return { ok: false, msg: "La date de paiement ne peut pas être dans le futur." };
        Object.assign(x, { paid_on: paid || null, payment_method: paid ? d.method || "" : "" });
        return payload({ msg: paid ? (x.kind === "credit" ? "Avoir remboursé." : "Paiement enregistré.") : "Paiement retiré." });
      },
      async invoice_pdf(id) {
        await ctx.wait(250);
        const x = find(id);
        if (!x) return { ok: false, msg: "Facture introuvable." };
        const name = `${x.number} ${(x.client || {}).name || ""}.pdf`, path = `C:\\Users\\Vous\\Documents\\Graou3D\\factures\\${name}`;
        return ctx.ok({ path, name, msg: "PDF enregistré : " + path });
      },
      async urssaf_summary() {
        if (need("urssaf")) return need("urssaf");
        const c = conf(), rows = await receipts(), today = day(0);
        const rate = { goods: c.rate_goods + (c.vl ? 1 : 0), services: c.rate_services + (c.vl ? 1.7 : 0) };
        const buckets = {};
        rows.forEach((r) => { const [k, label] = periodOf(r.date, c.period); const b = (buckets[k] = buckets[k] || { key: k, label, goods: 0, services: 0, count: 0 }); b.goods += r.goods; b.services += r.services; b.count += 1; });
        const cur = periodOf(today, c.period)[0];
        const periods = Object.keys(buckets).sort().reverse().map((k) => { const b = buckets[k], due = deadline(k, c.period);
          return Object.assign({}, b, { goods: r2(b.goods), services: r2(b.services), total: r2(b.goods + b.services), contributions: r2(b.goods * rate.goods / 100) + r2(b.services * rate.services / 100),
            deadline: due, open: k === cur, to_declare: k !== cur && due >= today }); });
        const yr = rows.filter((r) => r.date.slice(0, 4) === String(year)), g = r2(yr.reduce((a, r) => a + r.goods, 0)), sv = r2(yr.reduce((a, r) => a + r.services, 0));
        return ctx.ok({ period: c.period, rates: rate, vl: !!c.vl, periods, sources: { invoices: on("invoices"), orders: on("orders") },
          year: { year, goods: g, services: sv, total: r2(g + sv), ceiling_pct: Math.round(Math.max((g + sv) / 2031, sv / 836) * 10) / 10, ceilings: { goods: 203100, services: 83600 } } });
      },
      async urssaf_detail(key) {
        const c = conf();
        return ctx.ok({ rows: (await receipts()).filter((r) => periodOf(r.date, c.period)[0] === key).map((r) => Object.assign({}, r, { goods: r2(r.goods), services: r2(r.services) })) });
      },
      async urssaf_export() { await ctx.wait(200); const path = `C:\\Users\\Vous\\Documents\\Graou3D\\exports\\urssaf_${day(0)}.csv`; return ctx.ok({ path, msg: "Récapitulatif enregistré : " + path }); },
    };
  });

  /* ---- Messagerie entre amis (3.9) : un ami a déjà écrit, et répond parfois ---- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts;
    const msgs = [];
    const first = (db.friends || [])[0];
    if (!o.empty && first) {
      msgs.push({ id: ctx.newId(), from: "me", to: first.id, body: "Salut ! Tu as encore du PETG orange ?", created_at: ctx.ago(180), read_at: ctx.ago(170) },
        { id: ctx.newId(), from: first.id, to: "me", body: "Oui, presque une bobine entière. Je te la prête quand tu veux 🙂", created_at: ctx.ago(25), read_at: null });
    }
    const unread = () => { const out = {}; msgs.filter((m) => m.to === "me" && !m.read_at).forEach((m) => { out[m.from] = (out[m.from] || 0) + 1; }); return out; };
    const thread = (id) => msgs.filter((m) => (m.from === "me" && m.to === id) || (m.from === id && m.to === "me"))
      .map((m) => ({ id: m.id, mine: m.from === "me", body: m.body, created_at: m.created_at, read: !!m.read_at }));
    const isFriend = (id) => (db.friends || []).some((f) => String(f.id) === String(id));
    const social = ctx.api.social;
    return {
      async social() { const res = await social(); return Object.assign(res, { unread: unread() }); },
      async messages_thread(id) {
        msgs.forEach((m) => { if (m.from === id && m.to === "me" && !m.read_at) m.read_at = ctx.now(); });
        return ctx.ok({ messages: thread(id), unread: unread() });
      },
      async message_send(id, body) {
        body = String(body || "").trim();
        if (!body) return { ok: false, msg: "Écrivez un message." };
        if (body.length > 1000) return { ok: false, msg: "Message trop long (1000 caractères au plus)." };
        if (!isFriend(id)) return { ok: false, msg: "Vous ne pouvez écrire qu'à vos amis." };
        msgs.push({ id: ctx.newId(), from: "me", to: id, body, created_at: ctx.now(), read_at: null });
        // L'ami « lit » puis répond quelques secondes plus tard (une fois sur deux).
        setTimeout(() => { msgs.forEach((m) => { if (m.from === "me" && m.to === id) m.read_at = m.read_at || ctx.now(); });
          if (Math.random() < 0.5) msgs.push({ id: ctx.newId(), from: id, to: "me", body: "Bien reçu, je regarde ça ce soir !", created_at: ctx.now(), read_at: null }); }, 5000);
        return ctx.ok({ messages: thread(id) });
      },
      async message_delete(mid, id) { const i = msgs.findIndex((m) => String(m.id) === String(mid) && m.from === "me"); if (i >= 0) msgs.splice(i, 1); return ctx.ok({ messages: thread(id), msg: "Message retiré." }); },
    };
  });

  /* ---- Import automatique depuis le slicer (3.7) --------------------------------- */
  // Quelques secondes après l'activation de la fonction, un fichier découpé fictif « apparaît ».
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts;
    let folder = "", since = null, announced = false;   // aucun dossier par défaut : il faut le choisir
    const info = () => ({ folder, custom: !!folder, exists: !!folder });
    const live = ctx.api.live;
    return {
      async live() {
        const res = await live();
        if (!(db.settings.features || {}).slicer_watch || !folder) { since = null; return res; }
        since = since || Date.now();
        if (!announced && Date.now() - since > 6000) {
          announced = true;
          res.events = (res.events || []).concat([{ type: "slicer_file", name: "Support_casque_PLA_0.16mm.gcode.3mf", title: "Support_casque_PLA_0.16mm",
            grams: 64.2, hours: 3.4, material: "PLA", nozzle: "0.4 mm", plates: 1 }]);
        }
        return res;
      },
      async slicer_watch_info() { return ctx.ok(info()); },
      async slicer_watch_pick() {
        await ctx.wait(200);
        folder = "C:\\Users\\demo\\Documents\\Exports Bambu Studio";
        return ctx.ok(Object.assign(info(), { msg: o.demo ? "Dans la démo, ce dossier est fictif : aucun dossier de votre PC n'est lu."
          : "Dossier surveillé : " + folder }));
      },
      async slicer_watch_reset() { folder = ""; return ctx.ok(Object.assign(info(), { msg: "Plus aucun dossier n'est surveillé." })); },
    };
  });

  /* ---- Sauvegarde et restauration du compte ------------------------------------- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts;
    const counts = () => ({ spools: db.spools.length, tasks: db.tasks.length, maintenance: db.maintenance.length, shopping: db.shopping.length,
      projects: db.projects.length, bookmarks: db.bookmarks.length, journal: db.settings.print_log.length });
    const stamp = () => new Date().toISOString().slice(0, 19).replace("T", "_").replace(/:/g, "");
    const open_folder = ctx.api.open_folder;
    return {
      async backup_export() {
        await ctx.wait(400);
        const c = counts();
        if (o.demo) return ctx.ok({ path: "", summary: c, msg: `Sauvegarde terminée : ${c.spools} bobine(s), ${c.journal} impression(s) au journal, ${c.tasks} tâche(s). ${NO_FILE}` });
        const path = `C:\\Users\\demo\\Downloads\\Graou3D\\Sauvegardes\\graou3d_${stamp()}.graou3d`;
        return ctx.ok({ path, summary: c, msg: "Sauvegarde enregistrée : " + path });
      },
      async backup_pick() {
        await ctx.wait(300);
        if (o.demo) return ctx.ok({ token: "bk-demo", name: "graou3d_demo.graou3d", summary: Object.assign(counts(), { created_at: new Date(Date.now() - 7 * 86400000).toISOString(), app: "3.6.0" }) });
        return ctx.ok({ token: "bk1", name: "graou3d_2026-09-30_183000.graou3d", summary: { spools: 42, tasks: 12, maintenance: 9, shopping: 7, projects: 3, bookmarks: 18, journal: 128, created_at: "2026-09-30T18:30:00+00:00", app: "3.6.0" } });
      },
      async backup_restore(token, mode, confirmed) {
        await ctx.wait(700);
        if (mode === "replace" && confirmed !== true) return { ok: false, msg: "Confirmez le remplacement de toutes vos données." };
        if (o.demo) return ctx.ok({ msg: "Restauration terminée : la sauvegarde d'exemple contient déjà tout l'atelier fictif, rien n'a changé." });
        return ctx.ok({ msg: mode === "replace" ? "Restauration terminée : 42 bobine(s), 12 tâche(s), 9 rappel(s), 7 achat(s), 3 projet(s), 18 favori(s). Sauvegarde de sécurité : C:\\Users\\demo\\Downloads\\Graou3D\\Sauvegardes\\securite-avant-restauration_2026-10-16_101700.graou3d"
          : "Restauration terminée : 3 bobine(s), 1 tâche(s), 0 rappel(s), 2 achat(s), 0 projet(s), 4 favori(s). 52 élément(s) déjà présent(s) ou refusé(s)." });
      },
      // En démo, « Ouvrir le dossier » (sauvegardes, devis, exports…) le dit au lieu de ne rien faire.
      async open_folder(...a) { return o.demo ? ctx.ok({ msg: "Dans la démo, aucun dossier n'est ouvert : rien n'est écrit sur votre PC." }) : open_folder(...a); },
    };
  });

  /* ---- Mises à jour et signalement d'un problème -------------------------------- */
  // updates : "available" (une version 3.8.1 attend), "uptodate" (déjà à jour), "off" (non configurées).
  // En démo : « à jour », jamais de téléchargement ni d'installation, signalement désactivé.
  G3D_FAKE.extend((ctx) => {
    const o = ctx.opts, mode = o.demo ? (o.updates === "off" ? "off" : "uptodate") : o.updates || "uptodate";
    const enabled = mode !== "off", support = !o.demo && o.support !== false && enabled;
    const version = () => { const c = window.BC && window.BC.state && window.BC.state.constants; return (c && c.version) || "3.8.0"; };
    const upd = { auto: true, progress: { done: 0, total: 0, running: false },
      info: mode === "available" ? { available: true, version: "3.8.1", date: "2026-11-14", size: 78000000,
        notes: "Nouveautés :\n• Réglages › À propos : mises à jour et « Signaler un problème ».\n• Corrections dans le stock et la file d'attente." } : null };
    const DEMO_UPDATE = "Dans la démo, aucune mise à jour n'est téléchargée.";
    const DEMO_SUPPORT = "Le contact du support n'est pas disponible dans la démo.";
    return {
      async update_status() { return ctx.ok({ enabled, auto: upd.auto, version: o.demo ? undefined : version(), update: upd.info, progress: upd.progress, support, support_email: support ? "support@exemple.fr" : "" }); },
      async update_set_auto(on) { upd.auto = on === true; return ctx.ok({ auto: upd.auto }); },
      async update_check(manual) {
        await ctx.wait(manual ? 700 : 400);
        if (!enabled) return manual ? { ok: false, msg: "Les mises à jour ne sont pas configurées dans cette copie de Graou3D." } : ctx.ok({ available: false });
        return upd.info ? ctx.ok(upd.info) : ctx.ok({ available: false, msg: manual ? `Graou3D ${version()} est à jour.` : undefined });
      },
      async update_download() {
        if (o.demo || !upd.info) return { ok: false, msg: o.demo ? DEMO_UPDATE : "Aucune mise à jour à télécharger." };
        upd.progress = { done: 0, total: upd.info.size, running: true };
        for (let i = 1; i <= 10; i++) { await ctx.wait(150); upd.progress.done = Math.round(upd.info.size * i / 10); }
        upd.progress.running = false;
        return ctx.ok({ ready: true, version: upd.info.version });
      },
      async update_install() { if (o.demo) return { ok: false, msg: DEMO_UPDATE }; await ctx.wait(200); return ctx.ok({ msg: "Installation lancée : Graou3D va se fermer." }); },
      async support_report() {
        if (!support) return { ok: false, msg: o.demo ? DEMO_SUPPORT : "Aucune adresse de signalement n'est configurée dans cette copie de Graou3D." };
        await ctx.wait(300);
        return ctx.ok({ file: "C:\\Users\\<utilisateur>\\Downloads\\Graou3D\\rapport-graou3d-20261016-221500.txt", name: "rapport-graou3d-20261016-221500.txt" });
      },
      async support_mail() { return support ? ctx.ok({ opened: true, email: "support@exemple.fr", subject: "Graou3D : question", body: "Bonjour," }) : { ok: false, msg: DEMO_SUPPORT }; },
      async support_open_folder() { return support ? ctx.ok() : { ok: false, msg: DEMO_SUPPORT }; },
    };
  });

  /* ---- Préférences propres au PC (animation d'ouverture, thème) ------------------ */
  // Le vrai programme les écrit dans un fichier du PC : la fausse API les garde en mémoire
  // (l'aperçu ?dev=1 les remplace par des versions sessionStorage dans ui/dev/mock_api.js).
  G3D_FAKE.extend((ctx) => {
    const prefs = { theme: "", last: {} };
    return {
      async remember_intro(mode, v) { prefs.last[mode] = v; return ctx.ok({}); },
      async remember_theme(t) { prefs.theme = t === "light" ? "light" : "dark"; return ctx.ok({}); },
    };
  });

  /* ---- 3.7 : surveillance à deux niveaux et « Retirer une pièce »
     L'engrenage planétaire compte 4 pièces sur le plateau ; « Analyser » simule une alerte de niveau 1
     (pièce décollée, 68 %), que l'on peut ignorer pour l'impression en cours. */
  G3D_FAKE.extend((ctx) => {
    const baseStatus = ctx.api.ai_status;
    const PIECES = { "Engrenage planétaire": ["Couronne", "Soleil", "Satellite 1", "Satellite 2"] };
    const st = { job: "", objects: [], muted: [] };
    function objects() {
      const name = ctx.tele.subtask_name;
      if (st.job !== name) {   // nouvelle impression : nouvelle liste, plus rien d'ignoré
        st.job = name;
        st.objects = (PIECES[name] || [name]).map((n, i) => ({ id: 75 + i * 45, name: n, skipped: false, current: false }));
        st.muted = [];
      }
      const left = st.objects.filter((o) => !o.skipped);
      st.objects.forEach((o) => { o.current = !o.skipped && o === left[Math.floor(ctx.tele.progress / 7) % Math.max(1, left.length)]; });
      return st.objects.map((o) => Object.assign({}, o));
    }
    const printing = () => ["RUNNING", "PAUSE"].includes(ctx.tele.gcode_state);
    const level1 = { status: "warning", confidence: 0.68, score: 0.68, should_pause: false, streak: 0, confirm: 3, frame_id: "e7f8a9", sources: ["apprentissage"],
      defects: [{ type: "detachment", severity: "medium", description: "Probabilité de défaut estimée à 68 %.", label: "Pièce décollée du plateau", box: [0.55, 0.45, 0.8, 0.8] }],
      advice: "La pièce a peut-être bougé : nettoyez le plateau (eau + savon) et ajoutez une bordure.", summary: "À surveiller : pièce décollée du plateau" };
    const calm = { status: "ok", confidence: 0.9, score: 0.1, should_pause: false, defects: [], frame_id: "f0a1b2", sources: ["apprentissage"], advice: "", summary: "Rien d'anormal." };
    return {
      async printer_objects() {
        await ctx.wait(400);
        if (ctx.otherActive && ctx.otherActive()) return { ok: false, msg: "Dans la démo, seule la Bambu Lab A1 a plusieurs pièces sur son plateau." };
        if (!printing()) return { ok: false, msg: "Aucune impression en cours." };
        return ctx.ok({ objects: objects() });
      },
      async printer_skip_object(id) {
        const o = objects().find((x) => String(x.id) === String(id));
        if (!o) return { ok: false, msg: "Cette pièce n'est pas sur le plateau en cours." };
        if (o.skipped) return { ok: false, msg: "Cette pièce est déjà retirée." };
        if (st.objects.filter((x) => !x.skipped).length <= 1) return { ok: false, msg: "C'est la dernière pièce du plateau : utilisez plutôt « Arrêter »." };
        st.objects.find((x) => x.id === o.id).skipped = true;
        return ctx.ok({ msg: `« ${o.name} » ne sera plus imprimée.`, objects: objects() });
      },
      async ai_mute(type) {
        objects();
        if (!st.muted.includes(type)) st.muted.push(type);
        return ctx.ok({ muted: st.muted.slice(), msg: "Ce défaut ne sera plus signalé pendant cette impression." });
      },
      async ai_status() { objects(); return Object.assign(await baseStatus(), { muted: st.muted.slice() }); },
      async ai_analyze_now() {
        await ctx.wait(1200);
        objects();
        const other = ctx.otherActive && ctx.otherActive();
        if (other || ctx.tele.gcode_state !== "RUNNING" || st.muted.includes("detachment")) return ctx.ok({ result: calm });
        const topic = String((ctx.db.settings.alerts || {}).push_topic || "");
        const phone = topic && ctx.db.settings.ai.notify_level1 !== false;
        const photo = phone && ctx.db.settings.ai.notify_photo === true && topic.length >= 20 && new Set(topic.toLowerCase()).size >= 8;
        ctx.push({ type: "alert", kind: "defect", level: "warning", title: "Défaut détecté par la caméra",
          message: "Pièce décollée du plateau. " + (photo ? "Photo envoyée sur le téléphone, avec les boutons « Mettre en pause » et « Ignorer »."
            : phone ? "Notification envoyée sur le téléphone, sans photo." : level1.advice) });
        return ctx.ok({ result: level1 });
      },
    };
  });

  /* ---- Mode entreprise › Plusieurs imprimantes : la Bambu Lab A1 simulée (n° 1) + une Prusa MK4 libre
     et un Voron (Klipper) en pause. Visible seulement si « Mode entreprise » et « Plusieurs imprimantes »
     sont cochés dans Réglages › Fonctions. ---------------------------------------------------------- */
  G3D_FAKE.extend((ctx) => {
    const { db, tele } = ctx;
    const base = { live: ctx.api.live, dashboard: ctx.api.dashboard, printer_config: ctx.api.printer_config, printer_connect: ctx.api.printer_connect,
      job_control: ctx.api.job_control, printer_disconnect: ctx.api.printer_disconnect };
    const multi = () => { const f = db.settings.features || {}; return !!(f.enterprise && f.multi_printer); };
    db.printer.id = 1;
    const idleTele = (mode) => ({ connected: true, mode, state: "IDLE", gcode_state: "IDLE", nozzle_temp: 24.6, nozzle_target: 0, bed_temp: 23.8, bed_target: 0,
      progress: 0, remaining_min: 0, layer_num: 0, total_layer_num: 0, subtask_name: "", speed_level: 2, ams_trays: [], active_tray: null, hms: [], printing: false, camera: false });
    const others = new Map([
      [2, { printer: { id: 2, configured: true, protocol: "PRUSALINK", name: "Prusa MK4", host: "192.168.1.61", serial: "", port: 80, camera_url: "", cloud_email: "", has_secret: true, pinned_ports: [] },
        tele: idleTele("PRUSALINK"), material: "" }],
      [3, { printer: { id: 3, configured: true, protocol: "KLIPPER", name: "Voron 2.4", host: "192.168.1.70", serial: "", port: 7125, camera_url: "http://192.168.1.70/webcam/?action=snapshot", cloud_email: "", has_secret: false, pinned_ports: [] },
        tele: Object.assign(idleTele("KLIPPER"), { state: "PAUSED", gcode_state: "PAUSE", nozzle_temp: 243.2, nozzle_target: 245, bed_temp: 99.6, bed_target: 100,
          progress: 63, remaining_min: 71, layer_num: 126, total_layer_num: 200, subtask_name: "Boîtier ventilateur ABS" }), material: "ABS", color: "#2B2D31" }],
    ]);
    let active = 1;
    const current = () => (multi() && active !== 1 ? others.get(active) : null);
    ctx.otherActive = () => !!current();   // une autre imprimante que la Bambu Lab simulée est active
    const STATE = (t) => (!t.connected ? "offline" : t.gcode_state === "FAILED" ? "error" : t.gcode_state === "PAUSE" ? "paused"
      : ["RUNNING", "PREPARE"].includes(t.gcode_state) ? "printing" : "idle");
    function card(id, pr, t, extra = {}) {
      const state = STATE(t), busy = state === "printing" || state === "paused";
      return Object.assign({ id, name: pr.name, protocol: pr.protocol, brand: { BAMBU_LAN: "Bambu Lab", BAMBU_CLOUD: "Bambu Lab", PRUSALINK: "Prusa", KLIPPER: "Klipper", OCTOPRINT: "OctoPrint", CREALITY: "Creality" }[pr.protocol] || "",
        state, gcode_state: t.gcode_state, active: id === active, progress: busy ? t.progress : 0, remaining_min: busy ? t.remaining_min : 0,
        file: busy ? t.subtask_name : "", material: "", color_hex: "", message: t.connected ? "" : "Pas de réponse de l'imprimante." }, extra);
    }
    function fleet() {
      if (!multi()) return null;
      const tray = (tele.ams_trays || []).find((x) => x.slot === tele.active_tray) || {};
      const busy = ["RUNNING", "PREPARE", "PAUSE"].includes(tele.gcode_state);
      const list = [card(1, db.printer, tele, busy ? { material: tray.material || "", color_hex: tray.color_hex || "" } : {})];
      for (const [id, o] of others) list.push(card(id, o.printer, o.tele, o.material && ["RUNNING", "PAUSE"].includes(o.tele.gcode_state) ? { material: o.material, color_hex: o.color } : {}));
      return list;
    }
    // Les imprimantes fixes avancent un peu quand elles impriment (le Voron, si on le relance).
    let last = Date.now();
    function step() {
      const dt = Math.min(10, (Date.now() - last) / 1000);
      last = Date.now();
      for (const o of others.values()) {
        const t = o.tele;
        if (t.gcode_state !== "RUNNING") continue;
        t.progress = Math.min(100, t.progress + dt * 0.08);
        t.remaining_min = Math.max(0, Math.round((100 - t.progress) * 1.9));
        if (t.progress >= 100) Object.assign(t, { gcode_state: "FINISH", state: "COMPLETE", printing: false, nozzle_target: 0, bed_target: 0, progress: 100 });
        t.progress = Math.round(t.progress * 10) / 10;
      }
    }
    // Autre imprimante : la carte « Surveillance caméra » ne montre pas le dernier résultat de l'A1.
    const teleOf = (o, ref) => Object.assign({}, o.tele, { progress: Math.floor(o.tele.progress), thermal: { level: "ok", label: "Stable", band_c: 4, deviation_rms: 0.6, curve: [] },
      ai: ref && ref.ai && Object.assign({}, ref.ai, { last_result: null }), camera: false, printing: ["RUNNING", "PREPARE"].includes(o.tele.gcode_state) });
    const view = (res) => {
      step();
      const o = current();
      if (o) { res.telemetry = teleOf(o, res.telemetry); if ("printer" in res) res.printer = ctx.withCaps(o.printer); }
      res.fleet = fleet();
      return res;
    };
    return {
      async live() { return view(await base.live()); },
      async dashboard() { return view(await base.dashboard()); },
      async printer_config() { const o = current(); return o ? ctx.ok({ printer: ctx.withCaps(o.printer) }) : base.printer_config(); },
      async printer_select(id) {
        await ctx.wait(150);
        if (!multi()) return { ok: false, msg: "Activez « Plusieurs imprimantes » dans Réglages › Fonctions." };
        id = Number(id);
        if (id !== 1 && !others.has(id)) return { ok: false, msg: "Imprimante introuvable." };
        active = id;
        const res = await base.dashboard();
        return ctx.ok(view({ printer: res.printer, telemetry: res.telemetry }));
      },
      async printer_remove(id) {
        id = Number(id);
        if (id === 1) return { ok: false, msg: "Dans la démo, la Bambu Lab A1 reste en place." };
        others.delete(id);
        if (active === id) active = 1;
        const res = await base.dashboard();
        return ctx.ok(Object.assign(view({ printer: res.printer, telemetry: res.telemetry }), { msg: "Imprimante retirée." }));
      },
      async printer_connect(cfg) {
        const o = current();
        if (multi() && cfg && cfg.printer_id === "new") {
          await ctx.wait(500);
          const id = ctx.newId();
          const pr = { id, configured: true, protocol: cfg.protocol, name: cfg.name || "Nouvelle imprimante", host: cfg.host || "", serial: cfg.serial || "", port: Number(cfg.port) || null,
            camera_url: cfg.camera_url || "", cloud_email: "", has_secret: !!(cfg.access_code || cfg.api_key), pinned_ports: [] };
          others.set(id, { printer: pr, tele: idleTele(cfg.protocol), material: "" });
          active = id;
          return ctx.ok({ msg: `Connecté à ${pr.name}.`, printer: ctx.withCaps(pr) });
        }
        if (o) {
          await ctx.wait(400);
          Object.assign(o.printer, { protocol: cfg.protocol, name: cfg.name || o.printer.name, host: cfg.host || o.printer.host });
          o.tele.connected = true;
          return ctx.ok({ msg: `Connecté à ${o.printer.name}.`, printer: ctx.withCaps(o.printer) });
        }
        const res = await base.printer_connect(cfg);
        if (res.printer) res.printer.id = 1;
        return res;
      },
      async printer_disconnect() { const o = current(); if (!o) return base.printer_disconnect(); o.tele.connected = false; return ctx.ok({ msg: "Imprimante déconnectée." }); },
      async job_control(a) {
        const o = current();
        if (!o) return base.job_control(a);
        const t = o.tele;
        if (a === "pause" && t.gcode_state === "RUNNING") Object.assign(t, { gcode_state: "PAUSE", state: "PAUSED" });
        else if (a === "resume" && t.gcode_state === "PAUSE") Object.assign(t, { gcode_state: "RUNNING", state: "PRINTING" });
        else if (a === "stop" && ["RUNNING", "PAUSE"].includes(t.gcode_state)) Object.assign(t, { gcode_state: "FAILED", state: "CANCELLED", nozzle_target: 0, bed_target: 0 });
        else return { ok: false, msg: "Aucune impression en cours sur cette imprimante." };
        return ctx.ok({ msg: { pause: "Pause envoyée.", resume: "Reprise envoyée.", stop: "Arrêt envoyé." }[a] });
      },
    };
  });

  /* ---- Atelier partagé + plusieurs imprimantes : une imprimante d'un membre ajoutée à MON parc ---- */
  G3D_FAKE.extend((ctx) => {
    const connect = ctx.api.printer_connect;
    return {
      async printer_connect(cfg) {
        const res = await connect(cfg);
        if (res.ok && cfg && cfg.printer_id === "new" && cfg.host && ctx.db.wsAdopted) ctx.db.wsAdopted.add(String(cfg.host).trim());
        return res;
      },
    };
  });

  /* ---- Aperçu 3D : un engrenage dessiné à la volée (ui/dev n'est pas dans l'exe) -- */
  // Même format que backend/preview3d.py : coordonnées en uint16 (0 → 65535 sur la taille), encodées en base64.
  G3D_FAKE.extend((ctx) => {
    const b64 = (typed) => { const u8 = new Uint8Array(typed.buffer); let s = ""; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000)); return btoa(s); };
    const TEETH = 16, R_OUT = 34, R_IN = 29, R_HOLE = 8, H = 12;
    // Contour d'un engrenage : alternance dent / creux, centré sur (34, 34).
    function outline(n) {
      const pts = [];
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2, tooth = Math.floor((i / n) * TEETH * 4) % 4;
        const r = tooth === 0 || tooth === 1 ? R_OUT : R_IN;
        pts.push([34 + Math.cos(a) * r, 34 + Math.sin(a) * r]);
      }
      return pts;
    }
    function mesh() {
      const N = TEETH * 8, outer = outline(N), inner = [];
      for (let i = 0; i < N; i++) { const a = (i / N) * Math.PI * 2; inner.push([34 + Math.cos(a) * R_HOLE, 34 + Math.sin(a) * R_HOLE]); }
      const tri = [];
      const q = (x, y, z) => tri.push(Math.round(x / 68 * 65535), Math.round(y / 68 * 65535), Math.round(z / H * 65535));
      for (let i = 0; i < N; i++) {
        const j = (i + 1) % N, a = outer[i], b = outer[j], c = inner[i], d = inner[j];
        for (const z of [0, H]) { q(...a, z); q(...b, z); q(...c, z); q(...b, z); q(...d, z); q(...c, z); }   // dessus / dessous
        q(...a, 0); q(...b, 0); q(...b, H); q(...a, 0); q(...b, H); q(...a, H);                               // flanc extérieur
        q(...c, 0); q(...d, H); q(...d, 0); q(...c, 0); q(...c, H); q(...d, H);                               // trou central
      }
      const n = tri.length / 9;
      // Analyse d'impression (même format que preview3d.analyze) : pièce plate, aucun surplomb, tout le dessous posé.
      const area = (pts) => Math.abs(pts.reduce((s, p, i) => { const q = pts[(i + 1) % pts.length]; return s + p[0] * q[1] - q[0] * p[1]; }, 0)) / 2;
      const contact = Math.round((area(outer) - area(inner)) * 10) / 10;
      const ratio = Math.round(H / (2 * Math.sqrt(contact / Math.PI)) * 100) / 100;
      const analysis = { overhang_pct: 0, overhang_mm2: 0, contact_mm2: contact, height_mm: H, risk: "ok", ratio, supports: "none",
        key: "asis", label: "Telle quelle", best: null, flags: b64(new Uint8Array(Math.ceil(n / 8))) };
      return { kind: "mesh", triangles: n, total_triangles: n, reduced: false, min: [-34, -34, 0], size: [68, 68, H], data: b64(new Uint16Array(tri)), analysis };
    }
    function gcode() {
      const layers = 40, z = [], starts = [0], seg = [], typ = [], N = 96;
      const P = (x, y) => [Math.round(x / 68 * 65535), Math.round(y / 68 * 65535)];
      for (let L = 0; L < layers; L++) {
        z.push(r2(0.2 * (L + 1)));
        const ring = (pts, t) => { for (let i = 0; i < pts.length; i++) { const a = pts[i], b = pts[(i + 1) % pts.length]; seg.push(...P(...a), ...P(...b)); typ.push(t); } };
        const circle = (r) => Array.from({ length: N }, (_, i) => [34 + Math.cos(i / N * Math.PI * 2) * r, 34 + Math.sin(i / N * Math.PI * 2) * r]);
        if (L === 0) ring(circle(R_OUT + 4), 8);                       // jupe
        ring(outline(N), 1);                                            // paroi externe
        ring(circle(R_IN - 1.5), 2);                                    // paroi interne
        ring(circle(R_HOLE), 1);
        const top = L === 0 || L >= layers - 3;
        for (let k = -24; k <= 24; k += top ? 2 : 5) {                  // remplissage en diagonale, plein dessous / dessus
          const d = Math.sqrt(Math.max(0, 26 * 26 - k * k)), dir = L % 2 ? 1 : -1;
          if (Math.abs(k) < R_HOLE + 1) continue;
          seg.push(...P(34 + k, 34 - dir * d), ...P(34 + k, 34 + dir * d)); typ.push(top ? (L === 0 ? 4 : 5) : 3);
        }
        starts.push(typ.length);
      }
      return { kind: "gcode", segments: typ.length, truncated: false, types: ["Autre", "Paroi externe", "Paroi interne", "Remplissage", "Plein", "Dessus", "Support", "Pont", "Jupe / bordure"],
        min: [0, 0], size: [68, 68], layers_z: z, layer_start: b64(new Uint32Array(starts)), data: b64(new Uint16Array(seg)), type_data: b64(new Uint8Array(typ)) };
    }
    let cache = null;
    return {
      async preview_model() { await ctx.wait(300); return ctx.ok({ preview: (cache = cache || {}).mesh || (cache.mesh = mesh()) }); },
      async preview_job() { await ctx.wait(300); return ctx.ok({ preview: (cache = cache || {}).gcode || (cache.gcode = gcode()) }); },
    };
  });

  /* ---- 3.7 : fil allégé, fiches recettes, bandeau des timelapses, sauvegardes automatiques ---------- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts;
    const RECIPE_KEYS = ["title", "printer", "brand", "material", "color_hex", "grams", "hours", "nozzle_mm", "layer_mm", "nozzle_temp", "bed_temp", "speed_mm_s", "notes"];
    const clean = (d) => {
      const r = { kind: "graou3d-recette" };
      for (const k of RECIPE_KEYS) { const v = d && d[k]; if (v !== undefined && v !== null && v !== "") r[k] = typeof v === "string" && !isNaN(Number(v)) && k !== "title" && k !== "notes" ? Number(v) : v; }
      return r;
    };
    // Petite photo dessinée (ui/dev n'est pas dans l'exe) : la vignette du fil et sa version « entière ».
    function photo(side, label) {
      const c = document.createElement("canvas");
      c.width = side; c.height = Math.round(side * 0.75);
      const g = c.getContext("2d");
      const grad = g.createLinearGradient(0, 0, c.width, c.height);
      grad.addColorStop(0, "#2b2622"); grad.addColorStop(1, "#5a3a20");
      g.fillStyle = grad; g.fillRect(0, 0, c.width, c.height);
      g.fillStyle = "#DA5A04";
      const u = c.width / 16;
      for (let i = 0; i < 9; i++) g.fillRect(u * (5 + (i % 2) * 0.3), c.height - u * (2 + i * 0.55), u * (6 - i * 0.35), u * 0.5);
      g.fillStyle = "rgba(255,255,255,.85)"; g.font = `600 ${Math.round(u * 0.8)}px system-ui`; g.fillText(label, u, u * 1.3);
      return c.toDataURL("image/jpeg", 0.85);
    }
    const thumbs = {};
    if (!o.empty && db.feed.length) {
      const recipe = { kind: "graou3d-recette", title: "Benchy express", printer: "A1 du salon", brand: "Bambu Lab", material: "PLA Matte",
        color_hex: "#C0392B", grams: 14, hours: 0.75, nozzle_mm: 0.4, layer_mm: 0.2, nozzle_temp: 220, bed_temp: 60, speed_mm_s: 250,
        notes: "Ventilation à fond dès la 3e couche." };
      db.feed.splice(1, 0, { id: 403, user_id: "u-3", pseudo: "Venoxy", avatar_data: "", title: recipe.title, material: "Bambu Lab PLA Matte", grams: 14,
        duration_h: 0.75, notes: "", makerworld_url: "", image_data: photo(560, "Benchy"), image_full: true, preset_name: "graou3d-recette.json",
        has_preset: true, recipe, created_at: ctx.ago(60), likes: 7, liked: false, mine: false });
      thumbs[403] = () => photo(1200, "Benchy");
    }
    if (!Array.isArray(db.settings.recipes)) db.settings.recipes = [];
    const save = (r, author, postId) => {
      db.settings.recipes = [Object.assign({}, r, { id: "r" + ctx.newId(), saved_at: ctx.now(), author: author || "", post_id: postId ? String(postId) : undefined }),
        ...db.settings.recipes.filter((x) => !postId || x.post_id !== String(postId))].slice(0, 30);
      return ctx.ok({ settings: db.settings, msg: "Fiche enregistrée dans vos Favoris." });
    };

    // Timelapse fictif : quelques secondes d'impression dessinées puis enregistrées en WebM, dans le navigateur.
    let video = null;
    function makeVideo() {
      if (video) return video;
      video = new Promise((resolve, reject) => {
        if (typeof MediaRecorder === "undefined") { reject(new Error("Enregistrement vidéo indisponible.")); return; }
        const c = document.createElement("canvas");
        c.width = 640; c.height = 360;
        const g = c.getContext("2d");
        const mime = ["video/webm;codecs=vp8", "video/webm"].find((m) => MediaRecorder.isTypeSupported(m));
        const rec = new MediaRecorder(c.captureStream(30), { mimeType: mime });
        const parts = [];
        rec.ondataavailable = (e) => { if (e.data && e.data.size) parts.push(e.data); };
        rec.onstop = async () => resolve(new Uint8Array(await new Blob(parts, { type: "video/webm" }).arrayBuffer()));
        const t0 = performance.now(), DUR = 2500;
        const frame = () => {
          const k = Math.min(1, (performance.now() - t0) / DUR);
          g.fillStyle = "#1d1b1a"; g.fillRect(0, 0, 640, 360);
          g.fillStyle = "#3a3633"; g.fillRect(120, 300, 400, 14);
          g.fillStyle = "#DA5A04";
          const layers = Math.floor(k * 24);
          for (let i = 0; i < layers; i++) g.fillRect(250 - Math.sin(i / 7) * 30, 292 - i * 9, 140 + Math.sin(i / 7) * 60, 8);
          g.fillStyle = "#ddd"; g.fillRect(230 + (k * 900) % 180, 280 - layers * 9, 30, 14);
          if (k < 1) requestAnimationFrame(frame); else setTimeout(() => rec.stop(), 120);
        };
        rec.start(500);
        frame();
      });
      return video;
    }
    const tlOut = {};
    const download = ctx.api.timelapse_download;

    return {
      async post_image(id) { await ctx.wait(150); return ctx.ok({ src: thumbs[id] ? thumbs[id]() : (ctx.find(db.feed, id) || {}).image_data || "" }); },
      async recipe_publish(d) {
        await ctx.wait(300);
        const r = clean(d);
        if (!r.title) return { ok: false, msg: "Ajoutez un titre." };
        db.feed.unshift({ id: ctx.newId(), user_id: "u-1", pseudo: db.profile.pseudo, avatar_data: db.profile.avatar_data, title: r.title,
          material: [r.brand, r.material].filter(Boolean).join(" "), grams: r.grams || 0, duration_h: r.hours || 0, notes: "", makerworld_url: "",
          image_data: d.image_data || "", image_full: false, preset_name: "graou3d-recette.json", has_preset: true, recipe: r, created_at: ctx.now(), likes: 0, liked: false, mine: true });
        return ctx.ok({ msg: "Fiche recette publiée dans le fil." });
      },
      async recipe_code(d) {
        const json = JSON.stringify(clean(d));
        return ctx.ok({ code: "G3DR1." + btoa(unescape(encodeURIComponent(json))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") });
      },
      async recipe_save(id) { await ctx.wait(200); const p = ctx.find(db.feed, id); if (!p || !p.recipe) return { ok: false, msg: "Cette publication n'est pas une fiche recette." }; return save(p.recipe, p.pseudo, id); },
      async recipe_import(code) {
        const s = String(code || "").replace(/\s+/g, "");
        try {
          if (!s.startsWith("G3DR1.")) throw new Error();
          const r = clean(JSON.parse(decodeURIComponent(escape(atob(s.slice(6).replace(/-/g, "+").replace(/_/g, "/"))))));
          if (!r.title) throw new Error();
          return save(r, "", null);
        } catch (e) { return { ok: false, msg: "Ce texte n'est pas une fiche recette Graou3D." }; }
      },
      async recipe_delete(id) { db.settings.recipes = db.settings.recipes.filter((r) => r.id !== id); return ctx.ok({ settings: db.settings }); },

      async timelapse_download(path, play) { const r = await download(path, play); return Object.assign(r, { name: String(path).split("/").pop() }); },
      async timelapse_chunk(name, offset) {
        let bytes;
        try { bytes = await makeVideo(); } catch (e) { return { ok: false, msg: e.message }; }
        const CH = 3 * 1024 * 1024, start = Number(offset) || 0, part = bytes.subarray(start, start + CH);
        let bin = "";
        for (let i = 0; i < part.length; i += 0x8000) bin += String.fromCharCode.apply(null, part.subarray(i, i + 0x8000));
        return ctx.ok({ data: btoa(bin), size: bytes.length, next: start + part.length, done: start + part.length >= bytes.length, type: "video/webm" });
      },
      async timelapse_banner_begin(name) { const token = "tl" + ctx.newId(); tlOut[token] = { name: String(name || "timelapse").replace(/\.[^.]+$/, ""), size: 0 }; return ctx.ok({ token }); },
      async timelapse_banner_part(token, data) { const j = tlOut[token]; if (!j) return { ok: false, msg: "Enregistrement interrompu : recommencez." }; j.size += Math.floor(String(data).length * 3 / 4); return ctx.ok(); },
      async timelapse_banner_end(token, keep) {
        const j = tlOut[token];
        delete tlOut[token];
        if (!j || keep !== true) return ctx.ok({ path: "" });
        if (o.demo) return ctx.ok({ path: "(démo : aucun fichier écrit)", msg: "Vidéo fabriquée. Dans la démo, rien n'est écrit sur votre PC." });
        const path = `C:\\Users\\demo\\Downloads\\Graou3D\\timelapses\\${j.name} - bandeau.webm`;
        return ctx.ok({ path, msg: "Vidéo avec bandeau enregistrée : " + path });
      },

      async backup_auto_info() {
        await ctx.wait(150);
        const day = 86400;
        const items = db.autoBackup === false || o.empty ? [] : [3, 10, 17].map((d) => ({ name: `auto_3f9a0c12e4_${d}.graou3d`, at: Math.floor(Date.now() / 1000) - d * day }));
        return ctx.ok({ enabled: db.autoBackup !== false, keep: 5, items });
      },
      async backup_auto_set(on) { db.autoBackup = on === true; return ctx.ok({ enabled: db.autoBackup }); },
    };
  });
})();

/* ---- 3.8.2 : slicer multicouleur (couleurs d'un .3mf, coloriage) -------------------------------------- */
(function () {
  "use strict";
  if (!window.G3D_FAKE) return;
  G3D_FAKE.extend((ctx) => {
    const baseStart = ctx.api.slicer_start, baseJob = ctx.api.slicer_job;
    const b64 = (arr) => { const u = new Uint8Array(arr.buffer); let s = ""; for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s); };
    const rgb = (h) => { const m = /^#?([0-9a-f]{6})/i.exec(h || ""); return m ? [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)) : null; };
    const near = (color) => {
      let best = 1, bd = Infinity;
      for (const s of ctx.db.spools.filter((x) => x.ams_slot > 0)) {
        const p = rgb(color), q = rgb(s.color_hex);
        if (!p || !q) continue;
        const d = Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
        if (d < bd) { bd = d; best = s.ams_slot; }
      }
      return best;
    };
    // Tour à trois étages : chaque face plate se peint d'un clic.
    function tower() {
      const v = [], f = [];
      const box = (x0, y0, z0, x1, y1, z1) => {
        const b = v.length / 3;
        for (const [x, y, z] of [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]) v.push(x, y, z);
        for (const t of [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]) f.push(b + t[0], b + t[1], b + t[2]);
      };
      box(0, 0, 0, 30, 30, 12); box(5, 5, 12, 25, 25, 24); box(10, 10, 24, 20, 20, 34);
      return { paint_id: "demo-paint", triangles: f.length / 3, vertices: v.length / 3, min: [0, 0, 0], size: [30, 30, 34],
        positions: b64(new Float32Array(v)), indices: b64(new Uint32Array(f)) };
    }
    let mapping = [];
    return {
      async slicer_inspect(source) {
        await ctx.wait(200);
        const name = String((source && source.name) || "");
        if (/\.3mf$/i.test(name)) {
          const filaments = [{ color: "#0A2CA5", type: "PLA" }, { color: "#F4F4F4", type: "PLA" }, { color: "#C12E1F", type: "PLA" }];
          return ctx.ok({ filaments, painted: true, multicolor: true, paintable: false, reason: "Ce fichier a déjà ses couleurs : choisissez la bobine de chacune.", slots: filaments.map((x) => near(x.color)) });
        }
        return ctx.ok({ filaments: [], painted: false, multicolor: false, paintable: true, reason: "", slots: [] });
      },
      async paint_open() { await ctx.wait(300); return ctx.ok(tower()); },
      async paint_save() { return ctx.ok({ msg: "Dans la démo, rien n'est écrit sur votre PC." }); },
      async slicer_start(source, quality, slot, material, colors) {
        mapping = (colors && colors.slots) || [];
        const res = await baseStart(source, quality, (mapping[0] || slot), material);
        if (res && res.job) res.job.mapping = mapping;
        return res;
      },
      async slicer_job(id) {
        const res = await baseJob(id);
        const j = res && res.job;
        if (j && mapping.length > 1) {
          j.mapping = mapping;
          if (j.status === "ready" && j.analysis && !j.analysis.filaments) {
            const g = j.analysis.grams || 60;
            j.analysis.filaments = mapping.map((s, i) => ({ id: i + 1, grams: Math.round(g / mapping.length) }));
            j.stock = { ok: true, message: `Stock suffisant pour chaque couleur (${Math.round(g)} g en tout).` };
          }
        }
        return res;
      },
    };
  });
})();

/* ---- 4.3 : planning par imprimante, carnet de clients, tableau de bord Pro, import des ventes ------------ */
(function () {
  "use strict";
  if (!window.G3D_FAKE) return;
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, s = db.settings;
    Object.assign(s.features, { planning: false, clients: false }, (ctx.opts.features || {}));
    const on = (k) => !!(s.features.enterprise && s.features[k]);
    const need = (k, label) => (on(k) ? null : { ok: false, msg: `Activez d'abord « ${label} » dans Réglages › Fonctions (mode entreprise).` });
    const iso = (d) => { const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000); return z.toISOString().slice(0, 16); };
    const day = (n) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);
    const r2 = (v) => Math.round((Number(v) + Number.EPSILON) * 100) / 100;
    s.planning = s.planning || { night: { on: true, start: "22:00", end: "07:00" } };
    // Durées et dates limites d'exemple sur la file de démo.
    (db.tasks || []).forEach((t, i) => { if (t.est_hours === undefined) Object.assign(t, { est_hours: [2.5, 0, 4, 1.5, 6][i % 5], due_on: i % 2 ? day(i) : null, position: 0, printer_id: null }); });

    /* Même règle que backend/planning.py, en plus simple : à la suite, sur la première imprimante libre, rien ne démarre la nuit. */
    const mins = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
    function launchAt(d, night) {
      if (!night.on) return d;
      const a = mins(night.start), b = mins(night.end), m = d.getHours() * 60 + d.getMinutes();
      const inside = a < b ? a <= m && m < b : m >= a || m < b;
      if (!inside) return d;
      const e = new Date(d); e.setHours(Math.floor(b / 60), b % 60, 0, 0);
      if (e <= d) e.setDate(e.getDate() + 1);
      return e;
    }
    const sortKey = (t) => [t.position > 0 ? t.position : 1e6, t.due_on || "9999", t.id];
    const cmp = (a, b) => { const x = sortKey(a), y = sortKey(b); for (let i = 0; i < 3; i++) { if (x[i] < y[i]) return -1; if (x[i] > y[i]) return 1; } return 0; };
    async function printers() {
      const fl = (await ctx.api.dashboard()).fleet;
      return fl && fl.length ? fl.map((c) => ({ id: c.id, name: c.name, busy: c.state === "printing" ? c.remaining_min : 0 }))
        : [{ id: 1, name: db.printer.name || "Imprimante", busy: ctx.tele.printing ? ctx.tele.remaining_min : 0 }];
    }
    async function planPayload(msg) {
      const night = s.planning.night, now = new Date(); now.setSeconds(0, 0);
      const ps = await printers();
      const lanes = ps.map((p) => ({ printer: { id: p.id, name: p.name }, items: [], free: new Date(now.getTime() + (p.busy || 0) * 60000) }));
      const tasks = (db.tasks || []).filter((t) => t.status === "todo" || t.status === "printing");
      let late = 0, estimated = 0;
      const put = (t, lane, start) => {
        const guessed = !(t.est_hours > 0), hours = guessed ? Math.max(0.25, (t.estimated_grams || 12) / 12) : Number(t.est_hours);
        const end = new Date(start.getTime() + hours * 3600000);
        const isLate = !!(t.due_on && end > new Date(t.due_on + "T20:00:00"));
        late += isLate; estimated += guessed;
        lane.items.push({ id: t.id, title: t.title, status: t.status, start: iso(start), end: iso(end), hours: r2(hours), guessed, due_on: t.due_on || null, late: isLate,
          order_id: t.order_id || null, material: t.material || "", grams: t.estimated_grams || 0, pinned: t.printer_id != null });
        if (end > lane.free) lane.free = end;
      };
      tasks.filter((t) => t.status === "printing").forEach((t) => put(t, lanes[0], new Date(now.getTime() - 3600000)));
      tasks.filter((t) => t.status === "todo").sort(cmp).forEach((t) => {
        const pinned = lanes.find((l) => t.printer_id != null && String(l.printer.id) === String(t.printer_id));
        const lane = pinned || lanes.reduce((a, b) => (launchAt(b.free, night) < launchAt(a.free, night) ? b : a));
        put(t, lane, launchAt(lane.free, night));
      });
      const end = new Date(Math.max(...lanes.map((l) => l.free.getTime())));
      const orders = on("orders") ? (((await ctx.api.orders_list()).orders) || []).filter((o) => o.mine && ["todo", "printing", "done"].includes(o.status))
        .map((o) => ({ id: o.id, title: o.title, client: (o.client || {}).name || "", due_on: o.due_on })) : [];
      return ctx.ok({ ready: true, lanes: lanes.map((l) => ({ printer: l.printer, items: l.items, free_at: iso(l.free) })), late, estimated, end: iso(end), night,
        now: iso(now), rate: 12, personal_rate: false, printers: ps.map((p) => ({ id: p.id, name: p.name })), tasks, orders, msg });
    }
    const todoSorted = () => (db.tasks || []).filter((t) => t.status === "todo").sort(cmp);

    /* Clients : tirés des devis et commandes de la démo, plus les fiches enregistrées ici. */
    const saved = ctx.opts.empty ? [] : [{ id: 901, name: "Mairie de Metz", company: "Service communication", email: "communication@mairie-demo.fr", phone: "03 87 00 00 00", address: "1 place d'Armes\n57000 Metz", siren: "215704632", vat_number: "", notes: "Paiement par mandat administratif (30 jours).", source: "", updated_at: ctx.ago(3 * 1440) }];
    const keyOf = (n, e) => (String(e || "").includes("@") ? "e:" + String(e).toLowerCase() : "n:" + String(n || "").trim().toLowerCase());
    async function people() {
      const map = new Map();
      const get = (c) => {
        if (!c || !String(c.name || "").trim()) return null;
        let k = keyOf(c.name, c.email);
        const byName = keyOf(c.name, "");
        if (!map.has(k) && map.has(byName)) k = byName;
        for (const [key, p] of map) if (!map.has(k) && p.name.toLowerCase() === String(c.name).trim().toLowerCase()) k = key;
        if (!map.has(k)) map.set(k, { key: k, id: null, name: c.name, company: "", email: c.email || "", phone: "", address: c.address || "", siren: "", vat_number: "", notes: "", source: "",
          saved: false, quotes: 0, accepted: 0, orders: 0, invoices: 0, revenue_ht: 0, open_ht: 0, last: "", sources: [] });
        return map.get(k);
      };
      const touch = (p, d) => { d = String(d || "").slice(0, 10); if (d > p.last) p.last = d; };
      saved.forEach((c) => { const p = get(c); Object.assign(p, c, { saved: true }); touch(p, c.updated_at); });
      if (on("quotes")) (((await ctx.api.quotes_list()).quotes) || []).forEach((q) => { const p = get(q.client); if (p) { p.quotes += 1; p.accepted += q.status === "accepted"; touch(p, q.issued_on); } });
      if (on("orders")) (((await ctx.api.orders_list()).orders) || []).filter((o) => o.mine).forEach((o) => {
        const p = get(o.client); if (!p) return;
        p.orders += 1;
        if (o.source && !p.sources.includes(o.source)) p.sources.push(o.source);
        if (o.status === "paid") p.revenue_ht = r2(p.revenue_ht + o.price_ht); else if (o.status !== "paid") p.open_ht = r2(p.open_ht + o.price_ht);
        touch(p, o.created_at);
      });
      return [...map.values()].sort((a, b) => (b.last + b.name).localeCompare(a.last + a.name));
    }
    async function clientsPayload(msg) {
      const list = await people();
      const paying = list.filter((p) => p.revenue_ht > 0);
      return ctx.ok({ clients: list, ready: true, msg,
        summary: { count: list.length, saved: list.filter((p) => p.saved).length, paying: paying.length, repeat: list.filter((p) => p.orders + p.invoices >= 2).length,
          top: paying.sort((a, b) => b.revenue_ht - a.revenue_ht).slice(0, 5).map((p) => ({ name: p.name, revenue_ht: p.revenue_ht })) } });
    }

    /* Import : la démo lit le CSV pour de vrai (format libre, Etsy ou Shopify simplifiés) mais garde les ventes en mémoire. */
    const imported = new Set();
    function parseCsv(text) {
      const lines = text.replace(/\r/g, "").split("\n").filter((l) => l.trim());
      const sep = (lines[0].match(/;/g) || []).length > (lines[0].match(/,/g) || []).length ? ";" : ",";
      const head = lines[0].split(sep).map((x) => x.trim().replace(/^"|"$/g, ""));
      const rows = lines.slice(1).map((l) => { const c = l.split(sep).map((x) => x.trim().replace(/^"|"$/g, "")); return Object.fromEntries(head.map((h, i) => [h, c[i] || ""])); });
      return { head, rows };
    }
    const money = (v) => Number(String(v || "").replace(/[^\d,.-]/g, "").replace(/\.(?=\d{3}\b)/g, "").replace(",", ".")) || 0;
    async function readSales(src, opt) {
      const b64 = String(src && src.data || "").split(",")[1] || "";
      let text = "";
      try { text = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))); } catch (e) { return null; }
      const { head, rows } = parseCsv(text);
      const find = (...names) => head.find((h) => names.includes(h.toLowerCase()));
      const etsy = head.includes("Order ID") && head.includes("Sale Date"), shopify = head.includes("Financial Status");
      const fmt = etsy ? "etsy" : shopify ? "shopify" : "generic", source = etsy ? "etsy" : shopify ? "shopify" : (opt.source || "vinted");
      const cols = fmt === "generic" ? Object.assign({ date: find("date", "date de vente"), title: find("article", "titre", "title", "item"), price: find("prix", "price", "montant", "total"), client: find("acheteur", "buyer", "client"), shipped: find("statut", "status", "expédié") }, opt.columns || {}) : {};
      const sales = rows.map((r, i) => {
        const ref = etsy ? r["Order ID"] : shopify ? r.Name : `${r[cols.date] || i}-${r[cols.title] || ""}`;
        const st = String(r[cols.shipped] || "").toLowerCase();
        return { source, ref: `${source}:${ref}`, date: (etsy ? r["Sale Date"] : shopify ? r["Created at"] : r[cols.date] || "").slice(0, 10),
          title: etsy ? `Commande Etsy ${ref}` : shopify ? r["Lineitem name"] || `Commande ${ref}` : r[cols.title] || "Vente",
          client: etsy ? r["Full Name"] : shopify ? r["Billing Name"] : r[cols.client] || "", email: shopify ? r.Email : "",
          ttc: money(etsy ? r["Order Total"] : shopify ? r.Total : r[cols.price]), shipped: etsy ? !!r["Date Shipped"] : shopify ? r["Fulfillment Status"] === "fulfilled" : !cols.shipped || !/attente|non|pending/.test(st), items: 1 };
      }).filter((x) => x.ttc > 0 || x.title);
      return { fmt, source, cols, head, sales };
    }

    return {
      async planning_get() { await ctx.wait(120); return need("planning", "Planning") || planPayload(); },
      async planning_task_save(id, d) {
        const t = (db.tasks || []).find((x) => String(x.id) === String(id));
        if (!t) return { ok: false, msg: "Impression introuvable." };
        d = d || {};
        if ("est_hours" in d) t.est_hours = Math.max(0, Number(d.est_hours) || 0);
        if ("printer_id" in d) t.printer_id = d.printer_id === "" ? null : Number(d.printer_id);
        if ("due_on" in d) t.due_on = d.due_on || null;
        if ("order_id" in d) t.order_id = d.order_id === "" ? null : Number(d.order_id);
        return planPayload("Planning mis à jour.");
      },
      async planning_move(id, delta) {
        const list = todoSorted(), i = list.findIndex((t) => String(t.id) === String(id)), j = Math.max(0, Math.min(list.length - 1, i + (delta > 0 ? 1 : -1)));
        if (i < 0) return { ok: false, msg: "Impression introuvable dans la file." };
        [list[i], list[j]] = [list[j], list[i]];
        list.forEach((t, k) => { t.position = k + 1; });
        return planPayload();
      },
      async planning_sort_due() {
        todoSorted().sort((a, b) => String(a.due_on || "9999").localeCompare(String(b.due_on || "9999"))).forEach((t, k) => { t.position = k + 1; });
        return planPayload("File rangée par date limite.");
      },
      async planning_night_save(n) { s.planning = { night: Object.assign({ on: true, start: "22:00", end: "07:00" }, n || {}) }; return planPayload("Plage de nuit enregistrée."); },
      async clients_list() { await ctx.wait(120); return need("clients", "Carnet de clients") || clientsPayload(); },
      async clients_suggest() { return ctx.ok({ clients: on("clients") ? saved.map((c) => ({ name: c.name, company: c.company, email: c.email, address: c.address })) : [] }); },
      async client_save(d) {
        d = d || {};
        if (!String(d.name || "").trim()) return { ok: false, msg: "Donnez un nom au client." };
        const row = d.id ? saved.find((c) => String(c.id) === String(d.id)) : null;
        const values = { name: d.name, company: d.company || "", email: d.email || "", phone: d.phone || "", address: d.address || "", siren: d.siren || "", vat_number: d.vat_number || "", notes: d.notes || "", updated_at: ctx.now() };
        if (row) Object.assign(row, values); else saved.push(Object.assign({ id: ctx.newId(), source: "" }, values));
        return clientsPayload(`Fiche de ${d.name} enregistrée.`);
      },
      async client_delete(id) { const i = saved.findIndex((c) => String(c.id) === String(id)); if (i >= 0) saved.splice(i, 1); return clientsPayload("Fiche supprimée (les devis, commandes et factures ne changent pas)."); },
      async pro_dashboard() {
        await ctx.wait(150);
        if (!on("quotes") && !on("orders") && !on("invoices")) return { ok: false, msg: "Activez les devis, les commandes ou les factures dans Réglages › Fonctions (mode Pro)." };
        const orders = on("orders") ? (((await ctx.api.orders_list()).orders) || []).filter((o) => o.mine) : [];
        const quotes = on("quotes") ? (((await ctx.api.quotes_list()).quotes) || []) : [];
        const months = [...Array(12)].map((_, i) => { const d = new Date(); d.setDate(15); d.setMonth(d.getMonth() - 11 + i); return d.toISOString().slice(0, 7); });
        // Historique d'exemple (la démo n'a que quelques commandes récentes).
        const base = ctx.opts.empty ? [] : [120, 95, 210, 160, 0, 240, 185, 310, 260, 205, 330, 0];
        const by = Object.fromEntries(months.map((m, i) => [m, base[i] || 0]));
        orders.filter((o) => o.status === "paid" && o.paid_at).forEach((o) => { const k = String(o.paid_at).slice(0, 7); if (k in by) by[k] = r2(by[k] + o.price_ht); });
        const open = orders.filter((o) => ["todo", "printing", "done"].includes(o.status)), late = open.filter((o) => o.late);
        const soon = open.filter((o) => o.due_on && o.due_on >= day(0) && o.due_on <= day(7));
        const sent = quotes.filter((q) => q.status === "sent"), decided = quotes.filter((q) => ["accepted", "refused"].includes(q.status));
        const year = r2(Object.entries(by).filter(([k]) => k.startsWith(String(new Date().getFullYear()))).reduce((a, [, v]) => a + v, 0));
        const srcs = {};
        orders.forEach((o) => { const k = o.source || ""; srcs[k] = srcs[k] || { label: { "": "Direct", etsy: "Etsy", shopify: "Shopify", vinted: "Vinted" }[k] || k, count: 0, ht: 0 }; srcs[k].count += 1; srcs[k].ht = r2(srcs[k].ht + o.price_ht); });
        const plan = on("planning") ? await planPayload() : null;
        const items = plan ? plan.lanes.flatMap((l) => l.items) : [];
        return ctx.ok({ today: day(0), features: { quotes: on("quotes"), orders: on("orders"), invoices: on("invoices"), clients: on("clients"), planning: on("planning"), urssaf: on("urssaf") },
          revenue: { month: by[months[11]], last_month: by[months[10]], year, last_year_same: 0, ceiling_pct: r2(year / 2031), months: months.map((m) => ({ key: m, ht: by[m] })) },
          orders: { open: open.length, late: late.length, open_value: r2(open.reduce((a, o) => a + o.price_ht, 0)),
            due_soon: soon.map((o) => ({ id: o.id, title: o.title, client: (o.client || {}).name, due_on: o.due_on, status: o.status })),
            late_list: late.map((o) => ({ id: o.id, title: o.title, client: (o.client || {}).name, due_on: o.due_on })), margin_pct: 62.4,
            sources: Object.values(srcs).sort((a, b) => b.count - a.count) },
          quotes: { pending: sent.length, pending_value: r2(sent.reduce((a, q) => a + ((q.totals || {}).ht || 0), 0)), conversion: decided.length ? Math.round(decided.filter((q) => q.status === "accepted").length / decided.length * 100) : null, decided: decided.length },
          invoices: { unpaid: 0, overdue: 0, unpaid_ttc: 0, overdue_list: [] },
          clients: on("clients") ? (await clientsPayload()).summary : undefined,
          planning: plan ? { count: items.length, hours: r2(items.reduce((a, i) => a + i.hours, 0)), end: plan.end, late: plan.late, printers: plan.lanes.length } : undefined });
      },
      async orders_import_preview(src, opt) {
        if (need("orders", "Commandes clients")) return need("orders", "Commandes clients");
        const r = await readSales(src, opt || {});
        if (!r || !r.sales.length) return { ok: false, msg: "Aucune vente reconnue dans ce fichier." };
        const fresh = r.sales.filter((x) => !imported.has(x.ref));
        return ctx.ok({ format: r.fmt, source: r.source, columns: r.cols, head: r.head, count: fresh.length, duplicates: r.sales.length - fresh.length,
          total_ttc: r2(fresh.reduce((a, x) => a + x.ttc, 0)), sales: fresh.slice(0, 80), truncated: false, vat_mode: "franchise" });
      },
      async orders_import(src, opt) {
        const r = await readSales(src, opt || {});
        const fresh = r ? r.sales.filter((x) => !imported.has(x.ref)) : [];
        if (!fresh.length) return { ok: false, msg: "Rien de nouveau : toutes ces ventes sont déjà importées." };
        for (const x of fresh) {
          imported.add(x.ref);
          await ctx.api.order_save({ title: x.title, client_name: x.client || "Vinted", client_email: x.email, price_ht: x.ttc, notes: `Importée depuis ${x.source}` });
          const list = ((await ctx.api.orders_list()).orders || []);
          const o = list.find((y) => y.title === x.title && y.mine);
          if (o) { if (x.shipped) await ctx.api.order_status(o.id, "paid"); }
        }
        const res = await ctx.api.orders_list();
        return Object.assign(res, { msg: `${fresh.length} vente(s) importée(s).` });
      },
    };
  });
})();

/* ---- 4.0 : Conception (calcul géométrique en Python seulement : la démo l'explique) --------------------- */
(function () {
  "use strict";
  if (!window.G3D_FAKE) return;
  G3D_FAKE.extend(() => {
    const MSG = "Dans la démo, les outils de conception ne calculent rien : ils fonctionnent dans Graou3D installé sur le PC.";
    const no = async () => ({ ok: false, msg: MSG });
    return {
      async design_info() { return { ok: true, available: false, msg: MSG }; },
      design_pick_file: no,
      design_open: no,
      design_view: no,
      design_extract: no,
      design_job: no,
      design_auto_cuts: no,
      design_cut: no,
      design_study: no,
      design_logo: no,
      design_relief: no,
      design_texture: no,
      design_organizer_analyze: no,
      design_photo_sheet: no,
      design_photo_analyze: no,
      design_photo_measure: no,
      design_photo_build: no,
      design_photo_scad: no,
      scan_info: no,
      scan_list: no,
      scan_new: no,
      scan_status: no,
      scan_thumbs: no,
      scan_rename: no,
      scan_delete: no,
      scan_add_files: no,
      scan_add_photo: no,
      scan_remove_photo: no,
      scan_receive: no,
      scan_receive_stop: no,
      scan_start: no,
      scan_cancel: no,
      scan_open: no,
      scan_export_stl: no,
      scan_other_face: no,
      scan_merge: no,
      scan_open_folder: no,
      scan_ai_status: no,
      scan_ai_install: no,
      scan_ai_cancel: no,
      scan_ai_enable: no,
      scan_ai_remove: no,
      scan_phone_find: no,
      scan_phone_pair: no,
      scan_phone_send: no,
      scan_phone_done: no,
      scan_phone_status: no,
      design_organizer_build: no,
      design_generate: no,
      design_coupon: no,
      design_paint_spools: no,
      design_paint: no,
      design_paint_update: no,
      design_fit_record: no,
      design_fit_forget: no,
      design_materials: no,
      design_advise: no,
      design_save: no,
      design_to_slicer: no,
      design_diagnose: no,
      design_repair: no,
      design_scale: no,
      design_parametric: no,
      design_scad_open: no,
      design_scad_build: no,
      design_measure: no,
      design_calc: no,
      design_study_report: no,
      design_open_folder: no,
    };
  });
})();

/* ---- 4.3 : devis depuis un STL (même calcul que backend/estimate.py) ------------------------------------- */
(function () {
  "use strict";
  if (!window.G3D_FAKE) return;
  G3D_FAKE.extend((ctx) => {
    const MATERIALS = [["pla", "PLA", 1.24], ["pla_matte", "PLA mat", 1.32], ["petg", "PETG", 1.27], ["abs", "ABS", 1.04], ["asa", "ASA", 1.07], ["tpu95", "TPU 95A", 1.21]]
      .map(([key, name, density]) => ({ key, name, density }));
    function triangles(bytes) {
      const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      const n = bytes.length >= 84 ? dv.getUint32(80, true) : 0;
      const out = [];
      if (n && 84 + n * 50 === bytes.length) {
        for (let i = 0; i < n; i++) {
          const o = 84 + i * 50 + 12, t = [];
          for (let k = 0; k < 9; k++) t.push(dv.getFloat32(o + k * 4, true));
          out.push(t);
        }
        return out;
      }
      const text = new TextDecoder().decode(bytes), nums = [];
      text.replace(/vertex\s+(\S+)\s+(\S+)\s+(\S+)/g, (_, x, y, z) => { nums.push(+x, +y, +z); return ""; });
      for (let i = 0; i + 8 < nums.length; i += 9) out.push(nums.slice(i, i + 9));
      return out;
    }
    function measure(tris) {
      let vol = 0, area = 0;
      const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
      for (const [ax, ay, az, bx, by, bz, cx, cy, cz] of tris) {
        vol += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6;
        const ux = bx - ax, uy = by - ay, uz = bz - az, vx = cx - ax, vy = cy - ay, vz = cz - az;
        area += Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 2;
        [[ax, ay, az], [bx, by, bz], [cx, cy, cz]].forEach((p) => p.forEach((v, k) => { lo[k] = Math.min(lo[k], v); hi[k] = Math.max(hi[k], v); }));
      }
      return { volume: Math.abs(vol), area, size: hi.map((v, k) => Math.round((v - lo[k]) * 10) / 10) };
    }
    return {
      async quote_measure_file(source) {
        const name = String((source && source.name) || "");
        if (!/\.stl$/i.test(name)) return { ok: false, msg: "Dans la démo, seuls les fichiers .stl sont mesurés (le .3mf et le .obj le sont dans Graou3D installé)." };
        const raw = atob(String(source.data || "").split(",").pop());
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
        const m = measure(triangles(bytes));
        if (m.volume < 1) return { ok: false, msg: "Ce modèle n'a pas de volume mesurable (surface ouverte ?)." };
        return { ok: true, measure: { volume: m.volume, area: m.area }, size: m.size, name: name.replace(/\.[^.]+$/, ""), materials: MATERIALS };
      },
      async quote_estimate(meas, opts) {
        const o = Object.assign({ material: "pla", infill: 15, walls: 2, supports: false, qty: 1 }, opts || {});
        const mat = MATERIALS.find((m) => m.key === o.material) || MATERIALS[0];
        const volume = Number(meas && meas.volume) || 0, area = Number(meas && meas.area) || 0;
        const shell = Math.min(volume, area * Math.max(o.walls * 0.42, 0.8));
        let plastic = shell + (volume - shell) * o.infill / 100;
        if (o.supports) plastic *= 1.15;
        const grams = plastic / 1000 * mat.density;
        const log = (ctx.db.settings.print_log || []).filter((e) => e && e.success !== false && e.grams >= 5 && e.hours >= 0.2).map((e) => e.grams / e.hours).sort((a, b) => a - b);
        const personal = log.length >= 5, rate = personal ? Math.round(log[Math.floor(log.length / 2)] * 10) / 10 : 12;
        return { ok: true, estimate: { grams: Math.round(grams * 10) / 10, hours: grams > 0 ? Math.round(Math.max(0.15, grams / rate) * 100) / 100 : 0, rate, personal,
          density: mat.density, volume_cm3: volume / 1000, plastic_cm3: Math.round(plastic / 10) / 100, options: o } };
      },
    };
  });
})();

/* ---- 5.0 : Graoully, l'assistant. La démo n'embarque pas la bibliothèque (11 Mo) : quelques réponses
   toutes faites, tirées des fiches Graoully, pour montrer la discussion, les sources et les réglages. -------- */
(function () {
  "use strict";
  if (!window.G3D_FAKE) return;
  G3D_FAKE.extend(() => {
    const FICHE = "https://github.com/SoftFever/OrcaSlicer/tree/main/resources/profiles";
    const ANSWERS = [
      { rx: /fil|string|suint/i, facts: ["Votre bobine PETG HF Bleu cobalt : 910 g restants, ouverte depuis 32 jours, à sécher."],
        text: "Les fils viennent le plus souvent d'un filament qui a pris l'humidité [1] : séchez d'abord votre bobine de PETG HF, ouverte depuis un mois [Graou3D].\n\n- Séchez le PETG 4 à 6 h vers 65 °C [2]\n- Baissez la buse de 5 °C par essai [1]\n- Augmentez un peu la longueur de rétraction, puis lancez une tour de test [1]",
        sources: [{ n: 1, titre: "Fils et suintement (stringing, oozing)", section: "Remèdes", url: "" },
          { n: 2, titre: "Séchage et stockage des filaments", section: "Températures de séchage", url: "" }] },
      { rx: /premi[eè]re couche|colle|adh[eé]/i, facts: [],
        text: "Une première couche qui ne colle pas vient presque toujours du plateau [1] : nettoyez-le à l'eau chaude et au liquide vaisselle (pas seulement à l'alcool), puis vérifiez le décalage Z.\n\n- Première couche plus lente et un peu plus chaude [1]\n- Plateau à la bonne température pour la matière [2]\n- Bordure (brim) pour les petites pièces [1]",
        sources: [{ n: 1, titre: "Première couche ratée", section: "Remèdes", url: "" },
          { n: 2, titre: "Matériau PLA : réglages typiques (synthèse de 2961 profils)", section: "Matériau PLA", url: FICHE }] },
      { rx: /prochain|suivant|file/i, facts: ["Prochaine impression de la file : « Support casque », PETG HF, 62 g (5 dans la file).",
        "Coach : la bobine de PETG est ouverte depuis longtemps, la sécher avant (fils, bulles).",
        "Coach : l'imprimante est occupée ; la prochaine impression attendra la fin de celle en cours."],
        text: "Votre prochaine impression est « Support casque » en PETG HF, environ 62 g [Graou3D]. Avant de la lancer, séchez la bobine de PETG (ouverte depuis un mois) et attendez la fin de l'engrenage en cours [Graou3D].",
        sources: [] },
    ];
    let settings = { mode: "library", api: { provider: "mistral", base_url: "", model: "" }, offer_seen: true, head: {} };
    let hasKey = false;
    const jobs = {};
    const PROVIDERS = {
      mistral: { label: "Mistral AI", base_url: "https://api.mistral.ai/v1", model: "mistral-small-latest", key_url: "https://console.mistral.ai/api-keys", note: "Entreprise française, serveurs en Europe. Un accès d'essai gratuit (limité) existe." },
      openai: { label: "OpenAI (ChatGPT)", base_url: "https://api.openai.com/v1", model: "gpt-5-mini", key_url: "https://platform.openai.com/api-keys", note: "La clé API se crée à part de l'abonnement ChatGPT." },
      anthropic: { label: "Anthropic (Claude)", base_url: "https://api.anthropic.com", model: "claude-haiku-5-5", key_url: "https://console.anthropic.com/settings/keys", note: "" },
      openrouter: { label: "OpenRouter", base_url: "https://openrouter.ai/api/v1", model: "mistralai/mistral-small-3.2-24b-instruct", key_url: "https://openrouter.ai/keys", note: "Une seule clé pour des centaines de modèles, dont certains gratuits (suffixe « :free »)." },
      ollama: { label: "Ollama (sur ce PC ou le réseau)", base_url: "http://127.0.0.1:11434/v1", model: "qwen3:4b", key_url: "https://ollama.com/download", no_key: true, note: "Si vous utilisez déjà Ollama : pas de clé, le modèle tourne chez vous." },
      custom: { label: "Autre service (à votre adresse)", base_url: "", model: "", key_url: "", note: "LM Studio, Groq, DeepSeek, un serveur à vous…" },
    };
    const local = { installed: false, server: false, model: "", model_key: "", model_gb: 0, variant: "", running: false, install: { running: false },
      windows: true, catalog: [
        { key: "ministral3-3b", label: "Ministral 3 · 3B (Mistral AI)", approx_gb: 2.2, ram_gb: 8, licence: "Apache 2.0", recommended: true, note: "Français natif, le plus léger. Conseillé." },
        { key: "qwen35-4b", label: "Qwen 3.5 · 4B (Alibaba)", approx_gb: 2.8, ram_gb: 8, licence: "Apache 2.0", recommended: false, note: "Un peu plus fort en raisonnement, un peu plus lourd." }] };
    const local3d = { installed: false, quality: "standard", files: [], gb: 0, detail: "normal", details: ["rapide", "normal", "fin"],
      install: { running: false }, sizes: { standard: 584, max: 1768 } };
    const DEMO = "Dans la démo, Graoully ne télécharge rien : son cerveau local s'installe dans Graou3D sur le PC.";
    return {
      async graoully_info() {
        return { ok: true, settings, has_key: hasKey, providers: PROVIDERS, local, local3d, offer_install: false, has_hf: false,
          engines: [{ key: "trellis", label: "TRELLIS (Microsoft)", note: "" }, { key: "hunyuan", label: "Hunyuan3D (Tencent)", note: "" },
            { key: "triposg", label: "TripoSG (VAST)", note: "" }],
          creators: [{ id: "free3d:trellis", kind: "organic", label: "TRELLIS (Microsoft) · Hugging Face, gratuit" },
            { id: "free3d:hunyuan", kind: "organic", label: "Hunyuan3D (Tencent) · Hugging Face, gratuit" },
            { id: "free3d:triposg", kind: "organic", label: "TripoSG (VAST) · Hugging Face, gratuit" }]
            .concat(hasKey ? [{ id: "ai:" + settings.api.provider, kind: "exact",
              label: `${(PROVIDERS[settings.api.provider] || {}).label || settings.api.provider} · ${settings.api.model || (PROVIDERS[settings.api.provider] || {}).model || ""}` }] : []),
          ai_label: `${(PROVIDERS[settings.api.provider] || {}).label || settings.api.provider} · ${settings.api.model || (PROVIDERS[settings.api.provider] || {}).model || ""}`,
          hints: { key: hasKey ? "…demo" : "", hf: "", meshy: "" },
          library: { chunks: 10074, sources: 733, ready: true, available: true } };
      },
      async graoully_settings(changes) {
        if (changes && changes.mode) settings.mode = changes.mode;
        if (changes && changes.api) settings.api = Object.assign({}, settings.api, changes.api);
        if (changes && changes.head) settings.head = changes.head;
        if (changes && changes.engine) settings.engine = changes.engine;
        return { ok: true, settings };
      },
      async graoully_key(key) { hasKey = !!key; return { ok: true, has_key: hasKey, msg: hasKey ? "Clé enregistrée sur ce PC." : "Clé oubliée." }; },
      async graoully_test() { return { ok: false, msg: "Dans la démo, Graoully ne contacte aucun service en ligne." }; },
      async graoully_ask(q) {
        const id = "d" + Date.now();
        const a = ANSWERS.find((x) => x.rx.test(q));
        jobs[id] = { t: Date.now(), a, q };
        return { ok: true, job: id };
      },
      async graoully_job(id) {
        const j = jobs[id];
        if (!j) return { ok: false, msg: "Question introuvable." };
        const mode = settings.mode, a = j.a, el = Date.now() - j.t;
        if (j.create) {        // 5.6 : la démo montre la barre de progression, puis explique qu'elle ne crée rien
          const sec = Math.floor(el / 1000), pct = Math.min(95, Math.round(el / 80));
          if (el > 7000) return { ok: true, state: "error", create: "organic", mode: "free3d", error: "Dans la démo, Graoully ne crée pas de pièce : il faut Graou3D installé. Ici, seule la barre de progression est montrée." };
          return { ok: true, state: "running", step: "free3d", create: "organic", mode: "free3d", progress: pct, elapsed: sec, engine: "TRELLIS (Microsoft)",
            substep: pct < 30 ? "dessin d'une image d'après votre description" : "création de la forme (file d'attente partagée)",
            queue: pct < 30 ? "" : pct < 55 ? "file d'attente : 2e sur 4, environ 20 s" : "calcul en cours" };
        }
        if (!a) return { ok: true, state: "done", mode, text: "Dans la démo, je ne connais que quelques questions (fils, première couche, prochaine impression). Dans Graou3D installé, je cherche dans toute ma bibliothèque.", sources: [], facts: [], passages: [], further: [] };
        if (mode === "library") {
          return { ok: true, state: "done", mode, text: "", facts: a.facts, further: [],
            passages: a.sources.map((s) => ({ n: s.n, titre: s.titre, section: s.section, url: s.url, texte: a.text.split("\n\n")[0].replace(/\s*\[[^\]]+\]/g, "") })) };
        }
        const n = Math.min(a.text.length, Math.max(0, Math.floor((el - 600) / 12)));
        const done = n >= a.text.length;
        return { ok: true, state: done ? "done" : "running", step: n ? "write" : "think", mode, text: a.text.slice(0, n),
          sources: a.sources, facts: a.facts, passages: [], further: [] };
      },
      async graoully_cancel() { return { ok: true }; },
      async graoully_create(q) {
        const id = "c" + Date.now();
        jobs[id] = { t: Date.now(), create: true, q };
        return { ok: true, job: id };
      },
      async graoully_hf_token() { return { ok: false, msg: "Dans la démo, aucun jeton n'est enregistré." }; },
      async graoully_hf_test() { return { ok: true, msg: "Dans la démo, rien n'est testé : le générateur 3D gratuit répond dans l'application." }; },
      async graoully_meshy_key() { return { ok: false, msg: "Dans la démo, aucune clé n'est enregistrée." }; },
      async graoully_act() { return { ok: false, msg: "Dans la démo, Graoully ne modifie rien." }; },
      async graoully_undo() { return { ok: false, msg: "Dans la démo, Graoully ne modifie rien." }; },
      async graoully_release() { return { ok: true, reservations: {} }; },
      async graoully_attach() { return { ok: false, msg: "Dans la démo, Graoully ne lit pas de fichier : joignez vos photos et fichiers 3D dans Graou3D installé." }; },
      async graoully_local_install() { return { ok: false, msg: DEMO }; },
      async graoully_local_status() { return { ok: true, local }; },
      async graoully_local_cancel() { return { ok: true }; },
      async graoully_local_pick() { return { ok: false, msg: DEMO }; },
      async graoully_local_remove() { return { ok: true, local }; },
      async graoully_local3d_install() { return { ok: false, msg: "Dans la démo, rien n'est téléchargé : le générateur 3D s'installe dans Graou3D sur le PC." }; },
      async graoully_local3d_status() { return { ok: true, local3d }; },
      async graoully_local3d_cancel() { return { ok: true }; },
      async graoully_local3d_detail(d) { local3d.detail = d; return { ok: true, local3d }; },
      async graoully_local3d_remove() { return { ok: true, local3d }; },
      async graoully_offer_seen() { return { ok: true }; },
    };
  });

  /* ---- Catalogue de produits et recettes d'impression (5.5) : même logique que backend/catalog.py ---- */
  G3D_FAKE.extend((ctx) => {
    const { db } = ctx, o = ctx.opts, s = db.settings;
    const on = () => !!(s.features.enterprise && s.features.catalog);
    const products = o.empty ? [] : [
      { id: 901, name: "Porte-clés Graoully", sku: "PC-GR", price_ttc: 6, material: "PLA Basic", color: "Vert", grams: 9, minutes: 95, file_name: "porte-cles-graoully.gcode.3mf", stock: 3, min_stock: 10, batch: 12, notes: "Sachet kraft + carte.", active: true },
      { id: 902, name: "Support casque", sku: "SC-01", price_ttc: 18, material: "PETG HF", color: "Noir", grams: 66, minutes: 210, file_name: "support-casque.gcode.3mf", stock: 5, min_stock: 2, batch: 1, notes: "", active: true },
      { id: 903, name: "Lampe lithophanie Metz", sku: "", price_ttc: 39, material: "PLA Basic", color: "Blanc", grams: 140, minutes: 420, file_name: "", stock: 0, min_stock: 0, batch: 1, notes: "Sur commande seulement.", active: true },
    ];
    const recipes = o.empty ? [] : [
      { id: 951, name: "Porte-clés Graoully", file_name: "porte-cles-graoully.gcode.3mf", plate: 1, material: "PLA Basic", color_hex: "#3FA34D", spool: "Bambu PLA Basic Vert", nozzle: "0.4mm Inox", bed: "Plateau texturé", nozzle_temp: 220, bed_temp: 55, grams: 108, hours: 1.6, notes: "Pas de colle, bords ralentis.", journal_id: "", uses: 4, last_used_at: ctx.ago(2 * 1440) },
    ];
    const tag = (id) => `[cat#${id}]`;
    const LEGAL = [["cgu", "Conditions d'utilisation et de vente"], ["confidentialite", "Politique de confidentialité"], ["mentions", "Mentions légales"], ["sous_traitance", "Accord de sous-traitance (Pro)"]];
    const queued = (p) => (db.tasks || []).filter((t) => ["todo", "printing"].includes(t.status) && String(t.title).includes(tag(p.id)))
      .reduce((a, t) => a + (Number((String(t.title).match(new RegExp(`×\\s*(\\d+)\\s*\\[cat#${p.id}\\]`)) || [])[1]) || p.batch || 1), 0);
    const decorate = (p) => {
      const q = queued(p), need = p.min_stock - p.stock - q, plates = need > 0 ? Math.ceil(need / Math.max(1, p.batch)) : 0;
      return Object.assign({}, p, { queued: q, low: p.active && p.min_stock > 0 && p.stock <= p.min_stock, restock: { plates, pieces: plates * p.batch }, value: Math.round(p.price_ttc * p.stock * 100) / 100 });
    };
    const catalog = (msg) => {
      if (!on()) return { ok: false, msg: "Activez d'abord « Catalogue et stock » dans Réglages › Fonctions (mode entreprise)." };
      const items = products.slice().sort((a, b) => a.name.localeCompare(b.name)).map(decorate), act = items.filter((p) => p.active);
      return ctx.ok({ products: items, materials: window.G3D_FAKE.MATERIALS, msg,
        summary: { count: act.length, low: act.filter((p) => p.low).length, pieces: act.reduce((a, p) => a + p.stock, 0), value: Math.round(act.reduce((a, p) => a + p.value, 0) * 100) / 100 } });
    };
    const int = (v, lo, hi, d) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : d; };
    const findP = (id) => products.find((p) => String(p.id) === String(id));
    const findR = (id) => recipes.find((r) => String(r.id) === String(id));
    const book = (msg) => ctx.ok({ recipes: recipes.slice().sort((a, b) => String(b.last_used_at || "").localeCompare(String(a.last_used_at || ""))), msg });
    return {
      async catalog_list() { await ctx.wait(120); return catalog(); },
      async catalog_save(d) {
        d = d || {};
        if (!String(d.name || "").trim()) return { ok: false, msg: "Donnez un nom à l'article." };
        const v = { name: String(d.name).trim().slice(0, 120), sku: String(d.sku || "").slice(0, 40), price_ttc: Math.max(0, Number(d.price_ttc) || 0), material: d.material || "", color: d.color || "",
          grams: Math.max(0, Number(d.grams) || 0), minutes: int(d.minutes, 0, 100000, 0), file_name: d.file_name || "", stock: int(d.stock, 0, 1e6, 0), min_stock: int(d.min_stock, 0, 1e6, 0),
          batch: int(d.batch, 1, 500, 1), notes: d.notes || "", active: d.active !== false };
        const p = d.id ? findP(d.id) : null;
        if (p) Object.assign(p, v); else products.push(Object.assign({ id: ctx.newId() }, v));
        return catalog(p ? "Article enregistré." : `« ${v.name} » ajouté au catalogue.`);
      },
      async catalog_delete(id) { const i = products.findIndex((p) => String(p.id) === String(id)); if (i >= 0) products.splice(i, 1); return catalog("Article retiré du catalogue."); },
      async catalog_stock(id, delta) {
        const p = findP(id);
        if (!p) return { ok: false, msg: "Introuvable (supprimé entre-temps ?)." };
        delta = int(delta, -100000, 100000, 0);
        p.stock = Math.max(0, p.stock + delta);
        return catalog(`${Math.abs(delta)} pièce(s) ${delta >= 0 ? "ajoutée(s) au" : "retirée(s) du"} stock de « ${p.name} » : ${p.stock} en stock.`);
      },
      async catalog_restock(id, plates) {
        const p = findP(id);
        if (!p) return { ok: false, msg: "Introuvable (supprimé entre-temps ?)." };
        plates = int(plates, 1, 50, 1);
        for (let k = 0; k < plates; k++) {
          await ctx.api.task_add({ title: `${String(p.name).slice(0, 80).trim()} × ${p.batch} ${tag(p.id)}`, material: p.material || "PLA Basic", estimated_grams: Math.round(p.grams * p.batch) });
        }
        return catalog(`${plates} plateau(x) de « ${p.name} » ajouté(s) à la file d'attente (${plates * p.batch} pièces).`);
      },
      // Textes juridiques (5.5) : dans la démo, un résumé ; les textes complets sont livrés avec Graou3D installé.
      async legal_index() { return ctx.ok({ docs: LEGAL.map(([key, title]) => ({ key, title, version: "1.0" })), current: "cgu 1.0 · confidentialite 1.0" }); },
      async legal_text(key) {
        const d = LEGAL.find((x) => x[0] === key);
        if (!d) return { ok: false, msg: "Texte inconnu." };
        return ctx.ok({ doc: { key, title: d[1], version: "1.0", source: "app",
          body: `# ${d[1]}\n\nDans la démo, ce texte n'est pas affiché en entier : vous le trouverez dans Graou3D installé (Réglages › À propos, ou le bouton Aide).\n\n> Aucune donnée de la démo ne quitte votre navigateur : rien n'est enregistré ni envoyé.` } });
      },
      async legal_status() { return ctx.ok({ legal: { current: "cgu 1.0 · confidentialite 1.0", accepted: "cgu 1.0 · confidentialite 1.0", accepted_at: "", needed: false, first: false } }); },
      async legal_accept() { return ctx.ok({ legal: { current: "cgu 1.0 · confidentialite 1.0", accepted: "cgu 1.0 · confidentialite 1.0", accepted_at: ctx.now(), needed: false, first: false } }); },
      async print_recipes_list() { await ctx.wait(100); return book(); },
      async print_recipe_save(d) {
        d = d || {};
        if (!String(d.name || "").trim()) return { ok: false, msg: "Donnez un nom à la recette." };
        const v = { name: String(d.name).trim().slice(0, 120), file_name: d.file_name || "", plate: int(d.plate, 1, 64, 1), material: d.material || "", color_hex: d.color_hex || "",
          spool: d.spool || "", nozzle: d.nozzle || "", bed: d.bed || "", nozzle_temp: int(d.nozzle_temp, 0, 500, 0), bed_temp: int(d.bed_temp, 0, 150, 0),
          grams: Math.max(0, Number(d.grams) || 0), hours: Math.max(0, Number(d.hours) || 0), notes: d.notes || "", journal_id: d.journal_id || "" };
        const r = d.id ? findR(d.id) : null;
        if (r) Object.assign(r, v); else recipes.push(Object.assign({ id: ctx.newId(), uses: 0, last_used_at: null }, v));
        return book(r ? "Recette enregistrée." : `Recette « ${v.name} » enregistrée.`);
      },
      async print_recipe_from_journal(id) {
        const e = (s.print_log || []).find((x) => x.id === id);
        if (!e) return { ok: false, msg: "Impression introuvable dans le journal." };
        if (e.success === false) return { ok: false, msg: "Cette impression a échoué : enregistrez plutôt une impression réussie." };
        return ctx.ok({ recipe: { name: String(e.file || "Impression").replace(/\.(gcode\.3mf|3mf|gcode)$/i, ""), file_name: e.file || "", plate: 1, material: e.material || "",
          color_hex: e.color_hex || "", spool: e.spool || "", nozzle: s.active_nozzle || "", grams: e.grams || 0, hours: e.hours || 0, journal_id: e.id } });
      },
      async print_recipe_delete(id) { const i = recipes.findIndex((r) => String(r.id) === String(id)); if (i >= 0) recipes.splice(i, 1); return book("Recette supprimée."); },
      async print_recipe_print(id) {
        const r = findR(id);
        if (!r) return { ok: false, msg: "Introuvable (supprimé entre-temps ?)." };
        r.uses += 1;
        r.last_used_at = ctx.now();
        return book(`« ${r.name} » relancée (démo : l'imprimante fictive ne bouge pas).`);
      },
    };
  });
})();
