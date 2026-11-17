//! Builds PocketSphinx (CMU, BSD licence) from its released source into the module, in fixed point.
//!
//! The source is not kept in the repository: the build fetches the PyPI source release named below, checks it against
//! its SHA-256 and keeps the download under target/downloads (or WOZMOD_POCKETSPHINX_TARBALL names a copy already
//! on disk, checked the same way), then applies our patches in patches/, each pinned by its SHA-256. Every call it
//! makes to the C maths library (log, exp, cos, ...) goes to the libm crate instead (csrc/ps_math.h, speech.rs): the
//! platform's own functions differ in their last bits between Linux and Windows, and the mouths built from the phones
//! must come out byte for byte the same on every machine.

use sha2::{Digest, Sha256};
use std::env;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Command;

const VERSION: &str = "5.1.1";
const URL: &str = "https://files.pythonhosted.org/packages/61/e7/13e0e787ff467218de880310d79cba14424f13b87171ac44af0bde1e428c/pocketsphinx-5.1.1.tar.gz";
const SHA256: &str = "675778b309a22dfc9b7d37f7621976bba491d2a5f8c59696bd77fd6d07271355";
/// Our patches to that source (patches/, unified diffs against its top folder), each with its SHA-256, applied in order.
const PATCHES: &[(&str, &str)] =
    &[("ptm_mgau-reset-topn.patch", "f4a1a9bace19675aeaff082be3cc739a3be3eb7da7a9c1411bb5fa59d83fdc47")];

/// The library's C files, as its src/CMakeLists.txt lists them (ps_init links in every search and reader).
const SOURCES: &[&str] = &[
    "acmod.c", "allphone_search.c", "bin_mdef.c", "dict2pid.c", "dict.c",
    "fe/fe_sigproc.c", "fe/fixlog.c", "fe/fe_warp_inverse_linear.c", "fe/fe_noise.c", "fe/fe_warp.c",
    "fe/fe_interface.c", "fe/fe_warp_affine.c", "fe/yin.c", "fe/fe_warp_piecewise_linear.c",
    "feat/cmn.c", "feat/agc.c", "feat/cmn_live.c", "feat/feat.c", "feat/lda.c",
    "fsg_history.c", "fsg_lextree.c", "fsg_search.c", "hmm.c", "kws_detections.c", "kws_search.c",
    "lm/lm_trie_quant.c", "lm/ngram_model_trie.c", "lm/fsg_model.c", "lm/jsgf.c", "lm/ngram_model_set.c",
    "lm/ngrams_raw.c", "lm/jsgf_scanner.c", "lm/bitarr.c", "lm/ngram_model.c", "lm/lm_trie.c", "lm/jsgf_parser.c",
    "mdef.c", "ms_gauden.c", "ms_mgau.c", "ms_senone.c", "ngram_search.c", "ngram_search_fwdflat.c",
    "ngram_search_fwdtree.c", "phone_loop_search.c", "pocketsphinx.c", "ps_alignment.c", "ps_config.c",
    "ps_endpointer.c", "ps_lattice.c", "ps_mllr.c", "ps_vad.c", "ptm_mgau.c", "s2_semi_mgau.c",
    "state_align_search.c", "tmat.c",
    "common_audio/vad/vad_gmm.c", "common_audio/vad/webrtc_vad.c", "common_audio/vad/vad_filterbank.c",
    "common_audio/vad/vad_core.c", "common_audio/vad/vad_sp.c",
    "common_audio/signal_processing/division_operations.c", "common_audio/signal_processing/resample_48khz.c",
    "common_audio/signal_processing/resample.c", "common_audio/signal_processing/resample_fractional.c",
    "common_audio/signal_processing/downsample_fast.c", "common_audio/signal_processing/min_max_operations.c",
    "common_audio/signal_processing/cross_correlation.c",
    "common_audio/signal_processing/vector_scaling_operations.c",
    "common_audio/signal_processing/resample_by_2_internal.c", "common_audio/signal_processing/energy.c",
    "common_audio/signal_processing/spl_inl.c", "common_audio/signal_processing/get_scaling_square.c",
    "util/strfuncs.c", "util/dtoa.c", "util/case.c", "util/filename.c", "util/slamch.c", "util/cmd_ln.c",
    "util/blas_lite.c", "util/blkarray_list.c", "util/vector.c", "util/mmio.c", "util/hash_table.c", "util/err.c",
    "util/ckd_alloc.c", "util/slapack_lite.c", "util/matrix.c", "util/bio.c", "util/heap.c",
    "util/priority_queue.c", "util/bitvec.c", "util/profile.c", "util/errno.c", "util/logmath.c", "util/glist.c",
    "util/f2c_lite.c", "util/listelem_alloc.c", "util/pio.c", "util/genrand.c", "util/soundfiles.c",
];

