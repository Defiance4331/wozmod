import math

import fontforge
import psMat

from .buttonvec import contours as _contours, line_seg, rounded
from .skeleton import Skeleton, _layer, merge

U = 50
CAP = 31.169
SLANT = 0.1755
OVER = 0.619
STEM = 6.638
BAR = 1.906
SERIF = 0.881
SX = 3.656
BRK = 2.806
TIP = 0.3
BEAK = (3.606, 0.763, 4.969)
PEN = (6.413, 1.769)
FALL = 6.0


PARAMS = {
    "A": dict(xa=18.263, wa=2.244, ya=-0.262, xl=5.938, wl=2.169, xr=28.925, wr=6.962, aw=4.537, yc=20.281,
              hc=1.681, sl=5.781, sr=4.263, so=3.406),
    "B": dict(xb1=21.312, ym=14.188, xb2=23.425, r1=5.888, r2=7.356, t=0.52, w1=6.369, w2=6.388),
    "C": dict(xe=18.306, wl=6.641, tt=1.843, tb=1.693, rxo=15.389, ryo=14.062, rxob=15.352, ryob=13.331, rxi=8.599,
              ryi=11.319, rxib=9.292, ryib=13.775, to=0.52, ti=0.593, xo=27.544, bt=9.613, bb=10.381, f=0.619,
              rc=0.006, dt=1.35, db=1.369),
    "D": dict(xd=28.225, rx=11.519, ry=14.219, t=0.603, w=6.65),
    "E": dict(e1=20.025, bh1=8.5, f1=0.769, e2=16.756, ym=15.0, sh=4.725, e3=22.081, bh3=8.7, f3=0.019),
    "F": dict(e1=20.056, bh1=8.331, f1=0.675, e2=16.794, ym=14.963, sh=4.838),
    "G": dict(rx=12.762, ry=12.456, t=0.52, ang=0.0, xe=19.637, xo=27.275, bt=9.488, f=0.825, rc=1.15, xs=24.931,
              ys=16.231, rb=2.844, sl=3.619, sr=3.212),
    "H": dict(xr=25.675, ym=15.256, hb=1.681),
    "I": dict(),
    "K": dict(xa=20.45, wa=2.994, yj=18.137, sr=3.269, lb=20.687, wl=7.062, yl=15.006, bl=3.95, br=2.712),
    "L": dict(e=21.906, bh=9.131, f=0.131),
    "M": dict(hl=2.063, xv=13.606, wv=1.456, wd=6.525, xr=32.65, wt=2.306, sr=4.381, od=1.044, xt=22.5, yc=1.481,
              ul=0.956, ur=-1.894),
    "N": dict(hl=2.406, xr=24.694, hr=2.269, dt=3.638, db=26.337, wd=8.431),
    "O": dict(w=31.188, kx=0.998, ry=13.488, t=0.54, ang=0.025),
    "P": dict(xb=23.2, yb=18.494, rx=7.756, ry=8.256, t=0.578, w=6.637),
    "Q": dict(q0=5.55, qy0=0.494, qh=1.837, qb=25.988, qy1=8.056, qr=5.912, q1=30.938, qyt=2.256, qw=3.3),
    "R": dict(xb=22.731, yb=16.206, rx=7.006, ry=7.881, t=0.615, w=6.488, lt=11.781, lb=20.75, wl=7.069,
              tail=3.038),
    "S": dict(xe=11.45, xr=20.563, xeb=8.756, xlb=0.212, ot=-0.356, ob=-0.056, tt=1.667, tb=1.705, xl=-0.587,
              wl=5.886, xrb=22.762, wr=6.036, ya1=13.5, yb1=20.85, ya2=8.906, yb2=16.406, rxa1=7.968, rya1=8.668,
              ra2=6.287, ra3=3.513, rxa4=5.006, rya4=4.351, ta=0.465, rxb1=4.931, ryb1=4.169, rb2=2.576, rb3=6.343,
              rxb4=9.106, ryb4=8.043, tbn=0.559, bt=9.387, bb=10.969, f=0.431, rc=-0.206, dt=0.787, db=1.894),
    "T": dict(w=25.462, bh=8.95, f=0.675, xc=12.7),
    "U": dict(xr=24.562, hr=2.188, ys=21.669, rx=9.075, ry=10.137, t=0.562, hb=2.337, ti=0.515, rxl=12.656,
              ryl=10.25),
    "V": dict(xl=5.725, wl=7.081, xr=27.681, wr=2.1, xv=15.7, wv=2.594),
    "W": dict(x1=5.669, w1=7.219, v1=13.925, w2=2.031, x3=22.131, w3=7.2, v2=30.387, x4=39.094, w4=2.106, wv=2.694,
              t2=3.112),
    "Y": dict(xl=8.344, wl=6.95, xr=26.844, wr=2.375, xs=17.15, yj=15.762),
    "Z": dict(wd=8.4),
    "/": dict(xt=14.662, xb=0.562, w=2.331, yt=-4.15, yb=36.919),
}


