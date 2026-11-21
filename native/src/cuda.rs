//! The Dream voice separator's network on an NVIDIA GPU, with no layer between it and the driver: the driver's own
//! library (libcuda.so.1, nvcuda.dll, which every NVIDIA driver installs) is loaded at run time, compiles our kernels
//! from PTX (packaging/separator/kernels.cu, built by packaging/separator_kernels.py) and runs them. Nothing of the
//! CUDA toolkit is used or shipped.
//!
//! The network it runs is network.rs's, one launch a step: bandsplit, gemm, rmsnorm, gates, flash and glu.
//!
//! This engine is out of every build (the Cargo feature `cuda`, off): Vulkan's shaders run a chunk on the card it
//! was measured on in the same time (151 against 153 ms, ranges overlapping; owner's decision, 2026-11-21), and
//! they run on every maker's cards. To turn it back on:
//!
//! 1. Build the native module with the feature: `MATURIN_PEP517_ARGS="--features cuda" uv sync --extra native
//!    --reinstall-package wozmod-native` for a checkout; for a release, add `features = ["cuda"]` under
//!    `[tool.maturin]` in native/pyproject.toml.
//! 2. Build the kernels: `uv run python packaging/separator_kernels.py --cuda` writes the two PTX files into the
//!    separator folder. They must match `KERNELS` in src/wozmod/voices/separation.py; a change of kernels.cu
//!    changes them, and the script prints the new pins.
//! 3. Ship them: add `KERNELS` to the files packaging/build.py's add_separator copies and checks, and say so in
//!    packaging/SEPARATOR-NOTICE.txt.
//! 4. Choose when it runs: `WOZMOD_DREAM_ENGINE=cuda` tries it alone, in any build that has it. To make it the
//!    first choice on NVIDIA cards, put `nvidia_engine(...)` before the Vulkan engines in separation.gpu_engines().
//! 5. Settle the licence question first: kernels.cu includes NVIDIA's cuda_fp16.h, so the PTX holds what that
//!    header's inline functions compile to. While nothing of it ships the question does not arise.
//!
//! `cargo check --features cuda` (tests/test_native_features.py) keeps it compiling against network.rs meanwhile,
//! and tests/test_separation_engines.py runs it beside the Vulkan rows in a build that has it.
//!
//! Every copy between the host and the device goes on the stream the kernels run on. A plain copy runs on the
//! driver's default stream, which the kernels' stream does not wait for, and can return before its data has landed:
//! a kernel then read half-written input, and one chunk in 30 came out 29 dB different between two runs.

use libloading::Library;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::ffi::{c_char, c_int, c_uint, c_void, CStr};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use crate::network::{At, Network, Step, BANDS, EPSILON, HEADS};
use crate::separator::{Stored, ROWS};

type Status = c_int;
type Handle = *mut c_void;
type Address = u64; // a place in the device's memory

const UNSUPPORTED_PTX_VERSION: Status = 222;

macro_rules! driver {
    ($($name:ident: fn($($argument:ty),*)),* $(,)?) => {
        /// The driver functions the separator calls, by their names in the library.
        #[allow(non_snake_case)]
        struct Driver {
            $($name: unsafe extern "system" fn($($argument),*) -> Status,)*
            _library: Library,
        }

        impl Driver {
            fn load() -> Result<Driver, String> {
                let name = if cfg!(windows) { "nvcuda.dll" } else { "libcuda.so.1" };
                unsafe {
                    let library = Library::new(name).map_err(|e| format!("no NVIDIA driver library ({name}): {e}"))?;
                    Ok(Driver {
                        $($name: *library
                            .get(concat!(stringify!($name), "\0").as_bytes())
                            .map_err(|e| format!("{name} lacks {}: {e}", stringify!($name)))?,)*
                        _library: library,
                    })
                }
            }
        }
    };
}

