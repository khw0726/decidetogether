"""Re-triage every rule in the DB so applies_to/rule_type reflect title+text."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.automod.compiler.compiler import RuleCompiler
from src.automod.config import get_anthropic_client, settings
from src.automod.db.database import write_session
from src.automod.db.models import Community, Rule


async def main() -> None:
    compiler = RuleCompiler(get_anthropic_client(), settings)
    async with write_session() as db:
        rules = (await db.execute(select(Rule))).scalars().all()
        comms = {c.id: c for c in (await db.execute(select(Community))).scalars().all()}

    print(f"Re-triaging {len(rules)} rules...")
    sem = asyncio.Semaphore(8)

    async def one(rule: Rule):
        comm = comms.get(rule.community_id)
        if not comm:
            return rule.id, None, "no community"
        async with sem:
            try:
                result = await compiler.triage_rule(rule.title, rule.text, comm.name, comm.platform)
                return rule.id, result, None
            except Exception as e:
                return rule.id, None, str(e)

    results = await asyncio.gather(*[one(r) for r in rules])

    changed = 0
    failed = 0
    async with write_session() as db:
        for rid, result, err in results:
            if err:
                print(f"  FAIL {rid}: {err}")
                failed += 1
                continue
            rule = await db.get(Rule, rid)
            if not rule:
                continue
            old = (rule.rule_type, rule.applies_to)
            new = (result["rule_type"], result.get("applies_to", "both"))
            if old != new:
                changed += 1
                print(f"  {rule.title!r}: {old} -> {new}")
            rule.rule_type = new[0]
            rule.applies_to = new[1]
            rule.rule_type_reasoning = result.get("reasoning", "")
        await db.commit()

    print(f"\nDone. {changed} changed, {failed} failed, {len(rules) - changed - failed} unchanged.")


if __name__ == "__main__":
    asyncio.run(main())
