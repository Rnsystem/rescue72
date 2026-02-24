# from django.shortcuts import render

# Create your views here.

import os
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .models import Alert


def require_api_key(request) -> bool:
    expected = os.environ.get("API_KEY")
    provided = request.headers.get("X-API-Key")
    return bool(expected) and provided == expected


@csrf_exempt
@require_POST
def create_alert(request):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        alert_type = data["alert_type"]
        title = data["title"]
        message = data.get("message", "")
        issued_at_raw = data["issued_at"]

        if alert_type not in ["eq", "tsunami", "flood", "other"]:
            return JsonResponse({"ok": False, "error": "invalid_alert_type"}, status=400)

        dt = parse_datetime(issued_at_raw)
        if dt is None:
            return JsonResponse({"ok": False, "error": "invalid_issued_at"}, status=400)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())

    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    alert = Alert.objects.create(
        alert_type=alert_type,
        title=title,
        message=message,
        issued_at=dt,
    )

    return JsonResponse(
        {
            "ok": True,
            "alert": {
                "id": alert.id,
                "alert_type": alert.alert_type,
                "title": alert.title,
                "message": alert.message,
                "issued_at": alert.issued_at.isoformat(),
                "created_at": alert.created_at.isoformat(),
            },
        },
        status=201,
    )