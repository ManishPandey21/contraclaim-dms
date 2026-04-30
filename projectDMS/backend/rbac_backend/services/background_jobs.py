# services/background_jobs.py - UPDATED WITH MISSING FUNCTIONS

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
from uuid import uuid4
import traceback

logger = logging.getLogger(__name__)

class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class Job:
    id: str
    name: str
    func: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Any = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    priority: int = 0  # Higher number = higher priority, note: priority not enforced in queue

class BackgroundJobProcessor:
    """
    Background job processor using asyncio.
    Note: Job priority is not enforced due to use of simple asyncio.Queue.
    Consider a PriorityQueue or external queue like Celery for real priority support.
    """
    
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.jobs: Dict[str, Job] = {}
        self.job_queue: asyncio.Queue = asyncio.Queue()
        self.workers: List[asyncio.Task] = []
        self.running = False
        self._stats = {
            'total_jobs': 0,
            'completed_jobs': 0,
            'failed_jobs': 0,
            'cancelled_jobs': 0,
        }

    async def start(self):
        if self.running:
            return
        
        self.running = True
        logger.info(f"Starting background job processor with {self.max_workers} workers")
        
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self.workers.append(worker)

    async def stop(self):
        if not self.running:
            return
        
        logger.info("Stopping background job processor")
        self.running = False
        
        for worker in self.workers:
            worker.cancel()
        
        # Await worker shutdown with timeout to avoid indefinite waits
        try:
            await asyncio.wait_for(asyncio.gather(*self.workers, return_exceptions=True), timeout=10)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for workers to stop")
        
        self.workers.clear()

    async def _worker(self, worker_name: str):
        logger.info(f"Worker {worker_name} started")
        
        while self.running:
            try:
                job: Job = await asyncio.wait_for(self.job_queue.get(), timeout=1.0)
                
                if job.status == JobStatus.CANCELLED:
                    continue
                
                logger.info(f"Worker {worker_name} processing job {job.id}: {job.name}")
                await self._execute_job(job)
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Worker {worker_name} error: {e}")
                logger.debug(traceback.format_exc())
                continue
        
        logger.info(f"Worker {worker_name} stopped")

    async def _execute_job(self, job: Job):
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        
        try:
            if asyncio.iscoroutinefunction(job.func):
                result = await job.func(*job.args, **job.kwargs)
            else:
                result = job.func(*job.args, **job.kwargs)
            
            job.result = result
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.utcnow()
            self._stats['completed_jobs'] += 1
            logger.info(f"Job {job.id} completed successfully")
            
        except Exception as e:
            job.error = str(e)
            job.retry_count += 1
            
            if job.retry_count <= job.max_retries:
                job.status = JobStatus.PENDING
                await self.job_queue.put(job)
                logger.warning(f"Job {job.id} failed, retrying ({job.retry_count}/{job.max_retries}): {e}")
            else:
                job.status = JobStatus.FAILED
                job.completed_at = datetime.utcnow()
                self._stats['failed_jobs'] += 1
                logger.error(f"Job {job.id} failed permanently: {e}")
                logger.error(traceback.format_exc())

    async def submit_job(self, name: str, func: Callable, *args, priority: int = 0, max_retries: int = 3, **kwargs) -> str:
        """
        Submit a job for processing.
        Note: priority parameter is currently informational only.
        """
        job_id = str(uuid4())
        job = Job(
            id=job_id,
            name=name,
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority,
            max_retries=max_retries,
        )
        
        self.jobs[job_id] = job
        await self.job_queue.put(job)
        self._stats['total_jobs'] += 1
        logger.info(f"Submitted job {job_id}: {name}")
        return job_id

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        job = self.jobs.get(job_id)
        return job.status if job else None

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job and job.status == JobStatus.PENDING:
            job.status = JobStatus.CANCELLED
            self._stats['cancelled_jobs'] += 1
            logger.info(f"Cancelled job {job_id}")
            return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        active_jobs = sum(1 for job in self.jobs.values() if job.status in [JobStatus.PENDING, JobStatus.RUNNING])
        return {
            **self._stats,
            'active_jobs': active_jobs,
            'queue_size': self.job_queue.qsize(),
            'workers': len(self.workers),
            'running': self.running,
        }

    def cleanup_old_jobs(self, max_age_hours: int = 24):
        cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
        old_job_ids = [
            job_id for job_id, job in self.jobs.items()
            if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
            and job.completed_at and job.completed_at < cutoff_time
        ]
        
        for job_id in old_job_ids:
            del self.jobs[job_id]
        
        if old_job_ids:
            logger.info(f"Cleaned up {len(old_job_ids)} old jobs")

