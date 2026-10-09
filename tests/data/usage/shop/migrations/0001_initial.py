from django.db import migrations, models


class Migration(migrations.Migration):
    operations = [
        migrations.AddField("product", "old", models.NullBooleanField()),
    ]
