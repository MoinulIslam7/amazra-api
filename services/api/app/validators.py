import re


BD_PHONE_REGEX = re.compile(r"^\+8801\d{9}$")


def validate_bd_phone(phone: str) -> None:
    if not BD_PHONE_REGEX.match(phone):
        raise ValueError("Phone number must be in +8801XXXXXXXXX format")
