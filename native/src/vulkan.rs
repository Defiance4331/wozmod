//! The Dream voice separator's network on any GPU with a Vulkan driver: the system's Vulkan loader is loaded at run
//! time and runs our compute shaders (packaging/separator/vulkan/, compiled to SPIR-V by
//! packaging/separator_kernels.py). Nothing of a Vulkan SDK is used or shipped.
//!
//! The network it runs is network.rs's. Its tensors lie in device buffers, each buffer no larger than the device
//! lets one binding hold (maxStorageBufferRange), a tensor wholly inside one. A shader takes each operand's buffer on
//! a binding of its own and the operand's place in it as an offset, so a step whose operands lie in several buffers
//! is still one dispatch; products that run side by side are dispatched a group per set of buffers.
//!
//! A chunk is one command buffer, recorded once: the copy of its spectrum in, a dispatch a step (each followed by a
//! barrier), the copy of its masks out. The copies are part of it, on the queue the shaders run on, so no shader
//! can start before its input has landed.
//!
//! What a driver got wrong while this was written, and what holds here against each:
//! - conversions to 16-bit floats truncated: the shaders declare round-to-nearest, or round by hand;
//! - a shader with more shared memory than the device has made the driver fault at the first dispatch: every
//!   shader's shared memory (the build writes it down) is held against maxComputeSharedMemorySize before its
//!   pipeline is made;
//! - a matrix tile loaded past a tensor's end read the next tensor: a tensor read that way has that much slack
//!   after it (network.rs).

use ash::vk;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::HashMap;
use std::ffi::{c_void, CStr};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use crate::network::{At, Network, Product, Step, BANDS, EPSILON, HEADS};
use crate::separator::{Stored, ROWS};

const NONE: u32 = 0xffff_ffff;
const BINDINGS: usize = 5;
const STAGE: u64 = 64 << 20; // the staging buffer: a chunk's spectrum at its start, its masks at MASKS_AT
const MASKS_AT: u64 = 32 << 20;
const COOPERATIVE_MATRIX_2: &CStr = c"VK_NV_cooperative_matrix2";

/// VkPhysicalDeviceCooperativeMatrix2FeaturesNV, which this ash does not know yet.
#[repr(C)]
struct Matrix2Features {
    s_type: vk::StructureType,
    p_next: *mut c_void,
    workgroup_scope: vk::Bool32,
    flexible_dimensions: vk::Bool32,
    reductions: vk::Bool32,
    conversions: vk::Bool32,
    per_element_operations: vk::Bool32,
    tensor_addressing: vk::Bool32,
    block_loads: vk::Bool32,
}

impl Matrix2Features {
    fn new() -> Self {
        Matrix2Features {
            s_type: vk::StructureType::from_raw(1_000_593_000),
            p_next: std::ptr::null_mut(),
            workgroup_scope: 0, flexible_dimensions: 0, reductions: 0, conversions: 0,
            per_element_operations: 0, tensor_addressing: 0, block_loads: 0,
        }
    }
}

fn said<T>(result: Result<T, vk::Result>, what: &str) -> Result<T, String> {
    result.map_err(|error| format!("{what}: {error:?}"))
}

/// What a Vulkan device says of itself.
struct Facts {
    name: String,
    kind: &'static str, // "discrete", "integrated", "virtual", "cpu" or "other"
    vendor: u32,
    driver: String,
    version: (u32, u32, u32),
    subgroup: (u32, u32, u32),     // the subgroup size, and the least and the most a compute pipeline may ask for instead
    matrices: Vec<(u32, u32, u32)>, // cooperative matrix shapes (M, N, K) of 16-bit floats summed in 32-bit, a subgroup's
    matrix2: bool,                 // VK_NV_cooperative_matrix2 with reductions, conversions and per-element operations
    float16: bool,                 // 16-bit float arithmetic and 16-bit storage
    full_subgroups: bool,          // a pipeline may ask for full subgroups, which every shader here counts on
    buffer_limit: u64,             // maxStorageBufferRange
    shared_limit: u32,             // maxComputeSharedMemorySize
    memory: u64,                   // bytes of its largest device-local heap still free (its size where the driver does not say)
}

struct Loaded {
    entry: ash::Entry,
    instance: ash::Instance,
    devices: Vec<vk::PhysicalDevice>,
}

impl Drop for Loaded {
    fn drop(&mut self) {
        unsafe { self.instance.destroy_instance(None) }; // on every path out, after whatever was made of it
    }
}

fn loaded() -> Result<Loaded, String> {
    let entry = unsafe { ash::Entry::load() }.map_err(|error| format!("no Vulkan loader: {error}"))?;
    let application = vk::ApplicationInfo::default().api_version(vk::API_VERSION_1_3);
    let instance = said(unsafe { entry.create_instance(&vk::InstanceCreateInfo::default().application_info(&application), None) }, "a Vulkan instance")?;
    let mut vulkan = Loaded { entry, instance, devices: Vec::new() };
    vulkan.devices = said(unsafe { vulkan.instance.enumerate_physical_devices() }, "listing the Vulkan devices")?;
    Ok(vulkan)
}

fn has(extensions: &[vk::ExtensionProperties], name: &CStr) -> bool {
    extensions.iter().any(|each| each.extension_name_as_c_str().is_ok_and(|found| found == name))
}

/// The features a device has, as the chain a device is created with; `matrix` and `matrix2` say which of the last
/// two the chain holds.
struct Features {
    base: vk::PhysicalDeviceFeatures2<'static>,
    v11: vk::PhysicalDeviceVulkan11Features<'static>,
    v12: vk::PhysicalDeviceVulkan12Features<'static>,
    v13: vk::PhysicalDeviceVulkan13Features<'static>,
    matrix: vk::PhysicalDeviceCooperativeMatrixFeaturesKHR<'static>,
    matrix2: Matrix2Features,
}

