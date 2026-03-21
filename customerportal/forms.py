from django import forms


class PublicBookingForm(forms.Form):
    name = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Your name"})
    )
    phone = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone number"})
    )
    pickup_location = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "placeholder": "Pickup location",
            "rows": 3
        })
    )
    total_pc = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Total PC"})
    )


class SellerBookingForm(forms.Form):
    total_pc = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Total PC"})
    )
    booking_file = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"})
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "placeholder": "Optional note",
            "rows": 3
        })
    )


class TrackingForm(forms.Form):
    tracking_no = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter tracking number"})
    )