from django import forms
from rest_framework.exceptions import ValidationError

from accounts.serializers import RegistrationSerializer


class RegistrationForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[("RIDER", "I want to ride"), ("DRIVER", "I want to drive")])
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password", strip=False, max_length=128)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password", strip=False, max_length=128)

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        if data["password1"] != data["password2"]:
            self.add_error("password2", "The passwords do not match.")
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