impl Features {
    /// Ask the device, boxed so the chain's pointers stay good.
    fn of(vulkan: &Loaded, device: vk::PhysicalDevice, matrix: bool, matrix2: bool) -> Box<Features> {
        let mut found = Box::new(Features {
            base: Default::default(), v11: Default::default(), v12: Default::default(), v13: Default::default(),
            matrix: Default::default(), matrix2: Matrix2Features::new(),
        });
        let chain = &mut *found;
        if matrix2 {
            chain.matrix.p_next = (&mut chain.matrix2 as *mut Matrix2Features).cast();
        }
        if matrix {
            chain.v13.p_next = (&mut chain.matrix as *mut vk::PhysicalDeviceCooperativeMatrixFeaturesKHR).cast();
        }
        chain.v12.p_next = (&mut chain.v13 as *mut vk::PhysicalDeviceVulkan13Features).cast();
        chain.v11.p_next = (&mut chain.v12 as *mut vk::PhysicalDeviceVulkan12Features).cast();
        chain.base.p_next = (&mut chain.v11 as *mut vk::PhysicalDeviceVulkan11Features).cast();
        unsafe { vulkan.instance.get_physical_device_features2(device, &mut chain.base) };
        chain.base.features.robust_buffer_access = vk::FALSE; // its checks cost time, and no shader reads past a buffer
        found
    }
}

fn facts(vulkan: &Loaded, device: vk::PhysicalDevice) -> Result<Facts, String> {
    let extensions = said(unsafe { vulkan.instance.enumerate_device_extension_properties(device) }, "a device's extensions")?;
    let (matrix, matrix2) = (has(&extensions, ash::khr::cooperative_matrix::NAME), has(&extensions, COOPERATIVE_MATRIX_2));
    let mut v11 = vk::PhysicalDeviceVulkan11Properties::default();
    let mut v12 = vk::PhysicalDeviceVulkan12Properties::default();
    let mut v13 = vk::PhysicalDeviceVulkan13Properties::default();
    let mut properties = vk::PhysicalDeviceProperties2::default().push_next(&mut v11).push_next(&mut v12).push_next(&mut v13);
    unsafe { vulkan.instance.get_physical_device_properties2(device, &mut properties) };
    let base = properties.properties;
    let features = Features::of(vulkan, device, matrix, matrix && matrix2);
    let mut matrices = Vec::new();
    if matrix && features.matrix.cooperative_matrix == vk::TRUE {
        let shapes = ash::khr::cooperative_matrix::Instance::new(&vulkan.entry, &vulkan.instance);
        for each in said(unsafe { shapes.get_physical_device_cooperative_matrix_properties(device) }, "the device's matrix shapes")? {
            let float16 = vk::ComponentTypeKHR::FLOAT16;
            if each.a_type == float16 && each.b_type == float16 && each.c_type == vk::ComponentTypeKHR::FLOAT32
                && each.result_type == vk::ComponentTypeKHR::FLOAT32 && each.scope == vk::ScopeKHR::SUBGROUP
                && !matrices.contains(&(each.m_size, each.n_size, each.k_size))
            {
                matrices.push((each.m_size, each.n_size, each.k_size));
            }
        }
    }
    let mut budget = vk::PhysicalDeviceMemoryBudgetPropertiesEXT::default();
    let budgeted = has(&extensions, ash::ext::memory_budget::NAME);
    let mut memory = vk::PhysicalDeviceMemoryProperties2::default();
    if budgeted {
        memory = memory.push_next(&mut budget);
    }
    unsafe { vulkan.instance.get_physical_device_memory_properties2(device, &mut memory) };
    let heaps = memory.memory_properties;
    let free = (0..heaps.memory_heap_count as usize)
        .filter(|&heap| heaps.memory_heaps[heap].flags.contains(vk::MemoryHeapFlags::DEVICE_LOCAL))
        .map(|heap| if budgeted { budget.heap_budget[heap].saturating_sub(budget.heap_usage[heap]) } else { heaps.memory_heaps[heap].size })
        .max()
        .unwrap_or(0);
    let text = |chars: &[std::ffi::c_char]| unsafe { CStr::from_ptr(chars.as_ptr()) }.to_string_lossy().into_owned();
    Ok(Facts {
        name: text(&base.device_name),
        kind: match base.device_type {
            vk::PhysicalDeviceType::DISCRETE_GPU => "discrete",
            vk::PhysicalDeviceType::INTEGRATED_GPU => "integrated",
            vk::PhysicalDeviceType::VIRTUAL_GPU => "virtual",
            vk::PhysicalDeviceType::CPU => "cpu",
            _ => "other",
        },
        vendor: base.vendor_id,
        driver: format!("{} {}", text(&v12.driver_name), text(&v12.driver_info)),
        version: (vk::api_version_major(base.api_version), vk::api_version_minor(base.api_version), vk::api_version_patch(base.api_version)),
        subgroup: if features.v13.subgroup_size_control == vk::TRUE && v13.required_subgroup_size_stages.contains(vk::ShaderStageFlags::COMPUTE) {
            (v11.subgroup_size, v13.min_subgroup_size, v13.max_subgroup_size)
        } else {
            (v11.subgroup_size, v11.subgroup_size, v11.subgroup_size)
        },
        matrices,
        matrix2: features.matrix2.reductions == vk::TRUE && features.matrix2.conversions == vk::TRUE && features.matrix2.per_element_operations == vk::TRUE,
        float16: features.v12.shader_float16 == vk::TRUE && features.v11.storage_buffer16_bit_access == vk::TRUE,
        full_subgroups: features.v13.compute_full_subgroups == vk::TRUE,
        buffer_limit: base.limits.max_storage_buffer_range as u64,
        shared_limit: base.limits.max_compute_shared_memory_size,
        memory: free,
    })
}

