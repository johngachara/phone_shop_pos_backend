# Generated by Django 5.1.3 on 2024-11-12 13:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0037_rename_cutomer_name_lcdcustomers_customer_name'),
    ]

    operations = [
        migrations.AlterField(
            model_name='lcdcustomers',
            name='total_spent',
            field=models.DecimalField(decimal_places=2, max_digits=12),
        ),
    ]
