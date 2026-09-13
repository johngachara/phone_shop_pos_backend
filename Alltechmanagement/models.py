"""Data model.

Naming: PascalCase classes, explicit snake_case `db_table`. The previous names
(`SHOP2_STOCK_FIX`, `SAVED_TRANSACTIONS2_FIX`, ...) carried a shop number, a
`_FIX` suffix from a long-finished repair, and SCREAMING_SNAKE class names that
Django's own conventions reject.

Structure: a sale used to be moved between three tables to represent a change of
status -- created in SAVED, copied into COMPLETED *and* RECEIPTS on completion,
then deleted from COMPLETED once the daily email had been sent. That made the
permanent record a side effect of a duplicate write, and made the daily email
destructive. A sale is now one row whose status and timestamps change.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Stock(models.Model):
    """An item held in the shop."""

    product_name = models.CharField(max_length=100, unique=True)
    quantity = models.IntegerField(validators=[MinValueValidator(0)])
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    # Nullable on purpose, for now. Profit needs it, but the existing POS
    # frontend has no field for it and is not rebuilt until later in the
    # redesign; a non-null column would break every add-stock call in the
    # meantime. Profit is reported only where it is known. Tighten to non-null
    # once the frontend collects it.
    buying_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'stock'
        ordering = ['product_name']

    def __str__(self):
        return self.product_name


class Accessory(models.Model):
    """An accessory held in the shop.

    Ported out of Firestore, where accessories lived in an `accessories`
    collection with auto-increment ids simulated by a `counters/accessories`
    document. Kept as its own table rather than folded into Stock: the two are
    separate inventories with separate search indexes and separate screens in
    the POS, and merging them would change what every existing query means.

    Sales of both land in the same `sales` table, so reporting is unified even
    though inventory is not.
    """

    product_name = models.CharField(max_length=100, unique=True)
    quantity = models.IntegerField(validators=[MinValueValidator(0)])
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    buying_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accessories'
        ordering = ['product_name']
        verbose_name_plural = 'accessories'

    def __str__(self):
        return self.product_name


class Sale(models.Model):
    """One sale, from held order through to permanent record.

    Replaces SAVED_TRANSACTIONS2_FIX, COMPLETED_TRANSACTIONS2_FIX and
    RECEIPTS2_FIX. The three roles those tables played are now:

      status=PENDING                   held, unpaid -- was SAVED
      status=COMPLETED, reported_at=NULL   paid, not yet in a sales report
      status=COMPLETED, reported_at set    paid and reported

    Nothing is deleted on completion or on reporting, so the permanent ledger is
    the table itself rather than a duplicate written alongside it.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'

    class ItemType(models.TextChoices):
        SCREEN = 'SCREEN', 'Screen'
        ACCESSORY = 'ACCESSORY', 'Accessory'

    # Denormalised on purpose: a sale record must keep the name and prices it was
    # actually sold under, even if the stock item is later renamed or deleted.
    product_name = models.CharField(max_length=100, db_index=True)
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    # Captured at the moment of sale rather than joined from Stock at report
    # time. Joining would silently rewrite historical profit every time an item
    # is restocked at a different cost.
    buying_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    customer_name = models.CharField(max_length=255, default='null', db_index=True)

    # Which inventory this came from. Denormalised alongside the two nullable
    # links below so a sale still reports correctly after its item is deleted.
    item_type = models.CharField(
        max_length=16,
        choices=ItemType.choices,
        default=ItemType.SCREEN,
        db_index=True,
    )

    stock = models.ForeignKey(
        Stock,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sales',
    )
    accessory = models.ForeignKey(
        'Accessory',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sales',
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # When this sale was included in a sales report. Replaces deleting the row.
    reported_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = 'sales'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['-created_at', 'product_name']),
            models.Index(fields=['-created_at', 'customer_name']),
            models.Index(fields=['status', 'reported_at']),
        ]

    def __str__(self):
        return f"{self.product_name} x{self.quantity} ({self.status})"

    @staticmethod
    def _money(value):
        """Coerce to Decimal.

        Django does not convert field values assigned in Python, so an instance
        built with a string -- which DRF, JSON payloads and test fixtures all
        produce -- still holds a string until it is reloaded. `"100.00" * 3` is
        then string repetition rather than arithmetic, and returns
        "100.00100.00100.00" as a money amount with no error raised. That value
        feeds Customer.total_spent on completion, so it has to be impossible.
        """
        return value if isinstance(value, Decimal) else Decimal(str(value))

    @property
    def total_amount(self):
        return self._money(self.selling_price) * self.quantity

    @property
    def profit(self):
        """Profit, or None when the buying price was never recorded.

        None rather than zero: an unknown cost is not the same as a free item,
        and reporting it as zero would overstate profit.
        """
        if self.buying_price is None:
            return None
        return (self._money(self.selling_price) - self._money(self.buying_price)) * self.quantity


class Customer(models.Model):
    """Known customer. Backs name autocomplete during a sale."""

    name = models.CharField(max_length=255, unique=True)
    total_spent = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'customers'
        ordering = ['name']

    def __str__(self):
        return self.name


class PushDevice(models.Model):
    """A device registered for FCM push.

    The old PushNotificationToken was a bare token column with no association to
    anyone, so there was no way to send a notification to managers only -- which
    is the whole requirement now that FCM replaces the WhatsApp bot.
    """

    token = models.CharField(max_length=255, unique=True)
    # Supabase auth user id. Nullable until the Supabase auth work lands and
    # existing registrations can be attributed.
    user_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    role = models.CharField(max_length=16, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'push_devices'

    def __str__(self):
        return f"{self.token[:12]}... ({self.role or 'unassigned'})"


class WebAuthnCredential(models.Model):
    """A registered passkey.

    Ported out of Firestore, where credentials were an array on the user
    document. A table instead of a JSON column so a credential can be looked up
    by its id directly, and so the signature counter can be updated without
    rewriting every other credential the user owns.

    Passkeys are the second step of sign-in: Supabase checks the password, this
    checks the device.
    """

    # Supabase auth user id. Not a foreign key -- Supabase owns the user table
    # and it lives outside this database.
    user_id = models.CharField(max_length=64, db_index=True)
    # base64url, as the browser reports it.
    credential_id = models.CharField(max_length=512, unique=True)
    public_key = models.TextField()
    # Incremented by the authenticator on each use. A value that fails to
    # advance is the signal that a credential has been cloned.
    sign_count = models.BigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    device_type = models.CharField(max_length=32, blank=True, default='')
    backed_up = models.BooleanField(default=False)
    label = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'webauthn_credentials'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.label or self.credential_id[:12]} ({self.user_id})"
