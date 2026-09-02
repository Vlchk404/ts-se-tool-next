"""Finding the game folders, profiles and saves on disk."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from . import crypt
from . import text as text_sii

ETS2_DIR_NAMES = ("Euro Truck Simulator 2",)
ATS_DIR_NAMES = ("American Truck Simulator",)


def _documents_dirs() -> list[str]:
    """Candidate Documents folders, including a OneDrive-redirected one."""
    out = []
    home = os.path.expanduser("~")
    env = os.environ
    for base in (
        env.get("ETS2SE_DOCUMENTS"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Документы"),
        os.path.join(env.get("OneDrive", ""), "Documents") if env.get("OneDrive") else None,
        os.path.join(env.get("OneDrive", ""), "Документы") if env.get("OneDrive") else None,
        os.path.join(env.get("USERPROFILE", ""), "Documents") if env.get("USERPROFILE") else None,
    ):
        if base and os.path.isdir(base) and base not in out:
            out.append(base)
    return out


def decode_profile_name(dir_name: str) -> str:
    """Profile folders are named with the hex of the UTF-8 profile name."""
    name = dir_name
    if len(name) % 2 == 0 and name and all(c in "0123456789ABCDEFabcdef" for c in name):
        try:
            return bytes.fromhex(name).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
    return name


def encode_profile_name(name: str) -> str:
    return name.encode("utf-8").hex().upper()


@dataclass
class SaveSlot:
    path: str
    dir_name: str
    label: str = ""
    file_time: int = 0
    money: int = 0
    experience: int = 0
    game_time: int = 0
    version: int = 0
    kind: str = ""
    error: str = ""

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        return {
            "autosave": "автосохранение",
            "quicksave": "быстрое сохранение",
        }.get(self.dir_name, self.dir_name)

    @property
    def when(self) -> str:
        if not self.file_time:
            return ""
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(self.file_time))

    @property
    def game_file(self) -> str:
        return os.path.join(self.path, "game.sii")


@dataclass
class Profile:
    path: str
    dir_name: str
    name: str
    steam_cloud: bool = False
    saves: list[SaveSlot] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"[S] {self.name}" if self.steam_cloud else self.name


@dataclass
class GameDir:
    path: str
    game: str  # 'ETS2' | 'ATS'

    @property
    def display_name(self) -> str:
        return f"{self.game}: {self.path}"


def read_save_slot(save_dir: str) -> SaveSlot:
    slot = SaveSlot(path=save_dir, dir_name=os.path.basename(save_dir.rstrip("\\/")))
    info = os.path.join(save_dir, "info.sii")
    try:
        with open(info, "rb") as fh:
            payload, _ = crypt.unwrap(fh.read())
        if crypt.payload_kind(payload) != "text":
            slot.error = "info.sii в двоичном виде"
            return slot
        unit = text_sii.decode(payload).first("save_container")
        if unit is None:
            slot.error = "нет блока save_container"
            return slot
        a = unit.get("name")
        slot.label = a.as_str() if a else ""
        slot.file_time = unit.int_of("file_time")
        slot.money = unit.int_of("info_money_account")
        slot.experience = unit.int_of("info_players_experience")
        slot.game_time = unit.int_of("time")
        slot.version = unit.int_of("version")
    except FileNotFoundError:
        slot.error = "нет info.sii"
    except Exception as exc:  # a damaged save must not hide the rest
        slot.error = f"{type(exc).__name__}: {exc}"
    if os.path.isfile(slot.game_file):
        try:
            with open(slot.game_file, "rb") as fh:
                slot.kind = {b"ScsC": "зашифровано", b"BSII": "двоичный",
                             b"SiiN": "текст"}.get(fh.read(4), "?")
        except OSError:
            pass
    if not slot.file_time:
        try:
            slot.file_time = int(os.path.getmtime(slot.game_file))
        except OSError:
            pass
    return slot


def list_saves(profile_dir: str) -> list[SaveSlot]:
    root = os.path.join(profile_dir, "save")
    if not os.path.isdir(root):
        return []
    slots = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "game.sii")):
            slots.append(read_save_slot(path))
    slots.sort(key=lambda s: s.file_time, reverse=True)
    return slots


def list_profiles(game_dir: str, with_saves: bool = True) -> list[Profile]:
    out = []
    for sub, cloud in (("profiles", False), ("steam_profiles", True)):
        root = os.path.join(game_dir, sub)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            prof = Profile(path=path, dir_name=name,
                           name=decode_profile_name(name), steam_cloud=cloud)
            if with_saves:
                prof.saves = list_saves(path)
            out.append(prof)
    out.sort(key=lambda p: (p.steam_cloud, p.name.lower()))
    return out


def find_game_dirs() -> list[GameDir]:
    out = []
    for docs in _documents_dirs():
        for names, game in ((ETS2_DIR_NAMES, "ETS2"), (ATS_DIR_NAMES, "ATS")):
            for name in names:
                path = os.path.join(docs, name)
                if os.path.isdir(os.path.join(path, "profiles")) or os.path.isdir(
                    os.path.join(path, "steam_profiles")
                ):
                    out.append(GameDir(path=path, game=game))
    return out