driver! {
    cuInit: fn(c_uint),
    cuDriverGetVersion: fn(*mut c_int),
    cuDeviceGet: fn(*mut c_int, c_int),
    cuDeviceGetName: fn(*mut c_char, c_int, c_int),
    cuDeviceGetAttribute: fn(*mut c_int, c_int, c_int),
    cuDevicePrimaryCtxRetain: fn(*mut Handle, c_int),
    cuDevicePrimaryCtxRelease_v2: fn(c_int),
    cuCtxSetCurrent: fn(Handle),
    cuMemGetInfo_v2: fn(*mut usize, *mut usize),
    cuStreamCreate: fn(*mut Handle, c_uint),
    cuStreamDestroy_v2: fn(Handle),
    cuStreamSynchronize: fn(Handle),
    cuModuleLoadData: fn(*mut Handle, *const c_void),
    cuModuleUnload: fn(Handle),
    cuModuleGetFunction: fn(*mut Handle, Handle, *const c_char),
    cuFuncSetAttribute: fn(Handle, c_int, c_int),
    cuMemAlloc_v2: fn(*mut Address, usize),
    cuMemFree_v2: fn(Address),
    cuMemAllocHost_v2: fn(*mut *mut c_void, usize),
    cuMemFreeHost: fn(*mut c_void),
    cuMemcpyHtoDAsync_v2: fn(Address, *const c_void, usize, Handle),
    cuMemcpyDtoHAsync_v2: fn(*mut c_void, Address, usize, Handle),
    cuLaunchKernel: fn(Handle, c_uint, c_uint, c_uint, c_uint, c_uint, c_uint, c_uint, Handle, *mut *mut c_void, *mut *mut c_void),
    cuGetErrorString: fn(Status, *mut *const c_char),
}

impl Driver {
    fn check(&self, status: Status, what: &str) -> Result<(), String> {
        if status == 0 {
            return Ok(());
        }
        let mut text: *const c_char = std::ptr::null();
        let said = unsafe {
            if (self.cuGetErrorString)(status, &mut text) == 0 && !text.is_null() {
                CStr::from_ptr(text).to_string_lossy().into_owned()
            } else {
                "an error the driver does not name".to_string()
            }
        };
        Err(format!("{what}: {said} (CUDA error {status})"))
    }
}

/// The NVIDIA driver's version ("620.70"), from its management library; None where that does not load or say.
fn driver_version() -> Option<String> {
    let name = if cfg!(windows) { "nvml.dll" } else { "libnvidia-ml.so.1" };
    unsafe {
        let library = Library::new(name).ok()?;
        let start: libloading::Symbol<unsafe extern "C" fn() -> c_int> = library.get(b"nvmlInit_v2\0").ok()?;
        let version: libloading::Symbol<unsafe extern "C" fn(*mut c_char, c_uint) -> c_int> = library.get(b"nvmlSystemGetDriverVersion\0").ok()?;
        let stop: libloading::Symbol<unsafe extern "C" fn() -> c_int> = library.get(b"nvmlShutdown\0").ok()?;
        if start() != 0 {
            return None;
        }
        let mut text = [0 as c_char; 96];
        let found = version(text.as_mut_ptr(), text.len() as c_uint) == 0;
        stop();
        found.then(|| CStr::from_ptr(text.as_ptr()).to_string_lossy().into_owned())
    }
}

/// What the driver says of the first NVIDIA GPU.
struct Card {
    interface: i32, // the CUDA driver interface's version: 13000 is 13.0
    name: String,
    capability: (i32, i32), // compute capability: (7, 5) is the first with the kernels' tensor-core instructions
    free: usize, // bytes of video memory
    total: usize,
}

/// A device, a context current on the calling thread and a stream on it; what it allocated is freed with it.
struct Gpu {
    driver: Driver,
    device: c_int,
    context: Handle,
    stream: Handle,
    module: Handle,
    allocated: Vec<Address>,
    pinned: Vec<*mut c_void>, // page-locked host memory: a copy to or from it on the stream does not wait for the host
}

unsafe impl Send for Gpu {} // its handles are the driver's, which any thread may use once the context is current there
unsafe impl Sync for Gpu {} // reached only through its Mutex, whose holder may lend it to the thread that runs a chunk

