"""Buttonraster rendering support."""
import argparse
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import buttonvec as V
from .menu_effects import Label, blur, coverage, path, outline_table, pen_layout
from .word_key import word_key

NAVY = (33, 32, 49)
OUTLINE = 3.05
GLOW_SIGMA, GLOW_K = 1.8, 0.6


STYLES = {
    "501.3": dict(near=(222, 209, 247), far=(0, 0, 0), full=0.6),
    "493.9": dict(near=(242, 244, 255), far=(243, 245, 255), full=1.0),
    "508.0": dict(near=(226, 243, 255), far=(168, 180, 190), full=0.2),
}


FILL_BANDS = [(2, (255, 255, 247)), (4, (255, 255, 239)), (6, (255, 255, 231)), (12, (255, 251, 222)),
              (13, (255, 255, 239)), (14, (255, 255, 247))]

STYLE_WIDTH = {'501.3': 1.0, '493.9': 1.06, '508.0': 1.07}
# Keyed by word_key(word), the word's SHA-256, so the official word is not stored.
ROW_PLACES = {('493.9', "9982d716901b7d64b9b3840bca988930db6758b5519520111769bcc16b13cd29"): (0.0,
                                 16.187,
                                 30.9161,
                                 47.4988,
                                 63.4746,
                                 80.3894,
                                 101.0083,
                                 117.0337,
                                 131.4218,
                                 148.0005,
                                 161.3092,
                                 177.16,
                                 191.7442,
                                 206.4323,
                                 220.793),
 ('493.9', "2ca05767d5dce89655e2811605a51c3937f8786901f81571c6f4eea90e073a94"): (0.0,
                                 16.1664,
                                 30.5041,
                                 46.8998,
                                 60.3527,
                                 76.427,
                                 90.7523,
                                 105.4643,
                                 119.2116,
                                 142.0113,
                                 159.1081,
                                 174.1084,
                                 190.6646,
                                 210.7836,
                                 225.8547),
 ('493.9', "7a66eabf637f6a4c162d331966b5966e2656f4051ae52acbcb1af0a3ab2eff10"): (0.0, 17.373, 30.9863, 46.0227),
 ('493.9', "c210bfdf28cec3d9edb636d798e0f2aaf0d1cfda0b7f8d469b1a40650686ec02"): (0.0, 14.2966, 29.8169, 46.7269),
 ('493.9', "bca41978330e868e846dfdc61d5c2fdd5b60ccba38d80acb66307c7d7a6ad80e"): (0.0, 15.4802, 28.5387, 46.3642, 74.3436, 81.6616, 98.809, 112.356),
 ('493.9', "4e6ca2ef7fe047dfc5af32b6fa3a991e5917e9590fd7c008382152b8b2a813a9"): (0.0, 15.4829, 30.7152, 45.0021, 62.9523, 79.0608, 84.4378, 101.9976),
 ('493.9', "ae75b2880b5b5e401de30cd4b3fbbdcb8a4248f0c0018da2135cf7779a0974d5"): (0.0, 21.2389, 33.931, 50.9793, 65.3836, 83.344),
 ('501.3', "4bd4cc7abb671fa34c95be33912d1cd339835be696805421d41f7e01771a4a47"): (0.0,
                              17.9337,
                              34.1394,
                              49.8787,
                              56.1769,
                              66.8308,
                              84.8034,
                              98.7949,
                              104.7848,
                              121.646,
                              138.5168,
                              145.7993,
                              162.78),
 ('501.3', "b259d2df08fa205517e750d00a1c9958cfa801026adcb09f3b0824cd5dca032f"): (0.0, 15.2707, 33.1057),
 ('501.3', "67298f78a4f0de9ead1317c9cae3766c55e509458c353f1200d4eec6109fbc06"): (0.0,
                           15.3078,
                           32.3226,
                           39.2426,
                           57.3613,
                           73.3371,
                           89.1179,
                           106.4751,
                           121.1004,
                           136.2323),
 ('501.3', "f291e02b6a22bfe7b0817f629bd9ae14740623a63255607acca367d5327d7810"): (0.0,
                                15.682,
                                31.8354,
                                46.6642,
                                61.4361,
                                80.306,
                                106.0453,
                                122.3999,
                                136.5103,
                                150.9492,
                                166.8041,
                                174.8593,
                                191.9655,
                                209.1064),
 ('501.3', "60c2029e388cb55b086266335c3f21cdff3fa11fc0c797854d9e88cb53fd32f3"): (0.0,
                                 17.0085,
                                 32.3632,
                                 50.5135,
                                 64.6373,
                                 81.7882,
                                 98.1524,
                                 113.7249,
                                 128.8316,
                                 153.7079,
                                 167.9308,
                                 191.2966,
                                 197.732,
                                 211.9128,
                                 229.9587),
 ('501.3', "1f94a4d0dc148c0e7a00cf89551d543f6f9b62a1bdd17394a51b4f8bd0895fae"): (0.0,
                           16.843,
                           32.0117,
                           49.8858,
                           63.7463,
                           80.7143,
                           96.7389,
                           112.133,
                           126.9661,
                           143.0152),
 ('501.3', "89dbf71048801678ca4abfbaa3ea8f7c651aae193357a3e23d68e21512cd07f5"): (0.0, 17.1249, 32.4299, 44.5112, 62.2313, 79.6424, 91.7326),
 ('501.3', "b1e752fc291929422f412e4b9784d8729e6ab3c9bea12fa29a46443b8bec6253"): (0.0,
                                 15.1585,
                                 30.819,
                                 46.6446,
                                 64.7309,
                                 85.5638,
                                 92.0589,
                                 118.2331,
                                 133.1046,
                                 140.39,
                                 158.347,
                                 174.6601,
                                 190.5423,
                                 197.999,
                                 214.7951),
 ('501.3', "ba4afafc4b833426b1beb0ccc4af7a9c6e174d246d3363b972d825e4aa08f9ba"): (0.0,
                                15.1484,
                                30.9432,
                                46.6511,
                                64.7756,
                                85.6169,
                                92.1241,
                                117.9503,
                                134.1455,
                                149.6006,
                                167.8098,
                                184.9152,
                                201.4413,
                                223.6053),
 ('501.3', "cfe7e52822cb0abca3d434af6e0be3aab2fc534c8994575896bd633151ba0528"): (0.0, 14.8945, 29.1169, 43.686, 57.7365, 73.0251, 88.2739),
 ('501.3', "6746cfa6bdc11f969fe18974ebc7fbde852cc2a2201537b4bea11ad3b9b01f98"): (0.0, 13.7914, 31.4401, 46.5212, 62.8423, 77.4108),
 ('501.3', "034d21af73357705c6709036621ce2ed6ed29e5ec94672d775143da6f9c5308c"): (0.0,
                             13.357,
                             31.2997,
                             57.9078,
                             75.9494,
                             82.8031,
                             97.4401,
                             111.2479,
                             129.8919,
                             146.2669,
                             163.4801),
 ('501.3', "84f6046dc0b78866f1eeda8fbd321aaf25781f553b0441998585d4545df9fba3"): (0.0,
                            14.0564,
                            31.6611,
                            45.8528,
                            58.3676,
                            70.2754,
                            86.5273,
                            103.6384,
                            120.2038,
                            135.4475,
                            150.7681),
 ('501.3', "e7d9b8c6e712adcad6efbf5f06131030fb2d5a42ac51b4292507b8e31fa1674b"): (0.0,
                              16.1198,
                              34.103,
                              54.1046,
                              77.6439,
                              93.6946,
                              107.2463,
                              121.9211,
                              137.6616,
                              145.3073,
                              162.3099,
                              179.7374),
 ('501.3', "8eab2d06693ce59a75766eb9880556bf0a929ff36116207015c9365ce094b432"): (0.0, 17.8275, 32.1061, 50.0655, 65.7703, 83.6566, 90.0131, 106.7292),
 ('501.3', "0c23e3138346c4611f1b981f2aeaf658b6130461a53d13dd9a005a88578e7310"): (0.0, 14.1005, 22.2109, 35.9398),
 ('501.3', "d438686e54df812d53df373e2ca6a962d0c9197dc869310499748cbe7af5de26"): (0.0, 12.7293, 29.0511, 47.1572),
 ('501.3', "a34396cc70bcb1fe469abb7e932313db0a72577cdb19134a13a5cd7f71c95c04"): (0.0,
                              18.4692,
                              33.3363,
                              48.3933,
                              56.6055,
                              73.9167,
                              88.9714,
                              114.1872,
                              130.2661,
                              145.1181,
                              161.4584,
                              175.466),
 ('501.3', "565339bc4d33d72817b583024112eb7f5cdf3e5eef0252d6ec1b9c9a94e12bb3"): (0.0, 17.7455),
 ('501.3', "272776a0df04c4c5e20f12bfd4b6d2bca6d00cc852263f607351eee0b7d09eea"): (0.0, 18.085, 32.8941, 51.4737, 65.4062, 71.5789, 85.1833),
 ('501.3', "5443880cfd8cd7bb7438f98fa138b9afe6bdb65579748851a89b6f144775ef03"): (0.0,
                           16.6117,
                           30.6636,
                           46.296,
                           64.6338,
                           78.6661,
                           94.6835,
                           110.3039,
                           117.2941,
                           135.9171),
 ('501.3', "c210bfdf28cec3d9edb636d798e0f2aaf0d1cfda0b7f8d469b1a40650686ec02"): (0.0, 15.4634, 32.0867, 49.466),
 ('501.3', "d33e6bb32210cbc4c09ced6352de5d85f832ca3e26076d11f782c61c7d032df4"): (0.0, 14.4835, 29.8829, 47.405, 63.6418, 78.3764, 91.9693, 108.3309, 126.5753),
 ('501.3', "4f2d1c47d5c9e083f397dfabfc438c6c50f76fd234e4a298b89754dd053510d5"): (0.0, 16.0783, 31.2653, 49.2341, 66.766, 81.7137, 97.512),
 ('501.3', "4613822d04f273d0df31e79a3dd9bc7bea0acd15db18ef9e7515ea66a84d6691"): (0.0, 15.431, 34.0541, 51.4067, 68.8985),
 ('501.3', "565a0fb5d433062893f1e583080a6c03af991663d5efc65813c4eb1b93da258e"): (0.0, 15.2522, 33.792, 51.1359, 68.797, 93.3107, 109.0449, 123.7157, 138.2714),
 ('501.3', "d621c1a7169f2ca51bc8674da52e9572178a66a1dde88da24da78fe4951703f9"): (0.0, 14.2711, 29.6745, 44.4801, 60.252, 75.5914),
 ('501.3', "40ee0e97702adc8b4950296c1e6782b8de0c4702913bf68282c25c8a2b2358c9"): (0.0,
                               15.8365,
                               28.5347,
                               44.6101,
                               60.0994,
                               77.5515,
                               94.3679,
                               117.8691,
                               132.9285,
                               140.7657,
                               152.3935,
                               168.236,
                               183.2272),
 ('501.3', "b44419491c4b54997f418911bc7a43cfb396ee9749ca348fd4c4ec0f1614a577"): (0.0,
                           16.5904,
                           24.7058,
                           45.5012,
                           60.612,
                           73.0936,
                           87.8039,
                           105.4558,
                           122.4065,
                           138.2772),
 ('501.3', "c34feb9dd7870b0e50e718705b1e7c7ee84d0a77b7aff5e2d7118053ab937ced"): (0.0, 17.6685, 25.3647, 41.3463, 55.6441, 71.9044, 87.6278, 94.6725, 113.4081),
 ('501.3', "a430e6d293d0f1d5c9ec2e3ecd08eb143e053deb9532d1f8ce332fe05d119846"): (0.0, 16.3007, 35.5565, 42.8455, 59.9748),
 ('508.0', "de956cf5e135821be80428369b28aab96b246eefd7876e3b6ebdd399ef7c3e52"): (0.0, 14.0569, 34.4298, 50.7109, 66.868, 71.7545),
 ('508.0', "cf888f8318b597b573b26a108bb71f0896e998299382dfbe86493046b56a25b6"): (0.0, 17.0661, 31.9827, 38.4878, 51.4513, 56.4827, 71.3884, 78.0131, 91.6666),
 ('508.0', "89dbf71048801678ca4abfbaa3ea8f7c651aae193357a3e23d68e21512cd07f5"): (0.0, 15.7722, 29.34, 39.223, 56.0519, 71.602, 82.7465),
 ('508.0', "6746cfa6bdc11f969fe18974ebc7fbde852cc2a2201537b4bea11ad3b9b01f98"): (0.0, 12.3149, 29.621, 43.7068, 58.9636, 72.7782),
 ('508.0', "1e367f240be1b68c21cfbed3dd0026a80008facf31d7ca1db684447384f06b19"): (0.0, 13.9657, 20.24, 37.9222, 53.4794),
 ('508.0', "23e04084df125efcc954cba154e2f4c9e06e45d9a0111c8789bc7e6e723f5782"): (0.0, 16.567, 22.584, 36.263, 50.1945, 67.493, 81.6348),
 ('508.0', "0c23e3138346c4611f1b981f2aeaf658b6130461a53d13dd9a005a88578e7310"): (0.0, 13.3085, 19.6076, 32.5025),
 ('508.0', "bf151de81086363eb82fce98b85d9cc4f7e550d6e4d15c6be2c04d1579df538c"): (0.0, 18.5556, 35.3128, 51.3566, 72.6982, 85.8045, 91.5531, 105.5693),
 ('508.0', "10aae6592b39c03b0a584e048c3eee188998f69f1b7f28b924f3e83e74cdaa41"): (0.0, 18.1036, 31.1154, 44.4414, 50.1276, 66.262, 79.6459, 94.5236),
 ('508.0', "d0b1a869600d21076a3fa8b5f52546e55920588ce73dfb7e56ca9dc08bdcaf3e"): (0.0, 18.0155, 31.1856, 45.596, 51.3677, 68.9966, 85.0127),
 ('508.0', "0747c96a23dbfa586507337fc4ed4bdddb7b05833243622c4e6bb357b87adb0c"): (0.0, 15.0329, 28.0496, 43.2881, 59.2315, 78.22),
 ('508.0', "ae75b2880b5b5e401de30cd4b3fbbdcb8a4248f0c0018da2135cf7779a0974d5"): (0.0, 21.9508, 34.0296, 51.7554, 65.7907, 83.7784)}
