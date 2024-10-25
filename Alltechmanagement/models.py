from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin, Group, Permission
from django.db import models
from django.utils import timezone

#NEW SYSTEM
class SHOP2_STOCK_FIX(models.Model):
    product_name = models.CharField(max_length=30, unique=True)
    quantity = models.IntegerField()
    price = models.DecimalField(decimal_places=2, max_digits=7)

    def __str__(self):
        return self.product_name


class SHOP_STOCK_FIX(models.Model):
    product_name = models.CharField(max_length=30, unique=True)
    quantity = models.IntegerField()
    price = models.DecimalField(decimal_places=2, max_digits=7)

    def __str__(self):
        return self.product_name


class HOME_STOCK_FIX(models.Model):
    product_name = models.CharField(max_length=30, unique=True)
    quantity = models.IntegerField()

    def __str__(self):
        return self.product_name


class SAVED_TRANSACTIONS_FIX(models.Model):
    product_name = models.CharField(max_length=20)
    selling_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.IntegerField()
    customer_name = models.CharField(max_length=255, default='null')
    created_at = models.DateTimeField(auto_now_add=True)

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


class COMPLETED_TRANSACTIONS_FIX(models.Model):
    product_name = models.CharField(max_length=20)
    selling_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.IntegerField()
    customer_name = models.CharField(max_length=255, default='null')

    def __str__(self):
        return self.product_name


class COMPLETED_TRANSACTIONS2_FIX(models.Model):
    product_name = models.CharField(max_length=20)
    selling_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.IntegerField()
    customer_name = models.CharField(max_length=255, default='null')

    def __str__(self):
        return self.product_name


class RECEIPTS_FIX(models.Model):
    product_name = models.CharField(max_length=20)
    selling_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.IntegerField()
    customer_name = models.CharField(max_length=255, default='null')
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.product_name


class RECEIPTS2_FIX(models.Model):
    product_name = models.CharField(max_length=20)
    selling_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.IntegerField()
    customer_name = models.CharField(max_length=255, default='null')
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.product_name


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

