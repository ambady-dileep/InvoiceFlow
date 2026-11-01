from django.db import migrations, models

import apps.imports.models


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="upload",
            name="file",
            field=models.FileField(
                default="", upload_to=apps.imports.models.upload_file_path
            ),
            preserve_default=False,
        ),
    ]
