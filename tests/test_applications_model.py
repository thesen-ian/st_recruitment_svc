from st_recruitment_svc.models import Base


def test_applications_table_registered():
    tables = Base.metadata.tables
    assert 'applications' in tables
    assert 'application_answers' in tables

    cols = set(tables['applications'].columns.keys())
    expected = {'id', 'job_id', 'job_seeker_user_id', 'selected_resume_id', 'cover_letter', 'status', 'applied_at', 'updated_at'}
    assert expected.issubset(cols)

    # Ensure unique constraint on (job_id, job_seeker_user_id) exists
    constraints = [c for c in tables['applications'].constraints if getattr(c, 'name', None) == 'uq_applications_job_id_job_seeker_user_id']
    assert len(constraints) == 1

    # Ensure application_answers constraints and indexes are present
    ans_cols = set(tables['application_answers'].columns.keys())
    expected_ans = {'id', 'application_id', 'question_id', 'answer_text', 'selected_option_id', 'file_object_id'}
    assert expected_ans.issubset(ans_cols)

    constraints = [c for c in tables['application_answers'].constraints if getattr(c, 'name', None) == 'uq_application_answers_application_id_question_id']
    assert len(constraints) == 1

    chk = [c for c in tables['application_answers'].constraints if getattr(c, 'name', None) == 'chk_application_answers_single_answer_field']
    assert len(chk) == 1

