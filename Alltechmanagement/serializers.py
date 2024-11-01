from rest_framework import serializers

from Alltechmanagement.models import SHOP2_STOCK_FIX, SAVED_TRANSACTIONS2_FIX


class SellSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    quantity = serializers.IntegerField()
    customer_name = serializers.CharField()


class DispatchSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    quantity = serializers.IntegerField()


class shop2_serializer(serializers.ModelSerializer):
    class Meta:
        model = SHOP2_STOCK_FIX
        fields = '__all__'


class saved_serializer2(serializers.ModelSerializer):
    class Meta:
        model = SAVED_TRANSACTIONS2_FIX
        fields = '__all__'
