from django.shortcuts import render

# Create your views here.

import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Device, Location

@csrf_exempt
@require_POST
def create_location(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        device_id = data["device_id"]
        latitude = float(data["latitude"])
        longitude = float(data["longitude"])
        accuracy = data.get("accuracy", None)
        if accuracy is not None:
            accuracy = float(accuracy)
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    device, _ = Device.objects.get_or_create(device_id=device_id)
    loc = Location.objects.create(
        device=device,
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
    )

    return JsonResponse({
        "ok": True,
        "location": {
            "id": loc.id,
            "device_id": device.device_id,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
            "accuracy": loc.accuracy,
            "recorded_at": loc.recorded_at.isoformat(),
        }
    }, status=201)