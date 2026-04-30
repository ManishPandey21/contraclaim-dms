# Email Sharing Setup and Testing Guide

## Overview

This document provides complete instructions for setting up and testing the email sharing functionality in ContraClaim DMS, including SMTP configuration and end-to-end testing.

## Recent Changes

### Frontend Fix (ShareDocumentPage.tsx)

**Issue**: Email subject was using `document.filename` instead of the letter's subject.

**Fix Applied**: Changed the priority order to use `document.subject` first:

```typescript
// Before:
const docLabel =
  document.filename || document.subject || document.letterNo || "Document";

// After:
const docLabel =
  document.subject || document.letterNo || document.filename || "Document";
```

**Result**: Email subject now shows "Sharing document: [Letter Subject]" instead of "Sharing document: [filename.pdf]"

## SMTP Configuration

### Environment Variables (.env file)

The email service reads SMTP configuration from environment variables. Add these to your `.env` file in the `backend/` directory:

```env
# SMTP Configuration
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
FROM_EMAIL=noreply@contraclaim.com

# Application URL (for email links)
APP_URL=http://localhost:5173
```

### SMTP Provider Examples

#### Gmail

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-16-char-app-password
```

**Note**: For Gmail, you need to:

1. Enable 2-Factor Authentication
2. Generate an App Password at: https://myaccount.google.com/apppasswords
3. Use the 16-character app password (not your regular password)

#### Outlook/Office 365

```env
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=your-email@outlook.com
SMTP_PASSWORD=your-password
```

#### SendGrid

```env
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=your-sendgrid-api-key
```

#### AWS SES

```env
SMTP_HOST=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USER=your-ses-smtp-username
SMTP_PASSWORD=your-ses-smtp-password
```

## Backend Email Service Architecture

### Email Service (email_service.py)

Located at: `backend/rbac_backend/services/email_service.py`

**Key Features**:

- Reads SMTP configuration from environment variables
- Uses `aiosmtplib` for async email sending
- Supports both HTML and plain text emails
- Handles STARTTLS encryption
- Background task execution for non-blocking sends

**SMTP Configuration Check**:

```python
@property
def _smtp_enabled(self) -> bool:
    return bool(self.smtp_host and self.smtp_user and self.smtp_password and aiosmtplib)
```

### Email Router (email_share.py)

Located at: `backend/rbac_backend/routers/email_share.py`

**Endpoints**:

1. `POST /email/share-document` - Share document via email
2. `GET /email/suggestions` - Get email suggestions
3. `POST /email/resolve-recipients` - Resolve recipients for autocomplete

## End-to-End Testing Guide

### 1. Backend Setup

```bash
cd backend

# Install dependencies (if not already installed)
pip install aiosmtplib

# Create/update .env file with SMTP settings
nano .env  # or use your preferred editor

# Start the backend server
python -m rbac_backend.main
# or
uvicorn rbac_backend.main:app --reload --port 8000
```

### 2. Frontend Setup

```bash
cd client

# Install dependencies (if not already installed)
npm install

# Start the development server
npm run dev
```

### 3. Test Email Sharing

#### Step 1: Navigate to Document

1. Open browser: `http://localhost:5173`
2. Login to the application
3. Navigate to a document (e.g., `/documentviewer/[document-id]`)

#### Step 2: Access Share Page

1. Click the "Share" button on the document viewer
2. You should be redirected to: `/share/[document-id]`

#### Step 3: Verify Subject Line

1. Check that the "Subject" field shows: `Sharing document: [Letter Subject]`
2. NOT: `Sharing document: [filename.pdf]`

#### Step 4: Add Recipients

1. **Option A - Manual Entry**:
   - Type email address in "To" field
   - Press Enter or comma to add
2. **Option B - Autocomplete**:
   - Start typing name or email
   - Select from suggestions (Representatives/Parties)
3. **Option C - Email Groups**:
   - Select groups from the list
   - Choose target (To/CC/BCC)
   - Click "Apply Group Recipients"

#### Step 5: Configure Email Options

- ✅ Include letter link (recommended)
- ✅ Include reference letters (if applicable)
- ✅ Link enclosures (if applicable)
- Choose message format: HTML or Plain Text

#### Step 6: Customize Message

1. Edit the subject if needed
2. Modify the message body
3. Add any additional context

#### Step 7: Send Email

1. Click "Send Email" button
2. Wait for success toast notification
3. Check for any error messages

### 4. Verify Email Delivery

#### Check Backend Logs

```bash
# Look for email sending logs
tail -f backend/logs/app.log

# Expected log entries:
# - "Email queued for delivery"
# - SMTP connection logs
# - Success/failure messages
```

#### Check Recipient Inbox

