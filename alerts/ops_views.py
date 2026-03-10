# alerts/ops_views.py
import csv
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.db.models import Count, Q
from django.contrib.admin.views.decorators import staff_member_required

from .models import Alert, AlertDelivery

from django.views.decorators.http import require_POST
from django.utils import timezone
from django.shortcuts import redirect

from urllib.parse import urljoin

@staff_member_required
def ops_alert_list(request):
    """
    /ops/alerts/
    - 最新アラート一覧
    - total / sent / responded / failed / pending
    - 絞り込み: alert_type, "only_active"
    """
    alert_type = request.GET.get("type", "").strip()  # eq/tsunami/...
    only_active = request.GET.get("only_active", "1") == "1"  # デフォルトON

    qs = Alert.objects.all().order_by("-id")

    if alert_type:
        qs = qs.filter(alert_type=alert_type)

    if only_active:
        qs = qs.filter(expires_at__gt=timezone.now())

    qs = qs.annotate(
        total_count=Count("deliveries", distinct=True),
        sent_count=Count("deliveries", filter=Q(deliveries__status="sent"), distinct=True),
        responded_count=Count("deliveries", filter=Q(deliveries__status="responded"), distinct=True),
        failed_count=Count("deliveries", filter=Q(deliveries__status="failed"), distinct=True),
    )

    rows = []
    for a in qs[:200]:
        total = a.total_count or 0
        responded = a.responded_count or 0
        failed = a.failed_count or 0
        pending = max(total - responded - failed, 0)
        rows.append(
            {
                "alert": a,
                "total": total,
                "sent": a.sent_count or 0,
                "responded": responded,
                "failed": failed,
                "pending": pending,
            }
        )

    # フィルタ用（雑に固定値でOK）
    alert_types = sorted(set(Alert.objects.values_list("alert_type", flat=True)))

    return render(
        request,
        "alerts/ops_alert_list.html",
        {
            "rows": rows,
            "alert_types": alert_types,
            "selected_type": alert_type,
            "only_active": only_active,
        },
    )


@staff_member_required
def ops_alert_detail(request, alert_id: int):
    """
    /ops/alerts/<id>/
    - deliveries 一覧（ページング）
    - statusフィルタ
    """
    alert = get_object_or_404(Alert, id=alert_id)

    status = request.GET.get("status", "").strip()  # sent/responded/failed
    page_size = int(request.GET.get("page_size", "200"))
    page_size = max(1, min(page_size, 1000))
    page = int(request.GET.get("page", "1"))
    page = max(page, 1)

    qs = (
        AlertDelivery.objects.filter(alert=alert)
        .select_related("device")
        .order_by("id")
    )
    if status in ("sent", "responded", "failed"):
        qs = qs.filter(status=status)

    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    items = list(qs[start:end])

    # stats
    agg = (
        AlertDelivery.objects.filter(alert=alert)
        .values("status")
        .annotate(c=Count("id"))
    )
    counts = {r["status"]: r["c"] for r in agg}
    for k in ["sent", "responded", "failed"]:
        counts.setdefault(k, 0)
    total_all = counts["sent"] + counts["responded"] + counts["failed"]
    pending = max(total_all - counts["responded"] - counts["failed"], 0)

    num_pages = (total + page_size - 1) // page_size

    return render(
        request,
        "alerts/ops_alert_detail.html",
        {
            "alert": alert,
            "items": items,
            "status": status,
            "page": page,
            "page_size": page_size,
            "num_pages": num_pages,
            "total": total,
            "counts": counts,
            "pending": pending,
        },
    )


