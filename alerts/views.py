# from django.shortcuts import render

# Create your views here.

import os
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .models import Alert, AlertDelivery
from locations.models import Device
from django.db import transaction, IntegrityError
from itertools import islice

from django.views.decorators.http import require_GET
from django.core.paginator import Paginator
from django.db.models import Count


def require_api_key(request) -> bool:
    expected = os.environ.get("API_KEY")
    provided = request.headers.get("X-API-Key")
    return bool(expected) and provided == expected


def chunked(iterable, size: int):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            return
        yield chunk

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


@csrf_exempt
@require_POST
def create_deliveries(request):
    # 認証
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # JSON parse
    try:
        data = json.loads(request.body.decode("utf-8"))
        alert_id = int(data["alert_id"])
        raw_device_ids = data.get("device_ids")
        bbox = data.get("bbox")
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    # Alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=400)

    created_device_ids: list[str] = []
    skipped_device_ids: list[str] = []

    # =========================
    # 1) device_ids 指定ルート
    # =========================
    if raw_device_ids is not None:
        if not isinstance(raw_device_ids, list):
            return JsonResponse({"ok": False, "error": "device_ids_must_be_list"}, status=400)

        device_ids = list(dict.fromkeys([str(x).strip() for x in raw_device_ids if str(x).strip()]))
        if not device_ids:
            return JsonResponse({"ok": False, "error": "invalid_device_ids"}, status=400)

        with transaction.atomic():
            # (A) Deviceを不足分だけ作成（競合は無視）
            Device.objects.bulk_create(
                [Device(device_id=did) for did in device_ids],
                ignore_conflicts=True,
                batch_size=1000,
            )

            # (B) 対象Deviceの (device_id, pk) を取得
            device_rows = list(
                Device.objects.filter(device_id__in=device_ids).values_list("device_id", "id")
            )
            device_id_to_pk = {did: pk for did, pk in device_rows}
            if not device_id_to_pk:
                return JsonResponse(
                    {
                        "ok": True,
                        "alert_id": alert.id,
                        "created": 0,
                        "skipped": 0,
                        "created_device_ids": [],
                        "skipped_device_ids": [],
                    },
                    status=200,
                )

            # (C) bulk_create 前に既に存在する delivery
            existed_before = set(
                AlertDelivery.objects.filter(
                    alert=alert,
                    device__device_id__in=device_ids,
                ).values_list("device__device_id", flat=True)
            )

            # (D) 未存在分だけ bulk_create
            to_create = []
            for did in device_ids:
                if did in existed_before:
                    continue
                pk = device_id_to_pk.get(did)
                if pk is None:
                    continue
                to_create.append(AlertDelivery(alert=alert, device_id=pk, status="sent"))

            if to_create:
                AlertDelivery.objects.bulk_create(
                    to_create,
                    ignore_conflicts=True,
                    batch_size=1000,
                )

            # ★① 確定ロジック：bulk_create 後に再取得
            existed_after = set(
                AlertDelivery.objects.filter(
                    alert=alert,
                    device__device_id__in=device_ids,
                ).values_list("device__device_id", flat=True)
            )

        # transaction外で created/skipped を確定
        for did in device_ids:
            if did in existed_before:
                skipped_device_ids.append(did)
            elif did in existed_after:
                created_device_ids.append(did)
            else:
                skipped_device_ids.append(did)

    # =========================
    # 2) bbox 指定ルート（③ chunk 化）
    # =========================
    elif bbox is not None:
        try:
            min_lat = float(bbox["min_lat"])
            max_lat = float(bbox["max_lat"])
            min_lon = float(bbox["min_lon"])
            max_lon = float(bbox["max_lon"])
        except (KeyError, TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "invalid_bbox"}, status=400)

        devices_qs = Device.objects.filter(
            last_latitude__isnull=False,
            last_longitude__isnull=False,
            last_latitude__gte=min_lat,
            last_latitude__lte=max_lat,
            last_longitude__gte=min_lon,
            last_longitude__lte=max_lon,
        ).values_list("id", "device_id")

        CHUNK_SIZE = 1000
        any_target = False

        for pairs in chunked(devices_qs.iterator(chunk_size=CHUNK_SIZE), CHUNK_SIZE):
            if not pairs:
                continue
            any_target = True

            device_pks = [pk for pk, _ in pairs]

            with transaction.atomic():
                # bulk_create 前
                existed_before_pks = set(
                    AlertDelivery.objects.filter(alert=alert, device_id__in=device_pks)
                    .values_list("device_id", flat=True)
                )

                to_create = [
                    AlertDelivery(alert=alert, device_id=pk, status="sent")
                    for pk, _ in pairs
                    if pk not in existed_before_pks
                ]
                if to_create:
                    AlertDelivery.objects.bulk_create(
                        to_create,
                        ignore_conflicts=True,
                        batch_size=CHUNK_SIZE,
                    )

                # ★① 確定ロジック：bulk_create 後
                existed_after_pks = set(
                    AlertDelivery.objects.filter(alert=alert, device_id__in=device_pks)
                    .values_list("device_id", flat=True)
                )

            for pk, did in pairs:
                if pk in existed_before_pks:
                    skipped_device_ids.append(did)
                elif pk in existed_after_pks:
                    created_device_ids.append(did)
                else:
                    skipped_device_ids.append(did)

        if not any_target:
            # bboxで対象0件
            return JsonResponse(
                {
                    "ok": True,
                    "alert_id": alert.id,
                    "created": 0,
                    "skipped": 0,
                    "created_device_ids": [],
                    "skipped_device_ids": [],
                },
                status=200,
            )

    else:
        return JsonResponse({"ok": False, "error": "device_ids_or_bbox_required"}, status=400)

    # =========================
    # 共通レスポンス
    # =========================
    status_code = 201 if len(skipped_device_ids) == 0 else 200
    return JsonResponse(
        {
            "ok": True,
            "alert_id": alert.id,
            "created": len(created_device_ids),
            "skipped": len(skipped_device_ids),
            "created_device_ids": created_device_ids,
            "skipped_device_ids": skipped_device_ids,
        },
        status=status_code,
    )