impl Gpu {
    fn open() -> Result<(Gpu, Card), String> {
        let driver = Driver::load()?;
        let (mut interface, mut device, mut major, mut minor) = (0, 0, 0, 0);
        let mut name = [0 as c_char; 128];
        let mut context: Handle = std::ptr::null_mut();
        unsafe {
            driver.check((driver.cuInit)(0), "starting the NVIDIA driver")?;
            driver.check((driver.cuDriverGetVersion)(&mut interface), "its version")?;
            driver.check((driver.cuDeviceGet)(&mut device, 0), "its first GPU")?;
            driver.check((driver.cuDeviceGetName)(name.as_mut_ptr(), name.len() as c_int, device), "the GPU's name")?;
            driver.check((driver.cuDeviceGetAttribute)(&mut major, 75, device), "the GPU's compute capability")?;
            driver.check((driver.cuDeviceGetAttribute)(&mut minor, 76, device), "the GPU's compute capability")?;
            driver.check((driver.cuDevicePrimaryCtxRetain)(&mut context, device), "a context on the GPU")?;
        }
        let mut gpu = Gpu { driver, device, context, stream: std::ptr::null_mut(), module: std::ptr::null_mut(), allocated: Vec::new(), pinned: Vec::new() };
        let (mut free, mut total) = (0usize, 0usize);
        unsafe {
            gpu.current()?;
            gpu.driver.check((gpu.driver.cuMemGetInfo_v2)(&mut free, &mut total), "the GPU's free memory")?;
            gpu.driver.check((gpu.driver.cuStreamCreate)(&mut gpu.stream, 1), "a stream on the GPU")?; // 1: not blocked by the default stream
        }
        let name = unsafe { CStr::from_ptr(name.as_ptr()) }.to_string_lossy().into_owned();
        Ok((gpu, Card { interface, name, capability: (major, minor), free, total }))
    }

    /// Make the context current on this thread: a context is current per thread, and the caller may be any.
    fn current(&self) -> Result<(), String> {
        self.driver.check(unsafe { (self.driver.cuCtxSetCurrent)(self.context) }, "the GPU's context")
    }

    fn synchronize(&self) -> Result<(), String> {
        self.driver.check(unsafe { (self.driver.cuStreamSynchronize)(self.stream) }, "the GPU run")
    }

    fn allocate(&mut self, bytes: usize) -> Result<Address, String> {
        let mut address = 0;
        self.driver.check(unsafe { (self.driver.cuMemAlloc_v2)(&mut address, bytes.max(1)) }, &format!("{bytes} bytes of video memory"))?;
        self.allocated.push(address);
        Ok(address)
    }

    /// Page-locked host memory, which the driver copies to and from without a staging copy of its own.
    fn page_locked(&mut self, bytes: usize) -> Result<*mut u8, String> {
        let mut address = std::ptr::null_mut();
        self.driver.check(unsafe { (self.driver.cuMemAllocHost_v2)(&mut address, bytes.max(1)) }, &format!("{bytes} bytes of page-locked memory"))?;
        self.pinned.push(address);
        Ok(address.cast())
    }

    /// Copy host values to the device, on the kernels' stream, and wait: the values may go once this returns.
    fn write<T: Copy>(&self, to: Address, values: &[T]) -> Result<(), String> {
        let size = std::mem::size_of_val(values);
        self.driver.check(unsafe { (self.driver.cuMemcpyHtoDAsync_v2)(to, values.as_ptr().cast(), size, self.stream) }, "a copy to the GPU")?;
        self.synchronize()
    }

    fn uploaded<T: Copy>(&mut self, values: &[T]) -> Result<Address, String> {
        let address = self.allocate(std::mem::size_of_val(values))?;
        self.write(address, values)?;
        Ok(address)
    }

    /// Load the first of the PTX files the driver takes: a driver older than a file's PTX version refuses it, and
    /// the next (an older version) is tried. Returns the file's name.
    fn load(&mut self, files: &[PathBuf]) -> Result<String, String> {
        let mut refused = String::from("no PTX file was given");
        for file in files {
            let mut text = std::fs::read(file).map_err(|e| format!("{}: {e}", file.display()))?;
            text.push(0); // the driver reads a string
            let status = unsafe { (self.driver.cuModuleLoadData)(&mut self.module, text.as_ptr().cast()) };
            let name = file.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            match self.driver.check(status, &format!("loading {name}")) {
                Ok(()) => return Ok(name),
                Err(said) if status == UNSUPPORTED_PTX_VERSION => refused = said,
                Err(said) => return Err(said),
            }
        }
        Err(refused)
    }

    fn function(&self, name: &str) -> Result<Handle, String> {
        let mut function: Handle = std::ptr::null_mut();
        let named = format!("{name}\0");
        self.driver.check(unsafe { (self.driver.cuModuleGetFunction)(&mut function, self.module, named.as_ptr().cast()) }, &format!("the kernel {name}"))?;
        Ok(function)
    }

