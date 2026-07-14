#!/usr/bin/env python3

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path


REGIONS = [
    (0, "AFRICA"),
    (1, "AMERICA"),
    (2, "ASIA"),
    (3, "EUROPE"),
    (4, "MIDDLE EAST"),
]

NATIONS = [
    (0, "ALGERIA", 0),
    (1, "ARGENTINA", 1),
    (2, "BRAZIL", 1),
    (3, "CANADA", 1),
    (4, "EGYPT", 4),
    (5, "ETHIOPIA", 0),
    (6, "FRANCE", 3),
    (7, "GERMANY", 3),
    (8, "INDIA", 2),
    (9, "INDONESIA", 2),
    (10, "IRAN", 4),
    (11, "IRAQ", 4),
    (12, "JAPAN", 2),
    (13, "JORDAN", 4),
    (14, "KENYA", 0),
    (15, "MOROCCO", 0),
    (16, "MOZAMBIQUE", 0),
    (17, "PERU", 1),
    (18, "CHINA", 2),
    (19, "ROMANIA", 3),
    (20, "SAUDI ARABIA", 4),
    (21, "VIETNAM", 2),
    (22, "RUSSIA", 3),
    (23, "UNITED KINGDOM", 3),
    (24, "UNITED STATES", 1),
]


def write_rows(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write("|".join(str(value) for value in row))
            handle.write("|\n")


def generate(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    output = args.output
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    write_rows(output / "region.tbl", [(key, name, "synthetic") for key, name in REGIONS])
    write_rows(
        output / "nation.tbl",
        [(key, name, region, "synthetic") for key, name, region in NATIONS],
    )

    asia_nations = [key for key, _, region in NATIONS if region == 2]
    all_nations = [key for key, _, _ in NATIONS]

    suppliers = []
    suppliers_by_nation: dict[int, list[int]] = {nation: [] for nation in all_nations}
    for suppkey in range(1, args.suppliers + 1):
        nation = all_nations[(suppkey * 7) % len(all_nations)]
        suppliers_by_nation[nation].append(suppkey)
        suppliers.append(
            (
                suppkey,
                f"Supplier#{suppkey:09d}",
                "address",
                nation,
                "phone",
                "0.00",
                "synthetic",
            )
        )
    write_rows(output / "supplier.tbl", suppliers)

    customers = []
    customer_nation: dict[int, int] = {}
    for custkey in range(1, args.customers + 1):
        if args.asia_heavy and custkey % 3 != 0:
            nation = asia_nations[custkey % len(asia_nations)]
        else:
            nation = all_nations[(custkey * 5) % len(all_nations)]
        customer_nation[custkey] = nation
        customers.append(
            (
                custkey,
                f"Customer#{custkey:09d}",
                "address",
                nation,
                "phone",
                "0.00",
                "BUILDING",
                "synthetic",
            )
        )
    write_rows(output / "customer.tbl", customers)

    orders = []
    order_customer: dict[int, int] = {}
    for orderkey in range(1, args.orders + 1):
        custkey = ((orderkey - 1) % args.customers) + 1
        order_customer[orderkey] = custkey
        if orderkey % 5 == 0:
            orderdate = "1993-12-15"
        elif orderkey % 7 == 0:
            orderdate = "1995-01-15"
        else:
            month = ((orderkey - 1) % 12) + 1
            day = ((orderkey - 1) % 28) + 1
            orderdate = f"1994-{month:02d}-{day:02d}"
        orders.append(
            (
                orderkey,
                custkey,
                "O",
                "0.00",
                orderdate,
                "5-LOW",
                "Clerk#000000001",
                0,
                "synthetic",
            )
        )
    write_rows(output / "orders.tbl", orders)

    lineitems = []
    for line_no in range(1, args.lineitems + 1):
        orderkey = ((line_no - 1) % args.orders) + 1
        custkey = order_customer[orderkey]
        nation = customer_nation[custkey]
        if line_no % 2 == 0 and suppliers_by_nation.get(nation):
            suppkey = rng.choice(suppliers_by_nation[nation])
        else:
            suppkey = ((line_no * 11) % args.suppliers) + 1

        price = 100 + (line_no % 1000)
        discount = (line_no % 10) / 100
        lineitems.append(
            (
                orderkey,
                ((line_no * 13) % max(args.lineitems, 1)) + 1,
                suppkey,
                ((line_no - 1) % 7) + 1,
                "1.00",
                f"{price}.00",
                f"{discount:.2f}",
                "0.00",
                "N",
                "O",
                "1994-01-02",
                "1994-01-03",
                "1994-01-04",
                "DELIVER IN PERSON",
                "AIR",
                "synthetic",
            )
        )
    write_rows(output / "lineitem.tbl", lineitems)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic TPC-H-like Q5 data")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--customers", type=int, default=1000)
    parser.add_argument("--orders", type=int, default=5000)
    parser.add_argument("--lineitems", type=int, default=20000)
    parser.add_argument("--suppliers", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--asia-heavy", action="store_true")
    args = parser.parse_args()

    generate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
