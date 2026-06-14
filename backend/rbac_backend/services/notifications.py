import logging
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)

class NotificationChannel(str, Enum):
    """Available notification channels"""
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    SLACK = "slack"
    WEBHOOK = "webhook"

class NotificationPriority(str, Enum):
    """Notification priority levels"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

@dataclass
class NotificationTemplate:
    """Template for notification content"""
    subject_template: str
    message_template: str
    channel: NotificationChannel
    priority: NotificationPriority = NotificationPriority.NORMAL

class NotificationError(Exception):
    """Custom exception for notification errors"""
    pass

class NotificationService:
    """Service for sending notifications through various channels"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.templates: Dict[str, NotificationTemplate] = {}
        self._initialize_default_templates()
    
    def _initialize_default_templates(self):
        """Initialize default notification templates"""
        self.templates.update({
            "user_registered": NotificationTemplate(
                subject_template="Welcome to {app_name}",
                message_template="Hello {user_name}, welcome to our platform!",
                channel=NotificationChannel.EMAIL
            ),
            "password_reset": NotificationTemplate(
                subject_template="Password Reset Request",
                message_template="Click the link to reset your password: {reset_link}",
                channel=NotificationChannel.EMAIL,
                priority=NotificationPriority.HIGH
            ),
            "document_processed": NotificationTemplate(
                subject_template="Document Processing Complete",
                message_template="Your document '{document_name}' has been processed successfully.",
                channel=NotificationChannel.EMAIL
            ),
            "status_change": NotificationTemplate(
                subject_template="Status Update: {item_type}",
                message_template="The status of {item_name} has changed to {new_status}.",
                channel=NotificationChannel.EMAIL
            )
        })
    
    def _validate_user_id(self, user_id: str) -> None:
        """Validate user ID parameter"""
        if not user_id or not isinstance(user_id, str):
            raise ValueError("user_id must be a non-empty string")
    
    def _validate_content(self, subject: str, message: str) -> None:
        """Validate notification content"""
        if not subject or not isinstance(subject, str):
            raise ValueError("subject must be a non-empty string")
        
        if not message or not isinstance(message, str):
            raise ValueError("message must be a non-empty string")
    
    async def notify_user(
        self,
        user_id: str,
        subject: str,
        message: str,
        channel: NotificationChannel = NotificationChannel.EMAIL,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send notification to a user.
        
        Args:
            user_id: Target user ID
            subject: Notification subject
            message: Notification message
            channel: Notification channel to use
            priority: Notification priority
            metadata: Optional metadata for the notification
            
        Returns:
            True if notification was sent successfully
            
        Raises:
            NotificationError: If sending fails
        """
        try:
            # Validate inputs
            self._validate_user_id(user_id)
            self._validate_content(subject, message)
            
            if not isinstance(channel, NotificationChannel):
                raise ValueError("channel must be a NotificationChannel enum value")
            
            if not isinstance(priority, NotificationPriority):
                raise ValueError("priority must be a NotificationPriority enum value")
            
            logger.info(f"Sending {channel.value} notification to user {user_id}: {subject}")
            
            # Create notification payload
            notification_data = {
                "user_id": user_id,
                "subject": subject,
                "message": message,
                "channel": channel.value,
                "priority": priority.value,
                "timestamp": datetime.utcnow(),
                "metadata": metadata or {}
            }
            
            # Send through appropriate channel
            success = await self._send_via_channel(channel, notification_data)
            
            if success:
                logger.info(f"Successfully sent notification to {user_id} via {channel.value}")
            else:
                logger.warning(f"Failed to send notification to {user_id} via {channel.value}")
            
            return success
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to send notification to {user_id}: {e}")
            raise NotificationError(f"Notification sending failed: {str(e)}")
    
    async def notify_multiple_users(
        self,
        user_ids: List[str],
        subject: str,
        message: str,
        channel: NotificationChannel = NotificationChannel.EMAIL,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, bool]:
        """
        Send notifications to multiple users.
        
        Args:
            user_ids: List of target user IDs
            subject: Notification subject
            message: Notification message
            channel: Notification channel to use
            priority: Notification priority
            metadata: Optional metadata for the notifications
            
        Returns:
            Dictionary mapping user IDs to success status
        """
        if not user_ids:
            return {}
        
        logger.info(f"Sending bulk notifications to {len(user_ids)} users")
        
        results = {}
        
        # Send notifications concurrently
        tasks = []
        for user_id in user_ids:
            task = self.notify_user(user_id, subject, message, channel, priority, metadata)
            tasks.append((user_id, task))
        
        # Wait for all tasks to complete
        for user_id, task in tasks:
            try:
                success = await task
                results[user_id] = success
            except Exception as e:
                logger.error(f"Failed to send notification to {user_id}: {e}")
                results[user_id] = False
        
        successful = sum(1 for success in results.values() if success)
        logger.info(f"Bulk notification complete: {successful}/{len(user_ids)} successful")
        
        return results
    
    async def notify_from_template(
        self,
        user_id: str,
        template_name: str,
        template_vars: Dict[str, Any],
        channel: Optional[NotificationChannel] = None,
        priority: Optional[NotificationPriority] = None
    ) -> bool:
        """
        Send notification using a predefined template.
        
        Args:
            user_id: Target user ID
            template_name: Name of the template to use
            template_vars: Variables to substitute in template
            channel: Optional channel override
            priority: Optional priority override
            
        Returns:
            True if notification was sent successfully
            
        Raises:
            NotificationError: If template not found or sending fails
        """
        try:
            if template_name not in self.templates:
                raise NotificationError(f"Template '{template_name}' not found")
            
            template = self.templates[template_name]
            
            # Format template content
            try:
                subject = template.subject_template.format(**template_vars)
                message = template.message_template.format(**template_vars)
            except KeyError as e:
                raise NotificationError(f"Missing template variable: {e}")
            
            # Use template defaults or overrides
            notification_channel = channel or template.channel
            notification_priority = priority or template.priority
            
            return await self.notify_user(
                user_id, subject, message, notification_channel, notification_priority
            )
            
        except NotificationError:
            raise
        except Exception as e:
            logger.error(f"Failed to send template notification: {e}")
            raise NotificationError(f"Template notification failed: {str(e)}")
    
    async def _send_via_channel(self, channel: NotificationChannel, data: Dict[str, Any]) -> bool:
        """
        Send notification via specific channel.
        This is where actual implementation would go.
        
        Args:
            channel: Channel to send through
            data: Notification data
            
        Returns:
            True if sent successfully
        """
        try:
            if channel == NotificationChannel.EMAIL:
                return await self._send_email(data)
            elif channel == NotificationChannel.SMS:
                return await self._send_sms(data)
            elif channel == NotificationChannel.PUSH:
                return await self._send_push(data)
            elif channel == NotificationChannel.SLACK:
                return await self._send_slack(data)
            elif channel == NotificationChannel.WEBHOOK:
                return await self._send_webhook(data)
            else:
                logger.error(f"Unsupported notification channel: {channel}")
                return False
                
        except Exception as e:
            logger.error(f"Channel {channel.value} sending failed: {e}")
            return False
    
    async def _send_email(self, data: Dict[str, Any]) -> bool:
        """Send email notification (placeholder implementation)"""
        # TODO: Implement actual email sending
        # Could use: SMTP, SendGrid, AWS SES, etc.
        logger.info(f"[EMAIL] To: {data['user_id']} | Subject: {data['subject']}")
        await asyncio.sleep(0.1)  # Simulate network delay
        return True
    
    async def _send_sms(self, data: Dict[str, Any]) -> bool:
        """Send SMS notification (placeholder implementation)"""
        # TODO: Implement actual SMS sending
        # Could use: Twilio, AWS SNS, etc.
        logger.info(f"[SMS] To: {data['user_id']} | Message: {data['message'][:50]}...")
        await asyncio.sleep(0.1)  # Simulate network delay
        return True
    
    async def _send_push(self, data: Dict[str, Any]) -> bool:
        """Send push notification (placeholder implementation)"""
        # TODO: Implement actual push notification
        # Could use: Firebase, Apple Push, etc.
        logger.info(f"[PUSH] To: {data['user_id']} | Subject: {data['subject']}")
        await asyncio.sleep(0.1)  # Simulate network delay
        return True
    
    async def _send_slack(self, data: Dict[str, Any]) -> bool:
        """Send Slack notification (placeholder implementation)"""
        # TODO: Implement actual Slack integration
        logger.info(f"[SLACK] To: {data['user_id']} | Message: {data['message'][:50]}...")
        await asyncio.sleep(0.1)  # Simulate network delay
        return True
    
    async def _send_webhook(self, data: Dict[str, Any]) -> bool:
        """Send webhook notification (placeholder implementation)"""
        # TODO: Implement actual webhook sending
        logger.info(f"[WEBHOOK] To: {data['user_id']} | Data: {data}")
        await asyncio.sleep(0.1)  # Simulate network delay
        return True
    
    def register_template(self, name: str, template: NotificationTemplate) -> None:
        """
        Register a new notification template.
        
        Args:
            name: Template name
            template: NotificationTemplate instance
        """
        if not name or not isinstance(name, str):
            raise ValueError("Template name must be a non-empty string")
        
        if not isinstance(template, NotificationTemplate):
            raise ValueError("template must be a NotificationTemplate instance")
        
        self.templates[name] = template
        logger.info(f"Registered notification template: {name}")
    
    def get_available_templates(self) -> List[str]:
        """Get list of available template names"""
        return list(self.templates.keys())

# Global notification service instance
notification_service = NotificationService()

# Convenience functions for backward compatibility
async def notify_user(user_id: str, subject: str, message: str) -> bool:
    """Convenience function to send basic notification"""
    return await notification_service.notify_user(user_id, subject, message)

# Factory function
def create_notification_service(config: Optional[Dict[str, Any]] = None) -> NotificationService:
    """Create notification service instance"""
    return NotificationService(config)