/// The shaders a network is run with, and how: what separation.py picked for the device, as a dict.
#[derive(FromPyObject)]
#[pyo3(from_item_all)]
pub struct Kernels {
    /// Each step's SPIR-V file, by step: "products", "attention", "norm", "gates", "bandsplit", "glu".
    files: HashMap<String, PathBuf>,
    /// The cooperative matrix shape the products shader is specialised to; None for the shader without matrices.
    matrix: Option<(u32, u32, u32)>,
    matrix2: bool,          // the attention shader needs VK_NV_cooperative_matrix2
    tile: (u32, u32),       // the products shader's tile: rows and columns a workgroup computes
    queries: u32,           // the attention shader's queries a workgroup
    subgroup: u32,          // the subgroup size every pipeline is made with
    required: bool,         // each pipeline asks for that size: a device with a choice of sizes gives any of them otherwise
    limit: Option<u64>,     // a smaller buffer size than the device's, for tests
}

struct Dispatch {
    pipeline: vk::Pipeline,
    buffers: [usize; BINDINGS],
    push: Vec<u32>,
    grid: [u32; 3],
}

struct Gpu {
    vulkan: Loaded,
    index: usize, // the device's place in vulkan.devices
    device: ash::Device,
    queue: vk::Queue,
    pool: vk::CommandPool,
    fence: vk::Fence,
    set_layout: vk::DescriptorSetLayout,
    layout: vk::PipelineLayout,
    sets: vk::DescriptorPool,
    buffers: Vec<(vk::Buffer, vk::DeviceMemory)>,
    stage: (vk::Buffer, vk::DeviceMemory, *mut u8),
    pipelines: Vec<vk::Pipeline>,
    modules: Vec<vk::ShaderModule>,
}

unsafe impl Send for Gpu {} // its handles are the driver's; the Mutex that holds it orders their use
unsafe impl Sync for Gpu {} // reached only through that Mutex, whose holder may lend it to the thread that runs a chunk

impl Gpu {
    fn buffer(&mut self, size: u64, host: bool) -> Result<(vk::Buffer, vk::DeviceMemory), String> {
        let usage = vk::BufferUsageFlags::STORAGE_BUFFER | vk::BufferUsageFlags::TRANSFER_SRC | vk::BufferUsageFlags::TRANSFER_DST;
        let buffer = said(unsafe { self.device.create_buffer(&vk::BufferCreateInfo::default().size(size).usage(usage), None) }, "a device buffer")?;
        let needs = unsafe { self.device.get_buffer_memory_requirements(buffer) };
        let types = unsafe { self.vulkan.instance.get_physical_device_memory_properties(self.physical()) };
        // The staging buffer is read back, so it wants the host's cache: memory without it reads at a crawl.
        let visible = vk::MemoryPropertyFlags::HOST_VISIBLE | vk::MemoryPropertyFlags::HOST_COHERENT;
        let wanted: &[vk::MemoryPropertyFlags] = if host { &[visible | vk::MemoryPropertyFlags::HOST_CACHED, visible] } else { &[vk::MemoryPropertyFlags::DEVICE_LOCAL] };
        let kind = wanted
            .iter()
            .find_map(|&flags| (0..types.memory_type_count).find(|&i| needs.memory_type_bits & (1 << i) != 0 && types.memory_types[i as usize].property_flags.contains(flags)))
            .ok_or("the device has no memory of the kind a buffer needs")?;
        let memory = said(
            unsafe { self.device.allocate_memory(&vk::MemoryAllocateInfo::default().allocation_size(needs.size).memory_type_index(kind), None) },
            &format!("{size} bytes of device memory"),
        )?;
        said(unsafe { self.device.bind_buffer_memory(buffer, memory, 0) }, "binding a buffer's memory")?;
        Ok((buffer, memory))
    }

    fn physical(&self) -> vk::PhysicalDevice {
        self.vulkan.devices[self.index]
    }

    /// Run commands now, and wait for them.
    fn now(&self, record: impl FnOnce(vk::CommandBuffer)) -> Result<(), String> {
        let info = vk::CommandBufferAllocateInfo::default().command_pool(self.pool).command_buffer_count(1);
        let commands = said(unsafe { self.device.allocate_command_buffers(&info) }, "a command buffer")?[0];
        let begin = vk::CommandBufferBeginInfo::default().flags(vk::CommandBufferUsageFlags::ONE_TIME_SUBMIT);
        said(unsafe { self.device.begin_command_buffer(commands, &begin) }, "starting commands")?;
        record(commands);
        said(unsafe { self.device.end_command_buffer(commands) }, "ending commands")?;
        self.submit(commands)?;
        unsafe { self.device.free_command_buffers(self.pool, &[commands]) };
        Ok(())
    }

    fn submit(&self, commands: vk::CommandBuffer) -> Result<(), String> {
        let list = [commands];
        let submit = [vk::SubmitInfo::default().command_buffers(&list)];
        unsafe {
            said(self.device.reset_fences(&[self.fence]), "the run's fence")?;
            said(self.device.queue_submit(self.queue, &submit, self.fence), "starting a run on the device")?;
            said(self.device.wait_for_fences(&[self.fence], true, u64::MAX), "a run on the device")
        }
    }

    /// Copy bytes into a buffer, through the staging buffer, a piece at a time.
    fn write(&self, to: usize, at: u64, data: &[u8]) -> Result<(), String> {
        for (piece, part) in data.chunks(STAGE as usize).enumerate() {
            unsafe { std::ptr::copy_nonoverlapping(part.as_ptr(), self.stage.2, part.len()) };
            let region = [vk::BufferCopy { src_offset: 0, dst_offset: at + piece as u64 * STAGE, size: part.len() as u64 }];
            self.now(|commands| unsafe { self.device.cmd_copy_buffer(commands, self.stage.0, self.buffers[to].0, &region) })?;
        }
        Ok(())
    }

