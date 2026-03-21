from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission

from masterdata.models import Seller, Shipper
from .models import Account

User = get_user_model()


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "password",
                "autocomplete": "new-password",
            }
        ),
        label="Password",
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "confirm password",
                "autocomplete": "new-password",
            }
        ),
        label="Confirm password",
    )

    is_active = forms.BooleanField(required=False, initial=True)

    account_type = forms.ChoiceField(
        choices=Account.ACCOUNT_TYPE_CHOICES,
        initial=Account.ACCOUNT_TYPE_STAFF,
        label="Account Type",
    )

    groups = forms.ModelChoiceField(
        queryset=Group.objects.all().order_by("name"),
        required=False,
        empty_label="-- Select Role --",
        label="Role",
    )

    seller = forms.ModelChoiceField(
        queryset=Seller.objects.all().order_by("name"),
        required=False,
        empty_label="-- Select Seller --",
        label="Seller",
    )

    shipper = forms.ModelChoiceField(
        queryset=Shipper.objects.all().order_by("name"),
        required=False,
        empty_label="-- Select Shipper --",
        label="Shipper",
    )

    user_permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.all().order_by("content_type__app_label", "codename"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Extra Permissions",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"placeholder": "username"}),
            "first_name": forms.TextInput(attrs={"placeholder": "first name"}),
            "last_name": forms.TextInput(attrs={"placeholder": "last name"}),
            "email": forms.EmailInput(attrs={"placeholder": "email (optional)"}),
        }

    def clean(self):
        cleaned = super().clean()

        p1 = cleaned.get("password1") or ""
        p2 = cleaned.get("password2") or ""
        account_type = cleaned.get("account_type")
        group = cleaned.get("groups")
        seller = cleaned.get("seller")
        shipper = cleaned.get("shipper")

        if p1 != p2:
            self.add_error("password2", "Passwords do not match")

        if len(p1) < 6:
            self.add_error("password1", "Password must be at least 6 characters")

        if account_type == Account.ACCOUNT_TYPE_STAFF and not group:
            self.add_error("groups", "Staff account must select a role")

        if account_type == Account.ACCOUNT_TYPE_SELLER and not seller:
            self.add_error("seller", "Seller account must select a seller")

        if account_type == Account.ACCOUNT_TYPE_SHIPPER and not shipper:
            self.add_error("shipper", "Shipper account must select a shipper")

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])

        account_type = self.cleaned_data["account_type"]
        selected_seller = self.cleaned_data.get("seller")
        selected_shipper = self.cleaned_data.get("shipper")

        user.is_staff = account_type == Account.ACCOUNT_TYPE_STAFF

        if commit:
            user.save()

            group = self.cleaned_data.get("groups")
            user.groups.clear()
            if account_type == Account.ACCOUNT_TYPE_STAFF and group:
                user.groups.add(group)

            if account_type == Account.ACCOUNT_TYPE_STAFF:
                user.user_permissions.set(self.cleaned_data.get("user_permissions"))
            else:
                user.user_permissions.clear()

            account, _ = Account.objects.get_or_create(user=user)
            account.account_type = account_type
            account.seller = selected_seller if account_type == Account.ACCOUNT_TYPE_SELLER else None
            account.shipper = selected_shipper if account_type == Account.ACCOUNT_TYPE_SHIPPER else None
            account.save()

            # clear old portal links first
            Seller.objects.filter(portal_user=user).update(portal_user=None)
            Shipper.objects.filter(portal_user=user).update(portal_user=None)

            # sync seller / shipper portal link
            if account_type == Account.ACCOUNT_TYPE_SELLER and selected_seller:
                selected_seller.portal_user = user
                selected_seller.save(update_fields=["portal_user"])

            if account_type == Account.ACCOUNT_TYPE_SHIPPER and selected_shipper:
                selected_shipper.portal_user = user
                selected_shipper.save(update_fields=["portal_user"])

        return user


