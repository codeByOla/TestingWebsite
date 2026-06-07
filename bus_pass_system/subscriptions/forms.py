from unicodedata import name

from django import forms
from .models import StudentProfile

class CSVUploadForm(forms.Form):
    file = forms.FileField()


class StudentForm(forms.ModelForm):
    class Meta:
        model = StudentProfile
        fields = ["first_name", "last_name", "barcode", "university"]
        labels = {
            "first_name": "Emri",
            "last_name": "Mbiemri",
            "barcode": "Kodi i kartës së studentit",
            "university": "Universiteti",
        }


class CSVStudentValidator(forms.Form):
    barcode = forms.CharField(max_length=100)
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    university = forms.CharField(max_length=255)

    def clean_barcode(self):
        barcode = (self.cleaned_data.get("barcode") or "").strip()

        if not barcode:
            raise forms.ValidationError("Barcode është i detyrueshëm.")

        if not barcode.isdigit():
            raise forms.ValidationError("Barcode duhet te jete numer.")

        if len(barcode) < 5:
            raise forms.ValidationError("Barcode shume i shkurter.")

        if StudentProfile.objects.filter(barcode=barcode).exists():
            raise forms.ValidationError("Ky barcode ekziston.")

        return barcode

    def clean_first_name(self):
        name = (self.cleaned_data.get("first_name") or "").strip()
        if not name:
            raise forms.ValidationError("Emri është i detyrueshëm.")
        if not name.isalpha():
            raise forms.ValidationError("Emri duhet te kete vetem shkronja.")
        return name

    def clean_last_name(self):
        name = (self.cleaned_data.get("last_name") or "").strip()
        if not name:
            raise forms.ValidationError("Mbiemri është i detyrueshëm.")
        if not name.isalpha():
            raise forms.ValidationError("Mbiemri duhet te kete vetem shkronja.")
        return name