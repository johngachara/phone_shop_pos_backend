# Generated by Django 4.2.8 on 2024-02-19 06:59

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0010_sale_summary_total_goods_alter_sale_summary_total'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='sale_summary',
            name='total_goods',
        ),
    ]
