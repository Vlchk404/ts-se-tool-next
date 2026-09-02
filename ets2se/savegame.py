"""Loading a save, editing it, and writing it back.

Files are written out unencrypted and unwrapped: the game's SII loader accepts
`SiiNunit` and `BSII` payloads as readily as the `ScsC` ones it writes itself
(profile.sii, controls.sii and every def file in the game prove the point).
Everything the editor did not touch keeps its exact original bytes.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass

from . import bsii, crypt
from . import text as text_sii
from .model import Attr, SiiFile, Unit, float_to_text, text_to_float

BACKUP_DIR_NAME = "ets2se_backups"

WEAR_FIELDS = (
    "engine_wear", "transmission_wear", "cabin_wear", "chassis_wear",
    "trailer_body_wear",
)
UNFIXABLE_FIELDS = (
    "engine_wear_unfixable", "transmission_wear_unfixable",
    "cabin_wear_unfixable", "chassis_wear_unfixable",
    "trailer_body_wear_unfixable",
)
WEAR_ARRAYS = ("wheels_wear",)
UNFIXABLE_ARRAYS = ("wheels_wear_unfixable",)

SKILL_FIELDS = ("long_dist", "heavy", "fragile", "urgent", "mechanical")
GARAGE_SLOTS = {0: 0, 6: 1, 3: 5}

# `economy.default_training_policy` carries this table in the save itself;
# `driver_ai.training_policy` is a bare number, so the names live here too.
TRAINING_POLICIES = {
    "random": 0, "balanced": 1, "adr": 2, "long": 3,
    "valuable": 4, "fragile": 5, "jit": 6, "eco": 7,
}


class SaveError(Exception):
    pass


def adr_mask(levels: int) -> int:
    """ADR is stored as a bitmask of unlocked classes: 6 levels -> 63."""
    levels = max(0, min(6, int(levels)))
    return (1 << levels) - 1


def adr_levels(mask: int) -> int:
    return bin(int(mask) & 0x3F).count("1")


def referenced_names(f: SiiFile) -> set[str]:
    """Every value in the file that names another unit."""
    out: set[str] = set()
    for u in f.units:
        for a in u.attrs:
            if a.items is not None:
                out.update(v.strip() for v in a.items)
            elif a.value is not None:
                out.add(a.value.strip())
    out.discard("null")
    out.discard("")
    return out


def tree_roots(f: SiiFile) -> list[str]:
    """Units nothing points at.

    The game loads a save with `load_unit_tree()` and refuses a file that holds
    more than one tree ("There are multiple unit trees in the file" ->
    "Unable to create economy unit!"). Every real save has exactly one root, so
    an operation that drops a pointer without dropping the unit behind it makes
    the save unloadable.
    """
    seen = referenced_names(f)
    return [u.name for u in f.units if u.name not in seen]


@dataclass
class Document:
    """One SII file of a save."""

    path: str
    raw: bytes
    container: str          # 'encrypted' | 'plain' | '3nk'
    kind: str               # 'binary' | 'text'
    model: SiiFile
    payload: bytes

    @classmethod
    def load(cls, path: str) -> "Document":
        with open(path, "rb") as fh:
            raw = fh.read()
        payload, container = crypt.unwrap(raw)
        kind = crypt.payload_kind(payload)
        model = bsii.decode(payload) if kind == "binary" else text_sii.decode(payload)
        return cls(path=path, raw=raw, container=container, kind=kind,
                   model=model, payload=payload)

    def encode(self) -> bytes:
        return (bsii.encode(self.model) if self.kind == "binary"
                else text_sii.encode(self.model))

    @property
    def changed(self) -> bool:
        try:
            return self.encode() != self.payload
        except Exception:
            return True


class SaveGame:
    """A loaded `game.sii` + `info.sii` pair with the editing operations."""

    def __init__(self, save_dir: str):
        self.dir = save_dir
        self.log: list[str] = []
        game_path = os.path.join(save_dir, "game.sii")
        if not os.path.isfile(game_path):
            raise SaveError(f"нет файла {game_path}")
        self.game = Document.load(game_path)
        info_path = os.path.join(save_dir, "info.sii")
        self.info: Document | None = None
        if os.path.isfile(info_path):
            try:
                self.info = Document.load(info_path)
            except Exception:
                self.info = None
        self.f = self.game.model
        self.economy = self.f.first("economy")
        if self.economy is None:
            raise SaveError("в game.sii нет блока economy — это не сохранение игры")
        self.bank = self.f.follow(self.economy.value("bank"))
        self.player = self.f.follow(self.economy.value("player"))
        self.roots = set(tree_roots(self.f))

    # ------------------------------------------------------------------ info
    @property
    def save_version(self) -> int:
        if self.info is not None:
            u = self.info.model.first("save_container")
            if u is not None:
                return u.int_of("version")
        return 0

    @property
    def money(self) -> int:
        return self.bank.int_of("money_account") if self.bank else 0

    @property
    def experience(self) -> int:
        return self.economy.int_of("experience_points")

    @property
    def game_time(self) -> int:
        return self.economy.int_of("game_time")

    def note(self, text: str) -> None:
        self.log.append(text)

    # -------------------------------------------------------------- entities
    def trucks(self) -> list[Unit]:
        """Trucks the player owns (garage trucks included)."""
        out = []
        if self.player:
            for ref in self.player.items_of("trucks"):
                u = self.f.follow(ref)
                if u is not None:
                    out.append(u)
        return out

    def all_vehicles(self) -> list[Unit]:
        return self.f.by_class("vehicle")

    def all_trailers(self) -> list[Unit]:
        return self.f.by_class("trailer")

    def drivers(self) -> list[Unit]:
        return self.f.by_class("driver_ai")

    def garages(self) -> list[tuple[str, Unit]]:
        out = []
        for ref in self.economy.items_of("garages"):
            u = self.f.follow(ref)
            if u is not None:
                out.append((ref, u))
        return out

    def known_cities(self) -> list[str]:
        """Every city token the save itself mentions.

        Derived from the save rather than a bundled list, so modded and DLC maps
        work without an update.
        """
        cities: set[str] = set()
        for ref in self.economy.items_of("garages"):
            parts = ref.split(".")
            if len(parts) >= 2:
                cities.add(parts[-1])
        for ref in self.economy.items_of("companies"):
            parts = ref.split(".")
            if len(parts) >= 2:
                cities.add(parts[-1])
        cities.update(self.economy.items_of("visited_cities"))
        cities.discard("")
        cities.discard("null")
        return sorted(cities)

    def vehicle_brand(self, unit: Unit) -> str:
        """Truck/trailer model taken from its accessory data paths."""
        for ref in unit.items_of("accessories"):
            acc = self.f.follow(ref)
            if acc is None:
                continue
            path = acc.get("data_path")
            if path is None:
                continue
            text = path.as_str()
            if "/truck/" in text or "/trailer/" in text:
                part = text.split("/")
                for i, seg in enumerate(part):
                    if seg in ("truck", "trailer") and i + 1 < len(part):
                        return part[i + 1]
        return "?"

    def vehicle_label(self, unit: Unit) -> str:
        plate = unit.get("license_plate")
        plate_text = plate.as_str().split("|")[0] if plate else ""
        # license plates can carry font markup; keep only readable characters
        if "<" in plate_text:
            import re
            plate_text = re.sub(r"<[^>]*>", "", plate_text)
        return f"{self.vehicle_brand(unit)} [{plate_text or '—'}]"

    # ------------------------------------------------------------ operations
    def set_money(self, value: int) -> None:
        if self.bank is None:
            raise SaveError("в сохранении нет блока bank")
        value = max(0, int(value))
        self.bank.set("money_account", value)
        self.note(f"деньги: {value:,}".replace(",", " "))

    def add_money(self, delta: int) -> None:
        self.set_money(self.money + int(delta))

    def set_experience(self, value: int) -> None:
        value = max(0, min(int(value), 0xFFFFFFFF))
        self.economy.set("experience_points", value)
        self.note(f"опыт: {value:,}".replace(",", " "))

    def add_experience(self, delta: int) -> None:
        self.set_experience(self.experience + int(delta))

    def set_skills(self, adr: int | None = None, **levels: int) -> None:
        """`adr` is a class count (0-6); the rest are levels (0-6)."""
        if adr is not None:
            self.economy.set("adr", adr_mask(adr))
            self.note(f"ADR: {adr} классов")
        for name, value in levels.items():
            if name not in SKILL_FIELDS:
                raise SaveError(f"неизвестный навык {name}")
            value = max(0, min(6, int(value)))
            self.economy.set(name, value)
            self.note(f"{name}: {value}")

    def max_skills(self) -> None:
        self.set_skills(adr=6, **{k: 6 for k in SKILL_FIELDS})

    def set_game_time(self, minutes: int) -> None:
        minutes = max(0, int(minutes))
        self.economy.set("game_time", minutes)
        self.note(f"игровое время: {minutes} мин")

    def clear_loans(self) -> None:
        if self.bank is None:
            return
        loans = self.bank.get("loans")
        if loans is not None and loans.is_array and loans.items:
            n = len(loans.items)
            loans.items = []
            # the `bank_loan` units themselves have to go with the pointers,
            # or the save keeps them as a second unit tree and stops loading
            dropped = self.drop_orphans()
            self.note(f"кредиты списаны: {n}" +
                      (f" (удалено блоков: {dropped})" if dropped else ""))

    def drop_orphans(self) -> int:
        """Remove units nothing points at any more, and whatever hung off them.

        Only units orphaned by this session are touched: the roots the save
        already had when it was loaded stay put.
        """
        total = 0
        while True:
            seen = referenced_names(self.f)
            doomed = [u for u in self.f.units
                      if u.name not in seen and u.name not in self.roots]
            if not doomed:
                return total
            self.f.remove_many(doomed)
            total += len(doomed)

    def check_structure(self) -> None:
        """Refuse to write a save the game would not load."""
        extra = [n for n in tree_roots(self.f) if n not in self.roots]
        if extra:
            index = {u.name: u for u in self.f.units}
            shown = ", ".join(f"{index[n].cls}:{n}" for n in extra[:5])
            raise SaveError(
                f"внутренняя ошибка: {len(extra)} блок(ов) остались без ссылок "
                f"({shown}) — игра такое сохранение не загрузит, запись отменена"
            )

    def set_loan_limit(self, value: int) -> None:
        if self.bank is not None:
            self.bank.set("loan_limit", max(0, int(value)))
            self.note(f"лимит кредита: {value}")

    def _zero_wear(self, unit: Unit, unfixable: bool) -> int:
        touched = 0
        names = WEAR_FIELDS + (UNFIXABLE_FIELDS if unfixable else ())
        for name in names:
            a = unit.get(name)
            if a is not None and a.as_float() != 0.0:
                a.set_float(0.0)
                touched += 1
        arrays = WEAR_ARRAYS + (UNFIXABLE_ARRAYS if unfixable else ())
        for name in arrays:
            a = unit.get(name)
            if a is not None and a.is_array and any(
                text_to_float(v) != 0.0 for v in a.items
            ):
                a.items = [float_to_text(0.0)] * len(a.items)
                touched += 1
        return touched

    def repair_all(self, unfixable: bool = True, include_trailers: bool = True,
                   only: list[Unit] | None = None) -> int:
        units = only if only is not None else list(self.all_vehicles())
        if only is None and include_trailers:
            units += self.all_trailers()
        n = sum(1 for u in units if self._zero_wear(u, unfixable))
        self.note(f"отремонтировано объектов: {n}")
        return n

    def refuel_all(self, only: list[Unit] | None = None) -> int:
        units = only if only is not None else self.all_vehicles()
        n = 0
        for u in units:
            a = u.get("fuel_relative")
            if a is not None and a.as_float() < 1.0:
                a.set_float(1.0)
                n += 1
        self.note(f"заправлено машин: {n}")
        return n

    def fix_cargo(self) -> int:
        n = 0
        for u in self.all_trailers():
            a = u.get("cargo_damage")
            if a is not None and a.as_float() != 0.0:
                a.set_float(0.0)
                n += 1
        self.note(f"груз восстановлен: {n}")
        return n

    def set_odometer(self, unit: Unit, km: int) -> None:
        unit.set("odometer", max(0, int(km)))
        a = unit.get("odometer_float_part")
        if a is not None:
            a.set_float(0.0)

    def set_plate(self, unit: Unit, plate: str, country: str | None = None) -> None:
        current = unit.get("license_plate")
        if current is None:
            return
        old = current.as_str()
        suffix = old.split("|", 1)[1] if "|" in old else ""
        if country:
            suffix = country
        current.value = '"%s"' % (f"{plate}|{suffix}" if suffix else plate)

    def unlock_cities(self, cities: list[str] | None = None) -> int:
        cities = cities if cities is not None else self.known_cities()
        visited = self.economy.get("visited_cities")
        counts = self.economy.get("visited_cities_count")
        if visited is None or not visited.array_like:
            return 0
        have = visited.as_items()
        old = {c: 1 for c in have}
        if counts is not None and counts.array_like:
            for c, n in zip(have, counts.as_items()):
                try:
                    old[c] = max(1, int(n))
                except ValueError:
                    old[c] = 1
        merged = have + [c for c in cities if c not in old]
        visited.items = merged
        if counts is not None and counts.array_like:
            # the count array runs parallel to the city array
            counts.items = [str(old.get(c, 1)) for c in merged]
        added = len(merged) - len(old)
        self.note(f"открыто городов: +{added} (всего {len(merged)})")
        return added

    def unlock_dealers(self, cities: list[str] | None = None) -> int:
        return self._unlock_list("unlocked_dealers", cities, "автосалоны")

    def unlock_recruitments(self, cities: list[str] | None = None) -> int:
        return self._unlock_list("unlocked_recruitments", cities, "агентства найма")

    def _unlock_list(self, field: str, cities: list[str] | None, label: str) -> int:
        a = self.economy.get(field)
        if a is None or not a.array_like:
            return 0
        cities = cities if cities is not None else self.known_cities()
        have = a.as_items()
        seen = set(have)
        merged = have + [c for c in cities if c not in seen]
        added = len(merged) - len(seen)
        if added:
            a.items = merged
        self.note(f"{label}: +{added} (всего {len(merged)})")
        return added

    def set_garage_status(self, unit: Unit, status: int) -> None:
        """Statuses seen in real saves: 0 = не куплен, 6 = стартовый (1 место),
        3 = большой (5 мест). The slot arrays must match the status."""
        status = int(status)
        slots = GARAGE_SLOTS.get(status)
        unit.set("status", status)
        if slots is None:
            return
        for name in ("vehicles", "drivers"):
            a = unit.get(name)
            if a is None or not a.array_like:
                continue
            # occupied slots are never thrown away: dropping a pointer here
            # would leave the truck or the driver behind as a stray unit
            items = [v for v in a.as_items() if v != "null"]
            items += ["null"] * max(0, slots - len(items))
            a.items = items

    def buy_all_garages(self, status: int = 3) -> int:
        n = 0
        for _ref, u in self.garages():
            if u.int_of("status") != status:
                self.set_garage_status(u, status)
                n += 1
        self.note(f"гаражей изменено: {n}")
        return n

    def boost_drivers(self, level: int = 6, adr: int = 6,
                      policy: str | int | None = None) -> int:
        n = 0
        for d in self.drivers():
            d.set("adr", adr_mask(adr))
            for name in SKILL_FIELDS:
                d.set(name, max(0, min(6, level)))
            if policy is not None:
                self._set_policy(d.get("training_policy"), policy)
            n += 1
        self.note(f"навыки водителей подняты: {n}")
        return n

    @staticmethod
    def _set_policy(attr: Attr | None, policy: str | int) -> None:
        """Accepts a name (`eco`) or an index and writes whichever form the
        field uses: an ordinal field stores the name, a plain integer the index.
        """
        if attr is None:
            return
        ordinals = getattr(attr.field, "ordinals", None)
        if isinstance(policy, str):
            if policy not in TRAINING_POLICIES:
                raise SaveError(f"неизвестная политика обучения {policy}")
            name, index = policy, TRAINING_POLICIES[policy]
        else:
            index = int(policy)
            name = next((k for k, v in TRAINING_POLICIES.items() if v == index),
                        str(index))
        attr.value = name if ordinals else index

    def set_driver_policy(self, policy: str | int) -> None:
        self._set_policy(self.economy.get("default_training_policy"), policy)
        self.note(f"политика обучения по умолчанию: {policy}")

    def set_all_driver_policies(self, policy: str | int) -> int:
        """Only the policy, leaving the drivers' skills alone."""
        n = 0
        for d in self.drivers():
            self._set_policy(d.get("training_policy"), policy)
            n += 1
        self.note(f"политика обучения у водителей ({n}): {policy}")
        return n

    # ------------------------------------------------------------ info.sii
    def sync_info(self) -> None:
        """Mirror the headline numbers into info.sii so the save list matches."""
        if self.info is None:
            return
        u = self.info.model.first("save_container")
        if u is None:
            return
        u.set("info_money_account", self.money)
        u.set("info_players_experience", self.experience)
        u.set("time", self.game_time)
        for field, source in (
            ("info_visited_cities", "visited_cities"),
            ("info_unlocked_dealers", "unlocked_dealers"),
            ("info_unlocked_recruitments", "unlocked_recruitments"),
        ):
            a = self.economy.get(source)
            if a is not None and a.array_like:
                u.set(field, len(a.as_items()))

    def rename(self, label: str) -> None:
        if self.info is None:
            raise SaveError("нет info.sii — переименовать нельзя")
        u = self.info.model.first("save_container")
        if u is not None:
            u.set("name", '"%s"' % label.replace('"', ""))
            self.note(f"название: {label}")

    # ---------------------------------------------------------------- saving
    @property
    def changed(self) -> bool:
        return self.game.changed or (self.info is not None and self.info.changed)

    def backup(self) -> str:
        profile_dir = os.path.dirname(os.path.dirname(self.dir))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = os.path.join(profile_dir, BACKUP_DIR_NAME,
                              f"{os.path.basename(self.dir)}_{stamp}")
        os.makedirs(target, exist_ok=True)
        for name in os.listdir(self.dir):
            src = os.path.join(self.dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(target, name))
        return target

    def write(self, make_backup: bool = True) -> str:
        self.check_structure()
        backup_path = self.backup() if make_backup else ""
        self.sync_info()
        for doc in (self.game, self.info):
            if doc is None:
                continue
            data = doc.encode()
            if data == doc.payload and doc.container == "plain":
                continue
            tmp = doc.path + ".ets2se.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, doc.path)
            doc.raw = data
            doc.payload = data
            doc.container = "plain"
        return backup_path


def list_backups(profile_dir: str) -> list[tuple[str, str]]:
    root = os.path.join(profile_dir, BACKUP_DIR_NAME)
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root), reverse=True):
        path = os.path.join(root, name)
        if os.path.isdir(path):
            out.append((name, path))
    return out


def restore_backup(backup_path: str, save_dir: str) -> None:
    if not os.path.isdir(backup_path):
        raise SaveError(f"нет папки резервной копии {backup_path}")
    for name in os.listdir(backup_path):
        src = os.path.join(backup_path, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(save_dir, name))
