# Generated by Django 4.2.8 on 2024-11-01 09:27

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0034_remove_customuser_groups_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='COMPLETED_TRANSACTIONS_FIX',
        ),
        migrations.DeleteModel(
            name='HOME_STOCK_FIX',
        ),
        migrations.DeleteModel(
            name='RECEIPTS_FIX',
        ),
        migrations.DeleteModel(
            name='SAVED_TRANSACTIONS_FIX',
        ),
        migrations.DeleteModel(
            name='SHOP_STOCK_FIX',
        ),
    ]
