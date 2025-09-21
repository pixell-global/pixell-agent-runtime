import asyncio
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import structlog
from collections import OrderedDict

logger = structlog.get_logger()


@dataclass
class Session:
    """Represents an execution session with state preservation."""
    session_id: str
    variables: Dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_accessed: int = field(default_factory=lambda: int(time.time()))
    execution_count: int = 0
    last_code: str = ""
    last_error: Optional[str] = None
    
    def update_accessed(self):
        """Update last accessed timestamp."""
        self.last_accessed = int(time.time())


class SessionManager:
    """In-memory session management with LRU eviction."""
    
    def __init__(self, max_sessions: int = 1000, ttl_seconds: int = 5400):  # 90 min TTL
        self.sessions: OrderedDict[str, Session] = OrderedDict()
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._cleanup_task = None
        
    async def start(self):
        """Start background cleanup task."""
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self):
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    async def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create new one."""
        async with self._lock:
            if session_id in self.sessions:
                # Move to end (most recently used)
                session = self.sessions.pop(session_id)
                session.update_accessed()
                self.sessions[session_id] = session
                return session
            
            # Create new session
            session = Session(session_id=session_id)
            
            # Evict oldest if at capacity
            if len(self.sessions) >= self.max_sessions:
                oldest_id = next(iter(self.sessions))
                del self.sessions[oldest_id]
                logger.info("Evicted oldest session", session_id=oldest_id)
            
            self.sessions[session_id] = session
            logger.info("Created new session", session_id=session_id)
            return session
    
    async def get(self, session_id: str) -> Optional[Session]:
        """Get existing session or None."""
        async with self._lock:
            if session_id in self.sessions:
                session = self.sessions.pop(session_id)
                session.update_accessed()
                self.sessions[session_id] = session
                return session
            return None
    
    async def update_variables(self, session_id: str, variables: Dict[str, Any]):
        """Update session variables."""
        session = await self.get(session_id)
        if session:
            async with self._lock:
                session.variables.update(variables)
                session.update_accessed()
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get session manager statistics."""
        async with self._lock:
            return {
                "active_sessions": len(self.sessions),
                "max_sessions": self.max_sessions,
                "ttl_seconds": self.ttl_seconds,
                "oldest_session_age": self._get_oldest_age()
            }
    
    def _get_oldest_age(self) -> Optional[int]:
        """Get age of oldest session in seconds."""
        if not self.sessions:
            return None
        oldest = next(iter(self.sessions.values()))
        return int(time.time()) - oldest.last_accessed
    
    async def _cleanup_loop(self):
        """Background task to clean up expired sessions."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in cleanup loop", error=str(e))
    
    async def _cleanup_expired(self):
        """Remove sessions that have exceeded TTL."""
        current_time = int(time.time())
        expired = []
        
        async with self._lock:
            for session_id, session in self.sessions.items():
                if current_time - session.last_accessed > self.ttl_seconds:
                    expired.append(session_id)
            
            for session_id in expired:
                del self.sessions[session_id]
                logger.info("Cleaned up expired session", session_id=session_id)
        
        if expired:
            logger.info("Cleaned up sessions", count=len(expired))