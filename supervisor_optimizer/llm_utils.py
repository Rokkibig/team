"""
LLM Security Layer - JSON Schema Validation & Sanitization
Protects against injection attacks and validates all LLM responses
"""

import json
import jsonschema
import re
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# =============================================================================
# JSON SCHEMAS FOR ALL LLM RESPONSES
# =============================================================================

SYNTHESIS_SCHEMA = {
    "type": "object",
    "required": ["action_plan", "synthesis_reasoning"],
    "properties": {
        "synthesis_reasoning": {
            "type": "string",
            "maxLength": 2000,
            "minLength": 10
        },
        "action_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["priority", "type", "issue", "agent"],
                "properties": {
                    "priority": {"type": "integer", "minimum": 1, "maximum": 100},
                    "type": {"enum": ["fix", "improve", "refactor", "test"]},
                    "issue": {"type": "string", "maxLength": 1000, "minLength": 5},
                    "agent": {"type": "string", "maxLength": 100, "minLength": 2}
                }
            },
            "minItems": 1,
            "maxItems": 50
        }
    }
}

PATTERN_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["pattern_type", "root_cause", "suggested_fix"],
        "properties": {
            "pattern_type": {
                "enum": ["skill_gap", "agent_conflict", "process_issue"]
            },
            "root_cause": {"type": "string", "maxLength": 500},
            "suggested_fix": {"type": "string", "maxLength": 1000},
            "agents_involved": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0}
        }
    },
    "maxItems": 20
}

CONSENSUS_SCHEMA = {
    "type": "object",
    "required": ["decision", "confidence", "reasoning"],
    "properties": {
        "decision": {"enum": ["approve", "reject", "conditional"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "maxLength": 2000},
        "concerns": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 10
        }
    }
}

# =============================================================================
# SANITIZATION FUNCTIONS
# =============================================================================

def sanitize_llm_response(raw: str) -> str:
    """
    Remove potential injections from LLM response

    Protects against:
    - SQL injection attempts
    - Script tag injections
    - Command injection attempts
    - Path traversal
    """

    # Remove potential SQL injections
    raw = re.sub(
        r';\s*(DROP|DELETE|UPDATE|INSERT|CREATE|ALTER|EXEC|EXECUTE)',
        '',
        raw,
        flags=re.IGNORECASE
    )

    # Remove potential script tags
    raw = re.sub(
        r'<script[^>]*>.*?</script>',
        '',
        raw,
        flags=re.IGNORECASE | re.DOTALL
    )

    # Remove potential command injections
    raw = re.sub(r'[;&|`$]', '', raw)

    # Remove path traversal attempts
    raw = raw.replace('../', '').replace('..\\', '')

    # Remove null bytes
    raw = raw.replace('\x00', '')

    return raw

