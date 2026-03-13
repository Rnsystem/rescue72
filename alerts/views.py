from django.shortcuts import render

# Create your views here.

import os
import json
import time
from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .models import Alert, AlertDelivery
from locations.models import Device
from groups.models import GroupDevice
from django.db import transaction, IntegrityError
from itertools import islice

from django.views.decorators.http import require_GET
from django.core.paginator import Paginator
from django.db.models import Count

from pywebpush import webpush, WebPushException
from push.models import PushSubscription

from django.conf import settings

from django.urls import reverse
import hmac
import hashlib
import base64

RESPONSE_CODES = {"safe", "need_help", "injured", "unknown"}


def _b64url_decode_nopad(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _generate_device_token(alert_id: int, device_id: str, expires_ts: int) -> str:
    message = f"{alert_id}:{device_id}:{expires_ts}"
    signature = hmac.new(
        settings.RESCUE72_TOKEN_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{alert_id}:{device_id}:{expires_ts}:{sig_b64}"


def _verify_device_token(token: str):
    """
    returns: (ok: bool, alert_id: int|None, device_id: str|None, error: str|None)
    """

    try:
        alert_id_s, device_id, expires_ts_s, sig_b64 = token.split(":", 3)
        alert_id = int(alert_id_s)
        expires_ts = int(expires_ts_s)
    except Exception:
        return False, None, None, "invalid_token"

    # 有効期限チェック
    now_ts = int(time.time())
    if expires_ts < now_ts:
        return False, None, None, "token_expired"

    # 署名再生成
    message = f"{alert_id}:{device_id}:{expires_ts}"
    expected = hmac.new(
        settings.RESCUE72_TOKEN_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()

    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")

    # timing-safe 比較
    if not hmac.compare_digest(expected_b64, sig_b64):
        return False, None, None, "bad_signature"

    return True, alert_id, device_id, None


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
                "expires_at": alert.expires_at.isoformat(),
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
        group_id = data.get("group_id")
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    # 「どれか1つだけ」指定させる
    specified = sum(x is not None for x in [raw_device_ids, bbox, group_id])
    if specified != 1:
        return JsonResponse(
            {"ok": False, "error": "exactly_one_of_device_ids_bbox_group_id_required"},
            status=400,
        )

    # Alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=400)

    created_device_ids: list[str] = []
    skipped_device_ids: list[str] = []

    # chunk サイズ（DB/運用に応じて調整。まずは1000が無難）
    CHUNK_SIZE = 1000

    # =========================
    # 1) device_ids 指定ルート
    # =========================
    if raw_device_ids is not None:
        if not isinstance(raw_device_ids, list):
            return JsonResponse({"ok": False, "error": "device_ids_must_be_list"}, status=400)

        # 重複排除 + strip + 空要素除外（順序維持）
        device_ids = list(dict.fromkeys([str(x).strip() for x in raw_device_ids if str(x).strip()]))
        if not device_ids:
            return JsonResponse({"ok": False, "error": "invalid_device_ids"}, status=400)

        with transaction.atomic():
            # (A) Deviceを不足分だけ作成（競合は無視）
            Device.objects.bulk_create(
                [Device(device_id=did) for did in device_ids],
                ignore_conflicts=True,
                batch_size=CHUNK_SIZE,
            )

            # (B) 対象Deviceの (device_id, pk) を取得
            device_rows = list(Device.objects.filter(device_id__in=device_ids).values_list("device_id", "id"))
            device_id_to_pk = {did: pk for did, pk in device_rows}
            if not device_id_to_pk:
                return JsonResponse(
                    {"ok": True, "alert_id": alert.id, "created": 0, "skipped": 0,
                     "created_device_ids": [], "skipped_device_ids": []},
                    status=200,
                )

            # (C) bulk_create 前の存在
            existed_before = set(
                AlertDelivery.objects.filter(alert=alert, device__device_id__in=device_ids)
                .values_list("device__device_id", flat=True)
            )

            # (D) 未存在だけ作成
            to_create = []
            for did in device_ids:
                if did in existed_before:
                    continue
                pk = device_id_to_pk.get(did)
                if pk is None:
                    continue
                to_create.append(AlertDelivery(alert=alert, device_id=pk, status="sent"))

            if to_create:
                AlertDelivery.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=CHUNK_SIZE)

            # ★① 確定：bulk_create後に再取得
            existed_after = set(
                AlertDelivery.objects.filter(alert=alert, device__device_id__in=device_ids)
                .values_list("device__device_id", flat=True)
            )

        # transaction外：created/skippedを確定
        for did in device_ids:
            if did in existed_before:
                skipped_device_ids.append(did)
            elif did in existed_after:
                created_device_ids.append(did)
            else:
                skipped_device_ids.append(did)

    # =========================
    # 2) bbox 指定ルート（chunk化済み）
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

        any_target = False
        for pairs in chunked(devices_qs.iterator(chunk_size=CHUNK_SIZE), CHUNK_SIZE):
            if not pairs:
                continue
            any_target = True

            device_pks = [pk for pk, _ in pairs]

            with transaction.atomic():
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
                    AlertDelivery.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=CHUNK_SIZE)

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
            return JsonResponse(
                {"ok": True, "alert_id": alert.id, "created": 0, "skipped": 0,
                 "created_device_ids": [], "skipped_device_ids": []},
                status=200,
            )

    # =========================
    # 3) group_id 指定ルート（NEW）
    # =========================
    else:
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=400)

        # groupに紐づくDevice一覧（pk, device_id）
        # ※GroupDeviceにDeviceがFKで居る前提
        devices_qs = GroupDevice.objects.filter(group_id=gid).select_related("device").values_list(
            "device_id",       # Deviceのpk
            "device__device_id"  # 文字列device_id（レスポンス用）
        )

        any_target = False
        for pairs in chunked(devices_qs.iterator(chunk_size=CHUNK_SIZE), CHUNK_SIZE):
            if not pairs:
                continue
            any_target = True

            device_pks = [pk for pk, _ in pairs]

            with transaction.atomic():
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
                    AlertDelivery.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=CHUNK_SIZE)

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
            return JsonResponse(
                {"ok": True, "alert_id": alert.id, "created": 0, "skipped": 0,
                 "created_device_ids": [], "skipped_device_ids": []},
                status=200,
            )

    # 共通レスポンス
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


