from django import forms

from Alltechmanagement.models import SHOP2_STOCK_FIX, SHOP_STOCK_FIX, \
    HOME_STOCK_FIX


class signin_form(forms.Form):
    username = forms.CharField(label='Username', max_length=30)
    password = forms.CharField(label='Password', max_length=30, widget=forms.PasswordInput(attrs={"type": "password"}))


class products_form(forms.ModelForm):
    class Meta:
        model = SHOP_STOCK_FIX
        fields = '__all__'


class home_form(forms.ModelForm):
    class Meta:
        model = HOME_STOCK_FIX
        fields = '__all__'


class PaymentForm(forms.Form):
    product_name = forms.CharField(max_length=255)
    price = forms.DecimalField()
    quantity = forms.IntegerField()
    customer_name = forms.CharField(max_length=255)


class PaymentForm2(forms.Form):
    product_name = forms.CharField(max_length=255)
    price = forms.DecimalField()
    quantity = forms.IntegerField()
    customer_name = forms.CharField(max_length=255)


class CompleteForm(PaymentForm):
    pass


class shop2_form(forms.ModelForm):
    class Meta:
        model = SHOP2_STOCK_FIX
        fields = '__all__'
