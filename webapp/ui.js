(() => {
  const app = window.PowerbotApp;
  if (!app) return;
  const { elements, state } = app;

  const applyRevealAnimations = () => {
    const items = document.querySelectorAll(".hero, .nav, .view-frame");
    items.forEach((el, index) => {
      el.classList.add("reveal");
      el.style.animationDelay = `${index * 80}ms`;
    });
  };

  const updateNavIndicator = (button) => {
    if (!app.nav || !button) return;
    const navRect = app.nav.getBoundingClientRect();
    const btnRect = button.getBoundingClientRect();
    app.nav.style.setProperty("--indicator-left", `${btnRect.left - navRect.left}px`);
    app.nav.style.setProperty("--indicator-width", `${btnRect.width}px`);
    app.nav.style.setProperty("--indicator-top", `${btnRect.top - navRect.top}px`);
    app.nav.style.setProperty("--indicator-height", `${btnRect.height}px`);
  };

  const syncNavIndicator = () => {
    if (!app.nav || !app.activeNavButton) return;
    requestAnimationFrame(() => updateNavIndicator(app.activeNavButton));
  };

  const animateView = (viewEl) => {
    const items = viewEl.querySelectorAll(".panel, .card");
    items.forEach((el, index) => {
      el.classList.remove("reveal");
      void el.offsetWidth;
      el.classList.add("reveal");
      el.style.animationDelay = `${index * 45}ms`;
    });
  };

  const setActiveView = (viewId) => {
    app.views.forEach((view) => {
      const isActive = view.id === `view-${viewId}`;
      view.classList.toggle("active", isActive);
      view.setAttribute("aria-hidden", isActive ? "false" : "true");
      if (isActive) animateView(view);
    });
  };

  const setActiveNav = (button) => {
    app.navItems.forEach((item) => {
      item.classList.remove("active");
      item.setAttribute("aria-selected", "false");
    });
    button.classList.add("active");
    button.setAttribute("aria-selected", "true");
    app.activeNavButton = button;
    syncNavIndicator();
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
    return date.toLocaleString("uk-UA", {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
    });
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
      if (selectedId && b.id === selectedId) option.selected = true;
      elements.buildingSelect.appendChild(option);
    });
  };

  const renderSections = (selectedId, sectionsCount = 3) => {
    if (!elements.sectionSelect) return;
    elements.sectionSelect.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Оберіть секцію";
    elements.sectionSelect.appendChild(placeholder);

    const normalizedCount = Math.max(1, Math.min(3, Number(sectionsCount) || 3));
    for (let id = 1; id <= normalizedCount; id += 1) {
      const option = document.createElement("option");
      option.value = String(id);
      option.textContent = `${id} секція`;
      if (selectedId && id === selectedId) option.selected = true;
      elements.sectionSelect.appendChild(option);
    }
  };

  const renderPower = (power) => {
    if (!power || !power.building) {
      elements.powerStatus.textContent = "Будинок не обрано";
      elements.powerMeta.textContent = "Оберіть будинок, щоб отримувати точну інформацію.";
      elements.powerMeter.style.width = "0%";
      elements.heroStatus.textContent = "Оберіть будинок";
      return;
    }

    const { is_up, sensors_online, sensors_total, last_change, section_id } = power;
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
    const sectionText = section_id ? ` · секція ${section_id}` : "";
    elements.powerMeta.textContent = `Сенсорів онлайн: ${sensors_online}/${sensors_total}${sectionText} · ${formatDate(last_change)}`;
    elements.heroStatus.textContent = is_up ? "Світло є" : "Світла немає";
  };

  const renderSchedule = (schedule) => {
    if (!elements.scheduleText) return;
    const text = schedule?.text ? String(schedule.text).trim() : "";
    if (!text) {
      elements.scheduleText.textContent = "—";
      return;
    }
    elements.scheduleText.innerHTML = text.replace(/\n/g, "<br>");
  };

  const renderAlerts = (alerts) => {
    if (!alerts) return;
    if (alerts.status === "active") {
      if (elements.alertPill) {
        elements.alertPill.textContent = "Тривога";
        elements.alertPill.style.background = "rgba(200, 136, 116, 0.25)";
        elements.alertPill.style.color = "#8b3e2f";
      }
      if (elements.alertMeta) elements.alertMeta.textContent = "Оголошено тривогу. Бережіть себе.";
      if (elements.alertPillLarge) {
        elements.alertPillLarge.textContent = "Тривога";
        elements.alertPillLarge.style.background = "rgba(200, 136, 116, 0.25)";
        elements.alertPillLarge.style.color = "#8b3e2f";
      }
      if (elements.alertMetaLarge) elements.alertMetaLarge.textContent = "Оголошено тривогу. Бережіть себе.";
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
      if (elements.alertMeta) elements.alertMeta.textContent = "Зараз все спокійно.";
      if (elements.alertPillLarge) {
        elements.alertPillLarge.textContent = "Відбій";
        elements.alertPillLarge.style.background = "rgba(135, 155, 145, 0.2)";
        elements.alertPillLarge.style.color = "#4d6a5f";
      }
      if (elements.alertMetaLarge) elements.alertMetaLarge.textContent = "Зараз все спокійно.";
      if (elements.heroAlertBadge) {
        elements.heroAlertBadge.textContent = "Немає тривоги";
        elements.heroAlertBadge.style.background = "rgba(135, 155, 145, 0.2)";
        elements.heroAlertBadge.style.color = "#4d6a5f";
      }
    } else {
      if (elements.alertPill) elements.alertPill.textContent = "Невідомо";
      if (elements.alertMeta) elements.alertMeta.textContent = "Не вдалося отримати статус.";
      if (elements.alertPillLarge) elements.alertPillLarge.textContent = "Невідомо";
      if (elements.alertMetaLarge) elements.alertMetaLarge.textContent = "Не вдалося отримати статус.";
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
      if (state.placesCategoryId && state.placesCategoryId === cat.id) option.selected = true;
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
      const tier = (place.verified_tier || "").toString().trim();
      const verifiedLabel = tier ? tier.toUpperCase() : "Verified";
      const verifiedHtml = place.is_verified ? ` <span class="pill verified">✅ ${verifiedLabel}</span>` : "";
      card.innerHTML = `
        <strong>${place.name}</strong>${verifiedHtml}
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
    const parsePhones = (value) =>
      (value || "")
        .split(",")
        .map((phone) => phone.trim())
        .filter(Boolean);

    const cards = [];
    const items = [
      { label: "Адміністрація", values: ["067-107-38-08 (вайбер)", "044-300-18-77"] },
      { label: "Бухгалтерія", values: ["044-300-12-45", "067-558-35-77 (вайбер)"] },
      { label: "Охорона (цілодобово)", values: parsePhones(services.security_phone) },
      { label: "Сантехнік (цілодобово)", values: parsePhones(services.plumber_phone) },
      { label: "Електрик (цілодобово)", values: parsePhones(services.electrician_phone) },
      { label: "ІТ відділ", values: ["067-599-88-15"] },
      { label: "Диспетчер ліфтів (цілодобово)", values: parsePhones(services.elevator_phones) },
    ];
    items.forEach((item) => {
      if (!item.values || item.values.length === 0) return;
      const card = document.createElement("div");
      card.className = "service-card";
      card.innerHTML = `
        <strong>${item.label}</strong>
        <p class="muted">${item.values.join("<br>")}</p>
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
      if (app.tg && typeof app.tg.openLink === "function") {
        app.tg.openLink(tel);
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
    if (elements.scheduleToggle) {
      elements.scheduleToggle.checked = settings.schedule_notifications;
    }
    const businessOffersVisible = settings.business_offers_visible === true;
    if (elements.sponsoredToggle) {
      elements.sponsoredToggle.checked = settings.sponsored_offers_enabled === true;
      const sponsoredRow = elements.sponsoredToggle.closest("label.toggle");
      if (sponsoredRow) {
        sponsoredRow.hidden = !businessOffersVisible;
        sponsoredRow.style.display = businessOffersVisible ? "flex" : "none";
      }
    }
    if (elements.offersDigestToggle) {
      elements.offersDigestToggle.checked = settings.offers_digest_enabled === true;
      const digestRow = elements.offersDigestToggle.closest("label.toggle");
      if (digestRow) {
        digestRow.hidden = !businessOffersVisible;
        digestRow.style.display = businessOffersVisible ? "flex" : "none";
      }
    }

    if (settings.quiet_start === null || settings.quiet_end === null) {
      elements.quietSelect.value = "off";
    } else {
      const key = `${settings.quiet_start}-${settings.quiet_end}`;
      elements.quietSelect.value = key === "23-7" || key === "22-8" || key === "0-6" ? key : "off";
    }
  };

  app.ui = {
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
  };
})();
