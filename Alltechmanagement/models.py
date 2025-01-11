from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, Group, Permission, \
    AbstractUser
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


#NEW SYSTEM
class SHOP2_STOCK_FIX(models.Model):
    product_name = models.CharField(max_length=30, unique=True)
    quantity = models.IntegerField()
    price = models.DecimalField(decimal_places=2, max_digits=7)

    def __str__(self):
        return self.product_name


class SAVED_TRANSACTIONS2_FIX(models.Model):
    product_name = models.CharField(max_length=20)
    selling_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.IntegerField()
    customer_name = models.CharField(max_length=255, default='null')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return self.product_name


class COMPLETED_TRANSACTIONS2_FIX(models.Model):
    product_name = models.CharField(max_length=20)
    selling_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.IntegerField()
    customer_name = models.CharField(max_length=255, default='null')

    def __str__(self):
        return self.product_name


class RECEIPTS2_FIX(models.Model):
    # Existing fields with enhancements
    product_name = models.CharField(max_length=100)
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    quantity = models.IntegerField(
        validators=[MinValueValidator(1)]
    )
    customer_name = models.CharField(
        max_length=255,
        default='null',
        db_index=True
    )

    # Enhanced timestamp fields
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Computed property
    @property
    def total_amount(self):
        return self.selling_price * self.quantity

    def __str__(self):
        return f"{self.product_name} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        indexes = [
            models.Index(fields=['created_at', 'product_name']),
            models.Index(fields=['created_at', 'customer_name']),
        ]
        ordering = ['-created_at']


class SALE_SUMMARY_FIX(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    total = models.IntegerField()
    product_name = models.CharField(max_length=100)

    def __str__(self):
        return self.product_name


class PushNotificationToken(models.Model):
    token = models.CharField(max_length=255, unique=True)


class AuthorizedFirebaseToken(models.Model):
    token = models.TextField(unique=True)

    def __str__(self):
        return self.token


class LcdCustomers(models.Model):
    customer_name = models.CharField(max_length=255, unique=True)
    total_spent =  models.DecimalField(max_digits=12, decimal_places=2)

