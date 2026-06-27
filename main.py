"""
main.py — Ondas Binaurais · App completo
Interface com abas: Player · Sons · Focus · Dashboard · Conquistas
Tudo persiste em SQLite (~/.ondabinaural/data.db).
"""
import sys, os, math, time, threading, datetime
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# auto-install deps
def _ensure(pkg, imp=None):
    try: __import__(imp or pkg)
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable,"-m","pip","install",pkg,"--quiet"])
for _p,_i in [("numpy",None),("sounddevice",None),("pystray",None),("Pillow","PIL")]:
    try: _ensure(_p,_i)
    except Exception: pass

import database as db
import audio_engine as ae
import profiles as pr

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    HAVE_TRAY = True
except Exception:
    HAVE_TRAY = False


# ─────────────────────────────────────────────────────── toast (Windows) ─────
def toast(title, msg):
    try:
        ps = (
            f'Add-Type -AssemblyName System.Windows.Forms;'
            f'$n=New-Object System.Windows.Forms.NotifyIcon;'
            f'$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;'
            f'$n.ShowBalloonTip(5000,"{title}","{msg}",'
            f'[System.Windows.Forms.ToolTipIcon]::None);Start-Sleep -s 6;$n.Dispose()'
        )
        import subprocess
        subprocess.Popen(["powershell","-WindowStyle","Hidden","-Command",ps],
                         creationflags=0x08000000)
    except Exception:
        pass


