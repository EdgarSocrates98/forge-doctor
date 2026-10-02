"""Organization policy packs - declarative forbid/require rules.

Rule ids are org-defined (e.g. ``ORG001``), so findings produced here
carry the pack's own check ids and can be governed by severity policy
and suppressions like any built-in rule. When no packs are configured
the check emits nothing - policy packs are strictly opt-in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.core.models import CheckResult, Confidence, Severity
from forge_doctor.core.policy_pack import POLICY_CATEGORY, evaluate_packs, load_packs
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

CHECK_ID = "POLICY010"


class OrgPolicyPacks(CheckBase):
    """Evaluate discovered org policy packs against the project."""

    id = CHECK_ID
    title = "Organization policy packs"
    category = POLICY_CATEGORY
    why = "Org rules encode requirements no built-in check knows about."
    when_ok = "Every discovered pack is valid and has no violations."
    fix = "Fix the violating resource/file, or amend the pack."
    confidence = Confidence.HIGH

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        packs, errors = load_packs(ctx.root, ctx.config.policy_packs)
        results = list(errors)
        results.extend(evaluate_packs(ctx, packs))
        if not packs and not errors:
            return []
        if not results:
            results.append(self.result(Severity.PASS, "no policy pack violations"))
        return results


CHECKS: list[Check] = [OrgPolicyPacks()]
