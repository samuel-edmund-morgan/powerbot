(() => {
  const app = window.PowerbotApp;
  if (!app || !app.api || !app.ui) return;
  const { elements, state, tg } = app;
  const {
    applyRevealAnimations,
    syncNavIndicator,
    setActiveNav,
    setActiveView,
    showToast,
    renderBuildings,
    renderSections,
    renderPower,
    renderSchedule,
    renderAlerts,
    renderStats,
    renderVoteBars,
    renderUserVotes,
    renderShelters,
    renderCategories,
    renderPlaces,
    renderServices,
    openPhoneDialer,
    renderSettings,
  } = app.ui;

  if (tg) {
    tg.ready();
    tg.expand();
    if (typeof tg.requestFullscreen === "function") tg.requestFullscreen();
    if (typeof tg.setHeaderColor === "function") tg.setHeaderColor("#f6f1e8");
    if (typeof tg.setBackgroundColor === "function") tg.setBackgroundColor("#f6f1e8");

    const safeTop = tg.contentSafeAreaInset?.top ?? tg.safeAreaInset?.top ?? 0;
    const extraTop = tg.platform === "ios" ? 110 : 72;
    document.documentElement.style.setProperty("--tg-safe-top", `${safeTop}px`);
    document.documentElement.style.setProperty("--tg-top-extra", `${extraTop}px`);
  }

  const loadBootstrap = async () => {
    app.initData = await app.resolveInitData();
    if (!app.initData) {
      elements.authNotice.hidden = false;
      if (elements.authDebug) {
        const debugInfo = {
          hasTelegram: Boolean(tg),
          platform: tg?.platform || null,
          version: tg?.version || null,
          initDataLength: tg?.initData ? tg.initData.length : 0,
          initDataUnsafeUser: tg?.initDataUnsafe?.user?.id || null,
          urlHasTgWebAppData: Boolean(app.extractInitDataFromUrl()),
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

    const payload = await app.api("/bootstrap");
    state.settings = payload.settings;
    state.buildings = payload.buildings;
    state.categories = payload.categories;

    const getSectionsCountForBuilding = (building_id) => {
      const id = Number(building_id || 0);
      const building = state.buildings.find((b) => b.id === id);
      const count = building?.sections_count;
      return typeof count === "number" && count > 0 ? count : 3;
    };

    renderBuildings(payload.buildings, payload.settings.building_id);
    renderSections(payload.settings.section_id, getSectionsCountForBuilding(payload.settings.building_id));
    renderPower(payload.power);
    renderSchedule(payload.schedule);
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

    // Keep sections dropdown in sync with selected building.
    elements.buildingSelect.addEventListener("change", () => {
      const building_id = parseInt(elements.buildingSelect.value || "", 10);
      const count = getSectionsCountForBuilding(building_id);
      const selected = parseInt(elements.sectionSelect?.value || "", 10);
      const normalizedSelected = selected && selected <= count ? selected : null;
      renderSections(normalizedSelected, count);
    });
  };

  const refreshStatus = async () => {
    const payload = await app.api("/status");
    renderPower(payload.power);
    renderSchedule(payload.schedule);
    renderAlerts(payload.alerts);
    renderStats(elements.heatingStats, payload.heating);
    renderStats(elements.waterStats, payload.water);
    renderVoteBars(payload.heating, elements.heatingYesBar, elements.heatingNoBar, elements.heatingYesPct, elements.heatingNoPct);
    renderVoteBars(payload.water, elements.waterYesBar, elements.waterNoBar, elements.waterYesPct, elements.waterNoPct);
    renderUserVotes(payload.heating, payload.water);
  };

  const saveBuilding = async () => {
    const building_id = parseInt(elements.buildingSelect.value, 10);
    if (!building_id) {
      showToast("Оберіть будинок");
      return;
    }
    const section_id = parseInt(elements.sectionSelect?.value || "", 10);
    if (!section_id) {
      showToast("Оберіть секцію");
      return;
    }

    await app.api("/building", { method: "POST", body: JSON.stringify({ building_id, section_id }) });
    state.settings.building_id = building_id;
    state.settings.section_id = section_id;
    showToast("Будинок і секцію збережено");
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
      schedule_notifications: elements.scheduleToggle?.checked ?? false,
      quiet_start,
      quiet_end,
    };

    const result = await app.api("/notifications", { method: "POST", body: JSON.stringify(payload) });
    state.settings = { ...state.settings, ...result.settings };
    showToast("Налаштування збережено");
  };

  const vote = async (type, value) => {
    const payload = await app.api("/vote", { method: "POST", body: JSON.stringify({ type, value }) });
    renderStats(elements.heatingStats, payload.heating);
    renderStats(elements.waterStats, payload.water);
    renderVoteBars(payload.heating, elements.heatingYesBar, elements.heatingNoBar, elements.heatingYesPct, elements.heatingNoPct);
    renderVoteBars(payload.water, elements.waterYesBar, elements.waterNoBar, elements.waterYesPct, elements.waterNoPct);
    renderUserVotes(payload.heating, payload.water);
    showToast("Дякуємо за голос");
  };

  const searchPlaces = async () => {
    const query = elements.placeSearch.value.trim();
    const selectedCategoryId = elements.placesCategorySelect?.value ? Number(elements.placesCategorySelect.value) : null;

    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (selectedCategoryId) params.set("service_id", String(selectedCategoryId));

    const suffix = params.toString() ? `?${params.toString()}` : "";
    const payload = await app.api(`/places${suffix}`);
    state.placesCategoryId = selectedCategoryId || null;
    renderPlaces(payload.places);
  };

  const loadPlacesByCategory = async (id) => {
    state.placesCategoryId = id;
    renderCategories(state.categories);
    const payload = await app.api(`/places?service_id=${id}`);
    renderPlaces(payload.places);
  };

  const togglePlaceLike = async (id, like) => {
    await app.api(`/places/${id}/${like ? "like" : "unlike"}`, { method: "POST" });
    if (state.placesCategoryId) {
      await loadPlacesByCategory(state.placesCategoryId);
    } else {
      await searchPlaces();
    }
  };

  const toggleShelterLike = async (id, like) => {
    await app.api(`/shelters/${id}/${like ? "like" : "unlike"}`, { method: "POST" });
    const payload = await app.api("/shelters");
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
      if (type) vote(type, value).catch((err) => showToast(err.message));
    }
  });

  loadBootstrap().catch((err) => {
    elements.authNotice.hidden = false;
    showToast(err.message);
  });

  if (app.navItems.length > 0) {
    app.navItems.forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.target || button.dataset.nav || "utilities";
        setActiveNav(button);
        setActiveView(target);
      });
    });

    const initial = app.navItems.find((item) => item.classList.contains("active")) || app.navItems[0];
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
