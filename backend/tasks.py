"""The agents as queue jobs. Each runs in its own worker container (see docker-compose.yml)."""
import socket

from celery_app import celery_app
from pipeline import consensus_agent, peers_agent, report_agent, scraper_agent


def _run(agent, state: dict) -> dict:
    return {"output": agent(state), "worker": socket.gethostname()}


@celery_app.task(name="agents.market")
def market(state: dict) -> dict:
    return _run(consensus_agent, state)


@celery_app.task(name="agents.news")
def news(state: dict) -> dict:
    return _run(scraper_agent, state)


@celery_app.task(name="agents.peers")
def peers(state: dict) -> dict:
    return _run(peers_agent, state)


@celery_app.task(name="agents.report")
def report(state: dict) -> dict:
    return _run(report_agent, state)
