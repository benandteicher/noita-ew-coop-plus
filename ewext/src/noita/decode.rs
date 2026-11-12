//! Native world decode.
//!
//! The inbound half of world sync used to run in Lua (`quant.ew/files/system/
//! world_sync/world.lua:world.decode`): a per-pixel loop making two or more FFI
//! calls per pixel, over a 128x128 chunk, with no per-frame budget. That is
//! ~16k iterations and ~50-100k FFI transitions for a single chunk, executed
//! synchronously inside `OnWorldPreUpdate`.
//!
//! This is a direct transliteration of that loop. It deliberately calls the
//! same game functions the Lua path used - resolved by NoitaPatcher and handed
//! to us as raw pointers at init - rather than reimplementing cell construction
//! against hand-reversed struct layouts. That keeps the semantics identical to
//! the proven path and avoids taking on a second copy of Noita's memory layout.
//!
//! The one exception is finding a cell. The game's get_cell and chunk_loaded
//! are a few instructions of pointer arithmetic over the ChunkMap - the same
//! walk `encode_area` has always done directly - so when their code checks out
//! byte for byte we do that walk ourselves rather than calling them per pixel.
//! See [`CellAccess`]. remove_cell and construct_cell do real work and are
//! always called.
//!
//! The Lua implementation remains in the tree and is still the fallback; see
//! `quant.ew.native_world_decode`.

// The DLL only ever loads into 32-bit Noita. Host builds exist so `cargo check`
// and clippy can run on Linux, and there `decode_area` is a stub, which leaves
// its helpers unused.
#![cfg_attr(not(target_arch = "x86"), allow(dead_code))]

use std::ffi::c_void;

use super::ParticleWorldState;
use super::ntypes::{self, CELLDATA_SIZE};
use super::pixel::NoitaPixelRun;
#[cfg(target_arch = "x86")]
use super::{cell_slot, chunk_cells};

/// Mirrors `struct EncodedAreaHeader` from `world.lua`'s cdef. Packed, 12 bytes.
#[repr(C, packed)]
#[derive(Clone, Copy)]
pub(crate) struct EncodedAreaHeader {
    pub(crate) x: i32,
    pub(crate) y: i32,
    pub(crate) width: u8,
    pub(crate) height: u8,
    pub(crate) pixel_run_count: u16,
}

const _: () = assert!(size_of::<EncodedAreaHeader>() == 12);
const _: () = assert!(size_of::<NoitaPixelRun>() == 5);

/// `LIQUID_FLAG_STATIC` from `world.lua`'s cdef.
const LIQUID_FLAG_STATIC: u8 = 1;

/// Game functions needed to write cells, resolved by NoitaPatcher on the Lua
/// side (`noitapatcher.nsew.world_ffi`) and passed to us as raw addresses.
///
/// These are `__thiscall`, so the first argument goes in `ecx`. Rust only has
/// that ABI on 32-bit x86, which is the only architecture this DLL is ever
/// loaded into; the whole call surface is cfg'd out elsewhere.
#[cfg(target_arch = "x86")]
mod abi {
    use super::*;

    pub(super) type GetCellFn =
        unsafe extern "thiscall" fn(*mut c_void, i32, i32) -> *mut *mut ntypes::Cell;
    pub(super) type ChunkLoadedFn = unsafe extern "thiscall" fn(*mut c_void, i32, i32) -> bool;
    pub(super) type RemoveCellFn =
        unsafe extern "thiscall" fn(*mut c_void, *mut c_void, i32, i32, bool);
    pub(super) type ConstructCellFn = unsafe extern "thiscall" fn(
        *mut c_void,
        i32,
        i32,
        *const ntypes::CellData,
        *mut c_void,
    ) -> *mut ntypes::Cell;
}

#[cfg(not(target_arch = "x86"))]
mod abi {
    use super::*;

    // Placeholders so the crate still type-checks on the host for `cargo check`
    // and clippy. `decode_area` refuses to run on any non-x86 target, so these
    // are never called.
    pub(super) type GetCellFn =
        unsafe extern "C" fn(*mut c_void, i32, i32) -> *mut *mut ntypes::Cell;
    pub(super) type ChunkLoadedFn = unsafe extern "C" fn(*mut c_void, i32, i32) -> bool;
    pub(super) type RemoveCellFn = unsafe extern "C" fn(*mut c_void, *mut c_void, i32, i32, bool);
    pub(super) type ConstructCellFn = unsafe extern "C" fn(
        *mut c_void,
        i32,
        i32,
        *const ntypes::CellData,
        *mut c_void,
    ) -> *mut ntypes::Cell;
}

/// The game's get_cell, byte for byte, in each form NoitaPatcher might resolve:
/// the unchecked one (noita.exe 0x89c070) and three neighbours (0x89c0d0,
/// 0x89c140, 0x89c2d0) that return a dummy slot for an unloaded chunk. For a
/// loaded chunk all four return exactly `cell_slot(chunk_cells(..))`, and
/// nothing else: no allocation, no side effects. The absolute addresses in the
/// dummy-slot forms are fine to match on, as noita.exe has no ASLR.
pub(crate) const GET_CELL_CODE: [&[u8]; 4] = [
    &[
        0x55, 0x8b, 0xec, 0x56, 0x8b, 0x75, 0x08, 0x8b, 0xc6, 0x57, 0x8b, 0x7d, 0x0c, 0x81, 0xe6,
        0xff, 0x01, 0x00, 0x00, 0x8b, 0xd7, 0xc1, 0xf8, 0x09, 0xc1, 0xfa, 0x09, 0x2d, 0x00, 0x01,
        0x00, 0x00, 0x81, 0xea, 0x00, 0x01, 0x00, 0x00, 0x25, 0xff, 0x01, 0x00, 0x00, 0x81, 0xe2,
        0xff, 0x01, 0x00, 0x00, 0x81, 0xe7, 0xff, 0x01, 0x00, 0x00, 0xc1, 0xe2, 0x09, 0x03, 0xd0,
        0xc1, 0xe7, 0x09, 0x8b, 0x41, 0x08, 0x0b, 0xfe, 0x8b, 0x04, 0x90, 0x8b, 0x00, 0x8d, 0x04,
        0xb8, 0x5f, 0x5e, 0x5d, 0xc2, 0x08, 0x00,
    ],
    &[
        0x55, 0x8b, 0xec, 0x56, 0x8b, 0x75, 0x0c, 0x8b, 0xd6, 0xc1, 0xfa, 0x09, 0x57, 0x8b, 0x7d,
        0x08, 0x81, 0xea, 0x00, 0x01, 0x00, 0x00, 0x8b, 0xc7, 0x81, 0xe2, 0xff, 0x01, 0x00, 0x00,
        0xc1, 0xf8, 0x09, 0x2d, 0x00, 0x01, 0x00, 0x00, 0xc1, 0xe2, 0x09, 0x25, 0xff, 0x01, 0x00,
        0x00, 0x03, 0xd0, 0x8b, 0x41, 0x08, 0x8b, 0x04, 0x90, 0x85, 0xc0, 0x75, 0x10, 0x5f, 0xa3,
        0x20, 0x3d, 0x22, 0x01, 0xb8, 0x20, 0x3d, 0x22, 0x01, 0x5e, 0x5d, 0xc2, 0x08, 0x00, 0x8b,
        0x00, 0x81, 0xe6, 0xff, 0x01, 0x00, 0x00, 0xc1, 0xe6, 0x09, 0x81, 0xe7, 0xff, 0x01, 0x00,
        0x00, 0x0b, 0xf7, 0x5f, 0x8d, 0x04, 0xb0, 0x5e, 0x5d, 0xc2, 0x08, 0x00,
    ],
    &[
        0x55, 0x8b, 0xec, 0x56, 0x8b, 0x75, 0x0c, 0x8b, 0xd6, 0xc1, 0xfa, 0x09, 0x57, 0x8b, 0x7d,
        0x08, 0x81, 0xea, 0x00, 0x01, 0x00, 0x00, 0x8b, 0xc7, 0x81, 0xe2, 0xff, 0x01, 0x00, 0x00,
        0xc1, 0xf8, 0x09, 0x2d, 0x00, 0x01, 0x00, 0x00, 0xc1, 0xe2, 0x09, 0x25, 0xff, 0x01, 0x00,
        0x00, 0x03, 0xd0, 0x8b, 0x41, 0x08, 0x8b, 0x04, 0x90, 0x85, 0xc0, 0x75, 0x10, 0x5f, 0xa3,
        0xb0, 0x3d, 0x22, 0x01, 0xb8, 0xb0, 0x3d, 0x22, 0x01, 0x5e, 0x5d, 0xc2, 0x08, 0x00, 0x8b,
        0x00, 0x81, 0xe6, 0xff, 0x01, 0x00, 0x00, 0xc1, 0xe6, 0x09, 0x81, 0xe7, 0xff, 0x01, 0x00,
        0x00, 0x0b, 0xf7, 0x5f, 0x8d, 0x04, 0xb0, 0x5e, 0x5d, 0xc2, 0x08, 0x00,
    ],
    &[
        0x55, 0x8b, 0xec, 0x56, 0x8b, 0x75, 0x0c, 0x8b, 0xd6, 0xc1, 0xfa, 0x09, 0x57, 0x8b, 0x7d,
        0x08, 0x81, 0xea, 0x00, 0x01, 0x00, 0x00, 0x8b, 0xc7, 0x81, 0xe2, 0xff, 0x01, 0x00, 0x00,
        0xc1, 0xf8, 0x09, 0x2d, 0x00, 0x01, 0x00, 0x00, 0xc1, 0xe2, 0x09, 0x25, 0xff, 0x01, 0x00,
        0x00, 0x03, 0xd0, 0x8b, 0x41, 0x08, 0x8b, 0x04, 0x90, 0x85, 0xc0, 0x75, 0x0b, 0x5f, 0xb8,
        0x44, 0x46, 0x22, 0x01, 0x5e, 0x5d, 0xc2, 0x08, 0x00, 0x8b, 0x00, 0x81, 0xe6, 0xff, 0x01,
        0x00, 0x00, 0xc1, 0xe6, 0x09, 0x81, 0xe7, 0xff, 0x01, 0x00, 0x00, 0x0b, 0xf7, 0x5f, 0x8d,
        0x04, 0xb0, 0x5e, 0x5d, 0xc2, 0x08, 0x00,
    ],
];

