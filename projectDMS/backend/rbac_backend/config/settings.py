# settings.py - Email configuration
class EmailSettings:
    SMTP_HOST: str = "smtp.gmail.com"  # or your SMTP provider
    SMTP_PORT: int = 587
    USE_TLS: bool = True
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    FROM_EMAIL: str = "noreply@ycontraclaim.com"
    FROM_NAME: str = "ContraClaim DMS"
    COMPANY_NAME: str = "ContraClaim"
    SUPPORT_EMAIL: str = "support@contraclaim.com"

email = EmailSettings()
