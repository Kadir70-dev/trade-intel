"""Rule-based requirement extractor for UAE computer-components / laptop chats.

One WhatsApp message usually carries several line items ("16GB PC4 Samsung",
"512 NVMe 80% health", "Dell 5420 i5-11th 50 pcs"). This module turns the raw
text into structured :class:`Item` rows plus a buy/sell side. Everything is
evidence-first: only what the text says is extracted; unknowns stay ``None``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime

CATEGORIES = ("RAM", "SSD", "HDD", "LAPTOP", "BATTERY", "PHONE", "ACCESSORY", "OTHER")

_BUY = re.compile(
    r"\b(w\.?t\.?b\.?|want\s*to\s*buy|wanna\s*buy|i\s*want\s*to\s*buy|need(?:ed)?|required?|"
    r"looking\s*for|buying|wanted|buy)\b",
    re.I,
)
_SELL = re.compile(
    r"\b(w\.?t\.?s\.?|want\s*to\s*sa+le?|want\s*to\s*sell|wana\s*sell|for\s*sale|selling|"
    r"ready\s*stock|in\s*stock|available|stock\s*available|offer)\b",
    re.I,
)
_NOISE = re.compile(
    r"^(ok|okay|thanks?|hi|hello|waiting for this message|this message was deleted|"
    r"you deleted this message|<media omitted>)\W*$",
    re.I,
)
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_PHONE = re.compile(r"(?:\+|00)?\d[\d\s\-]{8,}\d")

_BRANDS_RAM = {
    "samsung": "Samsung",
    "sk hynix": "SK Hynix",
    "skhynix": "SK Hynix",
    "sk-hynix": "SK Hynix",
    "hynix": "SK Hynix",
    "micron": "Micron",
    "kingston": "Kingston",
    "crucial": "Crucial",
    "adata": "ADATA",
    "corsair": "Corsair",
}
_LAPTOP_BRANDS = {
    "dell": "Dell",
    "hp": "HP",
    "lenovo": "Lenovo",
    "thinkpad": "Lenovo",
    "macbook": "Apple",
    "apple": "Apple",
    "asus": "ASUS",
    "acer": "Acer",
    "msi": "MSI",
    "microsoft": "Microsoft",
    "surface": "Microsoft",
}
_LAPTOP_WORDS = re.compile(
    r"\b(latitude|precision|inspiron|xps|elitebook|elite\s*book|probook|zbook|thinkpad|"
    r"macbook|ideapad|yoga|laptops?|notebook|chromebook|surface|x360|i[3579][\s\-]?\d{1,2}(?:th|\s*gen)?|"
    r"core\s*i[3579]|ryzen)\b",
    re.I,
)
_CAP = re.compile(r"(?<![\d.])(\d{1,4})\s*(gb|g\.?b|tb|g)\b", re.I)
_BARE_CAP = re.compile(r"(?<![\d.])(2|4|8|16|32|64|128|256|512|1024)(?![\d.])")
_SSD_CAPS = {64, 120, 128, 240, 250, 256, 480, 500, 512, 960, 1000, 1024, 2048}
_BARE_ANY = re.compile(r"(?<![\d.])(\d{2,4})(?![\d.%])")
_SPEED = re.compile(r"(?<!\d)(1333|1600|2133|2400|2666|2933|3200|3600|4800|5200|5600|6000)(?!\d)")
_GEN = re.compile(r"\b(?:pc|ddr)\s*([345])(l)?\b|\bpc([345])l?\b|\bddr([345])\b", re.I)
_QTY = re.compile(
    r"(?:(\d{1,5})\s*\+?\s*(?:pcs?|pieces?|units?|nos?|qty)\b)|"
    r"(?:(?:qty|quantity|available|avail)\s*[:\-]?\s*(\d{1,5})\b)",
    re.I,
)
_HEALTH = re.compile(
    r"(\d{2,3})\s*(?:to|-|\u2013)\s*(\d{2,3})\s*%?\s*(?:health|helath)?|"
    r"(\d{2,3})\s*(\+{1,2})\s*%?\s*(?:health|helath)?|"
    r"(?:health|helath)\s*(\d{2,3})\s*%?\s*(\+)?|"
    r"(\d{2,3})\s*%\s*\+?\s*(?:health|helath)?",
    re.I,
)
_PRICE = re.compile(r"(?:aed|dhs?|dirhams?)\s*[:\-]?\s*(\d{2,6})|(\d{2,6})\s*(?:aed|dhs?)\b", re.I)
_CPU = re.compile(r"\b(i[3579])[\s\-]*(\d{1,2})?(?:th|\s*gen)?\b|\bcore\s*(i[3579])\b|\bryzen\s*[3579]\b", re.I)
_MODEL = re.compile(
    r"\b((?:latitude|precision|elitebook|elite\s*book|probook|zbook|thinkpad|inspiron|xps)\s+(?:[a-z]+\s+)?[a-z]?\d{2,4}\w*(?:\s*g\d{1,2})?|"
    r"[ept]\d{2,3}[s]?|\d{3,4}[\s\-]*g\d{1,2}|[5-7]\d{3}|x1\s*\w+|x360\s*\d*|\d{3}\s*g\d)\b",
    re.I,
)
_SSD_WORDS = re.compile(r"\b(ssd|nvme|nvne|nvmem|sata|2\.5|mini|long|medium|pulled|health)\b", re.I)
_RAM_WORDS = re.compile(r"\b(ram|rams|ddr[345]|pc[345]l?|sodimm|so-dimm|dimm|hynix|skhynix|samsung\s*\d*\s*chip)\b", re.I)
_HDD_WORDS = re.compile(r"\b(hdd|hard\s*disk|hard\s*drive|sata\s*hdd|1tb\s*hdd)\b", re.I)
_BATT_WORDS = re.compile(r"\b(batter(?:y|ies)|cell)\b", re.I)
_PHONE_WORDS = re.compile(
    r"\b(iphone|galaxy|samsung\s*(?:a|s|m|f)\d{2}\w*|redmi|xiaomi|oppo|vivo|realme|pixel|"
    r"a\d{2}\s*5g|\d/\d{2,3}\s*gb)\b",
    re.I,
)
_ACC_WORDS = re.compile(
    r"\b(charger|chargers|adapter|adaptor|cable|mouse|keyboard|bag|dock|hub|monitor|"
    r"headphone|earbud|accessor(?:y|ies)|screen|panel|fan|screen\s*guard)\b",
    re.I,
)


@dataclass(slots=True)
class Item:
    category: str
    raw_line: str
    brand: str | None = None
    model: str | None = None
    capacity_gb: int | None = None
    generation: str | None = None  # DDR3/DDR4/DDR5 (RAM)
    speed_mhz: int | None = None
    form: str | None = None  # laptop/desktop (RAM) | long/mini/medium (SSD) | 2.5 | m.2
    interface: str | None = None  # NVMe / SATA
    health_min: int | None = None
    health_max: int | None = None
    condition: str | None = None  # pulled / new / used / original
    cpu: str | None = None
    ram_gb: int | None = None
    ssd_gb: int | None = None
    qty: int | None = None
    price_aed: float | None = None
    flags: list[str] = field(default_factory=list)
    posted_at: datetime | None = None

    def key(self) -> str:
        """Stable identity for de-duplicating reposts of the same requirement."""
        parts = [
            self.category,
            self.brand or "",
            self.model or "",
            str(self.capacity_gb or ""),
            self.generation or "",
            str(self.speed_mhz or ""),
            self.form or "",
            self.interface or "",
            str(self.health_min or ""),
            self.cpu or "",
        ]
        return "|".join(parts).lower()

    def title(self) -> str:
        bits: list[str | None] = []
        if self.category == "RAM":
            bits += [self.brand, _fmt_cap(self.capacity_gb),
                     self.generation, f"{self.speed_mhz}" if self.speed_mhz else None,
                     self.form, "RAM"]
        elif self.category in ("SSD", "HDD"):
            bits += [self.brand, _fmt_cap(self.capacity_gb),
                     self.interface, self.form, self.category]
        elif self.category == "LAPTOP":
            bits += [self.brand, self.model, self.cpu,
                     f"{self.ram_gb}GB" if self.ram_gb else None,
                     f"{self.ssd_gb}GB SSD" if self.ssd_gb else None]
        else:
            bits += [self.brand, self.model, _fmt_cap(self.capacity_gb) if self.category == "PHONE" else None, self.category.title()]
        return " ".join(str(b) for b in bits if b) or self.category.title()


def _fmt_cap(gb: int | None) -> str | None:
    if not gb:
        return None
    return f"{gb // 1024}TB" if gb >= 1024 and gb % 1024 == 0 else f"{gb}GB"


def normalize(text: str) -> str:
    """NFKC (folds math-bold letters), drops emoji/markdown, collapses spaces."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _URL.sub(" ", t)
    t = t.replace("*", " ").replace("_", " ").replace("~", " ")
    t = "".join(ch if (ch.isalnum() or ch in " \n+%./-:,&()|@#=") else " " for ch in t)
    t = re.sub(r"\bm\.?\s?[12]\b", " nvmem ", t, flags=re.I)  # "M2 256" is not 2GB
    t = re.sub(r"(?i)(\d)(nvme|nvne|ssd|speed|pcs?|units?|qty|health|helath|chips?)\b", r"\1 \2", t)
    t = re.sub(r"(?i)(\d)(to)(?=\d)", r"\1 to ", t)
    t = re.sub(r"(?i)\b(\d{1,2})\s*(gb|g)(pc|ddr)", r"\1 \2 \3", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def detect_side(text: str) -> str | None:
    """'buy' | 'sell' | None by the earliest explicit trading keyword."""
    t = normalize(text)
    b, s = _BUY.search(t), _SELL.search(t)
    if b and s:
        return "buy" if b.start() <= s.start() else "sell"
    if b:
        return "buy"
    if s:
        return "sell"
    return None


def _gen(line: str) -> str | None:
    m = _GEN.search(line)
    if not m:
        return None
    n = m.group(1) or m.group(3) or m.group(4)
    lite = bool(m.group(2)) or f"pc{n}l" in line.lower()
    return f"DDR{n}" + ("L" if lite else "")


def _caps(line: str) -> list[int]:
    out: list[int] = []
    for m in _CAP.finditer(line):
        n = int(m.group(1))
        if m.group(2).lower().startswith("t"):
            n *= 1024
        out.append(n)
    return out


def _qty(line: str) -> int | None:
    m = _QTY.search(line)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _health(line: str) -> tuple[int | None, int | None]:
    m = _HEALTH.search(line)
    if not m:
        return None, None
    g = m.groups()
    if g[0] and g[1]:
        return int(g[0]), int(g[1])
    if g[2]:
        return int(g[2]), 100
    if g[4]:
        return int(g[4]), 100
    if g[6]:
        return int(g[6]), 100
    return None, None


def _brand(line: str, table: dict[str, str]) -> str | None:
    low = line.lower()
    for k, v in table.items():
        if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", low):
            return v
    return None


def has_ram_only_words(low: str) -> bool:
    return bool(re.search(r"\b(ddr[345]|pc[345]l?|nvme|nvmem|hdd)\b", low))


def _classify_line(line: str, ctx: str | None) -> str | None:
    low = line.lower()
    if _BATT_WORDS.search(low):
        return "BATTERY"
    if _HDD_WORDS.search(low):
        return "HDD"
    has_ram = bool(_RAM_WORDS.search(low))
    has_ssd = bool(_SSD_WORDS.search(low)) and not re.search(r"\b(ram|ddr|pc[345])\b", low)
    if _MODEL.search(line) and _brand(low, _LAPTOP_BRANDS) and not has_ram_only_words(low):
        return "LAPTOP"
    cpu = bool(_CPU.search(low)) or bool(_LAPTOP_WORDS.search(low) and re.search(r"\b(dell|hp|lenovo|macbook|thinkpad|latitude|elitebook|probook|zbook)\b", low))
    if cpu and not (has_ram and not re.search(r"\b(i[3579]|latitude|elitebook|probook|zbook|thinkpad)\b", low)):
        return "LAPTOP"
    if has_ram and not has_ssd:
        return "RAM"
    if has_ssd:
        return "SSD"
    if _PHONE_WORDS.search(low) and not re.search(r"\bdell|hp|lenovo\b", low):
        return "PHONE"
    if _ACC_WORDS.search(low):
        return "ACCESSORY"
    if _LAPTOP_WORDS.search(low):
        return "LAPTOP"
    if ctx in ("RAM", "SSD", "HDD") and (_caps(low) or _GEN.search(low) or _SPEED.search(low) or _BARE_CAP.search(low)):
        return ctx
    return None


def _build(line: str, cat: str, ctx_form: str | None) -> Item:
    low = line.lower()
    it = Item(category=cat, raw_line=line.strip())
    it.qty = _qty(line)
    pm = _PRICE.search(line)
    if pm:
        it.price_aed = float(pm.group(1) or pm.group(2))
    caps = _caps(low)
    if cat == "RAM":
        it.generation = _gen(low)
        sp = _SPEED.search(low)
        it.speed_mhz = int(sp.group(1)) if sp else None
        found = []
        for k, v in _BRANDS_RAM.items():
            if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", low) and v not in found:
                found.append(v)
        it.brand = "/".join(found) if found else None
        it.capacity_gb = caps[0] if caps else None
        if it.capacity_gb is None:
            m = re.search(r"(?<![\d.])(\d{1,2})\s*(?:gb?)?\s*(?:pc|ddr)", low)
            if m:
                it.capacity_gb = int(m.group(1))
        if it.capacity_gb is None:
            m = re.search(r"(?:pc|ddr)\s*[345]l?\s*/?\s*(\d{1,2})\s*(?:gb|g)\b", low)
            if m:
                it.capacity_gb = int(m.group(1))
        it.form = "Desktop" if re.search(r"desktop|dimm(?!.*so)|\bdt\b", low) and "laptop" not in low else (
            "Laptop" if re.search(r"laptop|sodimm|so-dimm|\blp\b|notebook", low) else ctx_form
        )
        if "laptop" in low and "desktop" in low:
            it.form = "Laptop/Desktop"
        if re.search(r"\bmix\b", low):
            it.flags.append("mixed")
        ch = re.search(r"(\d{1,2})\s*chips?", low)
        if ch:
            it.flags.append(f"{ch.group(1)}-chip")
    elif cat in ("SSD", "HDD"):
        it.capacity_gb = caps[0] if caps else None
        if it.capacity_gb is None:
            for m in _BARE_ANY.finditer(low):
                if int(m.group(1)) in _SSD_CAPS:
                    it.capacity_gb = int(m.group(1))
                    break
        if re.search(r"nvme|nvne|nvmem", low):
            it.interface = "NVMe"
        elif re.search(r"sata|2\.5", low):
            it.interface = "SATA"
        fm = re.search(r"\b(long|mini|medium|short|slim|fat|double cut|single cut)\b", low)
        it.form = (fm.group(1).title() if fm else None) or ("2.5\"" if "2.5" in low else None)
        it.health_min, it.health_max = _health(low)
        it.brand = _brand(low, {"samsung": "Samsung", "kingston": "Kingston", "wd": "WD", "western digital": "WD", "seagate": "Seagate", "crucial": "Crucial", "sandisk": "SanDisk", "toshiba": "Toshiba", "hynix": "SK Hynix", "micron": "Micron"})
    elif cat == "LAPTOP":
        it.brand = _brand(low, _LAPTOP_BRANDS)
        mm = _MODEL.search(line)
        it.model = mm.group(1).strip().title() if mm else None
        cm = _CPU.search(low)
        if cm:
            fam = (cm.group(1) or cm.group(3) or "").lower()
            gen = cm.group(2)
            if fam and not gen:
                bg = re.search(r"\bi[3579]\s+(\d{1,2})\b(?!\s*(?:pcs?|units?|qty))", low)
                gen = bg.group(1) if bg and 6 <= int(bg.group(1)) <= 14 else None
            it.cpu = f"{fam}{'-' + gen + 'th' if gen else ''}" if fam else "ryzen"
        rm = re.search(r"(\d{1,2})\s*gb\s*ram|ram\s*(\d{1,2})\s*gb|\b(\d{1,2})\s*/\s*(\d{3,4})\b|\b(\d{1,2})\s*g\b", low)
        if rm:
            it.ram_gb = int(next(g for g in (rm.group(1), rm.group(2), rm.group(3), rm.group(5)) if g))
        if it.ram_gb is None and it.ssd_gb is None and len(caps) >= 1:
            small = [c for c in caps if c <= 64]
            big = [c for c in caps if c >= 128]
            it.ram_gb = small[0] if small else None
            it.ssd_gb = big[0] if big else None
        sm = re.search(r"(\d{3,4})\s*gb\s*ssd|ssd\s*(\d{3,4})|/\s*(\d{3,4})\s*(?:gb)?|\b(\d{3,4})\s*ssd", low)
        if sm:
            it.ssd_gb = int(next(g for g in sm.groups() if g))
    elif cat == "BATTERY":
        it.brand = _brand(low, _LAPTOP_BRANDS)
    elif cat == "PHONE":
        it.brand = "Samsung" if "samsung" in low or re.search(r"\ba\d{2}\b", low) else ("Apple" if "iphone" in low else None)
        mm = re.search(r"\b([a-z]\d{2}[a-z]?\s*(?:5g)?|iphone\s*\d+\s*(?:pro\s*max|pro|plus)?)\b", low)
        it.model = mm.group(1).title() if mm else None
        it.capacity_gb = caps[-1] if caps else None
    else:
        it.brand = _brand(low, _LAPTOP_BRANDS)
    if re.search(r"pulled|pull out|pullout|refurb|used", low):
        it.condition = "Pulled/Used"
    elif re.search(r"\bnew\b|brand new|sealed|fresh", low):
        it.condition = "New"
    elif re.search(r"original|orignal", low):
        it.condition = "Original"
    return it


def _merge_laptop(base: Item, line: str) -> None:
    """Attach CPU / RAM / SSD / qty details from a continuation line to ``base``."""
    low = line.lower()
    extra = _build(line, "LAPTOP", None)
    base.cpu = base.cpu or extra.cpu
    if base.cpu and re.fullmatch(r"i[3579]", base.cpu):
        gm = re.search(r"\b(\d{1,2})\s*(?:th|gen)", low)
        if gm:
            base.cpu = f"{base.cpu}-{gm.group(1)}th"
    elif base.cpu is None:
        gm = re.search(r"\b(\d{1,2})\s*(?:th|gen)", low)
        if gm:
            base.cpu = f"{gm.group(1)}th gen"
    base.ram_gb = base.ram_gb or extra.ram_gb
    base.ssd_gb = base.ssd_gb or extra.ssd_gb
    base.qty = base.qty or extra.qty
    base.model = base.model or extra.model
    base.brand = base.brand or extra.brand
    base.raw_line = (base.raw_line + " | " + line.strip())[:400]


def extract_items(text: str) -> tuple[str | None, list[Item]]:
    """Return ``(side, items)`` for one message. Empty/noise messages → ``(None, [])``."""
    raw = normalize(text)
    if not raw or _NOISE.match(raw.strip()):
        return None, []
    side = detect_side(raw)
    items: list[Item] = []
    ctx: str | None = None
    ctx_form: str | None = None
    ctx_brand: str | None = None
    open_laptop: Item | None = None
    lines = [ln.strip(" -•:➡👉▫🌼🍁💫🚩") for ln in raw.split("\n")]
    for line in lines:
        line = line.strip()
        if not line:
            open_laptop = None  # blank line ends a laptop spec block
            continue
        if _PHONE.fullmatch(line):
            continue
        low = line.lower()
        if re.search(r"\blaptop\s*(ram|rams)\b", low):
            ctx, ctx_form = "RAM", "Laptop"
        elif re.search(r"\bdesktop\s*(ram|rams)\b", low):
            ctx, ctx_form = "RAM", "Desktop"
        elif re.fullmatch(r"(laptop |pulled )?ssd|laptop ssd", low):
            ctx = "SSD"
        elif re.fullmatch(r"ram|rams|ram ddr[345]|rams laptop pc4 ?& ?pc5", low):
            ctx = "RAM"
        has_model = bool(_MODEL.search(line))
        if (
            open_laptop is not None
            and not has_model
            and (_CPU.search(low) or re.search(r"\bram\b|\bssd\b|\d{1,2}\s*/\s*\d{3,4}|\bgen\b|\d+\s*th\b|\d+\s*gb", low) or _qty(line))
        ):
            _merge_laptop(open_laptop, line)
            continue
        cat = _classify_line(line, ctx)
        # Laptop context: bare model lines ("P50 30pcs", "T480 100pcs").
        if cat is None and ctx == "LAPTOP" and (has_model or open_laptop is not None):
            cat = "LAPTOP"
        if cat is None:
            # Speed/brand-only continuation of the previous RAM item ("4800 speed samsung").
            if items and items[-1].category == "RAM" and (_SPEED.search(low) or _brand(low, _BRANDS_RAM)):
                prev = items[-1]
                sp = _SPEED.search(low)
                if sp and not prev.speed_mhz:
                    prev.speed_mhz = int(sp.group(1))
                b = _brand(low, _BRANDS_RAM)
                if b and not prev.brand:
                    prev.brand = b
            continue
        if cat == "LAPTOP":
            ctx = "LAPTOP"
            b = _brand(low, _LAPTOP_BRANDS)
            if b:
                ctx_brand = b
            only_detail = bool(_CPU.search(low) or re.search(r"\d{1,2}\s*/\s*\d{3,4}|\bram\b|\bssd\b|\bgen\b|\d+\s*th\b", low))
            if open_laptop is not None and not has_model and (only_detail or _qty(line)):
                _merge_laptop(open_laptop, line)
                continue
            if not has_model and not only_detail and not _qty(line):
                # Header such as "WTB laptop lenovo ...": set context, no item.
                continue
            it = _build(line, "LAPTOP", None)
            it.brand = it.brand or ctx_brand
            items.append(it)
            open_laptop = it
            continue
        open_laptop = None
        has_detail = bool(
            _caps(low) or _GEN.search(low) or _SPEED.search(low)
            or _qty(line) or cat == "BATTERY"
            or (cat == "ACCESSORY" and not re.fullmatch(r"(want to \w+ )?accessor(y|ies)\W*", low))
        )
        if cat == "PHONE" and not (_caps(low) or has_model or _qty(line)):
            has_detail = False
        ssd_bare = cat in ("SSD", "HDD") and any(
            int(m.group(1)) in _SSD_CAPS for m in _BARE_ANY.finditer(low)
        )
        if cat in ("RAM", "SSD", "HDD") and not (
            _caps(low) or _GEN.search(low) or _qty(line) or _SPEED.search(low) or ssd_bare
        ):
            has_detail = False
        if ssd_bare:
            has_detail = True
        if not has_detail:
            continue
        # Speed-only RAM line ("( 2400 / 2666 / 3200 )") continues the previous item.
        if cat == "RAM" and not _caps(low) and not _GEN.search(low) and items and items[-1].category == "RAM":
            sp = _SPEED.search(low)
            if sp and not items[-1].speed_mhz:
                items[-1].speed_mhz = int(sp.group(1))
            continue
        if cat in ("RAM", "SSD", "HDD"):
            ctx = cat
        built = _build(line, cat, ctx_form if cat == "RAM" else None)
        caps_all = sorted({c for c in _caps(low) if c in (2, 4, 8, 16, 32, 64, 128)})
        if cat == "RAM" and len(caps_all) > 1:
            for c in caps_all:  # "16GB & 32GB RAM PC5" = two requirements
                items.append(replace(built, capacity_gb=c))
        else:
            items.append(built)
    # Header-only messages like "Need 16gb / Samsung": merge fragments into one RAM item.
    if not items:
        joined = " ".join(ln for ln in lines if ln)
        jl = joined.lower()
        if _caps(jl) and _brand(jl, _BRANDS_RAM) and not re.search(r"\bssd|nvme|hdd\b", jl):
            items.append(_build(joined, "RAM", None))
        elif re.search(r"(?<![\d.])(\d{2,4})\s*(?:gb|g)?\s*(?:nvme|nvmem|ssd)", jl):
            items.append(_build(joined, "SSD", None))
    items = [
        i for i in items
        if not (i.category in ("RAM", "SSD", "HDD") and not i.capacity_gb and not i.qty and not i.speed_mhz)
    ]
    seen: set[str] = set()
    uniq: list[Item] = []
    for it in items:
        k = it.key() + "|" + str(it.qty)
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return side, uniq