@csrf_exempt
@require_POST
def respond_alert(request, alert_id: int):

    # 1) JSON parse を最初に（token判定に必要）
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    # 2) token があるなら token 認証（APIキー不要）
    token = str(data.get("token", "")).strip()

    if token:
        ok, t_alert_id, t_device_id, err = _verify_device_token(token)
        if not ok:
            return JsonResponse({"ok": False, "error": err}, status=401)

        # URLのalert_id と tokenのalert_id が一致するか
        if int(alert_id) != int(t_alert_id):
            return JsonResponse({"ok": False, "error": "token_alert_mismatch"}, status=401)

        device_id = t_device_id

    # 3) token が無いなら 従来通り APIキー必須
    else:
        if not require_api_key(request):
            return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

        device_id = str(data.get("device_id", "")).strip()

    if not device_id:
        return JsonResponse({"ok": False, "error": "device_id_required"}, status=400)

    # 旧互換：status が来たら優先（responded / failed）
    status = data.get("status", None)
    if status is not None:
        status = str(status).strip()

    # 新：4択
    response_code = str(data.get("response_code", "unknown")).strip()
    response_note = str(data.get("response_note", "")).strip()

    # status 検証（旧互換）
    if status is not None and status not in {"responded", "failed"}:
        return JsonResponse({"ok": False, "error": "invalid_status"}, status=400)

    # response_code 検証（新仕様）
    if response_code not in RESPONSE_CODES:
        return JsonResponse(
            {"ok": False, "error": "invalid_response_code", "allowed": sorted(RESPONSE_CODES)},
            status=400,
        )

    # delivery lookup（既存の AlertDelivery が前提）
    try:
        delivery = AlertDelivery.objects.select_related("device", "alert").get(
            alert_id=alert_id,
            device__device_id=device_id,
        )
    except AlertDelivery.DoesNotExist:
        return JsonResponse({"ok": False, "error": "delivery_not_found"}, status=404)

    # 位置情報（任意）を Device に保存
    loc = data.get("location")
    if isinstance(loc, dict):
        try:
            lat = float(loc.get("lat"))
            lon = float(loc.get("lon"))
            acc_raw = loc.get("accuracy")
            acc = float(acc_raw) if acc_raw is not None else None

            # 簡易バリデーション
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                Device.objects.filter(device_id=device_id).update(
                    last_latitude=lat,
                    last_longitude=lon,
                    last_accuracy=acc,
                    last_seen_at=timezone.now(),
                )
        except (TypeError, ValueError):
            pass

    now = timezone.now()
    changed = False

    # 旧 status が来た場合：failed は failed にする
    if status == "failed":
        if delivery.status != "failed":
            delivery.status = "failed"
            changed = True
    else:
        if delivery.status != "responded":
            delivery.status = "responded"
            changed = True
        if delivery.responded_at is None:
            delivery.responded_at = now
            changed = True

    if delivery.response_code != response_code:
        delivery.response_code = response_code
        changed = True

    if response_note and delivery.response_note != response_note:
        delivery.response_note = response_note
        changed = True

    if changed:
        delivery.save(update_fields=["status", "responded_at", "response_code", "response_note"])

    return JsonResponse(
        {
            "ok": True,
            "alert_id": alert_id,
            "device_id": device_id,
            "status": delivery.status,
            "response_code": delivery.response_code,
            "response_note": delivery.response_note,
            "responded_at": delivery.responded_at.isoformat() if delivery.responded_at else None,
            "changed": changed,
        },
        status=200,
    )


