# Generated by Django 4.2.8 on 2024-10-18 02:15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0032_remove_webauthncredential_attestation_format_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='FirebaseWebAuthnCredential',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('firebase_uid', models.CharField(db_index=True, max_length=128)),
                ('credential_id', models.BinaryField(unique=True)),
                ('public_key', models.BinaryField()),
                ('sign_count', models.IntegerField(default=0)),
                ('credential_name', models.CharField(max_length=255, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.DeleteModel(
            name='WebAuthnCredential',
        ),
        migrations.AddIndex(
            model_name='firebasewebauthncredential',
            index=models.Index(fields=['firebase_uid'], name='Alltechmana_firebas_d59fba_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='firebasewebauthncredential',
            unique_together={('firebase_uid', 'credential_id')},
        ),
    ]
