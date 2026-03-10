# from django.shortcuts import render

# Create your views here.

import os
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.db import transaction, IntegrityError

from .models import DeviceGroup, GroupMember, GroupDevice
from django.contrib.auth import get_user_model
from locations.models import Device

from django.core.paginator import Paginator
from django.db.models import Count
from alerts.models import Alert, AlertDelivery
from django.utils import timezone

User = get_user_model()


def require_api_key(request) -> bool:
    expected = os.environ.get("API_KEY")
    provided = request.headers.get("X-API-Key")
    return bool(expected) and provided == expected


@csrf_exempt
@require_POST
def create_group(request):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        name = str(data["name"]).strip()
        owner_username = str(data.get("owner_username", "")).strip()
        if not name:
            return JsonResponse({"ok": False, "error": "name_required"}, status=400)
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    owner = None
    if owner_username:
        try:
            owner = User.objects.get(username=owner_username)
        except User.DoesNotExist:
            return JsonResponse({"ok": False, "error": "invalid_owner_username"}, status=400)

    with transaction.atomic():
        group = DeviceGroup.objects.create(name=name, owner=owner)

        # owner がいるなら自動で member に入れる（便利）
        if owner is not None:
            GroupMember.objects.get_or_create(group=group, user=owner)

    return JsonResponse(
        {"ok": True, "group": {"id": group.id, "name": group.name}},
        status=201,
    )


@csrf_exempt
@require_POST
def join_group(request, group_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        username = str(data["username"]).strip()
        if not username:
            return JsonResponse({"ok": False, "error": "username_required"}, status=400)
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    try:
        group = DeviceGroup.objects.get(id=group_id)
    except DeviceGroup.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=404)

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_username"}, status=400)

    obj, created = GroupMember.objects.get_or_create(group=group, user=user)
    return JsonResponse(
        {"ok": True, "group_id": group.id, "username": user.username, "joined": created},
        status=201 if created else 200,
    )


@csrf_exempt
@require_POST
def add_devices(request, group_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
        raw_device_ids = data["device_ids"]
        if not isinstance(raw_device_ids, list):
            return JsonResponse({"ok": False, "error": "device_ids_must_be_list"}, status=400)

        # 重複排除 + strip + 空除外（順序維持）
        device_ids = list(dict.fromkeys([str(x).strip() for x in raw_device_ids if str(x).strip()]))
        if not device_ids:
            return JsonResponse({"ok": False, "error": "invalid_device_ids"}, status=400)
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    try:
        group = DeviceGroup.objects.get(id=group_id)
    except DeviceGroup.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=404)

    created = []
    skipped = []

    with transaction.atomic():
        # Device を不足分だけまとめて作成
        Device.objects.bulk_create(
            [Device(device_id=did) for did in device_ids],
            ignore_conflicts=True,
            batch_size=1000,
        )

        # 対象 Device の PK をまとめて取得
        rows = list(Device.objects.filter(device_id__in=device_ids).values_list("device_id", "id"))
        did_to_pk = {did: pk for did, pk in rows}

        # 既に group に居る device を取得（device_id文字列で判定）
        existed_before = set(
            GroupDevice.objects.filter(group=group, device__device_id__in=device_ids)
            .values_list("device__device_id", flat=True)
        )

        # 未存在だけ追加
        to_create = []
        for did in device_ids:
            if did in existed_before:
                continue
            pk = did_to_pk.get(did)
            if pk is None:
                continue
            to_create.append(GroupDevice(group=group, device_id=pk))

        if to_create:
            GroupDevice.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=1000)

        existed_after = set(
            GroupDevice.objects.filter(group=group, device__device_id__in=device_ids)
            .values_list("device__device_id", flat=True)
        )

    for did in device_ids:
        if did in existed_before:
            skipped.append(did)
        elif did in existed_after:
            created.append(did)
        else:
            skipped.append(did)

    status_code = 201 if not skipped else 200
    return JsonResponse(
        {
            "ok": True,
            "group_id": group.id,
            "created": len(created),
            "skipped": len(skipped),
            "created_device_ids": created,
            "skipped_device_ids": skipped,
        },
        status=status_code,
    )


@csrf_exempt
@require_GET
def list_group_devices(request, group_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    try:
        group = DeviceGroup.objects.get(id=group_id)
    except DeviceGroup.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=404)

    device_ids = list(
        GroupDevice.objects.filter(group=group)
        .select_related("device")
        .values_list("device__device_id", flat=True)
        .order_by("device__device_id")
    )

    return JsonResponse(
        {"ok": True, "group": {"id": group.id, "name": group.name}, "count": len(device_ids), "device_ids": device_ids},
        status=200,
    )