def tray_image(label="", color="#2d6a9f"):
    img = Image.new("RGBA",(64,64),(0,0,0,0))
    d = ImageDraw.Draw(img); d.ellipse([2,2,62,62],fill=color)
    if label:
        try: fnt=ImageFont.truetype("arialbd.ttf",17)
        except: fnt=ImageFont.load_default()
        bb=d.textbbox((0,0),label,font=fnt)
        d.text(((64-(bb[2]-bb[0]))//2,(64-(bb[3]-bb[1]))//2-1),label,
               fill="white",font=fnt)
    return img


# ════════════════════════════════════════════════════════════════════ THEME ══
class Theme:
    def __init__(self, amoled=False):
        self.set(amoled)
    def set(self, amoled):
        self.BG     = "#000000" if amoled else "#0f0f14"
        self.CARD   = "#0a0a0a" if amoled else "#16161f"
        self.SURF   = "#121212" if amoled else "#1e1e2a"
        self.SURF2  = "#1a1a1a" if amoled else "#252532"
        self.BORDER = "#222222" if amoled else "#2a2a3a"
        self.TEXT   = "#e8e8f0"
        self.MUTED  = "#6a6a80"
        self.DIM    = "#3a3a50"
        self.ACCENT = "#378add"
        self.GREEN  = "#4adf8a"
        self.GOLD   = "#e0b020"


# ════════════════════════════════════════════════════════════════════ APP ════
class App(tk.Tk):
    WIDTH = 400

    def __init__(self):
        super().__init__()
        self.engine = ae.AudioEngine()
        self.theme  = Theme(db.get("theme")=="amoled")

        # estado de sessão
        self.preset_idx   = 1
        self.wave_phase   = 0.0
        self.timer_secs   = 0
        self.timer_target = 0
        self.timer_on     = False
        self.is_break     = False
        self.session_start= None
        self.session_started_iso = None
        self.objective    = None
        self._notified    = set()
        self.sleep_seq    = None       # lista de etapas quando em sleep mode
        self.sleep_idx    = 0
        self._dx=self._dy = 0
        self._tray=None

        # seed protocolos default uma vez
        if not db.list_protocols():
            for p in pr.DEFAULT_PROTOCOLS:
                db.add_protocol(p["name"], p["config"])

        self._build()
        self._apply_preset(1)
        self._restore_last_sounds()
        self._wave_tick()
        self._stat_tick()
        if HAVE_TRAY:
            self._start_tray()
        self.protocol("WM_DELETE_WINDOW", self._hide)

    # ════════════════════════════════════════════════════════════ BUILD ══════
    def _build(self):
        t=self.theme
        self.title("Ondas Binaurais")
        self.resizable(False, False)
        self.overrideredirect(True)
        if db.get("always_on_top","1")=="1":
            self.wm_attributes("-topmost", True)
        self.configure(bg=t.BG)
        sw,sh=self.winfo_screenwidth(),self.winfo_screenheight()
        self.geometry(f"{self.WIDTH}x720+{sw-self.WIDTH-24}+{max(20,sh-760)}")

        # ── header (única área que arrasta a janela) ────────────────────────
        hdr=tk.Frame(self,bg=t.SURF,height=42); hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ico=tk.Label(hdr,text="◈",bg=t.SURF,fg=t.ACCENT,font=("Segoe UI",13))
        ico.pack(side="left",padx=(12,4),pady=10)
        ttl=tk.Label(hdr,text="Ondas Binaurais",bg=t.SURF,fg=t.TEXT,
                     font=("Segoe UI",10,"bold"))
        ttl.pack(side="left",pady=10)
        # ligar arrasto apenas no header e seus rótulos
        for w in (hdr, ico, ttl):
            w.bind("<ButtonPress-1>", lambda e:self._ds(e))
            w.bind("<B1-Motion>",     lambda e:self._dm(e))
        tk.Button(hdr,text="✕",bg=t.SURF,fg=t.MUTED,font=("Segoe UI",11),bd=0,
                  cursor="hand2",activebackground=t.SURF,activeforeground="#e05555",
                  command=self._hide).pack(side="right",padx=6,pady=8)
        tk.Button(hdr,text="─",bg=t.SURF,fg=t.MUTED,font=("Segoe UI",11),bd=0,
                  cursor="hand2",activebackground=t.SURF,activeforeground=t.TEXT,
                  command=self._hide).pack(side="right",padx=2,pady=8)

        # ── tab bar ─────────────────────────────────────────────────────────
        self.tabbar=tk.Frame(self,bg=t.CARD); self.tabbar.pack(fill="x")
        self._tabs={}
        self._tab_btns={}
        for key,label in [("player","Player"),("sounds","Sons"),
                          ("focus","Focus"),("dash","Dashboard"),
                          ("achiev","Conquistas")]:
            b=tk.Button(self.tabbar,text=label,bg=t.CARD,fg=t.MUTED,
                        font=("Segoe UI",8,"bold"),bd=0,relief="flat",
                        padx=4,pady=7,cursor="hand2",
                        activebackground=t.CARD,activeforeground=t.TEXT,
                        command=lambda k=key:self._show_tab(k))
            b.pack(side="left",expand=True,fill="x")
            self._tab_btns[key]=b

        # ── container das abas ──────────────────────────────────────────────
        self.body=tk.Frame(self,bg=t.BG); self.body.pack(fill="both",expand=True)
        for key in ["player","sounds","focus","dash","achiev"]:
            f=tk.Frame(self.body,bg=t.BG)
            self._tabs[key]=f
        self._build_player()
        self._build_sounds()
        self._build_focus()
        self._build_dash()
        self._build_achiev()
        self._show_tab("player")

    def _show_tab(self,key):
        t=self.theme
        for k,f in self._tabs.items():
            f.pack_forget()
            self._tab_btns[k].config(fg=t.MUTED,bg=t.CARD)
        self._tabs[key].pack(fill="both",expand=True)
        self._tab_btns[key].config(fg=t.ACCENT,bg=t.SURF)
        if key=="dash":   self._refresh_dash()
        if key=="achiev": self._refresh_achiev()
        if key=="sounds": pass

    # ════════════════════════════════════════════════════════ PLAYER TAB ═════
    def _build_player(self):
        t=self.theme; f=self._tabs["player"]

        tk.Label(f,text="Use fones de ouvido para sentir o efeito completo",
                 bg=t.BG,fg=t.MUTED,font=("Segoe UI",8)).pack(pady=(10,0),padx=14,anchor="w")

        self._wc=tk.Canvas(f,bg=t.BG,height=80,highlightthickness=0,bd=0)
        self._wc.pack(fill="x",pady=(4,0))

        pf=tk.Frame(f,bg=t.BG); pf.pack(fill="x",padx=12,pady=(10,2))
        self._pbts=[]
        for i,p in enumerate(pr.WAVE_PRESETS):
            b=tk.Button(pf,text=p["name"],bg=t.SURF2,fg=t.TEXT,
                        font=("Segoe UI",8,"bold"),bd=0,relief="flat",pady=7,
                        cursor="hand2",activebackground=t.DIM,activeforeground=t.TEXT,
                        command=lambda i=i:self._apply_preset(i))
            b.pack(side="left",expand=True,fill="x",padx=2)
            self._pbts.append(b)

        self._desc=tk.Label(f,text="",bg=t.BG,fg=t.MUTED,font=("Segoe UI",8),
                            justify="center",wraplength=350)
        self._desc.pack(pady=(6,2))

        tk.Frame(f,bg=t.BORDER,height=1).pack(fill="x",padx=14,pady=(6,4))

        self._base_v=self._slider(f,"Base (Hz)",80,300,200,1,
                                  lambda v:self.engine.set_binaural(base=v))
        self._beat_v=self._slider(f,"Batida (Hz)",0.5,40,6,0.5,
                                  lambda v:self.engine.set_binaural(beat=v))
        self._vol_v=self._slider(f,"Volume ondas",0,100,50,1,
                                 lambda v:self.engine.set_binaural(vol=v))

        tk.Frame(f,bg=t.BORDER,height=1).pack(fill="x",padx=14,pady=(6,4))

        # timer
        tk.Label(f,text="Timer / Pomodoro",bg=t.BG,fg=t.MUTED,
                 font=("Segoe UI",8)).pack(anchor="w",padx=14)
        tf=tk.Frame(f,bg=t.BG); tf.pack(fill="x",padx=14,pady=(2,4))
        self._tlbl=tk.Label(tf,text="00:00",bg=t.BG,fg=t.TEXT,
                            font=("Segoe UI",30,"bold")); self._tlbl.pack(side="left")
        self._state_lbl=tk.Label(tf,text="",bg=t.BG,fg=t.ACCENT,
                                 font=("Segoe UI",9)); self._state_lbl.pack(side="left",padx=8)
        rf=tk.Frame(tf,bg=t.BG); rf.pack(side="right")
        tk.Label(rf,text="min (0=livre)",bg=t.BG,fg=t.MUTED,
                 font=("Segoe UI",8)).pack(anchor="e")
        self._spin=tk.Spinbox(rf,from_=0,to=240,width=5,font=("Segoe UI",12),
                              bg=t.SURF2,fg=t.TEXT,buttonbackground=t.SURF2,
                              insertbackground=t.TEXT,bd=0,relief="flat",justify="center")
        self._spin.delete(0,"end"); self._spin.insert(0,"25"); self._spin.pack(anchor="e")

        nf=tk.Frame(f,bg=t.BG); nf.pack(fill="x",padx=14,pady=(0,4))
        self._n5=tk.BooleanVar(value=True); self._nmid=tk.BooleanVar(value=True)
        self._n10=tk.BooleanVar(value=True)
        for var,txt in [(self._n5,"5 min"),(self._nmid,"Metade"),(self._n10,"10 min")]:
            tk.Checkbutton(nf,text=txt,variable=var,bg=t.BG,fg=t.MUTED,
                           selectcolor=t.SURF2,activebackground=t.BG,
                           activeforeground=t.MUTED,font=("Segoe UI",8),bd=0
                           ).pack(side="left",padx=(0,10))

        self._pbtn=tk.Button(f,text="▶   Iniciar",bg=t.ACCENT,fg="white",
                             font=("Segoe UI",12,"bold"),bd=0,height=2,cursor="hand2",
                             activebackground="#2878cc",activeforeground="white",
                             command=self._toggle)
        self._pbtn.pack(fill="x",padx=14,pady=(4,3))

        bf=tk.Frame(f,bg=t.BG); bf.pack(fill="x",padx=14,pady=(0,4))
        for txt,cmd in [("⭐ Salvar favorita",self._save_favorite),
                       ("↻ Última sessão",self._restart_last),
                       ("🌙 Sleep",self._start_sleep)]:
            tk.Button(bf,text=txt,bg=t.SURF2,fg=t.MUTED,font=("Segoe UI",8),
                      bd=0,cursor="hand2",activebackground=t.DIM,activeforeground=t.TEXT,
                      command=cmd).pack(side="left",expand=True,fill="x",padx=2)

        # favoritos salvos
        self._fav_frame=tk.Frame(f,bg=t.BG); self._fav_frame.pack(fill="x",padx=14,pady=(2,0))
        self._refresh_favorites()

    def _slider(self,parent,label,mn,mx,default,res,cmd):
        t=self.theme
        var=tk.DoubleVar(value=default)
        row=tk.Frame(parent,bg=t.BG); row.pack(fill="x",padx=14,pady=2)
        tk.Label(row,text=label,bg=t.BG,fg=t.MUTED,font=("Segoe UI",8),
                 width=11,anchor="w").pack(side="left")
        vl=tk.Label(row,bg=t.BG,fg=t.TEXT,font=("Segoe UI",9,"bold"),
                    width=6,anchor="e"); vl.pack(side="right")
        fmt=lambda v:(f"{float(v):.1f}" if res<1 else str(int(float(v))))
        vl.config(text=fmt(default))
        tk.Scale(row,variable=var,from_=mn,to=mx,orient="horizontal",resolution=res,
                 bg=t.BG,fg=t.TEXT,highlightthickness=0,troughcolor=t.SURF2,
                 activebackground=t.ACCENT,showvalue=False,bd=0,
                 command=lambda v,l=vl,c=cmd:(l.config(text=fmt(v)),c(float(v)))
                 ).pack(side="left",expand=True,fill="x",padx=(6,8))
        return var

    # ════════════════════════════════════════════════════════ SOUNDS TAB ═════
    def _build_sounds(self):
        t=self.theme; f=self._tabs["sounds"]
        tk.Label(f,text="Mixer de sons ambiente",bg=t.BG,fg=t.TEXT,
                 font=("Segoe UI",10,"bold")).pack(anchor="w",padx=14,pady=(10,2))
        tk.Label(f,text="Ative quantos quiser · cada um com volume próprio",
                 bg=t.BG,fg=t.MUTED,font=("Segoe UI",8)).pack(anchor="w",padx=14)

        # scroll area
        canvas=tk.Canvas(f,bg=t.BG,highlightthickness=0,height=560)
        sb=ttk.Scrollbar(f,orient="vertical",command=canvas.yview)
        inner=tk.Frame(canvas,bg=t.BG)
        inner.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0),window=inner,anchor="nw",width=self.WIDTH-24)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left",fill="both",expand=True,padx=(8,0),pady=8)
        sb.pack(side="right",fill="y",pady=8)
        canvas.bind_all("<MouseWheel>",lambda e:canvas.yview_scroll(int(-e.delta/120),"units"))

        self._sound_rows={}
        favs=set(db.get_json("fav_sounds",[]) or [])
        # ordenar: favoritos primeiro
        items=sorted(ae.AMBIENTS.items(),
                     key=lambda kv:(kv[0] not in favs, ae.AMBIENTS[kv[0]][0]))
        for sid,(label,_fn) in items:
            self._make_sound_row(inner,sid,label,sid in favs)

    def _make_sound_row(self,parent,sid,label,is_fav):
        t=self.theme
        row=tk.Frame(parent,bg=t.SURF,highlightthickness=0)
        row.pack(fill="x",pady=3,padx=4)
        inner=tk.Frame(row,bg=t.SURF); inner.pack(fill="x",padx=10,pady=6)

        top=tk.Frame(inner,bg=t.SURF); top.pack(fill="x")
        on_var=tk.BooleanVar(value=False)
        def _toggle_on():
            if on_var.get():
                self.engine.add_layer(sid)
                self.engine.set_layer_vol(sid,vol_var.get())
                if not self.engine.playing:
                    self.engine.start()
                self._save_sounds_state()
            else:
                self.engine.remove_layer(sid)
                self._save_sounds_state()
        cb=tk.Checkbutton(top,text=label,variable=on_var,command=_toggle_on,
                          bg=t.SURF,fg=t.TEXT,selectcolor=t.SURF2,
                          activebackground=t.SURF,activeforeground=t.TEXT,
                          font=("Segoe UI",9,"bold"),bd=0)
        cb.pack(side="left")

        fav_var=tk.BooleanVar(value=is_fav)
        def _toggle_fav():
            favs=set(db.get_json("fav_sounds",[]) or [])
            if fav_var.get(): favs.add(sid)
            else: favs.discard(sid)
            db.set_json("fav_sounds",list(favs))
        tk.Checkbutton(top,text="⭐",variable=fav_var,command=_toggle_fav,
                       bg=t.SURF,fg=t.GOLD,selectcolor=t.SURF,
                       activebackground=t.SURF,font=("Segoe UI",9),bd=0
                       ).pack(side="right")

        spatial_var=tk.BooleanVar(value=False)
        tk.Checkbutton(top,text="3D",variable=spatial_var,
                       command=lambda:self.engine.set_layer_spatial(sid,spatial_var.get()),
                       bg=t.SURF,fg=t.MUTED,selectcolor=t.SURF2,
                       activebackground=t.SURF,activeforeground=t.MUTED,
                       font=("Segoe UI",8),bd=0).pack(side="right",padx=(0,4))

        vol_var=tk.DoubleVar(value=50)
        tk.Scale(inner,variable=vol_var,from_=0,to=100,orient="horizontal",
                 resolution=1,bg=t.SURF,fg=t.TEXT,highlightthickness=0,
                 troughcolor=t.SURF2,activebackground=t.ACCENT,showvalue=False,bd=0,
                 command=lambda v:self.engine.set_layer_vol(sid,float(v))
                 ).pack(fill="x",pady=(4,0))

        self._sound_rows[sid]={"on":on_var,"vol":vol_var,"spatial":spatial_var,"fav":fav_var}

    def _save_sounds_state(self):
        state={}
        for sid,r in self._sound_rows.items():
            if r["on"].get():
                state[sid]={"vol":r["vol"].get(),"spatial":r["spatial"].get()}
        db.set_json("active_sounds",state)

    def _restore_last_sounds(self):
        state=db.get_json("active_sounds",{}) or {}
        for sid,cfg in state.items():
            if sid in self._sound_rows:
                self._sound_rows[sid]["on"].set(True)
                self._sound_rows[sid]["vol"].set(cfg.get("vol",50))
                self._sound_rows[sid]["spatial"].set(cfg.get("spatial",False))

    # ════════════════════════════════════════════════════════ FOCUS TAB ══════
    def _build_focus(self):
        t=self.theme; f=self._tabs["focus"]
        tk.Label(f,text="Focus Engine",bg=t.BG,fg=t.TEXT,
                 font=("Segoe UI",11,"bold")).pack(anchor="w",padx=14,pady=(10,2))
        tk.Label(f,text="Escolha o objetivo — o app monta a sessão inteira.",
                 bg=t.BG,fg=t.MUTED,font=("Segoe UI",8)).pack(anchor="w",padx=14)

        canvas=tk.Canvas(f,bg=t.BG,highlightthickness=0,height=580)
        sb=ttk.Scrollbar(f,orient="vertical",command=canvas.yview)
        inner=tk.Frame(canvas,bg=t.BG)
        inner.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0),window=inner,anchor="nw",width=self.WIDTH-24)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left",fill="both",expand=True,padx=(8,0),pady=8)
        sb.pack(side="right",fill="y",pady=8)

        for prof in pr.FOCUS_PROFILES:
            card=tk.Frame(inner,bg=t.SURF); card.pack(fill="x",pady=4,padx=4)
            ci=tk.Frame(card,bg=t.SURF); ci.pack(fill="x",padx=12,pady=10)
            head=tk.Frame(ci,bg=t.SURF); head.pack(fill="x")
            tk.Label(head,text=f"{prof['icon']}  {prof['label']}",bg=t.SURF,
                     fg=t.TEXT,font=("Segoe UI",10,"bold")).pack(side="left")
            mins=f"{prof['focus_min']}min"
            if prof['break_min']: mins+=f" + {prof['break_min']}min pausa"
            tk.Label(head,text=mins,bg=t.SURF,fg=t.ACCENT,
                     font=("Segoe UI",8)).pack(side="right")
            tk.Label(ci,text=prof["desc"],bg=t.SURF,fg=t.MUTED,font=("Segoe UI",8),
                     justify="left",wraplength=320).pack(anchor="w",pady=(4,6))
            tk.Button(ci,text="▶  Iniciar este perfil",bg=t.SURF2,fg=t.TEXT,
                      font=("Segoe UI",9,"bold"),bd=0,cursor="hand2",pady=6,
                      activebackground=t.ACCENT,activeforeground="white",
                      command=lambda p=prof:self._start_profile(p)).pack(fill="x")

        # protocolos personalizados
        tk.Label(inner,text="Protocolos Biohacker",bg=t.BG,fg=t.TEXT,
                 font=("Segoe UI",10,"bold")).pack(anchor="w",padx=4,pady=(14,4))
        self._proto_frame=tk.Frame(inner,bg=t.BG); self._proto_frame.pack(fill="x")
        self._refresh_protocols()
        tk.Button(inner,text="+ Criar protocolo a partir da config atual",bg=t.SURF2,
                  fg=t.ACCENT,font=("Segoe UI",8),bd=0,cursor="hand2",pady=6,
                  activebackground=t.DIM,activeforeground=t.TEXT,
                  command=self._save_protocol).pack(fill="x",padx=4,pady=6)

    def _refresh_protocols(self):
        t=self.theme
        for w in self._proto_frame.winfo_children(): w.destroy()
        for p in db.list_protocols():
            import json
            cfg=json.loads(p["config"])
            row=tk.Frame(self._proto_frame,bg=t.SURF); row.pack(fill="x",pady=3,padx=4)
            ri=tk.Frame(row,bg=t.SURF); ri.pack(fill="x",padx=10,pady=6)
            tk.Label(ri,text=p["name"],bg=t.SURF,fg=t.TEXT,
                     font=("Segoe UI",9,"bold")).pack(side="left")
            tk.Button(ri,text="▶",bg=t.SURF,fg=t.ACCENT,font=("Segoe UI",10),bd=0,
                      cursor="hand2",activebackground=t.SURF,activeforeground=t.GREEN,
                      command=lambda c=cfg:self._start_profile(c)).pack(side="right")
            tk.Button(ri,text="🗑",bg=t.SURF,fg=t.MUTED,font=("Segoe UI",9),bd=0,
                      cursor="hand2",activebackground=t.SURF,activeforeground="#e05555",
                      command=lambda pid=p["id"]:(db.delete_protocol(pid),
                                                  self._refresh_protocols())
                      ).pack(side="right",padx=6)

    # ════════════════════════════════════════════════════════ DASH TAB ═══════
    def _build_dash(self):
        t=self.theme; f=self._tabs["dash"]
        self._dash_canvas=tk.Canvas(f,bg=t.BG,highlightthickness=0)
        sb=ttk.Scrollbar(f,orient="vertical",command=self._dash_canvas.yview)
        self._dash_inner=tk.Frame(self._dash_canvas,bg=t.BG)
        self._dash_inner.bind("<Configure>",
            lambda e:self._dash_canvas.configure(scrollregion=self._dash_canvas.bbox("all")))
        self._dash_canvas.create_window((0,0),window=self._dash_inner,anchor="nw",
                                        width=self.WIDTH-24)
        self._dash_canvas.configure(yscrollcommand=sb.set)
        self._dash_canvas.pack(side="left",fill="both",expand=True,padx=(8,0),pady=8)
        sb.pack(side="right",fill="y",pady=8)

    def _refresh_dash(self):
        t=self.theme
        for w in self._dash_inner.winfo_children(): w.destroy()
        s=db.stats_overview()
        xp=db.get_xp(); lvl,into,need=db.level_for_xp(xp)
        strk=db.streak()
        goal=db.get_int("daily_goal_min",240)
        prog=min(100,round(s["today_min"]/goal*100)) if goal else 0

        # nível + XP
        top=tk.Frame(self._dash_inner,bg=t.SURF); top.pack(fill="x",pady=(4,6),padx=4)
        ti=tk.Frame(top,bg=t.SURF); ti.pack(fill="x",padx=12,pady=10)
        tk.Label(ti,text=f"Nível {lvl}",bg=t.SURF,fg=t.GOLD,
                 font=("Segoe UI",14,"bold")).pack(side="left")
        tk.Label(ti,text=f"{xp} XP",bg=t.SURF,fg=t.MUTED,
                 font=("Segoe UI",9)).pack(side="right")
        self._bar(top,into,need,t.GOLD)

        # streak
        sf=tk.Frame(self._dash_inner,bg=t.SURF); sf.pack(fill="x",pady=4,padx=4)
        tk.Label(sf,text=f"🔥 {strk} {'dia' if strk==1 else 'dias'} seguidos focando",
                 bg=t.SURF,fg=t.TEXT,font=("Segoe UI",11,"bold")).pack(padx=12,pady=10)

        # meta diária
        gf=tk.Frame(self._dash_inner,bg=t.SURF); gf.pack(fill="x",pady=4,padx=4)
        gi=tk.Frame(gf,bg=t.SURF); gi.pack(fill="x",padx=12,pady=10)
        tk.Label(gi,text=f"Meta hoje: {s['today_min']} / {goal} min",bg=t.SURF,
                 fg=t.TEXT,font=("Segoe UI",9,"bold")).pack(side="left")
        tk.Label(gi,text=f"{prog}%",bg=t.SURF,fg=t.GREEN if prog>=100 else t.ACCENT,
                 font=("Segoe UI",9,"bold")).pack(side="right")
        self._bar(gf,prog,100,t.GREEN if prog>=100 else t.ACCENT)

        # métricas grid
        grid=tk.Frame(self._dash_inner,bg=t.BG); grid.pack(fill="x",pady=4)
        metrics=[
            ("Hoje",f"{self._hm(s['today_min'])}"),
            ("Semana",f"{self._hm(s['week_min'])}"),
            ("Mês",f"{self._hm(s['month_min'])}"),
            ("Ano",f"{self._hm(s['year_min'])}"),
            ("Total",f"{s['all_hours']}h"),
            ("Maior sessão",f"{self._hm(s['biggest_min'])}"),
            ("Média/dia",f"{self._hm(s['avg_day_min'])}"),
            ("Pomodoros",f"{s['pomodoros']}"),
        ]
        for i,(lab,val) in enumerate(metrics):
            cell=tk.Frame(grid,bg=t.SURF2); 
            cell.grid(row=i//2,column=i%2,sticky="nsew",padx=3,pady=3)
            tk.Label(cell,text=val,bg=t.SURF2,fg=t.TEXT,
                     font=("Segoe UI",13,"bold")).pack(anchor="w",padx=10,pady=(8,0))
            tk.Label(cell,text=lab,bg=t.SURF2,fg=t.MUTED,
                     font=("Segoe UI",8)).pack(anchor="w",padx=10,pady=(0,8))
        grid.columnconfigure(0,weight=1); grid.columnconfigure(1,weight=1)

        # gráfico 30 dias
        tk.Label(self._dash_inner,text="Últimos 30 dias",bg=t.BG,fg=t.MUTED,
                 font=("Segoe UI",8)).pack(anchor="w",padx=6,pady=(8,2))
        self._draw_bar_chart(db.daily_series(30))

        # calendário tipo GitHub
        tk.Label(self._dash_inner,text="Calendário de foco",bg=t.BG,fg=t.MUTED,
                 font=("Segoe UI",8)).pack(anchor="w",padx=6,pady=(8,2))
        self._draw_heatmap(db.daily_series(98))

        # insights
        ins=tk.Frame(self._dash_inner,bg=t.SURF); ins.pack(fill="x",pady=8,padx=4)
        ph=s["peak_hour"]
        peak=f"{ph:02d}h" if ph is not None else "—"
        lines=[
            f"⏰ Você mais produz às {peak}",
            f"📊 Sessão média: {s['avg_sess_min']} min",
            f"🌊 Onda favorita: {s['fav_preset']}",
            f"🔊 Som favorito: {db.fav_sound()}",
            f"✅ Conclusão de pomodoros: {s['comp_rate']}%",
        ]
        for ln in lines:
            tk.Label(ins,text=ln,bg=t.SURF,fg=t.TEXT,font=("Segoe UI",8),
                     justify="left").pack(anchor="w",padx=12,pady=3)

        # botões export
        ef=tk.Frame(self._dash_inner,bg=t.BG); ef.pack(fill="x",pady=(4,10),padx=4)
        for txt,fn in [("Exportar CSV",self._export_csv),
                      ("Exportar JSON",self._export_json),
                      ("Histórico",self._show_history)]:
            tk.Button(ef,text=txt,bg=t.SURF2,fg=t.MUTED,font=("Segoe UI",8),bd=0,
                      cursor="hand2",activebackground=t.DIM,activeforeground=t.TEXT,
                      command=fn).pack(side="left",expand=True,fill="x",padx=2)

        # settings
        self._build_settings_inline()

    def _bar(self,parent,val,total,color):
        t=self.theme
        c=tk.Canvas(parent,bg=t.SURF,height=6,highlightthickness=0)
        c.pack(fill="x",padx=12,pady=(0,10))
        c.update_idletasks()
        w=c.winfo_width() or self.WIDTH-50
        frac=max(0,min(1,val/total)) if total else 0
        c.create_rectangle(0,0,w,6,fill=t.SURF2,outline="")
        c.create_rectangle(0,0,w*frac,6,fill=color,outline="")

    def _draw_bar_chart(self,series):
        t=self.theme
        c=tk.Canvas(self._dash_inner,bg=t.BG,height=90,highlightthickness=0)
        c.pack(fill="x",padx=6)
        c.update_idletasks()
        W=self.WIDTH-30; H=90
        mx=max((v for _,v in series),default=1) or 1
        n=len(series); bw=W/n
        for i,(d,v) in enumerate(series):
            x=i*bw; h=(v/mx)*(H-16)
            c.create_rectangle(x+1,H-h-2,x+bw-1,H-2,
                               fill=t.ACCENT if v>0 else t.SURF2,outline="")
        c.create_text(2,6,text=f"max {mx}min",fill=t.MUTED,
                      font=("Segoe UI",7),anchor="w")

    def _draw_heatmap(self,series):
        t=self.theme
        c=tk.Canvas(self._dash_inner,bg=t.BG,height=110,highlightthickness=0)
        c.pack(fill="x",padx=6)
        mx=max((v for _,v in series),default=1) or 1
        cell=13; gap=2
        cols=(len(series)+6)//7
        def shade(v):
            if v<=0: return t.SURF2
            r=v/mx
            if r<0.25: return "#1d4a2e"
            if r<0.5:  return "#2a6e44"
            if r<0.75: return "#39965c"
            return t.GREEN
        for i,(d,v) in enumerate(series):
            col=i//7; rowi=i%7
            x=col*(cell+gap); y=rowi*(cell+gap)
            c.create_rectangle(x,y,x+cell,y+cell,fill=shade(v),outline=t.BG)

    def _hm(self,minutes):
        if minutes>=60:
            h=minutes//60; m=minutes%60
            return f"{h}h{m:02d}" if m else f"{h}h"
        return f"{minutes}min"

    def _build_settings_inline(self):
        t=self.theme
        tk.Label(self._dash_inner,text="Configurações",bg=t.BG,fg=t.TEXT,
                 font=("Segoe UI",10,"bold")).pack(anchor="w",padx=6,pady=(10,4))
        box=tk.Frame(self._dash_inner,bg=t.SURF); box.pack(fill="x",padx=4,pady=(0,10))
        bi=tk.Frame(box,bg=t.SURF); bi.pack(fill="x",padx=12,pady=10)

        # meta diária
        gr=tk.Frame(bi,bg=t.SURF); gr.pack(fill="x",pady=2)
        tk.Label(gr,text="Meta diária (min)",bg=t.SURF,fg=t.MUTED,
                 font=("Segoe UI",8)).pack(side="left")
        goal_var=tk.StringVar(value=str(db.get_int("daily_goal_min",240)))
        e=tk.Entry(gr,textvariable=goal_var,width=6,bg=t.SURF2,fg=t.TEXT,
                   insertbackground=t.TEXT,bd=0,justify="center")
        e.pack(side="right")
        e.bind("<FocusOut>",lambda ev:db.set("daily_goal_min",goal_var.get() or "240"))

        # toggles
        def _toggle(key,label,default="0"):
            var=tk.BooleanVar(value=db.get(key,default)=="1")
            def _set():
                db.set(key,"1" if var.get() else "0")
                if key=="always_on_top":
                    self.wm_attributes("-topmost",var.get())
                if key=="open_with_windows":
                    self._set_autostart(var.get())
                if key=="theme_amoled":
                    pass
            tk.Checkbutton(bi,text=label,variable=var,command=_set,bg=t.SURF,
                           fg=t.TEXT,selectcolor=t.SURF2,activebackground=t.SURF,
                           activeforeground=t.TEXT,font=("Segoe UI",8),bd=0,
                           anchor="w").pack(fill="x",pady=1)
            return var
        _toggle("open_with_windows","Abrir com o Windows")
        _toggle("always_on_top","Sempre acima das janelas","1")
        _toggle("invisible_mode","Modo invisível (some no Alt+Tab ao iniciar)")
        _toggle("smart_pause","Pausa inteligente (pausa se PC inativo)","1")
        _toggle("rest_reminders","Lembretes de descanso","1")

        # tema amoled
        amoled_var=tk.BooleanVar(value=db.get("theme")=="amoled")
        def _set_theme():
            db.set("theme","amoled" if amoled_var.get() else "dark")
            messagebox.showinfo("Tema","Reinicie o app para aplicar o tema.")
        tk.Checkbutton(bi,text="Tema AMOLED (preto puro)",variable=amoled_var,
                       command=_set_theme,bg=t.SURF,fg=t.TEXT,selectcolor=t.SURF2,
                       activebackground=t.SURF,activeforeground=t.TEXT,
                       font=("Segoe UI",8),bd=0,anchor="w").pack(fill="x",pady=1)

    # ════════════════════════════════════════════════════════ ACHIEV TAB ═════
    def _build_achiev(self):
        t=self.theme; f=self._tabs["achiev"]
        self._achiev_canvas=tk.Canvas(f,bg=t.BG,highlightthickness=0)
        sb=ttk.Scrollbar(f,orient="vertical",command=self._achiev_canvas.yview)
        self._achiev_inner=tk.Frame(self._achiev_canvas,bg=t.BG)
        self._achiev_inner.bind("<Configure>",
            lambda e:self._achiev_canvas.configure(scrollregion=self._achiev_canvas.bbox("all")))
        self._achiev_canvas.create_window((0,0),window=self._achiev_inner,anchor="nw",
                                          width=self.WIDTH-24)
        self._achiev_canvas.configure(yscrollcommand=sb.set)
        self._achiev_canvas.pack(side="left",fill="both",expand=True,padx=(8,0),pady=8)
        sb.pack(side="right",fill="y",pady=8)

    def _refresh_achiev(self):
        t=self.theme
        for w in self._achiev_inner.winfo_children(): w.destroy()
        unlocked=db.unlocked_achievements()
        s=db.stats_overview()
        tk.Label(self._achiev_inner,text="Conquistas",bg=t.BG,fg=t.TEXT,
                 font=("Segoe UI",11,"bold")).pack(anchor="w",padx=6,pady=(4,2))
        tk.Label(self._achiev_inner,
                 text=f"{len(unlocked)} desbloqueadas",bg=t.BG,fg=t.MUTED,
                 font=("Segoe UI",8)).pack(anchor="w",padx=6,pady=(0,6))
        for key,icon,title,desc,check in pr.ACHIEVEMENTS:
            done=key in unlocked or check(s)
            card=tk.Frame(self._achiev_inner,bg=t.SURF if done else t.CARD)
            card.pack(fill="x",pady=3,padx=4)
            ci=tk.Frame(card,bg=card["bg"]); ci.pack(fill="x",padx=12,pady=8)
            tk.Label(ci,text=icon if done else "🔒",bg=card["bg"],
                     fg=t.GOLD if done else t.DIM,font=("Segoe UI",16)).pack(side="left",padx=(0,10))
            txt=tk.Frame(ci,bg=card["bg"]); txt.pack(side="left",fill="x",expand=True)
            tk.Label(txt,text=title,bg=card["bg"],fg=t.TEXT if done else t.MUTED,
                     font=("Segoe UI",9,"bold")).pack(anchor="w")
            tk.Label(txt,text=desc,bg=card["bg"],fg=t.MUTED,
                     font=("Segoe UI",8)).pack(anchor="w")

    # ════════════════════════════════════════════════════════ ACTIONS ════════
    def _apply_preset(self,i):
        t=self.theme; self.preset_idx=i; p=pr.WAVE_PRESETS[i]
        for j,b in enumerate(self._pbts):
            b.config(bg=p["hex"] if j==i else t.SURF2,
                     fg="white" if j==i else t.TEXT,
                     activebackground=p["hex"] if j==i else t.DIM)
        self._base_v.set(p["base"]); self._beat_v.set(p["beat"])
        self._desc.config(text=f"{p['range']} · {p['desc']}")
        self.engine.set_binaural(base=p["base"],beat=p["beat"])

    def _toggle(self):
        if self.timer_on or self.engine.playing:
            self._stop_session(save=True)
        else:
            self._start_session()

    def _start_session(self,target_min=None,objective=None):
        self.timer_target=(target_min if target_min is not None
                           else int(self._spin.get() or 0))*60
        self.timer_secs=0; self._notified.clear(); self.is_break=False
        self.objective=objective
        self.session_start=time.time()
        self.session_started_iso=datetime.datetime.now().isoformat()
        self.engine.set_binaural(vol=self._vol_v.get(),on=True)
        if not self.engine.playing:
            self.engine.start()
        self._pbtn.config(text="⏹   Parar",bg="#7a2a6a",activebackground="#6a1a5a")
        self.timer_on=True
        self._save_last_config()
        if db.get("invisible_mode")=="1":
            self.after(800,self._hide)
        self._tick()

    def _stop_session(self,save=True):
        if save and self.session_start:
            self._finish_and_save(completed=False)
        self.engine.stop()
        self._pbtn.config(text="▶   Iniciar",bg=self.theme.ACCENT,
                          activebackground="#2878cc")
        self.timer_on=False; self.is_break=False
        self.sleep_seq=None
        self._state_lbl.config(text="")

    def _finish_and_save(self,completed):
        dur=time.time()-self.session_start
        if dur>=60:
            p=pr.WAVE_PRESETS[self.preset_idx]
            db.save_session(self.session_started_iso,p["name"],self.objective or "",
                            dur,self.timer_target,completed,
                            self._base_v.get(),self._beat_v.get())
            # XP: 1 por minuto, bônus se completou
            gained=int(dur/60)+(20 if completed else 0)
            db.add_xp(gained)
            # uso de sons
            for sid in self.engine.active_layers():
                db.sound_add_usage(sid,dur)
            self._check_achievements()
        self.session_start=None

    def _tick(self):
        if not self.timer_on: return
        self.timer_secs+=1
        tgt=self.timer_target

        # tray label update handled separately
        if tgt>0:
            rem=tgt-self.timer_secs
            self._tlbl.config(text=self._fmt(max(rem,0)),fg=self.theme.TEXT)
            if not self.is_break:
                if self._n10.get() and rem==600 and "10" not in self._notified:
                    self._notified.add("10"); toast("Ondas Binaurais","Faltam 10 minutos ⏳")
                if self._nmid.get() and self.timer_secs==tgt//2 and "mid" not in self._notified:
                    self._notified.add("mid"); toast("Ondas Binaurais","Metade do caminho 💪")
                if self._n5.get() and rem==300 and "5" not in self._notified:
                    self._notified.add("5"); toast("Ondas Binaurais","Faltam 5 minutos 🔔")
            if rem<=0:
                self._on_timer_end(); return
        else:
            self._tlbl.config(text=self._fmt(self.timer_secs),fg=self.theme.TEXT)
        self.after(1000,self._tick)

    def _on_timer_end(self):
        # sleep mode: avançar etapa
        if self.sleep_seq is not None:
            self.sleep_idx+=1
            if self.sleep_idx < len(self.sleep_seq):
                step=self.sleep_seq[self.sleep_idx]
                pidx=next(i for i,p in enumerate(pr.WAVE_PRESETS) if p["id"]==step["preset"])
                self._apply_preset(pidx)
                self.timer_target=step["minutes"]*60
                self.timer_secs=0; self._notified.clear()
                self._state_lbl.config(text=f"Sleep: {pr.WAVE_PRESETS[pidx]['name']}")
                self.after(1000,self._tick)
                return
            else:
                # fim do sleep: fade out e fecha áudio
                toast("Sleep","Sequência concluída. Bons sonhos 🌙")
                self._finish_and_save(completed=True)
                self.engine.stop_with_fade(8.0)
                self._stop_session(save=False)
                return

        # pomodoro com pausa
        if not self.is_break and getattr(self,"_pending_break",0)>0:
            toast("Pausa!","Hora de descansar 🌿")
            self._finish_and_save(completed=True)
            self._show_rest_tip()
            self.is_break=True
            self.timer_target=self._pending_break*60
            self.timer_secs=0; self._notified.clear()
            self._state_lbl.config(text="Pausa")
            self.after(1000,self._tick)
            return

        # fim normal
        self._tlbl.config(text="✓ Pronto!",fg=self.theme.GREEN)
        p=pr.WAVE_PRESETS[self.preset_idx]
        toast("Sessão concluída! 🎉",f"{p['name']} · {int(self.timer_target/60)} min")
        self._finish_and_save(completed=True)
        if db.get("rest_reminders")=="1":
            self._show_rest_tip()
        self._stop_session(save=False)

    def _show_rest_tip(self):
        import random
        toast("Descanso",random.choice(pr.REST_TIPS))

    def _check_achievements(self):
        s=db.stats_overview()
        for key,icon,title,desc,check in pr.ACHIEVEMENTS:
            if check(s) and db.unlock(key):
                toast(f"Conquista! {icon}",title)
        # especiais
        strk=db.streak()
        hour=datetime.datetime.now().hour
        for item in pr.check_special(strk,hour):
            key,icon,title=item[0],item[1],item[2]
            if db.unlock(key):
                toast(f"Conquista! {icon}",title)

    # ── perfis / focus engine ───────────────────────────────────────────────
    def _start_profile(self,prof):
        self._show_tab("player")
        pid=prof.get("preset","theta")
        pidx=next((i for i,p in enumerate(pr.WAVE_PRESETS) if p["id"]==pid),1)
        self._apply_preset(pidx)
        if "base" in prof: self._base_v.set(prof["base"])
        if "beat" in prof: self._beat_v.set(prof["beat"])
        if "volume" in prof: self._vol_v.set(prof["volume"])
        self.engine.set_binaural(base=prof.get("base"),beat=prof.get("beat"),
                                 vol=prof.get("volume"))
        # sons
        self.engine.clear_layers()
        for sid,vol in self._sound_rows.items():
            self._sound_rows[sid]["on"].set(False)
        for sid,vol in (prof.get("sounds") or {}).items():
            self.engine.add_layer(sid); self.engine.set_layer_vol(sid,vol)
            if sid in self._sound_rows:
                self._sound_rows[sid]["on"].set(True)
                self._sound_rows[sid]["vol"].set(vol)
        # sleep?
        if prof.get("sleep_sequence"):
            self._start_sleep(); return
        self._pending_break=prof.get("break_min",0)
        self._spin.delete(0,"end"); self._spin.insert(0,str(prof.get("focus_min",25)))
        self._start_session(target_min=prof.get("focus_min",25),
                            objective=prof.get("label") or prof.get("name"))

    def _start_sleep(self):
        self._show_tab("player")
        self.sleep_seq=pr.SLEEP_SEQUENCE; self.sleep_idx=0
        step=self.sleep_seq[0]
        pidx=next(i for i,p in enumerate(pr.WAVE_PRESETS) if p["id"]==step["preset"])
        self._apply_preset(pidx)
        self._pending_break=0
        self._state_lbl.config(text=f"Sleep: {pr.WAVE_PRESETS[pidx]['name']}")
        self._start_session(target_min=step["minutes"],objective="Sono")

    # ── favoritos ───────────────────────────────────────────────────────────
    def _current_config(self):
        sounds={}
        for sid,r in self._sound_rows.items():
            if r["on"].get(): sounds[sid]=r["vol"].get()
        return {"preset":pr.WAVE_PRESETS[self.preset_idx]["id"],
                "base":self._base_v.get(),"beat":self._beat_v.get(),
                "volume":self._vol_v.get(),"focus_min":int(self._spin.get() or 25),
                "break_min":getattr(self,"_pending_break",0),"sounds":sounds}

    def _save_favorite(self):
        name=simpledialog.askstring("Favorita","Nome da sessão favorita:",parent=self)
        if name:
            db.add_favorite(name,self._current_config())
            self._refresh_favorites()

    def _refresh_favorites(self):
        t=self.theme
        for w in self._fav_frame.winfo_children(): w.destroy()
        favs=db.list_favorites()
        if not favs: return
        tk.Label(self._fav_frame,text="Favoritas",bg=t.BG,fg=t.MUTED,
                 font=("Segoe UI",8)).pack(anchor="w",pady=(4,2))
        for fav in favs[:6]:
            import json
            cfg=json.loads(fav["config"])
            row=tk.Frame(self._fav_frame,bg=t.SURF); row.pack(fill="x",pady=2)
            ri=tk.Frame(row,bg=t.SURF); ri.pack(fill="x",padx=8,pady=4)
            tk.Label(ri,text="⭐ "+fav["name"],bg=t.SURF,fg=t.TEXT,
                     font=("Segoe UI",8,"bold")).pack(side="left")
            tk.Button(ri,text="▶",bg=t.SURF,fg=t.ACCENT,font=("Segoe UI",9),bd=0,
                      cursor="hand2",activebackground=t.SURF,activeforeground=t.GREEN,
                      command=lambda c=cfg:self._start_profile(c)).pack(side="right")
            tk.Button(ri,text="🗑",bg=t.SURF,fg=t.MUTED,font=("Segoe UI",8),bd=0,
                      cursor="hand2",activebackground=t.SURF,activeforeground="#e05555",
                      command=lambda fid=fav["id"]:(db.delete_favorite(fid),
                                                    self._refresh_favorites())
                      ).pack(side="right",padx=4)

    def _restart_last(self):
        cfg=db.get_json("last_session_config",{})
        if cfg: self._start_profile(cfg)
        else: toast("Ondas Binaurais","Nenhuma sessão anterior salva.")

    def _save_last_config(self):
        db.set_json("last_session_config",self._current_config())

    def _save_protocol(self):
        name=simpledialog.askstring("Protocolo","Nome do protocolo:",parent=self)
        if name:
            db.add_protocol(name,self._current_config())
            self._refresh_protocols()

    # ── export / history ────────────────────────────────────────────────────
    def _export_csv(self):
        p=filedialog.asksaveasfilename(defaultextension=".csv",
                                       filetypes=[("CSV","*.csv")])
        if p: db.export_csv(p); toast("Export","CSV salvo.")

    def _export_json(self):
        p=filedialog.asksaveasfilename(defaultextension=".json",
                                       filetypes=[("JSON","*.json")])
        if p: db.export_json(p); toast("Export","JSON salvo.")

    def _show_history(self):
        t=self.theme
        win=tk.Toplevel(self); win.title("Histórico"); win.configure(bg=t.BG)
        win.geometry("520x440"); win.wm_attributes("-topmost",True)
        tk.Label(win,text="Histórico de Sessões",bg=t.BG,fg=t.TEXT,
                 font=("Segoe UI",12,"bold")).pack(pady=(14,8),padx=16,anchor="w")
        style=ttk.Style(win); style.theme_use("clam")
        style.configure("H.Treeview",background=t.SURF2,foreground=t.TEXT,
                        fieldbackground=t.SURF2,borderwidth=0,rowheight=24,
                        font=("Segoe UI",9))
        style.configure("H.Treeview.Heading",background=t.SURF,foreground=t.MUTED,
                        font=("Segoe UI",8,"bold"))
        cols=("Data","Preset","Objetivo","Min","✓")
        tv=ttk.Treeview(win,columns=cols,show="headings",style="H.Treeview")
        widths=[130,70,110,60,30]
        for c,w in zip(cols,widths):
            tv.heading(c,text=c); tv.column(c,width=w)
        for s in db.recent_sessions(300):
            tv.insert("","end",values=(s["ended_at"][:16],s["preset"],
                      s["objective"] or "—",round(s["duration_sec"]/60,1),
                      "✓" if s["completed"] else ""))
        sb=ttk.Scrollbar(win,orient="vertical",command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left",fill="both",expand=True,padx=(16,0),pady=(0,16))
        sb.pack(side="right",fill="y",pady=(0,16),padx=(0,16))

    # ── autostart Windows ───────────────────────────────────────────────────
    def _set_autostart(self,enable):
        try:
            import winreg
            key=winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_SET_VALUE)
            if enable:
                exe=f'pythonw "{os.path.abspath(__file__)}"'
                winreg.SetValueEx(key,"OndaBinaural",0,winreg.REG_SZ,exe)
            else:
                try: winreg.DeleteValue(key,"OndaBinaural")
                except FileNotFoundError: pass
            winreg.CloseKey(key)
        except Exception as e:
            print("autostart:",e)

    # ── wave animation ──────────────────────────────────────────────────────
    def _wave_tick(self):
        self._draw_wave(); self.after(40,self._wave_tick)

    def _draw_wave(self):
        c=self._wc; c.delete("all")
        W=c.winfo_width() or self.WIDTH; H=80
        beat=self._beat_v.get() if hasattr(self,"_beat_v") else 6
        if self.engine.playing:
            self.wave_phase+=0.07
            am=0.55+0.45*math.sin(self.wave_phase*beat*0.07)
        else: am=0.18
        col=pr.WAVE_PRESETS[self.preset_idx]["hex"]
        top=[]; fill=[]
        for x in range(0,W,2):
            tt=x/W; env=math.sin(tt*math.pi)
            y=H/2+math.sin(tt*2*math.pi*3.5+self.wave_phase)*env*am*26
            top.extend([x,y]); fill.extend([x,y])
        fill+=[W,H,0,H]
        if len(fill)>=6: c.create_polygon(fill,fill=col,outline="",stipple="gray25")
        if len(top)>=4: c.create_line(top,fill=col,width=2.5,smooth=True)

    # ── tray / stats periodic ───────────────────────────────────────────────
    def _stat_tick(self):
        # atualiza tray label
        if self._tray:
            try:
                if self.timer_on:
                    if self.timer_target>0:
                        rem=self.timer_target-self.timer_secs; lbl=self._fmt(max(rem,0))
                    else: lbl=self._fmt(self.timer_secs)
                    col=pr.WAVE_PRESETS[self.preset_idx]["hex"]
                else: lbl=""; col="#2a2a3a"
                self._tray.icon=tray_image(lbl[:5],col)
                self._tray.title=f"Ondas · {lbl}" if lbl else "Ondas Binaurais"
            except: pass
        self.after(5000,self._stat_tick)

    def _start_tray(self):
        menu=pystray.Menu(
            pystray.MenuItem("Abrir",self._show,default=True),
            pystray.MenuItem("Iniciar/Parar",lambda:self.after(0,self._toggle)),
            pystray.MenuItem("Dashboard",lambda:self.after(0,lambda:(self._show(),self._show_tab("dash")))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair",self._quit))
        self._tray=pystray.Icon("OndaBinaural",tray_image(),"Ondas Binaurais",menu)
        threading.Thread(target=self._tray.run,daemon=True).start()

    # ── window helpers ──────────────────────────────────────────────────────
    def _show(self,*_):
        self.after(0,lambda:(self.deiconify(),self.lift(),
                             self.wm_attributes("-topmost",
                             db.get("always_on_top","1")=="1")))
    def _hide(self): self.withdraw()
    def _ds(self,e): self._dx=e.x_root-self.winfo_x(); self._dy=e.y_root-self.winfo_y()
    def _dm(self,e): self.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")
    @staticmethod
    def _fmt(s): return f"{int(s)//60:02d}:{int(s)%60:02d}"
    def _quit(self,*_):
        if self.session_start: self._finish_and_save(completed=False)
        self.engine.stop()
        try: self._tray.stop()
        except: pass
        self.destroy(); os._exit(0)


if __name__=="__main__":
    App().mainloop()
