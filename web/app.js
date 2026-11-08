
(() => {
  "use strict";
  const API = new URLSearchParams(location.search).get("api") || "";
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (id) => document.getElementById(id);

  /* ── stages (mirror of runner._STAGE_LABELS, pipeline order) ──── */
  const ACQUIRE = "fetching city data";
  const STAGES = [ACQUIRE, "loading boundary", "loading existing facilities",
    "loading road graph", "generating candidate sites", "applying hard constraints",
    "scoring candidates", "joint MCLP selection", "accessibility before/after",
    "2SFCA index", "computing isochrones"];

  const PRESETS = {
    testville:  { center: [49.44, 8.74],  zoom: 12 },
    freiburg:   { center: [47.995, 7.85], zoom: 12 },
    heidelberg: { center: [49.40, 8.68],  zoom: 13 },
    mysuru:     { center: [12.30, 76.65], zoom: 13 },
  };

  /* state */
  const state = {
    city: "testville", threshold: 900, numSites: 2,
    jobId: null, lastBody: null, pollTimer: null,
    layers: { "iso-base": null, "iso-joint": null, sites: null, cands: null },
  };

  /*helpers*/
  const fmt = new Intl.NumberFormat("en-US");
  function toast(msg, isErr) {
    const t = document.createElement("div");
    t.className = "toast" + (isErr ? " err" : "");
    t.textContent = msg.trim();
    $("toasts").append(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 4200);
    setTimeout(() => t.remove(), 4700);
  }
  function show(view) {
    for (const v of ["form", "running", "result", "failed"])
      $("view-" + v).classList.toggle("hidden", v !== view);
  }
  
  function tickTo(node, value, decimals = 0, dur = 1100, delay = 0) {
    const f = (n) => n.toLocaleString("en-US",
      { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    if (REDUCED) { node.textContent = f(value); return; }
    let t0 = null;
    const step = (now) => {
      if (t0 === null) t0 = now + delay;
      const p = Math.min(1, Math.max(0, (now - t0) / dur));
      node.textContent = f(value * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* map */
  const map = L.map("map", { zoomControl: false, attributionControl: true })
    .setView(PRESETS.testville.center, PRESETS.testville.zoom);
  L.control.zoom({ position: "bottomleft" }).addTo(map);
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 16,
    attribution: 'Dark Gray Canvas &copy; <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, FAO, NOAA, USGS, OpenStreetMap contributors',
  }).addTo(map);

  const STYLES = {
    // baseline reach: wide translucent blue casing + thin core, so the green
    // joint core stays visible where both cover the same road (the old
    // equal-weight pair fully occluded the baseline).
    baseCasing: { color: "#6aa5dd", weight: 9, opacity: 0.24, lineCap: "round",
                  lineJoin: "round" },
    baseCore:   { color: "#6aa5dd", weight: 1.6, opacity: 0.9, lineCap: "round" },
    joint:      { color: "#7ebc6f", weight: 2.6, opacity: 0.95, lineCap: "round" },
  };
  function setLayer(key, layer) {
    if (state.layers[key]) map.removeLayer(state.layers[key]);
    state.layers[key] = layer;
    const box = document.querySelector(`#toggles input[data-l="${key}"]`);
    if (layer && box && box.checked) layer.addTo(map);
    else if (layer) layer.bringToFront?.();
  }
  document.querySelectorAll("#toggles input").forEach((box) => {
    box.addEventListener("change", () => {
      const l = state.layers[box.dataset.l];
      if (!l) return;
      box.checked ? l.addTo(map) : map.removeLayer(l);
      box.closest("label").classList.toggle("on", box.checked);
    });
    box.closest("label").classList.toggle("on", box.checked);
  });

  /* form*/
  $("city").addEventListener("change", (e) => {
    state.city = e.target.value;
    $("custom-row").classList.toggle("hidden", state.city !== "__custom");
    const p = PRESETS[state.city];
    if (p) map.flyTo(p.center, p.zoom, { duration: REDUCED ? 0 : 1.4 });
  });
  document.querySelectorAll("#threshold button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#threshold button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      state.threshold = +b.dataset.v;
    }));
  $("sites-minus").addEventListener("click", () =>
    $("sites-n").textContent = state.numSites = Math.max(1, state.numSites - 1));
  $("sites-plus").addEventListener("click", () =>
    $("sites-n").textContent = state.numSites = Math.min(5, state.numSites + 1));
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !$("view-form").classList.contains("hidden"))
      $("submit").click();
  });

  function clearLayers() {
    for (const k of Object.keys(state.layers)) setLayer(k, null);
    $("toggles").classList.add("hidden");
  }

  /* submit + poll*/
  async function submit() {
    let body;
    if (state.city === "__custom") {
      const place = $("place").value.trim();
      if (!place) return toast("Enter a place string first.", true);
      body = { place };
    } else {
      body = { city: state.city };
    }
    body = { ...body, threshold: state.threshold, num_sites: state.numSites };
    state.lastBody = body;
    $("submit").disabled = true;
    try {
      const r = await fetch(API + "/api/jobs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
      state.jobId = j.job_id;
      $("run-jid").textContent = "run " + j.job_id;
      clearLayers(); // fresh map for the new run
      renderStages(null);
      show("running");
      poll();
    } catch (e) {
      toast(e.message, true);
    } finally {
      $("submit").disabled = false;
    }
  }
  $("submit").addEventListener("click", submit);

  function renderStages(stage, fallback) {
    const ul = $("stages");
    ul.innerHTML = "";
    const idx = stage ? STAGES.indexOf(stage.split(" (")[0]) : -1;
    STAGES.forEach((s, i) => {
      const li = document.createElement("li");
      if (stage && i < idx) li.className = "done";
      else if (i === idx) {
        li.className = "active" + (fallback ? " fallback" : "");
        if (fallback) li.textContent = "";
      }
      const mk = document.createElement("span");
      mk.className = "mk";
      mk.textContent = "✓";
      li.prepend(mk);
      li.append(document.createTextNode(
        i === idx && fallback ? stage : s));
      ul.append(li);
    });
    $("run-progress").style.width =
      (idx >= 0 ? Math.round(((idx + 1) / STAGES.length) * 100) : 0) + "%";
  }

  function poll() {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(async () => {
      try {
        const r = await fetch(API + "/api/jobs/" + state.jobId);
        if (!r.ok) throw new Error("poll failed");
        const job = await r.json();
        if (job.status === "queued" || job.status === "running") {
          $("run-elapsed").textContent =
            (job.elapsed_s ?? 0).toFixed(1) + " s";
          renderStages(job.stage || null, (job.stage || "").includes("skipping"));
          poll();
        } else if (job.status === "done") {
          renderResult(job.result);
        } else {
          renderFailed(job);
        }
      } catch {
        poll(); // transient network blip: keep polling
      }
    }, 2200);
  }

  /* result */
  function renderResult(res) {
    show("result");
    const joint = res.joint_pct ?? res.optimized_single_pct;
    tickTo($("v-base"), res.baseline_pct ?? 0, 1, 900, 150);
    tickTo($("v-joint"), joint ?? 0, 1, 1100, 350);
    $("v-total").textContent = fmt.format(res.total_population ?? 0);
    $("v-single").textContent = (res.optimized_single_pct ?? 0).toFixed(1) + "%";
    $("v-delta").textContent =
      `+${fmt.format(Math.round(res.newly_covered_people ?? 0))} people newly covered`;
    const badge = $("status-badge");
    badge.className = "badge " + (res.comparable ? "ok" : "warn");
    badge.textContent = res.comparable ? "Done" : "Not comparable";
    $("res-comparable").classList.toggle("hidden", res.comparable !== false);

    const rows = (list, node, withObj) => {
      node.innerHTML = "";
      (list || []).forEach((s) => {
        const li = document.createElement("li");
        li.innerHTML =
          `<span class="rk">#${s.rank}</span>` +
          `<span class="co">${s.lat.toFixed(4)}, ${s.lng.toFixed(4)}</span>` +
          `<span class="gn">+${fmt.format(s.gain_people ?? 0)}` +
          (withObj && s.objective_people != null
            ? ` <span style="color:var(--muted)">· obj ${fmt.format(s.objective_people)}</span>` : "") +
          `</span>`;
        node.append(li);
      });
    };
    rows(res.sites, $("res-sites"), true);
    rows(res.top_sites, $("res-top"), false);

    // compact "MM-DD HH:MM" for ISO stamps — full timestamps wrapped badly
    const short = (v) => {
      const s = String(v ?? "");
      return /^\d{4}-\d{2}-\d{2}T/.test(s)
        ? `${s.slice(5, 10)} ${s.slice(11, 16)}` : (s || "—");
    };
    const stamps = Object.entries(res.data_stamps || {})
      .map(([k, v]) => `<b>${k.replace("fetched_", "")}</b> ${short(v)}`)
      .join(" &nbsp;·&nbsp; ");
    $("res-stamps").innerHTML =
      (stamps || "no data stamps") +
      `<br>as of <b>${short(res.data_as_of)}</b> · generated <b>${short(res.generated_at)}</b>`;

    drawResultLayers(res);
  }

  function drawResultLayers(res) {
    setLayer("iso-base", res.isochrones?.baseline
      ? L.layerGroup([
          L.geoJSON(res.isochrones.baseline, { style: () => STYLES.baseCasing }),
          L.geoJSON(res.isochrones.baseline, { style: () => STYLES.baseCore }),
        ]) : null);
    const jointL = res.isochrones?.joint
      ? L.geoJSON(res.isochrones.joint, { style: () => STYLES.joint }) : null;
    setLayer("iso-joint", jointL);

    setLayer("sites", L.layerGroup((res.sites || []).map((s) =>
      L.marker([s.lat, s.lng], {
        icon: L.divIcon({ className: "", html:
          `<div class="mk-site"><span class="ring"></span><span class="core"></span>` +
          `<span class="num">${s.rank}</span></div>`, iconSize: [18, 18], iconAnchor: [9, 9] }),
      }).bindTooltip(`site #${s.rank} · +${fmt.format(s.gain_people ?? 0)} people`,
        { className: "noir", direction: "top", offset: [0, -10] }))));

    setLayer("cands", L.layerGroup((res.top_sites || []).map((s) =>
      L.marker([s.lat, s.lng], {
        icon: L.divIcon({ className: "", html: `<div class="mk-cand"></div>`,
          iconSize: [10, 10], iconAnchor: [5, 5] }),
      }).bindTooltip(`candidate #${s.rank} · +${fmt.format(s.gain_people ?? 0)} people`,
        { className: "noir", direction: "top", offset: [0, -8] }))));

    $("toggles").classList.remove("hidden");
    const b = jointL?.getBounds?.();
    if (b && b.isValid()) map.flyToBounds(b.pad(0.15), { duration: REDUCED ? 0 : 1.6 });
  }

  /* failed */
  function renderFailed(job) {
    show("failed");
    clearLayers(); // don't leave the previous job's findings on the map
    $("fail-msg").innerHTML =
      `<b style="color:var(--text)">run ${job.job_id}</b> failed after ` +
      `${(job.elapsed_s ?? 0).toFixed(0)} s<br>${(job.error || "unknown error")
        .replace(/</g, "&lt;")}`;
  }
  $("retry").addEventListener("click", () => {
    if (state.lastBody) submit();
  });
  $("new-case").addEventListener("click", () => show("form"));
  $("fail-new").addEventListener("click", () => show("form"));

  /* health */
  (async () => {
    try {
      const r = await fetch(API + "/api/health");
      if (!r.ok) throw 0;
      $("health-dot").classList.add("ok");
      $("health-txt").textContent = "ON DUTY";
    } catch {
      $("health-dot").classList.add("bad");
      $("health-txt").textContent = "OFFLINE";
    }
  })();
})();
