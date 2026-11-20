"""Pure native-row relocation checks. Never loads or patches a game process."""
from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import unittest

from lupa.luajit21 import LuaRuntime, LuaError

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'quant.ew/files/system/player_ping/native_speed_row.lua'
sys.path.insert(0, str(ROOT / 'target/hover-tools'))
try:
    import capstone
    import pefile
except ImportError:
    capstone = pefile = None

BASE, START, END = 0x400000, 0x76AA84, 0x76AF45
EXPECTED_SHA = '204600f4a992309079dfc437055cb7d0b8e2aaa69fc5f6fbce4862201bd34400'
FORMAT_START, FORMAT_END = 0x76AD05, 0x76AD33
POINTERS = {
    'icon_prefix': 0x31000100, 'icon_name': 0x31000200,
    'label': 0x31000300, 'help': 0x31000400, 'empty': 0x31000500,
}
POINTER_PATCHES = {
    0x76AB28: 'icon_prefix', 0x76AB38: 'icon_name', 0x76AC13: 'label',
    0x76ACD8: 'empty', 0x76AE26: 'help', 0x76AECD: 'help',
}
WIDGET_PATCHES = {0x76AC9B: (0x4CF5, 0x7ED2), 0x76AD96: (0x4CF7, 0x7ED3)}


def word(value):
    return struct.pack('<I', value & 0xFFFFFFFF)


class PureBuilderTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
        self.api = self.lua.execute(MODULE.read_bytes())

    def ptrs(self, values=None):
        return self.lua.table_from({k.encode(): v for k, v in (values or POINTERS).items()})

    def test_metadata_contract_and_no_native_code_blob_or_engine_dependency(self):
        self.assertEqual(self.api[b'BLOCK_RVA'], START)
        self.assertEqual(self.api[b'BLOCK_END_RVA'], END)
        self.assertEqual(self.api[b'BLOCK_LENGTH'], END - START)
        self.assertEqual(self.api[b'BLOCK_SHA256'].decode(), EXPECTED_SHA)
        self.assertEqual(self.api[b'INSERT_BYTES'], bytes.fromhex('8b855cbfffff'))
        self.assertFalse(self.api[b'HAS_4CF3_IMMEDIATE'])
        source = MODULE.read_text(encoding='utf-8')
        self.assertNotRegex(source, r'["\'][0-9a-fA-F]{128,}["\']')
        for forbidden in ('require(', 'VirtualProtect(', 'VirtualAlloc(', 'EntityLoad(',
                          'ffi.', 'io.open(', 'os.execute('):
            self.assertNotIn(forbidden, source)

    def test_invalid_input_is_rejected_before_building(self):
        build = self.api[b'build']
        with self.assertRaisesRegex(LuaError, 'invalid code address'):
            build(float('nan'), BASE, b'', self.ptrs())
        with self.assertRaisesRegex(LuaError, 'invalid image base'):
            build(0x10000000, 0xFFFFF000, b'', self.ptrs())
        with self.assertRaisesRegex(LuaError, 'incorrect block length'):
            build(0x10000000, BASE, b'', self.ptrs())
        with self.assertRaisesRegex(LuaError, 'block header mismatch'):
            build(0x10000000, BASE, b'\x90' * (END - START), self.ptrs())