class UserEditForm(forms.ModelForm):
    is_active = forms.BooleanField(required=False)

    account_type = forms.ChoiceField(
        choices=Account.ACCOUNT_TYPE_CHOICES,
        initial=Account.ACCOUNT_TYPE_STAFF,
        label="Account Type",
    )

    groups = forms.ModelChoiceField(
        queryset=Group.objects.all().order_by("name"),
        required=False,
        empty_label="-- Select Role --",
        label="Role",
    )

    seller = forms.ModelChoiceField(
        queryset=Seller.objects.all().order_by("name"),
        required=False,
        empty_label="-- Select Seller --",
        label="Seller",
    )

    shipper = forms.ModelChoiceField(
        queryset=Shipper.objects.all().order_by("name"),
        required=False,
        empty_label="-- Select Shipper --",
        label="Shipper",
    )

    user_permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.all().order_by("content_type__app_label", "codename"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Extra Permissions",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"placeholder": "username"}),
            "first_name": forms.TextInput(attrs={"placeholder": "first name"}),
            "last_name": forms.TextInput(attrs={"placeholder": "last name"}),
            "email": forms.EmailInput(attrs={"placeholder": "email (optional)"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            first_group = self.instance.groups.first()
            if first_group:
                self.fields["groups"].initial = first_group

            account, _ = Account.objects.get_or_create(
                user=self.instance,
                defaults={"account_type": Account.ACCOUNT_TYPE_STAFF},
            )
            self.fields["account_type"].initial = account.account_type
            self.fields["seller"].initial = account.seller
            self.fields["shipper"].initial = account.shipper

    def clean(self):
        cleaned = super().clean()

        account_type = cleaned.get("account_type")
        group = cleaned.get("groups")
        seller = cleaned.get("seller")
        shipper = cleaned.get("shipper")

        if account_type == Account.ACCOUNT_TYPE_STAFF and not group:
            self.add_error("groups", "Staff account must select a role")

        if account_type == Account.ACCOUNT_TYPE_SELLER and not seller:
            self.add_error("seller", "Seller account must select a seller")

        if account_type == Account.ACCOUNT_TYPE_SHIPPER and not shipper:
            self.add_error("shipper", "Shipper account must select a shipper")

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)

        account_type = self.cleaned_data["account_type"]
        selected_seller = self.cleaned_data.get("seller")
        selected_shipper = self.cleaned_data.get("shipper")

        user.is_staff = account_type == Account.ACCOUNT_TYPE_STAFF

        if commit:
            user.save()

            group = self.cleaned_data.get("groups")
            user.groups.clear()
            if account_type == Account.ACCOUNT_TYPE_STAFF and group:
                user.groups.add(group)

            if account_type == Account.ACCOUNT_TYPE_STAFF:
                user.user_permissions.set(self.cleaned_data.get("user_permissions"))
            else:
                user.user_permissions.clear()

            account, _ = Account.objects.get_or_create(
                user=user,
                defaults={"account_type": Account.ACCOUNT_TYPE_STAFF},
            )
            account.account_type = account_type
            account.seller = selected_seller if account_type == Account.ACCOUNT_TYPE_SELLER else None
            account.shipper = selected_shipper if account_type == Account.ACCOUNT_TYPE_SHIPPER else None
            account.save()

            # clear old portal links first
            Seller.objects.filter(portal_user=user).update(portal_user=None)
            Shipper.objects.filter(portal_user=user).update(portal_user=None)

            # sync seller / shipper portal link
            if account_type == Account.ACCOUNT_TYPE_SELLER and selected_seller:
                selected_seller.portal_user = user
                selected_seller.save(update_fields=["portal_user"])

            if account_type == Account.ACCOUNT_TYPE_SHIPPER and selected_shipper:
                selected_shipper.portal_user = user
                selected_shipper.save(update_fields=["portal_user"])

        return user


class ChangePasswordForm(forms.Form):
    password1 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "new password",
                "autocomplete": "new-password",
            }
        ),
        label="New password",
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "confirm new password",
                "autocomplete": "new-password",
            }
        ),
        label="Confirm new password",
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1") or ""
        p2 = cleaned.get("password2") or ""

        if p1 != p2:
            self.add_error("password2", "Passwords do not match")

        if len(p1) < 6:
            self.add_error("password1", "Password must be at least 6 characters")

        return cleaned