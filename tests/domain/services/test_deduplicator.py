"""Covers `job_seeker.domain.services.deduplicator`.

Cross-board dedup is the whole reason to aggregate many boards: the same posting appears on
Himalayas, RemoteOK, and WeWorkRemotely with three different apply URLs, and it must collapse to
one. Keying on URL (as the old `Job.fingerprint` did) could never do that. Identity is normalized
company + normalized title, so the URL differences do not matter and legal-suffix noise in the
company name does not split a match.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from job_seeker.domain.models import CurrencySource, EligibilityHints, Job, SalaryRange
from job_seeker.domain.services.deduplicator import Deduplicator


def _dedupe(jobs: list[Job]) -> list[Job]:
    return Deduplicator().dedupe(jobs)


class TestCrossBoardIdentity:
    def test_the_same_posting_from_three_boards_collapses_to_one(
        self, make_job: Callable[..., Job]
    ) -> None:
        """This is the case the old URL-keyed fingerprint could not merge."""
        boards = [
            make_job(
                company="Acme",
                title="AI Engineer",
                source="himalayas",
                url="https://himalayas.app/jobs/acme-ai-engineer",
            ),
            make_job(
                company="Acme",
                title="AI Engineer",
                source="remotive",
                url="https://remotive.com/remote-jobs/12345",
            ),
            make_job(
                company="Acme",
                title="AI Engineer",
                source="remoteok",
                url="https://remoteok.com/l/98765",
            ),
        ]
        assert len(_dedupe(boards)) == 1

    def test_legal_suffix_and_case_variants_of_a_company_merge(
        self, make_job: Callable[..., Job]
    ) -> None:
        jobs = [
            make_job(company="Acme", title="AI Engineer", url="https://a/1"),
            make_job(company="Acme, Inc.", title="ai engineer", url="https://b/2"),
            make_job(company="ACME LLC", title="AI  Engineer", url="https://c/3"),
        ]
        assert len(_dedupe(jobs)) == 1

    def test_different_titles_at_one_company_stay_separate(
        self, make_job: Callable[..., Job]
    ) -> None:
        jobs = [
            make_job(company="Acme", title="AI Engineer", url="https://a/1"),
            make_job(company="Acme", title="Data Engineer", url="https://a/2"),
        ]
        assert len(_dedupe(jobs)) == 2

    def test_the_same_title_at_different_companies_stays_separate(
        self, make_job: Callable[..., Job]
    ) -> None:
        jobs = [
            make_job(company="Acme", title="AI Engineer", url="https://a/1"),
            make_job(company="Globex", title="AI Engineer", url="https://b/2"),
        ]
        assert len(_dedupe(jobs)) == 2


class TestMergePolicy:
    def test_keeps_the_newest_of_a_merged_group(self, make_job: Callable[..., Job]) -> None:
        """Freshness wins: a stale duplicate must not shadow a newer posting, or the survivor
        could be filtered out later by max_age while the fresh one was discarded."""
        older = make_job(url="https://a/1", posted_at=datetime(2026, 1, 1, tzinfo=UTC))
        newer = make_job(url="https://b/2", posted_at=datetime(2026, 7, 1, tzinfo=UTC))
        kept = _dedupe([older, newer])
        assert len(kept) == 1
        assert kept[0].url == "https://b/2"

    def test_prefers_the_richer_record_when_dates_are_equal(
        self, make_job: Callable[..., Job]
    ) -> None:
        when = datetime(2026, 7, 1, tzinfo=UTC)
        sparse = make_job(url="https://a/1", posted_at=when, description="", salary=None)
        rich = make_job(
            url="https://b/2",
            posted_at=when,
            description="Full JD here",
            salary=SalaryRange(
                minimum=150_000, currency="USD", currency_source=CurrencySource.PUBLISHED
            ),
        )
        kept = _dedupe([sparse, rich])
        assert len(kept) == 1
        assert kept[0].url == "https://b/2"

    def test_the_survivor_keeps_pay_the_loser_published(self, make_job: Callable[..., Job]) -> None:
        """The bug this merge exists to stop.

        Freshness decides the representative, so a copy posted an hour later with no pay used to
        win outright and take the published salary with it. A seeker asking for jobs over 150k
        then never saw a job whose 150k was on the wire.
        """
        paid = make_job(
            url="https://a/1",
            source="remoteok",
            posted_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
            salary=SalaryRange(
                minimum=150_000, currency="USD", currency_source=CurrencySource.PUBLISHED
            ),
        )
        newer_unpaid = make_job(
            url="https://b/2", source="himalayas", posted_at=datetime(2026, 8, 1, 11, tzinfo=UTC)
        )
        kept = _dedupe([paid, newer_unpaid])
        assert len(kept) == 1
        assert kept[0].url == "https://b/2"  # the fresher record is still the representative
        assert kept[0].salary is not None  # but it no longer arrives stripped
        assert kept[0].salary.minimum == 150_000

    def test_every_absent_field_is_completed_from_the_group(
        self, make_job: Callable[..., Job]
    ) -> None:
        sparse = make_job(
            url="https://b/2", posted_at=datetime(2026, 8, 1, 11, tzinfo=UTC), description=""
        )
        rich = make_job(
            url="https://a/1",
            posted_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
            description="Full JD here",
            location="Remote, LATAM",
            seniority="Senior",
            employment_type="Full Time",
        )
        merged = _dedupe([sparse, rich])[0]
        assert merged.description == "Full JD here"
        assert merged.location == "Remote, LATAM"
        assert merged.seniority == "Senior"
        assert merged.employment_type == "Full Time"

    def test_a_value_the_survivor_already_has_is_never_overwritten(
        self, make_job: Callable[..., Job]
    ) -> None:
        """Completing is not merging opinions. The representative's own facts stand."""
        winner = make_job(
            url="https://b/2",
            posted_at=datetime(2026, 8, 1, 11, tzinfo=UTC),
            description="The newer description",
            salary=SalaryRange(
                minimum=200_000, currency="USD", currency_source=CurrencySource.PUBLISHED
            ),
        )
        loser = make_job(
            url="https://a/1",
            posted_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
            description="The older description",
            salary=SalaryRange(
                minimum=100_000, currency="USD", currency_source=CurrencySource.PUBLISHED
            ),
        )
        merged = _dedupe([winner, loser])[0]
        assert merged.description == "The newer description"
        assert merged.salary is not None and merged.salary.minimum == 200_000

    def test_identity_fields_stay_the_survivors_own(self, make_job: Callable[..., Job]) -> None:
        """A merged record must still say truthfully where it came from and where to apply."""
        winner = make_job(
            url="https://b/2", source="himalayas", posted_at=datetime(2026, 8, 1, 11, tzinfo=UTC)
        )
        loser = make_job(
            url="https://a/1",
            source="remoteok",
            posted_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
            salary=SalaryRange(
                minimum=150_000, currency="USD", currency_source=CurrencySource.PUBLISHED
            ),
        )
        merged = _dedupe([winner, loser])[0]
        assert merged.url == "https://b/2"
        assert merged.source == "himalayas"

    def test_unknown_eligibility_hints_are_completed_but_stated_ones_are_not(
        self, make_job: Callable[..., Job]
    ) -> None:
        """Hints carry the absent-vs-empty distinction, so each half fills independently and a
        board that actually said something is never overruled by one that did not."""
        winner = make_job(
            url="https://b/2",
            posted_at=datetime(2026, 8, 1, 11, tzinfo=UTC),
            hints=EligibilityHints(location_restrictions=("Mexico",)),  # timezone unknown
        )
        loser = make_job(
            url="https://a/1",
            posted_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
            hints=EligibilityHints(
                location_restrictions=("Brazil",), timezone_restrictions=(-6.0,)
            ),
        )
        merged = _dedupe([winner, loser])[0]
        assert merged.hints.location_restrictions == ("Mexico",)  # the survivor's claim stands
        assert merged.hints.timezone_restrictions == (-6.0,)  # the unknown half is filled

    def test_hints_the_survivor_fully_stated_are_left_entirely_alone(
        self, make_job: Callable[..., Job]
    ) -> None:
        """Nothing to fill means nothing is rebuilt, so a complete record passes through as-is."""
        stated = EligibilityHints(location_restrictions=("Mexico",), timezone_restrictions=(-6.0,))
        winner = make_job(
            url="https://b/2", posted_at=datetime(2026, 8, 1, 11, tzinfo=UTC), hints=stated
        )
        loser = make_job(
            url="https://a/1",
            posted_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
            hints=EligibilityHints(location_restrictions=("Brazil",), timezone_restrictions=(0.0,)),
        )
        assert _dedupe([winner, loser])[0].hints == stated

    def test_a_record_with_a_date_beats_one_without(self, make_job: Callable[..., Job]) -> None:
        undated = make_job(url="https://a/1", posted_at=None)
        dated = make_job(url="https://b/2", posted_at=datetime(2026, 1, 1, tzinfo=UTC))
        kept = _dedupe([undated, dated])
        assert kept[0].url == "https://b/2"


