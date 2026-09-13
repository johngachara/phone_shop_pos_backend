from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


def backfill(apps, schema_editor):
    """Fail rather than invent a cost.

    makemigrations offers to fill existing NULLs with a default. There is no
    honest default here: zero claims the item was free and inflates profit, and
    the selling price claims it earned nothing. If a row has no cost, someone
    has to say what it was.

    Verified empty before writing this -- stock, accessories and sales all had
    zero NULLs -- so this raises only if that changes before the migration runs.
    """
    for model_name in ('Stock', 'Accessory'):
        model = apps.get_model('Alltechmanagement', model_name)
        missing = model.objects.filter(buying_price__isnull=True).count()
        if missing:
            raise RuntimeError(
                f"{missing} {model_name} row(s) have no buying price. Set one for "
                f"each before applying this migration; there is no correct value "
                f"to guess."
            )


class Migration(migrations.Migration):

    dependencies = [
        ('Alltechmanagement', '0005_insight'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='stock',
            name='buying_price',
            field=models.DecimalField(
                decimal_places=2, max_digits=10,
                validators=[MinValueValidator(Decimal('0'))],
            ),
        ),
        migrations.AlterField(
            model_name='accessory',
            name='buying_price',
            field=models.DecimalField(
                decimal_places=2, max_digits=10,
                validators=[MinValueValidator(Decimal('0'))],
            ),
        ),
    ]
