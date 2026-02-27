from st_recruitment_svc.models import Base


def test_job_seeker_profile_table_registered():
    tables = Base.metadata.tables
    assert 'job_seeker_profiles' in tables
    cols = set(tables['job_seeker_profiles'].columns.keys())
    expected = {
        'id',
        'user_id',
        'full_name',
        'email',
        'phone',
        'location',
        'summary',
        'experiences',
        'education',
        'skills',
        'languages',
        'certifications',
        'created_at',
        'updated_at',
    }
    assert expected.issubset(cols)
    # Ensure unique constraint on user_id exists by name
    constraints = [c for c in tables['job_seeker_profiles'].constraints if getattr(c, 'name', None) == 'uq_job_seeker_profiles_user_id']
    assert len(constraints) == 1
