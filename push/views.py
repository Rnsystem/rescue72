from django.shortcuts import render
import os
import json
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.db import transaction

from pywebpush import webpush, WebPushException

from locations.models import Device
from .models import PushSubscription


def require_api_key(request) -> bool:
    expected = os.environ.get("API_KEY")
    provided = request.headers.get("X-API-Key")
    return bool(expected) and provided == expected


def _validate_subscription(sub: dict) -> bool:
    # WebPush subscription の最低限チェック
    if not isinstance(sub, dict):
        return False
    if not sub.get("endpoint"):
        return False
    keys = sub.get("keys")
    if not isinstance(keys, dict):
        return False
    if not keys.get("p256dh") or not keys.get("auth"):
        return False
    return True


@csrf_exempt
@require_POST
def subscribe(request):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        device_id = str(data["device_id"]).strip()
        subscription = data["subscription"]
        user_agent = str(data.get("user_agent", "")).strip()
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    if not device_id:
        return JsonResponse({"ok": False, "error": "device_id_required"}, status=400)

    if not _validate_subscription(subscription):
        return JsonResponse({"ok": False, "error": "invalid_subscription"}, status=400)

    endpoint = subscription["endpoint"]
    p256dh = subscription["keys"]["p256dh"]
    auth = subscription["keys"]["auth"]

    with transaction.atomic():
        device, _ = Device.objects.get_or_create(device_id=device_id)

        # この endpoint が他の device に紐づいていたら削除（付け替えでもOK）
        PushSubscription.objects.filter(endpoint=endpoint).exclude(device=device).delete()

        # ★ OneToOne なので device をキーに upsert するのが正解
        obj, created = PushSubscription.objects.update_or_create(
            device=device,
            defaults={
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": user_agent,
            },
        )

    return JsonResponse(
        {"ok": True, "device_id": device.device_id, "created": created},
        status=201 if created else 200,
    )


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def unsubscribe(request):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    device_id = ""

    # 1) まず body(JSON) を試す（POST/DELETEどちらでも）
    if request.body:
        try:
            data = json.loads(request.body.decode("utf-8"))
            device_id = str(data.get("device_id", "")).strip()
        except (ValueError, json.JSONDecodeError, TypeError):
            return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    # 2) body が無い/取れない場合は querystring も許可
    if not device_id:
        device_id = str(request.GET.get("device_id", "")).strip()

    if not device_id:
        return JsonResponse({"ok": False, "error": "device_id_required"}, status=400)

    deleted = 0
    try:
        device = Device.objects.get(device_id=device_id)
        deleted, _ = PushSubscription.objects.filter(device=device).delete()
    except Device.DoesNotExist:
        deleted = 0

    return JsonResponse({"ok": True, "device_id": device_id, "deleted": deleted}, status=200)


@require_GET
def setup_page(request):
    return render(request, "push/setup.html")


@require_GET
def vapid_public_key(request):
    # フロントが applicationServerKey に使う
    return JsonResponse({"ok": True, "vapid_public_key": settings.VAPID_PUBLIC_KEY}, status=200)


@require_GET
def service_worker(request):
    js = r"""
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(clients.claim());
});

self.addEventListener("push", (event) => {
  event.waitUntil((async () => {
    let data = { title: "Rescue72", body: "", url: "/answer/" };

    try {
      if (event.data) data = await event.data.json(); // ★await必須
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
  console.log("notificationclick", event.notification?.data); // ★ログ
  event.notification.close();

  const url = event.notification?.data?.url || "/answer/";

  event.waitUntil((async () => {
    const allClients = await clients.matchAll({ type: "window", includeUncontrolled: true });

    for (const client of allClients) {
      if ("focus" in client) {
        await client.focus();
        if ("navigate" in client) {
          await client.navigate(url);
        } else {
          return clients.openWindow(url);
        }
        return;
      }
    }
    return clients.openWindow(url);
  })());
});
"""
    resp = HttpResponse(js, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    return resp


@csrf_exempt
@require_POST
def send_push(request):
    """
    1台の device_id 宛にPush送信（まずはここから）
    payload:
      { "device_id": "...", "title": "...", "body": "..." }
    """
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        device_id = str(data["device_id"]).strip()
        title = str(data.get("title", "Rescue72")).strip()
        body = str(data.get("body", "")).strip()
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    if not device_id:
        return JsonResponse({"ok": False, "error": "device_id_required"}, status=400)

    # device/subscription 取得
    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        return JsonResponse({"ok": False, "error": "device_not_found"}, status=404)

    try:
        sub = PushSubscription.objects.get(device=device)
    except PushSubscription.DoesNotExist:
        return JsonResponse({"ok": False, "error": "subscription_not_found"}, status=404)

    subscription_info = {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
    }

    payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)

    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
        )
    except WebPushException as e:
        # 典型: endpoint切れ、権限剥奪など
        # 必要ならここでsub削除も検討（運用方針次第）
        msg = str(e)
        if "410" in msg or "404" in msg:
            sub.delete()
            return JsonResponse({"ok": False, "error": "subscription_gone_deleted"}, status=410)
        return JsonResponse({"ok": False, "error": "webpush_failed", "detail": msg}, status=502)

    return JsonResponse({"ok": True, "device_id": device_id, "sent": True}, status=200)


@require_GET
def respond_page(request):
    return render(request, "push/respond.html")


def answer_page(request):
    token = request.GET.get("token", "")
    return render(request, "push/answer.html", {"token": token})