from backend.db.session import get_session_factory
from backend.models import VerificationProbe
from backend.services.jobs import seed_sources


def main():
    with get_session_factory()() as db, db.begin():
        seed_sources(db)
        if db.get(VerificationProbe, "beat-heartbeat") is None:
            db.add(VerificationProbe(key="beat-heartbeat", count=0))
    print("Sources and verification initialized; existing settings and schedules preserved.")


if __name__ == "__main__":
    main()
