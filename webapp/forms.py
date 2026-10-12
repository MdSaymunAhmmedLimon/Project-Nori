from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from accounts.serializers import RegistrationSerializer

User = get_user_model()


class RegistrationForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[("RIDER", "I want to ride"), ("DRIVER", "I want to drive")])
    security_question = forms.ChoiceField(
        choices=[("", "-- Select a security question (optional) --")] + list(User.SECURITY_QUESTION_CHOICES),
        required=False,
        label="Security question",
        help_text="Used to reset your password without needing email.",
    )
    security_answer = forms.CharField(
        max_length=255,
        required=False,
        label="Answer to security question",
        help_text="Your answer is case-insensitive and kept private.",
    )
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password", strip=False, max_length=128)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password", strip=False, max_length=128)

    def clean(self):
        data = super().clean()
        if data.get("password1") and data.get("password2") and data["password1"] != data["password2"]:
            self.add_error("password2", "The passwords do not match.")
        if data.get("security_question") and not data.get("security_answer"):
            self.add_error("security_answer", "Please provide an answer to your security question.")
        if data.get("security_answer") and not data.get("security_question"):
            self.add_error("security_question", "Please select a security question.")
        if self.errors:
            return data
        payload = {key: value for key, value in data.items() if not key.startswith("password")}
        payload["password"] = data["password1"]
        self.serializer = RegistrationSerializer(data=payload)
        if not self.serializer.is_valid():
            for field, errors in self.serializer.errors.items():
                field = "password1" if field == "password" else field
                self.add_error(field if field in self.fields else None, errors)
        return data

    def save(self):
        return self.serializer.save()


class SecurityQuestionRequestForm(forms.Form):
    username = forms.CharField(max_length=150, label="Username")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        user = User.objects.filter(username__iexact=username).first()
        if not user:
            raise forms.ValidationError("No account found with this username.")
        if not user.security_question or not user.security_answer:
            raise forms.ValidationError("No security question is configured for this account.")
        self.user = user
        return user.username


class SecurityQuestionResetForm(forms.Form):
    username = forms.CharField(widget=forms.HiddenInput)
    security_question = forms.CharField(
        label="Security question",
        required=False,
        widget=forms.TextInput(attrs={"readonly": "readonly", "style": "background:#f3f6ec;"}),
    )
    security_answer = forms.CharField(
        label="Your answer",
        max_length=255,
        help_text="Answer to your security question.",
    )
    new_password1 = forms.CharField(
        widget=forms.PasswordInput,
        label="New password",
        strip=False,
        max_length=128,
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput,
        label="Confirm new password",
        strip=False,
        max_length=128,
    )

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        username = data.get("username", "").strip()
        user = User.objects.filter(username__iexact=username).first()
        if not user:
            self.add_error(None, "Account not found.")
            return data
        if not user.check_security_answer(data.get("security_answer", "")):
            self.add_error("security_answer", "Incorrect answer to security question.")
            return data
        if data.get("new_password1") != data.get("new_password2"):
            self.add_error("new_password2", "The passwords do not match.")
            return data
        try:
            validate_password(data["new_password1"], user=user)
        except DjangoValidationError as exc:
            self.add_error("new_password1", exc.messages)
            return data
        self.user = user
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["new_password1"])
        self.user.save()
        return self.user