# Keyed by word_key(word), the word's SHA-256, so the official word is not stored.
ROW_PHASE = {('493.9', "9982d716901b7d64b9b3840bca988930db6758b5519520111769bcc16b13cd29"): (0.25, 0.0),
 ('493.9', "2ca05767d5dce89655e2811605a51c3937f8786901f81571c6f4eea90e073a94"): (0.4927, 0.4763),
 ('493.9', "7a66eabf637f6a4c162d331966b5966e2656f4051ae52acbcb1af0a3ab2eff10"): (0.6264000000000001, 0.06559999999999999),
 ('493.9', "c210bfdf28cec3d9edb636d798e0f2aaf0d1cfda0b7f8d469b1a40650686ec02"): (0.1875, -0.0625),
 ('493.9', "bca41978330e868e846dfdc61d5c2fdd5b60ccba38d80acb66307c7d7a6ad80e"): (0.0023, 0.4598),
 ('493.9', "4e6ca2ef7fe047dfc5af32b6fa3a991e5917e9590fd7c008382152b8b2a813a9"): (0.25, 0.0),
 ('493.9', "ae75b2880b5b5e401de30cd4b3fbbdcb8a4248f0c0018da2135cf7779a0974d5"): (0.3647, 0.5095000000000001),
 ('501.3', "4bd4cc7abb671fa34c95be33912d1cd339835be696805421d41f7e01771a4a47"): (0.6145, 0.0175),
 ('501.3', "b259d2df08fa205517e750d00a1c9958cfa801026adcb09f3b0824cd5dca032f"): (0.125, 0.0),
 ('501.3', "67298f78a4f0de9ead1317c9cae3766c55e509458c353f1200d4eec6109fbc06"): (0.1947, -0.0271),
 ('501.3', "f291e02b6a22bfe7b0817f629bd9ae14740623a63255607acca367d5327d7810"): (0.1875, 0.0),
 ('501.3', "60c2029e388cb55b086266335c3f21cdff3fa11fc0c797854d9e88cb53fd32f3"): (0.375, 0.0),
 ('501.3', "1f94a4d0dc148c0e7a00cf89551d543f6f9b62a1bdd17394a51b4f8bd0895fae"): (0.2775, 0.0344),
 ('501.3', "89dbf71048801678ca4abfbaa3ea8f7c651aae193357a3e23d68e21512cd07f5"): (0.1817, -0.0281),
 ('501.3', "b1e752fc291929422f412e4b9784d8729e6ab3c9bea12fa29a46443b8bec6253"): (0.25, 0.0),
 ('501.3', "ba4afafc4b833426b1beb0ccc4af7a9c6e174d246d3363b972d825e4aa08f9ba"): (0.2347, 0.004199999999999995),
 ('501.3', "cfe7e52822cb0abca3d434af6e0be3aab2fc534c8994575896bd633151ba0528"): (0.1691, 0.0035),
 ('501.3', "6746cfa6bdc11f969fe18974ebc7fbde852cc2a2201537b4bea11ad3b9b01f98"): (0.19, 0.035),
 ('501.3', "034d21af73357705c6709036621ce2ed6ed29e5ec94672d775143da6f9c5308c"): (0.2142, 0.006),
 ('501.3', "84f6046dc0b78866f1eeda8fbd321aaf25781f553b0441998585d4545df9fba3"): (0.375, 0.0),
 ('501.3', "e7d9b8c6e712adcad6efbf5f06131030fb2d5a42ac51b4292507b8e31fa1674b"): (0.375, 0.0),
 ('501.3', "8eab2d06693ce59a75766eb9880556bf0a929ff36116207015c9365ce094b432"): (0.3539, 0.0344),
 ('501.3', "0c23e3138346c4611f1b981f2aeaf658b6130461a53d13dd9a005a88578e7310"): (0.1999, 0.0251),
 ('501.3', "d438686e54df812d53df373e2ca6a962d0c9197dc869310499748cbe7af5de26"): (0.1875, 0.0),
 ('501.3', "a34396cc70bcb1fe469abb7e932313db0a72577cdb19134a13a5cd7f71c95c04"): (0.1013, 0.009),
 ('501.3', "565339bc4d33d72817b583024112eb7f5cdf3e5eef0252d6ec1b9c9a94e12bb3"): (0.25, 0.1875),
 ('501.3', "272776a0df04c4c5e20f12bfd4b6d2bca6d00cc852263f607351eee0b7d09eea"): (0.4375, 0.0),
 ('501.3', "5443880cfd8cd7bb7438f98fa138b9afe6bdb65579748851a89b6f144775ef03"): (0.4954, -0.0199),
 ('501.3', "c210bfdf28cec3d9edb636d798e0f2aaf0d1cfda0b7f8d469b1a40650686ec02"): (0.28, -0.0048),
 ('501.3', "d33e6bb32210cbc4c09ced6352de5d85f832ca3e26076d11f782c61c7d032df4"): (0.0625, 0.0),
 ('501.3', "4f2d1c47d5c9e083f397dfabfc438c6c50f76fd234e4a298b89754dd053510d5"): (0.25, 0.0),
 ('501.3', "4613822d04f273d0df31e79a3dd9bc7bea0acd15db18ef9e7515ea66a84d6691"): (0.25, 0.0),
 ('501.3', "565a0fb5d433062893f1e583080a6c03af991663d5efc65813c4eb1b93da258e"): (0.3466, 0.0364),
 ('501.3', "d621c1a7169f2ca51bc8674da52e9572178a66a1dde88da24da78fe4951703f9"): (0.3125, 0.0),
 ('501.3', "40ee0e97702adc8b4950296c1e6782b8de0c4702913bf68282c25c8a2b2358c9"): (0.4705, 0.0219),
 ('501.3', "b44419491c4b54997f418911bc7a43cfb396ee9749ca348fd4c4ec0f1614a577"): (0.3649, 0.0423),
 ('501.3', "c34feb9dd7870b0e50e718705b1e7c7ee84d0a77b7aff5e2d7118053ab937ced"): (0.4101, -0.0271),
 ('501.3', "a430e6d293d0f1d5c9ec2e3ecd08eb143e053deb9532d1f8ce332fe05d119846"): (0.0, 0.0),
 ('508.0', "de956cf5e135821be80428369b28aab96b246eefd7876e3b6ebdd399ef7c3e52"): (0.3506, -0.024),
 ('508.0', "cf888f8318b597b573b26a108bb71f0896e998299382dfbe86493046b56a25b6"): (0.1582, 0.0347),
 ('508.0', "89dbf71048801678ca4abfbaa3ea8f7c651aae193357a3e23d68e21512cd07f5"): (0.125, 0.0),
 ('508.0', "6746cfa6bdc11f969fe18974ebc7fbde852cc2a2201537b4bea11ad3b9b01f98"): (0.125, 0.0),
 ('508.0', "1e367f240be1b68c21cfbed3dd0026a80008facf31d7ca1db684447384f06b19"): (0.125, 0.0),
 ('508.0', "23e04084df125efcc954cba154e2f4c9e06e45d9a0111c8789bc7e6e723f5782"): (0.4847, -0.007199999999999998),
 ('508.0', "0c23e3138346c4611f1b981f2aeaf658b6130461a53d13dd9a005a88578e7310"): (0.25, 0.0),
 ('508.0', "bf151de81086363eb82fce98b85d9cc4f7e550d6e4d15c6be2c04d1579df538c"): (0.25, 0.0),
 ('508.0', "10aae6592b39c03b0a584e048c3eee188998f69f1b7f28b924f3e83e74cdaa41"): (0.0625, 0.0),
 ('508.0', "d0b1a869600d21076a3fa8b5f52546e55920588ce73dfb7e56ca9dc08bdcaf3e"): (0.1875, 0.0),
 ('508.0', "0747c96a23dbfa586507337fc4ed4bdddb7b05833243622c4e6bb357b87adb0c"): (0.25, 0.0),
 ('508.0', "ae75b2880b5b5e401de30cd4b3fbbdcb8a4248f0c0018da2135cf7779a0974d5"): (0.3125, 0.0)}


