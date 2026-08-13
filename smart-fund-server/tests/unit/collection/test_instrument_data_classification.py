from src.domain.collection.instrument_data_classification import instrument_data_class


def test_instrument_data_types_have_explicit_storage_semantics() -> None:
    assert instrument_data_class("fund_detail") == "profile"
    assert instrument_data_class("holdings") == "disclosure"
    assert instrument_data_class("nav") == "observation"
