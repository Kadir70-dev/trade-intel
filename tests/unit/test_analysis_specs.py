"""Requirement extractor + matcher on realistic UAE components-group messages."""

from __future__ import annotations

from datetime import datetime

from trade_intel.analysis.matching import score_pair
from trade_intel.analysis.specs import detect_side, extract_items

NOW = datetime(2026, 11, 23)


def _one(text: str):
    side, items = extract_items(text)
    return side, items


def test_unicode_bold_and_typo_sale_is_seller() -> None:
    side, items = _one("‼️𝐖𝐚𝐧𝐭 𝐭𝐨 𝐬𝐚𝐥𝐞 ‼️\n8𝐆𝐁 𝐏𝐂4, 𝙡𝙖𝙥𝙩𝙤𝙥\n𝐌𝐢𝐱 𝐬𝐩𝐞𝐞𝐝")
    assert side == "sell"
    assert items[0].category == "RAM" and items[0].capacity_gb == 8
    assert items[0].generation == "DDR4" and items[0].form == "Laptop"


def test_want_to_buy_vs_want_to_sell() -> None:
    assert detect_side("Want to buy 16gb samsung") == "buy"
    assert detect_side("WTB laptop") == "buy"
    assert detect_side("Want to sale\n256 nvme") == "sell"
    assert detect_side("W.T.S new stock") == "sell"
    assert detect_side("Ok") is None


def test_ram_lines_speed_and_multi_capacity() -> None:
    side, items = _one(
        "WANT TO BUY\n16GB & 32GB RAM PC5 SKHYNIX 4800 AND 5600\n16GB RAM PC4 3200AA ONLY SAMSUNG"
    )
    assert side == "buy"
    caps = sorted((i.capacity_gb, i.generation, i.brand) for i in items)
    assert (16, "DDR4", "Samsung") in caps
    assert (32, "DDR5", "SK Hynix") in caps and (16, "DDR5", "SK Hynix") in caps


def test_ssd_bare_capacity_health_and_m2_not_2gb() -> None:
    _, items = _one("Want to sale\nM2 256 50to69\nM2 256 80to100")
    assert [(i.capacity_gb, i.interface, i.health_min, i.health_max) for i in items] == [
        (256, "NVMe", 50, 69),
        (256, "NVMe", 80, 100),
    ]


def test_laptop_block_merges_continuation_lines() -> None:
    _, items = _one(
        "WTS\nHP ZBOOK FIREFLY 16 G9\nCORE i7\n12TH GEN\n16GB RAM/512GB SSD\nAvailable 18 PCS"
    )
    assert len(items) == 1
    it = items[0]
    assert it.category == "LAPTOP" and it.brand == "HP" and it.qty == 18
    assert (it.cpu, it.ram_gb, it.ssd_gb) == ("i7-12th", 16, 512)


def test_laptop_model_list_with_quantities() -> None:
    side, items = _one("WTB laptop lenovo a big quantities\n\nP50 30pcs\nP51 30pcs")
    assert side == "buy"
    assert [(i.model, i.qty, i.brand) for i in items] == [("P50", 30, "Lenovo"), ("P51", 30, "Lenovo")]


def test_noise_and_headers_produce_no_items() -> None:
    assert extract_items("This message was deleted") == (None, [])
    assert extract_items("Waiting for this message")[1] == []
    assert extract_items("💻WANT TO BUY ACCESSORIES💻")[1] == []


def test_match_ram_requires_capacity_and_generation() -> None:
    _, buy = _one("Need 16gb pc4 samsung")
    _, sell = _one("For sale\n16gb pc4 3200 samsung 40pcs")
    _, wrong_gen = _one("For sale\n16gb pc5 4800")
    _, wrong_cap = _one("For sale\n8gb pc4")
    m = score_pair(buy[0], sell[0], now=NOW)
    assert m is not None and m.grade in ("A+", "A") and any("Samsung" in r for r in m.reasons)
    assert score_pair(buy[0], wrong_gen[0], now=NOW) is None
    assert score_pair(buy[0], wrong_cap[0], now=NOW) is None


def test_match_ssd_health_and_interface() -> None:
    _, buy = _one("WTB 512 nvme 80+ health")
    _, ok = _one("WTS 512gb nvme 80 to 100 health")
    _, low = _one("WTS 512gb nvme 40 to 60 health")
    _, sata = _one("WTS 512gb ssd 2.5 sata")
    assert score_pair(buy[0], ok[0], now=NOW) is not None
    assert score_pair(buy[0], low[0], now=NOW) is None
    assert score_pair(buy[0], sata[0], now=NOW) is None
