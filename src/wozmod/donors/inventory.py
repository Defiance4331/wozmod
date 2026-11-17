"""Pinned donor members and their extraction names."""

from __future__ import annotations

from dataclasses import dataclass


def _relative_path(value: str) -> None:
    if not value or "\\" in value or ":" in value or any(
        part in ("", ".", "..") for part in value.split("/")
    ):
        raise ValueError("Member paths must be relative slash paths")


@dataclass(frozen=True)
class DonorFile:
    """A disc file, or with an offset the size bytes of it that start there, extracted as path."""

    path: str
    source: str
    size: int
    sha256: str
    offset: int | None = None

    def __post_init__(self) -> None:
        _relative_path(self.path)
        _relative_path(self.source)
        if self.size < 0 or len(self.sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.sha256
        ) or (self.offset is not None and self.offset < 0):
            raise ValueError("Invalid member identity")


@dataclass(frozen=True)
class Donor:
    name: str
    format: str
    files: tuple[DonorFile, ...]

    def __post_init__(self) -> None:
        if self.format not in ("iso9660", "xdvdfs"):
            raise ValueError("Unknown donor disc format")
        for names in ([file.path.casefold() for file in self.files],
                      [(file.source.casefold(), file.offset) for file in self.files]):
            if len(set(names)) != len(names):
                raise ValueError("Duplicate member identity")



WO2_X360 = Donor(
    'wo2-x360', 'xdvdfs', (
        DonorFile('default.xex', 'default.xex', 5451776,
                  '5a99bd246d56e3a2362a65f1a617ebd427cc84b1c6468eb4f187496f7758f506'),
        DonorFile('link_sebank.bdx', 'data/LINK_SEBANK.BDX', 50376704,
                  'b43c5cd3e906b67fd5efd63208079e5e8c4ef08e5b9ccb59fbc3565016f40d05'),
        DonorFile('link_sebank.hdx', 'data/LINK_SEBANK.HDX', 485376,
                  '292180c951016bf06b1b0a723fd0f80a861770c5d7b8722052ec9e74fb7664f3'),
        DonorFile('link_vodat.bdx', 'data/LINK_VODAT.BDX', 441327616,
                  '2553642804be8f1beded74cd4a1da8254588c1e8cee9a1ec51cfd942f367c5c9'),
        DonorFile('linkdata_ans.idx', 'data/LINKDATA_ANS.IDX', 210088,
                  '47c0129ac53694aed303ac666efdcc40fa4f50f6410ff638dfda0c58697ea6c4'),
        DonorFile('linkdata_bns.idx', 'data/LINKDATA_BNS.IDX', 61104,
                  'a7ac557aa1e5e8c2009f613b028f8e1c14a39115751dd312e447df240e390cdd'),
        DonorFile('linkdata_bns.lnk', 'data/LINKDATA_BNS.LNK', 3198955520,
                  'ba93fb181d392bc044fadaac2c729bd95397ee40c579cd7f46a38c1015728629'),
        DonorFile('linkdata_ens.idx', 'data/LINKDATA_ENS.IDX', 4448,
                  'bf621a9d86586431fdc7730e169b6eaa350caffb3b24b1de102890a5b6af4126'),
        DonorFile('linkdata_ens.lnk', 'data/LINKDATA_ENS.LNK', 355137536,
                  '259a4a3bb3d2fcaec946c5afb2717dbeeeb14d11251fdf031d3597dcd0f073ac'),
    ),
)


