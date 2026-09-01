"""Importing a roster from whatever the coordinator actually has.

A coordinator did not ask for software. They have a spreadsheet, they are busy, and
they will abandon anything that needs setup they cannot finish in one sitting. So the
tests here are mostly about messy input: the right response to a bad row is to import
everything else and say precisely what was wrong, not to reject the file.
"""

from __future__ import annotations

from datetime import timedelta

from zamu.core.models import ActionClass
from zamu.core.store import InMemoryStore
from zamu.infra.importer import apply, parse_moment, read_duties, read_people

ORG = "org_test"

PEOPLE_CSV = """Full Name,Email Address,Skills,Active
Amara Okonkwo,amara@example.org,"food-safety, forklift",yes
Marcus Tran,marcus@example.org,food-safety,yes
Sofia Marchetti,sofia@example.org,,yes
Departed Person,gone@example.org,food-safety,no
"""


# -- people ----------------------------------------------------------------------------


def test_a_plain_spreadsheet_imports():
    report = read_people(PEOPLE_CSV, ORG)
    assert len(report.people) == 4
    assert report.problems == []

    amara = report.people[0]
    assert amara.name == "Amara Okonkwo"
    assert amara.qualifications == frozenset({"food-safety", "forklift"})
    assert not report.people[3].active


def test_column_names_are_matched_loosely():
    """Real spreadsheets say Name, Full Name, Volunteer, and 'name ' with a space."""
    for header in ("Name,Email", "  Volunteer , E-mail ", "PERSON,Contact"):
        report = read_people(f"{header}\nMarcus Tran,marcus@example.org\n", ORG)
        assert len(report.people) == 1, header


def test_direct_contact_is_off_until_somebody_opts_in():
    """A coordinator uploading a spreadsheet has not obtained anybody's consent to be
    messaged by software. Inferring it from an email column would make the whole
    authority model a formality."""
    report = read_people(PEOPLE_CSV, ORG)
    assert all(ActionClass.SEND_ASK not in p.opt_ins for p in report.people)


def test_an_explicit_opt_in_is_honoured():
    csv_text = "Name,Email,Consent\nMarcus,marcus@example.org,yes\nDevon,devon@example.org,no\n"
    people = {p.name: p for p in read_people(csv_text, ORG).people}
    assert ActionClass.SEND_ASK in people["Marcus"].opt_ins
    assert ActionClass.SEND_ASK not in people["Devon"].opt_ins


def test_a_bad_row_is_reported_and_the_rest_still_import():
    csv_text = (
        "Name,Email\n"
        "Good Person,good@example.org\n"
        ",orphan@example.org\n"
        "No Email Person,not-an-email\n"
        "Another Good,another@example.org\n"
    )
    report = read_people(csv_text, ORG)
    assert [p.name for p in report.people] == ["Good Person", "Another Good"]
    assert report.skipped == 2
    assert "Row 3" in report.problems[0]
    assert "Row 4" in report.problems[1]


def test_a_duplicate_email_is_reported_rather_than_silently_overwriting():
    csv_text = "Name,Email\nA,same@example.org\nB,same@example.org\n"
    report = read_people(csv_text, ORG)
    assert len(report.people) == 1
    assert "more than once" in report.problems[0]


def test_a_missing_name_column_says_what_it_looked_for():
    report = read_people("Email,Skills\na@b.c,x\n", ORG)
    assert not report.ok
    assert "volunteer" in report.problems[0]


def test_an_empty_file_is_not_a_crash():
    assert read_people("", ORG).problems == ["That file is empty."]
    assert read_duties("   ", ORG).problems == ["That file is empty."]


def test_semicolon_and_tab_separated_files_work():
    for text in (
        "Name;Email\nMarcus Tran;marcus@example.org\n",
        "Name\tEmail\nMarcus Tran\tmarcus@example.org\n",
    ):
        assert len(read_people(text, ORG).people) == 1


def test_ids_are_stable_so_re_importing_updates_rather_than_duplicates():
    first = read_people(PEOPLE_CSV, ORG).people
    second = read_people(PEOPLE_CSV, ORG).people
    assert [p.id for p in first] == [p.id for p in second]


# -- times -----------------------------------------------------------------------------


def test_a_range_of_written_date_formats_is_understood():
    for text in (
        "2026-09-04 18:00",
        "2026-09-04T18:00",
        "04/09/2026 18:00",
        "4 Sep 2026 18:00",
    ):
        moment = parse_moment(text, "America/Chicago")
        assert moment is not None, text
        assert moment.hour == 23  # 18:00 Chicago is 23:00 UTC


