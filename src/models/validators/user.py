import re
import phonenumbers
import phonenumbers.carrier
from phonenumbers.phonenumberutil import NumberParseException
import email_validator


def validate_password_strength(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lower letter.")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one digit.")
    if not re.search(r"[@$!%*?&#]", password):
        raise ValueError("Password must contain at least one special character: @, $, !, %, *, ?, #, &.")
    return password


def validate_email(user_email: str) -> str:
    try:
        email_info = email_validator.validate_email(user_email, check_deliverability=False)
        email = email_info.normalized
    except email_validator.EmailNotValidError as error:
        raise ValueError(str(error))
    else:
        return email


def validate_phone_number(value):
    try:
        parsed_number = phonenumbers.parse(value, "US")
        if not phonenumbers.is_valid_number(parsed_number):
            raise ValueError("Invalid US phone number.")
        if not phonenumbers.is_possible_number(parsed_number):
            raise ValueError("Impossible US phone number.")

    except NumberParseException:
        raise ValueError("Invalid phone number format. Expected format: +1XXXXXXXXXX")

    return value
