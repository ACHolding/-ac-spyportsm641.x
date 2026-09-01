#!/usr/bin/env python3
"""
CAT'S SM64 PY PORT V0.1

A clean-room, single-file late-1990s-style 3D platformer built with pygame-ce.
All geometry, graphics, particles and audio are generated at runtime.
No ROM, models, images, textures, music, sounds, or proprietary source required.
"""

from __future__ import annotations

import array
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

try:
    import pygame
except Exception as exc:  # graceful failure when pygame itself is unavailable
    print("This game requires pygame-ce (pip install pygame-ce).", file=sys.stderr)
    print(f"Import error: {exc}", file=sys.stderr)
    raise SystemExit(1)


FILES_OFF = True
ENABLE_SAVE_FILES = False
CONTINUE_AFTER_STAR = False
TITLE = "CAT'S SM64 PY PORT V0.1"
SUBTITLE = "PYTHON 3.14 • 60 FPS • FILES_OFF • FULL COURSE TOUR"
BASE_W, BASE_H = 320, 240
WINDOW_W, WINDOW_H = 960, 720
TARGET_FPS = 60
FIXED_DT = 1.0 / 60.0
SAVE_NAME = "ac_sm64py_save.json"
TAU = math.tau


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def angle_diff(a: float, b: float) -> float:
    return (b - a + math.pi) % TAU - math.pi


@dataclass(slots=True)
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __add__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x * s, self.y * s, self.z * s)

    __rmul__ = __mul__

    def __truediv__(self, s: float) -> "Vec3":
        return Vec3(self.x / s, self.y / s, self.z / s)

    def dot(self, o: "Vec3") -> float:
        return self.x * o.x + self.y * o.y + self.z * o.z

    def cross(self, o: "Vec3") -> "Vec3":
        return Vec3(self.y * o.z - self.z * o.y,
                    self.z * o.x - self.x * o.z,
                    self.x * o.y - self.y * o.x)

    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def length_xz(self) -> float:
        return math.hypot(self.x, self.z)

    def normalized(self) -> "Vec3":
        n = self.length()
        return self / n if n > 1e-8 else Vec3()

    def copy(self) -> "Vec3":
        return Vec3(self.x, self.y, self.z)


@dataclass(slots=True)
class Triangle:
    a: Vec3
    b: Vec3
    c: Vec3
    color: tuple[int, int, int]
    double_sided: bool = False


@dataclass(slots=True)
class AABB:
    center: Vec3
    half: Vec3
    kind: str = "solid"
    color: tuple[int, int, int] = (120, 150, 110)
    tag: str = ""
    moving: bool = False
    origin: Vec3 = field(default_factory=Vec3)
    axis: Vec3 = field(default_factory=Vec3)
    amplitude: float = 0.0
    speed: float = 0.0
    phase: float = 0.0
    triggered: bool = False
    last_center: Vec3 = field(default_factory=Vec3)

    @property
    def minimum(self) -> Vec3:
        return self.center - self.half

    @property
    def maximum(self) -> Vec3:
        return self.center + self.half

    def contains(self, p: Vec3, margin: float = 0.0) -> bool:
        q, h = p - self.center, self.half
        return (abs(q.x) <= h.x + margin and abs(q.y) <= h.y + margin
                and abs(q.z) <= h.z + margin)


@dataclass(slots=True)
class Settings:
    fullscreen: bool = False
    render_scale: int = 3
    resolution_mode: int = 2
    fps_cap: int = 60
    wobble: bool = True
    fog: bool = True
    draw_distance: float = 115.0
    particles: bool = True
    shadows: bool = True
    master: float = 0.75
    music: float = 0.32
    sfx: float = 0.75
    mouse_sensitivity: float = 0.003


@dataclass(slots=True)
class SaveSlot:
    stars: list[str] = field(default_factory=list)
    coins: int = 0
    best_coins: dict[str, int] = field(default_factory=dict)
    play_time: float = 0.0
    lives: int = 4

    @property
    def completion(self) -> int:
        return min(100, round(len(self.stars) * 100 / 80))


class SaveManager:
    def __init__(self) -> None:
        self.path = Path(SAVE_NAME)
        self.slots = [SaveSlot() for _ in range(4)]
        self.selected = 0
        if ENABLE_SAVE_FILES and not FILES_OFF:
            self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf8"))
            self.slots = [SaveSlot(**s) for s in raw.get("slots", [])[:4]]
            self.slots += [SaveSlot() for _ in range(4 - len(self.slots))]
        except (OSError, ValueError, TypeError):
            self.slots = [SaveSlot() for _ in range(4)]

    def save(self) -> None:
        if not ENABLE_SAVE_FILES or FILES_OFF:
            return
        try:
            payload = {"slots": [{"stars": s.stars, "coins": s.coins,
                                  "best_coins": s.best_coins,
                                  "play_time": s.play_time, "lives": s.lives}
                                 for s in self.slots]}
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf8")
        except OSError:
            pass

    def erase(self, i: int) -> None:
        self.slots[i] = SaveSlot()
        self.save()

    def copy(self, src: int, dst: int) -> None:
        s = self.slots[src]
        self.slots[dst] = SaveSlot(s.stars.copy(), s.coins,
                                   s.best_coins.copy(), s.play_time, s.lives)
        self.save()


class AudioEngine:
    """Small PCM synthesizer; mixer failure is always non-fatal."""
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = False
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        self.music_channel: Optional[pygame.mixer.Channel] = None
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
            self.enabled = pygame.mixer.get_init() is not None
            if self.enabled:
                self._build_bank()
                self.music_channel = pygame.mixer.Channel(7)
                self._start_music()
        except (pygame.error, NotImplementedError, ImportError, OSError):
            self.enabled = False

    @staticmethod
    def _tone(freqs: list[float], duration: float, kind: str = "square",
              volume: float = 0.35, slide: float = 0.0) -> pygame.mixer.Sound:
        rate = 22050
        count = max(1, int(rate * duration))
        pcm = array.array("h")
        for i in range(count):
            t = i / rate
            f = freqs[min(len(freqs) - 1, int(t / duration * len(freqs)))] + slide * t
            phase = (t * f) % 1.0
            if kind == "sine":
                wave = math.sin(phase * TAU)
            elif kind == "noise":
                wave = random.uniform(-1.0, 1.0)
            elif kind == "tri":
                wave = 1.0 - 4.0 * abs(phase - 0.5)
            else:
                wave = 1.0 if phase < 0.5 else -1.0
            env = min(1.0, i / 100) * ((count - i) / count) ** 0.55
            pcm.append(int(clamp(wave * env * volume, -1, 1) * 32767))
        return pygame.mixer.Sound(buffer=pcm.tobytes())

    def _build_bank(self) -> None:
        recipes = {
            "menu_move": ([440, 554], .07, "square", .18, 0),
            "menu_confirm": ([523, 659, 784], .18, "tri", .25, 0),
            "jump": ([330, 490], .16, "square", .22, 900),
            "double_jump": ([440, 660], .18, "tri", .25, 1200),
            "triple_jump": ([523, 784, 1047], .27, "tri", .25, 1000),
            "coin": ([988, 1319], .12, "square", .20, 0),
            "star": ([523, 659, 784, 1047, 1319], .75, "tri", .28, 0),
            "damage": ([180, 110], .22, "noise", .24, -180),
            "ground_pound": ([100, 65], .25, "noise", .38, -100),
            "door": ([110, 147], .28, "tri", .22, -40),
            "enemy_defeat": ([280, 180, 90], .22, "square", .21, -200),
            "splash": ([200, 300], .26, "noise", .18, 0),
            "pause": ([330, 262], .17, "sine", .20, 0),
            "death": ([392, 330, 262, 196], .75, "tri", .25, -50),
            "boss_hit": ([90, 140, 75], .30, "square", .30, -120),
        }
        for name, args in recipes.items():
            self.sounds[name] = self._tone(*args)

    def _start_music(self) -> None:
        # A fully generated looping arpeggio bed.
        notes = [110, 165, 220, 165, 131, 196, 262, 196,
                 147, 220, 294, 220, 165, 247, 330, 247]
        rate, beat = 22050, .18
        pcm = array.array("h")
        for n in notes:
            for i in range(int(rate * beat)):
                t = i / rate
                p = (t * n) % 1.0
                w = (1.0 - 4.0 * abs(p - .5)) * .11
                w += math.sin(TAU * n * 2 * t) * .025
                env = min(1.0, i / 150) * min(1.0, (rate * beat - i) / 600)
                pcm.append(int(w * env * 32767))
        snd = pygame.mixer.Sound(buffer=pcm.tobytes())
        snd.set_volume(self.settings.master * self.settings.music)
        if self.music_channel:
            self.music_channel.play(snd, loops=-1)

    def play(self, name: str) -> None:
        if self.enabled and name in self.sounds:
            snd = self.sounds[name]
            snd.set_volume(self.settings.master * self.settings.sfx)
            snd.play()

    def pause(self, value: bool) -> None:
        if not self.enabled:
            return
        (pygame.mixer.pause if value else pygame.mixer.unpause)()

    def update_volume(self) -> None:
        if self.music_channel:
            self.music_channel.set_volume(self.settings.master * self.settings.music)


class Camera:
    def __init__(self) -> None:
        self.pos = Vec3(0, 7, -13)
        self.target = Vec3()
        self.yaw = 0.0
        self.pitch = .28
        self.distance = 13.0
        self.zoom_level = 1
        self.free = False
        self.first_person = False
        self.shake = 0.0

    def update(self, player: "Player", world: "World", inp: "InputState", dt: float) -> None:
        if self.free:
            move = Vec3(inp.cam_x, inp.cam_y, inp.cam_z)
            sy, cy = math.sin(self.yaw), math.cos(self.yaw)
            self.pos += Vec3(move.x * cy + move.z * sy, move.y,
                             move.z * cy - move.x * sy) * (18 * dt)
            return
        self.yaw += inp.camera_turn * dt * 1.9
        if inp.recenter:
            speed = player.vel.length_xz()
            if speed > .2:
                self.yaw = math.atan2(-player.vel.x, -player.vel.z)
        if inp.zoom:
            self.zoom_level = (self.zoom_level + 1) % 3
            self.distance = (9.0, 13.0, 18.0)[self.zoom_level]
        look = player.pos + Vec3(0, 2.1, 0)
        if self.first_person:
            desired = player.pos + Vec3(0, 2.4, 0)
        else:
            cp = math.cos(self.pitch)
            desired = look + Vec3(math.sin(self.yaw) * cp,
                                  math.sin(self.pitch),
                                  math.cos(self.yaw) * cp) * self.distance
            desired = world.camera_resolve(look, desired)
        smooth = 1.0 - math.exp(-dt * 9.0)
        self.pos += (desired - self.pos) * smooth
        self.target += (look - self.target) * smooth
        self.shake = max(0.0, self.shake - dt * 3)