/// The game's chunk_loaded (noita.exe 0x89c1b0): `chunk_table[chunk_index] != 0`.
pub(crate) const CHUNK_LOADED_CODE: &[u8] = &[
    0x55, 0x8b, 0xec, 0x8b, 0x55, 0x0c, 0x8b, 0x45, 0x08, 0x8b, 0x49, 0x08, 0xc1, 0xfa, 0x09, 0xc1,
    0xf8, 0x09, 0x81, 0xea, 0x00, 0x01, 0x00, 0x00, 0x2d, 0x00, 0x01, 0x00, 0x00, 0x81, 0xe2, 0xff,
    0x01, 0x00, 0x00, 0x25, 0xff, 0x01, 0x00, 0x00, 0xc1, 0xe2, 0x09, 0x03, 0xd0, 0x33, 0xc0, 0x39,
    0x04, 0x91, 0x0f, 0x95, 0xc0, 0x5d, 0xc2, 0x08, 0x00,
];

/// True if `code` begins with one of `known`.
fn code_matches(code: &[u8], known: &[&[u8]]) -> bool {
    known.iter().any(|k| code.starts_with(k))
}

/// How `decode_area` finds the slot for a cell.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum CellAccess {
    /// Call the game's chunk_loaded and get_cell. Used when their code is not
    /// one of the known forms (another Noita build, or something hooking them),
    /// and for good after a failed cross-check.
    Game,
    /// The code checked out, so walk the ChunkMap ourselves, but the result has
    /// not yet been compared with the game's own on a live area. The next
    /// decode does that first.
    DirectUnverified,
    /// Code checked out and results matched the game's: walk the ChunkMap.
    Direct,
}

#[derive(Clone, Copy)]
pub(crate) struct WorldFns {
    get_cell: abi::GetCellFn,
    chunk_loaded: abi::ChunkLoadedFn,
    remove_cell: abi::RemoveCellFn,
    construct_cell: abi::ConstructCellFn,
    /// Highest material id the game knows about. Discovered at runtime on the
    /// Lua side, so it accounts for modded material sets.
    last_material_id: i16,
    cell_access: CellAccess,
}

impl WorldFns {
    /// # Safety
    ///
    /// Every address must be the corresponding function from
    /// `noitapatcher.nsew.world_ffi`, taken from the currently running Noita
    /// process. Passing anything else will execute arbitrary memory.
    pub(crate) unsafe fn from_raw(
        get_cell: *const c_void,
        chunk_loaded: *const c_void,
        remove_cell: *const c_void,
        construct_cell: *const c_void,
        last_material_id: i16,
    ) -> eyre::Result<Self> {
        if get_cell.is_null()
            || chunk_loaded.is_null()
            || remove_cell.is_null()
            || construct_cell.is_null()
        {
            eyre::bail!("Got a null world function pointer from NoitaPatcher");
        }
        // SAFETY: caller guarantees these are real functions in the running
        // process, so their code is mapped and readable. The longest known form
        // is 102 bytes; a shorter function is followed by more .text.
        let cell_access = unsafe {
            let read = |f: *const c_void, len| std::slice::from_raw_parts(f.cast::<u8>(), len);
            let get_cell_len = GET_CELL_CODE.iter().map(|c| c.len()).max().unwrap_or(0);
            if code_matches(read(get_cell, get_cell_len), &GET_CELL_CODE)
                && code_matches(
                    read(chunk_loaded, CHUNK_LOADED_CODE.len()),
                    &[CHUNK_LOADED_CODE],
                )
            {
                CellAccess::DirectUnverified
            } else {
                CellAccess::Game
            }
        };
        // SAFETY: caller guarantees these are the real world_ffi functions, and
        // the signatures match world_ffi.lua's cdef block.
        unsafe {
            Ok(Self {
                get_cell: std::mem::transmute::<*const c_void, abi::GetCellFn>(get_cell),
                chunk_loaded: std::mem::transmute::<*const c_void, abi::ChunkLoadedFn>(
                    chunk_loaded,
                ),
                remove_cell: std::mem::transmute::<*const c_void, abi::RemoveCellFn>(remove_cell),
                construct_cell: std::mem::transmute::<*const c_void, abi::ConstructCellFn>(
                    construct_cell,
                ),
                last_material_id,
                cell_access,
            })
        }
    }
}

/// Remembers a per-chunk lookup - whether it is loaded, or its cell array -
/// for the last chunk it was asked about.
///
/// The ChunkMap is indexed by `coord >> 9` (see `chunk_index`), so the answer
/// can only change at a 512-cell boundary. World sync areas are 128-aligned
/// and never straddle one, which turns 16384 lookups per area into one. The
/// header comes off the network, so alignment is not assumed: the chunk is
/// still compared per pixel, and an unaligned area just costs a few more.
struct ChunkCache<T> {
    chunk: Option<(i32, i32)>,
    value: T,
}

