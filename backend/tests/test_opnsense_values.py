from app.services.opnsense_values import is_option_dict, options, selected


def test_is_option_dict_true_for_selected_objects():
    v = {"a": {"value": "A", "selected": "1"}, "b": {"value": "B", "selected": "0"}}
    assert is_option_dict(v) is True


def test_is_option_dict_false_for_plain_or_nested():
    assert is_option_dict({"x": "1"}) is False
    assert is_option_dict({}) is False


def test_options_maps_key_to_label():
    v = {"a": {"value": "Label A", "selected": "0"}}
    assert options(v) == [{"value": "a", "label": "Label A"}]


def test_selected_returns_keys_with_selected_1():
    v = {"a": {"value": "A", "selected": "1"}, "b": {"value": "B", "selected": "0"}}
    assert selected(v) == ["a"]
