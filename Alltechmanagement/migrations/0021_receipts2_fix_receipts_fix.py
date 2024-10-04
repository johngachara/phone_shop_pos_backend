# Generated by Django 4.2.8 on 2024-03-19 06:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0020_completed_transactions2_fix_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='RECEIPTS2_FIX',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(max_length=20)),
                ('selling_price', models.DecimalField(decimal_places=2, max_digits=7)),
                ('quantity', models.IntegerField()),
                ('customer_name', models.CharField(default='null', max_length=255)),
            ],
        ),
        migrations.CreateModel(
            name='RECEIPTS_FIX',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(max_length=20)),
                ('selling_price', models.DecimalField(decimal_places=2, max_digits=7)),
                ('quantity', models.IntegerField()),
                ('customer_name', models.CharField(default='null', max_length=255)),
            ],
        ),
    ]