BEARINGS = {
    "/": (3.08, 4.53), "A": (0.19, -1.66), "B": (2.31, 2.45), "C": (2.89, 2.41), "D": (2.15, 2.72),
    "E": (2.19, 3.05), "F": (1.94, 2.41), "G": (3.05, 1.6), "H": (1.27, 2.17), "I": (2.3, 1.99), "K": (2.73, 0.09),
    "L": (2.12, 1.32), "M": (1.68, 1.71), "N": (1.08, 1.62), "O": (2.91, 2.99), "P": (2.03, 2.71),
    "Q": (3.75, 3.65), "R": (1.98, 0.4), "S": (3.18, 2.75), "T": (2.15, 1.91), "U": (2.56, 1.5),
    "V": (-1.83, -1.58), "W": (-0.72, -0.72), "Y": (-1.56, 0.69), "Z": (3.05, 3.05),
}
SPACE = 16.7
# Keyed by word_key(word), the word's SHA-256, so the official word is not stored.
SETS = {
    "1879f84bfc3e6acfcf1608ef2b4b2ac54830532e82631d6fa04275119a771b9a": (0.0, 30.26, 66.26, 110.07),
    "ae75b2880b5b5e401de30cd4b3fbbdcb8a4248f0c0018da2135cf7779a0974d5": (0.0, 46.82, 76.01, 111.82, 144.62, 179.98),
    "cf888f8318b597b573b26a108bb71f0896e998299382dfbe86493046b56a25b6": (0.0, 35.83, 67.77, 85.79, 115.27, 133.33, 164.27, 182.32, 214.55),
    "9be0a62f71a8b12a5997cb2fd2fba721a4e0463026c7de1f145dec5edabe67cd": (0.0, 46.82, 76.01, 111.82, 143.62, 178.98, 231.81, 261.01, 297.06, 325.77, 344.62, 379.98),
    "bca41978330e868e846dfdc61d5c2fdd5b60ccba38d80acb66307c7d7a6ad80e": (0.0, 30.98, 60.17, 95.17, 155.94, 173.14, 209.98, 239.78),
    "4e6ca2ef7fe047dfc5af32b6fa3a991e5917e9590fd7c008382152b8b2a813a9": (0.0, 31.17, 67.0, 98.78, 134.92, 168.94, 185.17, 220.96),
    "bf151de81086363eb82fce98b85d9cc4f7e550d6e4d15c6be2c04d1579df538c": (0.0, 45.11, 77.93, 112.31, 159.82, 189.27, 208.55, 236.83),
    "1a19b3383ce6afc33d680a4f224ac3f359559755313614834baf6bbe34ab50a9": (0.0, 33.25, 68.25, 113.13, 160.99, 194.86, 231.22, 268.08, 298.01, 332.86, 369.1, 399.3),
    "0bad33e27107687ce9ed6814354915a6e42d00483e3e6c3f3e25f5a2193f9c0c": (0.0, 33.87, 69.23, 106.04, 143.02, 161.09, 192.02, 210.87, 247.23, 285.31),
    "10aae6592b39c03b0a584e048c3eee188998f69f1b7f28b924f3e83e74cdaa41": (0.0, 36.2, 65.2, 94.16, 113.13, 146.2, 177.14, 211.44),
    "d0b1a869600d21076a3fa8b5f52546e55920588ce73dfb7e56ca9dc08bdcaf3e": (0.0, 36.2, 68.22, 99.16, 118.0, 153.86, 191.94),
    "23e04084df125efcc954cba154e2f4c9e06e45d9a0111c8789bc7e6e723f5782": (0.0, 36.96, 56.25, 84.03, 115.81, 152.95, 182.79),
    "e7d9b8c6e712adcad6efbf5f06131030fb2d5a42ac51b4292507b8e31fa1674b": (0.0, 33.25, 68.25, 113.07, 161.3, 190.07, 221.08, 252.08, 283.02, 300.22, 338.0, 374.3),
    "b0eea89d283ca74cfd9db0fcd353cd96c5b4da124c687700b585fbb63d84307d": (0.0, 33.87, 69.23, 106.09, 137.01, 170.87, 208.05, 237.05, 266.07, 297.01),
    "4613822d04f273d0df31e79a3dd9bc7bea0acd15db18ef9e7515ea66a84d6691": (0.0, 28.56, 65.94, 100.92, 137.73),
    "8eab2d06693ce59a75766eb9880556bf0a929ff36116207015c9365ce094b432": (0.0, 35.01, 67.25, 103.07, 134.05, 172.02, 190.99, 225.3),
    "d33e6bb32210cbc4c09ced6352de5d85f832ca3e26076d11f782c61c7d032df4": (0.0, 25.98, 56.43, 90.81, 122.69, 145.78, 175.59, 209.98, 245.78),
    "a34396cc70bcb1fe469abb7e932313db0a72577cdb19134a13a5cd7f71c95c04": (0.0, 36.13, 65.13, 94.16, 113.13, 146.2, 177.14, 226.14, 260.2, 291.44, 320.2, 351.22),
    "222a857ddb780ba0422608f9245352d4d247acdbe8922c542b015ac7c9d6a56c": (0.0, 27.76, 56.86, 85.76, 116.75, 148.71, 196.0, 224.75, 255.76, 284.79, 318.82, 353.7,
        386.9, 404.43),
    "3156aee7eeecbacfcfa2997365487c8e83f72cad3f3164e2ea14d0b80fb78903": (0.0, 27.78, 59.56, 96.7, 126.54, 177.0, 204.82, 235.73, 264.82, 296.69, 329.78),
    "316a1d8733d73abafc8878c10395423ce5dc3f4aaa07a5191c231d3da726dfa5": (0.0, 33.25, 69.1, 99.04, 128.07, 159.01, 188.84),
    "e8144b1fafeed56bf5a74f043944cc8953e1e93533f04a492b73616fa2a872df": (0.0, 46.82, 76.01, 111.82, 144.62, 179.98, 218.06),
    "69c9c1ad9de35746bf10886c13e17c9daf8a0c7e02cdd4303cdadc9d19b17254": (0.0, 45.61, 79.43, 113.77, 131.88, 164.05),
    "0892858cd9f5586fca1c8d6c722ac74a4452edbaf63d24c934ac39472bd887e1": (0.0, 34.91, 61.96, 122.79, 154.77, 188.02, 222.8, 253.01, 287.84, 319.87, 348.93, 377.84),
    "4aa70a38c6d2c471fde92f15af760b7e7cb182d243375d280693da48d28da413": (0.0, 34.08, 67.33, 96.21, 133.1, 163.04, 195.29),
    "1b114dce35022c6738402f25fa82a3a89814a0e0d331b815aed4139c3ba26aee": (0.0, 36.26, 68.48, 86.89, 115.61, 134.02, 161.8, 209.22, 246.42, 281.06, 318.06, 353.5,
        371.91, 405.3, 436.31),
    "24f9313b7431f581892a5dc9e6868f1e8ed3c4f1d5ed2137d7f184713308f777": (0.0, 47.41, 76.92, 112.42, 145.77, 181.52, 233.56, 271.2, 305.14, 341.57, 377.52, 396.78,
        429.34, 460.67),
    "032f4134f8d4624a4e305b6cb9b6f003b845efe3772c84814500b4d0f083424b": (0.0, 18.01, 49.01, 79.15, 138.2, 172.96, 207.07, 244.16, 279.98, 297.96, 331.04, 362.01),
    "0affdd23e38e0ea6ffb5b8473bc2c0f54dbff0629f5fc4c6abdc96046e057826": (0.0, 36.85, 65.02, 129.1, 158.8, 191.89, 222.02, 257.06, 292.84, 325.84, 344.63),
    "586f188b0eddc1210dd8493be2723edfc810de36ac3c68fcdc1a5ef0e76a2528": (0.0, 28.61, 64.36, 96.43, 128.92, 162.21, 195.83, 230.62, 265.01, 299.99, 336.75),
    "2ca05767d5dce89655e2811605a51c3937f8786901f81571c6f4eea90e073a94": (0.0, 32.69, 68.39, 103.85, 135.81, 173.53, 206.26, 237.51, 268.64, 319.6, 356.03, 390.58,
        424.39, 472.17, 503.35),
    "a5b2ea78f89d49f98411b265da8125f27115e4df5e772c9d1ffb5946d5da229e": (0.0, 33.46, 70.17, 106.4, 139.02, 176.23, 210.57, 241.65, 273.62, 326.47, 355.59, 386.73,
        417.06, 449.18, 483.52),
}


TUNE = {
    "A": (0.0, -0.013, -0.025, 0.013, 0.0, 0.0, -0.025, -0.013, -0.038, 0.025, 0.025, 0.062, 0.113, 0.0, -0.013,
        -0.013, -0.013, -0.013, -0.025, 0.013, 0.0, 0.0, 0.0, 0.013, 0.025, -0.225, -0.188, 0.0, 0.05, -0.025),
    "B": (0.038, 0.062, -0.013, -0.075, 0.0, 0.075, 0.15, 0.0, 0.025, 0.05, 0.0, -0.013, 0.0, 0.05, -0.225, -0.3,
        -0.025, 0.2, 0.125, 0.088, 0.062, 0.05, 0.05, 0.013, 0.013, 0.013, 0.025, 0.013, -0.025, -0.013, 0.0, 0.0,
        0.0, -0.075),
    "C": (0.05, 0.0, 0.0, -0.013, -0.025, 0.0, -0.025, -0.013, -0.013, -0.025, -0.2, -0.3, 0.175, 0.0, 0.038, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.013, 0.038, 0.062, -0.025, 0.05, 0.075, 0.0, 0.125, 0.213),
    "D": (0.075, 0.025, 0.0, 0.0, 0.0, 0.013, 0.025, 0.025, -0.038, -0.1, -0.188, -0.275, -0.375, -0.05, 0.013,
        0.15, 0.2, 0.263, 0.188, 0.125, -0.025, 0.1, -0.062, -0.1, -0.013, 0.075, 0.05, 0.175, 0.013, -0.025,
        -0.013, -0.013, -0.013),
    "E": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.1, -0.075, -0.063, -0.063, -0.075, 0.05, -0.188, 0.1, 0.1, -0.075,
        -0.15, -0.025, -0.05, -0.1, -0.075, -0.1, -0.325, 0.0, 0.0, 0.1, 0.0, -0.125, 0.0, -0.05, -0.063, -0.038,
        -0.013),
    "F": (-0.1, -0.075, -0.062, -0.05, -0.063, -0.1, -0.075, -0.1, -0.2, -0.138, -0.025, -0.05, -0.05, 0.025,
        0.088, -0.025, -0.013, 0.0, 0.025, 0.0, -0.05, -0.063, -0.088, -0.125, -0.15, 0.0, -0.163, -0.088),
    "G": (0.1, 0.075, 0.05, 0.025, -0.163, 0.0, 0.013, 0.113, 0.038, -0.1, -0.025, -0.125, -0.062, 0.038, 0.1, 0.1,
        -0.15, -0.1, -0.038, -0.113, -0.163, 0.038, 0.0, 0.237, 0.288, -0.375, -0.6, -0.2, 0.1, 0.062, -0.138,
        -0.25),
    "H": (-0.1, -0.075, -0.063, -0.025, 0.025, 0.013, 0.0, 0.0, 0.0, -0.025, -0.013, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, -0.025, 0.0, 0.013, -0.025, -0.025, -0.038, -0.062, -0.1, -0.237, -0.175, -0.138, -0.038, 0.025,
        0.013, 0.0, 0.0, -0.025, -0.05),
    "I": (-0.3, -0.225, -0.2, -0.013, 0.075, 0.05, 0.038, 0.025, -0.05, -0.05, 0.038, 0.05, -0.013, 0.0, 0.0,
        0.013, 0.0),
    "L": (-0.3, -0.2, -0.138, -0.063, 0.0, -0.013, -0.025, -0.05, -0.038, 0.025, 0.1, 0.0, 0.15, 0.1, 0.063, 0.05,
        0.038, 0.025, 0.0, 0.0, 0.0, -0.013, 0.0),
    "M": (0.025, 0.025, 0.038, 0.05, 0.075, 0.125, 0.063, 0.0, -0.063, -0.15, 0.425, 0.475, 0.512, 0.313, 0.1,
        -0.088, -0.3, -0.2, 0.213, 0.088, 0.0, 0.0, 0.0, 0.0, -0.025, -0.013, -0.013, -0.025, 0.0, 0.075, -0.025,
        0.038, 0.113, 0.2, 0.3, 0.175, 0.138, 0.125, 0.15, 0.1, 0.113, 0.138, 0.2, 0.225, 0.138, 0.088, 0.05,
        0.025),
    "N": (0.975, 0.663, 0.313, -0.1, -0.1, -0.125, -0.038, -0.062, -0.1, -0.063, -0.038, -0.013, 0.0, 0.0, 0.013,
        -0.05, -0.125, -0.238, 0.825, 0.837, 0.65, -0.175, -0.138, -0.125, -0.15, -0.213, 0.1, 0.138, 0.225, 0.088,
        -0.025, -0.15, -0.3, -0.225, -0.138, 0.05),
    "O": (0.2, 0.3, 0.05, -0.35, -0.325, -0.775, -0.125, 0.2, 0.225, -0.15, -0.425, -0.4, -0.05, -0.075, -0.113,
        -0.175, -0.075, 0.15, 0.15, 0.05, -0.1, -0.025, 0.05, -0.063, -0.2, -0.038, 0.15, 0.225, -0.15, -0.1,
        -0.075),
    "P": (0.0, 0.263, 0.325, -0.025, -0.1, -0.2, 0.05, -0.125, -0.263, -0.1, 0.3, 0.338, 0.1, 0.05, 0.013, 0.013,
        0.025, 0.038, 0.013, -0.125, -0.075, -0.038, 0.0, -0.113, -0.038, 0.113, 0.175, -0.275),
    "Q": (-0.1, -0.137, -0.149, -0.128, -0.019, 0.0, -0.096, -0.107, -0.1, -0.075, -0.093, -0.156, -0.154, -0.113,
        -0.106, -0.102, -0.059, 0.009, 0.054, 0.056, 0.032, 0.01, 0.0, -0.004, -0.042, -0.088, 0.25, 0.313, 0.113,
        -0.188, -0.325, -0.3, -0.062, 0.2, 0.163, 0.0, -0.225, -0.3, -0.038),
    "R": (0.125, 0.0, -0.038, 0.075, 0.0, -0.038, -0.075, -0.1, -0.1, -0.062, 0.375, 0.275, 0.15, 0.0, 0.013,
        0.013, 0.013, 0.013, 0.0, -0.125, -0.013, 0.1, 0.225, -0.1, 0.0, 0.1, 0.05, -0.013, -0.012, 0.0, -0.088,
        -0.1, -0.088, -0.088),
    "S": (0.1, -0.009, 0.022, -0.02, 0.073, -0.048, -0.015, -0.019, 0.021, 0.002, -0.03, -0.022, -0.058, -0.073,
        -0.029, 0.0, 0.047, 0.1, 0.06, 0.018, 0.0, -0.04, 0.057, -0.048, 0.008, 0.032, -0.015, -0.109, -0.075,
        -0.069),
    "T": (-0.125, -0.075, -0.05, -0.025, -0.013, -0.025, -0.075, 0.138, 0.125, 0.075, 0.05, 0.025, 0.013, 0.225,
        0.55, 0.525, 0.3, 0.0, 0.013, 0.025, 0.05, 0.075, 0.113, 0.013, 0.0),
    "U": (0.638, 0.313, 0.2, -0.038, -0.05, -0.075, -0.025, -0.038, -0.05, 0.1, 0.05, 0.013, -0.025, 0.063, 0.025,
        0.0, -0.225, -0.138, -0.075, -0.075, -0.025, -0.013, 0.0, 0.038, 0.113, -0.075, -0.075, -0.05, -0.038,
        -0.025, 0.038),
    "V": (0.25, 0.0, 0.0, 0.038, 0.013, 0.0, -0.013, -0.05, -0.088, -0.05, -0.013, 0.013, 0.05, 0.088, -0.238,
        -0.05, 0.125, 0.1, 0.0, -0.075, -0.163, -0.175, -0.088, -0.025, 0.038),
    "W": (0.25, 0.088, 0.075, 0.05, 0.0, -0.038, -0.088, -0.15, -0.025, 0.013, 0.05, 0.1, 0.175, 0.088, 0.013,
        -0.062, -0.1, -0.038, 0.013, 0.062, 0.125, 0.2, -0.2, -0.275, -0.175, -0.1, 0.0, -0.038, -0.088, -0.088,
        0.0, 0.1, -0.05, -0.213, -0.113, -0.025, 0.025, 0.025, -0.062, -0.15, -0.088, -0.038, 0.025),
    "Y": (0.088, 0.05, 0.175, 0.25, 0.062, -0.125, -0.075, -0.025, 0.013, 0.075, 0.163, 0.1, -0.013, -0.013, 0.025,
        0.062, 0.113, -0.225, -0.175, -0.15, -0.025, 0.075, 0.075, 0.013, -0.125),
}


