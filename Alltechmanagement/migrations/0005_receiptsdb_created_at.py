# Generated by Django 4.2.8 on 2024-02-15 11:40

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0004_saved_transactions_created_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='receiptsdb',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
