import logging
from datetime import datetime, timezone
from typing import Dict, Set, Optional, List
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)

class WorkflowStatus(str, Enum):
    """Valid workflow statuses with string values"""
    DRAFT = "Draft"
    INPUT = "Input"
    STRATEGY = "Strategy"
    REVIEW = "Review"
    APPROVAL = "Approval"
    COMPLETED = "Completed"
    REJECTED = "Rejected"

@dataclass
class WorkflowTransition:
    """Represents a workflow transition with metadata"""
    from_status: WorkflowStatus
    to_status: WorkflowStatus
    timestamp: datetime
    user_id: Optional[str] = None
    comment: Optional[str] = None

class WorkflowError(Exception):
    """Custom exception for workflow errors"""
    pass

class InvalidStatusError(WorkflowError):
    """Exception raised when status is invalid"""
    pass

class InvalidTransitionError(WorkflowError):
    """Exception raised when transition is not allowed"""
    pass

class WorkflowEngine:
    """Engine for managing workflow transitions and validation"""

    # Define valid transitions as a mapping
    VALID_TRANSITIONS: Dict[WorkflowStatus, Set[WorkflowStatus]] = {
        WorkflowStatus.DRAFT: {WorkflowStatus.REVIEW, WorkflowStatus.STRATEGY, WorkflowStatus.REJECTED},
        WorkflowStatus.INPUT: {WorkflowStatus.STRATEGY, WorkflowStatus.REJECTED},
        WorkflowStatus.STRATEGY: {WorkflowStatus.DRAFT, WorkflowStatus.INPUT, WorkflowStatus.REJECTED},
        WorkflowStatus.REVIEW: {WorkflowStatus.APPROVAL, WorkflowStatus.DRAFT, WorkflowStatus.REJECTED},
        WorkflowStatus.APPROVAL: {WorkflowStatus.COMPLETED, WorkflowStatus.DRAFT, WorkflowStatus.REJECTED},
        WorkflowStatus.COMPLETED: set(),  # Terminal state
        WorkflowStatus.REJECTED: {WorkflowStatus.DRAFT},
    }

    def __init__(self):
        self.transition_history: Dict[str, List[WorkflowTransition]] = {}

    def _validate_status(self, status: str) -> WorkflowStatus:
        """
        Validate and convert status string to WorkflowStatus enum.

        Args:
            status: Status string to validate

        Returns:
            WorkflowStatus enum value

        Raises:
            InvalidStatusError: If status is invalid
        """
        if not isinstance(status, str):
            raise InvalidStatusError("Status must be a string")

        try:
            return WorkflowStatus(status)
        except ValueError:
            valid_statuses = [s.value for s in WorkflowStatus]
            raise InvalidStatusError(f"Invalid status '{status}'. Valid statuses: {valid_statuses}")

    def can_transition(self, current_status: str, new_status: str) -> bool:
        """
        Check if transition from current_status to new_status is allowed.

        Args:
            current_status: Current status string
            new_status: Desired new status string

        Returns:
            True if transition is allowed, False otherwise

        Raises:
            InvalidStatusError: If either status is invalid
        """
        try:
            current = self._validate_status(current_status)
            new = self._validate_status(new_status)

            # Allow staying in the same status
            if current == new:
                return True

            return new in self.VALID_TRANSITIONS.get(current, set())

        except InvalidStatusError:
            raise
        except Exception as e:
            logger.error(f"Error checking transition {current_status} -> {new_status}: {e}")
            return False

    def get_valid_transitions(self, current_status: str) -> List[str]:
        """
        Get list of valid transition statuses from current status.

        Args:
            current_status: Current status string

        Returns:
            List of valid next status strings

        Raises:
            InvalidStatusError: If current status is invalid
        """
        try:
            current = self._validate_status(current_status)
            valid_statuses = self.VALID_TRANSITIONS.get(current, set())
            return [status.value for status in valid_statuses]

        except InvalidStatusError:
            raise
        except Exception as e:
            logger.error(f"Error getting valid transitions for {current_status}: {e}")
            return []

    def execute_transition(
        self,
        item_id: str,
        current_status: str,
        new_status: str,
        user_id: Optional[str] = None,
        comment: Optional[str] = None
    ) -> WorkflowTransition:
        """
        Execute a workflow transition if valid.

        Args:
            item_id: Unique identifier for the item being transitioned
            current_status: Current status string
            new_status: Desired new status string
            user_id: Optional user ID performing the transition
            comment: Optional comment about the transition

        Returns:
            WorkflowTransition object representing the executed transition

        Raises:
            InvalidTransitionError: If transition is not allowed
            InvalidStatusError: If either status is invalid
        """
        try:
            if not item_id or not isinstance(item_id, str):
                raise ValueError("item_id must be a non-empty string")

            # Validate transition
            if not self.can_transition(current_status, new_status):
                raise InvalidTransitionError(
                    f"Transition from '{current_status}' to '{new_status}' is not allowed"
                )

            # Create transition record
            transition = WorkflowTransition(
                from_status=self._validate_status(current_status),
                to_status=self._validate_status(new_status),
                timestamp=datetime.now(timezone.utc),
                user_id=user_id,
                comment=comment
            )

            # Store in history
            if item_id not in self.transition_history:
                self.transition_history[item_id] = []

            self.transition_history[item_id].append(transition)

            logger.info(
                f"Executed transition for {item_id}: {current_status} -> {new_status}"
                + (f" by {user_id}" if user_id else "")
            )

            return transition

        except (InvalidStatusError, InvalidTransitionError):
            raise
        except Exception as e:
            logger.error(f"Failed to execute transition for {item_id}: {e}")
            raise WorkflowError(f"Transition execution failed: {str(e)}")

    def get_transition_history(self, item_id: str) -> List[WorkflowTransition]:
        """
        Get transition history for an item.

        Args:
            item_id: Item identifier

        Returns:
            List of WorkflowTransition objects ordered by timestamp
        """
        if not item_id or not isinstance(item_id, str):
            raise ValueError("item_id must be a non-empty string")

        return self.transition_history.get(item_id, [])

    def is_terminal_status(self, status: str) -> bool:
        """
        Check if status is a terminal status (no further transitions possible).

        Args:
            status: Status string to check

        Returns:
            True if status is terminal, False otherwise

        Raises:
            InvalidStatusError: If status is invalid
        """
        try:
            status_enum = self._validate_status(status)
            return len(self.VALID_TRANSITIONS.get(status_enum, set())) == 0

        except InvalidStatusError:
            raise
        except Exception as e:
            logger.error(f"Error checking if status is terminal: {e}")
            return False

    def is_terminal(self, status: str) -> bool:
        """
        Backward compatible alias for is_terminal_status used by LangGraph pipelines.
        """
        return self.is_terminal_status(status)

    def get_all_statuses(self) -> List[str]:
        """Get list of all valid status strings"""
        return [status.value for status in WorkflowStatus]

