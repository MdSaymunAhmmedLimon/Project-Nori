from django.contrib.auth import get_user_model, views as auth_views
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from .forms import SecurityQuestionRequestForm, SecurityQuestionResetForm
from .views import auth_limited

User = get_user_model()


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


class PasswordResetView(View):
    template_name = "webapp/password_form.html"

    def get(self, request):
        form = SecurityQuestionRequestForm()
        return render(request, self.template_name, {
            "form": form,
            "heading": "Forgot your password?",
            "description": "Enter your username to reset your password using your security question (no email needed).",
            "submit_label": "Find account",
        })

    def post(self, request):
        if auth_limited(request):
            form = SecurityQuestionRequestForm(request.POST)
            return render(request, self.template_name, {
                "form": form,
                "heading": "Forgot your password?",
                "description": "Enter your username to reset your password using your security question (no email needed).",
                "submit_label": "Find account",
                "rate_error": "Too many attempts. Please wait a minute.",
            }, status=429)

        form = SecurityQuestionRequestForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            request.session["reset_username"] = username
            return redirect(f"{reverse('webapp:password_reset_verify')}?username={username}")

        return render(request, self.template_name, {
            "form": form,
            "heading": "Forgot your password?",
            "description": "Enter your username to reset your password using your security question (no email needed).",
            "submit_label": "Find account",
        })


class PasswordResetVerifyView(View):
    template_name = "webapp/password_form.html"

    def get_user(self, request):
        username = request.GET.get("username") or request.session.get("reset_username")
        if not username:
            return None
        return User.objects.filter(username__iexact=username).first()

    def get(self, request, *args, **kwargs):
        user = self.get_user(request)
        if not user or not user.security_question:
            return redirect("webapp:password_reset")

        form = SecurityQuestionResetForm(initial={
            "username": user.username,
            "security_question": user.security_question,
        })
        return render(request, self.template_name, {
            "form": form,
            "heading": "Security verification",
            "description": "Answer your security question and choose a new password.",
            "submit_label": "Reset password",
        })

    def post(self, request, *args, **kwargs):
        if auth_limited(request):
            form = SecurityQuestionResetForm(request.POST)
            return render(request, self.template_name, {
                "form": form,
                "heading": "Security verification",
                "description": "Answer your security question and choose a new password.",
                "submit_label": "Reset password",
                "rate_error": "Too many attempts. Please wait a minute.",
            }, status=429)

        form = SecurityQuestionResetForm(request.POST)
        if form.is_valid():
            form.save()
            request.session.pop("reset_username", None)
            return redirect("webapp:password_reset_complete")

        # If security_question was not posted or blank, repopulate it from user
        user = self.get_user(request)
        if user and not form.data.get("security_question"):
            form.initial["security_question"] = user.security_question

        return render(request, self.template_name, {
            "form": form,
            "heading": "Security verification",
            "description": "Answer your security question and choose a new password.",
            "submit_label": "Reset password",
        })


class PasswordResetCompleteView(TemplateView):
    template_name = "webapp/account_message.html"
    extra_context = {
        "heading": "Password reset complete",
        "description": "Your password has been updated. Sign in with your new password to continue your journey.",
    }