impl<T: Copy> ChunkCache<T> {
    fn new(value: T) -> Self {
        Self { chunk: None, value }
    }

    fn get(&mut self, x: i32, y: i32, query: impl FnOnce() -> T) -> T {
        // Arithmetic shift, so negative coordinates floor the way the game's do.
        let chunk = (x >> 9, y >> 9);
        if self.chunk != Some(chunk) {
            self.value = query();
            self.chunk = Some(chunk);
        }
        self.value
    }
}

/// A validated local-protocol packet. Unlike the legacy decoder's parser,
/// this rejects every malformed extent before the first game mutation.
struct TerrainArea<'a> {
    x: i32,
    y: i32,
    data: &'a [u8],
    runs: &'a [u8],
}

impl<'a> TerrainArea<'a> {
    fn parse(data: &'a [u8], last_material_id: i16) -> eyre::Result<Self> {
        let (header, _, run_count) = parse_encoded_area(data)?;
        if header.width != 127 || header.height != 127 || header.x % 128 != 0 || header.y % 128 != 0
        {
            eyre::bail!("Terrain application requires one aligned 128x128 chunk");
        }
        header
            .x
            .checked_add(127)
            .ok_or_else(|| eyre::eyre!("Terrain x extent overflows"))?;
        header
            .y
            .checked_add(127)
            .ok_or_else(|| eyre::eyre!("Terrain y extent overflows"))?;
        let needed = size_of::<EncodedAreaHeader>() + run_count * size_of::<NoitaPixelRun>();
        if needed != data.len() || run_count > 128 * 128 {
            eyre::bail!("Terrain packet has extra bytes or too many runs");
        }
        let runs = &data[size_of::<EncodedAreaHeader>()..];
        let mut extent = 0usize;
        for run in runs.chunks_exact(5) {
            let material = i16::from_le_bytes([run[2], run[3]]);
            if material != -1 && (material < 0 || material > last_material_id) {
                eyre::bail!("Terrain packet has invalid material {material}");
            }
            if run[4] & !LIQUID_FLAG_STATIC != 0 {
                eyre::bail!("Terrain packet has unsupported flags {}", run[4]);
            }
            if material == 0 && run[4] != 0 {
                eyre::bail!("Air terrain cannot have liquid flags");
            }
            extent += usize::from(u16::from_le_bytes([run[0], run[1]])) + 1;
            if extent > 128 * 128 {
                eyre::bail!("Terrain runs exceed the chunk extent");
            }
        }
        if extent != 128 * 128 {
            eyre::bail!("Terrain runs do not fill the chunk extent");
        }
        Ok(Self {
            x: header.x,
            y: header.y,
            data,
            runs,
        })
    }

    fn offset(&self, x: i32, y: i32) -> usize {
        (x - self.x) as usize + (y - self.y) as usize * 128
    }

    /// Keep only targets not resolved during this call. In particular, later
    /// attempts must never reassert already completed cells over new edits.
    fn remaining_blob(&self, remaining: &[bool]) -> Option<Vec<u8>> {
        if !remaining.iter().any(|&value| value) {
            return None;
        }
        fn append_run(out: &mut Vec<u8>, length: usize, material: u16, flags: u8) {
            out.extend_from_slice(&((length - 1) as u16).to_le_bytes());
            out.extend_from_slice(&material.to_le_bytes());
            out.push(flags);
        }
        let mut out = self.data[..size_of::<EncodedAreaHeader>()].to_vec();
        let mut offset = 0usize;
        let mut count = 0u16;
        let mut current = (u16::MAX, 1u8);
        let mut length = 0usize;
        for run in self.runs.chunks_exact(5) {
            let run_length = usize::from(u16::from_le_bytes([run[0], run[1]])) + 1;
            let required = (u16::from_le_bytes([run[2], run[3]]), run[4]);
            for &unresolved in &remaining[offset..offset + run_length] {
                let target = if unresolved { required } else { (u16::MAX, 1) };
                if length != 0 && target != current {
                    append_run(&mut out, length, current.0, current.1);
                    count += 1;
                    length = 0;
                }
                current = target;
                length += 1;
            }
            offset += run_length;
        }
        append_run(&mut out, length, current.0, current.1);
        count += 1;
        out[10..12].copy_from_slice(&count.to_le_bytes());
        Some(out)
    }

    fn visit_required(
        &self,
        mut visit: impl FnMut(i32, i32, i16, u8) -> eyre::Result<()>,
    ) -> eyre::Result<()> {
        let mut offset = 0usize;
        for run in self.runs.chunks_exact(5) {
            let length = usize::from(u16::from_le_bytes([run[0], run[1]])) + 1;
            let material = i16::from_le_bytes([run[2], run[3]]);
            if material != -1 {
                for index in offset..offset + length {
                    visit(
                        self.x + (index % 128) as i32,
                        self.y + (index / 128) as i32,
                        material,
                        run[4],
                    )?;
                }
            }
            offset += length;
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug)]
struct TerrainCell {
    material: i16,
    kind: ntypes::CellType,
    flags: u8,
}

impl TerrainCell {
    /// The same synchronization projection as encode_area: box2d and
    /// non-terrain cells are represented as air, without removing them.
    fn matches(cell: Option<Self>, material: i16, flags: u8) -> bool {
        let (actual_material, actual_flags) = match cell {
            Some(Self {
                material,
                kind: ntypes::CellType::Liquid,
                flags,
            }) => (material, flags),
            Some(Self {
                material,
                kind: ntypes::CellType::Gas | ntypes::CellType::Fire,
                ..
            }) => (material, 0),
            _ => (0, 0),
        };
        actual_material == material && actual_flags == flags
    }
}

/// Shared by the real slot adapter and host regression adapters. A resolved
/// cell was observed after all mutations finished; construction count alone
/// never establishes application. Physical solid conflicts remain required.
trait TerrainGrid {
    fn loaded(&mut self, x: i32, y: i32) -> eyre::Result<bool>;
    fn read(&mut self, x: i32, y: i32) -> eyre::Result<Option<TerrainCell>>;
    fn remove(&mut self, x: i32, y: i32) -> eyre::Result<()>;
    fn construct(&mut self, x: i32, y: i32, material: i16, flags: u8) -> eyre::Result<()>;
    fn set_liquid_flags(&mut self, x: i32, y: i32, flags: u8) -> eyre::Result<()>;
}

pub(crate) struct TerrainApplyOutcome {
    pub(crate) remaining: Option<Vec<u8>>,
    /// False only when preflight observed an unloaded chunk before any writes.
    /// The caller must suppress that entire capture, including air-looking data.
    pub(crate) loaded: bool,
}

fn apply_terrain_grid(
    area: &TerrainArea<'_>,
    grid: &mut impl TerrainGrid,
) -> eyre::Result<TerrainApplyOutcome> {
    // Alignment guarantees these corners belong to the same native512 chunk.
    // Check load state separately: an unloaded encoder read also looks like air.
    let loaded = grid.loaded(area.x, area.y)? && grid.loaded(area.x + 127, area.y + 127)?;
    if area
        .runs
        .chunks_exact(5)
        .all(|run| u16::from_le_bytes([run[2], run[3]]) == u16::MAX)
    {
        // Unknown is no requirement. Even an unloaded empty mask is complete,
        // while loaded=false still tells the caller to suppress its capture.
        return Ok(TerrainApplyOutcome {
            remaining: None,
            loaded,
        });
    }
    if !loaded {
        return Ok(TerrainApplyOutcome {
            remaining: Some(area.data.to_vec()),
            loaded: false,
        });
    }
    // Most applications complete. Allocate a mask only for the first actual
    // physical conflict rather than zeroing/scanning 16K cells on every call.
    let mut remaining: Option<Vec<bool>> = None;
    area.visit_required(|x, y, material, flags| {
        let existing = grid.read(x, y)?;
        let mut applied = existing;
        if existing.is_some_and(|cell| cell.kind == ntypes::CellType::Solid) {
            if TerrainCell::matches(existing, material, flags) {
                return Ok(());
            }
            // Box2d occupancy can be ordinary and temporary. Leave the
            // physical cell alone, retain its target, and acknowledge only
            // the other cells actually resolved in this call.
            remaining.get_or_insert_with(|| vec![false; 128 * 128])[area.offset(x, y)] = true;
            return Ok(());
        }
        let same_material = existing.is_some_and(|cell| cell.material == material);
        if !same_material && !TerrainCell::matches(existing, material, flags) {
            if existing.is_some() {
                grid.remove(x, y)?;
                if grid.read(x, y)?.is_some() {
                    eyre::bail!("Terrain removal left an occupied slot at {x},{y}");
                }
                applied = None;
            }
            if material != 0 {
                grid.construct(x, y, material, flags)?;
                applied = grid.read(x, y)?;
            }
        }
        if applied.is_some_and(|cell| cell.kind == ntypes::CellType::Liquid && cell.flags != flags)
        {
            grid.set_liquid_flags(x, y, flags)?;
        }
        Ok(())
    })?;
    // A later constructor/removal may affect an earlier neighbouring cell.
    // Establish the boundary only after every required write has completed.
    if !grid.loaded(area.x, area.y)? || !grid.loaded(area.x + 127, area.y + 127)? {
        eyre::bail!("Terrain chunk unloaded during application");
    }
    area.visit_required(|x, y, material, flags| {
        if remaining.as_ref().is_some_and(|mask| mask[area.offset(x, y)]) {
            return Ok(());
        }
        if !TerrainCell::matches(grid.read(x, y)?, material, flags) {
            eyre::bail!("Terrain final readback differs at {x},{y}");
        }
        Ok(())
    })?;
    Ok(TerrainApplyOutcome {
        remaining: remaining.as_ref().and_then(|mask| area.remaining_blob(mask)),
        loaded: true,
    })
}

#[cfg(target_arch = "x86")]
struct LiveTerrainGrid<'a> {
    state: &'a ParticleWorldState,
    fns: &'a WorldFns,
}

