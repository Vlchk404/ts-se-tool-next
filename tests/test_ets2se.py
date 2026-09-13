"""Tests: `python -m unittest discover -s tests`

The codec tests run on synthetic payloads, so they work anywhere. The corpus
tests walk the saves installed on this machine and are skipped when there are
none; they only ever read them, and edits are applied to a temporary copy.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ets2se import bsii, crypt, game, savegame, tokens  # noqa: E402
from ets2se import text as text_sii  # noqa: E402
from ets2se.model import float_to_text, text_to_float  # noqa: E402

TEXT_SAMPLE = b"""SiiNunit
{
economy : _nameless.4f0.b120 {
 experience_points: 1234
 adr: 7
 visited_cities: 2
 visited_cities[0]: berlin
 visited_cities[1]: praha
 visited_cities_count: 2
 visited_cities_count[0]: 3
 visited_cities_count[1]: 1
 unlocked_dealers: 0
 bank: bank.x
}

bank : bank.x {
 money_account: 5000
 loan_limit: 500000
 loans: 0
}

}
"""


def read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def corpus() -> list[str]:
    out = []
    for gdir in game.find_game_dirs():
        for prof in game.list_profiles(gdir.path):
            out += [slot.path for slot in prof.saves]
    return out


class Tokens(unittest.TestCase):
    def test_round_trip(self):
        # 12 characters is the format's limit: longer names are stored as strings
        for word in ("berlin", "a", "money_accou", "zzzzzzzzzzz", "_nameless"):
            self.assertEqual(tokens.token_to_str(tokens.str_to_token(word)), word)

    def test_too_long_is_rejected(self):
        with self.assertRaises(ValueError):
            tokens.str_to_token("money_account")

    def test_empty(self):
        self.assertEqual(tokens.token_to_str(0), "")


class Floats(unittest.TestCase):
    def test_round_trip(self):
        for value in (0.0, 1.0, -0.5, 1234.5678, 3.4028234663852886e38):
            self.assertAlmostEqual(text_to_float(float_to_text(value)), value,
                                   places=3)

    def test_game_notation(self):
        self.assertEqual(float_to_text(1.0), "&3f800000")
        self.assertEqual(text_to_float("&3f800000"), 1.0)
        self.assertEqual(text_to_float("2.5"), 2.5)


class Skills(unittest.TestCase):
    def test_adr_mask(self):
        self.assertEqual(savegame.adr_mask(6), 63)
        self.assertEqual(savegame.adr_mask(0), 0)
        self.assertEqual(savegame.adr_mask(99), 63)
        self.assertEqual(savegame.adr_levels(63), 6)
        self.assertEqual(savegame.adr_levels(7), 3)


class TextCodec(unittest.TestCase):
    def test_byte_identical(self):
        f = text_sii.decode(TEXT_SAMPLE)
        self.assertEqual(text_sii.encode(f), TEXT_SAMPLE)

    def test_edit_only_touches_its_own_lines(self):
        f = text_sii.decode(TEXT_SAMPLE)
        f.first("bank").set("money_account", 99)
        out = text_sii.encode(f).decode()
        self.assertIn("money_account: 99", out)
        self.assertIn(" loan_limit: 500000", out)
        self.assertIn(" visited_cities[1]: praha", out)

    def test_empty_array_can_be_filled(self):
        f = text_sii.decode(TEXT_SAMPLE)
        attr = f.first("economy").get("unlocked_dealers")
        self.assertTrue(attr.array_like)
        attr.items = ["berlin", "praha"]
        out = text_sii.encode(f).decode()
        self.assertIn("unlocked_dealers: 2", out)
        self.assertIn("unlocked_dealers[1]: praha", out)


class Aes(unittest.TestCase):
    # NIST SP 800-38A, F.2.6 CBC-AES256.Decrypt - so the fallback is checked
    # even on a machine where `cryptography` is not installed.
    KEY = bytes.fromhex("603deb1015ca71be2b73aef0857d7781"
                        "1f352c073b6108d72d9810a30914dff4")
    IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    CIPHER = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd6")
    PLAIN = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")

    def test_fallback_matches_known_vector(self):
        from ets2se.aes_fallback import aes_cbc_decrypt
        self.assertEqual(aes_cbc_decrypt(self.KEY, self.IV, self.CIPHER),
                         self.PLAIN)

    def test_windows_backend_matches_known_vector(self):
        from ets2se import wincrypt
        if not wincrypt.available():
            self.skipTest("Windows CNG недоступен")
        self.assertEqual(wincrypt.aes_cbc_decrypt(self.KEY, self.IV, self.CIPHER),
                         self.PLAIN)

    def test_fallback_matches_library(self):
        try:
            from cryptography.hazmat.primitives.ciphers import (Cipher,
                                                                algorithms, modes)
        except ImportError:
            self.skipTest("cryptography не установлена")
        from ets2se.aes_fallback import aes_cbc_decrypt
        key = bytes(range(32))
        iv = bytes(range(16))
        data = bytes((i * 7) & 0xFF for i in range(64))
        want = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        self.assertEqual(aes_cbc_decrypt(key, iv, data),
                         want.update(data) + want.finalize())

    def test_backends_agree_on_a_long_message(self):
        """Every available backend must produce the same bytes: the editor
        picks whichever is fastest at run time."""
        import os as _os

        from ets2se import crypt, wincrypt
        from ets2se.aes_fallback import aes_cbc_decrypt
        key, iv = _os.urandom(32), _os.urandom(16)
        data = _os.urandom(16 * 200)
        outs = {"python": aes_cbc_decrypt(key, iv, data)}
        if wincrypt.available():
            outs["windows-cng"] = wincrypt.aes_cbc_decrypt(key, iv, data)
        try:
            from cryptography.hazmat.primitives.ciphers import (Cipher,
                                                                algorithms, modes)
            d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            outs["cryptography"] = d.update(data) + d.finalize()
        except ImportError:
            pass
        self.assertEqual(len(set(outs.values())), 1,
                         "бэкенды разошлись: %s" % ", ".join(sorted(outs)))
        self.assertIn(crypt.backend_name(), outs)

    def test_real_saves_decrypt_the_same_on_every_backend(self):
        from ets2se import crypt, wincrypt
        from ets2se.aes_fallback import aes_cbc_decrypt
        if not wincrypt.available():
            self.skipTest("Windows CNG недоступен")
        checked = 0
        for path in corpus():
            raw = read(os.path.join(path, "game.sii"))
            if raw[:4] != b"ScsC":
                continue
            iv = raw[36:52]
            body = raw[crypt.HEADER_SIZE:]
            body = body[:len(body) - len(body) % 16]
            if not body or len(body) > 300000:   # keep the slow one bearable
                continue
            self.assertEqual(wincrypt.aes_cbc_decrypt(crypt._KEY, iv, body),
                             aes_cbc_decrypt(crypt._KEY, iv, body), path)
            checked += 1
            if checked >= 5:
                break
        if not checked:
            self.skipTest("нет зашифрованных сохранений подходящего размера")


class Corpus(unittest.TestCase):
    """Every save on this machine, as the format's own test vectors."""

    @classmethod
    def setUpClass(cls):
        cls.saves = corpus()
        if not cls.saves:
            raise unittest.SkipTest("на этой машине нет сохранений игры")

    def test_every_save_parses_and_round_trips(self):
        for path in self.saves:
            for name in ("game.sii", "info.sii"):
                full = os.path.join(path, name)
                if not os.path.isfile(full):
                    continue
                with self.subTest(file=full):
                    payload, _c = crypt.unwrap(read(full))
                    if crypt.payload_kind(payload) == "binary":
                        self.assertEqual(bsii.encode(bsii.decode(payload)),
                                         payload)
                    else:
                        self.assertEqual(
                            text_sii.encode(text_sii.decode(payload)), payload)

    def test_summary_fields_are_readable(self):
        for path in self.saves:
            with self.subTest(save=path):
                s = savegame.SaveGame(path)
                self.assertIsNotNone(s.economy)
                self.assertGreaterEqual(s.experience, 0)
                self.assertFalse(s.changed, "чистое сохранение считается изменённым")