class TestRobustness:
    def test_a_naive_and_an_undated_duplicate_do_not_crash_the_merge(
        self, make_job: Callable[..., Job]
    ) -> None:
        """A naive posted_at compared against the aware undated sentinel raised TypeError and
        aborted the whole run. Job now stamps naive datetimes as UTC, so the compare is safe."""
        naive = make_job(url="https://a/1", posted_at=datetime(2026, 1, 1))  # no tzinfo
        undated = make_job(url="https://b/2", posted_at=None)
        kept = _dedupe([naive, undated])
        assert len(kept) == 1
        assert kept[0].url == "https://a/1"  # the dated one wins

    def test_two_blank_company_postings_with_the_same_title_do_not_merge(
        self, make_job: Callable[..., Job]
    ) -> None:
        """A company that normalizes to empty must not collapse every same-title posting into one:
        that is the silent over-dedup this service exists to avoid."""
        a = make_job(company="", title="AI Engineer", url="https://a/1")
        b = make_job(company="", title="AI Engineer", url="https://b/2")
        assert len(_dedupe([a, b])) == 2


class TestDegenerateInputs:
    def test_empty_input_is_empty_output(self) -> None:
        assert _dedupe([]) == []

    def test_a_single_job_is_returned_unchanged(self, make_job: Callable[..., Job]) -> None:
        job = make_job()
        assert _dedupe([job]) == [job]

    def test_distinct_jobs_all_survive_and_keep_their_order(
        self, make_job: Callable[..., Job]
    ) -> None:
        a = make_job(company="Acme", title="AI Engineer", url="https://a/1")
        b = make_job(company="Globex", title="Data Scientist", url="https://b/2")
        c = make_job(company="Initech", title="SRE", url="https://c/3")
        assert [j.url for j in _dedupe([a, b, c])] == ["https://a/1", "https://b/2", "https://c/3"]


class TestIdentity:
    def test_identity_ignores_url_and_source(self, make_job: Callable[..., Job]) -> None:
        dedup = Deduplicator()
        a = make_job(company="Acme", title="AI Engineer", url="https://a/1", source="himalayas")
        b = make_job(company="Acme", title="AI Engineer", url="https://b/2", source="remotive")
        assert dedup.identity(a) == dedup.identity(b)

    def test_identity_is_blank_company_safe(self, make_job: Callable[..., Job]) -> None:
        """A record with no company still gets a stable identity from the title alone rather
        than colliding every empty-company posting into one."""
        a = make_job(company="", title="AI Engineer", url="https://a/1")
        b = make_job(company="", title="Data Engineer", url="https://b/2")
        assert Deduplicator().identity(a) != Deduplicator().identity(b)
