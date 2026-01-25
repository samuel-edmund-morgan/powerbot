(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  let initData = "";
  const extractInitDataFromUrl = () => {
    const hash = window.location.hash ? window.location.hash.substring(1) : "";
    const search = window.location.search ? window.location.search.substring(1) : "";
    const hashParams = new URLSearchParams(hash);
    const searchParams = new URLSearchParams(search);

    return (
      hashParams.get("tgWebAppData") ||
      hashParams.get("initData") ||
      searchParams.get("tgWebAppData") ||
      searchParams.get("initData") ||
      ""
    );
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
    refreshStatus: document.getElementById("refreshStatus"),
    heatingPill: document.getElementById("heatingPill"),
    heatingStats: document.getElementById("heatingStats"),
    waterPill: document.getElementById("waterPill"),
    waterStats: document.getElementById("waterStats"),
    sheltersList: document.getElementById("sheltersList"),
    placesCategories: document.getElementById("placesCategories"),
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
      elements.alertPill.textContent = "Тривога";
      elements.alertPill.style.background = "rgba(200, 136, 116, 0.25)";
      elements.alertPill.style.color = "#8b3e2f";
      elements.alertMeta.textContent = "Оголошено тривогу. Бережіть себе.";
    } else if (alerts.status === "inactive") {
      elements.alertPill.textContent = "Відбій";
      elements.alertPill.style.background = "rgba(135, 155, 145, 0.2)";
      elements.alertPill.style.color = "#4d6a5f";
      elements.alertMeta.textContent = "Зараз все спокійно.";
    } else {
      elements.alertPill.textContent = "Невідомо";
      elements.alertMeta.textContent = "Не вдалося отримати статус."
    }
  };

  const renderStats = (section, stats) => {
    if (!stats) return;
    const percent = stats.total ? stats.has_percent : 0;
    const text = `Є: ${stats.has} · Немає: ${stats.has_not} · Всього: ${stats.total}`;
    section.textContent = `${text} (${percent}%)`;
  };

  const renderUserVotes = (heating, water) => {
    elements.heatingPill.textContent = heating?.user_vote === true ? "Ви: Є" : heating?.user_vote === false ? "Ви: Немає" : "Не голосували";
    elements.waterPill.textContent = water?.user_vote === true ? "Ви: Є" : water?.user_vote === false ? "Ви: Немає" : "Не голосували";
  };

  const renderShelters = (shelters) => {
    elements.sheltersList.innerHTML = "";
    if (!shelters || shelters.length === 0) {
      elements.sheltersList.innerHTML = "<p class='muted'>Список порожній.</p>";
      return;
    }
    shelters.forEach((shelter) => {
      const card = document.createElement("div");
      card.className = "shelter-card";
      card.innerHTML = `
        <strong>${shelter.name}</strong>
        <p class="muted">${shelter.description || ""}</p>
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
    elements.placesCategories.innerHTML = "";
    categories.forEach((cat) => {
      const chip = document.createElement("button");
      chip.className = `chip${state.placesCategoryId === cat.id ? " active" : ""}`;
      chip.textContent = cat.name;
      chip.dataset.action = "category";
      chip.dataset.id = cat.id;
      elements.placesCategories.appendChild(chip);
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
        <a class="button small outline" href="tel:${item.value.replace(/\s|,/g, "")}">Зателефонувати</a>
      `;
      cards.push(card);
    });
    elements.serviceCards.innerHTML = "";
    cards.forEach((card) => elements.serviceCards.appendChild(card));
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
    renderUserVotes(payload.heating, payload.water);
    renderShelters(payload.shelters);
    renderCategories(payload.categories);
    renderServices(payload.services);
    renderSettings(payload.settings);
    elements.donateLink.href = payload.donate_url;
  };

  const refreshStatus = async () => {
    const payload = await api("/status");
    renderPower(payload.power);
    renderAlerts(payload.alerts);
    renderStats(elements.heatingStats, payload.heating);
    renderStats(elements.waterStats, payload.water);
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
    renderUserVotes(payload.heating, payload.water);
    showToast("Дякуємо за голос");
  };

  const searchPlaces = async () => {
    const query = elements.placeSearch.value.trim();
    if (!query) {
      showToast("Введіть запит");
      return;
    }
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

    if (action === "category" && id) {
      loadPlacesByCategory(Number(id)).catch((err) => showToast(err.message));
    }
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
    if (target.dataset.vote) {
      const type = target.dataset.vote;
      const value = target.dataset.value === "true";
      vote(type, value).catch((err) => showToast(err.message));
    }
  });

  loadBootstrap().catch((err) => {
    elements.authNotice.hidden = false;
    showToast(err.message);
  });
})();
