# Generated by Django 3.2.12 on 2022-08-25 06:02

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vm_manager', '0013_new_expiration_stages'),
    ]

    operations = [
        migrations.CreateModel(
            name='BackupExpiration',
            fields=[
                ('expiration_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='vm_manager.expiration')),
            ],
            bases=('vm_manager.expiration',),
        ),
        migrations.AddField(
            model_name='volume',
            name='backup_expiration',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='expiration_for', to='vm_manager.backupexpiration'),
        ),
    ]