from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import User


class AccountCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("username", "email", "role", "phone_number")


class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = AccountCreationForm
    form = AccountChangeForm
    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = DjangoUserAdmin.list_filter + ("role",)
    fieldsets = DjangoUserAdmin.fieldsets + (("Ride sharing", {"fields": ("role", "phone_number")}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Ride sharing", {"fields": ("email", "role", "phone_number")}),
    )

    def get_readonly_fields(self, request, obj=None):
        # Role changes require an explicit migration of associated driver data.
        return ("role",) if obj else ()



from .models import Referral, ReferralCode


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "code", "created_at")
    search_fields = ("user__username", "code")


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("referrer", "referee", "status", "reward_amount", "created_at", "completed_at")
    list_filter = ("status",)
    search_fields = ("referrer__username", "referee__username")