    fn launch(&self, launch: &Launch) -> Result<(), String> {
        let mut values = launch.arguments.clone();
        let mut pointers: Vec<*mut c_void> = values.iter_mut().map(|value| value.pointer()).collect();
        let [x, y, z] = launch.grid;
        self.driver.check(
            unsafe { (self.driver.cuLaunchKernel)(launch.function, x, y, z, launch.threads, 1, 1, 0, self.stream, pointers.as_mut_ptr(), std::ptr::null_mut()) },
            "starting a kernel",
        )
    }
}

impl Drop for Gpu {
    fn drop(&mut self) {
        unsafe {
            if (self.driver.cuCtxSetCurrent)(self.context) == 0 {
                if !self.stream.is_null() {
                    (self.driver.cuStreamSynchronize)(self.stream);
                }
                for address in self.allocated.drain(..) {
                    (self.driver.cuMemFree_v2)(address);
                }
                for address in self.pinned.drain(..) {
                    (self.driver.cuMemFreeHost)(address);
                }
                if !self.module.is_null() {
                    (self.driver.cuModuleUnload)(self.module);
                }
                if !self.stream.is_null() {
                    (self.driver.cuStreamDestroy_v2)(self.stream);
                }
            }
            (self.driver.cuDevicePrimaryCtxRelease_v2)(self.device);
        }
    }
}

/// A kernel's argument, as the kernel declares it.
#[derive(Clone, Copy)]
enum Argument {
    At(Address),
    Int(i32),
    Float(f32),
}

impl Argument {
    fn pointer(&mut self) -> *mut c_void {
        match self {
            Argument::At(value) => (value as *mut Address).cast(),
            Argument::Int(value) => (value as *mut i32).cast(),
            Argument::Float(value) => (value as *mut f32).cast(),
        }
    }
}

struct Launch {
    function: Handle,
    grid: [c_uint; 3],
    threads: c_uint,
    arguments: Vec<Argument>,
}

/// One matrix product of the gemm kernel (kernels.cu `Gemm`, field for field): c[m][n] = act(sum over k of
/// a[m][k] * b[n][k] + bias[n]) + r[m][n], with the rotary turn of columns under rope_n where rc is set.
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Product {
    a: Address,
    b: Address,
    bias: Address,
    r: Address,
    c: Address,
    m: i32,
    n: i32,
    k: i32,
    lda: i32,
    ldb: i32,
    ldc: i32,
    ldr: i32,
    act: i32, // 0 none, 1 GELU, 2 tanh
    rc: Address,
    rs: Address,
    pdiv: i32, // row m is at position (m / pdiv) % pmod of its sequence
    pmod: i32,
    rope_n: i32,
    pad: i32,
}

/// The network on the first NVIDIA GPU: spectra in, masks out.
#[pyclass]
pub struct SeparatorCuda {
    gpu: Mutex<Gpu>,
    launches: Vec<Launch>,
    spec: Address,
    masks: Address,
    frames: usize,
    width: usize,
    /// The PTX file the driver took.
    #[pyo3(get)]
    kernels: String,
    device_nanos: AtomicU64, // how long the GPU had the last chunk: from its spectrum's copy in to its masks' copy out
    host_spec: *mut u8,      // page-locked: a chunk's spectrum on its way in
    host_masks: *mut u8,     // and its masks on their way out
}

unsafe impl Send for SeparatorCuda {} // a Launch's function handle is the driver's; the Mutex orders the runs
unsafe impl Sync for SeparatorCuda {}