WO1_X360 = Donor(
    'wo1-x360', 'xdvdfs', (
        DonorFile('default.xex', 'default.xex', 5058560,
                  '2b57b71c803487ad6f8a3a04f3c1ffefdd0b727a4c4c8498d224b0a8ad9cf9ae'),
        DonorFile('link_sebank.bdx', 'data/LINK_SEBANK.BDX', 228831232,
                  'af8561491cd096e00002701a531219e27e9e2fd8bfba73ddf4e6cf3c5899ba6f'),
        DonorFile('link_sebank.hdx', 'data/LINK_SEBANK.HDX', 1331200,
                  '7d4c238f8d37a807ac14918b630d1cf4a30bd14f63fe2da4d057f886c7a9f0d8'),
        DonorFile('link_vodat.bdx', 'data/LINK_VODAT.BDX', 407810048,
                  'ada38843540315156d8b363705dbf508ba80c7a6730b10430f86404170c2e8d2'),
        DonorFile('linkdata_ans.idx', 'data/LINKDATA_ANS.IDX', 184868,
                  '37e30fafb49b5b9964f59c721b82d36654ed65092e1c9232723a4e5abd5929c5'),
        DonorFile('linkdata_ens.idx', 'data/LINKDATA_ENS.IDX', 6048,
                  '6df832e16b6d8208b6701718767d4d6255dff9fe393fdafaf6584bf3fd449451'),
        DonorFile('linkdata_ens.lnk', 'data/LINKDATA_ENS.LNK', 484016128,
                  '1f6cc306c4912c536c1cfb3a070ec5c32dd594373a58d720e225bb4beba105bf'),
        # Entries 369 to 372 of the French text archive: the caption schedules of mev02 to mev05, timed to the dub.
        DonorFile('linkdata_fns/369.bin', 'data/LINKDATA_FNS.LNK', 600,
                  '2aae3d78e54b7de2194e21d7c5fdc3a58d3e96d155b37ba1be98c77d994394d2', 482185216),
        DonorFile('linkdata_fns/370.bin', 'data/LINKDATA_FNS.LNK', 374,
                  '79387d05f3180b5e9a8dd7813e735d0d7cd7d3817c6f189455e3e33325f1706e', 482187264),
        DonorFile('linkdata_fns/371.bin', 'data/LINKDATA_FNS.LNK', 319,
                  'bc857a92a52e63e707a81fe15ba53715bf69e3e11ec04f5d3239996beb853d7a', 482189312),
        DonorFile('linkdata_fns/372.bin', 'data/LINKDATA_FNS.LNK', 400,
                  '392212bb001853d3c430c1d4402d202ae2d0061f4bf4aea1f059770d0af3b805', 482191360),
        DonorFile('movie/mev01.wmv', 'data/movie/mev01.wmv', 72429463,
                  'b9605b0bcb544018e9c28715c29ed2ee170d1efcf963832e632b6c8cdfe76671'),
        DonorFile('movie/mev02.wmv', 'data/movie/mev02.wmv', 44195468,
                  '687bbd649211d369f7bd2c89bc9b830dfa4ff7ecbdbe877c32381b54009beede'),
        DonorFile('movie/mev03.wmv', 'data/movie/mev03.wmv', 59248131,
                  'a9b0ac2bbd4be53dc7cb9fbaad162a6f475a1959010f68d1295653df08dcff4a'),
        DonorFile('movie/mev04.wmv', 'data/movie/mev04.wmv', 40616968,
                  '4e4c11a38e01c1fc319490e287e2b74fc187d3430f4efa51596ecd80eb2c89c5'),
        DonorFile('movie/mev05.wmv', 'data/movie/mev05.wmv', 51434531,
                  '5047170e76c84a6fd8011ebfe79d036c71336aa79c873b252908103e9accdee7'),
        DonorFile('movie/mev06.wmv', 'data/movie/mev06.wmv', 73841093,
                  '32f474299bc4dad28b921ec3c69796be7a7813a7f94ae1551a3169fa82c5e716'),
        DonorFile('movie/mev11.wmv', 'data/movie/mev11.wmv', 2090660,
                  'de4563512027e03ba8307d8a2fafff51f2e3cd5dedc0ee266084a9ffaa6e8955'),
        DonorFile('movie/mev12.wmv', 'data/movie/mev12.wmv', 1942925,
                  'b8ac6e2bae7ea7a435a83800499f580c7d3cf7393ff97c77bc983552314e054d'),
        DonorFile('movie/mev13.wmv', 'data/movie/mev13.wmv', 1910095,
                  '7505b0caf31b3e84e320a6083df62831758440e03bebd441d0ee95ca8d9b0696'),
        DonorFile('movie/mev14.wmv', 'data/movie/mev14.wmv', 1959340,
                  '37fe6745b46e198f3405585c6ee32df9bc26080039f3d59816470b0041e00848'),
    ),
)


