# from django.shortcuts import render

# Create your views here.
import os
import json
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Device, Location
from alerts.models import Alert

def require_api_key(request) -> bool:
    expected = os.environ.get("API_KEY")
    provided = request.headers.get("X-API-Key")
    return bool(expected) and provided == expected

@csrf_exempt
@require_POST
def create_location(request):
    # 認証チェック
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = json.loads(request.body.decode("utf-8"))
        device_id = data["device_id"]
        latitude = float(data["latitude"])
        longitude = float(data["longitude"])
        accuracy = data.get("accuracy", None)
        if accuracy is not None:
            accuracy = float(accuracy)
        source = data.get("source", "manual")
        if source not in ["pre", "alert", "manual"]:
            return JsonResponse({"ok": False, "error": "invalid_source"}, status=400)
        captured_at = None
        captured_at_raw = data.get("captured_at")
        if captured_at_raw:
            dt = parse_datetime(captured_at_raw)
            if dt is None:
                return JsonResponse({"ok": False, "error": "invalid_captured_at"}, status=400)
            # naiveならサーバTZで解釈（クライアントはZ/offset付き推奨）
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            captured_at = dt
        alert_id = data.get("alert_id")
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)
    # source=alert の時だけ必須＆存在チェック
    alert = None
    if source == "alert":
        if not alert_id:
            return JsonResponse({"ok": False, "error": "alert_id_required"}, status=400)
        try:
            alert = Alert.objects.get(id=int(alert_id))
        except (ValueError, Alert.DoesNotExist):
            return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=400)
    device, _ = Device.objects.get_or_create(device_id=device_id)
    loc = Location.objects.create(
        device=device,
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
        source=source,
        captured_at=captured_at,
        alert=alert,
    )

    return JsonResponse({
        "ok": True,
        "location": {
            "id": loc.id,
            "device_id": device.device_id,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "accuracy": loc.accuracy,
            "source": loc.source,
            "alert_id": loc.alert_id,
            "recorded_at": loc.recorded_at.isoformat(),
            "captured_at": loc.captured_at.isoformat() if loc.captured_at else None,
        }
    }, status=201)