impl SeparatorCuda {
    fn build(weights: &[u8], tensors: &[Stored], bands: &[(usize, usize)], ptx: &[PathBuf], frames: usize) -> Result<SeparatorCuda, String> {
        let (mut gpu, _) = Gpu::open()?;
        let kernels = gpu.load(ptx)?;
        let [gemm, flash, rmsnorm, gates, bandsplit, glu] = ["gemm", "flash", "rmsnorm", "gates", "bandsplit", "glu"].map(|name| gpu.function(name));
        let (gemm, flash, rmsnorm, gates, bandsplit, glu) = (gemm?, flash?, rmsnorm?, gates?, bandsplit?, glu?);
        // 9: the share of a multiprocessor's cache given to shared memory; all of it, so two gemm blocks fit
        gpu.driver.check(unsafe { (gpu.driver.cuFuncSetAttribute)(gemm, 9, 100) }, "the gemm kernel's shared memory")?;
        let network = Network::new(weights, tensors, bands, frames, usize::MAX)?; // a driver allocation has no limit to fit
        let mut places = Vec::new();
        for tensor in &network.tensors {
            let place = gpu.allocate(tensor.bytes + tensor.slack)?;
            if let Some(data) = &tensor.data {
                gpu.write(place, data)?;
            }
            places.push(place);
        }
        let halves = |at: At| Argument::At(places[at.tensor] + 2 * at.offset as u64); // a place counted in 16-bit floats
        let words = |at: At| Argument::At(places[at.tensor] + 4 * at.offset as u64); // in 32-bit floats or integers
        let address = |argument: Argument| if let Argument::At(address) = argument { address } else { 0 };
        let (rows, t) = (network.rows as i32, network.frames as i32);
        let mut launches = Vec::new();
        for step in &network.steps {
            launches.push(match step {
                Step::BandSplit { spec, picked, edges, gammas, features, padded } => Launch {
                    function: bandsplit,
                    grid: [t as u32, BANDS as u32, 1],
                    threads: 32,
                    // a band's gammas start where its features do: the edges serve as both tables
                    arguments: vec![words(*spec), words(*picked), words(*edges), words(*gammas), words(*edges), halves(*features),
                                    Argument::Int(t), Argument::Int(*padded as i32), Argument::Float(EPSILON)],
                },
                Step::Products(products) => {
                    let table: Vec<Product> = products.iter().map(|p| Product {
                        a: address(halves(p.a)), b: address(halves(p.b)), bias: p.bias.map_or(0, |at| address(halves(at))),
                        r: p.r.map_or(0, |at| address(halves(at))), c: address(halves(p.c)),
                        m: p.m as i32, n: p.n as i32, k: p.k as i32, lda: p.lda as i32, ldb: p.ldb as i32, ldc: p.ldc as i32, ldr: p.ldr as i32,
                        act: p.act as i32,
                        rc: p.rotary.map_or(0, |turn| address(halves(turn.cos))), rs: p.rotary.map_or(0, |turn| address(halves(turn.sin))),
                        pdiv: p.rotary.map_or(1, |turn| turn.pdiv as i32), pmod: p.rotary.map_or(1, |turn| turn.pmod as i32),
                        rope_n: p.rotary.map_or(0, |turn| turn.columns as i32), pad: 0,
                    }).collect();
                    let most = |size: fn(&Product) -> i32| table.iter().map(size).max().unwrap_or(0) as u32;
                    Launch {
                        function: gemm,
                        grid: [most(|p| p.n).div_ceil(128), most(|p| p.m).div_ceil(128), table.len() as u32],
                        threads: 256,
                        arguments: vec![Argument::At(gpu.uploaded(&table)?)],
                    }
                }
                Step::Norm { x, gamma, to } => Launch {
                    function: rmsnorm,
                    grid: [(rows as u32).div_ceil(8), 1, 1],
                    threads: 256,
                    arguments: vec![halves(*x), Argument::At(0), halves(*gamma), halves(*to), Argument::Int(rows), Argument::Float(EPSILON)],
                },
                Step::Gates { normed, weights, bias, to } => Launch {
                    function: gates,
                    grid: [(rows as u32).div_ceil(8), 1, 1],
                    threads: 256,
                    arguments: vec![halves(*normed), halves(*weights), halves(*bias), words(*to), Argument::Int(rows)],
                },
                Step::Attention { q, k, v, to, gates, length, sequences, between, step } => Launch {
                    function: flash,
                    grid: [(*length as u32).div_ceil(64), (sequences * 8) as u32, 1],
                    threads: 128,
                    arguments: vec![halves(*q), halves(*k), halves(*v), halves(*to), words(*gates), Argument::Int(*length as i32),
                                    Argument::Int(*between as i32), Argument::Int(*step as i32), Argument::Int(HEADS as i32), Argument::Int(HEADS as i32)],
                },
                Step::Glu { hidden, starts, widths, offsets, masks } => Launch {
                    function: glu,
                    grid: [t as u32, BANDS as u32, 1],
                    threads: 256,
                    arguments: vec![halves(*hidden), words(*starts), words(*widths), words(*offsets), words(*masks), Argument::Int(t)],
                },
            });
        }
        gpu.synchronize()?;
        let host_spec = gpu.page_locked(ROWS * frames * 2 * 4)?;
        let host_masks = gpu.page_locked(frames * network.width * 4)?;
        Ok(SeparatorCuda {
            gpu: Mutex::new(gpu), launches, spec: places[network.spec], masks: places[network.masks], frames, width: network.width, kernels,
            device_nanos: AtomicU64::new(0), host_spec, host_masks,
        })
    }
}

