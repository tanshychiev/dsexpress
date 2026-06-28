from django import template

from customerportal.permissions import (
    current_role_name,
    is_seller_owner,
    user_has_portal_permission,
)


register = template.Library()


@register.filter
def portal_has_perm(user, permission_key):
    return user_has_portal_permission(user, permission_key)


@register.filter
def portal_role_name(user):
    return current_role_name(user)


@register.filter
def portal_is_owner(user):
    return is_seller_owner(user)


@register.filter
def dict_get(mapping, key):
    if not isinstance(mapping, dict):
        return False
    return mapping.get(key, False)