    /// A pipeline of a SPIR-V file, its subgroups full and of `subgroup` lanes (`fixed`: asked for, where the device
    /// has a choice of sizes), specialised with `constants`. Refused when the shader declares more shared memory than `limit`: a
    /// driver handed one faults at its first dispatch.
    fn pipeline(&mut self, file: &PathBuf, limit: u32, subgroup: u32, fixed: bool, constants: &[u32]) -> Result<vk::Pipeline, String> {
        let name = file.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let code = std::fs::read(file).map_err(|error| format!("{}: {error}", file.display()))?;
        let words = ash::util::read_spv(&mut std::io::Cursor::new(&code)).map_err(|error| format!("{name}: {error}"))?;
        let shared = shared_memory(&words).map_err(|error| format!("{name}: {error}"))?;
        if shared > limit as u64 {
            return Err(format!("{name} needs {shared} bytes of shared memory and the device has {limit}"));
        }
        let module = said(unsafe { self.device.create_shader_module(&vk::ShaderModuleCreateInfo::default().code(&words), None) }, &name)?;
        self.modules.push(module);
        let entries: Vec<vk::SpecializationMapEntry> =
            (0..constants.len() as u32).map(|id| vk::SpecializationMapEntry { constant_id: id, offset: 4 * id, size: 4 }).collect();
        let data: Vec<u8> = constants.iter().flat_map(|value| value.to_ne_bytes()).collect();
        let specialised = vk::SpecializationInfo::default().map_entries(&entries).data(&data);
        let mut sized = vk::PipelineShaderStageRequiredSubgroupSizeCreateInfo::default().required_subgroup_size(subgroup);
        let mut stage = vk::PipelineShaderStageCreateInfo::default()
            .stage(vk::ShaderStageFlags::COMPUTE)
            .module(module)
            .name(c"main")
            .flags(vk::PipelineShaderStageCreateFlags::REQUIRE_FULL_SUBGROUPS);
        if !constants.is_empty() {
            stage = stage.specialization_info(&specialised);
        }
        if fixed {
            stage = stage.push_next(&mut sized);
        }
        let info = [vk::ComputePipelineCreateInfo::default().stage(stage).layout(self.layout)];
        let made = unsafe { self.device.create_compute_pipelines(vk::PipelineCache::null(), &info, None) }
            .map_err(|(_, error)| format!("the device refused {name}: {error:?}"))?;
        self.pipelines.push(made[0]);
        Ok(made[0])
    }
}

/// The bytes of shared memory (variables of the Workgroup storage class) a SPIR-V module declares, counted the way
/// Vulkan bounds a driver's own count: each variable at the next offset its type allows under the standard buffer
/// layout.
fn shared_memory(words: &[u32]) -> Result<u64, String> {
    #[derive(Clone, Copy)]
    enum Type {
        Sized(u64, u64), // bytes, alignment
        Pointer(u32),    // to a type
    }
    let (mut types, mut constants): (HashMap<u32, Type>, HashMap<u32, u64>) = Default::default();
    let mut total = 0u64;
    let up = |value: u64, to: u64| value.div_ceil(to) * to;
    let mut at = 5;
    while at < words.len() {
        let (count, op) = ((words[at] >> 16) as usize, words[at] & 0xffff);
        if count == 0 || at + count > words.len() {
            return Err("not a SPIR-V module".into());
        }
        let w = &words[at..at + count];
        // A type this count does not know (a matrix unit's, an array sized by a specialization constant) is left
        // out, and so are the types made of it; it is an error only where a shared variable has one.
        let sized = |id: u32| match types.get(&id) {
            Some(Type::Sized(bytes, alignment)) => Some((*bytes, *alignment)),
            _ => None,
        };
        match op {
            20 => drop(types.insert(w[1], Type::Sized(4, 4))),                                // OpTypeBool
            21 | 22 => drop(types.insert(w[1], Type::Sized(w[2] as u64 / 8, w[2] as u64 / 8))), // OpTypeInt, OpTypeFloat
            23 => {
                // OpTypeVector: three components align as four
                if let Some((bytes, _)) = sized(w[2]) {
                    types.insert(w[1], Type::Sized(bytes * w[3] as u64, bytes * if w[3] == 3 { 4 } else { w[3] as u64 }));
                }
            }
            24 | 28 => {
                // OpTypeMatrix (columns), OpTypeArray (elements, their count a constant)
                let length = if op == 24 { Some(w[3] as u64) } else { constants.get(&w[3]).copied() };
                if let (Some((bytes, alignment)), Some(length)) = (sized(w[2]), length) {
                    types.insert(w[1], Type::Sized(up(bytes, alignment) * length, alignment));
                }
            }
            30 => {
                // OpTypeStruct
                let (mut bytes, mut most) = (0, 1);
                let members: Option<Vec<(u64, u64)>> = w[2..].iter().map(|&member| sized(member)).collect();
                if let Some(members) = members {
                    for (size, alignment) in members {
                        bytes = up(bytes, alignment) + size;
                        most = most.max(alignment);
                    }
                    types.insert(w[1], Type::Sized(up(bytes, most), most));
                }
            }
            32 => drop(types.insert(w[1], Type::Pointer(w[3]))), // OpTypePointer
            43 => drop(constants.insert(w[2], w[3] as u64)),     // OpConstant
            59 if w[3] == 4 => {
                // OpVariable of the Workgroup storage class
                let Some(Type::Pointer(to)) = types.get(&w[1]).copied() else { return Err("a variable without a pointer type".into()) };
                let (bytes, alignment) = sized(to).ok_or("a shared variable of a type this count does not know")?;
                total = up(total, alignment) + bytes;
            }
            _ => {}
        }
        at += count;
    }
    Ok(total)
}

impl Drop for Gpu {
    fn drop(&mut self) {
        unsafe {
            let _ = self.device.device_wait_idle();
            for &pipeline in &self.pipelines {
                self.device.destroy_pipeline(pipeline, None);
            }
            for &module in &self.modules {
                self.device.destroy_shader_module(module, None);
            }
            self.device.destroy_descriptor_pool(self.sets, None);
            self.device.destroy_pipeline_layout(self.layout, None);
            self.device.destroy_descriptor_set_layout(self.set_layout, None);
            self.device.destroy_fence(self.fence, None);
            self.device.destroy_command_pool(self.pool, None);
            for &(buffer, memory) in self.buffers.iter().chain(std::iter::once(&(self.stage.0, self.stage.1))) {
                self.device.destroy_buffer(buffer, None);
                self.device.free_memory(memory, None);
            }
            self.device.destroy_device(None);
        }
    }
}

