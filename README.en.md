# ETS2 / ATS Save Editor

Save editor for **Euro Truck Simulator 2** and **American Truck Simulator**,
game version **1.60.1.7 and newer**. GUI and command line, Python standard
library only, a backup before every write.

[Русская версия](README.md)

Why it exists: TS SE Tool 0.3.11.0 officially supports the game only up to 1.49
and corrupts 1.60 saves on write. This editor reads and writes the 1.60.1.7
format (`version: 97`) directly. ETS2 and ATS share the save format, so it works
with both games.

## Features

- **Money, experience, in-game time, save name.**
- **Driver skills:** ADR (0–6 classes) and the other five (`long_dist`, `heavy`,
  `fragile`, `urgent`, `mechanical`) — individually or all at once.
- **Loans:** pay off every loan, change the credit limit.
- **Vehicles:** full repair of all trucks and trailers including the
  "unfixable" wear, full tanks, cargo damage reset, odometer and licence plate
  of the selected truck.
- **World:** unlock all cities, truck dealers and recruitment agencies; buy and
  upgrade every garage to 5 slots; max out hired drivers' skills and set their
  training policy.
- **Raw tab:** any unit and any field of the save directly — whatever the ready
  made buttons do not cover, and headroom for future game versions.
- **Backups:** taken before every write, restored with one click.

## Requirements

- **Python 3.8 or newer.** Tick "Add python.exe to PATH" in the python.org
  installer. Nothing else to install — the package uses the standard library
  only. Saves are decrypted with Windows' own AES (CNG), so speed does not
  depend on any third-party package; an installed `cryptography` works too.
- **Euro Truck Simulator 2 or American Truck Simulator 1.60.1.7+.** Older saves
  open as well, but 1.60.1.7 is what this was tested against.
- **Windows 7 or newer** — the GUI targets Windows, tested on Windows 10 and 11,
  and display scaling (125%/150%) is handled. The command line runs anywhere the
  game's profile folders are visible.

## Install

Grab the archive with **Code → Download ZIP** and unpack it anywhere, or clone:

    git clone https://github.com/Vlchk404/ts-se-tool-next.git

## Run

Double-click **`Редактор ETS2.bat`** ("ETS2 editor") — the window opens. The
file only starts Python, it installs nothing.

The same from a terminal, in the project folder:

    python -m ets2se                 # editor window
    python -m ets2se list            # profiles and saves found
    python -m ets2se show "MyProfile/1"

## How to use it

1. **Quit the game**, or at least unload the profile. The game keeps the save in
   memory and will overwrite your edits on the next autosave.
2. Pick a save on the left, change the values, press **«Применить и сохранить»**
   (apply and save).
3. The whole save folder is copied to
   `<profile>/ets2se_backups/<save>_<date>` first. If anything goes wrong, use
   the **«Резервные копии»** (backups) tab to restore it.

## Command line

    python -m ets2se list
    python -m ets2se show "MyProfile/1"
    python -m ets2se edit "MyProfile/1" --money 5000000 --xp 900000 --all
    python -m ets2se edit "MyProfile/1" --money 5000000 --dry-run
    python -m ets2se backups MyProfile
    python -m ets2se restore <backup-folder> "MyProfile/1"
    python -m ets2se doctor

A save can be given as a full folder path, as `profile/save`, or by name alone
when it is unambiguous.

Main `edit` options:

| Option | Effect |
| --- | --- |
| `--money N`, `--add-money N` | set or add money |
| `--xp N`, `--add-xp N` | set or add experience |
| `--skills-max` | all driver skills to the maximum |
| `--adr N` | ADR classes, 0–6 |
| `--skill NAME=LEVEL` | one skill at a time, e.g. `--skill heavy=6` |
| `--clear-loans`, `--loan-limit N` | pay off loans, change the limit |
| `--repair`, `--keep-unfixable` | repair everything; the second keeps the "unfixable" wear |
| `--refuel`, `--fix-cargo` | full tanks, reset cargo damage |
| `--unlock-cities`, `--unlock-dealers`, `--unlock-recruitments` | unlock cities, dealers, recruitment agencies |
| `--garages [STATUS]` | all garages: `3` large, 5 slots (default), `6` starter, `0` not bought |
| `--drivers-max`, `--driver-policy NAME` | hired drivers' skills and training policy (`eco`, `long`, `adr`, …) |
| `--game-time MINUTES`, `--rename NAME` | in-game time, save name |
| `--all` | skills, repair, fuel, cargo, loans and every unlock at once |
| `--dry-run` | print what would change and write nothing |
| `--no-backup` | skip the backup (not recommended) |

