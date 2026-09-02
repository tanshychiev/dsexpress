from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_account_archived_at_account_is_archived_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="signature",
            field=models.ImageField(blank=True, null=True, upload_to="user_signatures/"),
        ),
    ]