/// Where each tensor lies: (its buffer, its first byte there), and each buffer's size. Tensors go one after
/// another, a new buffer wherever the next would pass `limit`.
fn placed(network: &Network, limit: u64) -> (Vec<(usize, u64)>, Vec<u64>) {
    let (mut places, mut sizes) = (Vec::new(), vec![0u64]);
    for tensor in &network.tensors {
        let size = ((tensor.bytes + tensor.slack) as u64).div_ceil(256) * 256;
        let mut last = sizes.len() - 1;
        if sizes[last] != 0 && sizes[last] + size > limit {
            sizes.push(0);
            last += 1;
        }
        places.push((last, sizes[last]));
        sizes[last] += size;
    }
    (places, sizes)
}

/// The network on one Vulkan device: spectra in, masks out.
#[pyclass]
pub struct SeparatorVulkan {
    gpu: Mutex<Gpu>,
    commands: vk::CommandBuffer,
    frames: usize,
    width: usize,
    /// The device buffers the network lies in.
    #[pyo3(get)]
    buffers: usize,
    device_nanos: AtomicU64, // how long the GPU had the last chunk: its command buffer, from submit to done
}

unsafe impl Send for SeparatorVulkan {}
unsafe impl Sync for SeparatorVulkan {}

impl SeparatorVulkan {
    fn build(index: usize, weights: &[u8], tensors: &[Stored], bands: &[(usize, usize)], frames: usize, kernels: &Kernels) -> Result<SeparatorVulkan, String> {
        let vulkan = loaded()?;
        let physical = *vulkan.devices.get(index).ok_or("the Vulkan device is no longer listed")?;
        let known = facts(&vulkan, physical)?;
        let extensions = said(unsafe { vulkan.instance.enumerate_device_extension_properties(physical) }, "the device's extensions")?;
        let matrix = kernels.matrix.is_some() || kernels.matrix2;
        let mut names = Vec::new();
        if matrix {
            names.push(ash::khr::cooperative_matrix::NAME.as_ptr());
        }
        if kernels.matrix2 {
            names.push(COOPERATIVE_MATRIX_2.as_ptr());
        }
        if (matrix && !has(&extensions, ash::khr::cooperative_matrix::NAME)) || (kernels.matrix2 && !has(&extensions, COOPERATIVE_MATRIX_2)) {
            return Err("the device lacks the matrix extension these shaders need".into());
        }
        let families = unsafe { vulkan.instance.get_physical_device_queue_family_properties(physical) };
        let family = families.iter().position(|each| each.queue_flags.contains(vk::QueueFlags::COMPUTE)).ok_or("the device has no compute queue")? as u32;
        let features = Features::of(&vulkan, physical, matrix, kernels.matrix2);
        let queues = [vk::DeviceQueueCreateInfo::default().queue_family_index(family).queue_priorities(&[1.0])];
        let mut create = vk::DeviceCreateInfo::default().queue_create_infos(&queues).enabled_extension_names(&names);
        create.p_next = (&features.base as *const vk::PhysicalDeviceFeatures2).cast();
        let device = said(unsafe { vulkan.instance.create_device(physical, &create, None) }, "opening the Vulkan device")?;
        // The device's handles are the Gpu's from here: whatever stops the build, its Drop gives them back.
        let queue = unsafe { device.get_device_queue(family, 0) };
        let mut gpu = Gpu {
            vulkan, index, device, queue,
            pool: vk::CommandPool::null(), fence: vk::Fence::null(), set_layout: vk::DescriptorSetLayout::null(), layout: vk::PipelineLayout::null(),
            sets: vk::DescriptorPool::null(), buffers: Vec::new(), stage: (vk::Buffer::null(), vk::DeviceMemory::null(), std::ptr::null_mut()),
            pipelines: Vec::new(), modules: Vec::new(),
        };
        let pool_info = vk::CommandPoolCreateInfo::default().queue_family_index(family);
        let bindings: Vec<vk::DescriptorSetLayoutBinding> = (0..BINDINGS as u32)
            .map(|binding| vk::DescriptorSetLayoutBinding::default().binding(binding).descriptor_type(vk::DescriptorType::STORAGE_BUFFER).descriptor_count(1).stage_flags(vk::ShaderStageFlags::COMPUTE))
            .collect();
        let ranges = [vk::PushConstantRange { stage_flags: vk::ShaderStageFlags::COMPUTE, offset: 0, size: 64 }];
        let sizes = [vk::DescriptorPoolSize { ty: vk::DescriptorType::STORAGE_BUFFER, descriptor_count: (BINDINGS * 1024) as u32 }];
        unsafe {
            gpu.pool = said(gpu.device.create_command_pool(&pool_info, None), "a command pool")?;
            gpu.fence = said(gpu.device.create_fence(&vk::FenceCreateInfo::default(), None), "a fence")?;
            gpu.set_layout = said(gpu.device.create_descriptor_set_layout(&vk::DescriptorSetLayoutCreateInfo::default().bindings(&bindings), None), "a binding layout")?;
            let layouts = [gpu.set_layout];
            gpu.layout = said(gpu.device.create_pipeline_layout(&vk::PipelineLayoutCreateInfo::default().set_layouts(&layouts).push_constant_ranges(&ranges), None), "a pipeline layout")?;
            gpu.sets = said(gpu.device.create_descriptor_pool(&vk::DescriptorPoolCreateInfo::default().max_sets(1024).pool_sizes(&sizes), None), "a binding pool")?;
        }
        let stage = gpu.buffer(STAGE, true)?;
        let mapped = said(unsafe { gpu.device.map_memory(stage.1, 0, STAGE, vk::MemoryMapFlags::empty()) }, "mapping the staging buffer")?;
        gpu.stage = (stage.0, stage.1, mapped.cast());

        // The pipelines, each shader's shared memory held against the device's first.
        let lanes = kernels.subgroup;
        if !known.full_subgroups || lanes < known.subgroup.1 || lanes > known.subgroup.2 || (!kernels.required && known.subgroup.1 != known.subgroup.2) {
            return Err(format!("the device cannot run its shaders in full subgroups of {lanes}, or was not asked to"));
        }
        let pipeline = |gpu: &mut Gpu, step: &str, constants: &[u32]| -> Result<vk::Pipeline, String> {
            let file = kernels.files.get(step).ok_or_else(|| format!("no shader was given for the {step} step"))?;
            gpu.pipeline(file, known.shared_limit, lanes, kernels.required, constants)
        };
        let products = match kernels.matrix {
            Some((m, n, k)) => pipeline(&mut gpu, "products", &[m, n, k, lanes, 8 * lanes])?,
            None => pipeline(&mut gpu, "products", &[])?,
        };
        let attention = pipeline(&mut gpu, "attention", &[])?;
        let (norm, gates, bandsplit, glu) = (pipeline(&mut gpu, "norm", &[])?, pipeline(&mut gpu, "gates", &[])?, pipeline(&mut gpu, "bandsplit", &[])?, pipeline(&mut gpu, "glu", &[])?);

        // The tensors, in buffers no larger than the device lets a binding hold.
        let limit = kernels.limit.unwrap_or(u64::MAX).min(known.buffer_limit);
        let network = Network::new(weights, tensors, bands, frames, limit as usize)?;
        let (places, sizes) = placed(&network, limit);
        for &size in &sizes {
            let made = gpu.buffer(size.max(256), false)?;
            gpu.buffers.push(made);
        }
        for (tensor, &(buffer, at)) in network.tensors.iter().zip(&places) {
            if let Some(data) = &tensor.data {
                gpu.write(buffer, at, data)?;
            }
        }
        let buffer_of = |at: At| places[at.tensor].0;
        let halves = |at: At| (places[at.tensor].1 / 2) as u32 + at.offset as u32; // a place counted in 16-bit floats
        let words = |at: At| (places[at.tensor].1 / 4) as u32 + at.offset as u32; // in 32-bit floats or integers

        // The steps as dispatches. Products go a group per set of buffers; every group's table lies in one last buffer.
        let (rows, t) = (network.rows as u32, network.frames as u32);
        let per_workgroup = 256 / lanes; // rows a workgroup of the small shaders takes: one a subgroup
        let tables = gpu.buffers.len();
        let mut table: Vec<u32> = Vec::new();
        let mut dispatches = Vec::new();
        for step in &network.steps {
            match step {
                Step::BandSplit { spec, picked, edges, gammas, features, padded } => {
                    if buffer_of(*picked) != buffer_of(*edges) {
                        return Err("the band tables lie in two buffers".into());
                    }
                    dispatches.push(Dispatch {
                        pipeline: bandsplit,
                        buffers: [buffer_of(*spec), buffer_of(*picked), buffer_of(*gammas), buffer_of(*features), 0],
                        push: vec![words(*spec), words(*picked), words(*edges), words(*gammas), halves(*features), t, *padded as u32, EPSILON.to_bits()],
                        grid: [t.div_ceil(per_workgroup), BANDS as u32, 1],
                    });
                }
                Step::Products(all) => {
                    let mut groups: Vec<([usize; BINDINGS], Vec<&Product>)> = Vec::new();
                    for product in all {
                        if product.bias.is_some_and(|bias| bias.tensor != product.b.tensor) || product.r.is_some_and(|r| r.tensor != product.c.tensor)
                            || product.rotary.is_some_and(|turn| turn.cos.tensor != turn.sin.tensor)
                        {
                            return Err("a product's bias, sum or rotary tables lie apart from where its shader looks for them".into());
                        }
                        let turn = product.rotary.map_or(0, |turn| buffer_of(turn.cos));
                        let key = [buffer_of(product.a), buffer_of(product.b), buffer_of(product.c), turn, tables];
                        match groups.iter_mut().find(|(found, _)| *found == key) {
                            Some((_, group)) => group.push(product),
                            None => groups.push((key, vec![product])),
                        }
                    }
                    for (buffers, group) in groups {
                        let start = table.len() as u32;
                        for p in &group {
                            table.extend([
                                halves(p.a), halves(p.b), p.bias.map_or(NONE, halves), p.r.map_or(NONE, halves), halves(p.c),
                                p.m as u32, p.n as u32, p.k as u32, p.lda as u32, p.ldb as u32, p.ldc as u32, p.ldr as u32, p.act,
                                p.rotary.map_or(NONE, |turn| halves(turn.cos)), p.rotary.map_or(NONE, |turn| halves(turn.sin)),
                                p.rotary.map_or(1, |turn| turn.pdiv as u32), p.rotary.map_or(1, |turn| turn.pmod as u32),
                                p.rotary.map_or(0, |turn| turn.columns as u32),
                            ]);
                        }
                        let most = |size: fn(&Product) -> usize| group.iter().map(|p| size(p)).max().unwrap_or(0) as u32;
                        dispatches.push(Dispatch {
                            pipeline: products,
                            buffers,
                            push: vec![start],
                            grid: [most(|p| p.n).div_ceil(kernels.tile.1), most(|p| p.m).div_ceil(kernels.tile.0), group.len() as u32],
                        });
                    }
                }
                Step::Norm { x, gamma, to } => dispatches.push(Dispatch {
                    pipeline: norm,
                    buffers: [buffer_of(*x), buffer_of(*gamma), buffer_of(*to), 0, 0],
                    push: vec![halves(*x), halves(*gamma), halves(*to), rows, EPSILON.to_bits()],
                    grid: [rows.div_ceil(per_workgroup), 1, 1],
                }),
                Step::Gates { normed, weights, bias, to } => {
                    if weights.tensor != bias.tensor {
                        return Err("the gates' weights and bias lie in two tensors".into());
                    }
                    dispatches.push(Dispatch {
                        pipeline: gates,
                        buffers: [buffer_of(*normed), buffer_of(*weights), buffer_of(*to), 0, 0],
                        push: vec![halves(*normed), halves(*weights), halves(*bias), words(*to), rows],
                        grid: [rows.div_ceil(per_workgroup), 1, 1],
                    });
                }
                Step::Attention { q, k, v, to, gates, length, sequences, between, step } => dispatches.push(Dispatch {
                    pipeline: attention,
                    buffers: [buffer_of(*q), buffer_of(*k), buffer_of(*v), buffer_of(*to), buffer_of(*gates)],
                    push: vec![halves(*q), halves(*k), halves(*v), halves(*to), words(*gates), *length as u32, *between as u32, *step as u32, HEADS as u32, HEADS as u32],
                    grid: [(*length as u32).div_ceil(kernels.queries), (sequences * 8) as u32, 1],
                }),
                Step::Glu { hidden, starts, widths, offsets, masks } => {
                    if buffer_of(*starts) != buffer_of(*widths) || buffer_of(*starts) != buffer_of(*offsets) {
                        return Err("the mask tables lie in several buffers".into());
                    }
                    dispatches.push(Dispatch {
                        pipeline: glu,
                        buffers: [buffer_of(*hidden), buffer_of(*starts), buffer_of(*masks), 0, 0],
                        push: vec![halves(*hidden), words(*starts), words(*widths), words(*offsets), words(*masks), t],
                        grid: [t, BANDS as u32, 1],
                    });
                }
            }
        }
        let made = gpu.buffer((4 * table.len() as u64).max(256), false)?;
        gpu.buffers.push(made);
        gpu.write(tables, 0, unsafe { std::slice::from_raw_parts(table.as_ptr().cast(), 4 * table.len()) })?;

        // A chunk's commands: its spectrum in, the dispatches, its masks out.
        let mut bound: HashMap<[usize; BINDINGS], vk::DescriptorSet> = HashMap::new();
        for dispatch in &dispatches {
            if bound.contains_key(&dispatch.buffers) {
                continue;
            }
            let layouts = [gpu.set_layout];
            let set = said(unsafe { gpu.device.allocate_descriptor_sets(&vk::DescriptorSetAllocateInfo::default().descriptor_pool(gpu.sets).set_layouts(&layouts)) }, "a set of bindings")?[0];
            let infos: Vec<[vk::DescriptorBufferInfo; 1]> =
                dispatch.buffers.iter().map(|&buffer| [vk::DescriptorBufferInfo { buffer: gpu.buffers[buffer].0, offset: 0, range: vk::WHOLE_SIZE }]).collect();
            let writes: Vec<vk::WriteDescriptorSet> = infos
                .iter()
                .enumerate()
                .map(|(binding, info)| vk::WriteDescriptorSet::default().dst_set(set).dst_binding(binding as u32).descriptor_type(vk::DescriptorType::STORAGE_BUFFER).buffer_info(info))
                .collect();
            unsafe { gpu.device.update_descriptor_sets(&writes, &[]) };
            bound.insert(dispatch.buffers, set);
        }
        let info = vk::CommandBufferAllocateInfo::default().command_pool(gpu.pool).command_buffer_count(1);
        let commands = said(unsafe { gpu.device.allocate_command_buffers(&info) }, "the chunk's command buffer")?[0];
        let (spec_at, masks_at) = (places[network.spec], places[network.masks]);
        let (spec_bytes, masks_bytes) = ((ROWS * frames * 2 * 4) as u64, (frames * network.width * 4) as u64);
        if spec_bytes > MASKS_AT || MASKS_AT + masks_bytes > STAGE {
            return Err("a chunk of that many frames does not fit the staging buffer".into());
        }
        let device = &gpu.device;
        let (shaders, copies) = (vk::PipelineStageFlags::COMPUTE_SHADER | vk::PipelineStageFlags::HOST, vk::PipelineStageFlags::TRANSFER);
        let before = [vk::MemoryBarrier::default()
            .src_access_mask(vk::AccessFlags::SHADER_WRITE | vk::AccessFlags::TRANSFER_WRITE | vk::AccessFlags::HOST_WRITE)
            .dst_access_mask(vk::AccessFlags::TRANSFER_READ | vk::AccessFlags::TRANSFER_WRITE)];
        let after = [vk::MemoryBarrier::default()
            .src_access_mask(vk::AccessFlags::TRANSFER_WRITE)
            .dst_access_mask(vk::AccessFlags::SHADER_READ | vk::AccessFlags::SHADER_WRITE | vk::AccessFlags::HOST_READ)];
        let between = [vk::MemoryBarrier::default().src_access_mask(vk::AccessFlags::SHADER_WRITE).dst_access_mask(vk::AccessFlags::SHADER_READ | vk::AccessFlags::SHADER_WRITE)];
        unsafe {
            said(device.begin_command_buffer(commands, &vk::CommandBufferBeginInfo::default()), "recording the chunk's commands")?;
            device.cmd_pipeline_barrier(commands, shaders, copies, vk::DependencyFlags::empty(), &before, &[], &[]);
            device.cmd_copy_buffer(commands, gpu.stage.0, gpu.buffers[spec_at.0].0, &[vk::BufferCopy { src_offset: 0, dst_offset: spec_at.1, size: spec_bytes }]);
            device.cmd_pipeline_barrier(commands, copies, shaders, vk::DependencyFlags::empty(), &after, &[], &[]);
            for dispatch in &dispatches {
                device.cmd_bind_pipeline(commands, vk::PipelineBindPoint::COMPUTE, dispatch.pipeline);
                device.cmd_bind_descriptor_sets(commands, vk::PipelineBindPoint::COMPUTE, gpu.layout, 0, &[bound[&dispatch.buffers]], &[]);
                let push: Vec<u8> = dispatch.push.iter().flat_map(|word| word.to_ne_bytes()).collect();
                device.cmd_push_constants(commands, gpu.layout, vk::ShaderStageFlags::COMPUTE, 0, &push);
                device.cmd_dispatch(commands, dispatch.grid[0], dispatch.grid[1], dispatch.grid[2]);
                device.cmd_pipeline_barrier(commands, vk::PipelineStageFlags::COMPUTE_SHADER, vk::PipelineStageFlags::COMPUTE_SHADER, vk::DependencyFlags::empty(), &between, &[], &[]);
            }
            device.cmd_pipeline_barrier(commands, shaders, copies, vk::DependencyFlags::empty(), &before, &[], &[]);
            device.cmd_copy_buffer(commands, gpu.buffers[masks_at.0].0, gpu.stage.0, &[vk::BufferCopy { src_offset: masks_at.1, dst_offset: MASKS_AT, size: masks_bytes }]);
            device.cmd_pipeline_barrier(commands, copies, shaders, vk::DependencyFlags::empty(), &after, &[], &[]);
            said(device.end_command_buffer(commands), "recording the chunk's commands")?;
        }
        let buffers = gpu.buffers.len();
        Ok(SeparatorVulkan { gpu: Mutex::new(gpu), commands, frames, width: network.width, buffers, device_nanos: AtomicU64::new(0) })
    }
}