@lru_cache(maxsize=32)
def glyphs(cap=V.DESIGN_CAP, style="501.3"):

    return outline_table(V, lambda ch: V.styled_layer(ch, cap, style), cap)


def layout(text, cap=V.DESIGN_CAP):


    return pen_layout(text, V, glyphs(cap))


def tracked(text, track, cap=V.DESIGN_CAP):
    """layout with `track` more after each character, spaces included."""
    placed, advance = layout(text, cap)
    letters = iter(placed)
    return ([(ch, next(letters)[1] + k * track) for k, ch in enumerate(text) if ch != " "],
            advance + (len(text) - 1) * track)


def render(text, scale=1.0, cap=V.DESIGN_CAP, style="501.3", hscale=1.0, *, track=None):
    """track=None: natural spacing, unphased. A number: tracked (see `tracked`) and set at its ROW_PHASE sub-pixel
    phase, as the art build letters every button."""


    g = glyphs(cap, style)
    placed, advance = layout(text, cap) if track is None else tracked(text, track, cap)
    natural, _ = layout(text, cap)
    key = word_key(text)
    places = ROW_PLACES.get((style, key))
    if places is not None:
        placed = [(ch, x + target * cap / V.DESIGN_CAP - nx)
                  for (ch, x), (_, nx), target in zip(placed, natural, places, strict=True)]
    s = scale
    hscale *= STYLE_WIDTH.get(style, 1.0)
    sx = s * hscale
    st = STYLES[style]
    pad = math.ceil((OUTLINE + 3 * GLOW_SIGMA) * s) + 1
    asc = max(cap - b[1] for _, b, _ in g.values())
    desc = max(b[3] - cap for _, b, _ in g.values())
    baseline = pad + math.ceil(asc * s)
    h = baseline + math.ceil(desc * s) + pad
    if placed:
        xmin = min(x + g[ch][1][0] for ch, x in placed)
        xmax = max(x + g[ch][1][2] for ch, x in placed)
        ymin = min(g[ch][1][1] for ch, _ in placed) - cap
        ymax = max(g[ch][1][3] for ch, _ in placed) - cap
    else:
        xmin = xmax = ymin = ymax = 0.0


    phase_x, phase_y = ROW_PHASE.get((style, key), (0.25, 0.035)) if track is not None else (0.0, 0.0)
    ox = pad - xmin * sx + phase_x * s
    w = math.ceil((xmax - xmin) * sx) + 2 * pad

    def draw(ctx):
        ctx.save()
        ctx.translate(ox, baseline - cap * s + phase_y * s)
        ctx.scale(hscale, 1.0)
        for ch, x in placed:
            path(ctx, g[ch][0], x * s, 0, s)
        ctx.restore()

    F = coverage(draw, w, h)
    O = coverage(draw, w, h, OUTLINE * s)
    G = np.clip(GLOW_K * blur(O, GLOW_SIGMA * s), 0, 1)
    height = (baseline - (np.arange(h) + 0.5)) / s * V.DESIGN_CAP / cap
    bands = np.array([c for _, c in FILL_BANDS] + [(255, 255, 255)], float)
    fill = bands[np.searchsorted([t for t, _ in FILL_BANDS], height, "right")][:, None, :]
    alpha = F + (1 - F) * (O + (1 - O) * G)
    near = np.clip(G / st["full"], 0, 1)[..., None]
    glow = np.array(st["far"], float) + near * (np.array(st["near"], float) - np.array(st["far"], float))
    pre = (F[..., None] * fill + ((1 - F) * O)[..., None] * np.array(NAVY, float)
           + ((1 - F) * (1 - O) * G)[..., None] * glow)
    rgb = pre / np.maximum(alpha, 1e-9)[..., None]
    rgba = np.dstack([np.clip(rgb + 0.5, 0, 255), np.clip(alpha * 255 + 0.5, 0, 255)]).astype(np.uint8)
    return Label(Image.fromarray(rgba, "RGBA"), baseline,
                 (pad, baseline + ymin * s, ox + xmax * sx, baseline + ymax * s), ox, advance * sx,
                 F.max(0).tolist())