@csrf_exempt
@require_POST
def resolve_alert(request, alert_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        device_id = str(data["device_id"]).strip()
        resolved = bool(data.get("resolved", True))  # デフォルト True
        resolved_note = str(data.get("resolved_note", "")).strip()
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    if not device_id:
        return JsonResponse({"ok": False, "error": "device_id_required"}, status=400)

    try:
        delivery = AlertDelivery.objects.select_related("device", "alert").get(
            alert_id=alert_id,
            device__device_id=device_id,
        )
    except AlertDelivery.DoesNotExist:
        return JsonResponse({"ok": False, "error": "delivery_not_found"}, status=404)

    now = timezone.now()
    changed = False

    # Trueなら解決、Falseなら「未解決に戻す」も可能にする（運用で便利）
    if delivery.is_resolved != resolved:
        delivery.is_resolved = resolved
        changed = True

    if resolved:
        if delivery.resolved_at is None:
            delivery.resolved_at = now
            changed = True
        if resolved_note and delivery.resolved_note != resolved_note:
            delivery.resolved_note = resolved_note
            changed = True
    else:
        # 未解決に戻す場合
        if delivery.resolved_at is not None:
            delivery.resolved_at = None
            changed = True
        if resolved_note and delivery.resolved_note != resolved_note:
            delivery.resolved_note = resolved_note
            changed = True

    if changed:
        delivery.save(update_fields=["is_resolved", "resolved_at", "resolved_note"])

    return JsonResponse(
        {
            "ok": True,
            "alert_id": alert_id,
            "device_id": device_id,
            "is_resolved": delivery.is_resolved,
            "resolved_at": delivery.resolved_at.isoformat() if delivery.resolved_at else None,
            "resolved_note": delivery.resolved_note,
            "changed": changed,
        },
        status=200,
    )



def _send_webpush_or_mark_gone(sub: PushSubscription, payload: dict) -> tuple[bool, bool, str]:
    """
    returns: (sent_ok, deleted, error_message)
      - sent_ok: True if push success
      - deleted: True if subscription deleted due to 404/410
      - error_message: "" if ok
    """
    subscription_info = {
        "endpoint": sub.endpoint,
        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
    }

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
        )
        return True, False, ""
    except WebPushException as e:
        msg = str(e)
        # endpoint切れ（典型）→DBから消す
        if "410" in msg or "404" in msg:
            sub.delete()
            return False, True, msg
        return False, False, msg



