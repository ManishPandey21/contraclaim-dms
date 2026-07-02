import asyncio
import logging
import time
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Deque
from enum import Enum

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logging.warning("psutil not available - system metrics will be limited")

logger = logging.getLogger(__name__)

class AlertSeverity(str, Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class PerformanceConfig:
    """Configuration for performance monitoring"""
    max_metrics: int = 10000
    max_slow_queries: int = 1000
    slow_query_threshold: float = 7.0
    cleanup_interval: int = 300  # 5 minutes
    monitoring_interval: int = 30  # 30 seconds
    memory_warning_threshold: float = 80.0
    memory_critical_threshold: float = 90.0
    cpu_warning_threshold: float = 80.0
    cpu_critical_threshold: float = 90.0

@dataclass
class PerformanceMetric:
    """Individual performance metric"""
    timestamp: datetime
    endpoint: str
    method: str
    response_time: float
    status_code: int
    memory_usage: float
    cpu_usage: float
    user_id: Optional[str] = None

@dataclass
class SystemHealth:
    """System health information"""
    status: str
    memory: Optional[Dict[str, Any]] = None
    cpu: Optional[Dict[str, Any]] = None
    disk: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass
class PerformanceAlert:
    """Performance alert"""
    type: str
    severity: AlertSeverity
    message: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    value: Optional[float] = None
    details: Optional[Dict[str, Any]] = None

class PerformanceMonitorError(Exception):
    """Custom exception for performance monitor errors"""
    pass

class PerformanceMonitor:
    """
    Comprehensive performance monitoring service for tracking API response times,
    resource usage, and system metrics with proper error handling
    """

    def __init__(self, config: Optional[PerformanceConfig] = None):
        self.config = config or PerformanceConfig()
        
        # Thread-safe collections with size limits
        self.metrics: Deque[PerformanceMetric] = deque(maxlen=self.config.max_metrics)
        self.slow_queries: Deque[PerformanceMetric] = deque(maxlen=self.config.max_slow_queries)
        
        # Statistics tracking
        self.error_count: Dict[str, int] = {}
        self.endpoint_stats: Dict[str, Dict[str, Any]] = {}
        
        # Background task management
        self._monitoring_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Lock for thread safety
        self._lock = asyncio.Lock()

    async def start_monitoring(self) -> None:
        """Start background monitoring tasks"""
        if self._running:
            logger.warning("Performance monitoring is already running")
            return

        try:
            self._running = True
            
            # Start monitoring tasks
            self._monitoring_task = asyncio.create_task(self._monitor_system())
            self._cleanup_task = asyncio.create_task(self._cleanup_old_metrics())
            
            logger.info("Performance monitoring started successfully")
            
        except Exception as e:
            self._running = False
            logger.error(f"Failed to start performance monitoring: {e}")
            raise PerformanceMonitorError(f"Failed to start monitoring: {str(e)}")

    async def stop_monitoring(self) -> None:
        """Stop background monitoring tasks"""
        if not self._running:
            return

        try:
            logger.info("Stopping performance monitoring")
            self._running = False

            # Cancel tasks
            tasks = [self._monitoring_task, self._cleanup_task]
            for task in tasks:
                if task and not task.done():
                    task.cancel()

            # Wait for tasks to complete with timeout
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*[t for t in tasks if t], return_exceptions=True),
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Monitoring tasks did not stop within timeout")

            self._monitoring_task = None
            self._cleanup_task = None
            
            logger.info("Performance monitoring stopped")
            
        except Exception as e:
            logger.error(f"Error stopping performance monitoring: {e}")

    async def _monitor_system(self) -> None:
        """Background task to monitor system metrics"""
        logger.info("System monitoring task started")
        
        while self._running:
            try:
                await asyncio.sleep(self.config.monitoring_interval)
                
                if not PSUTIL_AVAILABLE:
                    continue
                
                # Get system metrics
                try:
                    memory = psutil.virtual_memory()
                    cpu = psutil.cpu_percent(interval=1)
                    
                    # Check for high resource usage
                    if (memory.percent > self.config.memory_critical_threshold or 
                        cpu > self.config.cpu_critical_threshold):
                        logger.critical(
                            f"CRITICAL resource usage - Memory: {memory.percent:.1f}%, "
                            f"CPU: {cpu:.1f}%"
                        )
                    elif (memory.percent > self.config.memory_warning_threshold or 
                          cpu > self.config.cpu_warning_threshold):
                        logger.warning(
                            f"High resource usage - Memory: {memory.percent:.1f}%, "
                            f"CPU: {cpu:.1f}%"
                        )
                
                except Exception as e:
                    logger.error(f"Failed to collect system metrics: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"System monitoring error: {e}")
                await asyncio.sleep(60)  # Back off on error

        logger.info("System monitoring task stopped")

    async def _cleanup_old_metrics(self) -> None:
        """Background task to clean up old metrics"""
        logger.info("Cleanup task started")
        
        while self._running:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                await self._perform_cleanup()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup task error: {e}")
                await asyncio.sleep(60)  # Back off on error

        logger.info("Cleanup task stopped")

    async def _perform_cleanup(self) -> None:
        """Perform cleanup of old metrics"""
        try:
            async with self._lock:
                cutoff_time = datetime.utcnow() - timedelta(hours=24)
                
                # Clean metrics older than 24 hours
                original_metrics_count = len(self.metrics)
                # Since we're using deque with maxlen, just remove from left
                while self.metrics and self.metrics[0].timestamp < cutoff_time:
                    self.metrics.popleft()
                
                # Clean slow queries
                original_slow_count = len(self.slow_queries)
                while self.slow_queries and self.slow_queries[0].timestamp < cutoff_time:
                    self.slow_queries.popleft()
                
                metrics_cleaned = original_metrics_count - len(self.metrics)
                slow_cleaned = original_slow_count - len(self.slow_queries)
                
                if metrics_cleaned > 0 or slow_cleaned > 0:
                    logger.info(
                        f"Cleaned up {metrics_cleaned} old metrics and "
                        f"{slow_cleaned} slow queries"
                    )

        except Exception as e:
            logger.error(f"Cleanup operation failed: {e}")

    async def record_request(
        self,
        endpoint: str,
        method: str,
        response_time: float,
        status_code: int,
        user_id: Optional[str] = None
    ) -> None:
        """
        Record a request metric with proper validation and error handling.
        
        Args:
            endpoint: API endpoint path
            method: HTTP method
            response_time: Response time in seconds
            status_code: HTTP status code
            user_id: Optional user ID
        """
        try:
            # Validate inputs
            if not endpoint or not isinstance(endpoint, str):
                raise ValueError("endpoint must be a non-empty string")
            
            if not method or not isinstance(method, str):
                raise ValueError("method must be a non-empty string")
            
            if not isinstance(response_time, (int, float)) or response_time < 0:
                raise ValueError("response_time must be a non-negative number")
            
            if not isinstance(status_code, int) or status_code < 100 or status_code > 599:
                raise ValueError("status_code must be a valid HTTP status code")

            # Get system metrics safely
            memory_usage = 0.0
            cpu_usage = 0.0
            
            if PSUTIL_AVAILABLE:
                try:
                    memory = psutil.virtual_memory()
                    memory_usage = memory.percent
                    cpu_usage = psutil.cpu_percent()
                except Exception as e:
                    logger.debug(f"Failed to get system metrics: {e}")

            # Create metric
            metric = PerformanceMetric(
                timestamp=datetime.utcnow(),
                endpoint=endpoint,
                method=method,
                response_time=response_time,
                status_code=status_code,
                memory_usage=memory_usage,
                cpu_usage=cpu_usage,
                user_id=user_id
            )

            async with self._lock:
                # Add to metrics
                self.metrics.append(metric)

                # Track slow queries
                if response_time > self.config.slow_query_threshold:
                    self.slow_queries.append(metric)
                    logger.warning(
                        f"Slow query detected: {method} {endpoint} - "
                        f"{response_time:.3f}s (user: {user_id or 'anonymous'})"
                    )

                # Track errors
                if status_code >= 400:
                    error_key = f"{method}:{endpoint}:{status_code}"
                    self.error_count[error_key] = self.error_count.get(error_key, 0) + 1

                # Update endpoint statistics
                self._update_endpoint_stats(endpoint, method, response_time, status_code)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to record performance metric: {e}")
            # Don't raise here to avoid breaking the application

    def _update_endpoint_stats(
        self, 
        endpoint: str, 
        method: str, 
        response_time: float, 
        status_code: int
    ) -> None:
        """Update endpoint statistics (called within lock)"""
        try:
            key = f"{method}:{endpoint}"
            
            if key not in self.endpoint_stats:
                self.endpoint_stats[key] = {
                    'total_requests': 0,
                    'total_time': 0.0,
                    'min_time': float('inf'),
                    'max_time': 0.0,
                    'error_count': 0,
                    'response_times': deque(maxlen=100),  # Keep last 100 for percentiles
                }

            stats = self.endpoint_stats[key]
            stats['total_requests'] += 1
            stats['total_time'] += response_time
            stats['min_time'] = min(stats['min_time'], response_time)
            stats['max_time'] = max(stats['max_time'], response_time)
            stats['response_times'].append(response_time)

            if status_code >= 400:
                stats['error_count'] += 1

        except Exception as e:
            logger.error(f"Failed to update endpoint stats: {e}")

    async def get_performance_summary(self, hours: int = 1) -> Dict[str, Any]:
        """
        Get performance summary for the specified time period.
        
        Args:
            hours: Number of hours to look back
            
        Returns:
            Dictionary with performance summary
        """
        try:
            if hours <= 0:
                raise ValueError("hours must be positive")

            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            async with self._lock:
                recent_metrics = [m for m in self.metrics if m.timestamp >= cutoff_time]

            if not recent_metrics:
                return {
                    'total_requests': 0,
                    'avg_response_time': 0,
                    'p50_response_time': 0,
                    'p95_response_time': 0,
                    'p99_response_time': 0,
                    'error_rate': 0,
                    'slow_queries': 0,
                    'avg_memory_usage': 0,
                    'avg_cpu_usage': 0,
                }

            # Calculate statistics
            total_requests = len(recent_metrics)
            response_times = [m.response_time for m in recent_metrics]
            
            avg_response_time = statistics.mean(response_times)
            p50 = statistics.median(response_times)
            
            # Calculate percentiles safely
            sorted_times = sorted(response_times)
            p95_idx = max(0, int(0.95 * len(sorted_times)) - 1)
            p99_idx = max(0, int(0.99 * len(sorted_times)) - 1)
            p95 = sorted_times[p95_idx]
            p99 = sorted_times[p99_idx]

            error_count = sum(1 for m in recent_metrics if m.status_code >= 400)
            error_rate = (error_count / total_requests) * 100
            slow_queries = sum(1 for m in recent_metrics if m.response_time > self.config.slow_query_threshold)

            # System metrics
            memory_usages = [m.memory_usage for m in recent_metrics if m.memory_usage > 0]
            cpu_usages = [m.cpu_usage for m in recent_metrics if m.cpu_usage > 0]
            
            avg_memory = statistics.mean(memory_usages) if memory_usages else 0
            avg_cpu = statistics.mean(cpu_usages) if cpu_usages else 0

            return {
                'total_requests': total_requests,
                'avg_response_time': round(avg_response_time, 3),
                'p50_response_time': round(p50, 3),
                'p95_response_time': round(p95, 3),
                'p99_response_time': round(p99, 3),
                'error_rate': round(error_rate, 2),
                'slow_queries': slow_queries,
                'avg_memory_usage': round(avg_memory, 2),
                'avg_cpu_usage': round(avg_cpu, 2),
            }

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to get performance summary: {e}")
            raise PerformanceMonitorError(f"Performance summary failed: {str(e)}")

    async def get_endpoint_stats(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top endpoint statistics.
        
        Args:
            limit: Maximum number of endpoints to return
            
        Returns:
            List of endpoint statistics
        """
        try:
            if limit <= 0:
                raise ValueError("limit must be positive")

            async with self._lock:
                stats_list = []
                
                for endpoint, stats in self.endpoint_stats.items():
                    if stats['total_requests'] > 0:
                        avg_time = stats['total_time'] / stats['total_requests']
                        error_rate = (stats['error_count'] / stats['total_requests']) * 100

                        # Calculate p95 from recent response times
                        response_times = list(stats['response_times'])
                        p95 = 0
                        if response_times:
                            sorted_times = sorted(response_times)
                            p95_idx = max(0, int(0.95 * len(sorted_times)) - 1)
                            p95 = sorted_times[p95_idx]

                        stats_list.append({
                            'endpoint': endpoint,
                            'total_requests': stats['total_requests'],
                            'avg_response_time': round(avg_time, 3),
                            'min_response_time': round(stats['min_time'], 3) if stats['min_time'] != float('inf') else 0,
                            'max_response_time': round(stats['max_time'], 3),
                            'p95_response_time': round(p95, 3),
                            'error_rate': round(error_rate, 2),
                            'error_count': stats['error_count'],
                        })

                # Sort by total requests descending
                stats_list.sort(key=lambda x: x['total_requests'], reverse=True)
                return stats_list[:limit]

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to get endpoint stats: {e}")
            raise PerformanceMonitorError(f"Endpoint stats failed: {str(e)}")

    async def get_slow_queries(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get recent slow queries.
        
        Args:
            limit: Maximum number of slow queries to return
            
        Returns:
            List of slow query information
        """
        try:
            if limit <= 0:
                raise ValueError("limit must be positive")

            async with self._lock:
                recent_slow = list(self.slow_queries)[-limit:]

            return [
                {
                    'timestamp': metric.timestamp.isoformat(),
                    'endpoint': metric.endpoint,
                    'method': metric.method,
                    'response_time': round(metric.response_time, 3),
                    'status_code': metric.status_code,
                    'user_id': metric.user_id,
                }
                for metric in recent_slow
            ]

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to get slow queries: {e}")
            raise PerformanceMonitorError(f"Slow queries failed: {str(e)}")

    async def get_error_summary(self) -> Dict[str, int]:
        """Get error count summary"""
        try:
            async with self._lock:
                return dict(self.error_count)
        except Exception as e:
            logger.error(f"Failed to get error summary: {e}")
            return {}

    async def reset_stats(self) -> None:
        """Reset all statistics"""
        try:
            async with self._lock:
                self.metrics.clear()
                self.slow_queries.clear()
                self.error_count.clear()
                self.endpoint_stats.clear()
            
            logger.info("Performance statistics reset")
            
        except Exception as e:
            logger.error(f"Failed to reset stats: {e}")
            raise PerformanceMonitorError(f"Stats reset failed: {str(e)}")

    async def get_system_health(self) -> SystemHealth:
        """Get current system health status"""
        try:
            if not PSUTIL_AVAILABLE:
                return SystemHealth(
                    status="unknown",
                    timestamp=datetime.utcnow()
                )

            memory = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=1)
            disk = psutil.disk_usage('/')

            # Determine health status
            health_status = "healthy"
            if (memory.percent > self.config.memory_critical_threshold or 
                cpu > self.config.cpu_critical_threshold or 
                disk.percent > 90):
                health_status = "critical"
            elif (memory.percent > self.config.memory_warning_threshold or 
                  cpu > self.config.cpu_warning_threshold or 
                  disk.percent > 80):
                health_status = "warning"

            return SystemHealth(
                status=health_status,
                memory={
                    'total': memory.total,
                    'available': memory.available,
                    'percent': memory.percent,
                    'used': memory.used,
                },
                cpu={
                    'percent': cpu,
                    'count': psutil.cpu_count(),
                },
                disk={
                    'total': disk.total,
                    'used': disk.used,
                    'free': disk.free,
                    'percent': disk.percent,
                },
                timestamp=datetime.utcnow()
            )

        except Exception as e:
            logger.error(f"Error getting system health: {e}")
            return SystemHealth(
                status="error",
                timestamp=datetime.utcnow()
            )

    async def check_performance_alerts(self) -> List[PerformanceAlert]:
        """Check for performance alerts"""
        alerts = []

        try:
            # Get recent performance summary
            summary = await self.get_performance_summary(hours=1)

            # Check for high error rate
            if summary['error_rate'] > 5:
                alerts.append(PerformanceAlert(
                    type='high_error_rate',
                    severity=AlertSeverity.WARNING,
                    message=f"High error rate: {summary['error_rate']:.2f}%",
                    value=summary['error_rate']
                ))

            # Check for slow response times
            if summary['avg_response_time'] > 2:
                alerts.append(PerformanceAlert(
                    type='slow_response_time',
                    severity=AlertSeverity.WARNING,
                    message=f"Slow average response time: {summary['avg_response_time']:.3f}s",
                    value=summary['avg_response_time']
                ))

            # Check system resources
            health = await self.get_system_health()
            if health.status == 'critical':
                alerts.append(PerformanceAlert(
                    type='system_resources',
                    severity=AlertSeverity.CRITICAL,
                    message='Critical system resource usage',
                    details=health.__dict__
                ))
            elif health.status == 'warning':
                alerts.append(PerformanceAlert(
                    type='system_resources',
                    severity=AlertSeverity.WARNING,
                    message='High system resource usage',
                    details=health.__dict__
                ))

        except Exception as e:
            logger.error(f"Error checking performance alerts: {e}")
            alerts.append(PerformanceAlert(
                type='monitoring_error',
                severity=AlertSeverity.ERROR,
                message=f"Performance monitoring error: {str(e)}"
            ))

        return alerts

# Global performance monitor instance
performance_monitor = PerformanceMonitor()

# Middleware for FastAPI
class PerformanceMiddleware:
    """FastAPI middleware to automatically track request performance"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        method = scope.get("method", "")
        path = scope.get("path", "")

        # Get user ID from headers if available
        user_id = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"user-id":
                try:
                    user_id = header_value.decode('utf-8')
                except UnicodeDecodeError:
                    pass
                break

        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Record the request
            response_time = time.time() - start_time
            try:
                await performance_monitor.record_request(
                    endpoint=path,
                    method=method,
                    response_time=response_time,
                    status_code=status_code,
                    user_id=user_id
                )
            except Exception as e:
                # Don't let monitoring errors break the application
                logger.error(f"Failed to record performance metric: {e}")

# Factory function
def create_performance_monitor(config: Optional[PerformanceConfig] = None) -> PerformanceMonitor:
    """Create performance monitor instance"""
    return PerformanceMonitor(config)
