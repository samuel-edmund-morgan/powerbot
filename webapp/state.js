(() => {
  const app = (window.PowerbotApp = window.PowerbotApp || {});

  app.tg = window.Telegram?.WebApp || null;
  app.initData = "";

  app.elements = {
    authNotice: document.getElementById("authNotice"),
    authDebug: document.getElementById("authDebug"),
    heroStatus: document.getElementById("heroStatus"),
    buildingSelect: document.getElementById("buildingSelect"),
    sectionSelect: document.getElementById("sectionSelect"),
    buildingMeta: document.getElementById("buildingMeta"),
    saveBuilding: document.getElementById("saveBuilding"),
    powerStatus: document.getElementById("powerStatus"),
    powerMeta: document.getElementById("powerMeta"),
    powerMeter: document.getElementById("powerMeter"),
    scheduleText: document.getElementById("scheduleText"),
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
    scheduleToggle: document.getElementById("scheduleToggle"),
    sponsoredToggle: document.getElementById("sponsoredToggle"),
    offersDigestToggle: document.getElementById("offersDigestToggle"),
    quietSelect: document.getElementById("quietSelect"),
    saveSettings: document.getElementById("saveSettings"),
    toast: document.getElementById("toast"),
  };

  app.state = {
    settings: null,
    buildings: [],
    categories: [],
    placesCategoryId: null,
  };

  app.nav = document.querySelector(".nav");
  app.navItems = app.nav ? Array.from(app.nav.querySelectorAll(".nav-item")) : [];
  app.views = Array.from(document.querySelectorAll(".view"));
  app.activeNavButton = null;
})();