@staff_member_required
def ops_alert_deliveries_csv(request, alert_id: int):
    """
    /ops/alerts/<id>/deliveries.csv
    """
    alert = get_object_or_404(Alert, id=alert_id)

    qs = (
        AlertDelivery.objects.filter(alert=alert)
        .select_related("device")
        .order_by("id")
    )

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="alert_{alert_id}_deliveries.csv"'

    w = csv.writer(resp)
    w.writerow(["delivery_id", "device_id", "status", "response_code", "response_note", "sent_at", "responded_at", "is_resolved", "resolved_at", "resolved_note"])

    for d in qs.iterator(chunk_size=2000):
        w.writerow(
            [
                d.id,
                getattr(d.device, "device_id", ""),
                d.status,
                getattr(d, "response_code", ""),
                getattr(d, "response_note", ""),
                d.sent_at.isoformat() if d.sent_at else "",
                d.responded_at.isoformat() if d.responded_at else "",
                getattr(d, "is_resolved", False),
                getattr(d, "resolved_at", None).isoformat() if getattr(d, "resolved_at", None) else "",
                getattr(d, "resolved_note", ""),
            ]
        )
    return resp


@staff_member_required
@require_POST
def ops_delivery_resolve(request, alert_id: int):
    delivery_id = request.POST.get("delivery_id", "").strip()
    resolved_s = request.POST.get("resolved", "1").strip()
    note = request.POST.get("note", "").strip()

    # ★ここを修正
    next_url = (request.POST.get("next", "") or "").strip()
    default_detail = f"/ops/alerts/{alert_id}/"

    if not next_url:
        next_url = default_detail
    elif next_url.startswith("?"):
        # クエリだけ来たら、詳細URLにくっつける
        next_url = default_detail + next_url
    elif not (next_url.startswith("/") or next_url.startswith("http://") or next_url.startswith("https://")):
        # 念のため：変なのが来たら安全側へ
        next_url = default_detail

    # ---- ここから下は今のままでOK ----
    if not delivery_id.isdigit():
        raise Http404("invalid delivery_id")

    delivery = get_object_or_404(AlertDelivery, id=int(delivery_id), alert_id=alert_id)
    resolved = resolved_s in ("1", "true", "True", "on")

    changed = False
    now = timezone.now()

    if delivery.is_resolved != resolved:
        delivery.is_resolved = resolved
        changed = True

    if resolved:
        if delivery.resolved_at is None:
            delivery.resolved_at = now
            changed = True
        if note and delivery.resolved_note != note:
            delivery.resolved_note = note
            changed = True
    else:
        if delivery.resolved_at is not None:
            delivery.resolved_at = None
            changed = True
        if note and delivery.resolved_note != note:
            delivery.resolved_note = note
            changed = True

    if changed:
        delivery.save(update_fields=["is_resolved", "resolved_at", "resolved_note"])

    return redirect(next_url)


@staff_member_required
def ops_alert_export_csv(request, alert_id: int):
    alert = get_object_or_404(Alert, id=alert_id)

    # 既定：未回答（sent）のみ
    status = request.GET.get("status", "sent").strip()  # sent/responded/failed/all
    qs = AlertDelivery.objects.filter(alert=alert).select_related("device").order_by("id")

    if status in ("sent", "responded", "failed"):
        qs = qs.filter(status=status)
    elif status == "all":
        pass
    else:
        status = "sent"
        qs = qs.filter(status="sent")

    filename = f"rescue72_alert_{alert.id}_{status}.csv"
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    # Excel対策（必要なら）：resp.write("\ufeff")
    writer = csv.writer(resp)
    writer.writerow([
        "alert_id",
        "alert_title",
        "delivery_id",
        "device_id",
        "status",
        "response_code",
        "response_note",
        "sent_at",
        "responded_at",
        "is_resolved",
        "resolved_at",
        "resolved_note",
    ])

    for d in qs:
        writer.writerow([
            alert.id,
            alert.title,
            d.id,
            d.device.device_id,
            d.status,
            getattr(d, "response_code", ""),
            getattr(d, "response_note", ""),
            d.sent_at.isoformat() if d.sent_at else "",
            d.responded_at.isoformat() if d.responded_at else "",
            getattr(d, "is_resolved", False),
            getattr(d, "resolved_at", None).isoformat() if getattr(d, "resolved_at", None) else "",
            getattr(d, "resolved_note", ""),
        ])

    return resp