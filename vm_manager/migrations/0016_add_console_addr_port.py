# Generated by Django 3.2.16 on 2022-12-19 09:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vm_manager', '0015_backfill_backup_expiries'),
    ]

    operations = [
        migrations.AddField(
            model_name='instance',
            name='console_addr',
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='instance',
            name='console_port',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