Full list: `python -m ets2se edit --help`.

## Backups

Before every write the whole save folder is copied to
`<profile>/ets2se_backups/<save>_<date>`. Restore from the backups tab or:

    python -m ets2se backups MyProfile
    python -m ets2se restore <backup-folder> "MyProfile/1"

Backups are plain folders — nothing is uploaded anywhere and nothing is deleted;
copy or remove them by hand as you like.

## If the game folder is not found

The editor looks for `Documents`, its localised name and the OneDrive-redirected
variant, then for `Euro Truck Simulator 2` and `American Truck Simulator` inside,
then for `profiles` and `steam_profiles` (Steam Cloud profiles are marked `[S]`).
If your folder lives somewhere unusual, point at it:

    set ETS2SE_DOCUMENTS=D:\path\to\Documents
    python -m ets2se list

## Troubleshooting

Start here:

    python -m ets2se doctor

It prints the Python version and bitness, the Windows version, which AES backend
is in use, whether tkinter is present, and which game folders were found.

- **The window says "not responding" and closes with a Windows error report.**
  That is what happened before 1.2 on machines without `cryptography`: the save
  was decrypted in pure Python on the UI thread, and Windows declared the program
  hung. Decryption now goes through Windows' own AES and reading happens on a
  worker thread, so the window stays alive. If the status bar reads
  "AES: чистый Python", Windows CNG is unavailable for some reason — saves still
  open, just more slowly.
- **Double-clicking the launcher opens the Microsoft Store.** That is the Store's
  Python stub: it sits on `PATH` and runs nothing. Install Python from python.org;
  the launcher skips the stub when a real Python is present.
- **Blurry or tiny text** on a laptop at 125%/150% scaling — the editor declares
  itself DPI-aware, so nothing needs configuring.


## How it works

The game writes `game.sii` encrypted (`ScsC`). The editor reads those files but
writes **decrypted** ones: binary `BSII` stays binary, text `SiiNunit` stays
text. The game loads all three variants itself — the unencrypted `profile.sii`,
`controls.sii` and the game's own `def` files prove it.

Decryption uses whatever the machine has, fastest first: Windows' own AES (CNG,
`bcrypt.dll`, present since Vista and hardware-accelerated), then `cryptography`
if it is installed, and only as a last resort a pure-Python implementation. That
one is built on the four inverse T-tables and is ~57x faster than the textbook
form, but still far slower than the first two. The status bar and
`python -m ets2se doctor` both say which one is active.

Reading a save — decryption plus parsing a file that reaches 7 MB and 20 000
units — happens on a worker thread, so the window keeps answering and Windows
never declares it hung. Writing and the backup copy do the same.

Nothing else changes on write: every untouched field is written with the same
bytes it was read as. Over a corpus of 72 real saves, decode → encode reproduces
the original file byte for byte. The format is never converted between binary and
text, because the text form loses the sector packing of coordinates.

The game loads `game.sii` through `load_unit_tree()` and requires every block in
the file to be linked into a **single tree**. A block nothing points at is a
second root, and loading fails with `There are multiple unit trees in the file`
— in game, "an error occurred while loading the saved game". So the editor never
drops a pointer and leaves the block behind: clearing loans deletes the
`bank_loan` units too, and shrinking a garage keeps every occupied slot. The
structure is re-checked before each write; if stray blocks appeared anyway, the
write is cancelled with an error and the file is left untouched.

## Tests

    python -m unittest discover -s tests

The synthetic codec tests always run; the tests against real saves are skipped
when no game installation is found. Test edits are applied to a temporary copy —
game files are never touched.

## Not supported

Hiring new drivers, buying new vehicles and editing the jobs themselves. Those
are not single fields but a consistent set of units and parallel arrays, and a
mistake there corrupts the save silently. Existing drivers, vehicles and garages
are fully editable.

## Caution

Editing saves is not something the game's developers planned for. Copy your
profile folder before the first run, and after editing load the save and check
that everything is where you left it. The editor makes a backup on its own, but
what ends up in the save is your call.

This project is not affiliated with SCS Software. Euro Truck Simulator 2 and
American Truck Simulator are trademarks of SCS Software.

## Licence

[MIT](LICENSE) — use it, modify it, ship it, keep the copyright notice.
