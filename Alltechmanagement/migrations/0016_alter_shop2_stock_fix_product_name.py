# Generated by Django 4.2.8 on 2024-03-18 13:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0015_shop2_stock_fix'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shop2_stock_fix',
            name='product_name',
            field=models.CharField(max_length=30),
        ),
    ]
