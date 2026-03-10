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
from django.db import transaction

# 最低限の都道府県コード（運用で増やしてOK）
# ここは “入力バリデーション用” に置いておくと事故が減ります
PREFECTURES = {
    "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県", "05": "秋田県", "06": "山形県", "07": "福島県",
    "08": "茨城県", "09": "栃木県", "10": "群馬県", "11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県",
    "15": "新潟県", "16": "富山県", "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
    "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県",
    "25": "滋賀県", "26": "京都府", "27": "大阪府", "28": "兵庫県", "29": "奈良県", "30": "和歌山県",
    "31": "鳥取県", "32": "島根県", "33": "岡山県", "34": "広島県", "35": "山口県",
    "36": "徳島県", "37": "香川県", "38": "愛媛県", "39": "高知県",
    "40": "福岡県", "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県", "45": "宮崎県", "46": "鹿児島県",
    "47": "沖縄県",
}

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
        from alerts.models import Alert
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
    device.last_latitude = latitude
    device.last_longitude = longitude
    device.last_accuracy = accuracy
    device.last_seen_at = timezone.now()
    device.save(update_fields=["last_latitude", "last_longitude", "last_accuracy", "last_seen_at"])
    # source=alert のとき、配信ログを responded に更新（あれば）
    delivery_status = None
    if source == "alert" and alert is not None:
        delivery_status = "responded"
        from alerts.models import AlertDelivery  # 遅延import（循環回避）
        with transaction.atomic():
            qs = AlertDelivery.objects.select_for_update().filter(alert=alert, device=device)
            # 既存があるなら更新、なければ作成してrespondedにする（運用上安全）
            if qs.exists():
                delivery = qs.first()
                delivery.status = "responded"
                delivery.responded_at = timezone.now()
                delivery.save(update_fields=["status", "responded_at"])
            else:
                AlertDelivery.objects.create(
                    alert=alert,
                    device=device,
                    status="responded",
                    responded_at=timezone.now(),
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
            "delivery_status": delivery_status
        }
    }, status=201)


@csrf_exempt
@require_POST
def set_watch_area(request):
    # 既存の require_api_key を使う想定
    from .views import require_api_key  # 同一ファイル内なら不要。別なら調整してください。
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        device_id = str(data["device_id"]).strip()
        pref_code = str(data["prefecture_code"]).strip()
        pref_name = str(data.get("prefecture_name", "")).strip()
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    if not device_id:
        return JsonResponse({"ok": False, "error": "device_id_required"}, status=400)

    # 2桁に正規化（"37" or 37 を "37" に寄せる）
    if pref_code.isdigit():
        pref_code = pref_code.zfill(2)

    if pref_code not in PREFECTURES:
        return JsonResponse(
            {"ok": False, "error": "invalid_prefecture_code"},
            status=400,
        )

    # name は送られてこなければ辞書から補完
    if not pref_name:
        pref_name = PREFECTURES[pref_code]

    with transaction.atomic():
        device, _ = Device.objects.get_or_create(device_id=device_id)
        device.watch_prefecture_code = pref_code
        device.watch_prefecture_name = pref_name
        device.save(update_fields=["watch_prefecture_code", "watch_prefecture_name"])

    return JsonResponse(
        {
            "ok": True,
            "device": {
                "device_id": device.device_id,
                "watch_prefecture_code": device.watch_prefecture_code,
                "watch_prefecture_name": device.watch_prefecture_name,
            },
        },
        status=200,
    )