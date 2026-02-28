from st_recruitment_svc.models import Base


def test_interviews_tables_registered():
    tables = Base.metadata.tables
    assert 'interviews' in tables
    assert 'interview_reschedule_requests' in tables

    cols = set(tables['interviews'].columns.keys())
    expected = {'id', 'application_id', 'start_at', 'duration_minutes', 'format', 'location_or_link', 'interviewer_names', 'status', 'reschedule_count', 'created_at', 'updated_at'}
    assert expected.issubset(cols)

    req_cols = set(tables['interview_reschedule_requests'].columns.keys())
    expected_req = {'id', 'interview_id', 'requested_by_user_id', 'reason', 'preferred_times', 'requested_at'}
    assert expected_req.issubset(req_cols)

    # Ensure indexes exist by name
    idx_names = {idx.name for idx in tables['interviews'].indexes}
    assert 'idx_interviews_application_id' in idx_names

    req_idx_names = {idx.name for idx in tables['interview_reschedule_requests'].indexes}
    assert 'idx_interview_reschedule_requests_interview_id' in req_idx_names