#[pymethods]
impl SeparatorVulkan {
    /// SeparatorVulkan(device, weights, tensors, bands, frames, kernels): device is a place in
    /// separator_vulkan_devices' list, weights separator.weights, tensors and bands what separator.json says of it,
    /// frames the frames of every chunk it will run, kernels the shaders to run it with (vulkan.rs Kernels).
    #[new]
    fn new(py: Python<'_>, device: usize, weights: PathBuf, tensors: Vec<Stored>, bands: Vec<(usize, usize)>, frames: usize, kernels: Kernels) -> PyResult<Self> {
        py.detach(|| {
            let data = std::fs::read(&weights).map_err(|e| format!("{}: {e}", weights.display()))?;
            SeparatorVulkan::build(device, &data, &tensors, &bands, frames, &kernels)
        })
        .map_err(PyRuntimeError::new_err)
    }

    /// run(spec, chunks, frames) -> the masks (frames x width 32-bit floats) of one spectrum.
    fn run<'py>(&self, py: Python<'py>, spec: &[u8], chunks: usize, frames: usize) -> PyResult<Bound<'py, PyBytes>> {
        if chunks != 1 || frames != self.frames || spec.len() != ROWS * frames * 2 * 4 {
            return Err(PyValueError::new_err("the GPU network takes one chunk of the frames it was made for"));
        }
        // One chunk at a time: the staging buffer holds one spectrum and one chunk's masks.
        let gpu = self.gpu.try_lock().map_err(|_| PyRuntimeError::new_err("this network is running another chunk, or a run of it panicked"))?;
        let gpu = &*gpu;
        py.detach(|| -> Result<(), String> {
            unsafe { std::ptr::copy_nonoverlapping(spec.as_ptr(), gpu.stage.2, spec.len()) };
            let started = Instant::now();
            gpu.submit(self.commands)?;
            self.device_nanos.store(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
            Ok(())
        })
        .map_err(PyRuntimeError::new_err)?;
        // the masks are the 32-bit floats the last shader wrote: handed on as they lie in the staging buffer
        Ok(PyBytes::new(py, unsafe { std::slice::from_raw_parts(gpu.stage.2.add(MASKS_AT as usize), self.frames * self.width * 4) }))
    }

    /// The seconds the GPU had the last chunk run() gave: the rest of a run is the host's (copies and conversions).
    #[getter]
    fn device_seconds(&self) -> f64 {
        self.device_nanos.load(Ordering::Relaxed) as f64 * 1e-9
    }
}

