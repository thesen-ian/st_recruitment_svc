from st_recruitment_svc.models import Base


def test_notifications_tables_registered():
    tables = Base.metadata.tables
    assert 'notifications' in tables
    assert 'notification_preferences' in tables

    # notifications columns
    notif_cols = set(tables['notifications'].columns.keys())
    expected_notif = {'id', 'user_id', 'title', 'body', 'type', 'related_entity_type', 'related_entity_id', 'is_read', 'read_at', 'created_at', 'updated_at'}
    assert expected_notif.issubset(notif_cols)

    # indexes exist in table args by name
    idx_names = {getattr(i, 'name', None) for i in tables['notifications'].indexes}
    assert 'idx_notifications_user_created_at' in idx_names
    assert 'idx_notifications_user_is_read_created_at' in idx_names

    # preferences columns
    pref_cols = set(tables['notification_preferences'].columns.keys())
    expected_pref = {'id', 'user_id', 'in_app_enabled', 'notify_application_submitted', 'notify_application_status_changed', 'notify_application_withdrawn', 'notify_interview_updates', 'notify_admin_report_updates', 'created_at', 'updated_at'}
    assert expected_pref.issubset(pref_cols)

    # unique constraint on user_id
    uqs = [c for c in tables['notification_preferences'].constraints if getattr(c, 'name', None) == 'uq_notification_preferences_user_id']
    assert len(uqs) == 1