class Renderer:
    def __init__(self, surface: pygame.Surface, settings: Settings) -> None:
        self.surface = surface
        self.settings = settings
        self.focal = BASE_H * .86
        self.near = .12
        self.triangles_rendered = 0
        self.wireframe = False
        self.time = 0.0
        self.fog_color = (128, 174, 201)
        self.light = Vec3(-.45, .82, -.35).normalized()

    @staticmethod
    def box_triangles(box: AABB) -> Iterable[Triangle]:
        c, h = box.center, box.half
        v = [c + Vec3(x * h.x, y * h.y, z * h.z)
             for x, y, z in ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                             (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))]
        faces = ((0,3,2,1),(4,5,6,7),(0,4,7,3),
                 (1,2,6,5),(3,7,6,2),(0,1,5,4))
        shades = (.72, .86, .78, .84, 1.08, .58)
        for quad, shade in zip(faces, shades):
            col = tuple(int(clamp(ch * shade, 0, 255)) for ch in box.color)
            a,b,c_,d = (v[i] for i in quad)
            yield Triangle(a,b,c_,col)
            yield Triangle(a,c_,d,col)

    @staticmethod
    def octahedron(center: Vec3, radius: float, color: tuple[int,int,int]) -> Iterable[Triangle]:
        v = [center + Vec3(0,radius,0), center + Vec3(0,-radius,0),
             center + Vec3(radius,0,0), center + Vec3(-radius,0,0),
             center + Vec3(0,0,radius), center + Vec3(0,0,-radius)]
        for a,b,c in ((0,2,4),(0,4,3),(0,3,5),(0,5,2),
                      (1,4,2),(1,3,4),(1,5,3),(1,2,5)):
            yield Triangle(v[a],v[b],v[c],color, True)

    @staticmethod
    def cylinder(center: Vec3, radius: float, height: float,
                 color: tuple[int,int,int], sides: int = 8) -> Iterable[Triangle]:
        top, bot = center.y + height * .5, center.y - height * .5
        vt = [Vec3(center.x+math.cos(i*TAU/sides)*radius, top,
                   center.z+math.sin(i*TAU/sides)*radius) for i in range(sides)]
        vb = [Vec3(p.x,bot,p.z) for p in vt]
        ct, cb = Vec3(center.x,top,center.z), Vec3(center.x,bot,center.z)
        for i in range(sides):
            j=(i+1)%sides
            yield Triangle(vb[i],vt[i],vt[j],color)
            yield Triangle(vb[i],vt[j],vb[j],color)
            yield Triangle(ct,vt[j],vt[i],color)
            yield Triangle(cb,vb[i],vb[j],color)

    def _camera_space(self, p: Vec3, cam: Camera) -> Vec3:
        shake=math.sin(self.time*73)*cam.shake*.13
        q = p - (cam.pos+Vec3(shake,-shake*.45,0))
        yaw = math.atan2(cam.target.x-cam.pos.x, cam.target.z-cam.pos.z)
        flat = math.hypot(cam.target.x-cam.pos.x, cam.target.z-cam.pos.z)
        pitch = math.atan2(cam.target.y-cam.pos.y, max(.001, flat))
        sy, cy = math.sin(yaw), math.cos(yaw)
        x, z = q.x*cy-q.z*sy, q.x*sy+q.z*cy
        sp, cp = math.sin(pitch), math.cos(pitch)
        return Vec3(x, q.y*cp-z*sp, q.y*sp+z*cp)

    def _clip_near(self, poly: list[Vec3]) -> list[Vec3]:
        if not poly:
            return []
        out: list[Vec3] = []
        prev = poly[-1]
        prev_in = prev.z >= self.near
        for cur in poly:
            cur_in = cur.z >= self.near
            if cur_in != prev_in:
                t = (self.near - prev.z) / (cur.z - prev.z)
                out.append(prev + (cur - prev) * t)
            if cur_in:
                out.append(cur)
            prev, prev_in = cur, cur_in
        return out

    def render(self, triangles: Iterable[Triangle], cam: Camera, dt: float) -> None:
        self.time += dt
        self.triangles_rendered = 0
        draw: list[tuple[float,list[tuple[int,int]],tuple[int,int,int]]] = []
        wob = self.settings.wobble
        for tri in triangles:
            if min((tri.a-cam.pos).length(), (tri.b-cam.pos).length(),
                   (tri.c-cam.pos).length()) > self.settings.draw_distance:
                continue
            world_normal = (tri.b-tri.a).cross(tri.c-tri.a).normalized()
            center = (tri.a+tri.b+tri.c)/3
            if not tri.double_sided and world_normal.dot(cam.pos-center) <= 0:
                continue
            poly = self._clip_near([self._camera_space(p, cam) for p in (tri.a,tri.b,tri.c)])
            if len(poly) < 3:
                continue
            light = clamp(.33 + max(0.0, world_normal.dot(self.light))*.78, .26, 1.1)
            depth = sum(p.z for p in poly)/len(poly)
            fog = clamp((depth-34.0)/max(1.0,self.settings.draw_distance-34.0),0,1) if self.settings.fog else 0
            base = tuple(clamp(c*light,0,255) for c in tri.color)
            col = tuple(int(lerp(base[i],self.fog_color[i],fog)) for i in range(3))
            pts=[]
            for p in poly:
                x=BASE_W/2+p.x*self.focal/p.z
                y=BASE_H/2-p.y*self.focal/p.z
                if wob:
                    snap=1.0 if p.z<18 else 2.0
                    x=round(x/snap)*snap; y=round(y/snap)*snap
                pts.append((int(x),int(y)))
            for i in range(1,len(pts)-1):
                draw.append((depth,[pts[0],pts[i],pts[i+1]],col))
        draw.sort(key=lambda item:item[0],reverse=True)
        for _,pts,col in draw:
            if self.wireframe:
                pygame.draw.polygon(self.surface,(20,245,190),pts,1)
            else:
                pygame.draw.polygon(self.surface,col,pts)
            self.triangles_rendered += 1


@dataclass(slots=True)
class InputState:
    move_x: float = 0.0
    move_z: float = 0.0
    jump: bool = False
    jump_pressed: bool = False
    crouch: bool = False
    attack: bool = False
    attack_pressed: bool = False
    interact: bool = False
    interact_pressed: bool = False
    camera_turn: float = 0.0
    recenter: bool = False
    zoom: bool = False
    cam_x: float = 0.0
    cam_y: float = 0.0
    cam_z: float = 0.0


class InputManager:
    def __init__(self) -> None:
        self.prev_jump = False
        self.prev_attack = False
        self.prev_interact = False
        self.joystick: Optional[pygame.joystick.Joystick] = None
        self.bindings={"jump":pygame.K_SPACE,"crouch":pygame.K_LSHIFT,
                       "attack":pygame.K_LCTRL,"interact":pygame.K_f,"recenter":pygame.K_r}
        try:
            if pygame.joystick.get_count():
                self.joystick = pygame.joystick.Joystick(0)
        except pygame.error:
            pass

    def poll(self, events: list[pygame.event.Event]) -> InputState:
        k=pygame.key.get_pressed(); s=InputState()
        s.move_x=float(k[pygame.K_d])-float(k[pygame.K_a])
        s.move_z=float(k[pygame.K_w])-float(k[pygame.K_s])
        s.jump=bool(k[self.bindings["jump"]]); s.crouch=bool(k[self.bindings["crouch"]])
        s.attack=bool(k[self.bindings["attack"]])
        s.interact=bool(k[self.bindings["interact"]])
        s.camera_turn=float(k[pygame.K_e])-float(k[pygame.K_q])
        s.cam_x=float(k[pygame.K_RIGHT])-float(k[pygame.K_LEFT])
        s.cam_y=float(k[pygame.K_PAGEUP])-float(k[pygame.K_PAGEDOWN])
        s.cam_z=float(k[pygame.K_UP])-float(k[pygame.K_DOWN])
        if self.joystick:
            try:
                ax,ay=self.joystick.get_axis(0),self.joystick.get_axis(1)
                if abs(ax)>.16:s.move_x=ax
                if abs(ay)>.16:s.move_z=-ay
                s.jump=s.jump or self.joystick.get_button(0)
                s.attack=s.attack or self.joystick.get_button(2)
                s.crouch=s.crouch or self.joystick.get_button(1)
                if self.joystick.get_numbuttons()>3:s.interact=s.interact or self.joystick.get_button(3)
                if self.joystick.get_numaxes()>2:
                    rx=self.joystick.get_axis(2)
                    if abs(rx)>.2:s.camera_turn=rx
            except pygame.error: pass
        for e in events:
            if e.type==pygame.KEYDOWN:
                s.recenter|=e.key==self.bindings["recenter"]
                s.zoom|=e.key==pygame.K_z
            elif e.type==pygame.JOYBUTTONDOWN:
                s.recenter|=e.button==3; s.zoom|=e.button==4
        s.jump_pressed=s.jump and not self.prev_jump
        s.attack_pressed=s.attack and not self.prev_attack
        self.prev_jump=s.jump; self.prev_attack=s.attack
        mag=math.hypot(s.move_x,s.move_z)
        if mag>1:s.move_x/=mag;s.move_z/=mag
        return s


