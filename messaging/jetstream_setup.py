"""
JetStream Setup with DLQ (Dead Letter Queue)
Handles message failures and retry logic with proper isolation
"""

import nats
from nats.js import JetStreamContext
from nats.js.api import StreamConfig, ConsumerConfig, AckPolicy, DeliverPolicy
import logging
import json
import asyncio
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# =============================================================================
# JETSTREAM STREAM CONFIGURATIONS
# =============================================================================

async def setup_jetstream(nc: nats.NATS) -> JetStreamContext:
    """
    Setup JetStream with DLQ for all critical streams

    Streams:
    - PRC: Peer Review Consensus messages
    - ESCALATIONS: Supervisor escalations
    - DLQ: Dead Letter Queue for failed messages
    """

    js = nc.jetstream()

    # -------------------------------------------------------------------------
    # PRC Stream - Peer Review Consensus
    # -------------------------------------------------------------------------
    try:
        await js.add_stream(
            name="PRC",
            subjects=["prc.*.*"],  # prc.{session_id}.{event}
            retention="limits",
            max_msgs=100000,
            max_age=86400_000_000_000,  # 24h in nanoseconds
            storage="file",
            replicas=1,
            duplicate_window=120_000_000_000,  # 2 min deduplication
        )
        logger.info("PRC stream configured")
    except Exception as e:
        logger.warning(f"PRC stream already exists or error: {e}")

    # PRC Consumer with retry → DLQ
    try:
        await js.add_consumer(
            "PRC",
            durable_name="peer_responses",
            ack_policy="explicit",
            ack_wait=30_000_000_000,  # 30s timeout
            max_deliver=5,  # 5 attempts before DLQ
            deliver_policy="all",
            filter_subject="prc.*.response",
        )
        logger.info("PRC consumer configured")
    except Exception as e:
        logger.warning(f"PRC consumer already exists: {e}")

    # -------------------------------------------------------------------------
    # Escalation Stream
    # -------------------------------------------------------------------------
    try:
        await js.add_stream(
            name="ESCALATIONS",
            subjects=["supervisor.escalation"],
            retention="limits",
            max_msgs=10000,
            max_age=604800_000_000_000,  # 7 days
            storage="file",
        )
        logger.info("ESCALATIONS stream configured")
    except Exception as e:
        logger.warning(f"ESCALATIONS stream exists: {e}")

    # -------------------------------------------------------------------------
    # DLQ Stream - Dead Letter Queue
    # -------------------------------------------------------------------------
    try:
        await js.add_stream(
            name="DLQ",
            subjects=["dlq.>"],  # dlq.{original_subject}
            retention="workqueue",
            max_msgs=50000,
            max_age=604800_000_000_000,  # 7 days retention
            storage="file",
        )
        logger.info("DLQ stream configured")
    except Exception as e:
        logger.warning(f"DLQ stream exists: {e}")

    logger.info("JetStream fully configured with DLQ support")
    return js

# =============================================================================
# DLQ WORKER - Process Failed Messages
# =============================================================================

