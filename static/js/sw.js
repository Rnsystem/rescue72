self.addEventListener("install", (event) => {
  self.skipWaiting(); // 新SWを即時有効化
});

self.addEventListener("activate", (event) => {
  event.waitUntil(clients.claim()); // 既存タブも制御下に
});

self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let data = { title: "Rescue72", body: "", url: "/answer/" };

    try {
      if (event.data) data = await event.data.json();
    } catch (e) {
      const text = event.data ? await event.data.text() : "";
      data = { title: "Rescue72", body: text || "", url: "/answer/" };
    }

    const title = data.title || "Rescue72";
    const body  = data.body  || "";
    const url   = data.url   || "/answer/";

    await self.registration.showNotification(title, {
      body,
      data: { url, raw: data },
    });
  })());
});




self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification?.data?.url || "/answer/";
  console.log("notificationclick", event.notification?.data);

  event.waitUntil((async () => {
    // フォーカスできるタブがあれば前面に出す（遷移はしない）
    const clientList = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clientList) {
      if ("focus" in client) {
        await client.focus();
        break;
      }
    }
    // ★遷移は常に openWindow（最も確実）
    return clients.openWindow(url);
  })());
});