def shape(s, pts):
    xy = [p[:2] for p in pts]
    r = [p[2] if len(p) > 2 else 0.0 for p in pts]
    n = len(xy)

    def tangent(i):
        (ax, ay), (vx, vy), (bx, by) = xy[i - 1], xy[i], xy[(i + 1) % n]
        u = (ax - vx, ay - vy), (bx - vx, by - vy)
        c = (u[0][0] * u[1][0] + u[0][1] * u[1][1]) / (math.hypot(*u[0]) * math.hypot(*u[1]))
        th = math.acos(max(-1.0, min(1.0, c)))
        return 0.0 if r[i] <= 0 or th < 1e-3 or abs(th - math.pi) < 1e-3 else r[i] / math.tan(th / 2)
    d = [tangent(i) for i in range(n)]
    k = [1.0] * n
    for i in range(n):
        j = (i + 1) % n
        need, have = d[i] + d[j], math.dist(xy[i], xy[j])
        if need > have:
            k[i] = min(k[i], have / need)
            k[j] = min(k[j], have / need)
    l = _layer(s.contour(xy, [ri * ki for ri, ki in zip(r, k, strict=True)], True))
    l.correctDirection()
    return l


def blob(s, segs):
    c = fontforge.contour()
    c.moveTo(*s.fp(*segs[0][0]))
    for seg in segs:
        c.cubicTo(*(s.fp(*q) for q in seg[1:]))
    c.closed = True
    l = _layer(c)
    l.correctDirection()
    return l


def minus(a, b):
    b.exclude(a)
    return b


def stroke(xt, xb, wt, wb, y0=0.0, y1=None, top=(0, 0), bot=(0, 0)):
    y1 = CAP if y1 is None else y1
    L = lambda y: xt - wt / 2 + (xb - wb / 2 - xt + wt / 2) * (y - y0) / (y1 - y0)
    R = lambda y: xt + wt / 2 + (xb + wb / 2 - xt - wt / 2) * (y - y0) / (y1 - y0)
    (tl, tr), (bl, br) = top, bot
    pts = [(L(y0) - tl, y0, TIP if tl else 0.0)]
    pts += [(R(y0) + tr, y0, TIP), (R(y0) + tr, y0 + SERIF, TIP), (R(y0 + SERIF), y0 + SERIF, BRK)] if tr else \
        [(R(y0), y0)]
    pts += [(R(y1 - SERIF), y1 - SERIF, BRK), (R(y1) + br, y1 - SERIF, TIP), (R(y1) + br, y1, TIP)] if br else \
        [(R(y1), y1)]
    pts += [(L(y1) - bl, y1, TIP), (L(y1) - bl, y1 - SERIF, TIP), (L(y1 - SERIF), y1 - SERIF, BRK)] if bl else \
        [(L(y1), y1)]
    if tl:
        pts += [(L(y0 + SERIF), y0 + SERIF, BRK), (L(y0) - tl, y0 + SERIF, TIP)]
    return pts


def stem(x0, x1, top=None, bot=None, y0=0.0, y1=None):
    return stroke((x0 + x1) / 2, (x0 + x1) / 2, x1 - x0, x1 - x0, y0, CAP if y1 is None else y1,
                  (SX, SX) if top is None else top, (SX, SX) if bot is None else bot)


def hook(x0, e, y, d, depth, f, h, rc=None, sx=1, drop=0.0):
    bw, tw, br = BEAK
    rc = TIP if rc is None else rc
    xj = x0 + 2 * sx
    top = [(x0, y), (xj, y, FALL)] if drop else [(x0, y)]
    bottom = [(xj, y + d * h, FALL), (x0, y + d * h)] if drop else [(x0, y + d * h)]
    return [*top, (e, y + d * drop, rc), (e + sx * f, y + d * depth, TIP), (e + sx * (f - tw), y + d * depth),
            (e - sx * bw, y + d * (h + drop), br), *bottom]


def arm(x0, e, y, d, bh, f):
    return hook(x0, e, y, d, bh, f, BAR)


def ext(pen):
    a, b, th = pen[0] / 2, pen[1] / 2, (pen[2] if len(pen) > 2 else 0.0)
    return math.hypot(a * math.cos(th), b * math.sin(th)), math.hypot(a * math.sin(th), b * math.cos(th))


def mid_arm(x0, e, ym, sh):
    bw, tw, br = BEAK
    a, b = ym - BAR / 2, ym + BAR / 2
    return [(x0, a), (e - bw * 0.7, a, br * 0.6), (e - tw, a - sh), (e, a - sh, TIP), (e, b + sh, TIP),
            (e - tw, b + sh), (e - bw * 0.7, b, br * 0.6), (x0, b)]


