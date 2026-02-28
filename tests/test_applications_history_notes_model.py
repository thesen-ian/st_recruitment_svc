from st_recruitment_svc.models import Base


def test_application_history_and_notes_tables_registered():
    tables = Base.metadata.tables
    assert 'application_status_history' in tables
    assert 'application_notes' in tables

    history_cols = set(tables['application_status_history'].columns.keys())
    expected_hist = {'id', 'application_id', 'from_status', 'to_status', 'changed_by_user_id', 'changed_at', 'notes'}
    assert expected_hist.issubset(history_cols)

    notes_cols = set(tables['application_notes'].columns.keys())
    expected_notes = {'id', 'application_id', 'note_text', 'created_by_user_id', 'created_at'}
    assert expected_notes.issubset(notes_cols)

    # Ensure indexes exist (by name)
    hist_index_names = {idx.name for idx in tables['application_status_history'].indexes}
    assert 'idx_application_status_history_application_id_changed_at' in hist_index_names
    assert 'idx_application_status_history_application_id' in hist_index_names

    notes_index_names = {idx.name for idx in tables['application_notes'].indexes}
    assert 'idx_application_notes_application_id_created_at' in notes_index_names
