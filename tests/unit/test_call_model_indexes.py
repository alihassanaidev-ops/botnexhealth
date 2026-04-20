from src.app.models.call import Call


def test_call_model_includes_dashboard_indexes():
    indexes = {index.name: index for index in Call.__table__.indexes}

    assert "ix_call_institution_agent_date" in indexes
    assert [column.name for column in indexes["ix_call_institution_agent_date"].columns] == [
        "institution_id",
        "agent_used",
        "call_date",
    ]

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
