"""Background worker for BF-CBOM tasks."""

import asyncio
import logging
import os
import redis
import json
from typing import Dict, Any
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CBOMWorker:
    """Background worker for CBOM analysis tasks."""
    
    def __init__(self):
        """Initialize the worker."""
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis_client = redis.from_url(redis_url)
        self.running = False
    
    async def process_analysis_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a CBOM analysis task."""
        logger.info(f"Processing analysis task: {task_data}")
        
        # Simulate analysis work
        await asyncio.sleep(5)  # Simulate processing time
        
        result = {
            "task_id": task_data.get("task_id"),
            "project_url": task_data.get("project_url"),
            "status": "completed",
            "result": {
                "crypto_libraries": [
                    {"name": "cryptography", "version": "41.0.0", "usage": "high"},
                    {"name": "pycryptodome", "version": "3.19.0", "usage": "medium"}
                ],
                "vulnerabilities": [],
                "benchmark_results": {
                    "encryption_speed": "1000 ops/sec",
                    "decryption_speed": "950 ops/sec"
                }
            },
            "completed_at": datetime.now().isoformat()
        }
        
        return result
    
    async def process_benchmark_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a benchmark task."""
        logger.info(f"Processing benchmark task: {task_data}")
        
        # Simulate benchmark work
        await asyncio.sleep(10)  # Simulate longer processing time
        
        result = {
            "task_id": task_data.get("task_id"),
            "benchmark_type": task_data.get("benchmark_type"),
            "status": "completed",
            "result": {
                "aes_encryption": {"throughput": 1500, "latency": 0.67},
                "rsa_keygen": {"throughput": 50, "latency": 20.0},
                "hash_performance": {"throughput": 5000, "latency": 0.2}
            },
            "completed_at": datetime.now().isoformat()
        }
        
        return result
    
    async def listen_for_tasks(self):
        """Listen for tasks from Redis queue."""
        logger.info("Worker started, listening for tasks...")
        self.running = True
        
        while self.running:
            try:
                # Check for analysis tasks
                task_data = self.redis_client.lpop("analysis_queue")
                if task_data:
                    task_data = json.loads(task_data)
                    result = await self.process_analysis_task(task_data)
                    
                    # Store result
                    result_key = f"result:{task_data['task_id']}"
                    self.redis_client.setex(result_key, 3600, json.dumps(result))
                    logger.info(f"Analysis task completed: {task_data['task_id']}")
                
                # Check for benchmark tasks
                task_data = self.redis_client.lpop("benchmark_queue")
                if task_data:
                    task_data = json.loads(task_data)
                    result = await self.process_benchmark_task(task_data)
                    
                    # Store result
                    result_key = f"result:{task_data['task_id']}"
                    self.redis_client.setex(result_key, 3600, json.dumps(result))
                    logger.info(f"Benchmark task completed: {task_data['task_id']}")
                
                # Sleep briefly if no tasks
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing task: {e}")
                await asyncio.sleep(5)
    
    def stop(self):
        """Stop the worker."""
        logger.info("Stopping worker...")
        self.running = False


async def main():
    """Main worker function."""
    worker = CBOMWorker()
    
    try:
        await worker.listen_for_tasks()
    except KeyboardInterrupt:
        worker.stop()
        logger.info("Worker stopped by user")


if __name__ == "__main__":
    asyncio.run(main())