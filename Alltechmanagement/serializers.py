from rest_framework import serializers

from Alltechmanagement.models import Accessory, Customer, Sale, Stock


class SellSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    quantity = serializers.IntegerField(min_value=1)
    customer_name = serializers.CharField()


class DispatchSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)


class StockSerializer(serializers.ModelSerializer):
    """Stock, readable and writable under both the old and new price names.

    The column is `selling_price` now, but the current POS sends and reads
    `price`. Accepting both keeps the existing frontend working through the
    backend changes; the alias is dropped once the frontend is rebuilt.
    """

    price = serializers.DecimalField(
        source='selling_price', max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = Stock
        fields = [
            'id', 'product_name', 'quantity',
            'selling_price', 'price', 'buying_price',
            'created_at', 'updated_at',
        ]
        extra_kwargs = {
            # Not required on input, because a payload may supply `price`
            # instead. to_internal_value enforces that one of them is present.
            'selling_price': {'required': False},
        }

    def to_internal_value(self, data):
        if 'selling_price' not in data and 'price' in data:
            data = dict(data)
            data['selling_price'] = data['price']
        validated = super().to_internal_value(data)
        if not self.partial and validated.get('selling_price') is None:
            raise serializers.ValidationError(
                {'selling_price': 'Either selling_price or price is required.'}
            )
        return validated


class SaleSerializer(serializers.ModelSerializer):
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    profit = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, allow_null=True
    )

    class Meta:
        model = Sale
        fields = [
            'id', 'product_name', 'quantity', 'selling_price', 'buying_price',
            'customer_name', 'status', 'total_amount', 'profit',
            'created_at', 'completed_at', 'reported_at',
        ]


class CustomerSerializer(serializers.ModelSerializer):
    # The POS autocomplete reads `customer_name`; the column is `name`.
    customer_name = serializers.CharField(source='name', read_only=True)

    class Meta:
        model = Customer
        fields = ['customer_name']


class AccessorySerializer(serializers.ModelSerializer):
    """Accessory, readable and writable under both price names.

    Same alias as StockSerializer: the POS sends and reads `price`, the column
    is `selling_price`. Dropped once the frontend is rebuilt.
    """

    price = serializers.DecimalField(
        source='selling_price', max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = Accessory
        fields = [
            'id', 'product_name', 'quantity',
            'selling_price', 'price', 'buying_price',
            'created_at', 'updated_at',
        ]
        extra_kwargs = {
            'selling_price': {'required': False},
        }

    def to_internal_value(self, data):
        if 'selling_price' not in data and 'price' in data:
            data = dict(data)
            data['selling_price'] = data['price']
        validated = super().to_internal_value(data)
        if not self.partial and validated.get('selling_price') is None:
            raise serializers.ValidationError(
                {'selling_price': 'Either selling_price or price is required.'}
            )
        return validated
