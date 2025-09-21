"""Log aggregation for multiple PAR processes."""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, AsyncIterator
from collections import deque
import json

logger = logging.getLogger(__name__)


class LogEntry:
    """Represents a log entry from a process."""
    
    def __init__(self, process_id: str, timestamp: datetime, level: str, message: str, extra: Optional[Dict] = None):
        self.process_id = process_id
        self.timestamp = timestamp
        self.level = level
        self.message = message
        self.extra = extra or {}
        
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "process_id": self.process_id,
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "message": self.message,
            "extra": self.extra
        }
        
    @classmethod
    def from_line(cls, process_id: str, line: str) -> Optional['LogEntry']:
        """Parse a log line into a LogEntry."""
        try:
            # Try to parse JSON logs first
            if line.strip().startswith('{'):
                data = json.loads(line)
                return cls(
                    process_id=process_id,
                    timestamp=datetime.fromisoformat(data.get('timestamp', datetime.utcnow().isoformat())),
                    level=data.get('level', 'INFO'),
                    message=data.get('message', ''),
                    extra=data.get('extra', {})
                )
            else:
                # Parse standard log format: [timestamp] [level] message
                parts = line.strip().split(' ', 3)
                if len(parts) >= 3 and parts[0].startswith('[') and parts[1].startswith('['):
                    timestamp_str = parts[0][1:-1]
                    level = parts[1][1:-1]
                    message = parts[2] if len(parts) > 2 else ''
                    
                    # Try to parse timestamp
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str)
                    except:
                        timestamp = datetime.utcnow()
                        
                    return cls(
                        process_id=process_id,
                        timestamp=timestamp,
                        level=level,
                        message=message
                    )
                else:
                    # Fallback: treat entire line as message
                    return cls(
                        process_id=process_id,
                        timestamp=datetime.utcnow(),
                        level='INFO',
                        message=line.strip()
                    )
        except Exception as e:
            logger.debug(f"Failed to parse log line: {e}")
            return cls(
                process_id=process_id,
                timestamp=datetime.utcnow(),
                level='INFO',
                message=line.strip()
            )


class LogAggregator:
    """Aggregates logs from multiple PAR processes."""
    
    def __init__(self, max_entries_per_process: int = 1000):
        self.max_entries_per_process = max_entries_per_process
        self.process_logs: Dict[str, deque] = {}
        self.log_readers: Dict[str, asyncio.Task] = {}
        self._running = False
        
    async def start(self):
        """Start the log aggregator."""
        self._running = True
        logger.info("Log aggregator started")
        
    async def stop(self):
        """Stop the log aggregator."""
        self._running = False
        
        # Cancel all log readers
        for task in self.log_readers.values():
            task.cancel()
            
        # Wait for all tasks to complete
        if self.log_readers:
            await asyncio.gather(*self.log_readers.values(), return_exceptions=True)
            
        self.log_readers.clear()
        logger.info("Log aggregator stopped")
        
    def add_process(self, process_id: str, stdout_stream: asyncio.StreamReader, 
                   stderr_stream: asyncio.StreamReader):
        """Add a process to monitor."""
        if process_id not in self.process_logs:
            self.process_logs[process_id] = deque(maxlen=self.max_entries_per_process)
            
        # Create tasks to read stdout and stderr
        stdout_task = asyncio.create_task(
            self._read_stream(process_id, stdout_stream, "STDOUT")
        )
        stderr_task = asyncio.create_task(
            self._read_stream(process_id, stderr_stream, "STDERR")
        )
        
        self.log_readers[f"{process_id}_stdout"] = stdout_task
        self.log_readers[f"{process_id}_stderr"] = stderr_task
        
    def remove_process(self, process_id: str):
        """Remove a process from monitoring."""
        # Cancel readers
        for stream_type in ["stdout", "stderr"]:
            task_key = f"{process_id}_{stream_type}"
            if task_key in self.log_readers:
                self.log_readers[task_key].cancel()
                del self.log_readers[task_key]
                
    async def _read_stream(self, process_id: str, stream: asyncio.StreamReader, 
                          stream_type: str):
        """Read from a stream and aggregate logs."""
        try:
            while self._running:
                line = await stream.readline()
                if not line:
                    break
                    
                # Decode and create log entry
                line_str = line.decode('utf-8', errors='replace')
                entry = LogEntry.from_line(process_id, line_str)
                
                if entry:
                    # Add stream type to extra data
                    entry.extra['stream'] = stream_type.lower()
                    
                    # Store in process logs
                    if process_id in self.process_logs:
                        self.process_logs[process_id].append(entry)
                        
                    # Also log to supervisor logger for debugging
                    logger.debug(f"[{process_id}] {entry.message}")
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error reading logs from {process_id}: {e}")
            
    def get_logs(self, process_id: Optional[str] = None, 
                 level: Optional[str] = None,
                 since: Optional[datetime] = None,
                 limit: int = 100) -> List[LogEntry]:
        """Get logs with optional filtering."""
        logs = []
        
        # Get logs from specified process or all processes
        if process_id:
            if process_id in self.process_logs:
                logs = list(self.process_logs[process_id])
        else:
            # Merge logs from all processes
            for proc_logs in self.process_logs.values():
                logs.extend(list(proc_logs))
                
        # Apply filters
        if level:
            logs = [log for log in logs if log.level == level]
            
        if since:
            logs = [log for log in logs if log.timestamp >= since]
            
        # Sort by timestamp
        logs.sort(key=lambda x: x.timestamp, reverse=True)
        
        # Apply limit
        return logs[:limit]
        
    def tail_logs(self, process_id: Optional[str] = None, 
                  follow: bool = True) -> AsyncIterator[LogEntry]:
        """Tail logs in real-time."""
        # This would be implemented as an async generator
        # For now, return a simple implementation
        async def _tail():
            last_index = {}
            
            while self._running and follow:
                current_logs = self.get_logs(process_id=process_id, limit=10)
                
                # Emit new logs
                for log in reversed(current_logs):
                    log_key = f"{log.process_id}_{log.timestamp.isoformat()}"
                    if log_key not in last_index:
                        last_index[log_key] = True
                        yield log
                        
                await asyncio.sleep(0.5)
                
        return _tail()
        
    def clear_logs(self, process_id: Optional[str] = None):
        """Clear logs for a process or all processes."""
        if process_id:
            if process_id in self.process_logs:
                self.process_logs[process_id].clear()
        else:
            for logs in self.process_logs.values():
                logs.clear()