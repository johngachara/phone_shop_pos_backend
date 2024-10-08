# Generated by Django 4.2.8 on 2024-01-01 13:11

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Completed_transactions',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(max_length=20)),
                ('selling_price', models.DecimalField(decimal_places=2, max_digits=7)),
                ('quantity', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='Home_stock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(max_length=30, unique=True)),
                ('quantity', models.IntegerField()),
            ],
        ),
        migrations.CreateModel(
            name='Saved_transactions',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(max_length=20)),
                ('selling_price', models.DecimalField(decimal_places=2, max_digits=7)),
                ('quantity', models.IntegerField()),
                ('customer_name', models.CharField(default='null', max_length=255)),
            ],
        ),
    ]
