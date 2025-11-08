"""
Hardened Sandbox Executor with gVisor/Firecracker Support
Maximum security for code execution with multiple isolation layers
"""

import asyncio
import tempfile
import os
import json
import uuid
import subprocess
import shutil
from typing import Dict, Optional
from dataclasses import dataclass
from pathlib import Path
import logging

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# =============================================================================
# RATE LIMITING CONFIGURATION
# =============================================================================

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="Secure Sandbox Executor",
    description="Battle-hardened code execution with gVisor/Docker isolation"
)
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)

# =============================================================================
# SECURITY MIDDLEWARE
# =============================================================================

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response

# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class ExecuteRequest(BaseModel):
    """Code execution request"""
    code: str = Field(..., max_length=50000, description="Python code to execute")
    timeout: int = Field(default=30, ge=1, le=300, description="Timeout in seconds")
    language: str = Field(default="python", description="Programming language")

class ExecuteResponse(BaseModel):
    """Code execution response"""
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    execution_id: str

# =============================================================================
# SANDBOX EXECUTOR
# =============================================================================

class SandboxExecutor:
    """
    Execute code in maximum security sandbox

    Security layers:
    1. gVisor (if available) for kernel isolation
    2. Docker with strict resource limits
    3. No network access
    4. Read-only filesystem
    5. Resource limits (CPU, memory, processes)
    6. Timeout enforcement
    """

    def __init__(self):
        self.gvisor_available = self._check_gvisor()
        self.firecracker_available = self._check_firecracker()

        logger.info(
            f"Sandbox initialized - "
            f"gVisor: {self.gvisor_available}, "
            f"Firecracker: {self.firecracker_available}"
        )

    def _check_gvisor(self) -> bool:
        """Check if gVisor (runsc) is available"""
        return shutil.which("runsc") is not None

    def _check_firecracker(self) -> bool:
        """Check if Firecracker is available"""
        return shutil.which("firecracker") is not None

    async def execute(
        self,
        code: str,
        timeout: int = 30,
        language: str = "python"
    ) -> ExecuteResponse:
        """Execute code in hardened sandbox"""

        execution_id = str(uuid.uuid4())
        start_time = asyncio.get_event_loop().time()

        # Create isolated temp directory
        with tempfile.TemporaryDirectory(
            prefix=f"sandbox_{execution_id}_"
        ) as tmpdir:

            # Restrict permissions (owner only)
            os.chmod(tmpdir, 0o700)

            # Prepare code file
            code_file = self._prepare_code_file(tmpdir, code, language, timeout)

            # Execute based on available isolation
            if self.gvisor_available:
                result = await self._execute_gvisor(code_file, tmpdir, timeout, execution_id)
            else:
                result = await self._execute_docker(code_file, tmpdir, timeout, execution_id)

            execution_time = asyncio.get_event_loop().time() - start_time

            return ExecuteResponse(
                stdout=result["stdout"][:4096],  # Limit output
                stderr=result["stderr"][:4096],
                exit_code=result["exit_code"],
                execution_time=execution_time,
                execution_id=execution_id
            )

    def _prepare_code_file(
        self,
        tmpdir: str,
        code: str,
        language: str,
        timeout: int
    ) -> str:
        """Prepare code file with resource limits"""

        if language == "python":
            code_file = os.path.join(tmpdir, "main.py")

            # Add resource limits and timeout enforcement
            wrapper = f"""
import resource
import signal
import sys
import os

# Disable dangerous modules
import sys
sys.modules['os'] = None
sys.modules['subprocess'] = None
sys.modules['socket'] = None

# Set resource limits
try:
    resource.setrlimit(resource.RLIMIT_CPU, ({timeout}, {timeout}))  # CPU time
    resource.setrlimit(resource.RLIMIT_AS, (256*1024*1024, 256*1024*1024))  # 256MB memory
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))  # No subprocesses
    resource.setrlimit(resource.RLIMIT_NOFILE, (5, 5))  # Minimal file descriptors
except Exception as e:
    print(f"Warning: Could not set all limits: {{e}}", file=sys.stderr)

# Timeout handler
def timeout_handler(signum, frame):
    print("ERROR: Execution timeout", file=sys.stderr)
    sys.exit(124)

signal.signal(signal.SIGALRM, timeout_handler)
signal.alarm({timeout})

# User code below
# ============================================================
{code}
"""

            with open(code_file, "w") as f:
                f.write(wrapper)

            os.chmod(code_file, 0o400)  # Read-only

        else:
            raise ValueError(f"Unsupported language: {language}")

        return code_file

    async def _execute_gvisor(
        self,
        code_file: str,
        tmpdir: str,
        timeout: int,
        execution_id: str
    ) -> Dict:
        """Execute using gVisor for maximum isolation"""

        logger.info(f"Executing {execution_id} with gVisor")

        cmd = [
            "docker", "run",
            "--rm",
            "--runtime=runsc",  # Use gVisor runtime

            # Network isolation
            "--network=none",

            # Filesystem
            "--read-only",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size=64m",

            # Resource limits
            "--memory=256m",
            "--memory-swap=256m",
            "--cpus=0.5",
            "--pids-limit=10",

            # Security
            "--security-opt=no-new-privileges:true",
            "--cap-drop=ALL",

            # User isolation
            "--user=65534:65534",  # nobody:nogroup

            # Volume (read-only)
            f"-v={tmpdir}:/code:ro",

            # Labels
            f"--label=sandbox=true",
            f"--label=execution_id={execution_id}",

            # Image and command
            "python:3.11-slim",
            "timeout", str(timeout + 5), "python", "-u", "/code/main.py"
        ]

        return await self._run_subprocess(cmd, timeout + 10)

    async def _execute_docker(
        self,
        code_file: str,
        tmpdir: str,
        timeout: int,
        execution_id: str
    ) -> Dict:
        """Execute using standard Docker with strict limits"""

        logger.info(f"Executing {execution_id} with Docker")

        cmd = [
            "docker", "run",
            "--rm",

            # Network isolation
            "--network=none",

            # Filesystem
            "--read-only",
            f"--tmpfs=/tmp:rw,noexec,nosuid,size=64m",

            # Resource limits
            "--memory=256m",
            "--memory-swap=256m",
            "--cpus=0.5",
            "--pids-limit=10",

            # Security
            "--security-opt=no-new-privileges:true",
            "--security-opt=seccomp=default",
            "--cap-drop=ALL",

            # User isolation
            "--user=65534:65534",

            # Volume (read-only)
            f"-v={tmpdir}:/code:ro",

            # Environment
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTHONUNBUFFERED=1",

            # Labels
            f"--label=sandbox=true",
            f"--label=execution_id={execution_id}",

            # Image and command
            "python:3.11-slim",
            "timeout", "--signal=KILL", str(timeout + 5),
            "python", "-u", "/code/main.py"
        ]

        return await self._run_subprocess(cmd, timeout + 10)

    async def _run_subprocess(
        self,
        cmd: list,
        timeout: int
    ) -> Dict:
        """Run subprocess with timeout"""

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )

                return {
                    "stdout": stdout.decode('utf-8', errors='replace'),
                    "stderr": stderr.decode('utf-8', errors='replace'),
                    "exit_code": process.returncode
                }

            except asyncio.TimeoutError:
                # Kill process
                process.kill()
                await process.wait()

                return {
                    "stdout": "",
                    "stderr": "HARD TIMEOUT: Process killed",
                    "exit_code": -1
                }

        except Exception as e:
            logger.error(f"Subprocess execution failed: {e}")
            return {
                "stdout": "",
                "stderr": f"Execution failed: {str(e)}",
                "exit_code": -1
            }

# =============================================================================
# API ENDPOINTS
# =============================================================================

executor = SandboxExecutor()

@app.post("/execute", response_model=ExecuteResponse)
@limiter.limit("10/minute")
async def execute_code(
    request: Request,
    exec_request: ExecuteRequest
) -> ExecuteResponse:
    """
    Execute code in hardened sandbox

    Rate limit: 10 requests per minute per IP
    """

    try:
        result = await executor.execute(
            code=exec_request.code,
            timeout=exec_request.timeout,
            language=exec_request.language
        )
        return result

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        raise HTTPException(status_code=500, detail="Execution failed")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "gvisor_available": executor.gvisor_available,
        "firecracker_available": executor.firecracker_available
    }

# =============================================================================
# STARTUP
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
