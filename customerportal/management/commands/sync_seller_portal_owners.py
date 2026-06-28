from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Account
from masterdata.models import Seller


class Command(BaseCommand):
    help = "Safely mark each Seller.portal_user account as the seller owner."

    @transaction.atomic
    def handle(self, *args, **options):
        updated = 0
        created = 0

        sellers = Seller.objects.select_related("portal_user").exclude(portal_user=None)

        for seller in sellers.iterator():
            account, was_created = Account.objects.get_or_create(
                user=seller.portal_user,
                defaults={
                    "account_type": Account.ACCOUNT_TYPE_SELLER,
                    "seller": seller,
                    "is_seller_owner": True,
                },
            )

            changed = False
            if account.account_type != Account.ACCOUNT_TYPE_SELLER:
                account.account_type = Account.ACCOUNT_TYPE_SELLER
                changed = True
            if account.seller_id != seller.id:
                account.seller = seller
                changed = True
            if not account.is_seller_owner:
                account.is_seller_owner = True
                changed = True
            if account.is_archived:
                account.is_archived = False
                account.archived_at = None
                changed = True

            if changed:
                account.save()
                updated += 1

            if was_created:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Owner sync completed. Created: {created}, updated: {updated}."
            )
        )