def extract_json_from_text(text: str) -> Optional[str]:
    """
    Extract JSON from LLM response that might contain extra text

    Handles:
    - Markdown code blocks
    - Extra explanation text
    - Multiple JSON objects (takes first)
    """

    # Try markdown code block first
    code_block = re.search(r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
    if code_block:
        return code_block.group(1)

    # Try to find JSON object
    json_obj = re.search(r'\{.*\}', text, re.DOTALL)
    if json_obj:
        return json_obj.group()

    # Try to find JSON array
    json_arr = re.search(r'\[.*\]', text, re.DOTALL)
    if json_arr:
        return json_arr.group()

    return None

# =============================================================================
# SAFE PARSING FUNCTIONS
# =============================================================================

@dataclass
class ParsedSynthesis:
    """Validated synthesis response"""
    action_plan: List[Dict]
    reasoning: str
    raw_response: str

def safe_parse_synthesis(raw: str) -> ParsedSynthesis:
    """
    Parse and validate synthesis response

    Raises:
        ValueError: If response is invalid or malicious
    """

    try:
        # Sanitize first
        clean = sanitize_llm_response(raw)

        # Extract JSON
        json_str = extract_json_from_text(clean)
        if not json_str:
            raise ValueError("No JSON found in response")

        # Parse JSON
        data = json.loads(json_str)

        # Validate against schema
        jsonschema.validate(data, SYNTHESIS_SCHEMA)

        # Normalize and sanitize values
        for step in data["action_plan"]:
            step["priority"] = min(100, max(1, int(step["priority"])))
            step["type"] = step["type"].lower()
            step["issue"] = step["issue"][:1000].strip()
            step["agent"] = step["agent"][:100].strip()

        return ParsedSynthesis(
            action_plan=data["action_plan"],
            reasoning=data["synthesis_reasoning"],
            raw_response=raw
        )

    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        logger.error(f"LLM synthesis validation failed: {e}")
        logger.debug(f"Raw response: {raw[:500]}")
        raise ValueError(f"Invalid LLM synthesis response: {e}")

@dataclass
class ParsedPattern:
    """Validated pattern analysis"""
    pattern_type: str
    root_cause: str
    suggested_fix: str
    agents_involved: List[str]
    confidence: float

def safe_parse_patterns(raw: str) -> List[ParsedPattern]:
    """
    Parse and validate pattern analysis response

    Returns empty list if parsing fails (non-critical)
    """

    try:
        clean = sanitize_llm_response(raw)
        json_str = extract_json_from_text(clean)

        if not json_str:
            logger.warning("No JSON found in pattern response")
            return []

        data = json.loads(json_str)
        jsonschema.validate(data, PATTERN_SCHEMA)

        patterns = []
        for item in data:
            patterns.append(ParsedPattern(
                pattern_type=item["pattern_type"],
                root_cause=item["root_cause"],
                suggested_fix=item["suggested_fix"],
                agents_involved=item.get("agents_involved", []),
                confidence=item.get("confidence", 0.5)
            ))

        return patterns

    except Exception as e:
        logger.error(f"Pattern parsing failed: {e}")
        return []

@dataclass
class ParsedConsensus:
    """Validated consensus decision"""
    decision: str  # approve/reject/conditional
    confidence: float
    reasoning: str
    concerns: List[str]

def safe_parse_consensus(raw: str) -> ParsedConsensus:
    """Parse and validate consensus response"""

    try:
        clean = sanitize_llm_response(raw)
        json_str = extract_json_from_text(clean)

        if not json_str:
            raise ValueError("No JSON found in consensus response")

        data = json.loads(json_str)
        jsonschema.validate(data, CONSENSUS_SCHEMA)

        return ParsedConsensus(
            decision=data["decision"],
            confidence=data["confidence"],
            reasoning=data["reasoning"],
            concerns=data.get("concerns", [])
        )

    except Exception as e:
        logger.error(f"Consensus parsing failed: {e}")
        raise ValueError(f"Invalid consensus response: {e}")

# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_agent_name(name: str) -> bool:
    """Validate agent name format"""
    return bool(re.match(r'^[a-z_][a-z0-9_-]{1,50}$', name, re.IGNORECASE))

def validate_task_id(task_id: str) -> bool:
    """Validate task ID format (UUID)"""
    return bool(re.match(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        task_id,
        re.IGNORECASE
    ))

def sanitize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize metrics dictionary for safe storage"""

    sanitized = {}

    for key, value in metrics.items():
        # Only allow alphanumeric keys
        clean_key = re.sub(r'[^a-z0-9_]', '_', key.lower())

        # Sanitize values
        if isinstance(value, (int, float)):
            # Limit numeric values
            sanitized[clean_key] = min(1e9, max(-1e9, value))
        elif isinstance(value, str):
            # Limit string length and sanitize
            sanitized[clean_key] = sanitize_llm_response(value[:500])
        elif isinstance(value, bool):
            sanitized[clean_key] = value
        # Ignore other types

    return sanitized
