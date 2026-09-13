"""ETS2/ATS save editor for game 1.60.1.7 and newer.

    from ets2se import SaveGame
    s = SaveGame(r"C:\\...\\profiles\\<hex>\\save\\1")
    s.set_money(5_000_000)
    s.max_skills()
    s.write()            # backs the folder up first

Nothing here needs a third-party package; `cryptography` is used when present
only to speed up reading the game's encrypted saves.
"""

from .game import (GameDir, Profile, SaveSlot, decode_profile_name,
                   find_game_dirs, list_profiles, list_saves, read_save_slot)
from .model import Attr, SiiFile, Unit
from .savegame import (SaveError, SaveGame, adr_levels, adr_mask, list_backups,
                       restore_backup, tree_roots)

__version__ = "1.2"
__all__ = [
    "SaveGame", "SaveError", "SaveSlot", "Profile", "GameDir",
    "SiiFile", "Unit", "Attr",
    "find_game_dirs", "list_profiles", "list_saves", "read_save_slot",
    "decode_profile_name", "list_backups", "restore_backup",
    "adr_mask", "adr_levels", "tree_roots", "__version__",
]
