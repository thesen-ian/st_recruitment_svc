from st_recruitment_svc.models import Base


def test_company_profile_table_registered():
    tables = Base.metadata.tables
    assert 'company_profiles' in tables
    cols = set(tables['company_profiles'].columns.keys())
    expected = {
        'id',
        'company_id',
        'description',
        'website_url',
        'industry',
        'size',
        'hq_location',
        'logo_file_object_id',
        'cover_file_object_id',
        'created_at',
        'updated_at',
    }
    assert expected.issubset(cols)

    # Ensure unique constraint on company_id exists by name
    constraints = [c for c in tables['company_profiles'].constraints if getattr(c, 'name', None) == 'uq_company_profiles_company_id']
    assert len(constraints) == 1

    # Ensure foreign key targets exist and point to expected tables
    fk_company = list(tables['company_profiles'].c['company_id'].foreign_keys)
    assert len(fk_company) == 1
    assert fk_company[0].target_fullname == 'users.id'

    fk_logo = list(tables['company_profiles'].c['logo_file_object_id'].foreign_keys)
    assert len(fk_logo) == 1
    assert fk_logo[0].target_fullname == 'file_objects.id'

    fk_cover = list(tables['company_profiles'].c['cover_file_object_id'].foreign_keys)
    assert len(fk_cover) == 1
    assert fk_cover[0].target_fullname == 'file_objects.id'
