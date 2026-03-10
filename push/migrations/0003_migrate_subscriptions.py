# push/migrations/0002_migrate_subscriptions.py
from django.db import migrations

def forwards(apps, schema_editor):
    Old = apps.get_model("push", "DevicePushSubscription")
    New = apps.get_model("push", "PushSubscription")

    for row in Old.objects.all():
        sub = row.subscription or {}
        endpoint = sub.get("endpoint")
        keys = sub.get("keys") or {}
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")
        if not (endpoint and p256dh and auth):
            continue

        # endpoint unique なので update_or_create 的に扱う
        New.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "device_id": row.device_id,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": getattr(row, "user_agent", "") or "",
            },
        )

def backwards(apps, schema_editor):
    # 戻しは不要ならpassでOK（運用では戻さないことが多い）
    pass

class Migration(migrations.Migration):
    dependencies = [
         ("push", "0002_pushsubscription"),
        # 0001の次に「新テーブル作成」があるならそれに合わせて調整
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]