class Editing(unittest.TestCase):
    """The whole edit-write-reload cycle, on a copy of one real save."""

    @classmethod
    def setUpClass(cls):
        saves = corpus()
        if not saves:
            raise unittest.SkipTest("на этой машине нет сохранений игры")
        cls.source = saves[0]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ets2se_test_")
        self.work = os.path.join(self.tmp, "profile", "save", "1")
        shutil.copytree(self.source, self.work)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_money_and_experience_survive_a_write(self):
        s = savegame.SaveGame(self.work)
        s.set_money(1_234_567)
        s.set_experience(89_000)
        s.write(make_backup=False)
        again = savegame.SaveGame(self.work)
        self.assertEqual(again.money, 1_234_567)
        self.assertEqual(again.experience, 89_000)
        if again.info is not None:
            u = again.info.model.first("save_container")
            self.assertEqual(u.int_of("info_money_account"), 1_234_567)

    def test_backup_then_restore_brings_the_save_back(self):
        before = read(os.path.join(self.work, "game.sii"))
        s = savegame.SaveGame(self.work)
        s.set_money(42)
        backup = s.write()
        self.assertTrue(os.path.isdir(backup))
        self.assertEqual(savegame.SaveGame(self.work).money, 42)
        savegame.restore_backup(backup, self.work)
        self.assertEqual(read(os.path.join(self.work, "game.sii")), before)

    def test_repair_refuel_and_unlocks(self):
        s = savegame.SaveGame(self.work)
        s.repair_all()
        s.refuel_all()
        s.unlock_cities()
        s.buy_all_garages()
        s.write(make_backup=False)
        again = savegame.SaveGame(self.work)
        for unit in again.all_vehicles():
            self.assertEqual(unit.float_of("engine_wear"), 0.0)
            self.assertEqual(unit.float_of("fuel_relative"), 1.0)
        cities = again.economy.get("visited_cities")
        counts = again.economy.get("visited_cities_count")
        self.assertEqual(len(cities.as_items()), len(counts.as_items()))
        for _ref, g in again.garages():
            self.assertEqual(g.int_of("status"), 3)
            self.assertEqual(len(g.items_of("vehicles")), 5)

    def test_writing_twice_is_stable(self):
        s = savegame.SaveGame(self.work)
        s.set_money(5)
        s.write(make_backup=False)
        first = read(os.path.join(self.work, "game.sii"))
        again = savegame.SaveGame(self.work)
        again.write(make_backup=False)
        self.assertEqual(read(os.path.join(self.work, "game.sii")), first)


