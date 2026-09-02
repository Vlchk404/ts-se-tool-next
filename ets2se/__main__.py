"""Command line interface: `python -m ets2se ...`

    python -m ets2se list
    python -m ets2se show "MyProfile/1"
    python -m ets2se edit "MyProfile/1" --money 5000000 --xp 900000 --all
    python -m ets2se backups "MyProfile"
    python -m ets2se restore <папка_копии> <папка_сохранения>
"""

from __future__ import annotations

import argparse
import os
import sys

from . import game
from .savegame import SaveGame, SaveError, list_backups, restore_backup, SKILL_FIELDS


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def clock(minutes: int) -> str:
    days, rest = divmod(max(0, int(minutes)), 60 * 24)
    return "%d дн %02d:%02d" % (days, rest // 60, rest % 60)


def open_console() -> None:
    """A Windows console is usually cp866 or cp1251: degrade glyphs it cannot
    represent instead of dying on them."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass


def find_save(text: str) -> str:
    """Accept a folder path, `профиль/сохранение`, or just a save name."""
    if os.path.isdir(text) and os.path.isfile(os.path.join(text, "game.sii")):
        return text
    parts = [p for p in text.replace("\\", "/").split("/") if p]
    want_profile = parts[0].lower() if len(parts) > 1 else ""
    want_save = parts[-1].lower()
    hits: list[tuple[str, str]] = []
    for gdir in game.find_game_dirs():
        for prof in game.list_profiles(gdir.path):
            if want_profile and want_profile not in prof.name.lower():
                continue
            for slot in prof.saves:
                if want_save in (slot.dir_name.lower(), slot.label.lower()):
                    hits.append((f"{prof.name}/{slot.dir_name}", slot.path))
    if not hits:
        raise SaveError(f"сохранение {text!r} не найдено (см. `list`)")
    if len(hits) > 1:
        names = ", ".join(h[0] for h in hits[:8])
        raise SaveError(f"под {text!r} подходит несколько сохранений: {names}")
    return hits[0][1]


def find_profile(text: str) -> str:
    if os.path.isdir(os.path.join(text, "save")):
        return text
    for gdir in game.find_game_dirs():
        for prof in game.list_profiles(gdir.path, with_saves=False):
            if text.lower() in prof.name.lower():
                return prof.path
    raise SaveError(f"профиль {text!r} не найден")


def cmd_list(args) -> int:
    dirs = game.find_game_dirs()
    if not dirs:
        print("Папки игры не найдены. Укажите её через ETS2SE_DOCUMENTS=...")
        return 1
    for gdir in dirs:
        print(gdir.display_name)
        for prof in game.list_profiles(gdir.path):
            print("  %s  (%d сохр.)" % (prof.display_name, len(prof.saves)))
            for slot in prof.saves:
                note = f"  !! {slot.error}" if slot.error else ""
                print("    %-22s %-19s %13s  опыт %9s  %-11s %s%s"
                      % (slot.dir_name, slot.when, money(slot.money),
                         money(slot.experience), slot.kind,
                         slot.display_name if slot.label else "", note))
    return 0


def cmd_show(args) -> int:
    save = SaveGame(find_save(args.save))
    print("папка:      %s" % save.dir)
    print("формат:     %s / %s (версия сохранения %d)"
          % (save.game.container, save.game.kind, save.save_version))
    print("деньги:     %s" % money(save.money))
    print("опыт:       %s" % money(save.experience))
    print("время:      %s" % clock(save.game_time))
    from .savegame import adr_levels
    print("навыки:     ADR %d/6, %s" % (
        adr_levels(save.economy.int_of("adr")),
        ", ".join("%s %d" % (f, save.economy.int_of(f)) for f in SKILL_FIELDS)))
    if save.bank is not None:
        print("кредиты:    %d (лимит %s)" % (len(save.bank.items_of("loans")),
                                             money(save.bank.int_of("loan_limit"))))
    bought = sum(1 for _r, g in save.garages() if g.int_of("status") != 0)
    print("гаражи:     %d из %d куплено" % (bought, len(save.garages())))
    print("города:     %d открыто" % len(save.economy.items_of("visited_cities")))
    print("салоны:     %d, найм: %d"
          % (len(save.economy.items_of("unlocked_dealers")),
             len(save.economy.items_of("unlocked_recruitments"))))
    print("водители:   %d" % len(save.drivers()))
    print("техника:    %d машин, %d прицепов"
          % (len(save.all_vehicles()), len(save.all_trailers())))
    for unit in save.trucks():
        print("   грузовик  %-28s пробег %8d км  износ двиг. %.0f%%"
              % (save.vehicle_label(unit), unit.int_of("odometer"),
                 unit.float_of("engine_wear") * 100))
    return 0
def cmd_edit(args) -> int:
    save = SaveGame(find_save(args.save))
    if args.money is not None:
        save.set_money(args.money)
    if args.add_money:
        save.add_money(args.add_money)
    if args.xp is not None:
        save.set_experience(args.xp)
    if args.add_xp:
        save.add_experience(args.add_xp)
    if args.skills_max or args.all:
        save.max_skills()
    if args.adr is not None:
        save.set_skills(adr=args.adr)
    for pair in args.skill or ():
        name, _, value = pair.partition("=")
        save.set_skills(**{name.strip(): int(value or 0)})
    if args.clear_loans or args.all:
        save.clear_loans()
    if args.loan_limit is not None:
        save.set_loan_limit(args.loan_limit)
    if args.repair or args.all:
        save.repair_all(unfixable=not args.keep_unfixable)
    if args.refuel or args.all:
        save.refuel_all()
    if args.fix_cargo or args.all:
        save.fix_cargo()
    if args.unlock_cities or args.all:
        save.unlock_cities()
    if args.unlock_dealers or args.all:
        save.unlock_dealers()
    if args.unlock_recruitments or args.all:
        save.unlock_recruitments()
    if args.garages is not None:
        save.buy_all_garages(args.garages)
    if args.driver_policy:
        save.set_driver_policy(args.driver_policy)
    if args.drivers_max or args.all:
        save.boost_drivers(policy=args.driver_policy)
    elif args.driver_policy:
        save.set_all_driver_policies(args.driver_policy)
    if args.game_time is not None:
        save.set_game_time(args.game_time)
    if args.rename:
        save.rename(args.rename)
    for line in save.log:
        print("  " + line)
    if not save.log:
        print("Нечего менять — ни одна операция не задана.")
        return 1
    if args.dry_run:
        print("\n--dry-run: файлы не изменены.")
        return 0
    backup = save.write(make_backup=not args.no_backup)
    print("\nЗаписано: %s" % save.dir)
    if backup:
        print("Копия:    %s" % backup)
    return 0
def cmd_backups(args) -> int:
    root = find_profile(args.profile)
    rows = list_backups(root)
    if not rows:
        print("Копий нет.")
        return 0
    for name, path in rows:
        print("%-40s %s" % (name, path))
    return 0


def cmd_restore(args) -> int:
    target = find_save(args.save) if args.save else None
    if target is None:
        raise SaveError("укажите папку сохранения")
    restore_backup(args.backup, target)
    print("Восстановлено из %s в %s" % (args.backup, target))
    return 0


def cmd_gui(args) -> int:
    from .gui import main as gui_main
    return gui_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ets2se",
        description="Редактор сохранений Euro Truck Simulator 2 / "
                    "American Truck Simulator (1.60+)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="профили и сохранения").set_defaults(fn=cmd_list)
    sub.add_parser("gui", help="графическая оболочка").set_defaults(fn=cmd_gui)

    show = sub.add_parser("show", help="что внутри сохранения")
    show.add_argument("save")
    show.set_defaults(fn=cmd_show)

    b = sub.add_parser("backups", help="список резервных копий профиля")
    b.add_argument("profile")
    b.set_defaults(fn=cmd_backups)

    r = sub.add_parser("restore", help="вернуть сохранение из копии")
    r.add_argument("backup")
    r.add_argument("save")
    r.set_defaults(fn=cmd_restore)

    e = sub.add_parser("edit", help="изменить сохранение")
    e.add_argument("save")
    e.add_argument("--money", type=int, help="сколько денег стало")
    e.add_argument("--add-money", type=int, help="добавить денег")
    e.add_argument("--xp", type=int, help="сколько опыта стало")
    e.add_argument("--add-xp", type=int, help="добавить опыта")
    e.add_argument("--skills-max", action="store_true", help="все навыки на максимум")
    e.add_argument("--adr", type=int, help="классов ADR (0-6)")
    e.add_argument("--skill", action="append", metavar="ИМЯ=УРОВЕНЬ",
                   help="навык поштучно: " + ", ".join(SKILL_FIELDS))
    e.add_argument("--clear-loans", action="store_true", help="списать кредиты")
    e.add_argument("--loan-limit", type=int, help="лимит кредита")
    e.add_argument("--repair", action="store_true", help="починить всё")
    e.add_argument("--keep-unfixable", action="store_true",
                   help="не трогать неустранимый износ")
    e.add_argument("--refuel", action="store_true", help="полные баки")
    e.add_argument("--fix-cargo", action="store_true", help="восстановить груз")
    e.add_argument("--unlock-cities", action="store_true", help="открыть города")
    e.add_argument("--unlock-dealers", action="store_true", help="открыть салоны")
    e.add_argument("--unlock-recruitments", action="store_true",
                   help="открыть агентства найма")
    e.add_argument("--garages", type=int, nargs="?", const=3, metavar="СТАТУС",
                   help="все гаражи: 3 большой (5 мест), 6 стартовый, 0 не куплен")
    e.add_argument("--drivers-max", action="store_true", help="навыки водителей")
    e.add_argument("--driver-policy", metavar="ИМЯ",
                   help="политика обучения водителей (eco, long, adr, ...)")
    e.add_argument("--game-time", type=int, help="игровое время в минутах")
    e.add_argument("--rename", metavar="НАЗВАНИЕ", help="переименовать сохранение")
    e.add_argument("--all", action="store_true",
                   help="навыки, ремонт, топливо, груз, кредиты, все открытия")
    e.add_argument("--no-backup", action="store_true", help="без резервной копии")
    e.add_argument("--dry-run", action="store_true", help="только показать")
    e.set_defaults(fn=cmd_edit)
    return p


def main(argv: list[str] | None = None) -> int:
    open_console()
    args = build_parser().parse_args(argv)
    if not getattr(args, "fn", None):
        return cmd_gui(args)
    try:
        return args.fn(args)
    except SaveError as exc:
        print("Ошибка: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


