import json

from bfxapi._utils.json_decoder import JSONDecoder, _to_snake_case


class TestToSnakeCase:
    def test_camel_case(self):
        assert _to_snake_case("camelCase") == "camel_case"

    def test_pascal_case(self):
        # regex (?<!^) prevents inserting _ at start
        assert _to_snake_case("PascalCase") == "pascal_case"

    def test_already_snake_case(self):
        assert _to_snake_case("snake_case") == "snake_case"

    def test_single_word(self):
        assert _to_snake_case("word") == "word"

    def test_consecutive_upper(self):
        assert _to_snake_case("getHTTPResponse") == "get_h_t_t_p_response"

    def test_empty_string(self):
        assert _to_snake_case("") == ""

    def test_single_char(self):
        assert _to_snake_case("A") == "a"
        assert _to_snake_case("a") == "a"


class TestJSONDecoder:
    def test_simple_object(self):
        result = json.loads(
            '{"firstName": "John", "lastName": "Doe"}', cls=JSONDecoder
        )
        assert result == {"first_name": "John", "last_name": "Doe"}

    def test_nested_object(self):
        result = json.loads(
            '{"userInfo": {"firstName": "John"}}', cls=JSONDecoder
        )
        assert result == {"user_info": {"first_name": "John"}}

    def test_array_of_objects(self):
        result = json.loads('[{"chanId": 1}, {"chanId": 2}]', cls=JSONDecoder)
        assert result == [{"chan_id": 1}, {"chan_id": 2}]

    def test_non_object_passthrough(self):
        assert json.loads("[1, 2, 3]", cls=JSONDecoder) == [1, 2, 3]
        assert json.loads('"hello"', cls=JSONDecoder) == "hello"
        assert json.loads("42", cls=JSONDecoder) == 42

    def test_mixed_content(self):
        result = json.loads(
            '{"orderId": 123, "items": [1, 2, 3]}', cls=JSONDecoder
        )
        assert result == {"order_id": 123, "items": [1, 2, 3]}

    def test_tolerates_simplejson_encoding_kwarg(self):
        # Regression: `requests` routes response.json(cls=JSONDecoder) through
        # simplejson when python3-simplejson is installed, and simplejson.loads
        # injects an obsolete `encoding=` kwarg that stdlib json.JSONDecoder
        # rejects on Python 3.9+ (TypeError). The decoder must swallow it.
        # Without kwargs.pop("encoding", ...) constructing this raises TypeError.
        decoder = JSONDecoder(encoding="utf-8")
        assert decoder.decode('{"firstName": "John"}') == {"first_name": "John"}