def test_a_bare_date_becomes_a_morning_shift_rather_than_a_rejection():
    moment = parse_moment("2026-09-04", "UTC")
    assert moment is not None and moment.hour == 9


def test_nonsense_returns_none_rather_than_guessing():
    assert parse_moment("next Tuesday-ish", "UTC") is None
    assert parse_moment("", "UTC") is None


# -- duties ----------------------------------------------------------------------------


DUTIES_CSV = """Shift,Start,End,Role,Requires,Assigned To
Evening distribution,2026-09-04 18:00,2026-09-04 20:00,Distribution,food-safety,Amara Okonkwo
Saturday intake,2026-09-05 08:00,2026-09-05 13:00,Intake,food-safety,
Closing shift,2026-09-04 20:00,,Distribution,,marcus@example.org
"""


def test_duties_import_and_resolve_their_assignees():
    people = read_people(PEOPLE_CSV, ORG).people
    report = read_duties(DUTIES_CSV, ORG, timezone_name="America/Chicago", people=people)

    assert len(report.duties) == 3
    assert report.duties[0].assigned_person_id == people[0].id
    assert report.duties[1].assigned_person_id is None
    # Resolved by email as well as by name.
    assert report.duties[2].assigned_person_id == people[1].id


def test_an_imported_assignment_is_unconfirmed_not_covered():
    """Nobody has confirmed anything *to Zamu* yet. Importing a spreadsheet as though
    every row were a confirmed promise is how coverage software starts lying."""
    from zamu.core.coverage import assess_duty
    from zamu.core.models import CoverageState, Org, Roster

    people = read_people(PEOPLE_CSV, ORG).people
    duties = read_duties(DUTIES_CSV, ORG, timezone_name="UTC", people=people).duties
    org = Org(id=ORG, name="Test")
    roster = Roster(org=org, people=tuple(people), duties=tuple(duties))

    covered = duties[0]
    assert covered.confirmed_at is None
    before = covered.start - timedelta(days=1)
    state = assess_duty(covered, org, before, roster)
    assert state.state is CoverageState.UNKNOWN


def test_a_missing_end_time_defaults_to_a_two_hour_shift():
    people = read_people(PEOPLE_CSV, ORG).people
    duties = read_duties(DUTIES_CSV, ORG, people=people).duties
    assert duties[2].window.hours == 2.0


def test_an_hours_column_is_used_when_there_is_no_end_time():
    text = "Shift,Start,Hours\nLong one,2026-09-04 09:00,5\n"
    assert read_duties(text, ORG).duties[0].window.hours == 5.0


def test_an_unrecognised_assignee_imports_the_shift_as_uncovered_and_says_so():
    people = read_people(PEOPLE_CSV, ORG).people
    text = "Shift,Start,Assigned To\nEvening,2026-09-04 18:00,Somebody Else\n"
    report = read_duties(text, ORG, people=people)

    assert report.duties[0].assigned_person_id is None
    assert "nobody on the roster matches" in report.problems[0]
    assert report.skipped == 0  # the shift was still imported


def test_a_backwards_shift_is_rejected_with_its_row_number():
    text = "Shift,Start,End\nBackwards,2026-09-04 20:00,2026-09-04 18:00\n"
    report = read_duties(text, ORG)
    assert report.duties == []
    assert "ends before it starts" in report.problems[0]


def test_an_unreadable_date_suggests_a_format():
    text = "Shift,Start\nVague,sometime next week\n"
    report = read_duties(text, ORG)
    assert "2026-09-04 18:00" in report.problems[0]


def test_a_missing_start_column_says_what_it_looked_for():
    report = read_duties("Shift,Role\nEvening,Distribution\n", ORG)
    assert not report.ok
    assert "starts at" in report.problems[0]


# -- applying --------------------------------------------------------------------------


def test_applying_writes_everything_to_the_store():
    from zamu.core.models import Org

    store = InMemoryStore()
    store.put_org(Org(id=ORG, name="Test"))

    people_report = apply(store, read_people(PEOPLE_CSV, ORG))
    apply(store, read_duties(DUTIES_CSV, ORG, people=people_report.people))

    assert len(store.list_people(ORG)) == 4
    assert len(store.list_duties(ORG)) == 3


def test_the_summary_reads_like_a_sentence():
    assert read_people(PEOPLE_CSV, ORG).summary() == "Imported 4 people."
    text = "Name,Email\nA,a@b.c\n,broken\n"
    assert "1 row skipped" in read_people(text, ORG).summary()
    assert read_people("", ORG).summary() == "Nothing could be read from that file."