def rbox(x0, y0, x1, y1, corners):
    c = {i: ((ry, rx, t) if i in (0, 2) else (rx, ry, t)) for i, (rx, ry, t) in corners.items()}
    return rounded([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], c, closed=True)


def chain(s, segs, pen, closed=False):
    return s.curve(segs[0][0], *[q for seg in segs for q in seg[1:]], pen=pen, closed=closed)


def bowl(s, x0, y0, x1, y1, rx, ry, t, pen):
    segs = rounded([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], {1: (rx, ry, t), 2: (ry, rx, t)})
    return chain(s, segs, pen)


def oval(p, pen):
    x0, y0, x1, y1 = pen[0] / 2, -OVER + pen[1] / 2, p["w"] - pen[0] / 2, CAP + OVER - pen[1] / 2
    rx, ry = p["kx"] * (x1 - x0) / 2, p["ry"]
    return rbox(x0, y0, x1, y1, {i: (rx, ry, p["t"]) for i in range(4)}), (x0, y0, x1, y1)


def rpen(p):
    return (PEN[0], PEN[1], p.get("ang", 0.0))


BUILD = {}


def glyph(ch):
    def deco(fn):
        BUILD[ch] = fn
        return fn
    return deco


@glyph("I")
def _(s, p):
    return [shape(s, stem(0, STEM))]


@glyph("H")
def _(s, p):
    xr, ym, hb = p["xr"], p["ym"], p["hb"]
    return [shape(s, stem(0, STEM)), shape(s, stem(xr - STEM, xr)),
            s.fill(STEM - 0.5, ym - hb / 2, xr - STEM + 0.5, ym + hb / 2)]


@glyph("E")
def _(s, p):
    return [shape(s, stem(0, STEM, (SX, 0), (SX, 0))),
            shape(s, arm(1, p["e1"], 0, 1, p["bh1"], p["f1"])),
            shape(s, mid_arm(1, p["e2"], p["ym"], p["sh"])),
            shape(s, arm(1, p["e3"], CAP, -1, p["bh3"], p["f3"]))]


@glyph("F")
def _(s, p):
    return [shape(s, stem(0, STEM, (SX, 0), (SX, SX))),
            shape(s, arm(1, p["e1"], 0, 1, p["bh1"], p["f1"])),
            shape(s, mid_arm(1, p["e2"], p["ym"], p["sh"]))]


@glyph("L")
def _(s, p):
    return [shape(s, stem(0, STEM, (SX, SX), (SX, 0))), shape(s, arm(1, p["e"], CAP, -1, p["bh"], p["f"]))]


@glyph("T")
def _(s, p):
    w, bh, f = p["w"], p["bh"], p["f"]
    bw, tw, br = BEAK
    bar = [(0, 0, TIP), (w, 0, TIP), (w + f, bh, TIP), (w + f - tw, bh), (w - bw, BAR, br), (bw, BAR, br),
           (tw - f, bh), (-f, bh, TIP)]
    return [shape(s, bar), shape(s, stem(p["xc"] - STEM / 2, p["xc"] + STEM / 2, (0, 0), (SX, SX), y0=1.0))]


@glyph("D")
def _(s, p):
    pen = (p["w"], PEN[1])
    return [shape(s, stem(0, STEM, (SX, 0), (SX, 0))),
            bowl(s, 1, pen[1] / 2, p["xd"] - pen[0] / 2, CAP - pen[1] / 2, p["rx"], p["ry"], p["t"], pen)]


@glyph("P")
def _(s, p):
    pen = (p["w"], PEN[1])
    return [shape(s, stem(0, STEM, (SX, 0), (SX, SX))),
            bowl(s, 1, pen[1] / 2, p["xb"] - pen[0] / 2, p["yb"], p["rx"], p["ry"], p["t"], pen)]


@glyph("R")
def _(s, p):
    pen = (p["w"], PEN[1])
    return [shape(s, stem(0, STEM, (SX, 0), (SX, SX))),
            bowl(s, 1, pen[1] / 2, p["xb"] - pen[0] / 2, p["yb"], p["rx"], p["ry"], p["t"], pen),
            shape(s, stroke(p["lt"], p["lb"], p["wl"], p["wl"], p["yb"], CAP, bot=(0, p["tail"])))]


@glyph("B")
def _(s, p):
    ym, h = p["ym"], PEN[1]

    return [shape(s, stem(0, STEM, (SX, 0), (SX, 0))),
            bowl(s, 1, h / 2, p["xb1"] - p["w1"] / 2, ym - 0.06, p["r1"], p["r1"], p["t"], (p["w1"], h)),
            bowl(s, 1, ym, p["xb2"] - p["w2"] / 2, CAP - h / 2, p["r2"], p["r2"], p["t"], (p["w2"], h))]


@glyph("O")
def _(s, p):
    segs, _ = oval(p, PEN)
    return [chain(s, segs, rpen(p), closed=True)]


def open_round(p, pen, tail):
    hx, hy = ext(pen)
    x0, y0, y1 = hx, -OVER + hy, CAP + OVER - hy
    rx, ry, t = p["rx"], p["ry"], p["t"]
    pts = [(p["xe"], y0), (x0, y0), (x0, y1)] + tail(y1)
    corners = {1: (rx, ry, t), 2: (ry, rx, t)}
    if len(pts) > 4:
        corners[3] = (p["rb"], p["rb"], t)
    return rounded(pts, corners), hy


@glyph("C")
def _(s, p):
    xe, tt, tb, y0, y1 = p["xe"], p["tt"], p["tb"], -OVER, CAP + OVER
    outer = rbox(0, y0, xe, y1, {0: (p["rxo"], p["ryo"], p["to"]), 3: (p["rxob"], p["ryob"], p["to"])})
    inner = rbox(p["wl"], y0 + tt, xe + 4, y1 - tb,
                 {0: (p["rxi"], p["ryi"], p["ti"]), 3: (p["rxib"], p["ryib"], p["ti"])})
    return [minus(blob(s, outer), blob(s, inner)),
            shape(s, hook(xe - 1, p["xo"], y0, 1, p["bt"], p["f"], tt, p["rc"], drop=p["dt"])),
            shape(s, hook(xe - 1, p["xo"], y1, -1, p["bb"], p["f"], tb, p["rc"], drop=p["db"]))]


@glyph("G")
def _(s, p):
    pen = rpen(p)
    xs, ys = p["xs"], p["ys"]
    segs, hy = open_round(p, pen, lambda y1: [(xs, y1), (xs, ys)])
    w = 2 * ext(pen)[0]
    return [chain(s, segs, pen),
            shape(s, hook(p["xe"] - 1, p["xo"], -OVER, 1, p["bt"], p["f"], 2 * hy, p["rc"])),
            shape(s, stroke(xs, xs, w, w, ys, ys + 4, top=(p["sl"], p["sr"])))]


@glyph("U")
def _(s, p):
    xr, hr, ys, hb = p["xr"], p["hr"], p["ys"], p["hb"]
    outer = rbox(0, ys - 2, xr, CAP + OVER, {2: (p["rx"], p["ry"], p["t"]), 3: (p["rxl"], p["ryl"], p["t"])})
    inner = rbox(STEM, ys - 4, xr - hr, CAP + OVER - hb,
                 {2: (p["rx"] - hr, p["ry"] - hb, p["ti"]), 3: (p["rxl"] - STEM, p["ryl"] - hb, p["ti"])})
    return [minus(blob(s, outer), blob(s, inner)),
            shape(s, stem(0, STEM, (SX, SX), (0, 0), y1=ys)),
            shape(s, stem(xr - hr, xr, (SX, SX), (0, 0), y1=ys))]


@glyph("N")
def _(s, p):
    hl, xr, hr = p["hl"], p["xr"], p["hr"]
    diag = shape(s, stroke(p["dt"], p["db"], p["wd"], p["wd"], 0, CAP))
    return [shape(s, stem(0, hl, (SX, 0), (SX, SX))), shape(s, stem(xr - hr, xr, (SX, SX), (0, 0), y1=CAP)),
            s.clip(diag, 0, 0, xr, CAP)]


@glyph("M")
def _(s, p):
    hl, wd, xr, wt, xt, yc, ul, ur = p["hl"], p["wd"], p["xr"], p["wt"], p["xt"], p["yc"], p["ul"], p["ur"]
    b, xs = p["xv"] + p["wv"] / 2, xr - STEM
    xc = xs + ur * (CAP - yc) / CAP
    x0 = xc + (xc - b) * yc / (CAP - yc)
    thick, thin = vee(s, hl + ul - p["od"] + wd / 2, wd, (x0 + xt) / 2, wt, p["xv"], p["wv"], wrt=x0 - xt)
    return [shape(s, stroke(hl / 2 + ul, hl / 2, hl, hl, 0, CAP, (SX, 0), (SX, SX))),
            shape(s, stroke(xs + STEM / 2 + ur, xs + STEM / 2, STEM, STEM, 0, CAP, (0, p["sr"]), (SX, SX))),
            s.clip(thick, 0, 0, xr + max(ur, 0), CAP), s.clip(thin, 0, 0, xr + max(ur, 0), CAP)]


