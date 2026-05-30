from django import forms

from masterdata.models import Seller

from .models import InventorySellerSetting, StockProduct


class InventorySellerSettingForm(forms.ModelForm):
    class Meta:
        model = InventorySellerSetting
        fields = [
            "stock_mode",
            "show_stock_in_portal",
            "note",
        ]
        widgets = {
            "stock_mode": forms.Select(attrs={"class": "ds-input"}),
            "show_stock_in_portal": forms.CheckboxInput(attrs={"class": "ds-check"}),
            "note": forms.Textarea(attrs={"class": "ds-input", "rows": 3}),
        }


class StockInForm(forms.Form):
    seller = forms.ModelChoiceField(
        queryset=Seller.objects.filter(is_active=True).order_by("name"),
        widget=forms.Select(attrs={"class": "ds-input"}),
    )

    product = forms.ModelChoiceField(
        queryset=StockProduct.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "ds-input"}),
    )

    new_product_name = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "ds-input",
                "placeholder": "Only fill if this is new product",
            }
        ),
    )

    product_type = forms.CharField(
        required=False,
        max_length=80,
        widget=forms.TextInput(
            attrs={
                "class": "ds-input",
                "placeholder": "Serum / Cream / Gel",
            }
        ),
    )

    photo = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "ds-input"}),
    )

    location = forms.CharField(
        required=False,
        max_length=120,
        widget=forms.TextInput(
            attrs={
                "class": "ds-input",
                "placeholder": "Shelf / box location",
            }
        ),
    )

    qty = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "ds-input"}),
    )

    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "ds-input", "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        seller = kwargs.pop("seller", None)
        super().__init__(*args, **kwargs)

        if seller:
            self.fields["product"].queryset = (
                StockProduct.objects
                .filter(seller=seller, is_active=True)
                .order_by("name")
            )
        else:
            self.fields["product"].queryset = (
                StockProduct.objects
                .filter(is_active=True)
                .order_by("seller__name", "name")
            )


class AdjustStockForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=StockProduct.objects.filter(is_active=True).order_by(
            "seller__name",
            "name",
        ),
        widget=forms.Select(attrs={"class": "ds-input"}),
    )

    real_qty = forms.IntegerField(
        required=False,
        min_value=0,
        widget=forms.NumberInput(
            attrs={
                "class": "ds-input",
                "placeholder": "Real physical qty",
            }
        ),
    )

    diff_qty = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(
            attrs={
                "class": "ds-input",
                "placeholder": "Or adjustment +/-",
            }
        ),
    )

    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "ds-input", "rows": 2}),
    )

    def clean(self):
        cleaned = super().clean()
        real_qty = cleaned.get("real_qty")
        diff_qty = cleaned.get("diff_qty")

        if real_qty is None and diff_qty is None:
            raise forms.ValidationError("Enter real qty or diff qty.")

        return cleaned


class ConfirmStockForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=StockProduct.objects.filter(is_active=True).order_by(
            "seller__name",
            "name",
        ),
        widget=forms.Select(attrs={"class": "ds-input"}),
    )

    real_qty = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "ds-input"}),
    )

    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "ds-input", "rows": 2}),
    )