# Global background job processor instance
_background_processor: Optional[BackgroundJobProcessor] = None

def get_background_processor() -> BackgroundJobProcessor:
    """Get the global background processor instance."""
    global _background_processor
    if _background_processor is None:
        _background_processor = BackgroundJobProcessor()
    return _background_processor

async def start_background_services():
    """Start background services."""
    try:
        logger.info("Starting background services...")
        
        # Start the background job processor
        processor = get_background_processor()
        await processor.start()
        
        # Schedule periodic cleanup
        async def periodic_cleanup():
            while processor.running:
                try:
                    await asyncio.sleep(3600)  # Run every hour
                    processor.cleanup_old_jobs()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in periodic cleanup: {e}")
        
        # Start cleanup task
        asyncio.create_task(periodic_cleanup())
        
        logger.info("Background services started successfully")
        
    except Exception as e:
        logger.error(f"Failed to start background services: {e}")
        raise

async def stop_background_services():
    """Stop background services."""
    try:
        logger.info("Stopping background services...")
        
        # Stop the background job processor
        processor = get_background_processor()
        await processor.stop()
        
        logger.info("Background services stopped successfully")
        
    except Exception as e:
        logger.error(f"Failed to stop background services: {e}")
        raise

# Additional utility functions for job management

async def submit_background_job(
    name: str, 
    func: Callable, 
    *args, 
    priority: int = 0, 
    max_retries: int = 3, 
    **kwargs
) -> str:
    """Submit a job to the background processor."""
    processor = get_background_processor()
    return await processor.submit_job(name, func, *args, priority=priority, max_retries=max_retries, **kwargs)

def get_job_status(job_id: str) -> Optional[JobStatus]:
    """Get the status of a job."""
    processor = get_background_processor()
    return processor.get_job_status(job_id)

def get_job_details(job_id: str) -> Optional[Job]:
    """Get detailed information about a job."""
    processor = get_background_processor()
    return processor.get_job(job_id)

def cancel_background_job(job_id: str) -> bool:
    """Cancel a pending job."""
    processor = get_background_processor()
    return processor.cancel_job(job_id)

def get_background_stats() -> Dict[str, Any]:
    """Get statistics about the background processor."""
    processor = get_background_processor()
    return processor.get_stats()

# Example background job functions

async def example_async_job(message: str, delay: int = 1):
    """Example asynchronous background job."""
    logger.info(f"Starting async job with message: {message}")
    await asyncio.sleep(delay)
    logger.info(f"Completed async job with message: {message}")
    return f"Processed: {message}"

def example_sync_job(message: str, multiplier: int = 2):
    """Example synchronous background job."""
    logger.info(f"Starting sync job with message: {message}")
    result = message * multiplier
    logger.info(f"Completed sync job with result: {result}")
    return result

# Health check functions

def is_background_services_healthy() -> bool:
    """Check if background services are running properly."""
    try:
        processor = get_background_processor()
        return processor.running and len(processor.workers) > 0
    except Exception:
        return False

def get_background_services_info() -> Dict[str, Any]:
    """Get information about background services."""
    try:
        processor = get_background_processor()
        stats = processor.get_stats()
        
        return {
            "status": "running" if processor.running else "stopped",
            "workers_count": len(processor.workers),
            "max_workers": processor.max_workers,
            "stats": stats,
            "healthy": is_background_services_healthy()
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "healthy": False
        }