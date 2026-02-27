from st_recruitment_svc.models import Base


def test_resumes_table_registered():
    tables = Base.metadata.tables
    assert 'resumes' in tables
    cols = set(tables['resumes'].columns.keys())
    expected = {'id', 'user_id', 'label', 'file_object_id', 'created_at'}
    assert expected.issubset(cols)

    # Ensure unique constraint on file_object_id exists by name
    constraints = [c for c in tables['resumes'].constraints if getattr(c, 'name', None) == 'uq_resumes_file_object_id']
    assert len(constraints) == 1

    # Ensure index on user_id exists by name
    idx_names = {idx.name for idx in tables['resumes'].indexes}
    assert 'idx_resumes_user_id' in idx_names