#[pymethods]
impl SeparatorCuda {
    /// SeparatorCuda(weights, tensors, bands, ptx, frames): weights is separator.weights, tensors and bands what
    /// separator.json says of it, ptx the kernels' PTX files in the order to try them, frames the frames of every
    /// chunk it will run.
    #[new]
    fn new(py: Python<'_>, weights: PathBuf, tensors: Vec<Stored>, bands: Vec<(usize, usize)>, ptx: Vec<PathBuf>, frames: usize) -> PyResult<Self> {
        py.detach(|| {
            let data = std::fs::read(&weights).map_err(|e| format!("{}: {e}", weights.display()))?;
            SeparatorCuda::build(&data, &tensors, &bands, &ptx, frames)
        })
        .map_err(PyRuntimeError::new_err)
    }

    /// run(spec, chunks, frames) -> the masks (frames x width 32-bit floats) of one spectrum.
    fn run<'py>(&self, py: Python<'py>, spec: &[u8], chunks: usize, frames: usize) -> PyResult<Bound<'py, PyBytes>> {
        if chunks != 1 || frames != self.frames || spec.len() != ROWS * frames * 2 * 4 {
            return Err(PyValueError::new_err("the GPU network takes one chunk of the frames it was made for"));
        }
        // One chunk at a time: the page-locked buffers hold one spectrum and one chunk's masks.
        let gpu = self.gpu.try_lock().map_err(|_| PyRuntimeError::new_err("this network is running another chunk, or a run of it panicked"))?;
        let gpu = &*gpu;
        let masks = self.frames * self.width * 4;
        py.detach(|| -> Result<(), String> {
            gpu.current()?;
            unsafe { std::ptr::copy_nonoverlapping(spec.as_ptr(), self.host_spec, spec.len()) };
            let started = Instant::now();
            // The copy in, the kernels and the copy out queue on the one stream, in that order, and the host waits
            // once for all of them: no copy can pass a kernel, and none makes the host wait by itself.
            gpu.driver.check(unsafe { (gpu.driver.cuMemcpyHtoDAsync_v2)(self.spec, self.host_spec.cast(), spec.len(), gpu.stream) }, "a copy to the GPU")?;
            for launch in &self.launches {
                gpu.launch(launch)?;
            }
            gpu.driver.check(unsafe { (gpu.driver.cuMemcpyDtoHAsync_v2)(self.host_masks.cast(), self.masks, masks, gpu.stream) }, "a copy from the GPU")?;
            gpu.synchronize()?;
            self.device_nanos.store(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
            Ok(())
        })
        .map_err(PyRuntimeError::new_err)?;
        // the masks are the 32-bit floats the last kernel wrote: handed on as they came
        Ok(PyBytes::new(py, unsafe { std::slice::from_raw_parts(self.host_masks, masks) }))
    }

    /// The seconds the GPU had the last chunk run() gave: the rest of a run is the host's (copies and conversions).
    #[getter]
    fn device_seconds(&self) -> f64 {
        self.device_nanos.load(Ordering::Relaxed) as f64 * 1e-9
    }
}

/// separator_gpu() -> what the NVIDIA driver says of the first GPU: (the driver's version or None, the CUDA
/// interface's version (13000 is 13.0), the GPU's name, its compute capability (major, minor), its free video memory
/// in bytes, its video memory in bytes). RuntimeError, saying why, where there is no NVIDIA driver or GPU.
#[pyfunction]
pub fn separator_gpu(py: Python<'_>) -> PyResult<(Option<String>, i32, String, (i32, i32), usize, usize)> {
    py.detach(|| -> Result<_, String> {
        let (_gpu, card) = Gpu::open()?;
        Ok((driver_version(), card.interface, card.name, card.capability, card.free, card.total))
    })
    .map_err(PyRuntimeError::new_err)
}
