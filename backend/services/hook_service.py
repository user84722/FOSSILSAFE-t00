import os
import subprocess
import logging
import time
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class HookService:
    def __init__(self, hooks_dir: str = "/etc/fossilsafe/hooks.d"):
        self.hooks_dir = Path(hooks_dir)
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            if not self.hooks_dir.exists():
                self.hooks_dir.mkdir(parents=True, exist_ok=True)
                # Set permissions to root-only for safety if possible
                # os.chmod(self.hooks_dir, 0o700)
        except Exception as e:
            logger.error(f"Failed to create hooks directory {self.hooks_dir}: {e}")

    def list_hooks(self, stage: str = "pre") -> List[Dict]:
        """
        List discovered hooks for a given stage ('pre' or 'post').
        """
        hooks = []
        if not self.hooks_dir.exists():
            return []

        prefix = f"{stage}-"
        try:
            for item in self.hooks_dir.iterdir():
                if item.is_file() and item.name.startswith(prefix) and os.access(item, os.X_OK):
                    hooks.append({
                        "id": item.name,
                        "name": item.name[len(prefix):].replace("-", " ").title(),
                        "path": str(item),
                        "stage": stage
                    })
        except Exception as e:
            logger.error(f"Error listing hooks: {e}")
        
        return sorted(hooks, key=lambda x: x["id"])

    def execute_hook(self, hook_id: str, job_id: int, env: Optional[Dict] = None) -> Dict:
        """
        Execute a hook script securely.
        """
        hook_path = self.hooks_dir / hook_id
        if not hook_path.exists() or not os.access(hook_path, os.X_OK):
            return {"success": False, "error": f"Hook {hook_id} not found or not executable", "exit_code": -1}

        logger.info(f"Executing {hook_id} for Job {job_id}")
        
        # Prepare environment
        execution_env = os.environ.copy()
        execution_env["FOSSILSAFE_JOB_ID"] = str(job_id)
        if env:
            execution_env.update(env)

        start_time = time.time()
        try:
            result = subprocess.run(
                [str(hook_path)],
                capture_output=True,
                text=True,
                env=execution_env,
                timeout=300 # 5 minute timeout
            )
            
            duration = time.time() - start_time
            success = result.returncode == 0
            
            return {
                "success": success,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": duration
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Timeout expired",
                "duration": time.time() - start_time,
                "exit_code": 124
            }
        except Exception as e:
            logger.error(f"Hook execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "duration": time.time() - start_time,
                "exit_code": 1
            }

hook_service = HookService()
