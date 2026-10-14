"""The shell's only model: the admin's 5-character sign-in PIN.

The admin itself is a plain Django superuser (username, password, name on `first_name`).
Feature modules keep their models in their own app (chat/models.py, health/models.py, ...).
Same approach as AG-PROJ01's `AdminPin` and AG-PROJ02's passkey.
"""
import re

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import models


class AdminPin(models.Model):
    """The admin's 5-character PIN, used by the PIN tab on the sign-in page.

    Kept as a salted hash, like a password, so a PIN sign-in checks each admin's hash rather
    than looking the PIN up directly.
    """
    PATTERN = re.compile(r'[A-Z0-9]{5}')

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_pin')
    pin_hash = models.CharField(max_length=128)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def set_pin(self, raw_pin):
        self.pin_hash = make_password((raw_pin or '').upper())

    def check_pin(self, raw_pin):
        return check_password((raw_pin or '').upper(), self.pin_hash)

    def __str__(self):
        return f"PIN for {self.user.username}"