fn sha256(data: &[u8]) -> String {
    Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}

/// The source release's bytes, checked against SHA256: the named copy, else the cached download, else a new one.
fn tarball(manifest: &Path) -> Vec<u8> {
    println!("cargo:rerun-if-env-changed=WOZMOD_POCKETSPHINX_TARBALL");
    let given = env::var_os("WOZMOD_POCKETSPHINX_TARBALL").map(PathBuf::from);
    let cached = manifest.join("target/downloads").join(format!("pocketsphinx-{VERSION}-{SHA256}.tar.gz"));
    let path = given.clone().unwrap_or_else(|| cached.clone());
    if let Ok(data) = fs::read(&path) {
        let found = sha256(&data);
        if found == SHA256 {
            return data;
        }
        if given.is_some() {
            panic!("{} is not the PocketSphinx {VERSION} source release (SHA-256 {found})", path.display());
        }
    }
    fs::create_dir_all(cached.parent().unwrap()).expect("cannot make target/downloads");
    let partial = cached.with_extension("part");
    let status = Command::new("curl").args(["-fsSL", "--retry", "3", "-o"]).arg(&partial).arg(URL).status();
    if !matches!(status, Ok(s) if s.success()) {
        panic!("could not download {URL} with curl; set WOZMOD_POCKETSPHINX_TARBALL to a copy of it");
    }
    let data = fs::read(&partial).expect("the download vanished");
    let found = sha256(&data);
    if found != SHA256 {
        let _ = fs::remove_file(&partial);
        panic!("{URL} has SHA-256 {found}, not the pinned {SHA256}");
    }
    fs::rename(&partial, &cached).expect("cannot keep the download");
    data
}

/// Unpacks the source tree's src/ and include/ and its licence into out, replacing what an earlier build left.
fn unpack(data: &[u8], out: &Path) -> PathBuf {
    let root = out.join(format!("pocketsphinx-{VERSION}"));
    let _ = fs::remove_dir_all(&root);
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(data));
    for entry in archive.entries().expect("not a tar.gz") {
        let mut entry = entry.expect("a damaged tar entry");
        let name = entry.path().expect("a tar entry with no path").into_owned();
        let Ok(inner) = name.strip_prefix(format!("pocketsphinx-{VERSION}")) else { continue };
        let wanted = inner.starts_with("src") || inner.starts_with("include") || inner == Path::new("LICENSE");
        if !wanted || !entry.header().entry_type().is_file() {
            continue;
        }
        let target = root.join(inner);
        fs::create_dir_all(target.parent().unwrap()).unwrap();
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes).unwrap();
        fs::write(target, bytes).unwrap();
    }
    root
}

