import json
import re
from typing import Any


def _to_snake_case(string: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", string).lower()


def _object_hook(data: dict[str, Any]) -> Any:
    return {_to_snake_case(key): value for key, value in data.items()}


class JSONDecoder(json.JSONDecoder):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # requests uses simplejson as `complexjson` when installed; simplejson
        # passes an obsolete `encoding` kwarg that stdlib json.JSONDecoder
        # rejects on Python 3.9+ (confirmed failure on 3.12).
        kwargs.pop("encoding", None)
        super().__init__(*args, **kwargs, object_hook=_object_hook)


class _RawKeysJSONDecoder(json.JSONDecoder):
    """JSONDecoder that keeps object keys as sent.

    For replies keyed by data rather than by field name (currency codes such
    as ``"USD"``, or ``"vol_BFX"``), which snake-casing would turn into
    ``"u_s_d"`` and ``"vol__b_f_x"``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("encoding", None)  # see JSONDecoder
        super().__init__(*args, **kwargs)
