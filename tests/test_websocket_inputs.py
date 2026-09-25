from unittest.mock import AsyncMock

from bfxapi.websocket._client.bfx_websocket_inputs import BfxWebSocketInputs


def _make_inputs() -> tuple[BfxWebSocketInputs, AsyncMock]:
    handler = AsyncMock(return_value=None)
    return BfxWebSocketInputs(handler), handler


class TestOrders:
    async def test_submit_order(self):
        inputs, handler = _make_inputs()
        await inputs.submit_order("EXCHANGE LIMIT", "tBTCUSD", "0.5", "30000")
        handler.assert_awaited_once_with(
            "on",
            {
                "type": "EXCHANGE LIMIT",
                "symbol": "tBTCUSD",
                "amount": "0.5",
                "price": "30000",
                "lev": None,
                "price_trailing": None,
                "price_aux_limit": None,
                "price_oco_stop": None,
                "gid": None,
                "cid": None,
                "flags": None,
                "tif": None,
                "meta": None,
            },
        )

    async def test_submit_order_with_optional_args(self):
        inputs, handler = _make_inputs()
        await inputs.submit_order(
            "LIMIT",
            "tBTCUSD",
            "0.5",
            "30000",
            lev=10,
            price_trailing="10",
            price_aux_limit="29000",
            price_oco_stop="28000",
            gid=1,
            cid=2,
            flags=4096,
            tif="2021-01-01 00:00:00",
            meta={"aff_code": "bfxapi"},
        )
        handler.assert_awaited_once_with(
            "on",
            {
                "type": "LIMIT",
                "symbol": "tBTCUSD",
                "amount": "0.5",
                "price": "30000",
                "lev": 10,
                "price_trailing": "10",
                "price_aux_limit": "29000",
                "price_oco_stop": "28000",
                "gid": 1,
                "cid": 2,
                "flags": 4096,
                "tif": "2021-01-01 00:00:00",
                "meta": {"aff_code": "bfxapi"},
            },
        )

    async def test_update_order(self):
        inputs, handler = _make_inputs()
        await inputs.update_order(1234567, amount="0.25", price="31000")
        handler.assert_awaited_once_with(
            "ou",
            {
                "id": 1234567,
                "amount": "0.25",
                "price": "31000",
                "cid": None,
                "cid_date": None,
                "gid": None,
                "flags": None,
                "lev": None,
                "delta": None,
                "price_aux_limit": None,
                "price_trailing": None,
                "tif": None,
            },
        )

    async def test_update_order_with_optional_args(self):
        inputs, handler = _make_inputs()
        await inputs.update_order(
            1234567,
            cid=2,
            cid_date="2021-01-01",
            gid=1,
            flags=4096,
            lev=10,
            delta="0.1",
            price_aux_limit="29000",
            price_trailing="10",
            tif="2021-01-02 00:00:00",
        )
        handler.assert_awaited_once_with(
            "ou",
            {
                "id": 1234567,
                "amount": None,
                "price": None,
                "cid": 2,
                "cid_date": "2021-01-01",
                "gid": 1,
                "flags": 4096,
                "lev": 10,
                "delta": "0.1",
                "price_aux_limit": "29000",
                "price_trailing": "10",
                "tif": "2021-01-02 00:00:00",
            },
        )

    async def test_cancel_order_by_id(self):
        inputs, handler = _make_inputs()
        await inputs.cancel_order(id=1234567)
        handler.assert_awaited_once_with(
            "oc", {"id": 1234567, "cid": None, "cid_date": None}
        )

    async def test_cancel_order_by_cid(self):
        inputs, handler = _make_inputs()
        await inputs.cancel_order(cid=987654, cid_date="2021-01-01")
        handler.assert_awaited_once_with(
            "oc", {"id": None, "cid": 987654, "cid_date": "2021-01-01"}
        )

    async def test_cancel_order_multi(self):
        inputs, handler = _make_inputs()
        await inputs.cancel_order_multi(
            id=[1234567], cid=[(987654, "2021-01-01")], gid=[1]
        )
        handler.assert_awaited_once_with(
            "oc_multi",
            {
                "id": [1234567],
                "cid": [(987654, "2021-01-01")],
                "gid": [1],
                "all": None,
            },
        )

    async def test_cancel_order_multi_all(self):
        inputs, handler = _make_inputs()
        await inputs.cancel_order_multi(all=True)
        handler.assert_awaited_once_with(
            "oc_multi",
            {"id": None, "cid": None, "gid": None, "all": True},
        )


class TestFunding:
    async def test_submit_funding_offer(self):
        inputs, handler = _make_inputs()
        await inputs.submit_funding_offer("LIMIT", "fUSD", "1000", "0.0002", 30)
        handler.assert_awaited_once_with(
            "fon",
            {
                "type": "LIMIT",
                "symbol": "fUSD",
                "amount": "1000",
                "rate": "0.0002",
                "period": 30,
                "flags": None,
            },
        )

    async def test_submit_funding_offer_with_flags(self):
        inputs, handler = _make_inputs()
        await inputs.submit_funding_offer(
            "LIMIT", "fUSD", "1000", "0.0002", 2, flags=32768
        )
        handler.assert_awaited_once_with(
            "fon",
            {
                "type": "LIMIT",
                "symbol": "fUSD",
                "amount": "1000",
                "rate": "0.0002",
                "period": 2,
                "flags": 32768,
            },
        )

    async def test_cancel_funding_offer(self):
        inputs, handler = _make_inputs()
        await inputs.cancel_funding_offer(41238905)
        handler.assert_awaited_once_with("foc", {"id": 41238905})


class TestCalc:
    async def test_calc_single_key(self):
        inputs, handler = _make_inputs()
        await inputs.calc("margin_base")
        handler.assert_awaited_once_with("calc", [["margin_base"]])

    async def test_calc_multiple_keys(self):
        inputs, handler = _make_inputs()
        await inputs.calc("margin_sym_tBTCUSD", "position_tBTCUSD")
        handler.assert_awaited_once_with(
            "calc", [["margin_sym_tBTCUSD"], ["position_tBTCUSD"]]
        )

    async def test_calc_without_keys(self):
        inputs, handler = _make_inputs()
        await inputs.calc()
        handler.assert_awaited_once_with("calc", [])