/// Applies a unified diff to the tree at root. Every hunk's old lines must stand exactly where it says, as they do in
/// the pinned source; anything else stops the build rather than patch the wrong lines.
fn apply(root: &Path, name: &str, diff: &str) {
    let fail = |why: String| -> ! { panic!("patches/{name}: {why}") };
    let mut lines = diff.lines().skip_while(|l| !l.starts_with("--- ")).peekable();
    while let Some(old) = lines.next() {
        let Some(new) = lines.next().and_then(|l| l.strip_prefix("+++ b/")) else { fail(format!("no +++ after {old}")) };
        let path = root.join(new);
        let text = fs::read_to_string(&path).unwrap_or_else(|e| fail(format!("{new}: {e}")));
        let mut file: Vec<String> = text.lines().map(str::to_owned).collect();
        let mut shift = 0isize;
        while let Some(head) = lines.next_if(|l| l.starts_with("@@ ")) {
            let start: usize = head
                .trim_start_matches("@@ -")
                .split([',', ' '])
                .next()
                .and_then(|n| n.parse().ok())
                .unwrap_or_else(|| fail(format!("bad hunk header {head}")));
            let (mut was, mut now) = (Vec::new(), Vec::new());
            while let Some(line) = lines.next_if(|l| !l.starts_with("@@ ") && !l.starts_with("--- ")) {
                match line.split_at(line.len().min(1)) {
                    (" ", rest) => { was.push(rest.to_owned()); now.push(rest.to_owned()); }
                    ("-", rest) => was.push(rest.to_owned()),
                    ("+", rest) => now.push(rest.to_owned()),
                    _ => fail(format!("bad line in a hunk: {line:?}")),
                }
            }
            let at = (start as isize - 1 + shift) as usize;
            if file.get(at..at + was.len()) != Some(&was[..]) {
                fail(format!("{new} does not hold this hunk's lines at line {start}"));
            }
            shift += now.len() as isize - was.len() as isize;
            file.splice(at..at + was.len(), now);
        }
        fs::write(&path, file.join("\n") + "\n").unwrap();
    }
}

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=csrc");
    println!("cargo:rerun-if-changed=patches");
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let root = unpack(&tarball(&manifest), &out);
    for (name, pin) in PATCHES {
        let diff = fs::read(manifest.join("patches").join(name)).unwrap_or_else(|e| panic!("patches/{name}: {e}"));
        if sha256(&diff) != *pin {
            panic!("patches/{name} has SHA-256 {}, not the pinned {pin}", sha256(&diff));
        }
        apply(&root, name, std::str::from_utf8(&diff).expect("a patch is UTF-8"));
    }

    // config.h and sphinx_config.h, as the library's CMake would write them for this target.
    let windows = env::var("CARGO_CFG_TARGET_OS").unwrap() == "windows";
    let msvc = env::var("CARGO_CFG_TARGET_ENV").unwrap() == "msvc";
    let long = if windows || env::var("CARGO_CFG_TARGET_POINTER_WIDTH").unwrap() == "32" { 4 } else { 8 };
    let mut common = format!(
        "#define FIXED_POINT\n#define DEFAULT_RADIX 12\n#define HAVE_STDINT_H\n#define HAVE_LONG_LONG\n\
         #define SIZEOF_LONG {long}\n#define SIZEOF_LONG_LONG 8\n"
    );
    if !msvc {
        common += "#define HAVE_UNISTD_H\n";
    }
    let generated = out.join("generated");
    fs::create_dir_all(generated.join("pocketsphinx")).unwrap();
    fs::write(generated.join("pocketsphinx/sphinx_config.h"), &common).unwrap();
    fs::write(
        generated.join("config.h"),
        format!("{common}#define HAVE_SYS_TYPES_H\n#define HAVE_SYS_STAT_H\n#define HAVE_SNPRINTF\n#define PACKAGE_NAME \"PocketSphinx\"\n#define PACKAGE_VERSION \"{VERSION}\"\n"),
    )
    .unwrap();

    let mut build = cc::Build::new();
    build
        .include(&generated)
        .include(root.join("include"))
        .include(root.join("src"))
        .define("HAVE_CONFIG_H", None)
        .define("POCKETSPHINX_EXPORTS", None)
        .define("SPHINXBASE_EXPORTS", None)
        .warnings(false)
        .opt_level(2);
    let shim = manifest.join("csrc/ps_math.h");
    if msvc {
        build.flag("/FI").flag(shim.to_str().unwrap()).flag("/fp:precise");
    } else {
        build.std("gnu17").flag("-include").flag(shim.to_str().unwrap()).flag("-ffp-contract=off").flag("-fno-fast-math");
    }
    for file in SOURCES {
        build.file(root.join("src").join(file));
    }
    build.compile("pocketsphinx");
    if !windows {
        println!("cargo:rustc-link-lib=m");
    }
}
