from django import forms
from masterdata.models import Seller


class DeliveryReportFilterForm(forms.Form):
    seller = forms.ModelChoiceField(
        queryset=Seller.objects.all().order_by("name"),
        required=False,
        empty_label="All shops"
    )

    delivery_date_from = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    delivery_date_to = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )

    pending_date_from = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    pending_date_to = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )

    keyword = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Search tracking / phone / receiver / seller"
        })
    )

    status_filter = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All Status"),
            ("DONE", "DONE"),
            ("PENDING", "PENDING"),
            ("DONE_RETURN", "DONE RETURN"),
        ]
    )


class DeliveryReportUploadForm(forms.Form):
    file = forms.FileField()