@dataclass(slots=True)
class Ramp:
    center: Vec3
    half: Vec3
    rise: float
    axis: str
    color: tuple[int,int,int]

    def height_at(self, x: float, z: float) -> Optional[float]:
        if abs(x-self.center.x)>self.half.x or abs(z-self.center.z)>self.half.z:
            return None
        t=((x-self.center.x)/self.half.x+1)*.5 if self.axis=="x" else ((z-self.center.z)/self.half.z+1)*.5
        return self.center.y-self.half.y+clamp(t,0,1)*self.rise

    def triangles(self) -> Iterable[Triangle]:
        x0,x1=self.center.x-self.half.x,self.center.x+self.half.x
        z0,z1=self.center.z-self.half.z,self.center.z+self.half.z
        y0=self.center.y-self.half.y; y1=y0+self.rise
        if self.axis=="z":
            a,b,c,d=Vec3(x0,y0,z0),Vec3(x1,y0,z0),Vec3(x1,y1,z1),Vec3(x0,y1,z1)
        else:
            a,b,c,d=Vec3(x0,y0,z0),Vec3(x0,y0,z1),Vec3(x1,y1,z1),Vec3(x1,y1,z0)
        yield Triangle(a,d,c,self.color); yield Triangle(a,c,b,self.color)
        bottom=(self.color[0]//2,self.color[1]//2,self.color[2]//2)
        yield Triangle(a,b,c,bottom); yield Triangle(a,c,d,bottom)


@dataclass(slots=True)
class Particle:
    pos: Vec3
    vel: Vec3
    color: tuple[int,int,int]
    life: float
    size: float=.12

    def update(self,dt:float)->bool:
        self.life-=dt; self.vel.y-=12*dt; self.pos+=self.vel*dt
        return self.life>0


@dataclass(slots=True)
class Coin:
    pos: Vec3
    kind: str="yellow"
    active: bool=True
    phase: float=0.0

    @property
    def value(self)->int:
        return {"yellow":1,"red":2,"blue":5}.get(self.kind,1)

    @property
    def color(self)->tuple[int,int,int]:
        return {"yellow":(255,212,42),"red":(245,52,46),"blue":(55,150,255)}.get(self.kind,(255,220,30))


@dataclass(slots=True)
class StarCollectible:
    pos: Vec3
    objective: str
    key: str
    active: bool=True
    locked: bool=False


@dataclass(slots=True)
class Portal:
    pos: Vec3
    target: str
    required: int=0
    radius: float=2.2
    label: str="COURSE"
    kind: str="door"
    destination: Optional[Vec3]=None


class Enemy:
    def __init__(self,pos:Vec3,kind:str="walker") -> None:
        self.pos=pos.copy(); self.spawn=pos.copy(); self.vel=Vec3()
        self.kind=kind; self.state="patrol"; self.timer=random.random()*3
        self.yaw=random.random()*TAU; self.health=1 if kind!="turret" else 2
        self.dead=False; self.radius=.72

    def update(self,world:"World",player:"Player",dt:float)->None:
        if self.dead:return
        self.timer+=dt; delta=player.pos-self.pos; dist=delta.length_xz()
        if self.kind=="flyer":
            self.pos.y=self.spawn.y+math.sin(self.timer*2.3)*1.2
            if dist<10:self.pos+=(Vec3(delta.x,0,delta.z).normalized()*2.2)*dt
        elif self.kind=="roller":
            self.state="chase" if dist<14 else "patrol"
            direction=Vec3(delta.x,0,delta.z).normalized() if self.state=="chase" else Vec3(math.sin(self.yaw),0,math.cos(self.yaw))
            self.vel+=(direction*(15 if self.state=="chase" else 5))*dt
            self.vel.x*=.985;self.vel.z*=.985
            self.pos+=Vec3(self.vel.x,0,self.vel.z)*dt
        elif self.kind=="jumper":
            if self.pos.y<=self.spawn.y+.01 and self.timer%2.2<dt:
                self.vel.y=9
            self.vel.y-=20*dt;self.pos.y=max(self.spawn.y,self.pos.y+self.vel.y*dt)
            if dist<9:self.pos+=Vec3(delta.x,0,delta.z).normalized()*1.2*dt
        elif self.kind=="turret":
            self.state="aim" if dist<18 else "idle"
            if self.state=="aim" and self.timer%2.4<dt:
                world.projectiles.append(Projectile(self.pos+Vec3(0,1,0),delta.normalized()*10,"enemy"))
        else:
            if dist<9:self.state="chase"
            elif dist>13:self.state="patrol"
            if self.state=="patrol" and self.timer%3.5<dt:self.yaw+=random.uniform(-2,2)
            direction=Vec3(delta.x,0,delta.z).normalized() if self.state=="chase" else Vec3(math.sin(self.yaw),0,math.cos(self.yaw))
            self.pos+=direction*(2.8 if self.state=="chase" else 1.25)*dt


class Boss(Enemy):
    def __init__(self,pos:Vec3)->None:
        super().__init__(pos,"boss"); self.health=6;self.radius=2.0;self.phase=1
        self.state="guard"

    def update(self,world:"World",player:"Player",dt:float)->None:
        if self.dead:return
        self.timer+=dt; delta=player.pos-self.pos; dist=delta.length_xz()
        self.phase=1 if self.health>4 else 2 if self.health>2 else 3
        if self.timer%(3.5-self.phase*.5)<dt:
            self.state="charge"; self.vel=Vec3(delta.x,0,delta.z).normalized()*(8+self.phase*2)
        if self.state=="charge":
            self.pos+=self.vel*dt; self.vel*=.985
            if self.vel.length_xz()<2:self.state="guard"
        if self.phase>=2 and self.timer%2.1<dt:
            for a in range(0,360,60 if self.phase==2 else 40):
                r=math.radians(a);world.projectiles.append(Projectile(self.pos+Vec3(0,1,0),Vec3(math.sin(r)*7,1.5,math.cos(r)*7),"enemy"))


@dataclass(slots=True)
class Projectile:
    pos: Vec3
    vel: Vec3
    owner: str
    life: float=5.0

    def update(self,dt:float)->bool:
        self.life-=dt;self.pos+=self.vel*dt
        return self.life>0


@dataclass(frozen=True,slots=True)
class Level:
    key:str
    name:str
    ground:tuple[int,int,int]
    sky:tuple[int,int,int]


LEVELS={
    "hub":Level("hub","Crown Castle Courtyard",(78,157,94),(130,183,211)),
    "battlefield":Level("battlefield","Bombard Meadow",(91,176,76),(123,190,226)),
    "fortress":Level("fortress","Whompstone Fortress",(135,145,151),(130,185,220)),
    "bay":Level("bay","Jolly Gear Bay",(75,151,130),(85,168,221)),
    "mountain":Level("mountain","Cool Summit Mountain",(187,214,220),(127,183,224)),
    "haunt":Level("haunt","Big Boo Manor",(79,76,92),(65,61,88)),
    "cavern":Level("cavern","Hazy Maze Cavern",(105,111,105),(92,104,115)),
    "fire":Level("fire","Lethal Lava Foundry",(86,78,79),(121,73,69)),
    "sand":Level("sand","Shifting Sand Ruins",(207,157,73),(236,174,99)),
    "docks":Level("docks","Dire Dire Docks",(54,120,136),(50,96,134)),
    "snow":Level("snow","Snowman Highlands",(215,232,239),(138,190,225)),
    "wet":Level("wet","Wet-Dry Works",(88,145,158),(120,178,205)),
    "tall":Level("tall","Tall Tall Highlands",(87,163,79),(122,188,219)),
    "tiny":Level("tiny","Tiny-Huge Garden",(104,172,91),(137,194,224)),
    "clock":Level("clock","Tick-Tock Tower",(142,121,83),(91,112,151)),
    "rainbow":Level("rainbow","Rainbow Sky Ride",(166,172,181),(106,161,220)),
    "slide":Level("slide","Princess Secret Slide",(171,116,148),(147,191,227)),
    "wing":Level("wing","Wing Cap Clouds",(182,190,203),(100,169,231)),
    "metal":Level("metal","Metal Cavern",(100,110,116),(83,101,112)),
    "vanish":Level("vanish","Vanish Underpass",(105,82,125),(80,70,112)),
    "boss1":Level("boss1","Dark World Arena",(74,69,78),(88,63,86)),
    "boss2":Level("boss2","Fire Sea Arena",(95,68,59),(121,64,53)),
    "boss3":Level("boss3","Sky Finale Arena",(111,103,132),(77,92,151)),
}

COURSE_ORDER=(
    "battlefield","fortress","bay","mountain","haunt","cavern","fire","sand",
    "docks","snow","wet","tall","tiny","clock","rainbow"
)
SECRET_ORDER=("slide","wing","metal","vanish")
BOSS_ORDER=("boss1","boss2","boss3")
COURSE_REQUIREMENTS=(0,1,3,5,8,10,12,15,18,22,26,30,34,38,45)



class World:
    def __init__(self,audio:AudioEngine,settings:Settings)->None:
        self.audio=audio;self.settings=settings;self.level_id="hub"
        self.name="";self.sky=(120,180,220);self.fog=(120,180,220)
        self.boxes:list[AABB]=[];self.ramps:list[Ramp]=[];self.coins:list[Coin]=[]
        self.stars:list[StarCollectible]=[];self.portals:list[Portal]=[]
        self.enemies:list[Enemy]=[];self.projectiles:list[Projectile]=[]
        self.particles:list[Particle]=[];self.checkpoints:list[Vec3]=[]
        self.water:list[AABB]=[];self.lava:list[AABB]=[];self.spawn=Vec3(0,2,0)
        self.time=0.0;self.red_collected=0;self.boss:Optional[Boss]=None
        self.load("hub")

    def add_box(self,x:float,y:float,z:float,hx:float,hy:float,hz:float,
                color:tuple[int,int,int],kind:str="solid",tag:str="",**kw)->AABB:
        b=AABB(Vec3(x,y,z),Vec3(hx,hy,hz),kind,color,tag,origin=Vec3(x,y,z),**kw)
        b.last_center=b.center.copy()
        self.boxes.append(b)
        if kind=="water":self.water.append(b)
        if kind=="lava":self.lava.append(b)
        return b

    def add_coin_ring(self,center:Vec3,radius:float,count:int,kind:str="yellow")->None:
        for i in range(count):
            a=i*TAU/count;self.coins.append(Coin(center+Vec3(math.sin(a)*radius,0,math.cos(a)*radius),kind,True,a))

    def load(self,level_id:str)->None:
        profile=LEVELS[level_id]
        self.level_id=level_id;self.name=profile.name;self.ground_color=profile.ground;self.sky=profile.sky
        self.fog=self.sky;self.boxes=[];self.ramps=[];self.coins=[];self.stars=[]
        self.portals=[];self.enemies=[];self.projectiles=[];self.particles=[]
        self.checkpoints=[];self.water=[];self.lava=[];self.red_collected=0;self.boss=None
        self.requested_level:Optional[str]=None;self.portal_near:Optional[Portal]=None;self.door_near:Optional[AABB]=None
        self.total_stars=0;self.camera_shake=0.0
        random.seed("ac-kondo-"+level_id)
        if level_id=="hub":self._build_hub()
        else:self._build_course(level_id)

    def _build_hub(self)->None:
        stone=(173,167,145);roof=(77,83,127);gold=(222,175,53);door=(117,73,49)
        self.spawn=Vec3(0,1.2,-30)
        self.add_box(0,-1,0,38,1,42,(72,151,76))
        # Castle shell: courtyard, nave, towers, gallery, basement approach.
        self.add_box(0,1.5,3,17,1.5,20,stone)
        for x in (-17,17):self.add_box(x,7,3,1.2,8,21,stone)
        self.add_box(0,9,23,18,10,1.2,stone);self.add_box(0,9,-17,18,10,1.2,stone)
        self.add_box(0,3,-16,3.0,3.3,.6,door,"door","front_door")
        for x,z in ((-16,-16),(16,-16),(-16,22),(16,22)):
            self.add_box(x,12,z,3.2,12,3.2,stone);self.add_box(x,24.5,z,4,.8,4,roof)
        self.ramps.append(Ramp(Vec3(0,2.7,5),Vec3(4.5,2.7,9),5.4,"z",(157,151,132)))
        self.add_box(0,6.3,17,11,.6,4,(152,147,132),"elevator",moving=True,
                     axis=Vec3(0,1,0),amplitude=3.2,speed=.55)
        # Courtyard fountain and stepping stones.
        self.add_box(0,.15,-27,6,.2,6,(57,139,193),"water")
        for a in range(0,360,45):
            r=math.radians(a);self.add_box(math.sin(r)*9,.2,math.cos(r)*9-27,1,.25,1,(152,150,133))
        self.add_box(0,3,-27,1,3,1,gold,"solid","fountain")
        self.coins += [Coin(Vec3(x,1,-24)) for x in range(-10,11,2)]

        # Fifteen main course doors. They are entered with the explicit INTERACT button.
        floor_positions=[
            (-13,1,-10),(-8,1,-10),(-3,1,-10),(3,1,-10),(8,1,-10),(13,1,-10),
            (-13,1,18),(-8,1,18),(-3,1,18),(3,1,18),(8,1,18),(13,1,18),
            (-10,7,15),(0,7,15),(10,7,15)
        ]
        for i,(target,req,p) in enumerate(zip(COURSE_ORDER,COURSE_REQUIREMENTS,floor_positions),1):
            x,y,z=p;paint=(65+(i*21)%150,80+(i*31)%130,110+(i*17)%130)
            self.add_box(x,y+2,z,1.8,2.6,.42,paint,"portal_frame",target)
            self.portals.append(Portal(Vec3(x,y+1,z),target,req,2.1,f"COURSE {i}: {LEVELS[target].name}","door"))

        # Secret stages use glowing warp pads; bosses use star-gated doors.
        secret_positions=((-14,7,5),(-7,7,5),(7,7,5),(14,7,5))
        for target,p in zip(SECRET_ORDER,secret_positions):
            x,y,z=p;self.add_box(x,y-.35,z,1.8,.18,1.8,(95,205,235),"portal_frame",target)
            self.portals.append(Portal(Vec3(x,y,z),target,0,2.0,LEVELS[target].name,"warp"))
        boss_positions=((-9,1,7),(0,1,7),(9,1,7));boss_req=(8,30,50)
        for target,req,p in zip(BOSS_ORDER,boss_req,boss_positions):
            x,y,z=p;self.add_box(x,y+2,z,2.1,2.8,.5,(72,53,77),"portal_frame",target)
            self.portals.append(Portal(Vec3(x,y+1,z),target,req,2.2,LEVELS[target].name,"boss door"))
        self.stars.append(StarCollectible(Vec3(0,11.5,21),"Crown of the Courtyard","hub:crown"))
        self.checkpoints=[self.spawn.copy(),Vec3(0,6,10)]

    def _build_course(self,kind:str)->None:
        palettes={
            "battlefield":((76,167,69),(101,92,61),(55,128,72)),"fortress":((139,149,154),(103,108,113),(154,155,150)),
            "bay":((66,145,127),(91,113,103),(47,84,105)),"mountain":((220,235,240),(143,168,181),(94,130,155)),
            "haunt":((83,79,97),(106,82,74),(79,60,94)),"cavern":((105,112,104),(81,88,83),(117,111,91)),
            "fire":((87,79,78),(139,80,52),(62,61,68)),"sand":((207,157,73),(177,121,59),(129,88,58)),
            "docks":((57,121,133),(91,113,103),(47,84,105)),"snow":((220,235,240),(143,168,181),(94,130,155)),
            "wet":((89,145,159),(110,119,123),(70,116,139)),"tall":((79,165,75),(101,92,61),(55,128,72)),
            "tiny":((105,173,92),(116,98,69),(64,133,76)),"clock":((147,127,88),(104,86,64),(178,147,80)),
            "rainbow":((176,180,184),(117,124,142),(210,204,173)),"slide":((182,137,166),(124,103,141),(223,194,121)),
            "wing":((176,186,204),(117,124,142),(210,204,173)),"metal":((101,110,116),(74,84,92),(139,146,151)),
            "vanish":((108,86,127),(81,69,99),(142,112,162)),"boss1":((74,69,78),(104,76,92),(62,56,72)),
            "boss2":((95,68,59),(136,72,52),(62,56,61)),"boss3":((111,103,132),(82,79,105),(165,157,190)),
        }
        ground,rock,accent=palettes[kind];self.spawn=Vec3(0,1.2,-25)

        if kind in BOSS_ORDER:
            self.add_box(0,-1,0,24,1,24,ground)
            for a in range(0,360,45):
                r=math.radians(a);self.add_box(math.sin(r)*18,1.0,math.cos(r)*18,2.8,.8,2.8,rock)
            if kind=="boss2":self.add_box(0,.05,0,9,.15,9,(237,65,22),"lava")
            self.boss=Boss(Vec3(0,1,8));self.enemies.append(self.boss)
            self.portals.append(Portal(Vec3(0,1,-20),"hub",0,2.4,"RETURN TO CASTLE","warp"))
            self.stars=[]
            self.checkpoints=[self.spawn.copy()]
            return

        if kind in ("wing","rainbow"):
            self.add_box(0,-1,-23,11,1,9,ground)
        else:
            self.add_box(0,-1,0,31,1,31,ground)

        # A unique but deterministic 3D route for every course.
        seed=sum(ord(c) for c in kind);phase=(seed%17)*.19
        for i in range(10):
            x=math.sin(i*.78+phase)*10.5;z=-18+i*4.9;y=i*1.52
            moving=i in (3,6,8) or kind in ("clock","rainbow") and i%2==1
            axis=Vec3(1,0,0) if i%2==0 else Vec3(0,1,0)
            self.add_box(x,y,z,4 if kind!="tiny" else (2.4 if i%2 else 6),.65,3,rock,
                         "moving" if moving else "solid",f"path{i}",moving=moving,axis=axis,
                         amplitude=3 if moving else 0,speed=.65+i*.04,phase=i)
            self.coins.append(Coin(Vec3(x,y+1.25,z)))
            if i in (2,5):self.coins.append(Coin(Vec3(x+1.4,y+1.25,z),"red"))
            if i in (1,7):self.enemies.append(Enemy(Vec3(x,y+1,z),("walker","flyer","roller","turret","jumper")[(i+seed)%5]))

        self.ramps.append(Ramp(Vec3(-10,2.5,-5),Vec3(5,2.5,8),5,"z",accent))
        self.add_coin_ring(Vec3(-10,6,0),4,8,"yellow")
        self.add_box(-19,.3,-10,1.2,.3,1.2,(230,65,52),"switch","lift_switch")
        self.add_box(-19,2,3,3,.5,3,accent,"elevator","lift",moving=True,axis=Vec3(0,1,0),amplitude=5,speed=.5)
        self.add_box(14,1,-13,1.5,1.5,1.5,(174,111,54),"breakable","crate")
        self.add_box(18,1,-9,1.5,1.5,1.5,(120,126,145),"push","block")
        self.add_box(17,2,-2,3,.5,3,(151,139,103),"falling","fall")
        self.add_box(18,4,6,4,.45,1.5,accent,"rotating","rotor",moving=True,axis=Vec3(0,0,1),amplitude=5,speed=.8)
        self.add_box(-20,1,15,1,1,1,(55,55,65),"cannon","cannon")
        self.checkpoints=[self.spawn.copy(),Vec3(-10,6,0),Vec3(0,12,15)]

        # Local warp pair + course exit. Local warps teleport in-map; exit returns to hub.
        warp_a=Vec3(-25,1,-22);warp_b=Vec3(23,13,20)
        for wp in (warp_a,warp_b):self.add_box(wp.x,.15 if wp.y<2 else wp.y-.85,wp.z,1.5,.12,1.5,(80,210,235),"portal_frame","warp")
        self.portals.append(Portal(warp_a,"",0,1.9,"WARP TO UPPER ROUTE","warp",warp_b))
        self.portals.append(Portal(warp_b,"",0,1.9,"WARP TO START","warp",self.spawn+Vec3(0,0,3)))
        self.portals.append(Portal(self.spawn+Vec3(5,0,0),"hub",0,1.9,"RETURN TO CASTLE","warp"))

        # Themed landmarks / hazards.
        if kind in ("fire",):
            self.add_box(0,.05,8,12,.15,8,(237,65,22),"lava")
            self.add_box(0,2,24,10,2,10,(64,61,67));self.boss=Boss(Vec3(0,5,24));self.enemies.append(self.boss)
        elif kind in ("bay","docks"):
            self.add_box(0,2,8,18,3,12,(42,111,166),"water")
            for y in (1,5,9):self.add_box(0,y,20,7,.5,5,rock)
        elif kind=="sand":
            for x,z,h in ((-18,10,8),(16,15,12),(3,24,15)):self.add_box(x,h/2,z,3,h/2,3,rock)
            self.add_box(10,.1,8,6,.2,6,(176,116,62),"lava","quicksand")
        elif kind in ("snow","mountain"):
            self.ramps += [Ramp(Vec3(12,4,12),Vec3(7,4,10),8,"z",ground)]
        elif kind in ("battlefield","tall"):
            for x,z in ((-18,13),(18,13),(-15,22),(15,24)):
                self.add_box(x,3,z,1.3,3,1.3,(93,67,43));self.add_box(x,7,z,4,2.2,4,(48,128,63))
        elif kind=="fortress":
            for i in range(5):self.add_box(-15+i*7,2+i*2,12,3,2+i*.4,3,rock)
        elif kind=="haunt":
            for x,z in ((-12,8),(0,8),(12,8),(-12,20),(0,20),(12,20)):self.add_box(x,3,z,4,3,4,rock)
        elif kind=="cavern":
            self.add_box(0,1,10,20,2,8,(70,82,78));self.add_box(-12,5,18,5,.7,5,accent)
        elif kind=="wet":
            self.add_box(0,2,12,14,3,10,(42,111,166),"water")
            for i in range(4):self.add_box(-12+i*8,3+i*2,12,2,.5,2,rock)
        elif kind=="tiny":
            self.add_box(-18,4,18,8,4,8,rock);self.add_box(18,1.2,18,1.5,1.2,1.5,accent)
        elif kind=="clock":
            for i in range(8):
                a=i*TAU/8;self.add_box(math.sin(a)*12,4+i*.7,math.cos(a)*12,3,.45,1.4,accent,"rotating",f"gear{i}",moving=True,axis=Vec3(0,0,1),amplitude=2.5,speed=.5+i*.08,phase=i)
        elif kind in ("rainbow","wing"):
            for i in range(8):
                a=i*TAU/8;self.add_box(math.sin(a)*15,5+i*.8,math.cos(a)*15,3,.55,3,rock,"moving",moving=True,axis=Vec3(0,1,0),amplitude=2,speed=.7,phase=i)
        elif kind=="slide":
            self.ramps=[]
            for i in range(7):self.ramps.append(Ramp(Vec3(math.sin(i*.7)*7,2+i*2,-15+i*7),Vec3(4,1.5,5),3,"z",accent))
        elif kind=="metal":
            self.add_box(0,1,10,18,2,8,(80,91,97));self.add_box(0,5,20,8,.5,8,accent)
        elif kind=="vanish":
            for i in range(7):self.add_box(-18+i*6,2+(i%2)*3,12,2,.35,2,accent,"moving",moving=True,axis=Vec3(0,1,0),amplitude=1.5,speed=.9,phase=i)

        # Main, hidden, eight-red-coin, and 100-coin objectives.
        summit=Vec3(math.sin(9*.78+phase)*10.5,15.7,-18+9*4.9)
        self.stars=[StarCollectible(summit,"Reach the Summit",f"{kind}:summit"),
                    StarCollectible(Vec3(-24,3,24),"Find the Hidden Star",f"{kind}:hidden",locked=False),
                    StarCollectible(Vec3(0,5,24),"Collect Eight Red Coins",f"{kind}:red8",locked=True)]
        for i in range(6):self.coins.append(Coin(Vec3(-25+i*10,1.0,25-(i%2)*5),"red"))
        for i in range(16):
            x=random.uniform(-25,25);z=random.uniform(-25,25);self.coins.append(Coin(Vec3(x,1,z),"blue" if i in (13,15) else "yellow",True,i))
        self.enemies += [Enemy(Vec3(random.uniform(-22,22),1,random.uniform(-5,22)),k) for k in ("walker","flyer","roller","turret","jumper")]
    def update(self,player:"Player",dt:float)->None:
        self.time+=dt
        for b in self.boxes:
            b.last_center=b.center.copy()
            if b.moving:
                if b.kind=="rotating":
                    a=self.time*b.speed+b.phase;b.center=b.origin+Vec3(math.sin(a)*b.amplitude,0,math.cos(a)*b.amplitude)
                else:b.center=b.origin+b.axis*(math.sin(self.time*b.speed+b.phase)*b.amplitude)
            if b.kind=="falling" and b.triggered:
                b.center.y-=dt*clamp((self.time-b.phase)*8,0,20)
        for e in self.enemies:e.update(self,player,dt)
        self.projectiles[:]=[p for p in self.projectiles if p.update(dt)]
        self.particles[:]=[p for p in self.particles if p.update(dt)]
        for s in self.stars:
            if s.key.endswith(":red8"):s.locked=self.red_collected<8

    def floor_info(self,x:float,y:float,z:float)->tuple[float,Vec3,Optional[AABB]]:
        best=-1000.0;hit=None;normal=Vec3(0,1,0)
        for b in self.boxes:
            if b.kind in ("water","lava","portal_frame") or b.center.y<-50:continue
            top=b.center.y+b.half.y
            if abs(x-b.center.x)<=b.half.x+.42 and abs(z-b.center.z)<=b.half.z+.42 and top<=y+.8 and top>best:
                best=top;hit=b
        for r in self.ramps:
            h=r.height_at(x,z)
            if h is not None and h<=y+.8 and h>best:
                best=h;hit=None
                slope=r.rise/(r.half.x*2 if r.axis=="x" else r.half.z*2)
                normal=Vec3(-slope if r.axis=="x" else 0,1,-slope if r.axis=="z" else 0).normalized()
        return best,normal,hit

    def blocked(self,p:Vec3,radius:float,height:float)->Optional[AABB]:
        for b in self.boxes:
            if b.kind in ("water","lava","portal_frame","switch","cannon") or b.center.y<-50:continue
            mn,mx=b.minimum,b.maximum
            if (p.x+radius>mn.x and p.x-radius<mx.x and p.z+radius>mn.z and p.z-radius<mx.z
                    and p.y+height>mn.y+.08 and p.y<mx.y-.08):return b
        return None

    def camera_resolve(self,start:Vec3,desired:Vec3)->Vec3:
        delta=desired-start
        for i in range(1,17):
            p=start+delta*(i/16)
            if self.blocked(p,.25,.25):return start+delta*((i-1)/16)
        return desired

    def volume_at(self,p:Vec3,kind:str)->Optional[AABB]:
        volumes=self.water if kind=="water" else self.lava
        return next((v for v in volumes if v.contains(p,.25)),None)

    def burst(self,pos:Vec3,color:tuple[int,int,int],count:int=10,power:float=5)->None:
        if not self.settings.particles:return
        for _ in range(count):
            a=random.random()*TAU;s=random.uniform(power*.25,power)
            self.particles.append(Particle(pos.copy(),Vec3(math.sin(a)*s,random.uniform(1,power),math.cos(a)*s),color,random.uniform(.35,.85),random.uniform(.08,.2)))

    def triangles(self,player:"Player") -> Iterable[Triangle]:
        for b in self.boxes:
            if b.center.y>-45:
                if b.kind in ("water","lava"):
                    top=AABB(Vec3(b.center.x,b.center.y+b.half.y,b.center.z),Vec3(b.half.x,.04,b.half.z),b.kind,b.color)
                    yield from Renderer.box_triangles(top)
                else:yield from Renderer.box_triangles(b)
        for r in self.ramps:yield from r.triangles()
        for c in self.coins:
            if c.active:
                bob=math.sin(self.time*4+c.phase)*.15
                yield from Renderer.octahedron(c.pos+Vec3(0,bob,0),.38,c.color)
        for s in self.stars:
            if s.active and not s.locked:
                bob=math.sin(self.time*2.5)*.35
                yield from Renderer.octahedron(s.pos+Vec3(0,bob,0),.78,(255,232,75))
        for e in self.enemies:
            if e.dead:continue
            if isinstance(e,Boss):
                yield from Renderer.cylinder(e.pos+Vec3(0,1.5,0),2.0,3.0,(155,52+e.phase*20,65),10)
                yield from Renderer.octahedron(e.pos+Vec3(0,3.3,0),.8,(245,176,46))
            else:
                colors={"walker":(150,82,53),"flyer":(157,67,172),"roller":(57,112,159),"turret":(103,110,120),"jumper":(221,97,55)}
                yield from Renderer.cylinder(e.pos+Vec3(0,.65,0),e.radius,1.3,colors.get(e.kind,(150,80,80)),7)
        for p in self.projectiles:yield from Renderer.octahedron(p.pos,.25,(255,74,44))
        for p in self.particles:yield from Renderer.octahedron(p.pos,p.size,p.color)
        # Player mascot: shadow, body, head and cap made only from primitives.
        if self.settings.shadows and player.on_ground:
            sh=AABB(Vec3(player.pos.x,player.pos.y+.02,player.pos.z),Vec3(.65,.025,.42),"shadow",(45,55,48))
            yield from Renderer.box_triangles(sh)
        body_col=(46,112,218) if player.damage_flash<=0 else (255,255,255)
        yield from Renderer.cylinder(player.pos+Vec3(0,1.05,0),.58,1.25,body_col,8)
        yield from Renderer.octahedron(player.pos+Vec3(0,2.05,0),.72,(235,171,119))
        cap=AABB(player.pos+Vec3(0,2.65,0),Vec3(.7,.18,.55),"cap",(224,51,61))
        yield from Renderer.box_triangles(cap)


class Player:
    RADIUS=.48
    HEIGHT=2.45
    def __init__(self,spawn:Vec3,audio:AudioEngine)->None:
        self.audio=audio;self.pos=spawn.copy();self.vel=Vec3();self.yaw=0.0
        self.state="idle";self.on_ground=False;self.ground_box:Optional[AABB]=None
        self.wall_box:Optional[AABB]=None;self.floor_normal=Vec3(0,1,0)
        self.health=8.0;self.oxygen=8.0;self.coins=0;self.lives=4
        self.jump_chain=0;self.jump_window=0.0;self.state_timer=0.0
        self.invulnerable=0.0;self.damage_flash=0.0;self.fall_start=0.0
        self.checkpoint=spawn.copy();self.dead_timer=0.0;self.last_platform=Vec3()
        self.collected_star:Optional[StarCollectible]=None

    def respawn(self,world:World)->None:
        self.pos=self.checkpoint.copy() if self.checkpoint else world.spawn.copy()
        self.vel=Vec3();self.health=8;self.oxygen=8;self.state="idle"
        self.dead_timer=0;self.invulnerable=1.5

    def damage(self,amount:float,source:Vec3,world:World)->None:
        if self.invulnerable>0 or self.state=="dead":return
        self.health-=amount;self.invulnerable=1.0;self.damage_flash=.25
        away=Vec3(self.pos.x-source.x,0,self.pos.z-source.z).normalized()
        self.vel=away*8+Vec3(0,6,0);self.state="knockback";self.state_timer=0
        self.audio.play("damage");world.burst(self.pos+Vec3(0,1,0),(255,72,65),12,6)
        if self.health<=0:self.die(world)

    def die(self,world:World)->None:
        if self.state=="dead":return
        self.state="dead";self.dead_timer=2.1;self.vel=Vec3(0,11,0);self.audio.play("death")

    def _set_jump(self,power:float,state:str,sound:str)->None:
        self.vel.y=power;self.on_ground=False;self.state=state;self.state_timer=0
        self.audio.play(sound)

    def _horizontal_move(self,world:World,delta:Vec3)->None:
        self.wall_box=None
        old=self.pos.copy();self.pos.x+=delta.x
        hit=world.blocked(self.pos,self.RADIUS,self.HEIGHT)
        if hit:
            self.pos.x=old.x;self.vel.x=0;self.wall_box=hit
        oldz=self.pos.z;self.pos.z+=delta.z
        hit=world.blocked(self.pos,self.RADIUS,self.HEIGHT)
        if hit:
            self.pos.z=oldz;self.vel.z=0;self.wall_box=hit
        # Pushable blocks move when approached with enough momentum.
        if self.wall_box and self.wall_box.kind=="push" and abs(delta.x)+abs(delta.z)>.02:
            move=Vec3(delta.x,0,delta.z)*.45
            self.wall_box.center+=move

    def _interact_world(self,world:World,inp:InputState,dt:float)->None:
        # Coins and restorative pickups.
        for c in world.coins:
            if c.active and (self.pos+Vec3(0,1,0)-c.pos).length()<1.25:
                c.active=False;self.coins+=c.value;self.health=min(8,self.health+c.value*.25)
                if c.kind=="red":world.red_collected+=1
                self.audio.play("coin");world.burst(c.pos,c.color,8,4)
        for s in world.stars:
            if s.active and not s.locked and (self.pos+Vec3(0,1,0)-s.pos).length()<1.55:
                s.active=False;self.collected_star=s;self.state="star_dance";self.vel=Vec3()
        # Doors and warps are explicit interactions: stand near one and press INTERACT.
        for p in world.portals:
            if (self.pos-p.pos).length_xz()<p.radius and abs(self.pos.y-p.pos.y)<3:
                if world.portal_near is None or (self.pos-p.pos).length_xz() < (self.pos-world.portal_near.pos).length_xz():
                    world.portal_near=p
                if inp.interact_pressed and world.total_stars>=p.required:
                    self.audio.play("door")
                    if p.destination is not None:
                        self.pos=p.destination.copy();self.checkpoint=self.pos.copy();self.vel=Vec3();self.state="idle"
                        world.burst(self.pos+Vec3(0,1,0),(100,225,255),14,5)
                        break
                    elif p.target:
                        world.requested_level=p.target
                        break
        # Switches, cannons, falling platforms and breakable crates.
        for b in world.boxes:
            close=(self.pos-b.center).length_xz()<b.half.x+1.0 and abs(self.pos.y-b.center.y)<3
            if b.kind=="switch" and close and (self.state=="ground_pound_land" or inp.attack_pressed):
                b.triggered=True;b.color=(80,210,90);self.audio.play("menu_confirm")
            if b.kind=="falling" and self.ground_box is b and not b.triggered:
                b.triggered=True;b.phase=world.time+.7
            if b.kind=="breakable" and close and (self.state in ("dive","ground_pound","ground_pound_land") or inp.attack_pressed):
                b.center.y=-100;self.audio.play("enemy_defeat");world.burst(b.center,(181,117,58),18,7)
            if b.kind=="cannon" and close and inp.attack_pressed:
                self.pos=b.center+Vec3(0,2,0);self.vel=Vec3(0,19,20);self.state="cannon_shot"
                world.camera_shake=.5;self.audio.play("ground_pound")
            if b.kind=="door" and close:
                world.door_near=b
            if b.kind=="door" and close and inp.interact_pressed:
                b.center.y=-20;self.audio.play("door")
                world.burst(self.pos+Vec3(0,1,0),(202,168,93),8,3)
        # Checkpoints are intentionally forgiving and silent after first touch.
        for cp in world.checkpoints:
            if (self.pos-cp).length()<3:self.checkpoint=cp.copy()

    def _enemy_collisions(self,world:World)->None:
        for e in world.enemies:
            if e.dead:continue
            d=self.pos-e.pos;horizontal=math.hypot(d.x,d.z)
            if horizontal>self.RADIUS+e.radius or abs((self.pos.y+.8)-(e.pos.y+.7))>2:continue
            attacking=self.state in ("dive","slide","ground_pound","ground_pound_land","cannon_shot")
            stomp=self.vel.y<0 and self.pos.y>e.pos.y+.65
            if isinstance(e,Boss):
                if attacking or stomp:
                    if e.timer-e.__dict__.get("last_hit",-9)>.65:
                        e.__dict__["last_hit"]=e.timer;e.health-=1;self.vel.y=9
                        self.audio.play("boss_hit");world.burst(e.pos+Vec3(0,2,0),(255,205,48),22,8)
                        if e.health<=0:
                            e.dead=True;world.stars.append(StarCollectible(e.pos+Vec3(0,2,0),"Defeat the Forge Guardian",f"{world.level_id}:boss"))
                else:self.damage(2,e.pos,world)
            elif stomp or attacking:
                e.dead=True;self.vel.y=max(7,self.vel.y);self.audio.play("enemy_defeat")
                world.burst(e.pos+Vec3(0,.7,0),(235,171,72),12,6)
            else:self.damage(1,e.pos,world)
        for p in world.projectiles:
            if p.owner=="enemy" and (self.pos+Vec3(0,1,0)-p.pos).length()<.85:
                p.life=0;self.damage(1,p.pos,world)

    def update(self,world:World,cam:Camera,inp:InputState,dt:float)->None:
        self.state_timer+=dt;self.invulnerable=max(0,self.invulnerable-dt)
        self.damage_flash=max(0,self.damage_flash-dt);self.jump_window=max(0,self.jump_window-dt)
        if self.state=="dead":
            self.dead_timer-=dt;self.vel.y-=18*dt;self.pos+=self.vel*dt
            if self.dead_timer<=0:
                self.lives=max(0,self.lives-1);self.respawn(world)
            return
        if self.state=="star_dance":return
        if self.state=="ledge_grab":
            if inp.jump:
                self.pos.y+=1.3;self.pos+=Vec3(math.sin(self.yaw),0,math.cos(self.yaw))*.6
                self.state="ledge_climb";self.on_ground=True;self.audio.play("jump")
            elif inp.crouch:
                self.state="jump";self.vel.y=-2
            elif self.state_timer>.85:
                self.state="jump";self.vel.y=-2
            return
        if self.state=="ground_pound_land" and self.state_timer>.22:self.state="idle"
        if self.on_ground and self.ground_box and self.ground_box.moving:
            self.pos+=self.ground_box.center-self.ground_box.last_center
        in_water=world.volume_at(self.pos+Vec3(0,1,0),"water") is not None
        in_lava=world.volume_at(self.pos+Vec3(0,.2,0),"lava") is not None
        if in_lava:
            self.damage(1,Vec3(self.pos.x-1,self.pos.y,self.pos.z),world);self.vel.y=max(self.vel.y,8)
        sy,cy=math.sin(cam.yaw),math.cos(cam.yaw)
        wish=Vec3(inp.move_x*cy+inp.move_z*sy,0,inp.move_z*cy-inp.move_x*sy)
        mag=wish.length_xz();wish=wish.normalized() if mag>.01 else Vec3()
        if in_water:
            self.state="swimming";self.on_ground=False;self.oxygen=max(0,self.oxygen-dt*.55)
            vertical=(1 if inp.jump else 0)-(1 if inp.crouch else 0)
            desired=wish*5.0+Vec3(0,vertical*4,0)
            self.vel+=(desired-self.vel)*min(1,dt*3.2);self.pos+=self.vel*dt
            if inp.jump_pressed:self.audio.play("splash");world.burst(self.pos,(105,204,242),10,4)
            if self.oxygen<=0:self.damage(.5,self.pos-Vec3(1,0,0),world)
            self._interact_world(world,inp,dt);return
        self.oxygen=min(8,self.oxygen+dt*2)
        speed=self.vel.length_xz()
        # Action selection on the ground.
        if self.on_ground:
            self.fall_start=self.pos.y
            if inp.crouch:
                self.state="crawl" if mag>.1 else "crouch"
            elif mag>.05:
                if speed>8 and wish.dot(Vec3(self.vel.x,0,self.vel.z).normalized())<-.45:self.state="skid"
                else:self.state="run" if speed>6 else "walk"
            elif self.state not in ("ground_pound_land",):self.state="idle"
            if inp.jump_pressed:
                if inp.crouch and speed>5:
                    self._set_jump(8.5,"long_jump","jump");self.vel+=wish*4
                elif inp.crouch:
                    back=Vec3(-math.sin(self.yaw),0,-math.cos(self.yaw));self.vel=back*4
                    self._set_jump(12,"backflip","double_jump")
                elif self.state=="skid":
                    self.vel=wish*8;self._set_jump(11,"side_flip","double_jump")
                else:
                    self.jump_chain=self.jump_chain+1 if self.jump_window>0 else 1
                    self.jump_chain=min(3,self.jump_chain);self.jump_window=.75
                    powers=(0,9.2,10.6,12.5);names=("","jump","double_jump","triple_jump")
                    self._set_jump(powers[self.jump_chain],names[self.jump_chain],names[self.jump_chain])
        else:
            if inp.crouch and self.vel.y<1 and self.state not in ("ground_pound","dive"):
                self.state="ground_pound";self.vel=Vec3(0,-18,0);self.audio.play("ground_pound")
            elif inp.attack_pressed and self.state not in ("ground_pound","dive"):
                forward=wish if mag>.1 else Vec3(math.sin(self.yaw),0,math.cos(self.yaw))
                self.vel=forward*11+Vec3(0,2.5,0);self.state="dive"
            elif inp.jump_pressed and self.wall_box:
                away=(self.pos-self.wall_box.center);away.y=0;away=away.normalized()
                self.vel=away*9+Vec3(0,10.5,0);self.state="wall_kick";self.audio.play("double_jump")
        # Acceleration, friction and air control.
        if mag>.05:
            target_speed=2.8 if inp.crouch else 8.5
            accel=23 if self.on_ground else 7.5
            self.vel.x+=(wish.x*target_speed-self.vel.x)*min(1,accel*dt)
            self.vel.z+=(wish.z*target_speed-self.vel.z)*min(1,accel*dt)
            self.yaw+=angle_diff(self.yaw,math.atan2(wish.x,wish.z))*min(1,dt*11)
        elif self.on_ground:
            friction=.78 if self.state=="skid" else .70
            self.vel.x*=friction**(dt*60);self.vel.z*=friction**(dt*60)
        if self.state=="dive":
            self.vel.x*=.992;self.vel.z*=.992
        if not self.on_ground:self.vel.y-=24*dt
        old_y=self.pos.y;self._horizontal_move(world,Vec3(self.vel.x*dt,0,self.vel.z*dt))
        self.pos.y+=self.vel.y*dt
        if self.vel.y>0 and world.blocked(self.pos,self.RADIUS,self.HEIGHT):
            self.pos.y=old_y;self.vel.y=0;self.state="jump"
        floor,normal,ground=world.floor_info(self.pos.x,self.pos.y,self.pos.z)
        was_ground=self.on_ground;self.on_ground=self.vel.y<=0 and self.pos.y<=floor+.16
        if self.on_ground:
            impact=-self.vel.y;self.pos.y=floor;self.vel.y=0;self.floor_normal=normal;self.ground_box=ground
            if not was_ground:
                world.burst(self.pos,(220,214,175),8 if impact<12 else 18,5)
                if self.state=="ground_pound":
                    self.state="ground_pound_land";self.state_timer=0;cam.shake=.55;self.audio.play("ground_pound")
                elif self.state=="dive":self.state="slide"
                if impact>17:self.damage(min(4,(impact-15)*.35),self.pos-Vec3(1,0,0),world)
            # Slope handling: steep slopes cause a controlled slide.
            if normal.y<.78:
                downhill=Vec3(normal.x,0,normal.z).normalized();self.vel+=downhill*9*dt;self.state="slide"
        else:
            self.ground_box=None
            # Simplified ledge catch: chest blocked while head remains clear.
            if self.vel.y<0 and self.wall_box and not world.blocked(self.pos+Vec3(0,1.4,0),self.RADIUS*.6,.4):
                self.state="ledge_grab";self.state_timer=0;self.vel=Vec3()
                if inp.jump_pressed:self.pos.y+=1.2;self.state="ledge_climb"
        if self.pos.y<-25:self.die(world)
        self._interact_world(world,inp,dt);self._enemy_collisions(world)


class HUD:
    def __init__(self)->None:
        self.font=pygame.font.Font(None,18);self.small=pygame.font.Font(None,13)

    @staticmethod
    def text(surface:pygame.Surface,font:pygame.font.Font,text:str,pos:tuple[int,int],
             color=(255,255,255),shadow=True)->None:
        if shadow:surface.blit(font.render(text,True,(20,24,37)),(pos[0]+1,pos[1]+1))
        surface.blit(font.render(text,True,color),pos)

    def draw_health(self,s:pygame.Surface,player:Player)->None:
        center=(286,31);radius=19
        for i in range(8):
            a0=-math.pi/2+i*TAU/8+.05;a1=-math.pi/2+(i+1)*TAU/8-.05
            pts=[center,(center[0]+math.cos(a0)*radius,center[1]+math.sin(a0)*radius),
                 (center[0]+math.cos(a1)*radius,center[1]+math.sin(a1)*radius)]
            col=(66,220,116) if i<math.ceil(player.health) else (54,59,75)
            pygame.draw.polygon(s,col,pts)
        pygame.draw.circle(s,(238,241,226),center,8);pygame.draw.circle(s,(35,43,62),center,5)
        if player.state=="swimming":
            pygame.draw.rect(s,(35,48,69),(255,54,61,5))
            pygame.draw.rect(s,(70,175,239),(255,54,int(61*player.oxygen/8),5))

    def draw(self,s:pygame.Surface,game:"Game")->None:
        p=game.player;slot=game.saves.slots[game.saves.selected]
        self.text(s,self.font,f"LIFE × {p.lives}",(9,8),(255,226,120))
        self.text(s,self.font,f"COIN × {p.coins}",(9,25),(255,221,58))
        self.text(s,self.font,f"STAR × {len(slot.stars)}",(9,42),(255,239,114))
        self.text(s,self.small,game.world.name,(9,61),(222,242,255))
        self.draw_health(s,p)
        portal=getattr(game.world,"portal_near",None)
        if portal and (p.pos-portal.pos).length_xz()<3:
            key=pygame.key.name(game.input.bindings["interact"]).upper()
            if game.world.total_stars<portal.required:
                msg=f"LOCKED — NEED {portal.required} STARS";col=(255,190,75)
            else:
                msg=f"PRESS {key} — {portal.label}";col=(155,235,255)
            self.text(s,self.font,msg,(BASE_W//2-self.font.size(msg)[0]//2,190),col)
        elif getattr(game.world,"door_near",None) is not None:
            key=pygame.key.name(game.input.bindings["interact"]).upper();msg=f"PRESS {key} — OPEN DOOR"
            self.text(s,self.font,msg,(BASE_W//2-self.font.size(msg)[0]//2,190),(155,235,255))
        if game.debug:
            lines=[f"FPS {game.clock.get_fps():5.1f}  TRI {game.renderer.triangles_rendered}",
                   f"XYZ {p.pos.x:6.2f} {p.pos.y:6.2f} {p.pos.z:6.2f}",
                   f"VEL {p.vel.x:5.1f} {p.vel.y:5.1f} {p.vel.z:5.1f}",
                   f"STATE {p.state}  GROUND {p.on_ground}",
                   f"YAW {math.degrees(p.yaw):5.1f} PITCH {math.degrees(game.camera.pitch):4.1f}",
                   f"CAM {game.camera.pos.x:4.0f},{game.camera.pos.y:4.0f},{game.camera.pos.z:4.0f}",
                   f"LEVEL {game.world.level_id}  STARS {len(slot.stars)}"]
            pygame.draw.rect(s,(9,13,22,),(3,78,194,82))
            for i,line in enumerate(lines):self.text(s,self.small,line,(7,81+i*11),(135,255,201),False)


class Menu:
    def __init__(self,items:list[str],x:int=BASE_W//2-70,y:int=82,width:int=140)->None:
        self.items=items;self.x=x;self.y=y;self.width=width;self.selected=0
        self.font=pygame.font.Font(None,20);self.last_move=0

    def rect(self,i:int)->pygame.Rect:return pygame.Rect(self.x,self.y+i*23,self.width,19)

    def event(self,e:pygame.event.Event,audio:AudioEngine)->Optional[str]:
        old=self.selected
        if e.type==pygame.KEYDOWN:
            if e.key in (pygame.K_UP,pygame.K_w):self.selected=(self.selected-1)%len(self.items)
            elif e.key in (pygame.K_DOWN,pygame.K_s):self.selected=(self.selected+1)%len(self.items)
            elif e.key in (pygame.K_RETURN,pygame.K_SPACE):audio.play("menu_confirm");return self.items[self.selected]
        elif e.type==pygame.MOUSEMOTION:
            mx,my=e.pos;mx=mx*BASE_W/max(1,pygame.display.get_surface().get_width());my=my*BASE_H/max(1,pygame.display.get_surface().get_height())
            for i in range(len(self.items)):
                if self.rect(i).collidepoint(mx,my):self.selected=i
        elif e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
            mx,my=e.pos;mx=mx*BASE_W/max(1,pygame.display.get_surface().get_width());my=my*BASE_H/max(1,pygame.display.get_surface().get_height())
            for i in range(len(self.items)):
                if self.rect(i).collidepoint(mx,my):self.selected=i;audio.play("menu_confirm");return self.items[i]
        elif e.type==pygame.JOYHATMOTION:
            if e.value[1]>0:self.selected=(self.selected-1)%len(self.items)
            elif e.value[1]<0:self.selected=(self.selected+1)%len(self.items)
        elif e.type==pygame.JOYBUTTONDOWN and e.button==0:
            audio.play("menu_confirm");return self.items[self.selected]
        if old!=self.selected:audio.play("menu_move")
        return None

    def draw(self,s:pygame.Surface)->None:
        for i,item in enumerate(self.items):
            r=self.rect(i);selected=i==self.selected
            pygame.draw.rect(s,(11,19,42) if not selected else (33,92,138),r,border_radius=3)
            pygame.draw.rect(s,(61,197,235) if selected else (72,85,112),r,1,border_radius=3)
            label=self.font.render(item,True,(255,245,190) if selected else (220,230,242))
            s.blit(label,(r.centerx-label.get_width()//2,r.y+2))


class Game:
    def __init__(self)->None:
        if os.environ.get("AC_SM64PY_SMOKE"):
            os.environ.setdefault("SDL_VIDEODRIVER","dummy")
            os.environ.setdefault("SDL_AUDIODRIVER","dummy")
        try:
            try:pygame.mixer.pre_init(22050,-16,1,512)
            except (pygame.error,NotImplementedError,ImportError):pass
            pygame.init()
            if not pygame.font.get_init():pygame.font.init()
        except Exception as exc:
            print(f"pygame could not initialize: {exc}",file=sys.stderr);raise SystemExit(1)
        self.settings=Settings();self.window:pygame.Surface
        self._apply_window()
        pygame.display.set_caption(TITLE+" — [C] AC HOLDINGS 1999–2026")
        self.internal=pygame.Surface((BASE_W,BASE_H)).convert()
        self.clock=pygame.time.Clock();self.audio=AudioEngine(self.settings)
        self.saves=SaveManager();self.world=World(self.audio,self.settings)
        self.player=Player(self.world.spawn,self.audio);self.camera=Camera()
        self.renderer=Renderer(self.internal,self.settings);self.hud=HUD();self.input=InputManager()
        self.running=True;self.state="title";self.return_state="title";self.accumulator=0.0
        self.debug=False;self.collision_debug=False;self.fade=255.0;self.star_timer=0.0
        self.option_index=0;self.file_message="SELECT A FILE";self.file_message_timer=0.0
        self.controls_index=0;self.rebinding:Optional[str]=None
        self.intro_timer=0.0;self.intro_duration=8.5
        self.title_menu=Menu(["PLAY GAME","FILE SELECT","OPTIONS","CONTROLS","ABOUT","EXIT"],90,82,140)
        self.file_menu=Menu(["NEW / LOAD","COPY","ERASE","BACK"],90,142,140)
        self.pause_menu=Menu(["RESUME","CONTROLS","CAMERA SETTINGS","AUDIO SETTINGS",
                              "VIDEO SETTINGS","RESTART LEVEL","RETURN TO HUB",
                              "RETURN TO TITLE","QUIT"],84,27,152)
        self.big=pygame.font.Font(None,32);self.medium=pygame.font.Font(None,20)
        self.small=pygame.font.Font(None,14);self.smoke_frames=0

    def _apply_window(self)->None:
        flags=pygame.FULLSCREEN if self.settings.fullscreen else pygame.RESIZABLE
        size=(WINDOW_W,WINDOW_H) if self.settings.fullscreen else (BASE_W*self.settings.render_scale,BASE_H*self.settings.render_scale)
        try:self.window=pygame.display.set_mode(size,flags,vsync=1)
        except (pygame.error,TypeError):
            try:self.window=pygame.display.set_mode(size,flags)
            except pygame.error as exc:
                print(f"Display initialization failed: {exc}",file=sys.stderr);raise SystemExit(1)

    def start_game(self)->None:
        self.world.load("hub");self.world.total_stars=len(self.saves.slots[self.saves.selected].stars)
        self._apply_progress()
        self.player=Player(self.world.spawn,self.audio);self.player.lives=self.saves.slots[self.saves.selected].lives
        self.camera=Camera();self.state="intro";self.intro_timer=0.0;self.fade=255;self.audio.pause(False)

    def change_level(self,level_id:str)->None:
        self.world.load(level_id);self.world.total_stars=len(self.saves.slots[self.saves.selected].stars)
        self._apply_progress()
        self.player.pos=self.world.spawn.copy();self.player.checkpoint=self.world.spawn.copy()
        self.player.vel=Vec3();self.player.state="idle";self.player.on_ground=False
        self.camera.pos=self.player.pos+Vec3(0,7,-13);self.fade=255

    def _apply_progress(self)->None:
        completed=set(self.saves.slots[self.saves.selected].stars)
        for star in self.world.stars:star.active=star.key not in completed
        if self.world.boss and f"{self.world.level_id}:boss" in completed:self.world.boss.dead=True

    def _menu_action(self,action:str)->None:
        if self.state=="title":
            if action=="PLAY GAME":self.start_game()
            elif action=="FILE SELECT":self.state="files";self.fade=180
            elif action=="OPTIONS":self.return_state="title";self.state="options";self.fade=180
            elif action=="CONTROLS":self.return_state="title";self.state="controls";self.rebinding=None;self.fade=180
            elif action=="ABOUT":self.return_state="title";self.state="about";self.fade=180
            elif action=="EXIT":self.running=False
        elif self.state=="files":
            i=self.saves.selected
            if action=="NEW / LOAD":self.start_game()
            elif action=="COPY":
                dst=(i+1)%4;self.saves.copy(i,dst);self.file_message=f"COPIED FILE {chr(65+i)} TO {chr(65+dst)}";self.file_message_timer=2
            elif action=="ERASE":
                self.saves.erase(i);self.file_message=f"ERASED FILE {chr(65+i)}";self.file_message_timer=2
            elif action=="BACK":self.state="title";self.fade=180
        elif self.state=="pause":
            if action=="RESUME":self.state="playing";self.audio.pause(False)
            elif action=="CONTROLS":self.return_state="pause";self.state="controls";self.rebinding=None
            elif action in ("CAMERA SETTINGS","AUDIO SETTINGS","VIDEO SETTINGS"):
                self.return_state="pause";self.state="options"
                self.option_index={"VIDEO SETTINGS":0,"CAMERA SETTINGS":9,"AUDIO SETTINGS":10}[action]
            elif action=="RESTART LEVEL":self.change_level(self.world.level_id);self.state="playing";self.audio.pause(False)
            elif action=="RETURN TO HUB":self.change_level("hub");self.state="playing";self.audio.pause(False)
            elif action=="RETURN TO TITLE":self.state="title";self.audio.pause(False);self.fade=255
            elif action=="QUIT":self.running=False

    def _option_rows(self)->list[tuple[str,str]]:
        s=self.settings
        res=("160 × 120","256 × 192","320 × 240")[s.resolution_mode]
        return [("FULLSCREEN","ON" if s.fullscreen else "OFF"),("WINDOW SCALE",f"{s.render_scale}×"),
                ("INTERNAL RESOLUTION",res),("FPS CAP",str(s.fps_cap)),
                ("VERTEX WOBBLE","ON" if s.wobble else "OFF"),("FOG","ON" if s.fog else "OFF"),
                ("DRAW DISTANCE",str(int(s.draw_distance))),("PARTICLES","ON" if s.particles else "OFF"),
                ("SHADOWS","ON" if s.shadows else "OFF"),("MOUSE SENSITIVITY",f"{s.mouse_sensitivity:.3f}"),
                ("MASTER VOLUME",f"{int(s.master*100)}%"),("MUSIC VOLUME",f"{int(s.music*100)}%"),
                ("SFX VOLUME",f"{int(s.sfx*100)}%"),("CONTROLLER MAP","AUTO / STANDARD"),("BACK","")]

    def _adjust_option(self,d:int)->None:
        s=self.settings;i=self.option_index
        if i==0:s.fullscreen=not s.fullscreen;self._apply_window()
        elif i==1:s.render_scale=int(clamp(s.render_scale+d,2,4));self._apply_window()
        elif i==2:s.resolution_mode=(s.resolution_mode+d)%3
        elif i==3:
            vals=(30,60,120);s.fps_cap=vals[(vals.index(s.fps_cap)+d)%len(vals)]
        elif i==4:s.wobble=not s.wobble
        elif i==5:s.fog=not s.fog
        elif i==6:s.draw_distance=clamp(s.draw_distance+d*15,55,160)
        elif i==7:s.particles=not s.particles
        elif i==8:s.shadows=not s.shadows
        elif i==9:s.mouse_sensitivity=clamp(s.mouse_sensitivity+d*.001,.001,.012)
        elif i==10:s.master=clamp(s.master+d*.05,0,1)
        elif i==11:s.music=clamp(s.music+d*.05,0,1)
        elif i==12:s.sfx=clamp(s.sfx+d*.05,0,1)
        self.audio.update_volume();self.audio.play("menu_move")

    def _event_options(self,e:pygame.event.Event)->None:
        rows=self._option_rows();old=self.option_index
        if e.type==pygame.KEYDOWN:
            if e.key in (pygame.K_UP,pygame.K_w):self.option_index=(self.option_index-1)%len(rows)
            elif e.key in (pygame.K_DOWN,pygame.K_s):self.option_index=(self.option_index+1)%len(rows)
            elif e.key in (pygame.K_LEFT,pygame.K_a):self._adjust_option(-1)
            elif e.key in (pygame.K_RIGHT,pygame.K_d,pygame.K_RETURN,pygame.K_SPACE):
                if self.option_index==len(rows)-1:self.state=self.return_state
                else:self._adjust_option(1)
            elif e.key==pygame.K_ESCAPE:self.state=self.return_state
        elif e.type in (pygame.MOUSEMOTION,pygame.MOUSEBUTTONDOWN):
            mx,my=e.pos;my=my*BASE_H/max(1,self.window.get_height())
            idx=int((my-42)//12)
            if 0<=idx<len(rows):
                self.option_index=idx
                if e.type==pygame.MOUSEBUTTONDOWN:
                    if idx==len(rows)-1:self.state=self.return_state
                    else:self._adjust_option(1 if e.button==1 else -1)
        if old!=self.option_index:self.audio.play("menu_move")

    def handle_events(self,events:list[pygame.event.Event])->None:
        for e in events:
            if e.type==pygame.QUIT:self.running=False;continue
            if e.type==pygame.KEYDOWN and e.key==pygame.K_F3:self.debug=not self.debug
            if e.type==pygame.KEYDOWN and e.key==pygame.K_F4:self.renderer.wireframe=not self.renderer.wireframe
            if e.type==pygame.KEYDOWN and e.key==pygame.K_F5:self.camera.free=not self.camera.free
            if e.type==pygame.KEYDOWN and e.key==pygame.K_F6:self.collision_debug=not self.collision_debug
            if self.state=="title":
                a=self.title_menu.event(e,self.audio)
                if a:self._menu_action(a)
            elif self.state=="files":
                if e.type==pygame.KEYDOWN and e.key in (pygame.K_LEFT,pygame.K_a):self.saves.selected=(self.saves.selected-1)%4;self.audio.play("menu_move")
                elif e.type==pygame.KEYDOWN and e.key in (pygame.K_RIGHT,pygame.K_d):self.saves.selected=(self.saves.selected+1)%4;self.audio.play("menu_move")
                elif e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
                    mx,my=e.pos;mx*=BASE_W/max(1,self.window.get_width());my*=BASE_H/max(1,self.window.get_height())
                    if 18<=my<=119:
                        idx=int(mx//80)
                        if 0<=idx<4:self.saves.selected=idx;self.audio.play("menu_move")
                a=self.file_menu.event(e,self.audio)
                if a:self._menu_action(a)
                if e.type==pygame.KEYDOWN and e.key==pygame.K_ESCAPE:self.state="title"
            elif self.state=="options":self._event_options(e)
            elif self.state=="controls":
                actions=("jump","crouch","attack","interact","recenter")
                if self.rebinding and e.type==pygame.KEYDOWN:
                    if e.key==pygame.K_ESCAPE:self.rebinding=None
                    else:self.input.bindings[self.rebinding]=e.key;self.rebinding=None;self.audio.play("menu_confirm")
                elif e.type==pygame.KEYDOWN:
                    if e.key in (pygame.K_UP,pygame.K_w):self.controls_index=(self.controls_index-1)%6;self.audio.play("menu_move")
                    elif e.key in (pygame.K_DOWN,pygame.K_s):self.controls_index=(self.controls_index+1)%6;self.audio.play("menu_move")
                    elif e.key in (pygame.K_RETURN,pygame.K_SPACE):
                        if self.controls_index==5:self.state=self.return_state
                        else:self.rebinding=actions[self.controls_index]
                        self.audio.play("menu_confirm")
                    elif e.key==pygame.K_ESCAPE:self.state=self.return_state
                elif e.type==pygame.MOUSEBUTTONDOWN and e.button==1:
                    mx,my=e.pos;my*=BASE_H/max(1,self.window.get_height());idx=int((my-77)//21)
                    if 0<=idx<6:
                        self.controls_index=idx
                        if idx==5:self.state=self.return_state
                        else:self.rebinding=actions[idx]
            elif self.state=="about":
                if (e.type==pygame.KEYDOWN and e.key in (pygame.K_ESCAPE,pygame.K_RETURN,pygame.K_SPACE)) or e.type==pygame.MOUSEBUTTONDOWN:
                    self.state=self.return_state;self.audio.play("menu_confirm")
            elif self.state=="intro":
                if (e.type==pygame.KEYDOWN and e.key in (pygame.K_ESCAPE,pygame.K_RETURN,pygame.K_SPACE,self.input.bindings["interact"])) or (e.type==pygame.JOYBUTTONDOWN and e.button in (0,3)):
                    self.state="playing";self.fade=170;self.audio.play("menu_confirm")
            elif self.state=="playing":
                if e.type==pygame.KEYDOWN and e.key==pygame.K_ESCAPE:
                    self.state="pause";self.audio.play("pause");self.audio.pause(True)
                elif e.type==pygame.KEYDOWN and e.key==pygame.K_v:self.camera.first_person=not self.camera.first_person
                elif e.type==pygame.MOUSEMOTION and pygame.mouse.get_pressed()[2]:
                    self.camera.yaw-=e.rel[0]*self.settings.mouse_sensitivity
                    self.camera.pitch=clamp(self.camera.pitch-e.rel[1]*self.settings.mouse_sensitivity,-.15,1.05)
            elif self.state=="pause":
                if e.type==pygame.KEYDOWN and e.key==pygame.K_ESCAPE:
                    self.state="playing";self.audio.pause(False)
                else:
                    a=self.pause_menu.event(e,self.audio)
                    if a:self._menu_action(a)

    def _trigger_star(self,star:StarCollectible)->None:
        slot=self.saves.slots[self.saves.selected]
        if star.key not in slot.stars:slot.stars.append(star.key)
        slot.coins+=self.player.coins;slot.best_coins[self.world.level_id]=max(self.player.coins,slot.best_coins.get(self.world.level_id,0))
        slot.lives=self.player.lives;self.saves.save();self.world.total_stars=len(slot.stars)
        self.last_star_name=star.objective
        self.star_timer=2.8;self.state="star";self.audio.play("star");self.fade=80

    def update(self,inp:InputState,dt:float)->None:
        self.fade=max(0,self.fade-dt*260);self.file_message_timer=max(0,self.file_message_timer-dt)
        if self.state=="title":
            self.world.time+=dt;self.camera.target=Vec3(0,8,2)
            a=self.world.time*.17;self.camera.pos=Vec3(math.sin(a)*38,14,math.cos(a)*38)
        elif self.state=="intro":
            self.intro_timer+=dt;self.world.time+=dt
            t=self.intro_timer
            self.camera.target=Vec3(0,5.5,3)
            if t<3.4:
                a=-1.1+t*.23;self.camera.pos=Vec3(math.sin(a)*34,13+math.sin(t*.7)*2,math.cos(a)*34-2)
            else:
                u=min(1.0,(t-3.4)/max(.01,self.intro_duration-3.4));a=-.3+u*1.55
                self.camera.pos=Vec3(math.sin(a)*25,10+u*5,math.cos(a)*25+5)
                self.camera.target=Vec3(0,4+u*2,-2+u*9)
            if self.intro_timer>=self.intro_duration:
                self.state="playing";self.fade=160
        elif self.state=="playing":
            slot=self.saves.slots[self.saves.selected];slot.play_time+=dt
            self.world.total_stars=len(slot.stars);self.world.portal_near=None;self.world.door_near=None
            self.world.update(self.player,dt);self.player.update(self.world,self.camera,inp,dt)
            self.camera.update(self.player,self.world,inp,dt)
            if self.world.requested_level:self.change_level(self.world.requested_level)
            if self.player.coins>=100 and not any(s.key==f"{self.world.level_id}:100" for s in self.world.stars):
                self.world.stars.append(StarCollectible(self.player.pos+Vec3(0,2,0),"Collect 100 Coins",f"{self.world.level_id}:100"))
            if self.player.collected_star:
                star=self.player.collected_star;self.player.collected_star=None;self._trigger_star(star)
        elif self.state=="star":
            self.star_timer-=dt;self.world.time+=dt
            if self.star_timer<=0:
                if CONTINUE_AFTER_STAR:
                    self.player.state="idle";self.state="playing"
                else:self.change_level("hub");self.state="playing"

    def _gradient(self,top:tuple[int,int,int],bottom:tuple[int,int,int])->None:
        for y in range(0,BASE_H,3):
            t=y/(BASE_H-1);col=tuple(int(lerp(top[i],bottom[i],t)) for i in range(3))
            pygame.draw.rect(self.internal,col,(0,y,BASE_W,3))

    def _draw_3d(self,title_mode:bool=False)->None:
        sky=self.world.sky;top=tuple(max(0,c-35) for c in sky);bottom=tuple(min(255,c+25) for c in sky)
        self._gradient(top,bottom);self.renderer.fog_color=self.world.fog
        tris=self.world.triangles(self.player)
        if title_mode:
            logo=list(Renderer.octahedron(Vec3(0,14,2),2.2,(255,218,62)))
            tris=iter(list(tris)+logo)
        self.renderer.render(tris,self.camera,FIXED_DT)
        if self.player.damage_flash>0:
            flash=pygame.Surface((BASE_W,BASE_H),pygame.SRCALPHA);flash.fill((255,55,45,80));self.internal.blit(flash,(0,0))

    def _panel(self,rect:pygame.Rect,alpha:int=210)->None:
        p=pygame.Surface(rect.size,pygame.SRCALPHA);p.fill((8,16,36,alpha));pygame.draw.rect(p,(81,205,239,230),p.get_rect(),1,border_radius=5);self.internal.blit(p,rect)

    def _center_text(self,text:str,y:int,font:pygame.font.Font,color=(255,255,255))->None:
        img=font.render(text,True,color);shadow=font.render(text,True,(18,24,40))
        x=BASE_W//2-img.get_width()//2;self.internal.blit(shadow,(x+2,y+2));self.internal.blit(img,(x,y))

    def draw_intro(self)->None:
        self._draw_3d()
        t=self.intro_timer
        # Procedural camera-guide flyover: cloud, rider and camera are primitive shapes only.
        cx=int(258+math.sin(t*1.6)*10);cy=int(42+math.sin(t*2.2)*4)
        for ox,oy,r in ((-15,4,10),(-4,0,13),(9,4,11),(18,7,8)):
            pygame.draw.circle(self.internal,(245,248,244),(cx+ox,cy+oy),r)
            pygame.draw.circle(self.internal,(191,209,218),(cx+ox,cy+oy),r,1)
        pygame.draw.circle(self.internal,(237,205,78),(cx,cy-12),7)
        pygame.draw.rect(self.internal,(58,105,62),(cx-6,cy-12,12,9),border_radius=3)
        pygame.draw.rect(self.internal,(35,40,49),(cx-17,cy-18,12,8),border_radius=2)
        pygame.draw.circle(self.internal,(101,225,244),(cx-17,cy-14),3,1)
        tag=self.small.render("LAKITU CAM",True,(255,239,143));self.internal.blit(tag,(cx-tag.get_width()//2,cy+17))
        if t<4.6:
            veil=pygame.Surface((BASE_W,BASE_H),pygame.SRCALPHA);veil.fill((6,10,22,75));self.internal.blit(veil,(0,0))
            panel=pygame.Rect(42,45,236,132);self._panel(panel,232)
            self._center_text("DEAR MARIO,",58,self.medium,(255,231,129))
            lines=("THE CASTLE DOORS ARE OPEN AGAIN.","EXPLORE THE COURSES, FIND STARS,",
                   "USE F TO ENTER DOORS AND WARPS,","AND SEE WHAT WAITS AT THE TOP.")
            for i,line in enumerate(lines):self._center_text(line,88+i*17,self.small,(222,239,248))
            self._center_text("— YOUR FRIEND AT THE CASTLE",158,self.small,(255,218,177))
        else:
            self._center_text("LAKITU CAMERA TOUR",12,self.medium,(255,232,128))
            self._center_text("CASTLE APPROACH",31,self.small,(181,229,247))
        self._center_text("ENTER / SPACE / F TO SKIP",222,self.small,(173,218,236))

    def draw_title(self)->None:
        self._draw_3d(True)
        veil=pygame.Surface((BASE_W,BASE_H),pygame.SRCALPHA);veil.fill((5,11,30,82));self.internal.blit(veil,(0,0))
        self._center_text(TITLE,14,self.big,(255,229,91));self._center_text(SUBTITLE,45,self.small,(147,232,255))
        self.title_menu.draw(self.internal);self._center_text("CLEAN-ROOM PROCEDURAL 3D ENGINE",225,self.small,(176,199,219))

    def draw_files(self)->None:
        self._gradient((28,52,87),(8,17,40));self._center_text("FILE SELECT",7,self.big,(255,229,91))
        for i,slot in enumerate(self.saves.slots):
            r=pygame.Rect(4+i*79,39,75,86);sel=i==self.saves.selected
            pygame.draw.rect(self.internal,(32,83,126) if sel else (12,27,55),r,border_radius=4)
            pygame.draw.rect(self.internal,(88,221,250) if sel else (64,77,107),r,2 if sel else 1,border_radius=4)
            self._slot_text(f"FILE {chr(65+i)}",r.x+9,r.y+7,(255,235,129) if sel else (220,230,242))
            self._slot_text("NEW" if not slot.stars and slot.play_time<1 else f"★ {len(slot.stars):02d}",r.x+9,r.y+26)
            self._slot_text(f"COIN {slot.coins:04d}",r.x+9,r.y+41)
            mins=int(slot.play_time)//60;secs=int(slot.play_time)%60
            self._slot_text(f"{mins:03d}:{secs:02d}",r.x+9,r.y+56)
            self._slot_text(f"{slot.completion:3d}%",r.x+9,r.y+70,(119,238,175))
        self.file_menu.draw(self.internal)
        msg=self.file_message if self.file_message_timer>0 else "← / → SELECT SLOT"
        self._center_text(msg,228,self.small,(155,221,244))

    def _slot_text(self,text:str,x:int,y:int,color=(226,233,242))->None:
        self.internal.blit(self.small.render(text,True,color),(x,y))

    def draw_options(self)->None:
        self._gradient((24,53,84),(8,17,39));self._center_text("OPTIONS",8,self.big,(255,229,91))
        rows=self._option_rows()
        for i,(name,value) in enumerate(rows):
            y=42+i*12;sel=i==self.option_index
            if sel:pygame.draw.rect(self.internal,(35,91,128),(38,y-1,244,12),border_radius=2)
            col=(255,239,157) if sel else (213,228,240)
            self.internal.blit(self.small.render(name,True,col),(45,y))
            if value:
                img=self.small.render("‹  "+value+"  ›",True,(125,233,255));self.internal.blit(img,(273-img.get_width(),y))
        self._center_text("ARROWS / MOUSE TO CHANGE",227,self.small,(139,199,220))

    def draw_controls(self)->None:
        self._gradient((25,52,84),(8,16,37));self._center_text("CONTROLS",10,self.big,(255,229,91));self._panel(pygame.Rect(35,43,250,181))
        self._center_text("WASD / LEFT STICK — MOVE   •   Q/E — CAMERA",52,self.small,(180,222,239))
        actions=(("JUMP / WALL KICK","jump"),("CROUCH / GROUND POUND","crouch"),
                 ("DIVE / ATTACK","attack"),("INTERACT / ENTER DOOR","interact"),("RECENTER CAMERA","recenter"))
        for i,(label,key) in enumerate(actions):
            y=77+i*21;sel=i==self.controls_index
            if sel:pygame.draw.rect(self.internal,(35,91,128),(48,y-3,224,17),border_radius=3)
            name="PRESS A KEY…" if self.rebinding==key else pygame.key.name(self.input.bindings[key]).upper()
            self.internal.blit(self.small.render(label,True,(255,238,145) if sel else (214,232,241)),(55,y))
            img=self.small.render(name,True,(119,235,255));self.internal.blit(img,(264-img.get_width(),y))
        y=77+5*21;sel=self.controls_index==5
        if sel:pygame.draw.rect(self.internal,(35,91,128),(48,y-3,224,17),border_radius=3)
        self._center_text("BACK",y,self.small,(255,238,145) if sel else (214,232,241))
        self._center_text("GAMEPAD: A JUMP • X ATTACK • Y INTERACT",197,self.small,(158,207,226))
        self._center_text("F3 INFO • F4 WIREFRAME • F5 FREECAM • F6 COLLISION",211,self.small,(139,191,211))

    def draw_about(self)->None:
        self._gradient((30,54,84),(8,16,37));self._center_text("ABOUT",10,self.big,(255,229,91));self._panel(pygame.Rect(30,50,260,151))
        lines=("CAT'S SM64 PY PORT V0.1","A CLEAN-ROOM ORIGINAL 3D PLATFORMER","PYTHON 3.14 + PYGAME-CE • 60 FPS",
               "ALL GEOMETRY, ART, PARTICLES AND AUDIO","ARE GENERATED PROCEDURALLY AT RUNTIME.","NO ROM, EXTRACTED ASSETS OR EXTERNAL FILES.",
               "[C] AC HOLDINGS / AC KONDO 1999–2026")
        for i,line in enumerate(lines):
            col=(255,231,132) if i in (0,6) else (211,232,242);self._center_text(line,61+i*18,self.small,col)
        self._center_text("PRESS ENTER, ESC, OR CLICK TO RETURN",224,self.small,(142,215,239))

    def draw_pause(self)->None:
        self._draw_3d();veil=pygame.Surface((BASE_W,BASE_H),pygame.SRCALPHA);veil.fill((4,8,21,170));self.internal.blit(veil,(0,0))
        self._center_text("PAUSED",5,self.medium,(255,230,108));self.pause_menu.draw(self.internal)

    def draw_star(self)->None:
        self._draw_3d();veil=pygame.Surface((BASE_W,BASE_H),pygame.SRCALPHA);veil.fill((10,15,35,110));self.internal.blit(veil,(0,0))
        scale=1+math.sin((2.8-self.star_timer)*7)*.1
        center=(BASE_W//2,88);r=int(29*scale);pts=[]
        for i in range(10):
            a=-math.pi/2+i*math.pi/5;rr=r if i%2==0 else r*.43;pts.append((center[0]+math.cos(a)*rr,center[1]+math.sin(a)*rr))
        pygame.draw.polygon(self.internal,(255,224,67),pts);pygame.draw.polygon(self.internal,(255,249,182),pts,2)
        objective="OBJECTIVE COMPLETE"
        slot=self.saves.slots[self.saves.selected]
        # The collected key remains the newest entry, so use the star name captured below.
        name=getattr(self,"last_star_name",objective)
        self._center_text(objective,132,self.medium,(255,235,111));self._center_text(name.upper(),156,self.small,(224,242,255))
        self._center_text(f"TOTAL STARS × {len(slot.stars)}",183,self.medium,(140,239,255))

    def draw_collision_map(self)->None:
        r=pygame.Rect(236,164,80,72);pygame.draw.rect(self.internal,(3,7,15),r);pygame.draw.rect(self.internal,(80,220,194),r,1)
        scale=1.05
        for b in self.world.boxes:
            x=int(r.centerx+b.center.x*scale);y=int(r.centery+b.center.z*scale)
            w=max(1,int(b.half.x*2*scale));h=max(1,int(b.half.z*2*scale))
            pygame.draw.rect(self.internal,(93,116,131),(x-w//2,y-h//2,w,h),1)
        pygame.draw.circle(self.internal,(255,225,61),(int(r.centerx+self.player.pos.x*scale),int(r.centery+self.player.pos.z*scale)),2)

    def render_frame(self)->None:
        if self.state=="title":self.draw_title()
        elif self.state=="intro":self.draw_intro()
        elif self.state=="files":self.draw_files()
        elif self.state=="options":self.draw_options()
        elif self.state=="controls":self.draw_controls()
        elif self.state=="about":self.draw_about()
        elif self.state=="pause":self.draw_pause()
        elif self.state=="star":self.draw_star()
        else:
            self._draw_3d();self.hud.draw(self.internal,self)
            if self.collision_debug:self.draw_collision_map()
        if self.fade>0:
            f=pygame.Surface((BASE_W,BASE_H));f.fill((0,0,0));f.set_alpha(int(self.fade));self.internal.blit(f,(0,0))
        source=self.internal
        # Resolution simulation downsamples the complete software framebuffer, then uses nearest-neighbor upscale.
        low_sizes=((160,120),(256,192),(320,240));low=low_sizes[self.settings.resolution_mode]
        if low!=(BASE_W,BASE_H):source=pygame.transform.scale(pygame.transform.scale(self.internal,low),(BASE_W,BASE_H))
        scaled=pygame.transform.scale(source,self.window.get_size());self.window.blit(scaled,(0,0));pygame.display.flip()

    def run(self)->None:
        last=time.perf_counter()
        while self.running:
            now=time.perf_counter();frame_dt=min(.1,now-last);last=now
            events=pygame.event.get();self.handle_events(events);inp=self.input.poll(events)
            self.accumulator+=frame_dt
            while self.accumulator>=FIXED_DT:
                self.update(inp,FIXED_DT);self.accumulator-=FIXED_DT
            self.render_frame();self.clock.tick(self.settings.fps_cap)
            if os.environ.get("AC_SM64PY_SMOKE"):
                self.smoke_frames+=1
                if self.smoke_frames>=6:self.running=False
        self.saves.save();pygame.quit()


if __name__ == "__main__":
    Game().run()
