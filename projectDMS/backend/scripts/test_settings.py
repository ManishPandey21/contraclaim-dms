# test_settings.py
import os
import sys
from unittest.mock import patch

# Add the parent directory to Python path to import your modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_config_loading():
    """Test that configuration settings are loading correctly"""
    print("🔧 Testing Configuration Settings Loader")
    print("=" * 60)

    try:
        # Import your settings
        from config import settings

        # Test 1: Basic settings loading
        print("✅ Settings module imported successfully")
        print(f"✅ Settings class: {type(settings).__name__}")

        # Test 2: Check critical fields
        print("\n📋 Critical Fields Check:")
        print("-" * 30)

        critical_fields = settings.CRITICAL_FIELDS
        for field in critical_fields:
            value = getattr(settings, field, None)
            is_set = field in settings.model_fields_set
            status = "✅ SET" if is_set and value and value != field else "❌ USING DEFAULT"
            print(f"{field}: {status}")
            if not is_set or not value or value == field:
                print(f"   Value: '{value}'")

        # Test 3: SMTP Configuration
        print("\n📧 SMTP Configuration Check:")
        print("-" * 30)

        smtp_config = {
            'SMTP_HOST': settings.SMTP_HOST,
            'SMTP_PORT': settings.SMTP_PORT,
            'SMTP_USERNAME': settings.SMTP_USERNAME,
            'SMTP_FROM_EMAIL': settings.SMTP_FROM_EMAIL,
            'SMTP_PASSWORD': '***' + settings.SMTP_PASSWORD[-4:] if settings.SMTP_PASSWORD and len(settings.SMTP_PASSWORD) > 4 else '***'
        }

        for key, value in smtp_config.items():
            is_default = value in [key, "SMTP_USERNAME", "SMTP_PASSWORD"]
            status = "✅" if not is_default else "❌"
            print(f"{status} {key}: {value}")

        # Test 4: Database Configuration
        print("\n🗄️ Database Configuration:")
        print("-" * 30)
        print(f"Database URL: {settings.DATABASE_URL}")
        print(f"Local MongoDB URI: {settings.LOCAL_MONGODB_URI}")

        # Test 5: CORS Configuration
        print("\n🌐 CORS Configuration:")
        print("-" * 30)
        print(f"CORS Origins: {settings.CORS_ORIGINS}")
        print(f"Number of allowed origins: {len(settings.CORS_ORIGINS)}")

        # Test 6: AWS Configuration
        print("\n☁️ AWS Configuration:")
        print("-" * 30)
        aws_config = {
            'AWS_ACCESS_KEY_ID': '***' + settings.AWS_ACCESS_KEY_ID[-4:] if settings.AWS_ACCESS_KEY_ID and len(settings.AWS_ACCESS_KEY_ID) > 4 else settings.AWS_ACCESS_KEY_ID,
            'AWS_REGION': settings.AWS_REGION,
            'AWS_BUCKET_NAME': settings.AWS_BUCKET_NAME
        }
        for key, value in aws_config.items():
            is_default = value == key
            status = "✅" if not is_default else "❌"
            print(f"{status} {key}: {value}")

        # Test 7: OpenAI Configuration
        print("\n🤖 AI Configuration:")
        print("-" * 30)
        ai_config = {
            'OPENAI_API_KEY': '***' + settings.OPENAI_API_KEY[-4:] if settings.OPENAI_API_KEY and len(settings.OPENAI_API_KEY) > 4 else settings.OPENAI_API_KEY,
            'ASSISTANT_ID': settings.ASSISTANT_ID,
            'LANGGRAPH_ENABLED': settings.LANGGRAPH_ENABLED
        }
        for key, value in ai_config.items():
            is_default = value == key
            status = "✅" if not is_default else "❌"
            print(f"{status} {key}: {value}")

        # Test 8: File Upload Configuration
        print("\n📁 File Upload Configuration:")
        print("-" * 30)
        print(f"Uploads Directory: {settings.UPLOADS_DIR}")
        print(f"Secure Uploads Directory: {settings.SECURE_UPLOADS_DIR}")
        print(f"Bulk Upload Max Files: {settings.BULK_UPLOAD_MAX_FILES}")
        print(f"Allowed Document MIME Types: {settings.ALLOWED_DOCUMENT_MIMES}")

        # Test 9: Environment Variables Check
        print("\n🔍 Environment Variables Check:")
        print("-" * 30)
        env_vars_to_check = [
            'SMTP_HOST', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM_EMAIL',
            'DATABASE_URL', 'SECRET_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
            'OPENAI_API_KEY'
        ]

        for env_var in env_vars_to_check:
            value = os.getenv(env_var)
            if value:
                masked_value = value[:4] + '***' + value[-4:] if len(value) > 8 else '***'
                print(f"✅ {env_var}: Set ({masked_value})")
            else:
                print(f"❌ {env_var}: Not set")

        # Test 10: Email Configuration Test
        print("\n✉️ Email Configuration Test:")
        print("-" * 30)
        try:
            from email import conf
            print("✅ Email configuration imported successfully")
            print(f"✅ Mail server: {conf.MAIL_SERVER}:{conf.MAIL_PORT}")
            print(f"✅ Mail username: {conf.MAIL_USERNAME}")
            print(f"✅ Mail from: {conf.MAIL_FROM}")

            # Check if SMTP credentials are not using defaults
            if (conf.MAIL_USERNAME != "SMTP_USERNAME" and
                conf.MAIL_PASSWORD != "SMTP_PASSWORD" and
                conf.MAIL_FROM != "SMTP_FROM_EMAIL"):
                print("✅ SMTP credentials are properly configured")
            else:
                print("❌ SMTP credentials are using default values")

        except Exception as e:
            print(f"❌ Email configuration failed: {e}")

        # Summary
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)

        # Count issues
        critical_issues = sum(1 for field in critical_fields
                            if not getattr(settings, field) or
                            getattr(settings, field) == field)

        if critical_issues == 0:
            print("🎉 SUCCESS: All critical settings are properly configured!")
        else:
            print(f"⚠️  WARNING: {critical_issues} critical setting(s) are using default values")
            print("   Please check your .env file for the following variables:")
            for field in critical_fields:
                value = getattr(settings, field, None)
                if not value or value == field:
                    print(f"   - {field}")

        return critical_issues == 0

    except Exception as e:
        print(f"❌ Failed to import or test settings: {e}")
        return False

def test_environment_file():
    """Test if environment file is being loaded correctly"""
    print("\n📁 Environment File Test:")
    print("-" * 30)

    env_files = ['.env', '.env.example']
    for env_file in env_files:
        if os.path.exists(env_file):
            print(f"✅ {env_file} exists")
            try:
                with open(env_file, 'r') as f:
                    lines = f.readlines()
                    smtp_lines = [line for line in lines if 'SMTP' in line or 'EMAIL' in line]
                    print(f"   Found {len(smtp_lines)} SMTP/EMAIL related variables")
            except Exception as e:
                print(f"❌ Error reading {env_file}: {e}")
        else:
            print(f"❌ {env_file} not found")

if __name__ == "__main__":
    print("🚀 Starting Configuration Tests...\n")

    # Test environment files first
    test_environment_file()

    # Run main configuration test
    success = test_config_loading()

    if success:
        print("\n🎉 ALL TESTS PASSED! Your configuration is correctly set up.")
        sys.exit(0)
    else:
        print("\n❌ SOME TESTS FAILED! Please check your configuration.")
        sys.exit(1)