#[cfg(target_arch = "x86")]
impl LiveTerrainGrid<'_> {
    fn slot(&self, x: i32, y: i32) -> eyre::Result<*mut *mut ntypes::Cell> {
        // Re-read the LIVE chunk table for every slot, including readback.
        // No cached load result or cell pointer can hide unload during writes.
        // Direct requires both the byte-for-byte function guard and the live
        // area cross-check below; hooked/unknown functions keep the game path.
        let slot = if self.fns.cell_access == CellAccess::Direct {
            let cells = unsafe { chunk_cells(self.state.chunk_map_ptr, x, y) };
            if cells.is_null() {
                eyre::bail!("Terrain chunk unloaded during application at {x},{y}");
            }
            unsafe { cell_slot(cells, x, y) }
        } else {
            if !unsafe { (self.fns.chunk_loaded)(self.state.chunk_map_ptr, x, y) } {
                eyre::bail!("Terrain chunk unloaded during application at {x},{y}");
            }
            unsafe { (self.fns.get_cell)(self.state.chunk_map_ptr, x, y) }
        };
        if slot.is_null() {
            eyre::bail!("Terrain get_cell returned a null slot at {x},{y}");
        }
        Ok(slot)
    }
}

#[cfg(target_arch = "x86")]
impl TerrainGrid for LiveTerrainGrid<'_> {
    fn loaded(&mut self, x: i32, y: i32) -> eyre::Result<bool> {
        Ok(unsafe { (self.fns.chunk_loaded)(self.state.chunk_map_ptr, x, y) })
    }

    fn read(&mut self, x: i32, y: i32) -> eyre::Result<Option<TerrainCell>> {
        let pointer = unsafe { *self.slot(x, y)? };
        let Some(cell) = (unsafe { pointer.as_ref() }) else {
            return Ok(None);
        };
        let kind = ParticleWorldState::cell_type_of(cell)
            .ok_or_else(|| eyre::eyre!("Terrain cell has no material at {x},{y}"))?;
        let flags = if kind == ntypes::CellType::Liquid {
            u8::from(unsafe { (*pointer.cast::<ntypes::LiquidCell>()).is_static })
        } else {
            0
        };
        Ok(Some(TerrainCell {
            material: self.state.material_id_of(cell),
            kind,
            flags,
        }))
    }

    fn remove(&mut self, x: i32, y: i32) -> eyre::Result<()> {
        let pointer = unsafe { *self.slot(x, y)? };
        if pointer.is_null() {
            eyre::bail!("Terrain cell disappeared before removal at {x},{y}");
        }
        unsafe { (self.fns.remove_cell)(self.state._world_ptr, pointer.cast(), x, y, false) };
        Ok(())
    }

    fn construct(&mut self, x: i32, y: i32, material: i16, flags: u8) -> eyre::Result<()> {
        let pointer = unsafe {
            (self.fns.construct_cell)(
                self.state._world_ptr,
                x,
                y,
                self.state.material_ptr(material),
                std::ptr::null_mut(),
            )
        };
        if pointer.is_null() {
            eyre::bail!("Terrain constructor returned no cell at {x},{y}");
        }
        if ParticleWorldState::cell_type_of(unsafe { &*pointer }) == Some(ntypes::CellType::Liquid)
        {
            unsafe {
                (*pointer.cast::<ntypes::LiquidCell>()).is_static = flags == LIQUID_FLAG_STATIC
            };
        }
        unsafe { *self.slot(x, y)? = pointer };
        Ok(())
    }

    fn set_liquid_flags(&mut self, x: i32, y: i32, flags: u8) -> eyre::Result<()> {
        let pointer = unsafe { *self.slot(x, y)? };
        if pointer.is_null() {
            eyre::bail!("Terrain liquid disappeared before flag update at {x},{y}");
        }
        unsafe { (*pointer.cast::<ntypes::LiquidCell>()).is_static = flags == LIQUID_FLAG_STATIC };
        Ok(())
    }
}

/// Compare the direct ChunkMap walk with the game's chunk_loaded and get_cell
/// for every cell of an area. Returns how many loaded cells were compared, or
/// `None` on the first disagreement.
///
/// # Safety
///
/// Same as `decode_area`.
#[cfg(target_arch = "x86")]
unsafe fn cross_check_cell_access(
    chunk_map: *mut c_void,
    fns: &WorldFns,
    (x0, y0): (i32, i32),
    (x1, y1): (i32, i32),
) -> Option<usize> {
    let mut compared = 0;
    // Inclusive bounds allow a valid aligned chunk ending at i32::MAX.
    for y in y0..=y1 {
        for x in x0..=x1 {
            // SAFETY: chunk_map is the live ChunkMap; get_cell is only called
            // once the game itself says the chunk is loaded.
            unsafe {
                let cells = chunk_cells(chunk_map, x, y);
                if (fns.chunk_loaded)(chunk_map, x, y) == cells.is_null() {
                    return None;
                }
                if cells.is_null() {
                    continue;
                }
                if (fns.get_cell)(chunk_map, x, y) != cell_slot(cells, x, y) {
                    return None;
                }
            }
            compared += 1;
        }
    }
    Some(compared)
}

