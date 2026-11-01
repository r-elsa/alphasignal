"""Send work to the agent workers over the queue and wait for the result."""
import time
from typing import Iterable, Tuple

from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from celery_app import celery_app

ALL_AGENTS = ("market", "news", "reader", "peers", "report", "verifier")
# How long to wait for each agent. A job on a dead worker times out, and the
# LangGraph retry sends a new one, which another worker picks up.
TIMEOUTS = {"market": 40, "news": 40, "reader": 90, "peers": 60, "report": 120, "verifier": 20}
START_COMMAND = "docker compose up -d"


class AgentsUnavailable(RuntimeError):
    """Redis or an agent's workers aren't running. A RuntimeError, so LangGraph doesn't retry it."""


class AgentTimeout(Exception):
    """No result in time. LangGraph retries this."""


def ensure_agents_running(agents: Iterable[str] = ALL_AGENTS) -> None:
    try:
        workers = celery_app.control.inspect(timeout=1).active_queues() or {}
    except (OperationalError, RedisError, OSError):
        raise AgentsUnavailable(f"The agents can't be reached because Redis isn't running. Start them with: {START_COMMAND}")
    running = {queue["name"] for queues in workers.values() for queue in queues}
    missing = [a for a in agents if a not in running]
    if missing:
        raise AgentsUnavailable(f"These agents aren't running: {', '.join(missing)}. Start them with: {START_COMMAND}")


def run_agent(name: str, state: dict) -> Tuple[dict, str]:
    """Run one agent on a worker. Returns its output and the id of the worker that ran it."""
    timeout = TIMEOUTS[name]
    try:
        # expires: if this attempt times out and is retried, a late worker skips the old job
        job = celery_app.send_task(f"agents.{name}", args=[state], queue=name, expires=timeout)
    except (OperationalError, RedisError, OSError):
        raise AgentsUnavailable(f"The agents can't be reached because Redis isn't running. Start them with: {START_COMMAND}")

    deadline = time.monotonic() + timeout
    while not job.ready():
        if time.monotonic() > deadline:
            raise AgentTimeout(f"The {name} agent didn't answer within {timeout} seconds")
        time.sleep(0.2)
    if job.failed():
        raise job.result
    return job.result["output"], job.result["worker"]
