from src.app.models.call import Call


def test_call_model_includes_dashboard_indexes():
    indexes = {index.name: index for index in Call.__table__.indexes}

    # The (institution_id, agent_used, call_date) index was dropped in
    # alembic 20260512_drop_dead — dashboard now scopes by location_id,
    # not agent_used (commit 2d29e63).
    assert "ix_call_institution_agent_date" not in indexes
    assert "ix_call_institution_date" in indexes

    assert "ix_call_dashboard_open_callbacks" in indexes
    callback_index = indexes["ix_call_dashboard_open_callbacks"]
    assert [column.name for column in callback_index.columns] == [
        "institution_id",
        "call_date",
        "created_at",
    ]

    predicate = str(callback_index.dialect_options["postgresql"]["where"])
    assert "call_status" in predicate
    assert "needs_callback" in predicate
    assert "callback_resolved" in predicate