impl ParticleWorldState {
    /// Material id for a cell, by offset into the material list. Mirrors
    /// `world_ffi.get_material_id`.
    fn material_id_of(&self, cell: &ntypes::Cell) -> i16 {
        let mat_ptr = cell.material_ptr();
        // Not `offset_from`: these are two independently obtained raw pointers,
        // so they are not provably part of one allocation.
        let delta = (mat_ptr as usize).wrapping_sub(self.material_list_ptr as usize);
        (delta / CELLDATA_SIZE as usize) as i16
    }

    /// Mirrors `world_ffi.get_material_ptr`.
    fn material_ptr(&self, id: i16) -> *const ntypes::CellData {
        self.material_list_ptr
            .wrapping_byte_offset(CELLDATA_SIZE * id as isize)
            .cast()
    }

    fn cell_type_of(cell: &ntypes::Cell) -> Option<ntypes::CellType> {
        // SAFETY: material_ptr on a live cell points into the game's material
        // list; `as_ref` handles the null case.
        unsafe { Some(cell.material_ptr().as_ref()?.cell_type) }
    }

    /// Strict local-protocol application boundary. No remaining means complete;
    /// remaining contains only unresolved requirements. Unloaded returns the exact input
    /// before writes. Physical solid conflicts retain their individual targets
    /// without rewriting completed cells on later attempts. Unexpected runtime
    /// failures remain errors and must not be acknowledged or blindly retried.
    ///
    /// # Safety
    ///
    /// As for `decode_area`: the game main thread and live resolved functions.
    #[cfg(target_arch = "x86")]
    pub(crate) unsafe fn apply_terrain(
        &mut self,
        data: &[u8],
        fns: &mut WorldFns,
    ) -> eyre::Result<TerrainApplyOutcome> {
        let area = TerrainArea::parse(data, fns.last_material_id)?;
        if self.material_list_ptr.is_null() {
            eyre::bail!("Terrain material list is not initialized");
        }
        // Validate target material types before mutation too. The capture
        // projection cannot represent box2d/None as nonzero synchronized cells.
        for run in area.runs.chunks_exact(5) {
            let material = i16::from_le_bytes([run[2], run[3]]);
            if material <= 0 {
                continue;
            }
            let target = unsafe { self.material_ptr(material).as_ref() }
                .ok_or_else(|| eyre::eyre!("Terrain material pointer is null"))?;
            if !matches!(
                target.cell_type,
                ntypes::CellType::Liquid | ntypes::CellType::Gas | ntypes::CellType::Fire
            ) {
                eyre::bail!("Terrain material {material} is not a synchronized cell type");
            }
            if target.cell_type != ntypes::CellType::Liquid && run[4] != 0 {
                eyre::bail!("Terrain non-liquid material {material} has liquid flags");
            }
        }
        if fns.cell_access == CellAccess::DirectUnverified {
            // Before any mutation, compare the code-guarded direct walk with
            // the actual game functions on this entire validated area.
            match unsafe {
                cross_check_cell_access(
                    self.chunk_map_ptr,
                    fns,
                    (area.x, area.y),
                    (area.x + 127, area.y + 127),
                )
            } {
                None => {
                    fns.cell_access = CellAccess::Game;
                    println!("Strict terrain cell access: game (live cross-check mismatch)");
                }
                Some(0) => {} // Unloaded: defer and compare a later live area.
                Some(cells) => {
                    fns.cell_access = CellAccess::Direct;
                    println!("Strict terrain cell access: direct ({cells} cells cross-checked)");
                }
            }
        }
        apply_terrain_grid(&area, &mut LiveTerrainGrid { state: self, fns })
    }

    #[cfg(not(target_arch = "x86"))]
    pub(crate) unsafe fn apply_terrain(
        &mut self,
        data: &[u8],
        fns: &WorldFns,
    ) -> eyre::Result<TerrainApplyOutcome> {
        let _ = TerrainArea::parse(data, fns.last_material_id)?;
        eyre::bail!("Terrain application is only implemented for 32-bit x86 (Noita's target)")
    }

    /// Apply an encoded area to the live world.
    ///
    /// `data` must be the full `EncodedArea` blob as produced by `encode_area`:
    /// a packed [`EncodedAreaHeader`] followed by `pixel_run_count` runs.
    ///
    /// # Safety
    ///
    /// Must be called on the game's main thread, from inside a world update,
    /// with `fns` resolved from the running process. This mutates the live
    /// pixel grid.
    #[cfg(target_arch = "x86")]
    pub(crate) unsafe fn decode_area(
        &mut self,
        data: &[u8],
        fns: &mut WorldFns,
    ) -> eyre::Result<usize> {
        let (header, runs, run_count) = parse_encoded_area(data)?;

        let width = i32::from(header.width) + 1;
        let height = i32::from(header.height) + 1;
        let top_left_x = header.x;
        let top_left_y = header.y;
        let bottom_right_x = top_left_x + width;
        let bottom_right_y = top_left_y + height;

        let grid_world = self._world_ptr;
        let chunk_map = self.chunk_map_ptr;

        if fns.cell_access == CellAccess::DirectUnverified {
            // SAFETY: as for this function.
            let checked = unsafe {
                cross_check_cell_access(
                    chunk_map,
                    fns,
                    (top_left_x, top_left_y),
                    (bottom_right_x - 1, bottom_right_y - 1),
                )
            };
            match checked {
                None => fns.cell_access = CellAccess::Game,
                // Nothing loaded here to compare against; try the next area.
                Some(0) => {}
                Some(_) => fns.cell_access = CellAccess::Direct,
            }
        }
        let direct = fns.cell_access != CellAccess::Game;

        let mut run_ix = 0usize;
        // SAFETY: parse_encoded_area verified there are at least run_count runs.
        let mut current = unsafe { runs.read_unaligned() };
        let mut new_material = current.material as i16;
        let mut flags = current.flags;
        let mut left = i32::from(current.length) + 1;
        let mut written = 0usize;
        let mut loaded_cache = ChunkCache::new(false);
        let mut cells_cache = ChunkCache::new(std::ptr::null_mut());

        for y in top_left_y..bottom_right_y {
            for x in top_left_x..bottom_right_x {
                // A labelled block replaces the Lua's `goto next_pixel`: the
                // run bookkeeping below must run for every pixel regardless of
                // whether the pixel itself was skipped.
                'pixel: {
                    // -1 means "sender had no data here", not a material.
                    if new_material == -1 {
                        break 'pixel;
                    }
                    let ppixel = if direct {
                        // SAFETY: chunk_map is the game's ChunkMap for this
                        // world, and cells is non-null for the chunk of (x, y).
                        let cells =
                            cells_cache.get(x, y, || unsafe { chunk_cells(chunk_map, x, y) });
                        if cells.is_null() {
                            break 'pixel;
                        }
                        unsafe { cell_slot(cells, x, y) }
                    } else {
                        // SAFETY: chunk_map is the game's ChunkMap for this
                        // world, and get_cell only runs for a loaded chunk.
                        if !loaded_cache
                            .get(x, y, || unsafe { (fns.chunk_loaded)(chunk_map, x, y) })
                        {
                            break 'pixel;
                        }
                        unsafe { (fns.get_cell)(chunk_map, x, y) }
                    };
                    if ppixel.is_null() {
                        break 'pixel;
                    }

                    let mut current_material = 0i16;
                    // SAFETY: ppixel points at a Cell* slot in the grid.
                    let existing = unsafe { *ppixel };
                    if let Some(cell) = unsafe { existing.as_ref() } {
                        // Nobody knows how box2d pixels work; leave them alone.
                        if Self::cell_type_of(cell) == Some(ntypes::CellType::Solid) {
                            break 'pixel;
                        }
                        current_material = self.material_id_of(cell);
                        if new_material != current_material {
                            // SAFETY: `existing` is a live cell at (x, y).
                            unsafe {
                                (fns.remove_cell)(grid_world, existing.cast(), x, y, false);
                            }
                        }
                    }

                    if current_material == new_material || new_material == 0 {
                        break 'pixel;
                    }
                    if new_material > fns.last_material_id || new_material < 0 {
                        break 'pixel;
                    }
                    let mat_ptr = self.material_ptr(new_material);
                    if mat_ptr.is_null() {
                        break 'pixel;
                    }
                    // SAFETY: mat_ptr indexes the game's material list.
                    let pixel = unsafe {
                        (fns.construct_cell)(grid_world, x, y, mat_ptr, std::ptr::null_mut())
                    };
                    if pixel.is_null() {
                        // Happens when the material texture is transparent at
                        // this coordinate. Matches the Lua path: skip.
                        break 'pixel;
                    }

                    // SAFETY: just constructed by the game.
                    if let Some(cell) = unsafe { pixel.as_ref() }
                        && Self::cell_type_of(cell) == Some(ntypes::CellType::Liquid)
                    {
                        // LiquidCell begins with a Cell, so this reinterpret is
                        // the documented prefix relationship, not a punning.
                        let liquid = pixel.cast::<ntypes::LiquidCell>();
                        // SAFETY: cell_type says this really is a LiquidCell.
                        unsafe {
                            (*liquid).is_static =
                                (flags & LIQUID_FLAG_STATIC) == LIQUID_FLAG_STATIC;
                        }
                    }

                    // SAFETY: hand ownership of the new cell to the grid.
                    unsafe { *ppixel = pixel };
                    written += 1;
                }

                left -= 1;
                if left <= 0 {
                    run_ix += 1;
                    if run_ix >= run_count {
                        // Ran out of runs. The Lua asserted that this coincides
                        // with the last pixel; a truncated or over-long buffer
                        // from the network should not be fatal, so just stop.
                        return Ok(written);
                    }
                    // SAFETY: run_ix < run_count, checked directly above.
                    current = unsafe { runs.add(run_ix).read_unaligned() };
                    new_material = current.material as i16;
                    flags = current.flags;
                    left = i32::from(current.length) + 1;
                }
            }
        }