@glyph("A")
def _(s, p):
    xa, ya, aw, wa, wr = p["xa"], p["ya"], p["aw"], p["wa"], p["wr"]
    lt, rt = xa - aw / 2 + wa / 2, xa + aw / 2 - wr / 2
    left = shape(s, stroke(lt, p["xl"], wa, p["wl"], ya, CAP, bot=(p["so"], p["sl"])))
    right = shape(s, stroke(rt, p["xr"], wr, wr, ya, CAP, bot=(p["sr"], p["sr"])))
    e0, e1 = (xa - aw / 2, ya), (p["xl"] - p["wl"] / 2, CAP)
    L = math.hypot(e1[0] - e0[0], e1[1] - e0[1])
    n = (-(e1[1] - e0[1]) / L, (e1[0] - e0[0]) / L)
    yc, hc = p["yc"], p["hc"]
    c = lambda x0, x1, y: x0 + (x1 - x0) * (y - ya) / (CAP - ya)
    return [left, s.cut(right, e0, n, 0), s.fill(c(lt, p["xl"], yc), yc - hc / 2, c(rt, p["xr"], yc), yc + hc / 2)]


def vee(s, xl, wl, xr, wr, xv, wv, ltop=(0, 0), rtop=(0, 0), wrt=None):
    a, b = xv - wv / 2, xv + wv / 2
    left = shape(s, stroke(xl, a + wl / 2, wl, wl, 0, CAP, top=ltop))
    wrt = wr if wrt is None else wrt
    right = shape(s, stroke(xr, b - wr / 2, wrt, wr, 0, CAP, top=rtop))

    def edge(x0, x1, side):
        L = math.hypot(x1 - x0, CAP)
        return (side * CAP / L, -side * (x1 - x0) / L)
    return (s.cut(left, (b, CAP), edge(xr + wrt / 2, b, 1), 0),
            s.cut(right, (a, CAP), edge(xl - wl / 2, a, -1), 0))


@glyph("V")
def _(s, p):
    return list(vee(s, p["xl"], p["wl"], p["xr"], p["wr"], p["xv"], p["wv"], (SX, SX), (SX, SX)))


@glyph("W")
def _(s, p):
    return [*vee(s, p["x1"], p["w1"], p["x3"] - p["w3"] / 2 + p["w2"] / 2 + p["t2"], p["w2"], p["v1"], p["wv"],
                 (SX, SX)),
            *vee(s, p["x3"], p["w3"], p["x4"], p["w4"], p["v2"], p["wv"], (SX, SX), (SX, SX))]


@glyph("Y")
def _(s, p):
    xs, yj, wl, wr = p["xs"], p["yj"], p["wl"], p["wr"]
    on = lambda xt, xj: xt + (xj - xt) * (yj + 1) / yj
    left = shape(s, stroke(p["xl"], on(p["xl"], xs - STEM / 2 + wl / 2), wl, wl, 0, yj + 1, top=(SX, SX)))
    right = shape(s, stroke(p["xr"], on(p["xr"], xs + STEM / 2 - wr / 2), wr, wr, 0, yj + 1, top=(SX, SX)))

    def edge(x0, x1, side):
        L = math.hypot(x1 - x0, yj)
        return (side * yj / L, -side * (x1 - x0) / L)
    a, b = xs - STEM / 2, xs + STEM / 2
    return [s.cut(left, (b, yj), edge(p["xr"] + wr / 2, b, 1), 0),
            s.cut(right, (a, yj), edge(p["xl"] - wl / 2, a, -1), 0),
            shape(s, stem(xs - STEM / 2, xs + STEM / 2, (0, 0), (SX, SX), y0=yj - 0.5))]


@glyph("S")
def _(s, p):
    top, bot = -OVER - p["ot"], CAP + OVER + p["ob"]
    ti, bi, xl, xr = top + p["tt"], bot - p["tb"], p["xl"], p["xrb"]
    a = [(p["xe"], top), (xl, top), (xl, p["ya1"]), (xr - p["wr"], p["yb1"]), (xr - p["wr"], bi), (p["xeb"], bi)]
    b = [(p["xe"], ti), (xl + p["wl"], ti), (xl + p["wl"], p["ya2"]), (xr, p["yb2"]), (xr, bot), (p["xeb"], bot)]
    ta, tb = p["ta"], p["tbn"]
    ea = rounded(a, {1: (p["rxa1"], p["rya1"], ta), 2: (p["ra2"], p["ra2"], 0.552), 3: (p["ra3"], p["ra3"], 0.552),
                     4: (p["rya4"], p["rxa4"], ta)})
    eb = rounded(b, {1: (p["rxb1"], p["ryb1"], tb), 2: (p["rb2"], p["rb2"], 0.552), 3: (p["rb3"], p["rb3"], 0.552),
                     4: (p["ryb4"], p["rxb4"], tb)})
    segs = ea + [line_seg(a[-1], b[-1])] + [q[::-1] for q in eb[::-1]] + [line_seg(b[0], a[0])]
    return [blob(s, segs),
            shape(s, hook(p["xe"] - 1, p["xr"], top, 1, p["bt"], p["f"], p["tt"], p["rc"], drop=p["dt"])),
            shape(s, hook(p["xeb"] + 1, p["xlb"], bot, -1, p["bb"], p["f"], p["tb"], p["rc"], sx=-1,
                          drop=p["db"]))]


@glyph("K")
def _(s, p):
    xa, wa, yj = p["xa"], p["wa"], p["yj"]
    xj = STEM - wa / 2
    arm = shape(s, stroke(xa, xj, wa, wa, 0, yj, top=(SX, p["sr"])))
    lb, wl, yl = p["lb"], p["wl"], p["yl"]
    yp = yj * (xa - wa / 2 - STEM) / (xa + wa / 2 - STEM)
    k = (lb - wl / 2 - STEM) / (CAP - yp)
    lt = lb - k * (CAP - yl)
    leg = shape(s, stroke(lt - k * 6, lb, wl, wl, yl - 6, CAP, bot=(p["bl"], p["br"])))
    L = math.hypot(xj - xa, yj)
    n = (-yj / L, -(xa - xj) / L)
    return [shape(s, stem(0, STEM)), arm, s.cut(leg, (xa - wa / 2, 0), n, 0)]


@glyph("Z")
def _(s, p):
    e, wd = PARAMS["E"], p["wd"]
    zt = e["e1"]
    return [shape(s, hook(zt - 1, 0, 0, 1, e["bh1"], e["f1"], BAR, sx=-1)),
            shape(s, hook(1, e["e3"], CAP, -1, e["bh3"], e["f3"], BAR)),
            shape(s, stroke(zt - wd / 2, wd / 2, wd, wd, 0, CAP))]


@glyph("/")
def _(s, p):
    return [shape(s, stroke(p["xt"], p["xb"], p["w"], p["w"], p["yt"], p["yb"]))]


@glyph("Q")
def _(s, p):
    segs, _ = oval(PARAMS["O"], PEN)
    q0, y0 = p["q0"], CAP + p["qy0"]
    tail = [(q0, y0, 0.5), (q0 - 1.0, y0 + p["qh"], 0.5), (p["qb"], CAP + p["qy1"], p["qr"]),
            (p["q1"], CAP + p["qyt"], TIP), (q0 + p["qw"], y0 - 0.5)]
    return [chain(s, segs, rpen(PARAMS["O"]), closed=True), shape(s, tail)]


BUILD_SET = "".join(sorted(BUILD))


def layer(ch, p=None, slant=None):
    q = PARAMS[ch] if p is None else p
    overrides = {k[1:].upper(): v for k, v in q.items()
                 if k in ("_stem", "_bar", "_serif", "_sx", "_brk")}
    saved = {k: globals()[k] for k in overrides}
    try:
        globals().update(overrides)
        s = Skeleton(CAP, PEN, 1.0, U)
        l = unfold(merge(BUILD[ch](s, q)))
        ds = q.get("_tune", TUNE.get(ch))
        if ds:
            l = unfold(untwist(tune(l, ds, ch), len(l)), tiny=0.1 * U if q.get("_clean") else 1.0)
        l.transform(psMat.skew(math.atan(SLANT if slant is None else slant)))
        return l
    finally:
        globals().update(saved)


def untwist(l, n):
    cs = sorted(merge([l]), key=lambda c: -((c.boundingBox()[2] - c.boundingBox()[0]) *
                                            (c.boundingBox()[3] - c.boundingBox()[1])))
    out = fontforge.layer()
    for c in cs[:n]:
        out += c
    return out


def nodes(l):
    out = []
    for c in l:
        pts = [(p.x, p.y, p.on_curve) for p in c]
        out.append((pts, [i for i, q in enumerate(pts) if q[2]]))
    return out


KNOT = 6.0


