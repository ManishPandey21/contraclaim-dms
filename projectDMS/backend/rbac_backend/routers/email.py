# Improved email.py
"""
Secure email service with comprehensive validation, rate limiting, and async operations.
Addresses XSS vulnerabilities, email injection, and performance issues.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional, Dict, Any, Union
import asyncio
import logging
from datetime import datetime, timedelta
import html
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
import aiosmtplib
from jinja2 import Environment, BaseLoader, select_autoescape
import bleach

from ..core.permissions import Permissions
from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.database import get_db
from ..core.config import settings
from ..services.email_service import EmailService
from ..services.authorization_service import AuthorizationService
from ..services.policy_service import PolicyService
from ..services.template_service import TemplateService
from ..models.email_models import (
    EmailRequest, EmailResponse, EmailTemplate, EmailAttachment
)
from ..utils.validation import validate_input, sanitize_html, validate_email
from ..utils.error_handler import handle_exceptions, EmailError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()


class EmailController:
    """Secure email controller with comprehensive validation and rate limiting."""
    
    def __init__(
        self,
        email_service: EmailService,
        auth_service: AuthorizationService,
        template_service: TemplateService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.email_service = email_service
        self.auth_service = auth_service
        self.template_service = template_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def send_document_share_email(
        self,
        background_tasks: BackgroundTasks,
        email_request: EmailRequest,
        current_user: CurrentUser
    ) -> EmailResponse:
        """Send document sharing email with security validation."""
        try:
            # Rate limiting - email sending is expensive
            await self.rate_limiter.check_user_limit(
                current_user.id, 
                cost=5,  # High cost for email sending
                window_seconds=3600,  # 1 hour window
                max_requests=20  # Max 20 emails per hour per user
            )
            
            # Authorization check
            await self.auth_service.require_permission(
                current_user, "emails:send"
            )
            
            # Validate and sanitize email request
            validated_request = await self._validate_email_request(email_request)
            
            # Verify document access
            document = await self._verify_document_access(
                validated_request.document_id, current_user
            )
            
            # Generate secure URLs with expiration
            document_urls = await self.email_service.generate_secure_document_urls(
                document, expire_hours=72
            )
            
            # Build email content using secure templates
            email_content = await self._build_secure_email_content(
                validated_request, document, document_urls, current_user
            )
            
            # Queue email for background sending
            background_tasks.add_task(
                self._send_email_async,
                validated_request.recipients,
                email_content,
                current_user.id,
                validated_request.document_id
            )
            
            # Audit log
            await self.audit_logger.log_email_sent(
                current_user.id,
                validated_request.recipients,
                document.get("id"),
                "document_share"
            )
            
            return EmailResponse(
                message="Email queued for delivery",
                recipients=validated_request.recipients,
                status="queued"
            )
            
        except (EmailError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Email sending failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Email service temporarily unavailable"
            )

    async def send_notification_email(
        self,
        background_tasks: BackgroundTasks,
        template_name: str,
        recipients: List[EmailStr],
        template_data: Dict[str, Any],
        current_user: CurrentUser
    ) -> EmailResponse:
        """Send template-based notification email."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Authorization check
            await self.auth_service.require_permission(
                current_user, "emails:send_notifications"
            )
            
            # Validate inputs
            validated_recipients = [validate_email(email) for email in recipients]
            sanitized_data = await self._sanitize_template_data(template_data)
            
            # Get and validate template
            template = await self.template_service.get_template(template_name)
            if not template:
                raise EmailError(
                    f"Template '{template_name}' not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Render email content securely
            email_content = await self.template_service.render_secure_template(
                template, sanitized_data
            )
            
            # Queue email
            background_tasks.add_task(
                self._send_template_email_async,
                validated_recipients,
                email_content,
                current_user.id,
                template_name
            )
            
            return EmailResponse(
                message="Notification email queued",
                recipients=validated_recipients,
                status="queued"
            )
            
        except (EmailError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Notification email failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Notification service temporarily unavailable"
            )

    async def _validate_email_request(self, request: EmailRequest) -> EmailRequest:
        """Comprehensive email request validation."""
        # Validate recipients
        validated_recipients = []
        for email in request.recipients:
            validated_email = validate_email(email)
            validated_recipients.append(validated_email)
        
        # Validate and sanitize subject
        safe_subject = sanitize_html(
            validate_input(request.subject, max_length=200)
        )
        
        # Validate and sanitize message
        safe_message = sanitize_html(
            validate_input(request.message, max_length=5000)
        )
        
        # Validate document ID
        document_id = validate_input(request.document_id, required=True)
        
        return EmailRequest(
            recipients=validated_recipients,
            subject=safe_subject,
            message=safe_message,
            document_id=document_id,
            template_name=request.template_name,
            attachments=request.attachments or []
        )

    async def _verify_document_access(
        self, document_id: str, current_user: CurrentUser
    ) -> Dict[str, Any]:
        """Verify user has access to document being shared."""
        document = await self.email_service.get_document_by_id(document_id)
        if not document:
            raise EmailError("Document not found", status.HTTP_404_NOT_FOUND)
        
        await PolicyService().authorize_document(
            current_user,
            Permissions.DOCUMENT_SHARE,
            document,
            resource_type="document_share",
        )
        
        return document

    async def _build_secure_email_content(
        self,
        request: EmailRequest,
        document: Dict[str, Any],
        urls: Dict[str, str],
        current_user: CurrentUser
    ) -> Dict[str, str]:
        """Build email content using secure templates."""
        
        # Prepare template data with sanitization
        template_data = {
            'recipient_message': bleach.clean(
                request.message,
                tags=['p', 'br', 'strong', 'em', 'ul', 'ol', 'li'],
                strip=True
            ),
            'document_title': html.escape(document.get('title', 'Document')),
            'document_id': html.escape(document.get('id', '')),
            'sender_name': html.escape(f"{current_user.first_name} {current_user.last_name}"),
            'sender_email': html.escape(current_user.email),
            'view_url': html.escape(urls.get('view_url', '')),
            'download_url': html.escape(urls.get('download_url', '')),
            'current_year': datetime.now().year,
            'company_name': html.escape(settings.email.COMPANY_NAME),
            'support_email': html.escape(settings.email.SUPPORT_EMAIL)
        }
        
        # Use secure template rendering
        template_name = request.template_name or 'document_share'
        return await self.template_service.render_secure_template(
            template_name, template_data
        )

    async def _sanitize_template_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize template data to prevent XSS."""
        sanitized = {}
        for key, value in data.items():
            if isinstance(value, str):
                sanitized[key] = html.escape(value)
            elif isinstance(value, dict):
                sanitized[key] = await self._sanitize_template_data(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    html.escape(str(item)) if isinstance(item, str) else item 
                    for item in value
                ]
            else:
                sanitized[key] = value
        
        return sanitized

    async def _send_email_async(
        self,
        recipients: List[str],
        content: Dict[str, str],
        sender_user_id: str,
        document_id: Optional[str] = None
    ):
        """Send email asynchronously with error handling."""
        try:
            await self.email_service.send_secure_email(
                recipients=recipients,
                subject=content.get('subject', 'Document Shared'),
                html_content=content.get('html_body', ''),
                text_content=content.get('text_body', ''),
                sender_user_id=sender_user_id
            )
            
            # Log successful delivery
            await self.audit_logger.log_email_delivered(
                sender_user_id, recipients, document_id
            )
            
        except Exception as e:
            logger.error(f"Async email sending failed: {str(e)}")
            await self.audit_logger.log_email_failed(
                sender_user_id, recipients, str(e)
            )

    async def _send_template_email_async(
        self,
        recipients: List[str],
        content: Dict[str, str],
        sender_user_id: str,
        template_name: str
    ):
        """Send template-based email asynchronously."""
        try:
            await self.email_service.send_secure_email(
                recipients=recipients,
                subject=content.get('subject', 'Notification'),
                html_content=content.get('html_body', ''),
                text_content=content.get('text_body', ''),
                sender_user_id=sender_user_id
            )
            
            await self.audit_logger.log_template_email_sent(
                sender_user_id, recipients, template_name
            )
            
        except Exception as e:
            logger.error(f"Template email sending failed: {str(e)}")
            await self.audit_logger.log_email_failed(
                sender_user_id, recipients, str(e)
            )


# Dependency injection
async def get_email_controller() -> EmailController:
    """Factory function for email controller."""
    email_service = EmailService()
    auth_service = AuthorizationService()
    template_service = TemplateService()
    rate_limiter = RateLimiter(
        max_requests=30,
        window_seconds=3600,
        scope="email",
    )
    audit_logger = AuditLogger()
    
    return EmailController(
        email_service, auth_service, template_service, 
        rate_limiter, audit_logger
    )


# API Endpoints
@router.post("/send-document-share", response_model=EmailResponse)
@handle_exceptions
async def send_document_share_email(
    background_tasks: BackgroundTasks,
    email_request: EmailRequest,
    controller: EmailController = Depends(get_email_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Send secure document sharing email."""
    return await controller.send_document_share_email(
        background_tasks, email_request, current_user
    )


@router.post("/send-notification", response_model=EmailResponse)
@handle_exceptions
async def send_notification_email(
    background_tasks: BackgroundTasks,
    template_name: str,
    recipients: List[EmailStr],
    template_data: Dict[str, Any],
    controller: EmailController = Depends(get_email_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Send template-based notification email."""
    return await controller.send_notification_email(
        background_tasks, template_name, recipients, template_data, current_user
    )


@router.get("/templates", response_model=List[EmailTemplate])
@handle_exceptions
async def get_email_templates(
    controller: EmailController = Depends(get_email_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get available email templates."""
    await controller.auth_service.require_permission(
        current_user, "emails:view_templates"
    )
    return await controller.template_service.get_all_templates()


@router.get("/send-history")
@handle_exceptions
async def get_email_history(
    limit: int = 50,
    controller: EmailController = Depends(get_email_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get email sending history for current user."""
    await controller.auth_service.require_permission(
        current_user, "emails:view_history"
    )
    return await controller.audit_logger.get_user_email_history(
        current_user.id, limit
    )
