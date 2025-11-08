"""
Auto-fix Utilities
Automatic recovery from common failure scenarios
"""

import json
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# =============================================================================
# AUTO-FIX FOR GUARD FAILURES
# =============================================================================

@dataclass
class GuardFailure:
    """Information about a guard failure"""
    guard_name: str
    reason: str
    failed_checks: List[str]
    task_id: str
    current_state: str
    target_state: str

@dataclass
class AutoFixAction:
    """Action to fix guard failure"""
    action_type: str  # test/review/refactor/document
    priority: int
    description: str
    auto_added: bool = True

class GuardAutoFix:
    """
    Automatically fix guard failures

    Common scenarios:
    1. Deploying without tests → Add test generation task
    2. Reviewing without approval → Request peer review
    3. Missing documentation → Add documentation task
    """

    def __init__(self, db_pool):
        self.db = db_pool

    async def handle_guard_failure(
        self,
        task_id: str,
        guard_failure: GuardFailure
    ) -> Dict:
        """
        Handle guard failure with automatic fix

        Returns:
            Dict with fix actions taken
        """

        # Determine fix strategy
        fix_actions = self._determine_fixes(guard_failure)

        if not fix_actions:
            logger.warning(f"No auto-fix available for guard: {guard_failure.guard_name}")
            return {
                "status": "no_fix_available",
                "guard": guard_failure.guard_name
            }

        # Apply fixes
        success = await self._apply_fixes(task_id, fix_actions)

        if success:
            logger.info(
                f"Auto-fixed guard failure for task {task_id}: "
                f"{[a.action_type for a in fix_actions]}"
            )

            return {
                "status": "auto_fixed",
                "guard": guard_failure.guard_name,
                "actions": [
                    {
                        "type": a.action_type,
                        "description": a.description,
                        "priority": a.priority
                    }
                    for a in fix_actions
                ],
                "new_state": self._get_new_state(guard_failure)
            }
        else:
            return {
                "status": "fix_failed",
                "guard": guard_failure.guard_name
            }

    def _determine_fixes(
        self,
        failure: GuardFailure
    ) -> List[AutoFixAction]:
        """Determine what fixes are needed"""

        fixes = []

        # Guard: DEPLOYING requires tests
        if failure.guard_name == "has_tests" and failure.target_state == "deploying":
            fixes.append(AutoFixAction(
                action_type="test",
                priority=1,
                description="Generate missing tests for deployment readiness",
            ))

        # Guard: DEPLOYING requires security review
        elif failure.guard_name == "security_approved" and failure.target_state == "deploying":
            fixes.append(AutoFixAction(
                action_type="review",
                priority=1,
                description="Request security review for deployment",
            ))

        # Guard: REVIEWING requires code quality
        elif failure.guard_name == "code_quality" and failure.target_state == "reviewing":
            fixes.append(AutoFixAction(
                action_type="refactor",
                priority=2,
                description="Improve code quality to meet review standards",
            ))

        # Guard: Missing documentation
        elif failure.guard_name == "has_documentation":
            fixes.append(AutoFixAction(
                action_type="document",
                priority=3,
                description="Add missing documentation",
            ))

        # Guard: Performance requirements
        elif failure.guard_name == "performance_ok":
            fixes.append(AutoFixAction(
                action_type="improve",
                priority=2,
                description="Optimize performance to meet requirements",
            ))

        # Generic fix: Add investigation task
        if not fixes:
            fixes.append(AutoFixAction(
                action_type="fix",
                priority=1,
                description=f"Investigate and fix guard failure: {failure.reason}",
            ))

        return fixes

    async def _apply_fixes(
        self,
        task_id: str,
        actions: List[AutoFixAction]
    ) -> bool:
        """Apply fix actions to task"""

        try:
            # Convert to action plan format
            action_plan = [
                {
                    "priority": action.priority,
                    "type": action.action_type,
                    "issue": action.description,
                    "agent": self._get_agent_for_action(action.action_type),
                    "auto_added": True,
                    "reason": "guard_failure_auto_fix"
                }
                for action in actions
            ]

            # Update task in database
            async with self.db.acquire() as conn:
                # Move task back to developing state
                await conn.execute("""
                    UPDATE tasks
                    SET
                        state = 'developing',
                        metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                        updated_at = NOW()
                    WHERE id = $2
                """,
                    json.dumps({"action_plan": action_plan}),
                    task_id
                )

            return True

        except Exception as e:
            logger.error(f"Failed to apply auto-fix: {e}")
            return False

    def _get_agent_for_action(self, action_type: str) -> str:
        """Get appropriate agent for action type"""

        agent_map = {
            "test": "tester",
            "review": "reviewer",
            "refactor": "developer",
            "document": "developer",
            "improve": "developer",
            "fix": "developer"
        }

        return agent_map.get(action_type, "developer")

    def _get_new_state(self, failure: GuardFailure) -> str:
        """Determine new state after auto-fix"""

        # Most fixes require going back to developing
        return "developing"

