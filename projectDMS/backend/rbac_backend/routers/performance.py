"""
Secure performance monitoring API with comprehensive authorization, rate limiting,
and proper error handling. Addresses admin-only operations and security concerns.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime, timedelta

from ..core.security import get_current_user, CurrentUser
from ..services.performance_service import PerformanceService
from ..services.authorization_service import AuthorizationService
from ..models.performance_models import (
    HealthStatus, PerformanceMetrics, EndpointStats, JobStats
)
from ..utils.error_handler import handle_exceptions, PerformanceError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger
from ..utils.cache_service import cache_with_ttl

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/performance", tags=["performance"])


class PerformanceController:
    """Secure performance controller with comprehensive monitoring and authorization."""
    
    def __init__(
        self,
        performance_service: PerformanceService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.performance_service = performance_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def get_health_status(self, current_user: Optional[CurrentUser] = None) -> HealthStatus:
        """Get system health status - publicly accessible with rate limiting."""
        try:
            # Rate limiting for health checks
            client_ip = "anonymous" if not current_user else current_user.id
            await self.rate_limiter.check_client_limit(
                client_ip, cost=1, window_seconds=60, max_requests=30
            )
            
            # Get cached health status (cache for 30 seconds)
            health_data = await self._get_cached_health_status()
            
            return HealthStatus(**health_data)
            
        except Exception as e:
            logger.error(f"Health check error: {str(e)}")
            # Return degraded health status instead of failing completely
            return HealthStatus(
                status="degraded",
                message="Health check service temporarily unavailable",
                services={},
                alerts=[],
                timestamp=datetime.utcnow()
            )

    async def get_performance_metrics(
        self, hours: int, current_user: CurrentUser
    ) -> PerformanceMetrics:
        """Get comprehensive performance metrics (admin only)."""
        try:
            # Rate limiting for expensive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=10)
            
            # Authorization check - admin only
            await self.auth_service.require_permission(current_user, "performance:admin")
            
            # Validate hours parameter
            if hours < 1 or hours > 168:  # Max 1 week
                raise PerformanceError(
                    "Hours parameter must be between 1 and 168",
                    status.HTTP_400_BAD_REQUEST
                )
            
            # Get cached metrics (cache for 5 minutes for expensive operations)
            metrics = await self._get_cached_performance_metrics(hours)
            
            # Audit log for admin access
            await self.audit_logger.log_performance_metrics_accessed(
                current_user.id, hours
            )
            
            return PerformanceMetrics(**metrics)
            
        except PerformanceError:
            raise
        except Exception as e:
            logger.error(f"Error getting performance metrics: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Performance metrics service temporarily unavailable"
            )

    async def get_endpoint_performance(
        self, limit: int, current_user: CurrentUser
    ) -> List[EndpointStats]:
        """Get endpoint performance statistics (admin only)."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "performance:admin")
            
            # Validate limit
            limit = min(max(1, limit), 100)  # Between 1 and 100
            
            # Get cached endpoint stats
            stats = await self._get_cached_endpoint_stats(limit)
            
            return [EndpointStats(**stat) for stat in stats]
            
        except Exception as e:
            logger.error(f"Error getting endpoint performance: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Endpoint performance service temporarily unavailable"
            )

    async def get_slow_queries(
        self, limit: int, current_user: CurrentUser
    ) -> List[Dict[str, Any]]:
        """Get slow query log (admin only)."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "performance:admin")
            
            # Validate limit
            limit = min(max(1, limit), 200)  # Between 1 and 200
            
            # Get slow queries (cached for 2 minutes)
            slow_queries = await self._get_cached_slow_queries(limit)
            
            return slow_queries
            
        except Exception as e:
            logger.error(f"Error getting slow queries: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Slow query service temporarily unavailable"
            )

    async def get_cache_stats(self, current_user: CurrentUser) -> Dict[str, Any]:
        """Get cache statistics (admin only)."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "performance:admin")
            
            # Get cache statistics
            cache_stats = await self.performance_service.get_cache_statistics()
            
            return cache_stats
            
        except Exception as e:
            logger.error(f"Error getting cache stats: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cache statistics service temporarily unavailable"
            )

    async def clear_cache(self, current_user: CurrentUser) -> Dict[str, str]:
        """Clear cache (admin only with audit logging)."""
        try:
            # Rate limiting for destructive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=20)
            
            # Authorization check - require superadmin for cache clearing
            await self.auth_service.require_permission(current_user, "performance:superadmin")
            
            # Clear cache
            cleared_entries = await self.performance_service.clear_all_caches()
            
            # Audit log for security
            await self.audit_logger.log_cache_cleared(current_user.id, cleared_entries)
            
            return {
                "message": f"Cache cleared successfully. {cleared_entries} entries removed."
            }
            
        except Exception as e:
            logger.error(f"Error clearing cache: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cache clearing service temporarily unavailable"
            )

    async def get_job_stats(self, current_user: CurrentUser) -> JobStats:
        """Get background job statistics (admin only)."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "performance:admin")
            
            # Get job statistics
            job_stats = await self.performance_service.get_background_job_stats()
            
            return JobStats(**job_stats)
            
        except Exception as e:
            logger.error(f"Error getting job stats: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Job statistics service temporarily unavailable"
            )

    async def cancel_job(
        self, job_id: str, current_user: CurrentUser
    ) -> Dict[str, str]:
        """Cancel background job (admin only)."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "performance:admin")
            
            # Validate job ID
            if not job_id or len(job_id) > 100:
                raise PerformanceError(
                    "Invalid job ID",
                    status.HTTP_400_BAD_REQUEST
                )
            
            # Cancel job
            success = await self.performance_service.cancel_background_job(job_id)
            
            if not success:
                raise PerformanceError(
                    "Job not found or cannot be cancelled",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Audit log
            await self.audit_logger.log_job_cancelled(current_user.id, job_id)
            
            return {"message": f"Job {job_id} cancelled successfully"}
            
        except PerformanceError:
            raise
        except Exception as e:
            logger.error(f"Error cancelling job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Job cancellation service temporarily unavailable"
            )

    @cache_with_ttl(ttl_seconds=30)
    async def _get_cached_health_status(self) -> Dict[str, Any]:
        """Get cached health status."""
        return await self.performance_service.get_system_health()

    @cache_with_ttl(ttl_seconds=300)  # 5 minutes
    async def _get_cached_performance_metrics(self, hours: int) -> Dict[str, Any]:
        """Get cached performance metrics."""
        return await self.performance_service.get_performance_metrics(hours)

    @cache_with_ttl(ttl_seconds=60)  # 1 minute
    async def _get_cached_endpoint_stats(self, limit: int) -> List[Dict[str, Any]]:
        """Get cached endpoint statistics."""
        return await self.performance_service.get_endpoint_statistics(limit)

    @cache_with_ttl(ttl_seconds=120)  # 2 minutes
    async def _get_cached_slow_queries(self, limit: int) -> List[Dict[str, Any]]:
        """Get cached slow queries."""
        return await self.performance_service.get_slow_queries(limit)


# Dependency injection
async def get_performance_controller() -> PerformanceController:
    """Factory function for performance controller."""
    performance_service = PerformanceService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(scope="performance")
    audit_logger = AuditLogger()
    
    return PerformanceController(
        performance_service, auth_service, rate_limiter, audit_logger
    )


# API Endpoints
@router.get("/health", response_model=HealthStatus)
@handle_exceptions
async def get_health_status(
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: Optional[CurrentUser] = Depends(get_current_user)
):
    """Get system health status (publicly accessible with rate limiting)."""
    return await controller.get_health_status(current_user)


@router.get("/metrics", response_model=PerformanceMetrics)
@handle_exceptions
async def get_performance_metrics(
    hours: int = Query(1, ge=1, le=168),
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get performance metrics (admin only)."""
    return await controller.get_performance_metrics(hours, current_user)


@router.get("/endpoints", response_model=List[EndpointStats])
@handle_exceptions
async def get_endpoint_performance(
    limit: int = Query(20, ge=1, le=100),
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get endpoint performance statistics (admin only)."""
    return await controller.get_endpoint_performance(limit, current_user)


@router.get("/slow-queries")
@handle_exceptions
async def get_slow_queries(
    limit: int = Query(50, ge=1, le=200),
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get slow query log (admin only)."""
    return await controller.get_slow_queries(limit, current_user)


@router.get("/cache")
@handle_exceptions
async def get_cache_stats(
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get cache statistics (admin only)."""
    return await controller.get_cache_stats(current_user)


@router.post("/cache/clear")
@handle_exceptions
async def clear_cache(
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Clear all cache entries (superadmin only)."""
    return await controller.clear_cache(current_user)


@router.get("/jobs", response_model=JobStats)
@handle_exceptions
async def get_job_stats(
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get background job statistics (admin only)."""
    return await controller.get_job_stats(current_user)


@router.post("/job/{job_id}/cancel")
@handle_exceptions
async def cancel_job(
    job_id: str,
    controller: PerformanceController = Depends(get_performance_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Cancel a background job (admin only)."""
    return await controller.cancel_job(job_id, current_user)
