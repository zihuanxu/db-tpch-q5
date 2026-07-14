from __future__ import annotations

from collections import OrderedDict

import pyarrow as pa


def q5_schemas() -> dict[str, pa.Schema]:
    return OrderedDict(
        [
            (
                "region",
                pa.schema(
                    [
                        pa.field("r_regionkey", pa.int32(), nullable=False),
                        pa.field("r_name", pa.dictionary(pa.int32(), pa.string()), nullable=False),
                    ]
                ),
            ),
            (
                "nation",
                pa.schema(
                    [
                        pa.field("n_nationkey", pa.int32(), nullable=False),
                        pa.field("n_name", pa.dictionary(pa.int32(), pa.string()), nullable=False),
                        pa.field("n_regionkey", pa.int32(), nullable=False),
                    ]
                ),
            ),
            (
                "supplier",
                pa.schema(
                    [
                        pa.field("s_suppkey", pa.int32(), nullable=False),
                        pa.field("s_nationkey", pa.int32(), nullable=False),
                    ]
                ),
            ),
            (
                "customer",
                pa.schema(
                    [
                        pa.field("c_custkey", pa.int32(), nullable=False),
                        pa.field("c_nationkey", pa.int32(), nullable=False),
                    ]
                ),
            ),
            (
                "orders",
                pa.schema(
                    [
                        pa.field("o_orderkey", pa.int32(), nullable=False),
                        pa.field("o_custkey", pa.int32(), nullable=False),
                        pa.field("o_orderdate", pa.date32(), nullable=False),
                    ]
                ),
            ),
            (
                "lineitem",
                pa.schema(
                    [
                        pa.field("l_orderkey", pa.int32(), nullable=False),
                        pa.field("l_suppkey", pa.int32(), nullable=False),
                        pa.field("l_extendedprice", pa.decimal128(15, 2), nullable=False),
                        pa.field("l_discount", pa.decimal128(15, 2), nullable=False),
                    ]
                ),
            ),
        ]
    )


def schema_string(schema: pa.Schema) -> str:
    return schema.to_string()
