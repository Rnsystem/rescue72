async function registerPush(deviceId, vapidPublicKey) {
  const reg = await navigator.serviceWorker.register("/sw.js");

  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("permission_denied");

  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
  });

  await fetch("/api/push/subscribe/", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": "dev-api-key-change-me",
    },
    body: JSON.stringify({
      device_id: deviceId,
      subscription: sub.toJSON(),
      user_agent: navigator.userAgent,
    }),
  });
}

// VAPIDのpublic key変換
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}