@csrf_exempt
@require_GET
def list_deliveries(request, alert_id: int):
    # 認証
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # alert existence
    try:
        alert = Alert.objects.get(id=int(alert_id))
    except (ValueError, Alert.DoesNotExist):
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=400)

    # クエリパラメータ
    status = request.GET.get("status")  # sent/responded/failed/None
    include_device = request.GET.get("include_device", "0") == "1"  # 1ならDevice情報も返す

    page = int(request.GET.get("page", "1"))
    page_size = int(request.GET.get("page_size", "200"))
    page_size = max(1, min(page_size, 1000))  # 安全のため上限

    qs = AlertDelivery.objects.filter(alert=alert).select_related("device").order_by("id")

    if status:
        if status not in ["sent", "responded", "failed"]:
            return JsonResponse({"ok": False, "error": "invalid_status"}, status=400)
        qs = qs.filter(status=status)

    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(page)

    items = []
    for d in page_obj.object_list:
        row = {
            "id": d.id,
            "device_id": d.device.device_id,
            "status": d.status,
            "sent_at": d.sent_at.isoformat() if d.sent_at else None,
            "responded_at": d.responded_at.isoformat() if d.responded_at else None,
        }
        if include_device:
            row["device"] = {
                "last_latitude": getattr(d.device, "last_latitude", None),
                "last_longitude": getattr(d.device, "last_longitude", None),
                "last_accuracy": getattr(d.device, "last_accuracy", None),
                "last_seen_at": getattr(d.device, "last_seen_at", None).isoformat()
                if getattr(d.device, "last_seen_at", None)
                else None,
            }
        items.append(row)

    return JsonResponse(
        {
            "ok": True,
            "alert": {
                "id": alert.id,
                "alert_type": alert.alert_type,
                "title": alert.title,
                "issued_at": alert.issued_at.isoformat(),
            },
            "count": paginator.count,
            "page": page_obj.number,
            "page_size": page_size,
            "num_pages": paginator.num_pages,
            "items": items,
        },
        status=200,
    )

@csrf_exempt
@require_GET
def alert_stats(request, alert_id: int):
    # 認証
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # Alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=400)

    # status別集計
    rows = (
        AlertDelivery.objects
        .filter(alert=alert)
        .values("status")
        .annotate(count=Count("id"))
    )
    counts = {r["status"]: r["count"] for r in rows}

    # 欠けがちなので0補完
    for s in ["sent", "responded", "failed"]:
        counts.setdefault(s, 0)

    total = counts["sent"] + counts["responded"] + counts["failed"]

    return JsonResponse(
        {
            "ok": True,
            "alert": {"id": alert.id, "title": alert.title, "alert_type": alert.alert_type},
            "counts": counts,
            "total": total,
        },
        status=200,
    )