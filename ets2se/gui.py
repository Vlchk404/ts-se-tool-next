"""Tkinter interface: profiles on the left, the editing tabs on the right.

Nothing is written until «Применить и сохранить» is pressed, and that always
copies the whole save folder into `<профиль>/ets2se_backups/` first unless the
backup box is unchecked.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import game
from .savegame import (SKILL_FIELDS, TRAINING_POLICIES, SaveError, SaveGame,
                       adr_levels, list_backups, restore_backup)

SKILL_LABELS = {
    "long_dist": "Дальние рейсы",
    "heavy": "Тяжёлые грузы",
    "fragile": "Хрупкие грузы",
    "urgent": "Срочная доставка",
    "mechanical": "Эко-вождение",
}
GARAGE_CHOICES = (
    ("не трогать", None),
    ("большой — 5 мест", 3),
    ("стартовый — 1 место", 6),
    ("не куплен", 0),
)
POLICY_CHOICES = ("не трогать",) + tuple(TRAINING_POLICIES)


def fmt(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def spin(parent, var, to=6, width=4):
    return tk.Spinbox(parent, from_=0, to=to, width=width, textvariable=var,
                      justify="right")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Редактор сохранений ETS2 / ATS — 1.60.1.7 и выше")
        self.geometry("1080x680")
        self.minsize(900, 560)
        self.save: SaveGame | None = None
        self.slots: dict[str, object] = {}
        self.trucks: dict[str, object] = {}
        self.unit_map: dict[str, object] = {}
        self.attr_map: dict[str, object] = {}
        self.backup_paths: list[str] = []
        self.loaded: dict = {}
        self.profile_dir = ""
        self._loading = False
        self._writing = False
        scale_fonts(self)
        self._build()
        self.refresh_tree()
    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=6)

        left = ttk.Frame(pane)
        pane.add(left, weight=1)
        self.tree = ttk.Treeview(left, columns=("when", "money"), height=20)
        self.tree.heading("#0", text="Профиль / сохранение")
        self.tree.heading("when", text="Когда")
        self.tree.heading("money", text="Деньги")
        self.tree.column("#0", width=250)
        self.tree.column("when", width=115, anchor="center")
        self.tree.column("money", width=110, anchor="e")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_pick)
        ttk.Button(left, text="Обновить список", command=self.refresh_tree)\
            .pack(fill="x", pady=(4, 0))

        right = ttk.Frame(pane)
        pane.add(right, weight=2)
        self.head = ttk.Label(right, text="Выберите сохранение слева",
                              font=("", 10, "bold"))
        self.head.pack(anchor="w")
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True, pady=4)
        self._tab_main()
        self._tab_fleet()
        self._tab_world()
        self._tab_raw()
        self._tab_backups()

        bar = ttk.Frame(right)
        bar.pack(fill="x")
        self.backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="сделать резервную копию",
                        variable=self.backup_var).pack(side="left")
        ttk.Button(bar, text="Применить и сохранить", command=self.apply)\
            .pack(side="right")
        ttk.Button(bar, text="Перечитать", command=self.reload)\
            .pack(side="right", padx=4)
        self.log = tk.Text(right, height=8, wrap="word")
        self.log.pack(fill="both", expand=False, pady=(6, 0))
        self.log.configure(state="disabled")

        strip = ttk.Frame(self)
        strip.pack(fill="x", padx=6, pady=(0, 4))
        self.status = ttk.Label(strip, text=self._idle_status(),
                                foreground="#555")
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(strip, mode="indeterminate", length=160)
        # packed only while something is running

    @staticmethod
    def _idle_status() -> str:
        from . import crypt

        names = {
            "windows-cng": "AES: Windows (быстро)",
            "cryptography": "AES: cryptography (быстро)",
            "python": "AES: чистый Python — большие сохранения читаются долго",
        }
        return "Готов. " + names.get(crypt.backend_name(), "")

    def _tab_main(self) -> None:
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text="Основное")
        self.v_money = tk.StringVar()
        self.v_xp = tk.StringVar()
        self.v_time = tk.StringVar()
        self.v_name = tk.StringVar()
        self.v_limit = tk.StringVar()
        self.v_loans = tk.BooleanVar()
        rows = (
            ("Деньги", self.v_money),
            ("Опыт", self.v_xp),
            ("Игровое время, минут", self.v_time),
            ("Название сохранения", self.v_name),
            ("Лимит кредита", self.v_limit),
        )
        for i, (label, var) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(f, textvariable=var, width=24)\
                .grid(row=i, column=1, sticky="w", pady=2)
        ttk.Checkbutton(f, text="списать все кредиты", variable=self.v_loans)\
            .grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(6, 0))

        box = ttk.Labelframe(f, text="Навыки водителя (0-6)", padding=8)
        box.grid(row=0, column=2, rowspan=8, padx=(24, 0), sticky="n")
        self.v_adr = tk.StringVar()
        ttk.Label(box, text="ADR, классов").grid(row=0, column=0, sticky="w")
        spin(box, self.v_adr).grid(row=0, column=1, padx=4)
        self.v_skills = {}
        for i, name in enumerate(SKILL_FIELDS, start=1):
            self.v_skills[name] = tk.StringVar()
            ttk.Label(box, text=SKILL_LABELS[name]).grid(row=i, column=0, sticky="w")
            spin(box, self.v_skills[name]).grid(row=i, column=1, padx=4)
        ttk.Button(box, text="всё на максимум", command=self.fill_max_skills)\
            .grid(row=len(SKILL_FIELDS) + 1, column=0, columnspan=2, pady=(6, 0))
        ttk.Label(f, text="Пустое поле или прежнее значение — операция не "
                          "выполняется.", foreground="#555")\
            .grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w", pady=(10, 0))
    def _tab_fleet(self) -> None:
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text="Техника")
        self.v_repair = tk.BooleanVar(value=True)
        self.v_unfix = tk.BooleanVar(value=True)
        self.v_refuel = tk.BooleanVar(value=True)
        self.v_cargo = tk.BooleanVar(value=True)
        for i, (text, var) in enumerate((
            ("починить все машины и прицепы", self.v_repair),
            ("в том числе неустранимый износ", self.v_unfix),
            ("полные баки", self.v_refuel),
            ("восстановить груз в прицепах", self.v_cargo),
        )):
            ttk.Checkbutton(f, text=text, variable=var)\
                .grid(row=i, column=0, sticky="w", padx=(20 if i == 1 else 0, 0))

        box = ttk.Labelframe(f, text="Грузовики игрока", padding=6)
        box.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        f.rowconfigure(5, weight=1)
        f.columnconfigure(0, weight=1)
        self.fleet = ttk.Treeview(box, columns=("odo", "wear"), height=8,
                                  show="tree headings")
        self.fleet.heading("#0", text="Грузовик")
        self.fleet.heading("odo", text="Пробег, км")
        self.fleet.heading("wear", text="Износ")
        self.fleet.column("odo", width=110, anchor="e")
        self.fleet.column("wear", width=80, anchor="center")
        self.fleet.pack(fill="both", expand=True)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        self.v_odo = tk.StringVar()
        self.v_plate = tk.StringVar()
        ttk.Label(row, text="пробег").pack(side="left")
        ttk.Entry(row, textvariable=self.v_odo, width=10).pack(side="left", padx=4)
        ttk.Label(row, text="номер").pack(side="left")
        ttk.Entry(row, textvariable=self.v_plate, width=12).pack(side="left", padx=4)
        ttk.Button(row, text="применить к выбранному",
                   command=self.apply_truck).pack(side="left", padx=4)
    def _tab_world(self) -> None:
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text="Карта и водители")
        self.v_cities = tk.BooleanVar(value=True)
        self.v_dealers = tk.BooleanVar(value=True)
        self.v_recruit = tk.BooleanVar(value=True)
        self.v_drivers = tk.BooleanVar(value=True)
        for i, (text, var) in enumerate((
            ("открыть все города, которые знает сохранение", self.v_cities),
            ("открыть все автосалоны", self.v_dealers),
            ("открыть все агентства найма", self.v_recruit),
            ("поднять навыки всех водителей до максимума", self.v_drivers),
        )):
            ttk.Checkbutton(f, text=text, variable=var)\
                .grid(row=i, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Гаражи").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.v_garage = tk.StringVar(value=GARAGE_CHOICES[0][0])
        ttk.Combobox(f, textvariable=self.v_garage, state="readonly", width=22,
                     values=[c[0] for c in GARAGE_CHOICES])\
            .grid(row=5, column=1, sticky="w", pady=(10, 0))
        ttk.Label(f, text="Обучение водителей")\
            .grid(row=6, column=0, sticky="w", pady=2)
        self.v_policy = tk.StringVar(value=POLICY_CHOICES[0])
        ttk.Combobox(f, textvariable=self.v_policy, state="readonly", width=22,
                     values=list(POLICY_CHOICES))\
            .grid(row=6, column=1, sticky="w", pady=2)
        self.world_info = ttk.Label(f, text="", foreground="#555", justify="left")
        self.world_info.grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _tab_backups(self) -> None:
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text="Резервные копии")
        self.backups = tk.Listbox(f, height=14)
        self.backups.pack(fill="both", expand=True)
        ttk.Button(f, text="Восстановить выбранную копию в это сохранение",
                   command=self.do_restore).pack(fill="x", pady=(6, 0))
    def _tab_raw(self) -> None:
        """Straight access to units and attributes, for anything the tabs above
        do not cover (and for save formats newer than this build)."""
        f = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(f, text="Всё подряд")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.v_filter = tk.StringVar()
        ttk.Label(top, text="Фильтр по классу или имени").pack(side="left")
        e = ttk.Entry(top, textvariable=self.v_filter, width=30)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.fill_raw())
        ttk.Button(top, text="Искать", command=self.fill_raw).pack(side="left")

        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.units = ttk.Treeview(body, columns=(), height=16)
        self.units.heading("#0", text="Юнит")
        self.units.pack(side="left", fill="both", expand=True)
        self.units.bind("<<TreeviewSelect>>", self.on_unit)
        self.attrs = ttk.Treeview(body, columns=("value",), height=16)
        self.attrs.heading("#0", text="Поле")
        self.attrs.heading("value", text="Значение (двойной щелчок — правка)")
        self.attrs.column("#0", width=200)
        self.attrs.column("value", width=320)
        self.attrs.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.attrs.bind("<Double-1>", self.edit_attr)
    # ----------------------------------------------------------------- state
    def note(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.slots.clear()
        dirs = game.find_game_dirs()
        if not dirs:
            self.note("Папки игры не найдены. Задайте переменную окружения "
                      "ETS2SE_DOCUMENTS с путём к папке Documents.")
            return
        for gdir in dirs:
            root = self.tree.insert("", "end", text=gdir.display_name, open=True)
            for prof in game.list_profiles(gdir.path):
                node = self.tree.insert(root, "end", open=len(prof.saves) <= 8,
                                        text="%s (%d)" % (prof.display_name,
                                                          len(prof.saves)))
                for slot in prof.saves:
                    iid = self.tree.insert(
                        node, "end", text=slot.display_name,
                        values=(slot.when, fmt(slot.money)))
                    self.slots[iid] = (prof, slot)

    def on_pick(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel or sel[0] not in self.slots:
            return
        prof, slot = self.slots[sel[0]]
        self.open_save(prof.path, slot.path)

    # Reading a save means AES-decrypting and parsing a file that reaches ~7 MB
    # and 20 000 units. On the main thread that stops Tk answering Windows, and
    # after a few seconds Windows paints the window over, calls it "not
    # responding" and offers to close it — which looks exactly like a crash.
    # So the work happens on a worker thread and the window stays alive.
    def open_save(self, profile_dir: str, save_dir: str) -> None:
        if self._loading or self._writing:
            # A click during a long read would otherwise look like nothing
            # happened at all.
            self.note("Подождите: предыдущая операция ещё идёт.")
            return
        self._loading = True
        self.profile_dir = profile_dir
        self.head.configure(text="Читаю %s ..." % os.path.basename(save_dir))
        self.busy_start("Читаю сохранение — это может занять несколько секунд")
        result: queue.Queue = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                result.put(("ok", SaveGame(save_dir)))
            except BaseException as exc:  # noqa: BLE001 - reported in the UI
                result.put(("err", exc))

        threading.Thread(target=work, daemon=True,
                         name="ets2se-load").start()
        self.after(50, self._poll_load, result, save_dir)

    def _poll_load(self, result: queue.Queue, save_dir: str) -> None:
        try:
            status, payload = result.get_nowait()
        except queue.Empty:
            self.after(50, self._poll_load, result, save_dir)
            return
        self._loading = False
        self.busy_stop()
        if status == "err":
            self.save = None
            self.head.configure(text="Не открылось: %s"
                                     % os.path.basename(save_dir))
            messagebox.showerror("Не удалось прочитать сохранение",
                                 "%s\n\n%s" % (save_dir, payload))
            return
        self.save = payload
        self.fill_fields()
        self.fill_backups()
        self.fill_raw()
        self.note("Открыто: %s" % save_dir)

    def busy_start(self, text: str) -> None:
        self.status.configure(text=text)
        self.progress.pack(side="right", padx=(6, 0))
        self.progress.start(12)
        self.configure(cursor="watch")

    def busy_stop(self) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.status.configure(text=self._idle_status())
        self.configure(cursor="")

    def fill_fields(self) -> None:
        s = self.save
        if s is None:
            return
        label = ""
        if s.info is not None:
            u = s.info.model.first("save_container")
            label = u.get("name").as_str() if u and u.get("name") else ""
        self.head.configure(
            text="%s — %s / %s, версия %d"
            % (os.path.basename(s.dir), s.game.container, s.game.kind,
               s.save_version))
        self.v_money.set(str(s.money))
        self.v_xp.set(str(s.experience))
        self.v_time.set(str(s.game_time))
        self.v_name.set(label)
        self.v_limit.set(str(s.bank.int_of("loan_limit")) if s.bank else "")
        self.v_loans.set(False)
        self.v_adr.set(str(adr_levels(s.economy.int_of("adr"))))
        for name, var in self.v_skills.items():
            var.set(str(s.economy.int_of(name)))
        self.loaded = {
            "money": self.v_money.get(), "xp": self.v_xp.get(),
            "time": self.v_time.get(), "name": self.v_name.get(),
            "limit": self.v_limit.get(), "adr": self.v_adr.get(),
            "skills": {k: v.get() for k, v in self.v_skills.items()},
        }
        self.fleet.delete(*self.fleet.get_children())
        self.trucks = {}
        for unit in s.trucks():
            iid = self.fleet.insert(
                "", "end", text=s.vehicle_label(unit),
                values=(unit.int_of("odometer"),
                        "%.0f%%" % (unit.float_of("engine_wear") * 100)))
            self.trucks[iid] = unit
        bought = sum(1 for _r, g in s.garages() if g.int_of("status") != 0)
        self.world_info.configure(
            text="в сохранении: городов %d, гаражей %d (куплено %d), "
                 "салонов %d, агентств %d, водителей %d\n"
                 "город из сохранения берётся сам — работают и моды, и DLC"
            % (len(s.economy.items_of("visited_cities")), len(s.garages()),
               bought, len(s.economy.items_of("unlocked_dealers")),
               len(s.economy.items_of("unlocked_recruitments")),
               len(s.drivers())))

    def fill_backups(self) -> None:
        self.backups.delete(0, "end")
        self.backup_paths = []
        for name, path in list_backups(self.profile_dir):
            self.backups.insert("end", name)
            self.backup_paths.append(path)
    def fill_raw(self) -> None:
        self.units.delete(*self.units.get_children())
        self.attrs.delete(*self.attrs.get_children())
        self.unit_map = {}
        if self.save is None:
            return
        needle = self.v_filter.get().strip().lower()
        shown = 0
        for unit in self.save.f.units:
            if needle and needle not in unit.cls.lower() \
                    and needle not in unit.name.lower():
                continue
            iid = self.units.insert("", "end",
                                    text="%s : %s" % (unit.cls, unit.name))
            self.unit_map[iid] = unit
            shown += 1
            if shown >= 500:
                self.units.insert("", "end", text="… показаны первые 500")
                break

    def on_unit(self, _event=None) -> None:
        self.attrs.delete(*self.attrs.get_children())
        self.attr_map = {}
        sel = self.units.selection()
        unit = self.unit_map.get(sel[0]) if sel else None
        if unit is None:
            return
        for attr in unit.attrs:
            if attr.is_array:
                text = "[%d] %s" % (len(attr.items),
                                    ", ".join(attr.items[:6])[:80])
            else:
                text = attr.value or ""
            iid = self.attrs.insert("", "end", text=attr.name, values=(text,))
            self.attr_map[iid] = attr

    def edit_attr(self, _event=None) -> None:
        sel = self.attrs.selection()
        attr = self.attr_map.get(sel[0]) if sel else None
        if attr is None or attr.is_array:
            if attr is not None:
                messagebox.showinfo("Массив", "Списки правятся только кнопками "
                                              "на других вкладках.")
            return
        from tkinter.simpledialog import askstring
        new = askstring("Правка поля", "%s:" % attr.name,
                        initialvalue=attr.value or "", parent=self)
        if new is None or new == attr.value:
            return
        attr.value = new
        self.attrs.item(sel[0], values=(new,))
        self.note("поле %s = %s" % (attr.name, new))
    # ------------------------------------------------------------- the button
    def apply(self) -> None:
        s = self.save
        if s is None:
            messagebox.showinfo("Нет сохранения", "Сначала выберите сохранение.")
            return
        if self._loading or self._writing:
            return
        before = len(s.log)
        try:
            self._collect(s)
        except (SaveError, ValueError) as exc:
            messagebox.showerror("Не то значение", str(exc))
            return
        if len(s.log) == before and not s.changed:
            self.note("Нечего менять.")
            return
        for line in s.log[before:]:
            self.note("  " + line)
        # Encoding a big save and copying the backup folder are slow enough to
        # ghost the window on a slow disk, so they go to a worker thread too.
        self._writing = True
        self.busy_start("Записываю сохранение и делаю копию")
        make_backup = self.backup_var.get()
        result: queue.Queue = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                result.put(("ok", s.write(make_backup=make_backup)))
            except BaseException as exc:  # noqa: BLE001 - reported in the UI
                result.put(("err", exc))

        threading.Thread(target=work, daemon=True, name="ets2se-write").start()
        self.after(50, self._poll_write, result, s)

    def _poll_write(self, result: queue.Queue, s: SaveGame) -> None:
        try:
            status, payload = result.get_nowait()
        except queue.Empty:
            self.after(50, self._poll_write, result, s)
            return
        self._writing = False
        self.busy_stop()
        if status == "err":
            messagebox.showerror("Не удалось записать", str(payload))
            return
        backup = payload
        self.note("Записано: %s" % s.dir)
        if backup:
            self.note("Копия: %s" % backup)
        messagebox.showinfo("Готово", "Сохранение записано.\n"
                            + ("Копия: %s" % backup if backup else
                               "Копия не делалась."))
        self.open_save(self.profile_dir, s.dir)
        self.refresh_tree()


    def _collect(self, s: SaveGame) -> None:
        old = self.loaded
        if self.v_money.get().strip() and self.v_money.get() != old["money"]:
            s.set_money(int(self.v_money.get()))
        if self.v_xp.get().strip() and self.v_xp.get() != old["xp"]:
            s.set_experience(int(self.v_xp.get()))
        if self.v_time.get().strip() and self.v_time.get() != old["time"]:
            s.set_game_time(int(self.v_time.get()))
        if self.v_name.get() != old["name"]:
            s.rename(self.v_name.get())
        if self.v_limit.get().strip() and self.v_limit.get() != old["limit"]:
            s.set_loan_limit(int(self.v_limit.get()))
        if self.v_loans.get():
            s.clear_loans()
        if self.v_adr.get() != old["adr"]:
            s.set_skills(adr=int(self.v_adr.get()))
        for name, var in self.v_skills.items():
            if var.get() != old["skills"][name]:
                s.set_skills(**{name: int(var.get())})
        if self.v_repair.get():
            s.repair_all(unfixable=self.v_unfix.get())
        if self.v_refuel.get():
            s.refuel_all()
        if self.v_cargo.get():
            s.fix_cargo()
        if self.v_cities.get():
            s.unlock_cities()
        if self.v_dealers.get():
            s.unlock_dealers()
        if self.v_recruit.get():
            s.unlock_recruitments()
        status = dict(GARAGE_CHOICES).get(self.v_garage.get())
        if status is not None:
            s.buy_all_garages(status)
        policy = self.v_policy.get()
        policy = policy if policy in TRAINING_POLICIES else None
        if policy:
            s.set_driver_policy(policy)
        if self.v_drivers.get():
            s.boost_drivers(policy=policy)
        elif policy:
            s.set_all_driver_policies(policy)

    def apply_truck(self) -> None:
        sel = self.fleet.selection()
        unit = self.trucks.get(sel[0]) if sel else None
        if self.save is None or unit is None:
            messagebox.showinfo("Не выбран грузовик",
                                "Выберите грузовик в списке.")
            return
        if self.v_odo.get().strip():
            self.save.set_odometer(unit, int(self.v_odo.get()))
            self.save.note("пробег %s: %s км" % (self.save.vehicle_label(unit),
                                                 self.v_odo.get()))
        if self.v_plate.get().strip():
            self.save.set_plate(unit, self.v_plate.get().strip())
            self.save.note("номер %s" % self.v_plate.get().strip())
        self.fleet.item(sel[0], values=(unit.int_of("odometer"),
                                        "%.0f%%" % (unit.float_of("engine_wear")
                                                    * 100)))
        self.note("грузовик изменён — не забудьте «Применить и сохранить»")

    def reload(self) -> None:
        if self.save is not None:
            self.open_save(self.profile_dir, self.save.dir)

    def do_restore(self) -> None:
        sel = self.backups.curselection()
        if self.save is None or not sel:
            messagebox.showinfo("Нет копии", "Выберите копию в списке.")
            return
        path = self.backup_paths[sel[0]]
        if not messagebox.askyesno(
                "Восстановить?",
                "Файлы сохранения\n%s\nбудут заменены копией\n%s"
                % (self.save.dir, path)):
            return
        restore_backup(path, self.save.dir)
        self.note("восстановлено из %s" % path)
        self.reload()
    def fill_max_skills(self) -> None:
        self.v_adr.set("6")
        for var in self.v_skills.values():
            var.set("6")


def enable_dpi_awareness() -> None:
    """Tell Windows this process scales itself.

    Without it a 125%/150% desktop — the default on most laptops since Windows
    10 — hands Tk a 96-DPI window and stretches the result, so the whole editor
    looks blurred. Each call is tried in turn: per-monitor v2 (Windows 10 1703+),
    per-monitor (8.1+), then system-wide (Vista+).
    """
    import sys

    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
    except ImportError:
        return
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def scale_fonts(root: tk.Tk) -> None:
    """Match Tk's own scaling to the desktop's, so text is sized right."""
    try:
        dpi = root.winfo_fpixels("1i")
    except tk.TclError:
        return
    if dpi > 0:
        root.tk.call("tk", "scaling", dpi / 72.0)


def main() -> int:
    enable_dpi_awareness()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())