def knots(l):
    out = []
    for pts, ix in nodes(l):
        per = sum(math.dist(pts[i][:2], pts[(i + 1) % len(pts)][:2]) for i in range(len(pts))) / U
        out.append(max(4, round(per / KNOT)))
    return out


def tune(l, ds, ch="?"):
    ks = knots(l)
    if sum(ks) != len(ds):
        raise ValueError(f"TUNE[{ch!r}] has {len(ds)} knots for {sum(ks)}: refit it")
    out, k0 = fontforge.layer(), 0

    def unit(a, b):
        d = (b[0] - a[0], b[1] - a[1])
        m = math.hypot(*d)
        return (d[0] / m, d[1] / m) if m > 1e-9 else None

    for (pts, ix), K in zip(nodes(l), ks, strict=True):
        n = len(pts)
        amp = ds[k0:k0 + K]
        k0 += K
        seg = [math.dist(pts[i][:2], pts[(i + 1) % n][:2]) for i in range(n)]
        per = sum(seg)
        arc, acc = [], 0.0
        for i in range(n):
            arc.append(acc)
            acc += seg[i]
        tin, tout = {}, {}
        for i in ix:
            tin[i] = next(u for j in range(1, n) for u in [unit(pts[i - j], pts[i])] if u)
            tout[i] = next(u for j in range(1, n) for u in [unit(pts[i], pts[(i + j) % n])] if u)
        vec = {}
        for i in ix:
            u = arc[i] / per * K
            j, f = int(u) % K, u - int(u)
            d = (amp[j] * (1 - f) + amp[(j + 1) % K] * f) * U
            t = (tin[i][0] + tout[i][0], tin[i][1] + tout[i][1])
            m = math.hypot(*t)
            t = (t[0] / m, t[1] / m) if m > 1e-9 else tin[i]
            vec[i] = (-t[1] * d, t[0] * d)


        m = len(ix)
        close = [math.dist(pts[ix[q]][:2], pts[ix[(q + 1) % m]][:2]) < 0.4 * U for q in range(m)]
        on_line = lambda i: min(abs(pts[i][1]), abs(pts[i][1] - CAP * U)) < 0.02 * U
        if not all(close):
            q0 = next(q for q in range(m) if not close[q - 1])
            run = []
            for q in list(range(q0, m)) + list(range(q0)):
                run.append(ix[q])
                if not close[q]:
                    mean = (sum(vec[i][0] for i in run) / len(run), sum(vec[i][1] for i in run) / len(run))
                    if any(on_line(i) for i in run):
                        mean = (mean[0], 0.0)
                    for i in run:
                        vec[i] = mean
                    run = []
        for i in ix:
            if on_line(i):
                vec[i] = (vec[i][0], 0.0)
        new = [list(q) for q in pts]
        for i in ix:
            for j in (i - 1, i, (i + 1) % n):
                if j == i or not pts[j][2]:
                    new[j][0] += vec[i][0]
                    new[j][1] += vec[i][1]
        for i in ix:
            a, b = tin[i], tout[i]
            if math.degrees(math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1])))) > 8.0:
                continue
            p, q = i - 1, (i + 1) % n
            if pts[p][2] and pts[q][2]:
                e = unit(new[p], new[q])
                if e:
                    t = (new[i][0] - new[p][0]) * e[0] + (new[i][1] - new[p][1]) * e[1]
                    new[i][0], new[i][1] = new[p][0] + e[0] * t, new[p][1] + e[1] * t
                continue
            if pts[p][2] and not pts[q][2]:
                e = unit(new[p], new[i])
                h = math.dist(new[q][:2], new[i][:2])
                if e:
                    new[q][0], new[q][1] = new[i][0] + e[0] * h, new[i][1] + e[1] * h
            elif pts[q][2] and not pts[p][2]:
                e = unit(new[i], new[q])
                h = math.dist(new[p][:2], new[i][:2])
                if e:
                    new[p][0], new[p][1] = new[i][0] - e[0] * h, new[i][1] - e[1] * h
        c = fontforge.contour()
        start = ix[0]
        order = new[start:] + new[:start]
        c.moveTo(*order[0][:2])
        j = 1
        while j <= len(order):
            q = order[j % len(order)]
            if q[2]:
                c.lineTo(*q[:2])
                j += 1
            else:
                c.cubicTo(*order[j][:2], *order[j + 1][:2], *order[(j + 2) % len(order)][:2])
                j += 3
        c.closed = True
        out += c
    return out


def unfold(l, short=0.5 * U, spike=120.0, kink=(1.5, 45.0), stub=1.0 * U, tiny=1.0):
    out = fontforge.layer()
    for c in l:
        pts = [(p.x, p.y, p.on_curve) for p in c]
        k = next(i for i, p in enumerate(pts) if p[2])
        pts = pts[k:] + pts[:k]
        segs, i = [], 0
        while i < len(pts):
            j = i + 1
            while not pts[j % len(pts)][2]:
                j += 1
            segs.append([list(q[:2]) for q in pts[i:j]] + [list(pts[j % len(pts)][:2])])
            i = j

        def tan(seg, end):
            ref = seg[-1] if end else seg[0]
            others = seg[-2::-1] if end else seg[1:]
            q = next((q for q in others if math.dist(q, ref) > 1e-6), None)
            if q is None:
                return None
            v = (ref[0] - q[0], ref[1] - q[1]) if end else (q[0] - ref[0], q[1] - ref[1])
            n = math.hypot(*v)
            return (v[0] / n, v[1] / n)

        def turn(a, b):
            return abs(math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])))

        def joint(m):
            a, b = tan(segs[m], 1), tan(segs[(m + 1) % len(segs)], 0)
            return 0.0 if a is None or b is None else turn(a, b)

        def move_start(seg, q):
            d = (q[0] - seg[0][0], q[1] - seg[0][1])
            seg[0] = list(q)
            if len(seg) == 4:
                seg[1] = [seg[1][0] + d[0], seg[1][1] + d[1]]

        def move_end(seg, q):
            d = (q[0] - seg[-1][0], q[1] - seg[-1][1])
            seg[-1] = list(q)
            if len(seg) == 4:
                seg[2] = [seg[2][0] + d[0], seg[2][1] + d[1]]

        changed = True
        while changed and len(segs) > 3:
            changed = False
            for m, seg in enumerate(segs):
                if len(seg) != 2 or math.dist(seg[0], seg[1]) >= max(short, stub):
                    continue
                prev, nxt = segs[m - 1], segs[(m + 1) % len(segs)]
                before, after = joint(m - 1), joint(m)
                if math.dist(seg[0], seg[1]) >= short and max(before, after) <= spike:
                    continue
                if after > spike or math.dist(seg[0], seg[1]) < tiny:
                    move_start(nxt, seg[0])
                elif before > spike:
                    move_end(prev, seg[1])
                elif kink[0] < before < kink[1] and kink[0] < after < kink[1] and (len(prev) == 4 or len(nxt) == 4):
                    mid = ((seg[0][0] + seg[1][0]) / 2, (seg[0][1] + seg[1][1]) / 2)
                    move_end(prev, mid)
                    move_start(nxt, mid)
                else:
                    continue
                del segs[m]
                changed = True
                break
        for m in range(len(segs)):
            a, b = segs[m], segs[(m + 1) % len(segs)]
            if (len(a) == 4 or len(b) == 4) and kink[0] < joint(m) < kink[1]:
                ta, tb = tan(a, 1), tan(b, 0)
                if len(a) == 2:
                    t = ta
                elif len(b) == 2:
                    t = tb
                else:
                    t = (ta[0] + tb[0], ta[1] + tb[1])
                    t = (t[0] / math.hypot(*t), t[1] / math.hypot(*t))
                q = a[-1]
                if len(a) == 4:
                    r = math.dist(a[2], q)
                    a[2] = [q[0] - t[0] * r, q[1] - t[1] * r]
                if len(b) == 4:
                    r = math.dist(b[1], q)
                    b[1] = [q[0] + t[0] * r, q[1] + t[1] * r]
        nc = fontforge.contour()
        nc.moveTo(*segs[0][0])
        for seg in segs:
            if len(seg) == 2:
                nc.lineTo(*seg[1])
            else:
                nc.cubicTo(*seg[1], *seg[2], *seg[3])
        nc.closed = True
        out += nc
    return out


def contours(l):
    return _contours(l, CAP)


