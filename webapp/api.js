(() => {
  const app = window.PowerbotApp;
  if (!app) return;

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

    if (hashParams.get("hash") && hashParams.get("user")) {
      return hash;
    }

    return "";
  };

  const resolveInitData = async () => {
    if (!app.tg) return "";
    for (let i = 0; i < 12; i += 1) {
      if (app.tg.initData) return app.tg.initData;
      const urlInit = extractInitDataFromUrl();
      if (urlInit) return urlInit;
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
    return extractInitDataFromUrl();
  };

  const buildHeaders = () => (app.initData ? { "X-Telegram-Init-Data": app.initData } : {});

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

  app.extractInitDataFromUrl = extractInitDataFromUrl;
  app.resolveInitData = resolveInitData;
  app.api = api;
})();