# =============================================================================
# CONSENSUS EXPLAINABILITY
# =============================================================================

class ConsensusExplainer:
    """
    Explain consensus decisions for humans

    Makes the voting process transparent and debuggable
    """

    def __init__(self, role_weights: Dict[str, float]):
        """
        Args:
            role_weights: Voting weights per role
        """
        self.role_weights = role_weights

    def explain_consensus(
        self,
        votes: Dict[str, str],
        quorum: float = 0.75
    ) -> Dict:
        """
        Explain consensus calculation

        Args:
            votes: Dict of {agent_id: vote} where vote is approve/reject/conditional
            quorum: Required consensus threshold

        Returns:
            Dict with detailed explanation
        """

        contributions = {}
        details = []
        vote_counts = {"approve": 0, "conditional": 0, "reject": 0}

        # Calculate contributions
        for agent_id, vote in votes.items():
            # Extract role from agent_id (format: role-instance)
            agent_role = agent_id.split("-")[0] if "-" in agent_id else agent_id
            weight = self.role_weights.get(agent_role, 0.05)

            # Calculate contribution
            if vote == "approve":
                contribution = weight
                symbol = "✅"
                vote_counts["approve"] += 1
            elif vote == "conditional":
                contribution = weight * 0.5
                symbol = "⚠️"
                vote_counts["conditional"] += 1
            else:  # reject
                contribution = 0
                symbol = "❌"
                vote_counts["reject"] += 1

            contributions[agent_id] = contribution

            details.append(
                f"{symbol} {agent_id}: {vote.upper()} "
                f"(weight: {weight:.2f}, contribution: {contribution:.2f})"
            )

        # Calculate total consensus
        total = sum(contributions.values())
        passed = total >= quorum
        missing = max(0, quorum - total)

        # Generate summary
        summary = self._generate_summary(
            total, quorum, passed, vote_counts, len(votes)
        )

        return {
            "consensus_score": round(total, 3),
            "quorum_threshold": quorum,
            "passed": passed,
            "missing_for_quorum": round(missing, 3),
            "contributions": contributions,
            "vote_counts": vote_counts,
            "details": details,
            "summary": summary
        }

    def _generate_summary(
        self,
        consensus: float,
        quorum: float,
        passed: bool,
        vote_counts: Dict,
        total_voters: int
    ) -> str:
        """Generate human-readable summary"""

        if passed:
            margin = consensus - quorum
            return (
                f"✅ CONSENSUS REACHED: {consensus:.2f}/{quorum:.2f} "
                f"({vote_counts['approve']} approve, "
                f"{vote_counts['conditional']} conditional, "
                f"{vote_counts['reject']} reject) "
                f"- margin: +{margin:.2f}"
            )
        else:
            needed = quorum - consensus
            return (
                f"❌ CONSENSUS FAILED: {consensus:.2f}/{quorum:.2f} "
                f"({vote_counts['approve']} approve, "
                f"{vote_counts['conditional']} conditional, "
                f"{vote_counts['reject']} reject) "
                f"- need: +{needed:.2f}"
            )

    def explain_vote_impact(
        self,
        agent_id: str,
        current_votes: Dict[str, str],
        quorum: float = 0.75
    ) -> Dict:
        """
        Explain impact of a specific agent's vote

        Useful for understanding voting power
        """

        agent_role = agent_id.split("-")[0] if "-" in agent_id else agent_id
        weight = self.role_weights.get(agent_role, 0.05)

        # Calculate current consensus
        current_consensus = self.explain_consensus(current_votes, quorum)

        # Calculate potential impact
        potential_impact = {
            "approve": weight,
            "conditional": weight * 0.5,
            "reject": 0
        }

        return {
            "agent_id": agent_id,
            "role": agent_role,
            "weight": weight,
            "potential_impact": potential_impact,
            "current_consensus": current_consensus["consensus_score"],
            "could_single_handedly_pass": weight >= (quorum - current_consensus["consensus_score"]),
            "percentage_of_quorum": round(weight / quorum * 100, 1)
        }