SCENARIO_PARAMS = {
    'A': dict(xa=18.27863, wa=2.27525, ya=0.31612, xl=5.98487, wl=2.21588, xr=28.75313, wr=6.79012,
        aw=4.63075, yc=20.26537, hc=1.72788, sl=6.23412, sr=3.77862, so=3.48413, _stem=6.638,
        _bar=1.906, _serif=0.77163, _sx=3.656, _brk=3.506, _tune=(0.0, -0.09113, -0.24375, -0.237,
        0.09375, -0.0625, -0.025, -0.05987, -0.038, 0.275, -0.00625, -0.1255, 0.19112, -0.21875, -0.263,
        -0.12238, -0.09113, -0.138, -0.00938, 0.09113, -0.23438, -0.07812, -0.03125, -0.00263, 0.00938,
        -0.13125, -0.438, 0.04688, 0.19062, -0.025)),
    'C': dict(xe=18.13413, wl=6.641, tt=1.843, tb=1.72425, rxo=15.2015, ryo=14.762, rxob=15.1645,
        ryob=14.031, rxi=8.474, ryi=11.17838, rxib=9.33887, ryib=14.25938, to=0.52, ti=0.593,
        xo=27.24713, bt=9.863, bb=10.59975, f=0.419, rc=-0.119, dt=1.52188, db=1.47837, _stem=6.638,
        _bar=1.906, _serif=0.881, _sx=3.656, _brk=2.806, _tune=(-0.075, 0.15625, 0.01562, -0.09113,
        -0.275, 0.0, 0.0375, 0.00263, -0.013, -0.00938, -0.10625, -0.44063, 0.09687, -0.125, 0.1005,
        0.0, -0.04688, -0.03125, -0.0625, 0.0, -0.00263, 0.038, 0.062, -0.04063, 0.08125, 0.075,
        -0.01562, 0.17188, 0.2755)),
    'E': dict(e1=19.325, bh1=8.34375, f1=0.86275, e2=16.34975, ym=14.98438, sh=5.0375, e3=21.56537,
        bh3=8.575, f3=-0.0435, _stem=6.41925, _bar=1.79662, _serif=0.84975, _sx=3.89038, _brk=2.69663,
        _tune=(0.09375, 0.0, -0.17188, -0.04688, 0.03125, 0.14062, 0.04688, 0.00937, -0.21563, -0.07863,
        0.03075, -0.18438, 0.05, -0.17238, 0.1, 0.1, -0.10625, -0.24375, 0.0375, -0.05, -0.1625,
        0.12812, -0.20938, -0.10625, -0.25, 0.25, 0.24062, -0.07812, -0.125, 0.0, -0.175, -0.0005,
        -0.02237, -0.013)),
    'I': dict(_stem=6.43487, _bar=1.906, _serif=0.8185, _sx=3.73413, _brk=2.57475, _tune=(-0.22187,
        -0.25625, -0.34063, -0.013, -0.175, -0.04375, 0.00675, 0.025, 0.0125, -0.3, 0.1005, 0.05,
        -0.04425, 0.01562, 0.09375, 0.263, 0.0)),
    'L': dict(e=21.37475, bh=9.381, f=0.131, _stem=6.40362, _bar=1.74975, _serif=0.78725, _sx=3.781,
        _brk=2.481, _tune=(-0.31562, -0.21563, -0.15363, -0.063, -0.21875, -0.10675, -0.07187, -0.06562,
        0.212, -0.0375, 0.05313, -0.0625, 0.16562, 0.1, 0.07863, 0.05, 0.02237, 0.025, -0.17188,
        -0.07812, -0.07812, -0.15363, 0.0)),
    'N': dict(hl=2.37475, xr=24.03775, hr=2.269, dt=3.41925, db=25.637, wd=8.14975, _stem=6.638,
        _bar=1.906, _serif=0.64663, _sx=4.01538, _brk=3.506, _tune=(0.725, 0.55363, 0.2505, -0.08438,
        -0.13125, -0.1875, -0.288, -0.03075, -0.08438, -0.063, -0.038, -0.02862, -0.03125, -0.07812,
        -0.1745, -0.1125, -0.10938, -0.113, 0.825, 0.85262, 0.65, -0.175, -0.388, -0.1875, -0.025,
        -0.338, -0.00937, 0.388, 0.38125, 0.338, 0.0375, -0.18125, -0.425, -0.35, 0.00262, 0.05)),
    'O': dict(w=31.188, kx=1.058, ry=13.8005, t=0.54, ang=0.025, _stem=6.638, _bar=1.906, _serif=0.881,
        _sx=3.656, _brk=2.806, _tune=(0.45, 0.45625, 0.19062, -0.49062, -0.15313, -1.025, 0.125, 0.45,
        0.475, -0.2125, -0.675, -0.44688, -0.20625, -0.05937, -0.34737, -0.425, -0.325, -0.1, -0.1,
        -0.2, -0.33437, -0.10312, 0.06563, -0.2505, -0.45, -0.288, -0.1, 0.14688, -0.4, -0.35, -0.21563)),
    'R': dict(xb=22.031, yb=16.28412, rx=6.89663, ry=7.881, t=0.615, w=6.363, lt=11.64038, lb=20.05,
        wl=6.86587, tail=3.24112, _stem=6.40362, _bar=1.906, _serif=0.756, _sx=3.92163, _brk=3.46225,
        _tune=(0.28125, -0.15625, 0.00888, 0.05937, -0.0625, -0.06925, -0.10625, -0.13125, 0.15, -0.062,
        0.375, 0.3375, 0.15, 0.0, -0.12762, -0.0495, -0.03388, -0.06513, 0.0, -0.04688, 0.0495, 0.20938,
        0.475, -0.24062, -0.03125, -0.10312, -0.0125, 0.1745, -0.12137, 0.10938, -0.22862, 0.15,
        -0.19737, -0.1505)),
    'S': dict(xe=11.05937, xr=20.3755, xeb=8.97475, xlb=0.2745, ot=-0.356, ob=0.0065, tt=1.542,
        tb=1.75188, xl=-0.587, wl=5.886, xrb=22.762, wr=6.036, ya1=13.5, yb1=20.85, ya2=8.87475,
        yb2=16.67162, rxa1=8.668, rya1=8.918, ra2=6.987, ra3=4.05987, rxa4=5.69038, rya4=4.86662,
        ta=0.465, rxb1=5.631, ryb1=4.38775, rb2=2.48225, rb3=7.043, rxb4=9.59037, ryb4=8.72737,
        tbn=0.559, bt=9.762, bb=11.469, f=0.19662, rc=0.07525, dt=0.9745, db=1.9565, _stem=6.638,
        _bar=1.906, _serif=0.881, _sx=3.656, _brk=2.806, _tune=(0.35, -0.1965, -0.228, -0.11375,
        -0.08325, -0.048, -0.015, -0.11275, 0.021, 0.14263, -0.03, -0.272, -0.308, -0.1355, -0.029,
        -0.0625, 0.21887, -0.15, -0.19, -0.107, 0.03125, -0.05563, 0.057, -0.048, 0.05488, 0.00075,
        -0.03062, -0.109, -0.075, -0.319)),
    'T': dict(w=24.762, bh=9.35625, f=0.90938, xc=12.5125, _stem=6.388, _bar=1.81225, _serif=0.5685,
        _sx=4.01225, _brk=2.931, _tune=(-0.23438, -0.1375, -0.08125, -0.05625, -0.0755, -0.275,
        -0.04375, 0.16925, -0.07812, 0.0125, 0.03438, 0.05625, 0.09112, 0.27187, 0.34688, 0.44688,
        0.175, 0.01562, 0.02862, 0.05625, 0.09688, 0.18438, 0.20675, 0.013, 0.0)),
}
BACKGROUND_PARAMS = {
    'A': dict(xa=18.15363, wa=1.97838, ya=0.09737, xl=5.95362, wl=2.21588, xr=28.4875, wr=6.68075,
        aw=4.27137, yc=20.3435, hc=1.60288, sl=5.99975, sr=4.013, so=3.64038, _stem=6.638, _bar=1.906,
        _serif=0.72475, _sx=3.656, _brk=3.42788, _tune=(0.07305, -0.0755, -0.39375, 0.05362, 0.15,
        -0.15, -0.05696, -0.08487, 0.08928, 0.12284, 0.04169, 0.15198, -0.037, 0.13438, -0.413,
        -0.12238, -0.0755, 0.05887, -0.05, -0.05652, -0.08438, -0.09375, -0.03125, 0.013, 0.01015,
        -0.13223, -0.588, -0.2178, 0.21562, 0.00832)),
    'B': dict(xb1=20.59431, ym=14.11187, xb2=22.76468, r1=6.08328, r2=7.59897, t=0.5275, w1=6.21982,
        w2=6.19457, _stem=6.34023, _bar=1.906, _serif=0.84362, _sx=4.24409, _brk=3.03101,
        _tune=(0.11404, 0.19164, 0.09158, -0.41842, 0.0, 0.28732, -0.00379, -0.30763, 0.04317, 0.06475,
        -0.08155, -0.2255, 0.06907, 0.13646, -0.26022, -0.63366, -0.23872, 0.51329, 0.23438, 0.13487,
        0.07762, 0.16332, -0.04189, -0.087, -0.08075, -0.01825, -0.125, 0.20987, -0.22187, 0.00263,
        0.07812, 0.24975, -0.01938, -0.34178)),
    'C': dict(xe=17.91188, wl=6.48588, tt=1.69597, tb=1.61537, rxo=15.074, ryo=15.462, rxob=15.3809,
        ryob=14.731, rxi=8.6615, ryi=12.32225, rxib=10.32809, ryib=15.05514, to=0.57682, ti=0.64642,
        xo=26.85664, bt=10.17489, bb=10.61909, f=0.61299, rc=-0.16812, dt=0.90383, db=1.44619,
        _stem=6.638, _bar=1.906, _serif=0.881, _sx=3.656, _brk=2.806, _tune=(-0.15937, 0.16129, 0.0,
        -0.09113, -0.37015, -0.04688, 0.475, 0.08075, 0.0495, 0.07857, 0.22687, -0.76875, 0.175,
        -0.17603, 0.05495, 0.03327, -0.05803, 0.0625, -0.01562, -0.01525, 0.12201, 0.038, 0.01512,
        -0.04169, 0.09405, 0.01444, -0.07893, 0.1933, 0.4613)),
    'D': dict(xd=27.47301, rx=11.35358, ry=14.70337, t=0.5916, w=6.42371, _stem=6.35057, _bar=1.906,
        _serif=1.09447, _sx=4.13556, _brk=2.22527, _tune=(-0.425, -0.26188, -0.12385, -0.04688,
        -0.03125, -0.26409, -0.04611, 0.02079, -0.52237, -0.24062, -0.09425, -0.46413, -0.56123,
        -0.04401, -0.24025, -0.00388, 0.20251, 0.28254, 0.38082, 0.25, 0.23882, -0.19041, -0.3067,
        -0.3875, 0.2812, -0.29506, 0.15607, 0.08546, -0.08993, -0.10809, -0.05987, -0.02862, -0.013)),
    'G': dict(rx=12.85861, ry=13.11045, t=0.55415, ang=0.0047, xe=20.36411, xo=26.81164, bt=9.3019,
        f=0.60084, rc=1.44756, xs=24.51355, ys=16.40357, rb=2.7634, sl=3.4414, sr=2.73059, _stem=6.638,
        _bar=1.906, _serif=0.55538, _sx=3.656, _brk=2.59178, _tune=(0.14226, 0.01883, 0.16373, 0.12705,
        -0.33596, -0.40923, -0.37686, -0.22222, -0.12311, -0.12597, -0.03876, -0.10938, -0.29495,
        -0.112, -0.14664, 0.37764, -0.23982, -0.01763, -0.038, 0.137, -0.663, 0.02104, -0.24058,
        0.44809, 0.16708, -0.48225, -1.1, 0.10485, 0.14377, 0.42877, -0.53739, -0.35701)),
    'K': dict(xa=20.09614, wa=2.40752, yj=17.56752, sr=3.41776, lb=20.13188, wl=7.01309, yl=15.306,
        bl=3.36311, br=3.01259, _stem=6.03538, _bar=1.906, _serif=0.74173, _sx=4.88612, _brk=2.96785,
        _tune=()),
    'N': dict(hl=2.45288, xr=23.994, hr=2.25338, dt=3.49737, db=25.6495, wd=8.13412, _stem=6.638,
        _bar=1.906, _serif=0.5685, _sx=3.99975, _brk=3.506, _tune=(1.02036, 0.84237, 0.36488, -0.18599,
        -0.1625, -0.1875, -0.538, 0.04738, -0.1692, -0.01613, -0.02237, -0.02862, -0.04688, -0.34375,
        0.09113, -0.12812, -0.15625, 0.02097, 0.7625, 1.19637, 0.66304, -0.46553, 0.31512, -0.01562,
        -0.25938, -0.44704, -0.05416, -0.362, 0.36386, 0.1649, -0.07187, -0.22812, -0.44875, -0.23475,
        0.11214, -0.0173)),
    'O': dict(w=31.188, kx=1.058, ry=13.8005, t=0.54, ang=0.025, _stem=6.638, _bar=1.906, _serif=0.881,
        _sx=3.656, _brk=2.806, _tune=(0.7, 0.8, 0.40937, -0.31847, -0.42692, -1.2125, -0.125, 0.2,
        0.725, -0.43125, -0.32433, -0.19688, -0.3625, 0.1889, -0.28487, -0.6125, -0.575, -0.35, -0.35,
        -0.45, -0.6, -0.13437, -0.00149, -0.00621, -0.2, -0.19121, 0.04679, 0.35667, -0.11875, -0.4125,
        -0.12188)),
    'R': dict(xb=22.01126, yb=16.20997, rx=7.08124, ry=7.64415, t=0.58206, w=6.39447, lt=11.32666,
        lb=20.17724, wl=6.70234, tail=2.61253, _stem=6.40188, _bar=1.906, _serif=1.00449, _sx=4.17874,
        _brk=3.1659, _tune=(0.07914, 0.19375, -0.3255, 0.04308, -0.0375, -0.06925, -0.17458, -0.15729,
        -0.3875, 0.04449, 0.11983, 0.53151, 0.1875, -0.01512, -0.13532, -0.0495, -0.00263, -0.00888,
        0.0375, -0.14816, -0.06612, 0.0375, 0.0, -0.13713, -0.28484, -0.14585, 0.13474, -0.12945,
        -0.08075, 0.03409, -0.1255, 0.15391, 0.17387, 0.037)),
    'S': dict(xe=11.2, xr=20.563, xeb=9.1935, xlb=0.212, ot=-0.106, ob=-0.056, tt=1.667, tb=1.705,
        xl=-0.587, wl=6.10475, xrb=22.762, wr=6.06725, ya1=13.29688, yb1=20.81875, ya2=8.8435,
        yb2=16.406, rxa1=8.38987, rya1=9.35237, ra2=6.987, ra3=4.013, rxa4=5.706, rya4=4.38225,
        ta=0.465, rxb1=4.2935, ryb1=3.63775, rb2=2.29475, rb3=7.043, rxb4=9.806, ryb4=8.1055, tbn=0.559,
        bt=9.79325, bb=11.094, f=-0.10025, rc=0.294, dt=0.787, db=1.894, _stem=6.638, _bar=1.906,
        _serif=0.881, _sx=3.656, _brk=2.806, _tune=(0.50625, -0.509, -0.28085, -0.30125, 0.24487,
        0.17512, -0.18313, -0.23775, -0.1473, 0.48638, -0.28, -0.42101, -0.55077, -0.10425, 0.02569,
        -0.02776, 0.24601, -0.06057, 0.185, -0.0445, 0.03516, -0.20075, 0.557, -0.45425, 0.25298, 0.282,
        -0.35875, 0.141, -0.45, -0.33796)),
    'U': dict(xr=23.84928, hr=2.17394, ys=22.12774, rx=8.49715, ry=9.75977, t=0.52749, hb=2.35741,
        ti=0.48698, rxl=12.70309, ryl=10.13382, _stem=6.44477, _bar=1.906, _serif=0.56371, _sx=4.09129,
        _brk=3.31752, _tune=(0.98626, 0.3755, 0.2498, -0.17669, -0.175, 0.0064, -0.197, -0.19425,
        -0.33239, 0.24146, 0.175, 0.013, -0.12098, -0.06803, -0.35, -0.11827, 0.02008, -0.638, -0.075,
        0.20625, -0.37126, -0.138, 0.125, -0.212, 0.22001, -0.14708, 0.08427, -0.13601, 0.00887,
        0.31875, -0.087)),
}
# Keyed by word_key(word), the word's SHA-256, so the official word is not stored.
ROW_PARAMS = {"222a857ddb780ba0422608f9245352d4d247acdbe8922c542b015ac7c9d6a56c": SCENARIO_PARAMS, "586f188b0eddc1210dd8493be2723edfc810de36ac3c68fcdc1a5ef0e76a2528": BACKGROUND_PARAMS}


SETS["cf888f8318b597b573b26a108bb71f0896e998299382dfbe86493046b56a25b6@1.13"] = (0.0, 35.37687, 67.37937, 85.39938, 115.37937, 133.42375, 164.37938, 182.42937, 214.62813)
SETS["bf151de81086363eb82fce98b85d9cc4f7e550d6e4d15c6be2c04d1579df538c@1.13"] = (0.0, 45.18812, 78.02375, 112.435, 159.42937, 189.36375, 208.62813, 236.42375)
SETS["cf888f8318b597b573b26a108bb71f0896e998299382dfbe86493046b56a25b6@17.34"] = (0.0, 36.1425, 68.12937, 86.14938, 115.12937, 133.17375, 164.12938, 182.17937, 214.37813)
SETS["bf151de81086363eb82fce98b85d9cc4f7e550d6e4d15c6be2c04d1579df538c@17.34"] = (0.0, 44.93812, 77.77375, 112.185, 160.14812, 189.12938, 208.3625, 237.17375)
SETS["d0b1a869600d21076a3fa8b5f52546e55920588ce73dfb7e56ca9dc08bdcaf3e@17.34"] = (0.0, 36.1375, 68.18875, 99.14437, 117.9375, 154.34438, 192.39312)