1. Check recipient's email inbox
2. Verify email received with correct:
   - Subject: "Sharing document: [Letter Subject]"
   - From: Your configured FROM_EMAIL
   - Body: Your custom message
   - Links: Document view link (if enabled)

#### Check Spam Folder

If email not in inbox, check spam/junk folder

### 5. Common Issues and Solutions

#### Issue: "SMTP not configured"

**Solution**:

- Verify `.env` file has all SMTP variables
- Restart backend server after updating `.env`
- Check `aiosmtplib` is installed: `pip list | grep aiosmtplib`

#### Issue: "Authentication failed"

**Solution**:

- For Gmail: Use App Password, not regular password
- Verify SMTP_USER and SMTP_PASSWORD are correct
- Check if 2FA is enabled (required for Gmail)

#### Issue: "Connection timeout"

**Solution**:

- Verify SMTP_HOST and SMTP_PORT are correct
- Check firewall settings
- Try alternative port (465 for SSL, 587 for TLS)

#### Issue: "Email not received"

**Solution**:

- Check spam/junk folder
- Verify recipient email is valid
- Check backend logs for sending errors
- Test with a different email provider

#### Issue: Subject shows filename instead of letter subject

**Solution**:

- Verify the fix is applied in ShareDocumentPage.tsx
- Clear browser cache
- Restart frontend dev server
- Check document has a `subject` field in database

### 6. Testing Checklist

- [ ] SMTP configuration in .env file
- [ ] Backend server running
- [ ] Frontend server running
- [ ] Can access share page
- [ ] Subject shows letter subject (not filename)
- [ ] Can add recipients manually
- [ ] Autocomplete suggestions work
- [ ] Email groups work (if configured)
- [ ] Can select CC/BCC recipients
- [ ] Can toggle HTML/Plain text format
- [ ] Can include/exclude letter link
- [ ] Can include reference letters
- [ ] Can include enclosures
- [ ] Send button works
- [ ] Success toast appears
- [ ] Email received in inbox
- [ ] Email content is correct
- [ ] Links in email work

### 7. Advanced Testing

#### Test Multiple Recipients

```
To: user1@example.com, user2@example.com
CC: user3@example.com
BCC: user4@example.com
```

#### Test HTML Formatting

```html
<p>Hello,</p>
<p>Please review the <strong>important</strong> document.</p>
<ul>
  <li>Item 1</li>
  <li>Item 2</li>
</ul>
```

#### Test Reference Letters

1. Enable "Include reference letters"
2. Select multiple references
3. Verify links in email

#### Test Email Groups

1. Create email group with multiple members
2. Select group
3. Apply to To/CC/BCC
4. Verify all members receive email

## Security Considerations

1. **SMTP Credentials**: Never commit `.env` file to version control
2. **Email Validation**: Backend validates all email addresses
3. **HTML Sanitization**: User input is sanitized to prevent XSS
4. **Rate Limiting**: Email sending is rate-limited per user
5. **Authorization**: Users must have permission to share documents

## Monitoring and Logs

### Backend Logs

```bash
# View real-time logs
tail -f backend/logs/app.log

# Search for email-related logs
grep "email" backend/logs/app.log

# Check for errors
grep "ERROR" backend/logs/app.log | grep "email"
```

### Database Audit

Email sending events can be tracked in the audit log (if implemented):

```javascript
// Check audit logs
db.audit_logs.find({ action: "email_sent" }).sort({ timestamp: -1 }).limit(10);
```

## Troubleshooting Commands

### Test SMTP Connection (Python)

```python
import aiosmtplib
import asyncio
from email.message import EmailMessage

async def test_smtp():
    message = EmailMessage()
    message["From"] = "your-email@gmail.com"
    message["To"] = "test@example.com"
    message["Subject"] = "Test Email"
    message.set_content("This is a test email")

    smtp = aiosmtplib.SMTP(hostname="smtp.gmail.com", port=587)
    await smtp.connect()
    await smtp.starttls()
    await smtp.login("your-email@gmail.com", "your-app-password")
    await smtp.send_message(message)
    await smtp.quit()
    print("Email sent successfully!")

asyncio.run(test_smtp())
```

### Check Environment Variables

```bash
# In backend directory
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('SMTP_HOST:', os.getenv('SMTP_HOST')); print('SMTP_USER:', os.getenv('SMTP_USER'))"
```

## Support

For additional help:

1. Check backend logs for detailed error messages
2. Verify SMTP provider documentation
3. Test SMTP credentials with standalone script
4. Contact system administrator for firewall/network issues

## Changelog

### 2024-01-XX

- Fixed subject line to use letter subject instead of filename
- Updated ShareDocumentPage.tsx priority order
- Added comprehensive testing guide
- Documented SMTP configuration for multiple providers