        Ok(written)
    }

    #[cfg(not(target_arch = "x86"))]
    pub(crate) unsafe fn decode_area(
        &mut self,
        _data: &[u8],
        _fns: &mut WorldFns,
    ) -> eyre::Result<usize> {
        eyre::bail!("Native world decode is only implemented for 32-bit x86 (Noita's target)")
    }
}

/// Split an `EncodedArea` blob into its header and run array, validating that
/// the buffer actually contains the runs the header claims.
///
/// The blob arrives over the network, so the run count cannot be trusted.
fn parse_encoded_area(
    data: &[u8],
) -> eyre::Result<(EncodedAreaHeader, *const NoitaPixelRun, usize)> {
    let header_size = size_of::<EncodedAreaHeader>();
    if data.len() < header_size {
        eyre::bail!(
            "Encoded area is {} bytes, shorter than its {header_size} byte header",
            data.len()
        );
    }
    // SAFETY: length checked above; EncodedAreaHeader is packed and made
    // entirely of integers, so any bit pattern is valid.
    let header = unsafe { data.as_ptr().cast::<EncodedAreaHeader>().read_unaligned() };
    let run_count = usize::from(header.pixel_run_count);
    if run_count == 0 {
        eyre::bail!("Encoded area declares zero pixel runs");
    }
    let needed = header_size + run_count * size_of::<NoitaPixelRun>();
    if data.len() < needed {
        eyre::bail!(
            "Encoded area declares {run_count} runs ({needed} bytes) but is only {} bytes",
            data.len()
        );
    }
    let runs = unsafe { data.as_ptr().add(header_size) }.cast::<NoitaPixelRun>();
    Ok((header, runs, run_count))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blob(header: EncodedAreaHeader, runs: &[NoitaPixelRun]) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(&header.x.to_ne_bytes());
        out.extend_from_slice(&header.y.to_ne_bytes());
        out.push(header.width);
        out.push(header.height);
        out.extend_from_slice(&header.pixel_run_count.to_ne_bytes());
        for run in runs {
            out.extend_from_slice(&run.length.to_ne_bytes());
            out.extend_from_slice(&run.material.to_ne_bytes());
            out.push(run.flags);
        }
        out
    }

    fn header(run_count: u16) -> EncodedAreaHeader {
        EncodedAreaHeader {
            x: 0,
            y: 0,
            width: 127,
            height: 127,
            pixel_run_count: run_count,
        }
    }

    #[test]
    fn parses_a_well_formed_area() {
        let runs = [
            NoitaPixelRun {
                length: 3,
                material: 5,
                flags: 1,
            },
            NoitaPixelRun {
                length: 0,
                material: 7,
                flags: 0,
            },
        ];
        let data = blob(header(2), &runs);
        let (h, ptr, count) = parse_encoded_area(&data).expect("should parse");
        assert_eq!(count, 2);
        assert_eq!({ h.width }, 127);
        let first = unsafe { ptr.read_unaligned() };
        assert_eq!({ first.material }, 5);
        let second = unsafe { ptr.add(1).read_unaligned() };
        assert_eq!({ second.material }, 7);
    }

    #[test]
    fn rejects_a_truncated_header() {
        assert!(parse_encoded_area(&[0u8; 4]).is_err());
    }

    #[test]
    fn rejects_a_run_count_the_buffer_cannot_back() {
        // Declares 500 runs but supplies one: reading them would run off the
        // end of the allocation.
        let data = blob(
            header(500),
            &[NoitaPixelRun {
                length: 1,
                material: 1,
                flags: 0,
            }],
        );
        assert!(parse_encoded_area(&data).is_err());
    }

    #[test]
    fn rejects_zero_runs() {
        let data = blob(header(0), &[]);
        assert!(parse_encoded_area(&data).is_err());
    }

    fn terrain_blob(runs: &[(u16, u16, u8)]) -> Vec<u8> {
        blob(
            header(runs.len() as u16),
            &runs
                .iter()
                .map(|&(length, material, flags)| NoitaPixelRun {
                    length,
                    material,
                    flags,
                })
                .collect::<Vec<_>>(),
        )
    }

    fn liquid(material: i16, flags: u8) -> TerrainCell {
        TerrainCell {
            material,
            kind: ntypes::CellType::Liquid,
            flags,
        }
    }

    struct TestTerrainGrid {
        cells: Vec<Option<TerrainCell>>,
        is_loaded: bool,
        loaded_calls: usize,
        unload_after_preflight: bool,
        remove_fails: bool,
        construct_fails: bool,
        damage_previous: bool,
        removals: usize,
        constructions: usize,
        flag_changes: usize,
    }

    impl Default for TestTerrainGrid {
        fn default() -> Self {
            Self {
                cells: vec![None; 128 * 128],
                is_loaded: true,
                loaded_calls: 0,
                unload_after_preflight: false,
                remove_fails: false,
                construct_fails: false,
                damage_previous: false,
                removals: 0,
                constructions: 0,
                flag_changes: 0,
            }
        }
    }

    impl TerrainGrid for TestTerrainGrid {
        fn loaded(&mut self, _: i32, _: i32) -> eyre::Result<bool> {
            self.loaded_calls += 1;
            Ok(self.is_loaded && !(self.unload_after_preflight && self.loaded_calls > 2))
        }
        fn read(&mut self, x: i32, y: i32) -> eyre::Result<Option<TerrainCell>> {
            Ok(self.cells[x as usize + y as usize * 128])
        }
        fn remove(&mut self, x: i32, y: i32) -> eyre::Result<()> {
            self.removals += 1;
            if !self.remove_fails {
                self.cells[x as usize + y as usize * 128] = None;
            }
            Ok(())
        }
        fn construct(&mut self, x: i32, y: i32, material: i16, flags: u8) -> eyre::Result<()> {
            self.constructions += 1;
            if !self.construct_fails {
                self.cells[x as usize + y as usize * 128] = Some(liquid(material, flags));
            }
            if self.damage_previous && self.constructions == 2 {
                self.cells[0] = None;
            }
            Ok(())
        }
        fn set_liquid_flags(&mut self, x: i32, y: i32, flags: u8) -> eyre::Result<()> {
            self.flag_changes += 1;
            self.cells[x as usize + y as usize * 128]
                .as_mut()
                .unwrap()
                .flags = flags;
            Ok(())
        }
    }

    fn apply_test(data: &[u8], grid: &mut TestTerrainGrid) -> eyre::Result<TerrainApplyOutcome> {
        let area = TerrainArea::parse(data, 10)?;
        apply_terrain_grid(&area, grid)
    }

    #[test]
    fn terrain_validator_requires_exact_chunk_and_run_extent() {
        let valid = terrain_blob(&[(16383, 0, 0)]);
        assert!(TerrainArea::parse(&valid, 10).is_ok());
        for invalid in [
            terrain_blob(&[(16382, 0, 0)]),
            terrain_blob(&[(16384, 0, 0)]),
            terrain_blob(&[(16383, 0, 0), (0, 0, 0)]),
        ] {
            assert!(TerrainArea::parse(&invalid, 10).is_err());
        }
        let mut trailing = valid.clone();
        trailing.push(0);
        assert!(TerrainArea::parse(&trailing, 10).is_err());
        let mut unaligned = valid.clone();
        unaligned[0] = 1;
        assert!(TerrainArea::parse(&unaligned, 10).is_err());
        let mut dimensions = valid.clone();
        dimensions[8] = 126;
        assert!(TerrainArea::parse(&dimensions, 10).is_err());
        let mut negative_aligned = valid.clone();
        negative_aligned[..4].copy_from_slice(&(-128i32).to_le_bytes());
        assert!(TerrainArea::parse(&negative_aligned, 10).is_ok());
    }

    #[test]
    fn terrain_validator_rejects_invalid_late_material_and_flags_before_writes() {
        for invalid in [
            terrain_blob(&[(0, 6, 0), (16382, 11, 0)]),
            terrain_blob(&[(16383, (-2i16) as u16, 0)]),
            terrain_blob(&[(16383, 6, 2)]),
            terrain_blob(&[(16383, 0, 1)]),
        ] {
            let mut grid = TestTerrainGrid::default();
            assert!(apply_test(&invalid, &mut grid).is_err());
            assert_eq!(grid.loaded_calls + grid.removals + grid.constructions, 0);
        }
    }

    #[test]
    fn terrain_unloaded_air_defers_without_any_mutation() {
        let mut grid = TestTerrainGrid {
            is_loaded: false,
            ..Default::default()
        };
        grid.cells[0] = Some(liquid(6, 0));
        let original = terrain_blob(&[(16383, 0, 0)]);
        let outcome = apply_test(&original, &mut grid).unwrap();
        assert!(
            !outcome.loaded,
            "unloaded deferral must suppress the entire sample"
        );
        assert_eq!(outcome.remaining, Some(original));
        assert_eq!(grid.removals + grid.constructions + grid.flag_changes, 0);
        assert_eq!(grid.cells[0].unwrap().material, 6);
    }

    #[test]
    fn terrain_air_removal_and_equal_air_complete_without_construction() {
        let mut grid = TestTerrainGrid::default();
        grid.cells[0] = Some(liquid(6, 0));
        let outcome = apply_test(&terrain_blob(&[(16383, 0, 0)]), &mut grid).unwrap();
        assert!(outcome.loaded && outcome.remaining.is_none());
        assert!(grid.cells.iter().all(Option::is_none));
        assert_eq!(grid.removals, 1);
        assert_eq!(grid.constructions, 0);
        assert!(
            apply_test(&terrain_blob(&[(16383, 0, 0)]), &mut grid)
                .unwrap()
                .remaining
                .is_none()
        );
        assert_eq!(grid.removals, 1);
    }

    #[test]
    fn terrain_unknown_cells_are_preserved_and_do_not_require_writes() {
        let mut grid = TestTerrainGrid::default();
        grid.cells[1] = Some(liquid(6, 1));
        assert!(
            apply_test(&terrain_blob(&[(0, 0, 0), (16382, u16::MAX, 1)]), &mut grid)
                .unwrap()
                .remaining
                .is_none()
        );
        assert_eq!(grid.cells[1].unwrap().material, 6);
        assert_eq!(grid.cells[1].unwrap().flags, 1);
        assert_eq!(grid.removals + grid.constructions + grid.flag_changes, 0);
    }

    #[test]
    fn terrain_unknown_unloaded_is_vacuously_complete_without_writes() {
        let mut grid = TestTerrainGrid {
            is_loaded: false,
            ..Default::default()
        };
        grid.cells[0] = Some(liquid(6, 1));
        let outcome = apply_test(&terrain_blob(&[(16383, u16::MAX, 1)]), &mut grid).unwrap();
        assert!(!outcome.loaded);
        assert!(
            outcome.remaining.is_none(),
            "no requirement may remain queued"
        );
        assert_eq!(grid.removals + grid.constructions + grid.flag_changes, 0);
        assert_eq!(
            (
                grid.cells[0].unwrap().material,
                grid.cells[0].unwrap().flags
            ),
            (6, 1)
        );
    }

    #[test]
    fn terrain_existing_liquid_flags_are_applied_and_verified() {
        let mut grid = TestTerrainGrid::default();
        grid.cells[0] = Some(liquid(6, 0));
        assert!(
            apply_test(&terrain_blob(&[(0, 6, 1), (16382, u16::MAX, 1)]), &mut grid)
                .unwrap()
                .remaining
                .is_none()
        );
        assert_eq!(grid.cells[0].unwrap().flags, 1);
        assert_eq!(grid.constructions + grid.removals, 0);
        assert_eq!(grid.flag_changes, 1);
    }

    #[test]
    fn terrain_failed_removal_and_construction_are_errors_not_deferrals() {
        let mut removal = TestTerrainGrid {
            remove_fails: true,
            ..Default::default()
        };
        removal.cells[0] = Some(liquid(5, 0));
        assert!(
            apply_test(
                &terrain_blob(&[(0, 0, 0), (16382, u16::MAX, 1)]),
                &mut removal
            )
            .is_err()
        );
        let mut constructor = TestTerrainGrid {
            construct_fails: true,
            ..Default::default()
        };
        constructor.cells[0] = Some(liquid(5, 0));
        assert!(
            apply_test(
                &terrain_blob(&[(0, 6, 0), (16382, u16::MAX, 1)]),
                &mut constructor
            )
            .is_err()
        );
        assert_eq!(constructor.removals, 1);
        assert!(
            constructor.cells[0].is_none(),
            "partial work stays a fault, not an ACK or retry"
        );
    }

    #[test]
    fn terrain_solid_projection_accepts_air_but_retains_nonzero_without_removal() {
        let mut grid = TestTerrainGrid::default();
        grid.cells[0] = Some(TerrainCell {
            material: 9,
            kind: ntypes::CellType::Solid,
            flags: 0,
        });
        assert!(
            apply_test(&terrain_blob(&[(0, 0, 0), (16382, u16::MAX, 1)]), &mut grid)
                .unwrap()
                .remaining
                .is_none()
        );
        let required = terrain_blob(&[(0, 6, 0), (16382, u16::MAX, 1)]);
        let remaining = apply_test(&required, &mut grid)
            .unwrap()
            .remaining
            .expect("solid target stays pending");
        assert_eq!(remaining, required);
        assert_eq!(grid.removals + grid.constructions, 0);
    }

    #[test]
    fn terrain_partial_completion_never_reasserts_completed_cells_over_later_edits() {
        let mut grid = TestTerrainGrid::default();
        grid.cells[0] = Some(liquid(5, 0));
        grid.cells[1] = Some(TerrainCell {
            material: 9,
            kind: ntypes::CellType::Solid,
            flags: 0,
        });
        let required = terrain_blob(&[(1, 6, 0), (16381, u16::MAX, 1)]);
        let outcome = apply_test(&required, &mut grid).unwrap();
        assert!(
            outcome.loaded,
            "a physical solid is distinct from unloaded terrain"
        );
        let remaining = outcome.remaining.expect("one cell is blocked");
        assert_eq!(
            remaining,
            terrain_blob(&[(0, u16::MAX, 1), (0, 6, 0), (16381, u16::MAX, 1)])
        );
        assert_eq!(grid.cells[0].unwrap().material, 6);
        assert_eq!((grid.removals, grid.constructions), (1, 1));

        // This is a legitimate edit after the first cell's application. Only
        // the remaining required mask may be attempted again.
        grid.cells[0] = Some(liquid(7, 0));
        for _ in 0..3 {
            assert_eq!(
                apply_test(&remaining, &mut grid)
                    .unwrap()
                    .remaining
                    .as_ref(),
                Some(&remaining)
            );
            assert_eq!((grid.removals, grid.constructions), (1, 1));
            assert_eq!(grid.cells[0].unwrap().material, 7);
        }
        grid.cells[1] = None; // The physical box2d body has now vacated.
        assert!(
            apply_test(&remaining, &mut grid)
                .unwrap()
                .remaining
                .is_none()
        );
        assert_eq!(grid.cells[0].unwrap().material, 7);
        assert_eq!(grid.cells[1].unwrap().material, 6);
        assert_eq!((grid.removals, grid.constructions), (1, 2));
    }

    #[test]
    fn terrain_remaining_mask_is_bounded_valid_and_never_contains_completed_targets() {
        let mut grid = TestTerrainGrid::default();
        for index in (0..128 * 128).step_by(2) {
            grid.cells[index] = Some(TerrainCell {
                material: 9,
                kind: ntypes::CellType::Solid,
                flags: 0,
            });
        }
        let remaining = apply_test(&terrain_blob(&[(16383, 6, 1)]), &mut grid)
            .unwrap()
            .remaining
            .unwrap();
        assert_eq!(
            remaining.len(),
            12 + 128 * 128 * 5,
            "alternating unresolved/unknown is the exact worst case"
        );
        let validated = TerrainArea::parse(&remaining, 10).unwrap();
        let mut required_count = 0;
        validated
            .visit_required(|x, y, material, flags| {
                assert_eq!((x + y * 128) % 2, 0);
                assert_eq!((material, flags), (6, 1));
                required_count += 1;
                Ok(())
            })
            .unwrap();
        assert_eq!(required_count, 128 * 128 / 2);
    }

    #[test]
    fn terrain_final_readback_catches_later_mutation_of_an_earlier_cell() {
        let mut grid = TestTerrainGrid {
            damage_previous: true,
            ..Default::default()
        };
        assert!(apply_test(&terrain_blob(&[(1, 6, 0), (16381, u16::MAX, 1)]), &mut grid).is_err());
        assert_eq!(grid.constructions, 2);
    }

    #[test]
    fn terrain_unload_after_mutation_is_a_fault_not_a_retryable_deferral() {
        let mut grid = TestTerrainGrid {
            unload_after_preflight: true,
            ..Default::default()
        };
        assert!(apply_test(&terrain_blob(&[(0, 6, 0), (16382, u16::MAX, 1)]), &mut grid).is_err());
        assert_eq!(grid.constructions, 1);
    }

    /// Counts the lookups `ChunkCache` makes over an area, walked in the same
    /// row-major order as `decode_area`.
    fn chunk_loaded_calls(x0: i32, y0: i32, w: i32, h: i32) -> usize {
        let mut cache = ChunkCache::new(false);
        let mut calls = 0;
        for y in y0..y0 + h {
            for x in x0..x0 + w {
                cache.get(x, y, || {
                    calls += 1;
                    true
                });
            }
        }
        calls
    }

    #[test]
    fn chunk_loaded_asked_once_for_an_aligned_area() {
        assert_eq!(chunk_loaded_calls(128, 256, 128, 128), 1);
        assert_eq!(chunk_loaded_calls(-128, -512, 128, 128), 1);
    }

    #[test]
    fn chunk_loaded_asked_again_across_a_chunk_boundary() {
        // Straddles x = 512, so every row starts in one chunk and ends in the
        // next.
        assert_eq!(chunk_loaded_calls(448, 0, 128, 128), 256);
        // Straddles y = 0: rows below it are a different chunk.
        assert_eq!(chunk_loaded_calls(0, -64, 128, 128), 2);
    }

    #[test]
    fn chunk_loaded_answer_follows_the_chunk() {
        let mut cache = ChunkCache::new(false);
        assert!(cache.get(0, 0, || true));
        // Same chunk: the cached answer stands, the query is not run.
        assert!(cache.get(511, 511, || unreachable!()));
        assert!(!cache.get(512, 0, || false));
        assert!(!cache.get(-1, 0, || false));
    }

    #[test]
    fn known_game_code_matches_only_exactly() {
        for code in GET_CELL_CODE {
            // Followed by whatever comes next in .text.
            let mut padded = code.to_vec();
            padded.extend_from_slice(&[0xcc; 32]);
            assert!(code_matches(&padded, &GET_CELL_CODE));
            // A hook or a different build changes the bytes.
            let mut patched = padded.clone();
            patched[0] = 0xe9;
            assert!(!code_matches(&patched, &GET_CELL_CODE));
            let mut tweaked = padded;
            tweaked[code.len() - 4] ^= 1;
            assert!(!code_matches(&tweaked, &GET_CELL_CODE));
        }
        assert!(code_matches(CHUNK_LOADED_CODE, &[CHUNK_LOADED_CODE]));
        assert!(!code_matches(&CHUNK_LOADED_CODE[1..], &[CHUNK_LOADED_CODE]));
        assert!(!code_matches(GET_CELL_CODE[0], &[CHUNK_LOADED_CODE]));
    }
}
