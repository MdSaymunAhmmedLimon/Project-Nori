"""Browser password management using Django's one-use recovery tokens."""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy

from .views import auth_limited


class LimitedPasswordMixin:
    def post(self, request, *args, **kwargs):
        if auth_limited(request):
            return self.render_to_response(
                self.get_context_data(rate_error="Too many attempts. Please wait a minute."),
                status=429,
            )
        return super().post(request, *args, **kwargs)


class PasswordChangeView(LimitedPasswordMixin, auth_views.PasswordChangeView):
    template_name = "webapp/password_form.html"
    success_url = reverse_lazy("webapp:password_change_done")
    extra_context = {
        "heading": "Change your password",
        "description": "Enter your current password and choose a new one. Your other devices will need to sign in again.",
        "submit_label": "Update password",
    }


class PasswordChangeDoneView(auth_views.PasswordChangeDoneView):
    template_name = "webapp/account_message.html"
    extra_context = {
        "heading": "Password updated",
        "description": "Your new password is ready to use. You are still signed in on this device.",
    }


class PasswordResetView(LimitedPasswordMixin, auth_views.PasswordResetView):
    template_name = "webapp/password_form.html"
    email_template_name = "webapp/password_reset_email.txt"
    subject_template_name = "webapp/password_reset_subject.txt"
    success_url = reverse_lazy("webapp:password_reset_done")
    extra_context = {
        "heading": "Forgot your password?",
        "description": "Enter your account email and we will send you a link to choose a new password.",
        "submit_label": "Send reset link",
    }

    def form_valid(self, form):
        # Use the configured public origin in deployment, never a supplied Host.
        origin = urlsplit(settings.PUBLIC_BASE_URL)
        form.save(
            domain_override=self.request.get_host() if settings.DEBUG else origin.netloc,
            use_https=self.request.is_secure() if settings.DEBUG else origin.scheme == "https",
            token_generator=self.token_generator,
            from_email=self.from_email,
            email_template_name=self.email_template_name,
            subject_template_name=self.subject_template_name,
            request=self.request,
        )
        return HttpResponseRedirect(self.get_success_url())


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "webapp/account_message.html"
    extra_context = {
        "heading": "Check your inbox",
        "description": "If an active account uses that email, a reset link will be sent. Check your spam folder too. The link expires in one hour.",
    }


class PasswordResetConfirmView(LimitedPasswordMixin, auth_views.PasswordResetConfirmView):
    template_name = "webapp/password_form.html"
    success_url = reverse_lazy("webapp:password_reset_complete")
    extra_context = {
        "heading": "Choose a new password",
        "description": "Choose a password you have not used elsewhere. You will need to sign in again after saving it.",
        "submit_label": "Save new password",
    }


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "webapp/account_message.html"
    extra_context = {
        "heading": "You are ready to return",
        "description": "Your password has been reset. Sign in with your new password to continue your journey.",
    }