class InstalledBlockTests(PureBuilderTests):
    @classmethod
    def setUpClass(cls):
        executable = Path(os.environ.get('NOITA_EXE', r'D:\SteamLibrary\steamapps\common\Noita\noita.exe'))
        if not executable.exists() or capstone is None or pefile is None:
            raise unittest.SkipTest('Installed verified PE and offline Capstone/pefile required')
        cls.pe = pefile.PE(str(executable))
        cls.source = cls.pe.get_data(START, END - START)
        if hashlib.sha256(cls.source).hexdigest() != EXPECTED_SHA:
            raise AssertionError('Installed source block differs from sealed build')
        cls.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        cls.md.detail = True
        cls.original = list(cls.md.disasm(cls.source, BASE + START))
        cls.relocations = [entry.rva - START
                           for block in cls.pe.DIRECTORY_ENTRY_BASERELOC
                           for entry in block.entries
                           if entry.type == 3 and START <= entry.rva < END]

    def build(self, code=0x10000000, image=BASE, source=None):
        return self.api[b'build'](code, image, self.source if source is None else source, self.ptrs())

    def mapping(self, code):
        mapped, size = {}, 0
        for ins in self.original:
            rva = ins.address - BASE
            if FORMAT_START < rva < FORMAT_END:
                continue
            mapped[rva] = code + size
            if rva == FORMAT_START:
                size += 22
            elif ins.group(capstone.CS_GRP_BRANCH_RELATIVE):
                size += 5 if ins.mnemonic in ('call', 'jmp') else 6
            else:
                size += ins.size
        mapped[END] = code + size
        return mapped, size

    def relocated_source(self, image):
        raw = bytearray(self.source)
        for offset in self.relocations:
            original = struct.unpack_from('<I', raw, offset)[0]
            raw[offset:offset + 4] = word(original - BASE + image)
        return bytes(raw)

    def expected(self, code, image):
        mapped, _ = self.mapping(code)
        relocated = self.relocated_source(image)
        result = []
        for ins in self.original:
            rva = ins.address - BASE
            if FORMAT_START < rva < FORMAT_END:
                continue
            address = mapped[rva]
            if rva == FORMAT_START:
                result.append(b'\x53\xe8' + word(image + 0x1CC80 - (address + 6))
                              + b'\xc6\x45\xfc\x98\x53\x8d\x8d\xfc\xec\xff\xff\xe8'
                              + word(image + 0x1CC80 - (address + 22)))
            elif rva == 0x76AAA8:
                result.append(b'\xb8' + word(1))
            elif rva in POINTER_PATCHES:
                result.append(b'\x68' + word(POINTERS[POINTER_PATCHES[rva]]))
            elif rva in WIDGET_PATCHES:
                result.append(b'\x68' + word(WIDGET_PATCHES[rva][1]))
            elif ins.group(capstone.CS_GRP_BRANCH_RELATIVE):
                destination = ins.operands[0].imm - BASE
                target = mapped[destination] if START <= destination <= END else image + destination
                if ins.mnemonic in ('call', 'jmp'):
                    opcode = b'\xe8' if ins.mnemonic == 'call' else b'\xe9'
                    result.append(opcode + word(target - (address + 5)))
                else:
                    condition = ins.bytes[0] & 15 if ins.size == 2 else ins.bytes[1] & 15
                    result.append(bytes((0x0F, 0x80 + condition)) + word(target - (address + 6)))
            else:
                offset = rva - START
                result.append(relocated[offset:offset + ins.size])
        return b''.join(result)

    def test_exact_hash_full_decode_and_all_unchanged_bytes_preserved(self):
        self.assertEqual(len(self.source), 1217)
        self.assertEqual(sum(i.size for i in self.original), len(self.source))
        self.assertEqual(len(self.original), 270)
        for image in (BASE, 0x65000000):
            for code in (0x10000000, 0xF0000000):
                cloned = self.build(code, image)
                self.assertEqual(cloned, self.expected(code, image))
                decoded = list(self.md.disasm(cloned, code))
                self.assertEqual(sum(i.size for i in decoded), len(cloned))

    def test_disk_and_already_relocated_input_produce_identical_output(self):
        image, code = 0x65000000, 0x10000000
        self.assertEqual(self.build(code, image), self.build(code, image, self.relocated_source(image)))

    def test_each_relative_destination_and_exclusive_end_label(self):
        code, image = 0x17000000, 0x65000000
        cloned = self.build(code, image)
        mapped, size = self.mapping(code)
        decoded = {i.address: i for i in self.md.disasm(cloned, code)}
        for ins in self.original:
            rva = ins.address - BASE
            if not ins.group(capstone.CS_GRP_BRANCH_RELATIVE) or rva == 0x76AAA8 or FORMAT_START <= rva < FORMAT_END:
                continue
            destination = ins.operands[0].imm - BASE
            expected = mapped[destination] if START <= destination <= END else image + destination
            new = decoded[mapped[rva]]
            self.assertEqual(new.mnemonic, ins.mnemonic)
            self.assertEqual(new.operands[0].imm & 0xFFFFFFFF, expected & 0xFFFFFFFF)
            if START <= destination <= END:
                self.assertTrue(expected in decoded or expected == code + size)
        end_branch = decoded[mapped[0x76AE8F]]
        self.assertEqual(end_branch.mnemonic, 'je')
        self.assertEqual(end_branch.operands[0].imm & 0xFFFFFFFF, code + len(cloned))
        self.assertNotEqual(end_branch.operands[0].imm & 0xFFFFFFFF, image + END)

    def test_exact_ebx_string_constructor_neutral_color_pointers_and_widget_ids(self):
        code, image = 0x10000000, 0x65000000
        cloned = self.build(code, image)
        mapped, _ = self.mapping(code)
        decoded = {i.address: i for i in self.md.disasm(cloned, code)}
        push = decoded[mapped[FORMAT_START]]
        ctor = decoded[push.address + push.size]
        self.assertEqual((push.mnemonic, push.op_str), ('push', 'ebx'))
        self.assertEqual(ctor.mnemonic, 'call')
        self.assertEqual(ctor.operands[0].imm, image + 0x1CC80)
        state = decoded[ctor.address + ctor.size]
        push2 = decoded[state.address + state.size]
        local = decoded[push2.address + push2.size]
        ctor2 = decoded[local.address + local.size]
        self.assertEqual((state.mnemonic, state.op_str), ('mov', 'byte ptr [ebp - 4], 0x98'))
        self.assertEqual((push2.mnemonic, push2.op_str), ('push', 'ebx'))
        self.assertEqual((local.mnemonic, local.op_str), ('lea', 'ecx, [ebp - 0x1304]'))
        self.assertEqual(ctor2.mnemonic, 'call')
        self.assertEqual(ctor2.operands[0].imm, image + 0x1CC80)
        neutral = decoded[mapped[0x76AAA8]]
        self.assertEqual((neutral.mnemonic, neutral.op_str), ('mov', 'eax, 1'))
        for rva, field in POINTER_PATCHES.items():
            pushed = decoded[mapped[rva]]
            self.assertEqual(pushed.mnemonic, 'push')
            self.assertEqual(pushed.operands[0].imm & 0xFFFFFFFF, POINTERS[field])
        for rva, (_, unique) in WIDGET_PATCHES.items():
            self.assertEqual(decoded[mapped[rva]].operands[0].imm, unique)
        self.assertNotIn(word(0x4CF3), self.source)
        self.assertTrue(all('ebx' not in ins.op_str for ins in self.original))

    def test_native_call_set_changes_only_requested_calls(self):
        code, image = 0x10000000, 0x65000000
        expected = Counter(ins.operands[0].imm - BASE + image for ins in self.original
                           if ins.mnemonic == 'call' and ins.address - BASE not in (0x76AAA8, 0x76AD15, 0x76AD2B))
        expected[image + 0x1CC80] += 2
        actual = Counter(ins.operands[0].imm for ins in self.md.disasm(self.build(code, image), code)
                         if ins.mnemonic == 'call')
        self.assertEqual(actual, expected)

    def test_minimal_guards_reject_corrupted_metadata_sites(self):
        corrupted = bytearray(self.source)
        corrupted[self.relocations[0]] ^= 1
        with self.assertRaisesRegex(LuaError, 'absolute relocation mismatch'):
            self.build(source=bytes(corrupted))
        corrupted = bytearray(self.source)
        corrupted[4] ^= 1  # the first E8 target is part of the sealed entry header
        with self.assertRaisesRegex(LuaError, 'block header mismatch'):
            self.build(source=bytes(corrupted))
        corrupted = bytearray(self.source)
        corrupted[0x76AE8F - START + 2] ^= 1
        with self.assertRaisesRegex(LuaError, 'source branch target mismatch'):
            self.build(source=bytes(corrupted))
        pointers = dict(POINTERS, label=0)
        with self.assertRaisesRegex(LuaError, 'invalid label pointer'):
            self.api[b'build'](0x10000000, BASE, self.source, self.ptrs(pointers))


if __name__ == '__main__':
    unittest.main()