X360_PAUSE_GRADIENT = (
    (0.5, (247, 247, 247)), (2.5, (247, 243, 247)),
    (6.5, (247, 243, 239)), (8.5, (239, 243, 239)),
    (10.5, (231, 229, 222)), (12.5, (210, 209, 202)),
    (14.5, (198, 195, 189)), (16.5, (210, 207, 198)),
    (18.5, (227, 225, 206)), (20.5, (239, 239, 218)),
    (24.5, (247, 243, 222)),
)
X360_PAUSE_NAVY = (16, 24, 74)
X360_PAUSE_HALO = (173, 180, 198)
X360_PAUSE_TRACK = -0.71
X360_PAUSE_EFFECTS = dict(outline=2.05, sigma=1.50, glow=0.86,
                          inner=0.12, inner_sigma=0.55)


def render_x360_pause(text, scale=1.0, cap=V.DESIGN_CAP, *,
                      target_width=None, hscale=1.0, track=X360_PAUSE_TRACK):


    if scale <= 0 or cap <= 0 or hscale <= 0:
        raise ValueError("Button scale, cap and horizontal scale must be positive")
    if target_width is not None and target_width <= 0:
        raise ValueError("The target button fill width must be positive")
    g = glyphs(cap, "508.0")
    placed, pen, gaps = [], 0.0, 0
    for ch in text:
        if ch == " ":
            pen += V.SPACE * cap / V.DESIGN_CAP
            gaps += 1
            continue
        if ch not in g:
            raise KeyError(f"buttonvec has no glyph for {ch!r} in {text!r}")
        ls, rs = V.BEARINGS[ch]
        x0, _, x1, _ = g[ch][1]
        placed.append((ch, pen + ls - x0 + gaps * track))
        pen += ls + x1 - x0 + rs
        gaps += 1
    if placed:
        xmin = min(x + g[ch][1][0] for ch, x in placed)
        xmax = max(x + g[ch][1][2] for ch, x in placed)
        ymin = min(g[ch][1][1] for ch, _ in placed) - cap
        ymax = max(g[ch][1][3] for ch, _ in placed) - cap
        if target_width is not None:
            hscale = target_width / (xmax - xmin)
    else:
        xmin = xmax = ymin = ymax = 0.0
    outline = X360_PAUSE_EFFECTS["outline"]
    sigma = X360_PAUSE_EFFECTS["sigma"]
    glow_strength = X360_PAUSE_EFFECTS["glow"]
    pad = math.ceil((outline + 4 * sigma) * scale) + 1
    asc = max(cap - b[1] for _, b, _ in g.values())
    desc = max(b[3] - cap for _, b, _ in g.values())
    baseline = pad + math.ceil(asc * scale)
    h = baseline + math.ceil(desc * scale) + pad
    sx = scale * hscale
    ox = pad - xmin * sx
    w = math.ceil((xmax - xmin) * sx) + 2 * pad

    def draw(ctx):
        ctx.save()
        ctx.translate(ox, baseline - cap * scale)
        ctx.scale(hscale, 1.0)
        for ch, x in placed:
            path(ctx, g[ch][0], x * scale, 0, scale)
        ctx.restore()

    F = coverage(draw, w, h)
    O = coverage(draw, w, h, outline * scale)
    G = np.clip(glow_strength * blur(O, sigma * scale), 0, 1)
    depth = ((np.arange(h) + 0.5) - (baseline - cap * scale)) / scale
    depth *= V.DESIGN_CAP / cap
    stops = [d for d, _ in X360_PAUSE_GRADIENT]
    fill = np.stack([np.interp(depth, stops, [c[k] for _, c in X360_PAUSE_GRADIENT])
                     for k in range(3)], axis=-1)[:, None, :]


    inner = (X360_PAUSE_EFFECTS["inner"]
             * blur(1.0 - F, X360_PAUSE_EFFECTS["inner_sigma"] * scale))
    fill = fill * (1.0 - inner[..., None]) + np.array(X360_PAUSE_NAVY) * inner[..., None]
    alpha = F + (1 - F) * (O + (1 - O) * G)
    pre = (F[..., None] * fill
           + ((1 - F) * O)[..., None] * np.array(X360_PAUSE_NAVY)
           + ((1 - F) * (1 - O) * G)[..., None] * np.array(X360_PAUSE_HALO))
    rgb = pre / np.maximum(alpha, 1e-9)[..., None]
    rgba = np.dstack([np.clip(rgb + 0.5, 0, 255),
                      np.clip(alpha * 255 + 0.5, 0, 255)]).astype(np.uint8)
    return Label(Image.fromarray(rgba, "RGBA"), baseline,
                 (pad, baseline + ymin * scale, pad + (xmax - xmin) * sx,
                  baseline + ymax * scale), ox,
                 (pen + max(0, gaps - 1) * track) * sx, F.max(0).tolist())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text")
    parser.add_argument("output", type=Path)
    parser.add_argument("--scale", type=float, default=1.0, help="device px per official 1x texel")
    parser.add_argument("--cap", type=float, default=V.DESIGN_CAP, help="cap height in 1x px")
    parser.add_argument("--style", choices=STYLES, default="501.3",
                        help="the WO1 sheet whose glow colour and weight to take")
    parser.add_argument("--track", type=float,
                        help="letter as the art build does: this much more after each character, at the fitted phase")
    args = parser.parse_args()
    label = render(args.text, args.scale, args.cap, args.style, track=args.track)
    label.emit(args.output)
