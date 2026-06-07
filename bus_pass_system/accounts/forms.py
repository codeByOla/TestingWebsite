from django import forms
from django.contrib.auth import get_user_model
from subscriptions.models import StudentProfile, University
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()

class StudentRegisterForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)
    barcode = forms.CharField(max_length=100)
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    university = forms.ModelChoiceField(
        queryset=University.objects.all(),
        empty_label="Select University"
    )
    photo = forms.ImageField(
    required=True,
    help_text="Ngarko një foto të qartë (JPG or PNG, max 2MB)")

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if not photo:
            raise forms.ValidationError("Foto është e detyrueshme.")

        allowed_extensions = ('.jpg', '.jpeg', '.png')
        if not photo.name.lower().endswith(allowed_extensions):
            raise forms.ValidationError("Lejohen vetem skedar JPG dhe PNG.")

        if photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Madhesia e skedarit duhet te jete nen 2MB.")

    # Validate actual image content — not just extension
        try:
            from PIL import Image as PilImage
            img = PilImage.open(photo)
            img.verify()  # Detects truncated/corrupt/malicious image data
            photo.seek(0)  # Reset after verify() exhausts the stream

        # Re-open to check format (verify() closes the image)
            img2 = PilImage.open(photo)
            if img2.format not in ('JPEG', 'PNG'):
                raise forms.ValidationError("Formati i imazhit nuk është i pranuar.")
            photo.seek(0)
        except forms.ValidationError:
            raise
        except Exception:
            raise forms.ValidationError("Skedari nuk është imazh i vlefshëm.")

        return photo
    


    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Ky username ekziston tashmë.")
        return username

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")
        barcode = cleaned_data.get("barcode", "").strip()
        first_name = (cleaned_data.get("first_name") or "").strip().lower()
        last_name = (cleaned_data.get("last_name") or "").strip().lower()
        university = cleaned_data.get("university")

        if password and confirm_password:
            if password != confirm_password:
                self.add_error("confirm_password", "Passwordet nuk përputhen.")
            else:
                try:
                    validate_password(password)
                except ValidationError as e:
                    self.add_error("password", e)

        if barcode:
            try:
                profile = StudentProfile.objects.get(barcode=barcode)
            except StudentProfile.DoesNotExist:
                self.add_error("barcode", "Barcode i pavlefshëm. Kontakto administratën.")
                return cleaned_data

            if profile.user is not None:
                self.add_error("barcode", "Ky barcode është tashmë i regjistruar.")
                return cleaned_data

            profile_first = (profile.first_name or "").strip().lower()
            profile_last = (profile.last_name or "").strip().lower()

            if first_name and profile_first != first_name:
                self.add_error("first_name", "Emri nuk përputhet me të dhënat institucionale.")

            if last_name and profile_last != last_name:
                self.add_error("last_name", "Mbiemri nuk përputhet me të dhënat institucionale.")

            if university and profile.university != university:
                self.add_error("university", "Universiteti nuk përputhet me të dhënat institucionale.")

        return cleaned_data

class PasswordResetRequestForm(forms.Form):
    username = forms.CharField(max_length=150, label="Username")
    barcode = forms.CharField(max_length=100, label="Barcode")
    first_name = forms.CharField(max_length=100, label="Emri")
    last_name = forms.CharField(max_length=100, label="Mbiemri")


class PasswordResetConfirmForm(forms.Form):
    new_password = forms.CharField(widget=forms.PasswordInput, label="Fjalëkalimi i ri")
    confirm_password = forms.CharField(widget=forms.PasswordInput, label="Konfirmo fjalëkalimin")

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")

        if new_password and confirm_password:
            if new_password != confirm_password:
                self.add_error("confirm_password", "Fjalëkalimet nuk përputhen.")
            else:
                try:
                    validate_password(new_password)
                except ValidationError as e:
                    self.add_error("new_password", e)

        return cleaned_data
