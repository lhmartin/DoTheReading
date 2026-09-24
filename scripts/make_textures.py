"""Draws the app's leather, paper and wood textures.

Run from the repo root:  python scripts/make_textures.py
Writes PNG tiles into app/renderer/textures/ (JPEG for the woods). Every tile repeats seamlessly:
all the noise is built on a torus (FFT-filtered noise, wrap-around cells).

Leather is modelled rather than faked with noise: a height map of pebbles
(cellular noise with warped, uneven cells), a few long creases and pores,
lit from the top left, then coloured so the tile averages the chosen colour.
Wood is growth rings bent by long, stretched noise, with pores, streaks and
(per species) stripes, ray flecks or burl swirls; drawn at 2048 x 1024 and
saved at half that, since the rail it covers is narrow.
Needs numpy and Pillow.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "renderer" / "textures"
N = 512  # shown at 256 CSS px, so it stays crisp on 2x screens
WOOD_W, WOOD_H = 2048, 1024  # drawn at this size, saved at half (grain runs left to right)


def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def hex_rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)]) / 255


def noise(n, sigma, rng):
    """Periodic gaussian-blurred noise, zero mean, unit variance."""
    return normalised(blur(rng.standard_normal((n, n)), sigma))


def normalised(a):
    return (a - a.mean()) / a.std()


def worley(n, per_side, rng, warp_amp, warp_sigma):
    """Distances to the nearest and second-nearest cell point, on a torus,
    sampled through a warped grid so cells are uneven, not honeycomb."""
    cell = n / per_side
    grid = np.stack(np.meshgrid(np.arange(per_side), np.arange(per_side), indexing="ij"), -1)
    pts = (grid + 0.1 + 0.8 * rng.random((per_side, per_side, 2))) * cell
    y, x = np.mgrid[0:n, 0:n].astype(float)
    y = (y + warp_amp * noise(n, warp_sigma, rng)) % n
    x = (x + warp_amp * noise(n, warp_sigma, rng)) % n
    ci = np.floor(y / cell).astype(int)
    cj = np.floor(x / cell).astype(int)
    d1 = np.full((n, n), np.inf)
    d2 = np.full((n, n), np.inf)
    for di in (-2, -1, 0, 1, 2):
        for dj in (-2, -1, 0, 1, 2):
            p = pts[(ci + di) % per_side, (cj + dj) % per_side]
            dy = (y - p[..., 0] + n / 2) % n - n / 2
            dx = (x - p[..., 1] + n / 2) % n - n / 2
            d = np.hypot(dx, dy)
            d2 = np.where(d < d1, d1, np.minimum(d2, d))
            d1 = np.minimum(d1, d)
    return d1, d2, cell


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def blur(a, sigma, sigma_x=None):
    """Gaussian blur that wraps round the edges (the tiles are tori); sigma_x,
    if given, blurs across (along x) by a different amount than down."""
    sx = sigma if sigma_x is None else sigma_x
    ky = np.fft.fftfreq(a.shape[0])[:, None]
    kx = np.fft.fftfreq(a.shape[1])[None, :]
    g = np.exp(-2 * np.pi ** 2 * ((sx * kx) ** 2 + (sigma * ky) ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(a) * g))


def to_image(lin, target):
    """Linear RGB scaled so the tile averages `target` (so it reads as that
    colour from afar), as an 8-bit sRGB image."""
    for _ in range(4):
        mean = srgb_to_linear(linear_to_srgb(lin).reshape(-1, 3).mean(0))
        lin = lin * (target / mean)[None, None, :]
    return Image.fromarray((linear_to_srgb(lin) * 255 + 0.5).astype(np.uint8), "RGB")


def leather_height(rng):
    # Pebbles: rounded domes that fall away into soft valleys. The distance
    # to the cell border (F2 - F1), eased, gives a shoulder rather than a
    # flat-topped slab; mild warping keeps cells uneven without smearing.
    d1, d2, cell = worley(N, 46, rng, warp_amp=1.6, warp_sigma=8)
    edge = (d2 - d1) / cell
    # The border distance is roof-shaped (ridges run to each cell's middle),
    # so let it level off early and round it afterwards: a bead, not a roof.
    shoulder = smoothstep(0.0, 0.24, edge)
    crown = np.clip(1 - (d1 / cell) ** 2, 0, 1)
    pebble = shoulder * (0.84 + 0.16 * crown)
    # Pebbles differ in height, so the surface doesn't read as a waffle.
    pebble *= 0.78 + 0.22 * np.tanh(noise(N, 11, rng))

    # The hide underneath undulates gently.
    swell = noise(N, 22, rng)

    # A few long, shallow creases.
    crease = np.exp(-(noise(N, 34, rng) / 0.07) ** 2) * np.clip(noise(N, 60, rng) + 0.4, 0, 1)

    # Pores: sparse pin-pricks.
    impulses = (rng.random((N, N)) < 0.0025).astype(float)
    pores = blur(impulses, 0.8)
    pores /= pores.max()

    h = pebble + 0.10 * swell - 0.22 * crease - 0.25 * pores + 0.02 * noise(N, 0.8, rng)
    return blur(h, 1.05)  # rounded shoulders, no stair-stepped edges


def shade(h, strength, light=(-0.55, -0.62, 0.56)):
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) / 2
    gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) / 2
    nx, ny, nz = -strength * gx, -strength * gy, np.ones_like(h)
    norm = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    nx, ny, nz = nx / norm, ny / norm, nz / norm
    L = np.array(light) / np.linalg.norm(light)
    diffuse = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1) / L[2]
    H = L + np.array([0, 0, 1.0])
    H /= np.linalg.norm(H)
    spec = np.clip(nx * H[0] + ny * H[1] + nz * H[2], 0, 1) ** 36
    # Hollows catch less light whatever their angle.
    hn = (h - h.min()) / (h.max() - h.min())
    ao = 0.84 + 0.16 * smoothstep(0.1, 0.7, hn)
    return ao * (0.52 + 0.48 * diffuse), spec


def leather(color, seed, spec_amount, mottle):
    rng = np.random.default_rng(seed)
    h = leather_height(rng)
    light, spec = shade(h, strength=2.1)
    base = srgb_to_linear(hex_rgb(color))
    # Shadows go warmer: blue falls off faster than red, as in real hide.
    lin = base[None, None, :] * light[..., None] ** np.array([0.92, 1.0, 1.12])
    lin *= (1 + mottle * np.tanh(noise(N, 70, rng)))[..., None]
    lin += spec[..., None] * spec_amount * np.array([1.0, 0.92, 0.8])
    return to_image(lin, base)


def paper_grain(ink, seed, max_alpha):
    """Paper's own texture as alpha over any paper colour: the cloudy
    'formation' of the pulp, fine specks, and a scatter of short fibres
    lying every which way (a grid of fibres reads as cloth, not paper)."""
    n = 256
    rng = np.random.default_rng(seed)
    formation = 0.5 + 0.5 * np.tanh(noise(n, 5, rng) * 0.9)
    speck = np.clip(noise(n, 0.55, rng) - 1.5, 0, None)

    # Fibres: short, gently curved strokes drawn at 2x, wrapped round the
    # tile's edges, then scaled down so they're hair-thin.
    big = Image.new("L", (2 * n, 2 * n), 0)
    draw = ImageDraw.Draw(big)
    for _ in range(70):
        x, y = rng.random(2) * 2 * n
        angle = rng.random() * np.pi
        length = 14 + rng.random() * 40
        bend = (rng.random() - 0.5) * 0.8
        tone = int(60 + rng.random() * 120)
        pts = []
        for t in np.linspace(0, 1, 8):
            a = angle + bend * t
            pts.append((x + np.cos(a) * length * t, y + np.sin(a) * length * t))
        for dx in (-2 * n, 0, 2 * n):
            for dy in (-2 * n, 0, 2 * n):
                draw.line([(px + dx, py + dy) for px, py in pts], fill=tone, width=1)
    fibres = np.asarray(big.resize((n, n), Image.LANCZOS), float) / 255

    a = 0.45 * formation + 0.9 * speck + 1.1 * fibres
    a = np.clip(a / np.percentile(a, 99.8), 0, 1) * max_alpha
    rgba = np.zeros((n, n, 4))
    rgba[..., :3] = hex_rgb(ink)
    rgba[..., 3] = a
    return Image.fromarray((rgba * 255 + 0.5).astype(np.uint8), "RGBA")


def stretched(rng, sx, sy):
    """Noise on the wood tile, blurred sx along the grain and sy across it."""
    return normalised(blur(rng.standard_normal((WOOD_H, WOOD_W)), sy, sx))


def wood(dark, light, mean, seed=4, rings=13, figure=1.0, wobble=0.10, late=0.6, band=False,
         pores=0.35, pore_len=14, light_pores=False, streak=0.5, cloud=0.08, rays=0.0, burl=0.0, mid=None,
         band_vary=0.12, band_edge=0.06):
    """rings: bands per tile height (density). figure: how far they bow into
    arches. late: weight of the dark latewood line. band: hard-edged stripes
    (zebrawood, ribbon mahogany). pores: short dashes along the grain;
    light_pores draws them pale (wenge, ash). rays: the pale flecks of
    quartersawn oak. burl: swirling figure instead of straight rings."""
    rng = np.random.default_rng(seed)
    y = np.mgrid[0:WOOD_H, 0:WOOD_W][0] / WOOD_H
    bow = stretched(rng, 520, 70)
    v = y * rings + figure * bow * (0.6 + 0.4 * np.tanh(stretched(rng, 300, 90))) + wobble * stretched(rng, 50, 5)
    if burl:  # tight, broken swirls rather than smooth contours
        v = v * (1 - burl) + burl * (2.0 * stretched(rng, 70, 70) + 0.8 * stretched(rng, 22, 22) + 0.3 * stretched(rng, 7, 7))
    t = v - np.floor(v)
    latewood = np.exp(-((t - 0.82) / 0.07) ** 2)  # the thin dark line at each ring's end
    if band:  # dark stripe of varying width: its start wanders along the board
        start = 0.5 + band_vary * np.tanh(stretched(rng, 160, 12))
        ring = smoothstep(start, start + band_edge, t) * (1 - smoothstep(0.93 - band_edge, 0.97, t))
    else:
        ring = 0.55 * smoothstep(0.2, 0.8, t) * (1 - smoothstep(0.85, 1.0, t)) + late * latewood
    pore = np.clip(stretched(rng, pore_len, 0.7) - 1.3, 0, None) * np.clip(stretched(rng, 3, 3) + 0.5, 0, 2)
    str_ = np.clip(stretched(rng, 70, 0.9) - 1.0, 0, None)
    cl = stretched(rng, 240, 50)
    k = 0.2 + 0.45 * ring + streak * str_ + cloud * cl
    k += (-1 if light_pores else 1) * pores * pore * (0.5 + latewood)
    if rays:
        fleck = smoothstep(1.6, 2.4, stretched(rng, 26, 2.2)) * smoothstep(-0.3, 0.6, stretched(rng, 120, 30))
        k -= rays * fleck
    if burl:
        eyes = smoothstep(2.2, 2.9, stretched(rng, 2.2, 2.2)) * smoothstep(-0.2, 1.0, stretched(rng, 70, 70))
        k += burl * (0.9 * eyes + 0.12 * stretched(rng, 90, 90))
    k = np.clip(k, 0, 1)
    d, l = srgb_to_linear(hex_rgb(dark)), srgb_to_linear(hex_rgb(light))
    if mid:  # a third colour in the middle of the range: tints the figure
        m = srgb_to_linear(hex_rgb(mid))
        a = np.clip(k * 2, 0, 1)[..., None]
        b = np.clip(k * 2 - 1, 0, 1)[..., None]
        lin = np.where(k[..., None] < 0.5, l * (1 - a) + m * a, m * (1 - b) + d * b)
    else:
        lin = l[None, None, :] * (1 - k[..., None]) + d[None, None, :] * k[..., None]
    return to_image(lin, srgb_to_linear(hex_rgb(mean))).resize((WOOD_W // 2, WOOD_H // 2), Image.LANCZOS)


# The rail's woods, chosen in Settings (cherry by default). Their names and
# descriptions live in app.js's WOODS, under the same ids.
WOODS = {
    # id: the settings for wood()
    "walnut": dict(dark="#3a2213", light="#8a5c3a", mean="#61402a"),
    "walnut-quiet": dict(dark="#2a180d", light="#5e3f2a", mean="#432c1d", rings=22, figure=0.5, late=0.35, pores=0.15, streak=0.25, seed=8),
    "walnut-bold": dict(dark="#20120a", light="#9a6a44", mean="#553722", rings=8, figure=2.0, late=0.9, pores=0.45, seed=21),
    "cherry": dict(dark="#5a2a16", light="#c07a4e", mean="#8e4f30", rings=11, figure=1.2, late=0.4, pores=0.08, streak=0.2, cloud=0.14, seed=5),
    "mahogany": dict(dark="#4a1c12", light="#9a4c34", mean="#6a2e1e", rings=24, figure=0.3, wobble=0.07, band=True, band_vary=0.2, band_edge=0.12, pores=0.3, pore_len=20, streak=0.3, seed=9),
    "oak-quarter": dict(dark="#7a5530", light="#caa274", mean="#a47c4f", rings=26, figure=0.2, late=0.5, pores=0.55, pore_len=10, rays=0.8, seed=13),
    "maple": dict(dark="#b58a58", light="#ecd2a6", mean="#d6b482", rings=16, figure=0.7, late=0.25, pores=0.05, streak=0.12, cloud=0.1, seed=17),
    "ash": dict(dark="#6f5a40", light="#e2cfae", mean="#bba17c", rings=9, figure=1.4, late=1.0, pores=0.6, pore_len=18, streak=0.4, seed=19),
    "wenge": dict(dark="#1a120d", light="#4c3a2c", mean="#2e221a", rings=34, figure=0.3, band=True, pores=0.8, pore_len=24, light_pores=True, streak=0.2, seed=23),
    "zebrano": dict(dark="#3a2716", light="#e0caa2", mean="#b0936c", rings=24, figure=0.25, wobble=0.05, band=True, band_vary=0.14, band_edge=0.04, pores=0.2, streak=0.15, seed=27),
    "bog-oak": dict(dark="#262320", light="#6b6259", mean="#453f39", rings=18, figure=0.9, late=0.6, pores=0.5, rays=0.2, seed=31),
    "teak": dict(dark="#4a2e14", light="#c0925a", mean="#91683a", rings=10, figure=1.0, late=0.5, pores=0.3, streak=0.6, cloud=0.08, mid="#9a6a36", seed=35),
    "burl": dict(dark="#2a170b", light="#a8764a", mean="#634129", rings=4, burl=0.9, late=0.7, pores=0.1, streak=0.05, cloud=0.0, seed=41),
    "rosewood": dict(dark="#1c0f0e", light="#7a4436", mean="#4a2822", rings=12, figure=1.3, late=1.1, pores=0.3, streak=0.7, mid="#5c2e2a", seed=43),
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    leather("#a2683a", seed=11, spec_amount=0.10, mottle=0.035).save(OUT / "leather.png", optimize=True)
    leather("#3b2618", seed=11, spec_amount=0.05, mottle=0.04).save(OUT / "leather-dark.png", optimize=True)
    paper_grain("#6b4e2a", seed=3, max_alpha=0.13).save(OUT / "paper-grain.png", optimize=True)
    paper_grain("#f3e6cc", seed=3, max_alpha=0.06).save(OUT / "paper-grain-dark.png", optimize=True)
    (OUT / "woods").mkdir(exist_ok=True)
    (OUT / "woods" / "thumbs").mkdir(exist_ok=True)
    for name, kw in WOODS.items():
        tile = wood(**kw)
        tile.save(OUT / "woods" / f"{name}.jpg", quality=88)
        # Settings shows each wood as a 36px swatch: a 72px piece, for 2x screens.
        tile.crop((600, 300, 672, 372)).save(OUT / "woods" / "thumbs" / f"{name}.jpg", quality=88)
    for f in sorted(OUT.glob("**/*.[pj]*g")):
        print(f.relative_to(OUT), f.stat().st_size // 1024, "KB")