@csrf_exempt
@require_GET
def group_alert_stats(request, group_id: int, alert_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # Alert existence（分かりやすいエラーにする）
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=404)

    # グループ内デバイスに限定した deliveries
    qs = AlertDelivery.objects.filter(
        alert_id=alert_id,
        device__group_links__group_id=group_id,  # GroupDevice.related_name="group_links" を利用
    )

    rows = qs.values("status").annotate(c=Count("id"))
    counts = {"sent": 0, "responded": 0, "failed": 0}
    total = 0
    for r in rows:
        s = r["status"]
        c = int(r["c"])
        if s in counts:
            counts[s] = c
        total += c

    return JsonResponse(
        {
            "ok": True,
            "group": {"id": group_id},
            "alert": {"id": alert.id, "title": alert.title, "alert_type": alert.alert_type},
            "counts": counts,
            "total": total,
        },
        status=200,
    )


@csrf_exempt
@require_GET
def group_alert_deliveries(request, group_id: int, alert_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # Alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=404)

    # optional filters
    status = request.GET.get("status")
    try:
        page = int(request.GET.get("page", "1"))
        page_size = int(request.GET.get("page_size", "200"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_pagination"}, status=400)

    page_size = max(1, min(page_size, 1000))  # 上限はひとまず1000

    qs = AlertDelivery.objects.filter(
        alert_id=alert_id,
        device__group_links__group_id=group_id,
    ).select_related("device")

    if status:
        if status not in {"sent", "responded", "failed"}:
            return JsonResponse({"ok": False, "error": "invalid_status"}, status=400)
        qs = qs.filter(status=status)

    qs = qs.order_by("-sent_at", "-id")

    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(page)

    items = []
    for d in page_obj.object_list:
        items.append(
            {
                "id": d.id,
                "device_id": d.device.device_id,
                "status": d.status,
                "sent_at": d.sent_at.isoformat(),
                "responded_at": d.responded_at.isoformat() if d.responded_at else None,
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "group": {"id": group_id},
            "alert": {"id": alert.id, "alert_type": alert.alert_type, "title": alert.title, "issued_at": alert.issued_at.isoformat()},
            "count": paginator.count,
            "page": page_obj.number,
            "page_size": page_size,
            "num_pages": paginator.num_pages,
            "items": items,
        },
        status=200,
    )


@csrf_exempt
@require_POST
def create_group_deliveries(request, group_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # payload
    try:
        data = json.loads(request.body.decode("utf-8"))
        alert_id = int(data["alert_id"])
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_payload"}, status=400)

    # group existence
    try:
        group = DeviceGroup.objects.get(id=group_id)
    except DeviceGroup.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=404)

    # alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=404)

    created_device_ids: list[str] = []
    skipped_device_ids: list[str] = []

    # グループ内デバイス（(pk, device_id) で取得）
    pairs = list(
        GroupDevice.objects.filter(group=group)
        .select_related("device")
        .values_list("device_id", "device__device_id")
    )

    if not pairs:
        return JsonResponse(
            {
                "ok": True,
                "group": {"id": group.id, "name": group.name},
                "alert_id": alert.id,
                "created": 0,
                "skipped": 0,
                "created_device_ids": [],
                "skipped_device_ids": [],
            },
            status=200,
        )

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
            AlertDelivery.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=1000)

        existed_after_pks = set(
            AlertDelivery.objects.filter(alert=alert, device_id__in=device_pks)
            .values_list("device_id", flat=True)
        )

    # created/skipped確定（device_id文字列を返す）
    for pk, did in pairs:
        if pk in existed_before_pks:
            skipped_device_ids.append(did)
        elif pk in existed_after_pks:
            created_device_ids.append(did)
        else:
            skipped_device_ids.append(did)

    status_code = 201 if not skipped_device_ids else 200
    return JsonResponse(
        {
            "ok": True,
            "group": {"id": group.id, "name": group.name},
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
def group_alert_triage(request, group_id: int, alert_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # group existence（エラーを分かりやすく）
    try:
        group = DeviceGroup.objects.get(id=group_id)
    except DeviceGroup.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=404)

    # alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=404)

    # グループ内デバイスの、そのalertのdelivery
    qs = AlertDelivery.objects.filter(
        alert_id=alert_id,
        device__group_links__group_id=group_id,
    ).select_related("device")

    now = timezone.now()
    expires_at = alert.expires_at

    # 優先度ロジック（運用で調整しやすい固定）
    def level(delivery: AlertDelivery, rem_sec: int) -> str:
        if rem_sec <= 0:
            return "expired"

        # failed は未達/失敗なので高優先（ここは好みで high or critical）
        if delivery.status == "failed":
            return "high"

        code = getattr(delivery, "response_code", None) or "unknown"

        if code == "injured":
            return "critical"
        if code == "need_help":
            return "high"
        if code == "unknown":
            return "mid"
        if code == "safe":
            return "low"
        return "mid"

    items = []
    for d in qs.order_by("sent_at", "id"):
        rem = int((expires_at - now).total_seconds())
        lv = level(d, rem)
        items.append(
            {
                "device_id": d.device.device_id,
                "status": d.status,
                "response_code": getattr(d, "response_code", None),
                "response_note": getattr(d, "response_note", None),
                "sent_at": d.sent_at.isoformat(),
                "responded_at": d.responded_at.isoformat() if d.responded_at else None,
                "remaining_seconds": rem,
                "level": lv,
            }
        )

    # 並び替え（優先度→残り時間→送信時刻）
    priority = {"critical": 0, "high": 1, "mid": 2, "low": 3, "expired": 4}
    items.sort(key=lambda x: (priority.get(x["level"], 9), x["remaining_seconds"], x["sent_at"]))

    # counts（優先度＋ステータス）
    counts_level = {"expired": 0, "critical": 0, "high": 0, "mid": 0, "low": 0}
    counts_status = {"sent": 0, "responded": 0, "failed": 0}

    for it in items:
        lv = it["level"]
        st = it["status"]
        if lv in counts_level:
            counts_level[lv] += 1
        if st in counts_status:
            counts_status[st] += 1

    # 返す件数（まずは上位200だけ）
    return JsonResponse(
        {
            "ok": True,
            "group": {"id": group.id, "name": group.name},
            "alert": {"id": alert.id, "title": alert.title, "alert_type": alert.alert_type},
            "expires_at": expires_at.isoformat(),
            "counts": {**counts_level, **counts_status},
            "items": items[:200],
        },
        status=200,
    )


@csrf_exempt
@require_GET
def group_alert_summary(request, group_id: int, alert_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # group existence
    try:
        group = DeviceGroup.objects.get(id=group_id)
    except DeviceGroup.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=404)

    # alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=404)

    # triage と同じ母集団
    qs = AlertDelivery.objects.filter(
        alert_id=alert_id,
        device__group_links__group_id=group_id,
    ).select_related("device")

    now = timezone.now()
    expires_at = alert.expires_at
    remaining_seconds = int((expires_at - now).total_seconds())

    # 件数
    status_rows = qs.values("status").annotate(c=Count("id"))
    status_counts = {"sent": 0, "responded": 0, "failed": 0}
    for r in status_rows:
        s = r["status"]
        if s in status_counts:
            status_counts[s] = int(r["c"])

    # need_help / injured / safe / unknown の内訳（respondedのもの中心に見る）
    # response_code が無い古い行もあるので null も拾う
    code_rows = qs.values("response_code").annotate(c=Count("id"))
    code_counts = {"injured": 0, "need_help": 0, "safe": 0, "unknown": 0}

    for r in code_rows:
        code = r["response_code"] or "unknown"   # ★Noneはunknownへ
        c = int(r["c"])
        if code not in code_counts:
            code = "unknown"
        code_counts[code] += c

    # 緊急上位（最大10件）
    priority = {"injured": 0, "need_help": 1, "unknown": 2, "safe": 3}

    def code_key(d: AlertDelivery):
        code = getattr(d, "response_code", None) or "unknown"
        return priority.get(code, 9)

    # responded の中で injured/need_help を優先して並べる
    top = []
    for d in qs.filter(status="responded").order_by("responded_at", "id"):
        top.append(d)
    top.sort(key=lambda d: (code_key(d), d.responded_at or now))
    top = top[:10]

    top_items = []
    for d in top:
        code = (getattr(d, "response_code", None) or "unknown")
        note = (getattr(d, "response_note", None) or "").strip()
        top_items.append(
            {
                "device_id": d.device.device_id,
                "response_code": code,
                "response_note": note,
                "responded_at": d.responded_at.isoformat() if d.responded_at else None,
            }
        )

    # 通知文（とりあえず日本語）
    # ※ここは将来、テンプレ/多言語/Slack通知などに拡張しやすい
    headline = f"[Rescue72] {group.name} / {alert.title} 状況まとめ"
    body_lines = [
        f"残り時間: {max(0, remaining_seconds)//3600}h",
        f"ステータス: responded={status_counts['responded']} / sent={status_counts['sent']} / failed={status_counts['failed']}",
        f"回答内訳: injured={code_counts['injured']}, need_help={code_counts['need_help']}, safe={code_counts['safe']}, unknown={code_counts['unknown']}",
    ]
    unresolved = qs.filter(
        status="responded",
        response_code__in=["injured", "need_help"],
        is_resolved=False,
    ).count()
    body_lines.insert(2, f"未解決（injured/need_help）: {unresolved}")
    if top_items:
        body_lines.append("優先確認（上位）:")
        for it in top_items:
            note = (it["response_note"] or "").strip()
            note_part = f" - {note}" if note else ""
            body_lines.append(f"  • {it['device_id']}: {it['response_code']}{note_part}")
    else:
        body_lines.append("優先確認（上位）: なし")

    return JsonResponse(
        {
            "ok": True,
            "group": {"id": group.id, "name": group.name},
            "alert": {"id": alert.id, "title": alert.title, "alert_type": alert.alert_type},
            "expires_at": expires_at.isoformat(),
            "counts": {"status": status_counts, "response_code": code_counts, "unresolved": unresolved},
            "top": top_items,
            "message": {
                "headline": headline,
                "body": "\n".join(body_lines),
            },
        },
        status=200,
    )


@csrf_exempt
@require_GET
def group_alert_notify(request, group_id: int, alert_id: int):
    if not require_api_key(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    # group existence
    try:
        group = DeviceGroup.objects.get(id=group_id)
    except DeviceGroup.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_group_id"}, status=404)

    # alert existence
    try:
        alert = Alert.objects.get(id=alert_id)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "invalid_alert_id"}, status=404)

    qs = AlertDelivery.objects.filter(
        alert_id=alert_id,
        device__group_links__group_id=group_id,
    ).select_related("device")

    now = timezone.now()
    expires_at = alert.expires_at
    remaining_seconds = int((expires_at - now).total_seconds())
    remaining_h = max(0, remaining_seconds) // 3600

    # status counts
    status_rows = qs.values("status").annotate(c=Count("id"))
    status_counts = {"sent": 0, "responded": 0, "failed": 0}
    for r in status_rows:
        s = r["status"]
        if s in status_counts:
            status_counts[s] = int(r["c"])

    # response_code counts（Noneはunknownへ寄せる）
    code_rows = qs.values("response_code").annotate(c=Count("id"))
    code_counts = {"injured": 0, "need_help": 0, "safe": 0, "unknown": 0}
    for r in code_rows:
        code = r["response_code"] or "unknown"
        if code not in code_counts:
            code = "unknown"
        code_counts[code] += int(r["c"])

    # unresolved（injured/need_help かつ 未解決）
    unresolved = qs.filter(
        status="responded",
        response_code__in=["injured", "need_help"],
        is_resolved=False,
    ).count()

    # 優先リスト（injured/need_help & 未解決を上位）
    priority = {"injured": 0, "need_help": 1, "unknown": 2, "safe": 3}

    def sort_key(d: AlertDelivery):
        code = getattr(d, "response_code", None) or "unknown"
        pr = priority.get(code, 9)
        # 未解決を上へ
        unresolved_rank = 0 if (code in {"injured", "need_help"} and not d.is_resolved) else 1
        t = d.responded_at or d.sent_at
        return (unresolved_rank, pr, t, d.id)

    top_qs = qs.filter(status="responded").order_by("responded_at", "id")
    top_list = list(top_qs)
    top_list.sort(key=sort_key)
    top_list = top_list[:10]

    top_lines = []
    for d in top_list:
        code = getattr(d, "response_code", None) or "unknown"
        note = (getattr(d, "response_note", None) or "").strip()
        resolved_mark = "✅" if d.is_resolved else "⚠️" if code in {"injured", "need_help"} else "•"
        note_part = f" - {note}" if note else ""
        top_lines.append(f"{resolved_mark} {d.device.device_id}: {code}{note_part}")

    headline = f"[Rescue72] {group.name} / {alert.title} 通知"
    body_lines = [
        f"残り時間: {remaining_h}h",
        f"ステータス: responded={status_counts['responded']} / sent={status_counts['sent']} / failed={status_counts['failed']}",
        f"未解決（injured/need_help）: {unresolved}",
        f"回答内訳: injured={code_counts['injured']}, need_help={code_counts['need_help']}, safe={code_counts['safe']}, unknown={code_counts['unknown']}",
        "優先確認（上位）:",
    ]
    if top_lines:
        body_lines.extend([f"  {ln}" for ln in top_lines])
    else:
        body_lines.append("  なし")

    body = "\n".join(body_lines)

    # Slack/汎用Webhookへ流しやすい形（後で送信に使える）
    payload = {
        "text": headline,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": headline}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{body}```"}},
        ],
        "meta": {
            "group_id": group.id,
            "alert_id": alert.id,
            "expires_at": expires_at.isoformat(),
            "counts": {
                "status": status_counts,
                "response_code": code_counts,
                "unresolved": unresolved,
            },
        },
    }

    return JsonResponse(
        {
            "ok": True,
            "group": {"id": group.id, "name": group.name},
            "alert": {"id": alert.id, "title": alert.title, "alert_type": alert.alert_type},
            "expires_at": expires_at.isoformat(),
            "headline": headline,
            "body": body,
            "payload": payload,
        },
        status=200,
    )