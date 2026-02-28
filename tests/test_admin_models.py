from st_recruitment_svc.models import Base
from st_recruitment_svc.models import Report, AdminAuditLog


def test_admin_tables_registered():
    tables = Base.metadata.tables
    assert 'reports' in tables
    assert 'admin_audit_logs' in tables


def test_jobs_has_is_removed_column():
    jobs_table = Base.metadata.tables.get('jobs')
    assert jobs_table is not None
    assert 'is_removed' in jobs_table.c


def test_details_json_accepts_dict():
    # Instantiate the model class and assign a dict to details_json to ensure type affinity
    log = AdminAuditLog(admin_user_id='u1', action_type='METRICS_VIEW', target_type='system', details_json={'q': 1})
    assert isinstance(log.details_json, dict)
