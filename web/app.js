(() => {
  "use strict";
  const PAGE = 60;
  const SENIORITY = { intern: "實習", junior: "初階", mid: "中階", senior: "資深", staff_plus: "Staff+", manager: "主管" };
  // Date facet: [days, label]. "Within N days" counts the job's date (posted, else first collected) as day 0.
  const DAY_OPTIONS = [[1, "今天"], [3, "3 天內"], [7, "7 天內"], [14, "14 天內"], [30, "30 天內"], [90, "90 天內"]];
  const CITY_ORDER = ["台北", "新北", "新竹", "桃園", "台中", "台南", "高雄", "台灣"];
  const $ = (s) => document.querySelector(s);

  let DATA = null;
  let shown = PAGE;
  const state = { q: "", sort: "score", group: new Set(), category: new Set(), city: new Set(), seniority: new Set(), days: 0, survey: false, company: "" };

  const daysAgo = (iso) => (iso ? (Date.now() - new Date(iso + "T00:00:00+08:00")) / 864e5 : Infinity);
  const jobDate = (j) => (j.posted_at || j.first_seen || "").slice(0, 10) || null;
  const esc = (s) => String(s ?? "");
  const label = (en, zh) => (zh && zh !== en ? `${en} ${zh}` : en);

  async function load() {
    for (const url of ["data/jobs.json", "../data/jobs.json"]) {
      try {
        const r = await fetch(url, { cache: "no-cache" });
        if (r.ok) return r.json();
      } catch (_) { /* try next */ }
    }
    throw new Error("找不到 jobs.json");
  }

  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    state.q = p.get("q") || "";
    state.sort = p.get("sort") || "score";
    for (const k of ["group", "category", "city", "seniority"]) state[k] = new Set((p.get(k) || "").split(",").filter(Boolean));
    state.days = DAY_OPTIONS.some(([d]) => d === +p.get("days")) ? +p.get("days") : p.get("new") === "1" ? 7 : 0; // "new=1": old links
    state.survey = p.get("survey") === "1";
    state.company = p.get("company") || "";
  }
  function writeHash() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.sort !== "score") p.set("sort", state.sort);
    for (const k of ["group", "category", "city", "seniority"]) if (state[k].size) p.set(k, [...state[k]].join(","));
    if (state.days) p.set("days", state.days);
    if (state.survey) p.set("survey", "1");
    if (state.company) p.set("company", state.company);
    history.replaceState(null, "", p.toString() ? "#" + p : location.pathname);
  }

  const surveyIds = () => new Set(DATA.companies.filter((c) => c.in_salary_survey).map((c) => c.id));

  function matches(j, skip) {
    if (state.q) {
      const hay = [j.title, j.company, j.company_zh, j.department, j.summary, ...(j.skills || []), ...(j.matched || [])].join(" ").toLowerCase();
      if (!state.q.toLowerCase().split(/\s+/).every((t) => hay.includes(t))) return false;
    }
    if (skip !== "group" && state.group.size && !state.group.has(j.group)) return false;
    if (skip !== "category" && state.category.size && !state.category.has(j.category)) return false;
    if (skip !== "city" && state.city.size && !state.city.has(j.city)) return false;
    if (skip !== "seniority" && state.seniority.size && !state.seniority.has(j.seniority)) return false;
    if (skip !== "days" && state.days && daysAgo(jobDate(j)) >= state.days) return false;
    if (state.survey && !surveyIds().has(j.company_id)) return false;
    if (state.company && j.company_id !== state.company) return false;
    return true;
  }

  function renderChips(key, el, labels, order) {
    const counts = {};
    for (const j of DATA.jobs) if (matches(j, key)) counts[j[key]] = (counts[j[key]] || 0) + 1;
    const keys = (order || Object.keys(labels)).filter((k) => counts[k] || state[key].has(k));
    el.replaceChildren(
      ...keys.map((k) => {
        const b = document.createElement("button");
        b.className = "chip";
        b.type = "button";
        b.setAttribute("aria-pressed", state[key].has(k));
        b.innerHTML = `${esc(labels[k] || k)}<span class="n">${counts[k] || 0}</span>`;
        b.onclick = () => { state[key].has(k) ? state[key].delete(k) : state[key].add(k); shown = PAGE; update(); };
        return b;
      })
    );
  }

  // Single-select, cumulative ranges; counts respect every other filter.
  function renderDays() {
    const ages = DATA.jobs.filter((j) => matches(j, "days")).map((j) => daysAgo(jobDate(j)));
    const opts = [[0, "不限", ages.length], ...DAY_OPTIONS.map(([d, l]) => [d, l, ages.filter((a) => a < d).length])];
    $("#f-days").replaceChildren(
      ...opts.filter(([d, , n]) => n || d === 0 || d === state.days).map(([d, l, n]) => {
        const b = document.createElement("button");
        b.className = "chip";
        b.type = "button";
        b.setAttribute("aria-pressed", state.days === d);
        b.innerHTML = `${l}<span class="n">${n}</span>`;
        b.onclick = () => { state.days = d; shown = PAGE; update(); };
        return b;
      })
    );
  }

  function sorted(list) {
    const by = {
      score: (a, b) => b.score - a.score || (b.first_seen || "").localeCompare(a.first_seen || ""),
      new: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || (b.posted_at || "").localeCompare(a.posted_at || "") || b.score - a.score,
      company: (a, b) => a.company.localeCompare(b.company) || b.score - a.score,
    }[state.sort];
    return list.slice().sort(by);
  }

  function renderJobs(list) {
    const tpl = $("#job-tpl");
    const ol = $("#jobs");
    if (!list.length) {
      ol.innerHTML = '<li class="empty">沒有符合條件的職缺</li>';
      $("#more").hidden = true;
      return;
    }
    const frag = document.createDocumentFragment();
    for (const j of list.slice(0, shown)) {
      const n = tpl.content.cloneNode(true);
      const s = Math.round(j.score);
      n.querySelector(".score-num").textContent = s;
      n.querySelector(".score-bar i").style.width = s + "%";
      n.querySelector(".company").textContent = label(j.company, j.company_zh);
      n.querySelector(".new").hidden = daysAgo(j.first_seen) > 7;
      const via = n.querySelector(".via");
      via.hidden = !j.via;
      via.textContent = j.via ? `來源：${j.via}` : "";
      const a = n.querySelector(".title");
      a.textContent = j.title;
      a.href = j.url;
      n.querySelector(".summary").textContent = j.summary || j.snippet || "";
      const tags = (j.skills && j.skills.length ? j.skills : j.matched || []).slice(0, 6);
      if (j.seniority && SENIORITY[j.seniority]) tags.unshift(SENIORITY[j.seniority]);
      n.querySelector(".tags").replaceChildren(...tags.map((t) => Object.assign(document.createElement("span"), { textContent: t })));
      n.querySelector(".city").textContent = j.city;
      n.querySelector(".city").title = j.location;
      n.querySelector(".cat").textContent = DATA.categories[j.category] || j.category;
      const d = n.querySelector(".date");
      d.textContent = j.posted_at ? `刊登 ${j.posted_at}` : `收錄 ${j.first_seen}`;
      d.dateTime = j.posted_at || j.first_seen;
      frag.append(n);
    }
    ol.replaceChildren(frag);
    $("#more").hidden = list.length <= shown;
  }

  function update() {
    writeHash();
    renderChips("group", $("#f-group"), DATA.groups);
    renderChips("category", $("#f-category"), DATA.categories);
    renderChips("city", $("#f-city"), Object.fromEntries(CITY_ORDER.map((c) => [c, c])), CITY_ORDER);
    renderDays();
    if (DATA.llm_enabled) renderChips("seniority", $("#f-seniority"), SENIORITY);
    const list = sorted(DATA.jobs.filter((j) => matches(j)));
    $("#result-line").textContent = `共 ${list.length} 筆職缺`;
    renderJobs(list);
    renderExternal();
  }

  // Companies whose official careers sites can't be crawled: link cards, filtered like the job list.
  function renderExternal() {
    // Crawled companies whose site failed this run and have nothing to show also get a card.
    const failing = DATA.companies
      .filter((c) => c.ok === false && !c.ai_jobs)
      .map((c) => ({ ...c, reason: "暫時無法自動抓取" }));
    const listed = new Set(DATA.jobs.map((j) => `${j.company} ${j.company_zh}`.toLowerCase()));
    const hasJobs = (c) => [...listed].some((l) => (c.name_zh && l.includes(c.name_zh.toLowerCase())) || l.includes(c.name.toLowerCase()));
    const ext = [...(DATA.external || []), ...failing].filter((c) => {
      if (hasJobs(c)) return false; // e.g. a link-only company that now has listings via a job board
      if (state.group.size && !state.group.has(c.group)) return false;
      if (state.survey && !c.in_salary_survey) return false;
      if (state.company) return false;
      if (state.q) {
        const hay = `${c.name} ${c.name_zh}`.toLowerCase();
        // keep cards visible for topic searches ("LLM"); hide them only when the query names another company
        const namesCompany = [...DATA.companies, ...(DATA.external || [])].some((o) => `${o.name} ${o.name_zh}`.toLowerCase().includes(state.q.toLowerCase()));
        if (namesCompany && !hay.includes(state.q.toLowerCase())) return false;
      }
      return true;
    });
    $("#external").hidden = !ext.length;
    $("#ext-grid").replaceChildren(
      ...ext.map((c) => {
        const a = document.createElement("a");
        a.className = "ext-card";
        a.href = c.careers_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        const name = Object.assign(document.createElement("span"), { className: "ext-name", textContent: c.name_zh && c.name_zh !== c.name ? `${c.name_zh} ${c.name}` : c.name });
        const meta = Object.assign(document.createElement("span"), { className: "ext-meta", textContent: `${DATA.groups[c.group] || c.group} · ${c.reason}` });
        a.append(name, meta);
        return a;
      })
    );
  }

  function renderStatic() {
    const fresh = DATA.jobs.filter((j) => daysAgo(j.first_seen) <= 7).length;
    const updated = new Date(DATA.generated_at).toLocaleString("zh-TW", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Taipei" });
    $("#stats").innerHTML = [
      ["AI 職缺", DATA.jobs.length],
      ["近 7 天新增", fresh],
      ["有職缺的公司", new Set(DATA.jobs.map((j) => j.company_id)).size],
      ["更新", updated],
    ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");

    // Crawl targets per company type, from the data so it stays accurate as companies are added.
    const official = DATA.companies.filter((c) => !c.via && c.group !== "tw_startup");
    const startups = DATA.companies.filter((c) => c.via).length;
    const parts = Object.entries(DATA.groups).map(([g, name]) => {
      const n = g === "tw_startup" ? startups : official.filter((c) => c.group === g).length;
      return n ? `${g === "tw_startup" ? "Yourator 新創" : name} <b>${n}</b> 家` : "";
    }).filter(Boolean);
    const ext = (DATA.external || []).length;
    $("#scope").innerHTML = `爬取對象：${parts.join("、")}${ext ? `；另有 <b>${ext}</b> 家無法自動爬取的公司附官網連結` : ""}。`;

    const sel = $("#f-company");
    for (const c of DATA.companies.filter((c) => c.ai_jobs).sort((a, b) => b.ai_jobs - a.ai_jobs)) {
      sel.add(new Option(`${label(c.name, c.name_zh)}（${c.ai_jobs}）`, c.id));
    }
    $("#facet-seniority").hidden = !DATA.llm_enabled;

    const tbody = $("#coverage tbody");
    tbody.replaceChildren(
      ...DATA.companies
        .filter((c) => !c.via) // startups found through a job board are summarised by the board's own row
        .slice()
        .sort((a, b) => a.group.localeCompare(b.group) || (b.ai_jobs || 0) - (a.ai_jobs || 0))
        .map((c) => {
          const tr = document.createElement("tr");
          const name = c.careers_url ? `<a href="${encodeURI(c.careers_url)}" target="_blank" rel="noopener noreferrer"></a>` : "<span></span>";
          tr.innerHTML = `<td>${name}</td><td></td><td class="num">${c.taiwan_jobs ?? "–"}</td><td class="num">${c.ai_jobs ?? 0}</td><td></td>`;
          tr.cells[0].firstChild.textContent = label(c.name, c.name_zh);
          tr.cells[1].textContent = DATA.groups[c.group] || c.group;
          tr.cells[4].textContent = c.ok === false ? "抓取失敗" : "正常";
          if (c.ok === false) { tr.cells[4].className = "err"; tr.cells[4].title = c.error || ""; }
          return tr;
        })
    );
    $("#method").textContent = DATA.llm_enabled
      ? `關聯度 = 75% LLM 分析（${DATA.llm_model}）＋ 25% 關鍵字規則。本次掃描 ${DATA.stats.taiwan_jobs_scanned} 筆台灣職缺。`
      : `關聯度由關鍵字規則計算。本次掃描 ${DATA.stats.taiwan_jobs_scanned} 筆台灣職缺。`;
  }

  function bind() {
    const q = $("#q");
    q.value = state.q;
    let t;
    q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value.trim(); shown = PAGE; update(); }, 150); });
    $("#sort").value = state.sort;
    $("#sort").onchange = (e) => { state.sort = e.target.value; update(); };
    $("#t-survey").checked = state.survey;
    $("#t-survey").onchange = (e) => { state.survey = e.target.checked; shown = PAGE; update(); };
    $("#f-company").value = state.company;
    $("#f-company").onchange = (e) => { state.company = e.target.value; shown = PAGE; update(); };
    $("#more").onclick = () => { shown += PAGE; update(); };
    $("#reset").onclick = () => { location.hash = ""; readHash(); bind(); update(); };
  }

  // GitHub star count: public API (60 requests/hour per IP unauthenticated), cached per browser for 30 min.
  async function loadStars() {
    const KEY = "ai-career:stars", TTL = 30 * 60 * 1000;
    let cached = null;
    try { cached = JSON.parse(localStorage.getItem(KEY)); } catch (_) { /* storage unavailable */ }
    let n = cached && Date.now() - cached.t < TTL ? cached.n : null;
    if (n == null) {
      try {
        const r = await fetch("https://api.github.com/repos/Hikari-Tsai/ai-career", { headers: { Accept: "application/vnd.github+json" } });
        if (r.ok) n = (await r.json()).stargazers_count;
      } catch (_) { /* offline or rate-limited */ }
      if (n == null && cached) n = cached.n; // stale beats nothing
      else if (n != null) try { localStorage.setItem(KEY, JSON.stringify({ n, t: Date.now() })); } catch (_) { /* ignore */ }
    }
    if (typeof n === "number") {
      const el = $("#star-count");
      el.textContent = n.toLocaleString("zh-TW");
      el.hidden = false;
      $("#star").setAttribute("aria-label", `在 GitHub 給這個專案星星（目前 ${n} 顆）`);
    }
  }
  loadStars();

  load()
    .then((d) => {
      DATA = d; readHash(); renderStatic(); bind(); update();
      window.addEventListener("hashchange", () => { readHash(); bind(); shown = PAGE; update(); });
    })
    .catch((e) => { $("#jobs").innerHTML = `<li class="empty">資料載入失敗：${esc(e.message)}</li>`; });
})();