WO2_PSP = Donor(
    'wo2-psp', 'iso9660', (
        DonorFile('PSP_GAME/PARAM.SFO', 'PSP_GAME/PARAM.SFO', 472,
                  '279068f106f67d9838c75b9f33b6708dc9b4ed3bd7f95a5c92a707d94d9fbad6'),
        DonorFile('PSP_GAME/SYSDIR/BOOT.BIN', 'PSP_GAME/SYSDIR/BOOT.BIN', 3197900,
                  '22c2e3e9b6cd654b7db2f1e5df74460c6275f323717a7ee2688aa2b3d32be67a'),
        DonorFile('PSP_GAME/USRDIR/BOOT.BIN', 'PSP_GAME/USRDIR/BOOT.BIN', 3210916,
                  '07671edbcdc67a3c5c5eb28ef9dda2197a3e4613418fa56a906cd20341a647e4'),
        DonorFile('PSP_GAME/USRDIR/LINKDATA.BNS', 'PSP_GAME/USRDIR/LINKDATA.BNS', 224425984,
                  '16073121459020245b4cad88b146709aa2455e5fcc87824d3eb7fcc91e26686e'),
        DonorFile('PSP_GAME/USRDIR/LINKDATA.CNS', 'PSP_GAME/USRDIR/LINKDATA.CNS', 60180480,
                  '110b190911b006289629c45012378261f146a114d9263f73dc22fd2d7914c227'),
        DonorFile('PSP_GAME/USRDIR/LINKDATA.DNS', 'PSP_GAME/USRDIR/LINKDATA.DNS', 540672,
                  '1ef813b10d0cf32d2f7de60beefa3b9a8019d1c174ee15043db20f0bdc71d867'),
        DonorFile('PSP_GAME/USRDIR/LINKDATA.ENS', 'PSP_GAME/USRDIR/LINKDATA.ENS', 192739328,
                  '4aa460ac96033416ea7d4a7b205655ef48855dd6ae51e050bdcbdade0574c755'),
        DonorFile('PSP_GAME/USRDIR/LINKDATA_FRA.FNS', 'PSP_GAME/USRDIR/LINKDATA_FRA.FNS', 34621440,
                  '696a08d42e4f9934d51b6cc4821624230c081dbb474203b9c29144eaf861f84f'),
        DonorFile('PSP_GAME/USRDIR/LINKDATA_NA.FNS', 'PSP_GAME/USRDIR/LINKDATA_NA.FNS', 34457600,
                  '6d7213772b30cfdbf9e4ad3c4d49b52d6acf78696b0f2259d6c421356c79a40b'),
        DonorFile('PSP_GAME/USRDIR/movie/MEV_201.pmf', 'PSP_GAME/USRDIR/movie/MEV_201.pmf', 14827520,
                  '43d94289b97a862148bf12a34783b8d6f949c7a80aba11eceed7c1428c599861'),
        DonorFile('PSP_GAME/USRDIR/movie/MEV_202.pmf', 'PSP_GAME/USRDIR/movie/MEV_202.pmf', 24090624,
                  'd4a4845888ce723761525aafbc5434b997db1246803cf4ca98af23b66276b9d9'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_201.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_201.pmf', 5949440,
                  '73e2e3d54d90fb22ef02d1f3b12c2f9d89ef1b2fb230deeb66ee141346608a9a'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_202.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_202.pmf', 6715392,
                  '527a7cea90d7b9793f6ddbf551d8a4f643419da7c4c2bdbd7718d9eb3bbbe832'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_203.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_203.pmf', 13084672,
                  'b1347a0455d3b277d89b441c3c2c4817fe7c047e22c2765d338d1fbfcb6cea0f'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_204.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_204.pmf', 15663104,
                  'cca18a97e908116ab21671c7a1ef71b5c3092195794b727adb85fe912fca4774'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_205.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_205.pmf', 8030208,
                  'da8531d4f519c5388e8d2908d863476ce19d4664b5b217291101994a94a31ba5'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_206.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_206.pmf', 9037824,
                  '9a3939d7a5b9ee2f4802684cf4d2a85ae60e3f5ba03fb23eb96fa404639ad77a'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_207.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_207.pmf', 9893888,
                  '26afc82cc61a9be768841fe74d6fae5680f9643ed3c2ce868b16cb8beeb92fe2'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_208.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_208.pmf', 12984320,
                  '433131b83cac0c632edcc429f78a3e34c6f9b5d68362c15c2f79c18ba6057d68'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_209.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_209.pmf', 4456448,
                  '4c73b233f2cc0f1a54be26293fbf203fa45e1c6cc572186821edd4b833bca356'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_210.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_210.pmf', 5738496,
                  'e45a7ef93b5362c38631b39b7a265e03eee1106aff5603115c5d6307ee2c673e'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_211.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_211.pmf', 8273920,
                  'fd535d2d6d99e503b1dde27c78d6e93ca82430111dd216d15c3a7b64ac404cdf'),
        DonorFile('PSP_GAME/USRDIR/movie/event/GEV_212.pmf', 'PSP_GAME/USRDIR/movie/event/GEV_212.pmf', 7036928,
                  '2bd144299298a1747174161a8fe66c238b55e248dec12675bc1402d0db4194cd'),
        DonorFile('PSP_GAME/USRDIR/sound/voice_na.bns', 'PSP_GAME/USRDIR/sound/voice_na.bns', 100902912,
                  'c0303321783ff239d7c9617f63bfe16411b22f82263f78cf183c258da7b5cf3d'),
    ),
)


DONORS = {donor.name: donor for donor in (WO1_X360, WO2_X360, WO2_PSP)}