class DLQWorker:
    """
    Process messages in Dead Letter Queue

    Responsibilities:
    - Log all failed messages to database
    - Alert on critical failures
    - Provide retry mechanism with manual intervention
    """

    def __init__(self, nc: nats.NATS, db_pool):
        self.nc = nc
        self.db = db_pool
        self.running = False

    async def start(self):
        """Start processing DLQ messages"""

        self.running = True
        js = self.nc.jetstream()

        # Subscribe to all DLQ messages
        try:
            sub = await js.pull_subscribe("dlq.>", "dlq_processor")
        except Exception as e:
            logger.error(f"Failed to subscribe to DLQ: {e}")
            return

        logger.info("DLQ worker started")

        while self.running:
            try:
                # Fetch messages in batches
                msgs = await sub.fetch(batch=10, timeout=5)

                for msg in msgs:
                    await self.process_dlq_message(msg)
                    await msg.ack()

            except nats.errors.TimeoutError:
                # No messages, continue
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"DLQ processing error: {e}")
                await asyncio.sleep(5)

    async def stop(self):
        """Stop DLQ worker"""
        self.running = False
        logger.info("DLQ worker stopped")

    async def process_dlq_message(self, msg):
        """
        Process single DLQ message

        Actions:
        1. Log to database
        2. Check criticality
        3. Send alerts if needed
        """

        subject = msg.subject
        data = msg.data.decode('utf-8')
        headers = dict(msg.headers) if msg.headers else {}

        # Extract original subject
        original_subject = subject.replace("dlq.", "", 1)

        try:
            # Log to database
            async with self.db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO dlq_messages
                    (original_subject, data, headers, error_count, created_at)
                    VALUES ($1, $2, $3, $4, NOW())
                """,
                    original_subject,
                    data,
                    json.dumps(headers),
                    msg.metadata.num_delivered if msg.metadata else 0
                )

            logger.warning(
                f"DLQ message logged: {original_subject} "
                f"(attempts: {msg.metadata.num_delivered if msg.metadata else 0})"
            )

            # Alert on critical failures
            if "escalation" in original_subject:
                logger.critical(
                    f"CRITICAL: Escalation failed processing - {original_subject}"
                )
                await self._send_critical_alert(original_subject, data)

            elif msg.metadata and msg.metadata.num_delivered >= 5:
                logger.error(
                    f"Message failed 5+ times: {original_subject}"
                )

        except Exception as e:
            logger.error(f"Failed to log DLQ message: {e}")

    async def _send_critical_alert(self, subject: str, data: str):
        """Send alert for critical DLQ messages"""

        # In production, integrate with PagerDuty, Slack, etc.
        alert_msg = {
            "severity": "critical",
            "subject": subject,
            "timestamp": datetime.utcnow().isoformat(),
            "message": f"Critical message failed: {subject}",
            "data_preview": data[:200]
        }

        # Publish alert
        try:
            await self.nc.publish(
                "alerts.critical",
                json.dumps(alert_msg).encode()
            )
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

# =============================================================================
# MESSAGE PUBLISHER WITH DLQ SUPPORT
# =============================================================================

class SafePublisher:
    """
    Publishes messages with automatic DLQ routing on failure

    Usage:
        publisher = SafePublisher(nc)
        await publisher.publish("prc.session_123.response", data)
    """

    def __init__(self, nc: nats.NATS):
        self.nc = nc
        self.js = nc.jetstream()

    async def publish(
        self,
        subject: str,
        data: bytes,
        headers: Optional[dict] = None,
        timeout: float = 5.0
    ):
        """
        Publish message with automatic DLQ routing

        If publish fails after retries, routes to DLQ
        """

        try:
            # Publish with acknowledgement
            ack = await self.js.publish(
                subject,
                data,
                headers=headers,
                timeout=timeout
            )

            logger.debug(f"Published to {subject}: {ack.seq}")
            return ack

        except asyncio.TimeoutError:
            logger.error(f"Publish timeout for {subject}, routing to DLQ")
            await self._route_to_dlq(subject, data, headers, "timeout")
            raise

        except Exception as e:
            logger.error(f"Publish failed for {subject}: {e}, routing to DLQ")
            await self._route_to_dlq(subject, data, headers, str(e))
            raise

    async def _route_to_dlq(
        self,
        original_subject: str,
        data: bytes,
        headers: Optional[dict],
        error: str
    ):
        """Route failed message to DLQ"""

        dlq_subject = f"dlq.{original_subject}"

        # Add error info to headers
        dlq_headers = headers.copy() if headers else {}
        dlq_headers["original_subject"] = original_subject
        dlq_headers["error"] = error
        dlq_headers["dlq_timestamp"] = datetime.utcnow().isoformat()

        try:
            # Use basic NATS (not JetStream) for DLQ to avoid recursion
            await self.nc.publish(dlq_subject, data, headers=dlq_headers)
            logger.info(f"Routed to DLQ: {dlq_subject}")

        except Exception as dlq_error:
            logger.critical(
                f"FAILED TO ROUTE TO DLQ: {dlq_subject} - {dlq_error}"
            )

# =============================================================================
# DATABASE SCHEMA FOR DLQ
# =============================================================================

DLQ_SCHEMA = """
CREATE TABLE IF NOT EXISTS dlq_messages (
    id SERIAL PRIMARY KEY,
    original_subject TEXT NOT NULL,
    data TEXT NOT NULL,
    headers JSONB,
    error_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    resolution_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_dlq_created ON dlq_messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dlq_unresolved ON dlq_messages(resolved) WHERE resolved = FALSE;
"""

async def init_dlq_schema(db_pool):
    """Initialize DLQ database schema"""
    async with db_pool.acquire() as conn:
        await conn.execute(DLQ_SCHEMA)
    logger.info("DLQ schema initialized")