type Said = (String, String, u32, String, (u32, u32, u32), (u32, u32, u32), Vec<(u32, u32, u32)>, (bool, bool, bool), (u64, u32, u64));

/// separator_shared_memory(file) -> the bytes of shared memory the SPIR-V file's shader declares.
#[pyfunction]
pub fn separator_shared_memory(file: PathBuf) -> PyResult<u64> {
    let code = std::fs::read(&file).map_err(|error| PyRuntimeError::new_err(format!("{}: {error}", file.display())))?;
    let words = ash::util::read_spv(&mut std::io::Cursor::new(&code)).map_err(|error| PyValueError::new_err(error.to_string()))?;
    shared_memory(&words).map_err(PyValueError::new_err)
}

/// separator_vulkan_devices() -> every Vulkan device, in the loader's order, each as (what it says of itself, None)
/// or (None, why that could not be read): (name, kind "discrete", "integrated", "virtual", "cpu" or "other", vendor
/// id, driver, Vulkan version, subgroup sizes (its own, least, most), matrix shapes (M, N, K), (matrix2, float16,
/// full subgroups), (buffer limit, shared memory limit, free device memory)). RuntimeError, saying why, where there
/// is no Vulkan loader.
#[pyfunction]
pub fn separator_vulkan_devices(py: Python<'_>) -> PyResult<Vec<(Option<Said>, Option<String>)>> {
    py.detach(|| -> Result<Vec<(Option<Said>, Option<String>)>, String> {
        let vulkan = loaded()?;
        Ok(vulkan
            .devices
            .iter()
            .map(|&device| match facts(&vulkan, device) {
                Ok(f) => (Some((f.name, f.kind.to_string(), f.vendor, f.driver, f.version, f.subgroup, f.matrices, (f.matrix2, f.float16, f.full_subgroups), (f.buffer_limit, f.shared_limit, f.memory))), None),
                Err(why) => (None, Some(why)),
            })
            .collect())
    })
    .map_err(PyRuntimeError::new_err)
}
