# services/template_service.py

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path
import os
from jinja2 import Environment, BaseLoader, select_autoescape, Template, TemplateNotFound
from jinja2.sandbox import SandboxedEnvironment
import bleach
import html

from ..core.config import settings
from ..core.database import get_database
from ..models.email_models import EmailTemplate

logger = logging.getLogger(__name__)

class TemplateServiceError(Exception):
    """Custom exception for template service errors."""
    pass

class DatabaseTemplateLoader(BaseLoader):
    """Custom Jinja2 loader that loads templates from database."""
    
    def __init__(self, db):
        self.db = db
    
    async def get_source(self, environment, template_name):
        """Load template from database."""
        try:
            template_doc = await self.db.email_templates.find_one({
                'name': template_name,
                'active': True
            })
            
            if not template_doc:
                raise TemplateNotFound(template_name)
            
            source = template_doc.get('content', '')
            # Return (source, filename, uptodate_func)
            return source, template_name, lambda: True
            
        except Exception as e:
            logger.error(f"Failed to load template {template_name}: {str(e)}")
            raise TemplateNotFound(template_name)

class TemplateService:
    """Service for managing and rendering email templates securely."""
    
    def __init__(self):
        self.db = None
        self.jinja_env = None
        self.default_templates = self._get_default_templates()
        
    async def _get_db(self):
        """Get database connection."""
        if self.db is None:
            self.db = await get_database()
        return self.db
    
    async def _get_jinja_env(self):
        """Get Jinja2 environment with database loader."""
        if not self.jinja_env:
            db = await self._get_db()
            loader = DatabaseTemplateLoader(db)
            
            # Use sandboxed environment for security
            self.jinja_env = SandboxedEnvironment(
                loader=loader,
                autoescape=select_autoescape(['html', 'xml']),
                trim_blocks=True,
                lstrip_blocks=True
            )
            
            # Add custom filters
            self.jinja_env.filters['escape_html'] = html.escape
            self.jinja_env.filters['clean_html'] = self._clean_html_filter
            
        return self.jinja_env
    
    def _clean_html_filter(self, text, allowed_tags=None):
        """Jinja2 filter to clean HTML content."""
        if not text:
            return ""
        
        if allowed_tags is None:
            allowed_tags = ['p', 'br', 'strong', 'em', 'b', 'i', 'ul', 'ol', 'li']
        
        return bleach.clean(text, tags=allowed_tags, strip=True)
    
    def _get_default_templates(self) -> Dict[str, str]:
        """Get default email templates."""
        return {
            'document_share': """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Document Shared</title>
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: #f8f9fa; padding: 20px; text-align: center; }
        .content { padding: 20px; background: white; }
        .actions { margin: 20px 0; }
        .btn { display: inline-block; padding: 12px 24px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; }
        .footer { font-size: 12px; color: #666; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Document Shared</h1>
        </div>
        <div class="content">
            <p>Hello,</p>
            <p><strong>{{ sender_name }}</strong> ({{ sender_email }}) has shared a document with you:</p>
            
            <h3>{{ document_title }}</h3>
            
            {% if recipient_message %}
            <div style="background: #f8f9fa; padding: 15px; border-left: 4px solid #007bff; margin: 20px 0;">
                <strong>Message from {{ sender_name }}:</strong><br>
                {{ recipient_message | clean_html | safe }}
            </div>
            {% endif %}
            
            <div class="actions">
                <a href="{{ view_url }}" class="btn" style="color: white;">View Document</a>
                <a href="{{ download_url }}" class="btn" style="color: white; background: #28a745; margin-left: 10px;">Download</a>
            </div>
            
            <p><small><strong>Note:</strong> These links will expire in 72 hours for security.</small></p>
        </div>
        <div class="footer">
            <p>This email was sent by {{ company_name }}.<br>
            If you have questions, contact us at {{ support_email }}.</p>
            <p>&copy; {{ current_year }} {{ company_name }}. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
            """,
            
            'notification': """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Notification</title>
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: #f8f9fa; padding: 20px; text-align: center; }
        .content { padding: 20px; background: white; }
        .footer { font-size: 12px; color: #666; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ title | default('Notification') }}</h1>
        </div>
        <div class="content">
            {{ content | clean_html | safe }}
        </div>
        <div class="footer">
            <p>This email was sent by {{ company_name }}.<br>
            If you have questions, contact us at {{ support_email }}.</p>
            <p>&copy; {{ current_year }} {{ company_name }}. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
            """,
            
            'welcome': """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Welcome</title>
    <style>
        body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: #007bff; color: white; padding: 20px; text-align: center; }
        .content { padding: 20px; background: white; }
        .footer { font-size: 12px; color: #666; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to {{ company_name }}!</h1>
        </div>
        <div class="content">
            <p>Hello {{ user_name }},</p>
            <p>Welcome to our document management system. Your account has been successfully created.</p>
            
            <p>You can now:</p>
            <ul>
                <li>Upload and manage documents</li>
                <li>Share documents securely</li>
                <li>Collaborate with team members</li>
            </ul>
            
            <p>If you have any questions, don't hesitate to contact our support team.</p>
            
            <p>Best regards,<br>The {{ company_name }} Team</p>
        </div>
        <div class="footer">
            <p>This email was sent by {{ company_name }}.<br>
            If you have questions, contact us at {{ support_email }}.</p>
            <p>&copy; {{ current_year }} {{ company_name }}. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
            """
        }
    
    async def get_template(self, template_name: str) -> Optional[Dict[str, Any]]:
        """Get template by name from database or defaults."""
        try:
            db = await self._get_db()
            
            # Try to get from database first
            template_doc = await db.email_templates.find_one({
                'name': template_name,
                'active': True
            })
            
            if template_doc:
                return {
                    'name': template_doc['name'],
                    'subject': template_doc.get('subject', ''),
                    'content': template_doc.get('content', ''),
                    'content_type': template_doc.get('content_type', 'html'),
                    'description': template_doc.get('description', ''),
                    'variables': template_doc.get('variables', [])
                }
            
            # Fallback to default templates
            if template_name in self.default_templates:
                return {
                    'name': template_name,
                    'subject': self._get_default_subject(template_name),
                    'content': self.default_templates[template_name],
                    'content_type': 'html',
                    'description': f'Default {template_name} template',
                    'variables': []
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get template {template_name}: {str(e)}")
            return None
    
    def _get_default_subject(self, template_name: str) -> str:
        """Get default subject for template."""
        subjects = {
            'document_share': 'Document Shared: {{ document_title }}',
            'notification': '{{ title | default("Notification") }}',
            'welcome': 'Welcome to {{ company_name }}!'
        }
        return subjects.get(template_name, 'Notification')
    
    async def render_secure_template(
        self, 
        template: Dict[str, Any], 
        data: Dict[str, Any]
    ) -> Dict[str, str]:
        """Render template with data securely."""
        try:
            # Sanitize data
            sanitized_data = self._sanitize_template_data(data)
            
            # Get template content
            template_content = template.get('content', '')
            template_subject = template.get('subject', 'Notification')
            
            if not template_content:
                raise TemplateServiceError("Template content is empty")
            
            # Create Jinja2 template
            jinja_template = Template(
                template_content,
                environment=await self._get_jinja_env()
            )
            
            subject_template = Template(
                template_subject,
                environment=await self._get_jinja_env()
            )
            
            # Render templates
            rendered_html = jinja_template.render(**sanitized_data)
            rendered_subject = subject_template.render(**sanitized_data)
            
            # Generate text version (basic HTML stripping)
            rendered_text = self._html_to_text(rendered_html)
            
            return {
                'subject': rendered_subject.strip(),
                'html_body': rendered_html,
                'text_body': rendered_text
            }
            
        except Exception as e:
            logger.error(f"Failed to render template: {str(e)}")
            raise TemplateServiceError(f"Template rendering failed: {str(e)}")
    
    def _sanitize_template_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize template data to prevent XSS."""
        sanitized = {}
        
        for key, value in data.items():
            if isinstance(value, str):
                # Don't escape URLs - they need to work in links
                if key.endswith('_url') or key.endswith('_link'):
                    sanitized[key] = value
                else:
                    sanitized[key] = html.escape(value)
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_template_data(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    html.escape(str(item)) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                sanitized[key] = value
                
        return sanitized
    
    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML to plain text."""
        if not html_content:
            return ""
        
        # Basic HTML to text conversion
        import re
        
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', html_content)
        # Decode HTML entities
        text = html.unescape(text)
        # Clean up whitespace
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        
        return text.strip()
    
    async def get_all_templates(self) -> List[EmailTemplate]:
        """Get all available templates."""
        try:
            db = await self._get_db()
            templates = []
            
            # Get templates from database
            db_templates = await db.email_templates.find({
                'active': True
            }).to_list(length=None)
            
            for template_doc in db_templates:
                templates.append(EmailTemplate(
                    name=template_doc['name'],
                    subject=template_doc.get('subject', ''),
                    description=template_doc.get('description', ''),
                    content_type=template_doc.get('content_type', 'html'),
                    variables=template_doc.get('variables', []),
                    created_at=template_doc.get('created_at', datetime.utcnow()),
                    updated_at=template_doc.get('updated_at', datetime.utcnow())
                ))
            
            # Add default templates that aren't in database
            for name, content in self.default_templates.items():
                if not any(t.name == name for t in templates):
                    templates.append(EmailTemplate(
                        name=name,
                        subject=self._get_default_subject(name),
                        description=f'Default {name} template',
                        content_type='html',
                        variables=[],
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    ))
            
            return templates
            
        except Exception as e:
            logger.error(f"Failed to get all templates: {str(e)}")
            return []
    
    async def create_template(
        self, 
        name: str, 
        subject: str, 
        content: str,
        description: str = "",
        variables: List[str] = None
    ) -> bool:
        """Create a new email template."""
        try:
            db = await self._get_db()
            
            template_doc = {
                'name': name,
                'subject': subject,
                'content': content,
                'description': description,
                'content_type': 'html',
                'variables': variables or [],
                'active': True,
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            }
            
            await db.email_templates.insert_one(template_doc)
            return True
            
        except Exception as e:
            logger.error(f"Failed to create template {name}: {str(e)}")
            return False
    
    async def update_template(
        self, 
        name: str, 
        updates: Dict[str, Any]
    ) -> bool:
        """Update an existing template."""
        try:
            db = await self._get_db()
            
            updates['updated_at'] = datetime.utcnow()
            
            result = await db.email_templates.update_one(
                {'name': name},
                {'$set': updates}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Failed to update template {name}: {str(e)}")
            return False
    
    async def delete_template(self, name: str) -> bool:
        """Soft delete a template by marking it inactive."""
        try:
            db = await self._get_db()
            
            result = await db.email_templates.update_one(
                {'name': name},
                {'$set': {'active': False, 'updated_at': datetime.utcnow()}}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Failed to delete template {name}: {str(e)}")
            return False



