(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (typeof tg.requestFullscreen === "function") {
      tg.requestFullscreen();
    }
    if (typeof tg.setHeaderColor === "function") {
      tg.setHeaderColor("#f6f1e8");
    }
    if (typeof tg.setBackgroundColor === "function") {
      tg.setBackgroundColor("#f6f1e8");
    }
    const safeTop = tg.contentSafeAreaInset?.top ?? tg.safeAreaInset?.top ?? 0;
    const extraTop = tg.platform === "ios" ? 110 : 72;
    document.documentElement.style.setProperty("--tg-safe-top", `${safeTop}px`);
    document.documentElement.style.setProperty("--tg-top-extra", `${extraTop}px`);
  }

  let initData = "";
  const extractInitDataFromUrl = () => {
    const hash = window.location.hash ? window.location.hash.substring(1) : "";
    const search = window.location.search ? window.location.search.substring(1) : "";
    const hashParams = new URLSearchParams(hash);
    const searchParams = new URLSearchParams(search);

    const direct =
      hashParams.get("tgWebAppData") ||
      hashParams.get("initData") ||
      searchParams.get("tgWebAppData") ||
      searchParams.get("initData");

    if (direct) return direct;

    // Деякі клієнти можуть передавати initData прямо в hash як query string
    if (hashParams.get("hash") && hashParams.get("user")) {
      return hash;
    }

    return "";
  };

  const resolveInitData = async () => {
    if (!tg) return "";
    for (let i = 0; i < 12; i += 1) {
      if (tg.initData) return tg.initData;
      const urlInit = extractInitDataFromUrl();
      if (urlInit) return urlInit;
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
    return extractInitDataFromUrl();
  };

  const buildHeaders = () => (initData ? { "X-Telegram-Init-Data": initData } : {});

  const elements = {
    authNotice: document.getElementById("authNotice"),
    authDebug: document.getElementById("authDebug"),
    heroStatus: document.getElementById("heroStatus"),
    buildingSelect: document.getElementById("buildingSelect"),
    buildingMeta: document.getElementById("buildingMeta"),
    saveBuilding: document.getElementById("saveBuilding"),
    powerStatus: document.getElementById("powerStatus"),
    powerMeta: document.getElementById("powerMeta"),
    powerMeter: document.getElementById("powerMeter"),
    alertPill: document.getElementById("alertPill"),
    alertMeta: document.getElementById("alertMeta"),
    alertPillLarge: document.getElementById("alertPillLarge"),
    alertMetaLarge: document.getElementById("alertMetaLarge"),
    heroAlertBadge: document.getElementById("heroAlertBadge"),
    refreshStatus: document.getElementById("refreshStatus"),
    heatingPill: document.getElementById("heatingPill"),
    heatingStats: document.getElementById("heatingStats"),
    heatingYesOption: document.getElementById("heatingYesOption"),
    heatingNoOption: document.getElementById("heatingNoOption"),
    heatingYesBar: document.getElementById("heatingYesBar"),
    heatingNoBar: document.getElementById("heatingNoBar"),
    heatingYesPct: document.getElementById("heatingYesPct"),
    heatingNoPct: document.getElementById("heatingNoPct"),
    waterPill: document.getElementById("waterPill"),
    waterStats: document.getElementById("waterStats"),
    waterYesOption: document.getElementById("waterYesOption"),
    waterNoOption: document.getElementById("waterNoOption"),
    waterYesBar: document.getElementById("waterYesBar"),
    waterNoBar: document.getElementById("waterNoBar"),
    waterYesPct: document.getElementById("waterYesPct"),
    waterNoPct: document.getElementById("waterNoPct"),
    sheltersList: document.getElementById("sheltersList"),
    placesCategorySelect: document.getElementById("placesCategorySelect"),
    placesList: document.getElementById("placesList"),
    placeSearch: document.getElementById("placeSearch"),
    searchPlaces: document.getElementById("searchPlaces"),
    serviceCards: document.getElementById("serviceCards"),
    lightToggle: document.getElementById("lightToggle"),
    alertToggle: document.getElementById("alertToggle"),
    quietSelect: document.getElementById("quietSelect"),
    saveSettings: document.getElementById("saveSettings"),
    donateLink: document.getElementById("donateLink"),
    toast: document.getElementById("toast"),
  };

  const state = {
    settings: null,
    buildings: [],
    categories: [],
    placesCategoryId: null,
  };

  const applyRevealAnimations = () => {
    const items = document.querySelectorAll(".hero, .nav, .view-frame");
    items.forEach((el, index) => {
      el.classList.add("reveal");
      el.style.animationDelay = `${index * 80}ms`;
    });
  };

  const nav = document.querySelector(".nav");
  const navItems = nav ? Array.from(nav.querySelectorAll(".nav-item")) : [];
  const views = Array.from(document.querySelectorAll(".view"));
  let activeNavButton = null;

  const updateNavIndicator = (button) => {
    if (!nav || !button) return;
    const navRect = nav.getBoundingClientRect();
    const btnRect = button.getBoundingClientRect();
    nav.style.setProperty("--indicator-left", `${btnRect.left - navRect.left}px`);
    nav.style.setProperty("--indicator-width", `${btnRect.width}px`);
    nav.style.setProperty("--indicator-top", `${btnRect.top - navRect.top}px`);
    nav.style.setProperty("--indicator-height", `${btnRect.height}px`);
  };

  const syncNavIndicator = () => {
    if (!nav || !activeNavButton) return;
    requestAnimationFrame(() => updateNavIndicator(activeNavButton));
  };

  const animateView = (viewEl) => {
    const items = viewEl.querySelectorAll(".panel, .card");
    items.forEach((el, index) => {
      el.classList.remove("reveal");
      // force reflow for restarting animation
      void el.offsetWidth;
      el.classList.add("reveal");
      el.style.animationDelay = `${index * 45}ms`;
    });
  };

  const setActiveView = (viewId) => {
    views.forEach((view) => {
      const isActive = view.id === `view-${viewId}`;
      view.classList.toggle("active", isActive);
      view.setAttribute("aria-hidden", isActive ? "false" : "true");
      if (isActive) {
        animateView(view);
      }
    });
  };

  const setActiveNav = (button) => {
    navItems.forEach((item) => {
      item.classList.remove("active");
      item.setAttribute("aria-selected", "false");
    });
    button.classList.add("active");
    button.setAttribute("aria-selected", "true");
    activeNavButton = button;
    syncNavIndicator();
  };

  const api = async (path, options = {}) => {
    const opts = {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...buildHeaders(),
        ...(options.headers || {}),
      },
    };
    const res = await fetch(`/api/v1/webapp${path}`, opts);
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}));
      throw new Error(payload.message || "Request failed");
    }
    return res.json();
  };

  const showToast = (text) => {
    if (!elements.toast) return;
    elements.toast.textContent = text;
    elements.toast.hidden = false;
    setTimeout(() => {
      elements.toast.hidden = true;
    }, 2400);
  };

  const formatDate = (iso) => {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("uk-UA", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
  };

  const renderBuildings = (buildings, selectedId) => {
    elements.buildingSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Оберіть будинок";
    elements.buildingSelect.appendChild(placeholder);

    buildings.forEach((b) => {
      const option = document.createElement("option");
      option.value = String(b.id);
      option.textContent = `${b.name} (${b.address})`;
      if (selectedId && b.id === selectedId) {
        option.selected = true;
      }
      elements.buildingSelect.appendChild(option);
    });
  };

  const renderPower = (power) => {
    if (!power || !power.building) {
      elements.powerStatus.textContent = "Будинок не обрано";
      elements.powerMeta.textContent = "Оберіть будинок, щоб отримувати точну інформацію.";
      elements.powerMeter.style.width = "0%";
      elements.heroStatus.textContent = "Оберіть будинок";
      return;
    }

    const { is_up, sensors_online, sensors_total, last_change } = power;
    if (sensors_total === 0) {
      elements.powerStatus.textContent = "Сенсорів немає";
      elements.powerMeta.textContent = "Поки немає датчика для цього будинку.";
      elements.powerMeter.style.width = "0%";
      elements.heroStatus.textContent = `${power.building.name}: без сенсорів`;
      return;
    }

    const percent = Math.round((sensors_online / sensors_total) * 100);
    elements.powerMeter.style.width = `${percent}%`;
    elements.powerStatus.textContent = is_up ? "Світло є" : "Світла немає";
    elements.powerMeta.textContent = `Сенсорів онлайн: ${sensors_online}/${sensors_total} · ${formatDate(last_change)}`;
    elements.heroStatus.textContent = is_up ? "Світло є" : "Світла немає";
  };

  const renderAlerts = (alerts) => {
    if (!alerts) return;
    if (alerts.status === "active") {
      if (elements.alertPill) {
        elements.alertPill.textContent = "Тривога";
        elements.alertPill.style.background = "rgba(200, 136, 116, 0.25)";
        elements.alertPill.style.color = "#8b3e2f";
      }
      if (elements.alertMeta) {
        elements.alertMeta.textContent = "Оголошено тривогу. Бережіть себе.";
      }
      if (elements.alertPillLarge) {
        elements.alertPillLarge.textContent = "Тривога";
        elements.alertPillLarge.style.background = "rgba(200, 136, 116, 0.25)";
        elements.alertPillLarge.style.color = "#8b3e2f";
      }
      if (elements.alertMetaLarge) {
        elements.alertMetaLarge.textContent = "Оголошено тривогу. Бережіть себе.";
      }
      if (elements.heroAlertBadge) {
        elements.heroAlertBadge.textContent = "Тривога!";
        elements.heroAlertBadge.style.background = "rgba(200, 136, 116, 0.25)";
        elements.heroAlertBadge.style.color = "#8b3e2f";
      }
    } else if (alerts.status === "inactive") {
      if (elements.alertPill) {
        elements.alertPill.textContent = "Відбій";
        elements.alertPill.style.background = "rgba(135, 155, 145, 0.2)";
        elements.alertPill.style.color = "#4d6a5f";
      }
      if (elements.alertMeta) {
        elements.alertMeta.textContent = "Зараз все спокійно.";
      }
      if (elements.alertPillLarge) {
        elements.alertPillLarge.textContent = "Відбій";
        elements.alertPillLarge.style.background = "rgba(135, 155, 145, 0.2)";
        elements.alertPillLarge.style.color = "#4d6a5f";
      }
      if (elements.alertMetaLarge) {
        elements.alertMetaLarge.textContent = "Зараз все спокійно.";
      }
      if (elements.heroAlertBadge) {
        elements.heroAlertBadge.textContent = "Немає тривоги";
        elements.heroAlertBadge.style.background = "rgba(135, 155, 145, 0.2)";
        elements.heroAlertBadge.style.color = "#4d6a5f";
      }
    } else {
      if (elements.alertPill) {
        elements.alertPill.textContent = "Невідомо";
      }
      if (elements.alertMeta) {
        elements.alertMeta.textContent = "Не вдалося отримати статус.";
      }
      if (elements.alertPillLarge) {
        elements.alertPillLarge.textContent = "Невідомо";
      }
      if (elements.alertMetaLarge) {
        elements.alertMetaLarge.textContent = "Не вдалося отримати статус.";
      }
      if (elements.heroAlertBadge) {
        elements.heroAlertBadge.textContent = "Статус тривоги?";
        elements.heroAlertBadge.style.background = "rgba(31, 44, 63, 0.08)";
        elements.heroAlertBadge.style.color = "#4a5059";
      }
    }
  };

  const renderStats = (section, stats) => {
    if (!stats) return;
    const percent = stats.total ? stats.has_percent : 0;
    const text = `Є: ${stats.has} · Немає: ${stats.has_not} · Всього: ${stats.total}`;
    section.textContent = `${text} (${percent}%)`;
  };

  const renderVoteBars = (stats, yesBar, noBar, yesPct, noPct) => {
    if (!stats) return;
    const yesPercent = stats.total ? Math.round(stats.has_percent) : 0;
    const noPercent = stats.total ? Math.round(100 - stats.has_percent) : 0;
    if (yesBar) yesBar.style.width = `${yesPercent}%`;
    if (noBar) noBar.style.width = `${noPercent}%`;
    if (yesPct) yesPct.textContent = `${yesPercent}%`;
    if (noPct) noPct.textContent = `${noPercent}%`;
  };

  const renderUserVotes = (heating, water) => {
    elements.heatingPill.textContent = heating?.user_vote === true ? "Ви: Є" : heating?.user_vote === false ? "Ви: Немає" : "Не голосували";
    elements.waterPill.textContent = water?.user_vote === true ? "Ви: Є" : water?.user_vote === false ? "Ви: Немає" : "Не голосували";

    if (elements.heatingYesOption && elements.heatingNoOption) {
      elements.heatingYesOption.classList.toggle("selected", heating?.user_vote === true);
      elements.heatingNoOption.classList.toggle("selected", heating?.user_vote === false);
    }
    if (elements.waterYesOption && elements.waterNoOption) {
      elements.waterYesOption.classList.toggle("selected", water?.user_vote === true);
      elements.waterNoOption.classList.toggle("selected", water?.user_vote === false);
    }
  };

  const renderShelters = (shelters) => {
    elements.sheltersList.innerHTML = "";
    if (!shelters || shelters.length === 0) {
      elements.sheltersList.innerHTML = "<p class='muted'>Список порожній.</p>";
      return;
    }
    const list = [...shelters];
    const priority = (shelter) => {
      const text = `${shelter.name || ""} ${shelter.description || ""} ${shelter.address || ""}`.toLowerCase();
      if (text.includes("укрит")) return 0;
      if (text.includes("паркінг")) return 1;
      if (text.includes("комора")) return 2;
      return 3;
    };
    list.sort((a, b) => {
      const pa = priority(a);
      const pb = priority(b);
      if (pa !== pb) return pa - pb;
      return (a.name || "").localeCompare(b.name || "", "uk");
    });
    list.forEach((shelter) => {
      const card = document.createElement("div");
      card.className = "shelter-card";
      card.innerHTML = `
        <strong>${shelter.name}</strong>
        <p class="muted">${shelter.description || ""}</p>
        ${shelter.address ? `<p class="muted">📍 ${shelter.address}</p>` : ""}
        ${shelter.map_image ? `<img src="${shelter.map_image}" alt="${shelter.name}" class="map" />` : ""}
        <div class="card-actions">
          <button class="button small ${shelter.liked ? "outline" : ""}" data-action="${shelter.liked ? "shelter-unlike" : "shelter-like"}" data-id="${shelter.id}">
            ${shelter.liked ? "Забрати лайк" : "Подобається"}
          </button>
          <span class="pill">❤ ${shelter.likes_count}</span>
        </div>
      `;
      elements.sheltersList.appendChild(card);
    });
  };

  const renderCategories = (categories) => {
    if (!elements.placesCategorySelect) return;
    elements.placesCategorySelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Оберіть категорію";
    elements.placesCategorySelect.appendChild(placeholder);

    categories.forEach((cat) => {
      const option = document.createElement("option");
      option.value = String(cat.id);
      option.textContent = cat.name;
      if (state.placesCategoryId && state.placesCategoryId === cat.id) {
        option.selected = true;
      }
      elements.placesCategorySelect.appendChild(option);
    });
  };

  const renderPlaces = (places) => {
    elements.placesList.innerHTML = "";
    if (!places || places.length === 0) {
      elements.placesList.innerHTML = "<p class='muted'>Нічого не знайдено.</p>";
      return;
    }
    places.forEach((place) => {
      const card = document.createElement("div");
      card.className = "place-card";
      card.innerHTML = `
        <strong>${place.name}</strong>
        <p class="muted">${place.description || ""}</p>
        <p class="muted">${place.address || ""}</p>
        <div class="card-actions">
          <button class="button small ${place.liked ? "outline" : ""}" data-action="${place.liked ? "place-unlike" : "place-like"}" data-id="${place.id}">
            ${place.liked ? "Забрати лайк" : "Подобається"}
          </button>
          <span class="pill">❤ ${place.likes_count || 0}</span>
        </div>
      `;
      elements.placesList.appendChild(card);
    });
  };

  const renderServices = (services) => {
    const cards = [];
    const items = [
      { label: "Охорона", value: services.security_phone },
      { label: "Сантехнік", value: services.plumber_phone },
      { label: "Електрик", value: services.electrician_phone },
      { label: "Диспетчер ліфтів", value: services.elevator_phones },
    ];
    items.forEach((item) => {
      if (!item.value) return;
      const card = document.createElement("div");
      card.className = "service-card";
      card.innerHTML = `
        <strong>${item.label}</strong>
        <p class="muted">${item.value}</p>
      `;
      cards.push(card);
    });
    elements.serviceCards.innerHTML = "";
    cards.forEach((card) => elements.serviceCards.appendChild(card));
  };

  const openPhoneDialer = (raw) => {
    if (!raw) return;
    const phone = raw.replace(/[^\d+]/g, "");
    if (!phone) return;
    const tel = `tel:${phone}`;
    try {
      if (tg && typeof tg.openLink === "function") {
        tg.openLink(tel);
        return;
      }
    } catch (err) {
      // fallback below
    }
    window.location.href = tel;
  };

  const renderSettings = (settings) => {
    elements.lightToggle.checked = settings.light_notifications;
    elements.alertToggle.checked = settings.alert_notifications;

    if (settings.quiet_start === null || settings.quiet_end === null) {
      elements.quietSelect.value = "off";
    } else {
      const key = `${settings.quiet_start}-${settings.quiet_end}`;
      elements.quietSelect.value = key === "23-7" || key === "22-8" || key === "0-6" ? key : "off";
    }
  };

  const loadBootstrap = async () => {
    initData = await resolveInitData();
    if (!initData) {
      elements.authNotice.hidden = false;
      if (elements.authDebug) {
        const debugInfo = {
          hasTelegram: Boolean(tg),
          platform: tg?.platform || null,
          version: tg?.version || null,
          initDataLength: tg?.initData ? tg.initData.length : 0,
          initDataUnsafeUser: tg?.initDataUnsafe?.user?.id || null,
          urlHasTgWebAppData: Boolean(extractInitDataFromUrl()),
          hashHasTgWebAppData: window.location.hash.includes("tgWebAppData"),
          hashHasUser: window.location.hash.includes("user="),
          hashHasHash: window.location.hash.includes("hash="),
          hashLength: window.location.hash ? window.location.hash.length : 0,
          searchLength: window.location.search ? window.location.search.length : 0,
          userAgent: navigator.userAgent,
        };
        elements.authDebug.textContent = JSON.stringify(debugInfo, null, 2);
        elements.authDebug.hidden = false;
      }
      return;
    }
    const payload = await api("/bootstrap");
    state.settings = payload.settings;
    state.buildings = payload.buildings;
    state.categories = payload.categories;

    renderBuildings(payload.buildings, payload.settings.building_id);
    renderPower(payload.power);
    renderAlerts(payload.alerts);
    renderStats(elements.heatingStats, payload.heating);
    renderStats(elements.waterStats, payload.water);
    renderVoteBars(payload.heating, elements.heatingYesBar, elements.heatingNoBar, elements.heatingYesPct, elements.heatingNoPct);
    renderVoteBars(payload.water, elements.waterYesBar, elements.waterNoBar, elements.waterYesPct, elements.waterNoPct);
    renderUserVotes(payload.heating, payload.water);
    renderShelters(payload.shelters);
    renderCategories(payload.categories);
    renderServices(payload.services);
    renderSettings(payload.settings);
    elements.donateLink.href = payload.donate_url;
    applyRevealAnimations();
  };

  const refreshStatus = async () => {
    const payload = await api("/status");
    renderPower(payload.power);
    renderAlerts(payload.alerts);
    renderStats(elements.heatingStats, payload.heating);
    renderStats(elements.waterStats, payload.water);
    renderVoteBars(payload.heating, elements.heatingYesBar, elements.heatingNoBar, elements.heatingYesPct, elements.heatingNoPct);
    renderVoteBars(payload.water, elements.waterYesBar, elements.waterNoBar, elements.waterYesPct, elements.waterNoPct);
    renderUserVotes(payload.heating, payload.water);
  };

  const saveBuilding = async () => {
    const id = parseInt(elements.buildingSelect.value, 10);
    if (!id) {
      showToast("Оберіть будинок");
      return;
    }
    await api("/building", { method: "POST", body: JSON.stringify({ building_id: id }) });
    state.settings.building_id = id;
    showToast("Будинок збережено");
    await refreshStatus();
  };

  const saveSettings = async () => {
    const quietValue = elements.quietSelect.value;
    let quiet_start = null;
    let quiet_end = null;
    if (quietValue !== "off") {
      const [start, end] = quietValue.split("-").map(Number);
      quiet_start = start;
      quiet_end = end;
    }

    const payload = {
      light_notifications: elements.lightToggle.checked,
      alert_notifications: elements.alertToggle.checked,
      quiet_start,
      quiet_end,
    };

    const result = await api("/notifications", { method: "POST", body: JSON.stringify(payload) });
    state.settings = { ...state.settings, ...result.settings };
    showToast("Налаштування збережено");
  };

  const vote = async (type, value) => {
    const payload = await api("/vote", { method: "POST", body: JSON.stringify({ type, value }) });
    renderStats(elements.heatingStats, payload.heating);
    renderStats(elements.waterStats, payload.water);
    renderVoteBars(payload.heating, elements.heatingYesBar, elements.heatingNoBar, elements.heatingYesPct, elements.heatingNoPct);
    renderVoteBars(payload.water, elements.waterYesBar, elements.waterNoBar, elements.waterYesPct, elements.waterNoPct);
    renderUserVotes(payload.heating, payload.water);
    showToast("Дякуємо за голос");
  };

  const searchPlaces = async () => {
    const query = elements.placeSearch.value.trim();
    const selectedCategoryId = elements.placesCategorySelect?.value
      ? Number(elements.placesCategorySelect.value)
      : null;
    if (!query && !selectedCategoryId) {
      showToast("Введіть запит або оберіть категорію");
      return;
    }
    if (!query && selectedCategoryId) {
      await loadPlacesByCategory(selectedCategoryId);
      return;
    }
    state.placesCategoryId = null;
    const payload = await api(`/places?q=${encodeURIComponent(query)}`);
    renderPlaces(payload.places);
  };

  const loadPlacesByCategory = async (id) => {
    state.placesCategoryId = id;
    renderCategories(state.categories);
    const payload = await api(`/places?service_id=${id}`);
    renderPlaces(payload.places);
  };

  const togglePlaceLike = async (id, like) => {
    await api(`/places/${id}/${like ? "like" : "unlike"}`, { method: "POST" });
    if (state.placesCategoryId) {
      await loadPlacesByCategory(state.placesCategoryId);
    } else {
      await searchPlaces();
    }
  };

  const toggleShelterLike = async (id, like) => {
    await api(`/shelters/${id}/${like ? "like" : "unlike"}`, { method: "POST" });
    const payload = await api("/shelters");
    renderShelters(payload.shelters);
  };

  elements.saveBuilding.addEventListener("click", () => {
    saveBuilding().catch((err) => showToast(err.message));
  });

  elements.refreshStatus.addEventListener("click", () => {
    refreshStatus().catch((err) => showToast(err.message));
  });

  elements.saveSettings.addEventListener("click", () => {
    saveSettings().catch((err) => showToast(err.message));
  });

  elements.searchPlaces.addEventListener("click", () => {
    searchPlaces().catch((err) => showToast(err.message));
  });

  if (elements.placesCategorySelect) {
    elements.placesCategorySelect.addEventListener("change", () => {
      const selected = elements.placesCategorySelect?.value;
      if (selected && !elements.placeSearch.value.trim()) {
        loadPlacesByCategory(Number(selected)).catch((err) => showToast(err.message));
      }
    });
  }

  elements.placeSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      searchPlaces().catch((err) => showToast(err.message));
    }
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const action = target.dataset.action;
    const id = target.dataset.id;

    if (action === "place-like" && id) {
      togglePlaceLike(Number(id), true).catch((err) => showToast(err.message));
    }
    if (action === "place-unlike" && id) {
      togglePlaceLike(Number(id), false).catch((err) => showToast(err.message));
    }
    if (action === "shelter-like" && id) {
      toggleShelterLike(Number(id), true).catch((err) => showToast(err.message));
    }
    if (action === "shelter-unlike" && id) {
      toggleShelterLike(Number(id), false).catch((err) => showToast(err.message));
    }
    if (action === "call") {
      openPhoneDialer(target.dataset.phone || "");
    }
    const voteTarget = target.closest("[data-vote]");
    if (voteTarget instanceof HTMLElement) {
      const type = voteTarget.dataset.vote;
      const value = voteTarget.dataset.value === "true";
      if (type) {
        vote(type, value).catch((err) => showToast(err.message));
      }
    }
  });

  loadBootstrap().catch((err) => {
    elements.authNotice.hidden = false;
    showToast(err.message);
  });

  if (navItems.length > 0) {
    navItems.forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.target || button.dataset.nav || "utilities";
        setActiveNav(button);
        setActiveView(target);
      });
    });

    const initial = navItems.find((item) => item.classList.contains("active")) || navItems[0];
    if (initial) {
      const target = initial.dataset.target || initial.dataset.nav || "utilities";
      setActiveNav(initial);
      setActiveView(target);
    }

    window.addEventListener("resize", () => {
      syncNavIndicator();
    });

    if (tg && typeof tg.onEvent === "function") {
      tg.onEvent("viewportChanged", () => {
        syncNavIndicator();
      });
    }
  }
})();
