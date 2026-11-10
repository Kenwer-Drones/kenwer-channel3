"""Report origin/status of synthetic records in the development database.

Does not delete anything.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from kenwer_c3.settings import get_settings


def main() -> None:
    e = create_engine(get_settings().database_url)
    with e.connect() as c:
        print("=== DATABASE kenwer_c3 (dev) inventory ===")
        tables = [
            "tenants",
            "cognitive_identities",
            "beliefs",
            "belief_versions",
            "belief_proposals",
            "candidate_lessons",
            "attribution_hypotheses",
            "learning_jobs",
            "mission_episodes",
            "mission_events",
            "persistent_facts",
            "validation_decisions",
        ]
        for t in tables:
            n = c.execute(text(f"select count(*) from {t}")).scalar()
            print(f"{t}: {n}")

        print("--- belief_versions by status ---")
        for r in c.execute(
            text(
                "select approval_status, count(*) from belief_versions "
                "group by 1 order by 1"
            )
        ):
            print(tuple(r))

        print("--- belief_key family counts ---")
        for r in c.execute(
            text(
                """
                select
                  case
                    when belief_key like 'thermal.unapproved_only.%'
                      then 'thermal.unapproved_only.*'
                    when belief_key like 'thermal.wind_degrades_image_quality.%'
                      then 'thermal.wind_degrades_image_quality.*'
                    when belief_key like 'thermal.precheck.%'
                      then 'thermal.precheck.*'
                    when belief_key like 'thermal.challenge.%'
                      then 'thermal.challenge.*'
                    when belief_key like 'thermal.phase5.%'
                      then 'thermal.phase5.*'
                    when belief_key like 'lesson.%'
                      then 'lesson.*'
                    else left(belief_key, 48)
                  end as family,
                  count(*)
                from beliefs
                group by 1
                order by 2 desc
                """
            )
        ):
            print(tuple(r))

        print("--- recent versions (provenance / confidence) ---")
        for r in c.execute(
            text(
                """
                select left(b.belief_key, 56) as belief_key,
                       bv.approval_status,
                       bv.confidence_basis,
                       bv.evidence_support_label,
                       bv.provenance
                from belief_versions bv
                join beliefs b on b.id = bv.belief_id
                order by bv.created_at desc
                limit 10
                """
            )
        ):
            print(tuple(r))


if __name__ == "__main__":
    main()