class WorkflowUtils:
    """Utility functions for workflow operations"""

    @staticmethod
    def compute_pendency_days(updated_at: datetime) -> int:
        """
        Calculate number of days since last update.

        Args:
            updated_at: Last update datetime

        Returns:
            Number of days since update

        Raises:
            ValueError: If updated_at is invalid
        """
        if not isinstance(updated_at, datetime):
            raise ValueError("updated_at must be a datetime object")

        try:
            # Handle timezone-aware and naive datetimes
            now = datetime.now(timezone.utc)

            # Convert to UTC if timezone-aware
            if updated_at.tzinfo is not None:
                updated_utc = updated_at.astimezone(timezone.utc)
            else:
                # Assume naive datetime is in UTC
                updated_utc = updated_at.replace(tzinfo=timezone.utc)

            delta = now - updated_utc
            return max(0, delta.days)  # Don't return negative days

        except Exception as e:
            logger.error(f"Error computing pendency days: {e}")
            return 0

    @staticmethod
    def is_overdue(updated_at: datetime, max_days: int = 7) -> bool:
        """
        Check if an item is overdue based on last update.

        Args:
            updated_at: Last update datetime
            max_days: Maximum allowed days without update

        Returns:
            True if overdue, False otherwise
        """
        try:
            pendency_days = WorkflowUtils.compute_pendency_days(updated_at)
            return pendency_days > max_days

        except Exception as e:
            logger.error(f"Error checking if overdue: {e}")
            return False

    @staticmethod
    def get_status_color(status: str) -> str:
        """
        Get color code for status (useful for UI).

        Args:
            status: Status string

        Returns:
            Color code string
        """
        color_map = {
            WorkflowStatus.DRAFT.value: "#6B7280",      # Gray
            WorkflowStatus.INPUT.value: "#F59E0B",      # Amber
            WorkflowStatus.STRATEGY.value: "#0EA5E9",   # Sky
            WorkflowStatus.REVIEW.value: "#3B82F6",     # Blue
            WorkflowStatus.APPROVAL.value: "#8B5CF6",   # Purple
            WorkflowStatus.COMPLETED.value: "#10B981",  # Green
            WorkflowStatus.REJECTED.value: "#EF4444",   # Red
        }

        return color_map.get(status, "#6B7280")  # Default to gray

# Global workflow engine instance
workflow_engine = WorkflowEngine()

# Convenience functions for backward compatibility
def can_transition(current: str, new: str) -> bool:
    """Convenience function to check if transition is allowed"""
    return workflow_engine.can_transition(current, new)

def compute_pendency_days(updated_at: datetime) -> int:
    """Convenience function to compute pendency days"""
    return WorkflowUtils.compute_pendency_days(updated_at)

# Constants for backward compatibility
VALID_STATUSES = [status.value for status in WorkflowStatus]
TRANSITIONS = {
    status.value: [t.value for t in transitions]
    for status, transitions in WorkflowEngine.VALID_TRANSITIONS.items()
}

# Factory function
def create_workflow_engine() -> WorkflowEngine:
    """Create workflow engine instance"""
    return WorkflowEngine()