def _push_to_prefecture(alert: Alert, prefecture_code: str, title: str, body: str, base_url: str) -> dict:
    devices_qs = Device.objects.filter(
        watch_prefecture_code=prefecture_code,
        push_subscription__isnull=False
    ).only("id", "device_id")
    targets = devices_qs.count()
    if targets == 0:
        return {
            "targets": 0,
            "created_deliveries": 0,
            "sent": 0,
            "no_subscription": 0,
            "deleted_subscription": 0,
            "failed": 0,
        }

    now = timezone.now()
    sent = 0
    no_sub = 0
    deleted = 0
    failed = 0
    created_deliveries = 0

    expires_ts = int(alert.expires_at.timestamp())

    for device in devices_qs.iterator():
        # ✅ deviceごとに token を作る（ここが重要）
        token = _generate_device_token(alert.id, device.device_id, expires_ts)

        # 通知タップで開くURL（絶対URL推奨）
        # 例: /answer/?token=... ではなく http://127.0.0.1:8000/answer/?token=...
        answer_url = f"{settings.BASE_URL}/answer/?token={token}"

        payload = {
            "title": title,
            "body": body,
            "url": answer_url,
            "meta": {
                "alert_id": alert.id,
                "prefecture_code": prefecture_code,
                "expires_at": alert.expires_at.isoformat() if getattr(alert, "expires_at", None) else None,
            },
        }

        # delivery 冪等作成
        with transaction.atomic():
            delivery, created = AlertDelivery.objects.get_or_create(
                alert=alert,
                device=device,
                defaults={"status": "sent", "sent_at": now},
            )
            if created:
                created_deliveries += 1

        # subscription 取得
        try:
            sub = PushSubscription.objects.get(device=device)
        except PushSubscription.DoesNotExist:
            no_sub += 1
            if delivery.status != "responded":
                AlertDelivery.objects.filter(id=delivery.id).update(status="failed")
            continue

        ok, was_deleted, err = _send_webpush_or_mark_gone(sub, payload)
        if ok:
            sent += 1
            if delivery.status != "responded":
                AlertDelivery.objects.filter(id=delivery.id).update(status="sent", sent_at=now)
        else:
            failed += 1
            if was_deleted:
                deleted += 1
            if delivery.status != "responded":
                AlertDelivery.objects.filter(id=delivery.id).update(status="failed")

    return {
        "targets": targets,
        "created_deliveries": created_deliveries,
        "sent": sent,
        "no_subscription": no_sub,
        "deleted_subscription": deleted,
        "failed": failed,
    }


@csrf_exempt
@require_POST
def push_prefecture(request, alert_id: int):
    """
    payload:
      {
        "prefecture_code": "37",
        "title": "Rescue72",
        "body": "地震が発生しました…"
      }
    """

    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        prefecture_code = str(data["prefecture_code"]).strip()
        title = str(data.get("title", "Rescue72")).strip()
        body = str(data.get("body", "")).strip()
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    if not prefecture_code:
        return JsonResponse({"ok": False, "error": "prefecture_code_required"}, status=400)

    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=404)

    result = _push_to_prefecture(alert, prefecture_code, title, body)

    return JsonResponse(
        {"ok": True, "alert_id": alert.id, "prefecture_code": prefecture_code, **result},
        status=200,
    )



@csrf_exempt
@require_POST
def ingest_disaster(request):
    """
    災害監視プロセスが叩く入口（本番は署名/認証を強化）
    payload:
      {
        "prefecture_code":"37",
        "alert_type":"eq",
        "title":"地震",
        "body":"震度6弱…",
        "expires_hours":72
      }
    """
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        prefecture_code = str(data["prefecture_code"]).strip()
        alert_type = str(data.get("alert_type", "unknown")).strip()
        title = str(data.get("title", "Disaster")).strip()
        body = str(data.get("body", "")).strip()
        expires_hours = int(data.get("expires_hours", 72))
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    if not prefecture_code:
        return JsonResponse({"ok": False, "error": "prefecture_code_required"}, status=400)

    now = timezone.now()
    expires_at = now + timedelta(hours=max(1, min(expires_hours, 72)))

    # Alert 作成
    alert = Alert.objects.create(
        title=title,
        alert_type=alert_type,
        issued_at=now,
        expires_at=expires_at,
    )

    base_url = request.build_absolute_uri("/")[:-1]  # 末尾の / を落とす

    push_result = _push_to_prefecture(
        alert=alert,
        prefecture_code=prefecture_code,
        title="Rescue72",
        body=body if body else "災害が発生しました。回答してください。",
        base_url=base_url,
    )

    return JsonResponse(
        {
            "ok": True,
            "alert": {
                "id": alert.id,
                "title": alert.title,
                "alert_type": alert.alert_type,
                "expires_at": alert.expires_at.isoformat(),
            },
            "prefecture_code": prefecture_code,
            "push_result": push_result,
        },
        status=201,
    )


@require_GET
def respond_page(request):
    # alert_id は ?alert_id=5 みたいに SW から来る想定
    alert_id = request.GET.get("alert_id", "")
    return render(request, "alerts/respond.html", {"alert_id": alert_id})