class UnitTrees(unittest.TestCase):
    """A save must stay a single unit tree.

    The game loads game.sii with `load_unit_tree()`; a file with a stray unit
    fails with "There are multiple unit trees in the file" and the career will
    not start. Clearing a pointer array without removing the units behind it
    used to do exactly that.
    """

    @classmethod
    def setUpClass(cls):
        cls.saves = corpus()
        if not cls.saves:
            raise unittest.SkipTest("на этой машине нет сохранений игры")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ets2se_trees_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def copy(self, source: str) -> str:
        work = os.path.join(self.tmp, "save-%d" % len(os.listdir(self.tmp)))
        shutil.copytree(source, work)
        return work

    def test_every_save_has_exactly_one_root(self):
        for path in self.saves:
            with self.subTest(save=path):
                s = savegame.SaveGame(path)
                self.assertEqual(len(savegame.tree_roots(s.f)), 1)

    def test_clearing_a_loan_removes_the_loan_unit(self):
        for path in self.saves:
            s = savegame.SaveGame(path)
            if s.bank is not None and s.bank.items_of("loans"):
                break
        else:
            self.skipTest("ни в одном сохранении нет активного кредита")
        work = self.copy(path)
        s = savegame.SaveGame(work)
        loans = len(s.bank.items_of("loans"))
        units = len(s.f.units)
        s.clear_loans()
        s.write(make_backup=False)
        again = savegame.SaveGame(work)
        self.assertEqual(again.bank.items_of("loans"), [])
        self.assertEqual(len(again.f.units), units - loans)
        self.assertEqual(len(savegame.tree_roots(again.f)), 1)
        self.assertEqual(again.f.by_class("bank_loan"), [])

    def test_every_operation_keeps_one_root(self):
        work = self.copy(self.saves[0])
        s = savegame.SaveGame(work)
        s.set_money(10_000_000)
        s.set_experience(500_000)
        s.max_skills()
        s.repair_all()
        s.refuel_all()
        s.fix_cargo()
        s.clear_loans()
        s.unlock_cities()
        s.unlock_dealers()
        s.unlock_recruitments()
        s.buy_all_garages()
        s.boost_drivers()
        s.write(make_backup=False)
        self.assertEqual(len(savegame.tree_roots(savegame.SaveGame(work).f)), 1)

    def test_shrinking_a_garage_keeps_its_vehicles(self):
        for path in self.saves:
            s = savegame.SaveGame(path)
            hit = [u for _r, u in s.garages()
                   if [v for v in u.items_of("vehicles") if v != "null"]]
            if hit:
                break
        else:
            self.skipTest("ни в одном сохранении нет гаража с машиной")
        parked = [v for v in hit[0].items_of("vehicles") if v != "null"]
        s.set_garage_status(hit[0], 6)          # 1 slot, several vehicles in it
        kept = [v for v in hit[0].items_of("vehicles") if v != "null"]
        self.assertEqual(kept, parked)
        s.check_structure()

    def test_write_refuses_a_save_with_a_stray_unit(self):
        work = self.copy(self.saves[0])
        s = savegame.SaveGame(work)
        before = read(os.path.join(work, "game.sii"))
        pointer = next(a for a in s.economy.attrs
                       if a.items is None and (a.value or "").startswith(
                           ("_nameless.", "bank.", "player.")))
        pointer.value = "null"
        with self.assertRaises(savegame.SaveError):
            s.write(make_backup=False)
        self.assertEqual(read(os.path.join(work, "game.sii")), before)


if __name__ == "__main__":
    unittest.main()


