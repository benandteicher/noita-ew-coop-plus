"""Actual production EXE-reader helper; mocked OS failures plus a Unicode Win32 file.

No game process, native hook, injected gameplay or generated machine code runs.
The optional Windows case reads a newly created synthetic fixture file only.
"""
import os
import pathlib
import tempfile
import unittest
from lupa.luajit21 import LuaRuntime

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'quant.ew/files/system/player_ping/native_inventory_capture.lua'
SIZE = 15460864


def reader_fixture():
    lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
    lua.globals()[b'SOURCE'] = SOURCE.read_bytes()
    lua.globals()[b'EXPECTED'] = b'fixture\0' * (SIZE // 8)
    lua.globals()[b'WIDE'] = 'C:\\Private Ω 𐐀 game\\noita.exe'.encode('utf-16-le')
    lua.execute(b'''
    local backend=assert(loadstring(SOURCE))()
    local function find(fn,wanted,seen)
        seen=seen or {};if seen[fn]then return end;seen[fn]=true
        for i=1,100 do
            local name,value=debug.getupvalue(fn,i);if not name then break end
            if name==wanted then return value end
            if type(value)=='function'then local hit=find(value,wanted,seen);if hit then return hit end end
        end
    end
    READER=assert(find(backend.install,'read_actual_executable'))
    FFI=require('ffi');opens=0;closes=0;reads=0
    K={}
    function K.GetModuleFileNameW(_,out,capacity)
        assert(capacity==32768)
        if path_failure then return 0 end
        if truncated_path then return capacity end
        FFI.copy(out,WIDE,#WIDE);out[#WIDE/2]=0;return #WIDE/2
    end
    function K.CreateFileW(path,access,share,security,creation,attributes,template)
        opens=opens+1
        assert(FFI.string(path,#WIDE)==WIDE,'wide actual module path changed')
        assert(access==0x80000000 and share==1 and security==nil and creation==3 and attributes==0x80 and template==nil)
        if open_failure then return FFI.cast('void*',-1)end
        return FFI.cast('void*',41)
    end
    function K.GetFileSizeEx(_,out)
        if size_failure then return 0 end
        out[0]=wrong_size or #EXPECTED;return 1
    end
    function K.ReadFile(_,out,size,count,overlap)
        reads=reads+1;assert(size==15460864 and overlap==nil)
        if read_exception then error('injected read exception')end
        if read_failure then return 0 end
        FFI.copy(out,EXPECTED,size);count[0]=short_read and size-1 or size;return 1
    end
    function K.CloseHandle()closes=closes+1;return close_failure and 0 or 1 end
    for i=1,30 do
        local name=debug.getupvalue(READER,i);if not name then break end
        if name=='ffi'then debug.setupvalue(READER,i,FFI)end
        if name=='kernel'then debug.setupvalue(READER,i,K)end
    end
    ''')
    return lua


class UnicodeExecutableReaderTests(unittest.TestCase):
    def test_utf16_path_and_exact_bytes_reach_existing_guards(self):
        lua = reader_fixture()
        lua.execute(b'local data,reason=READER();assert(data==EXPECTED,reason);assert(opens==1 and closes==1 and reads==1)')

    def test_missing_truncated_and_unopenable_module_never_fall_back(self):
        for flag, opened in ((b'path_failure', 0), (b'truncated_path', 0), (b'open_failure', 1)):
            with self.subTest(flag=flag):
                lua = reader_fixture(); lua.globals()[flag] = True
                lua.execute(b'local data,reason=READER();assert(data==nil and reason);assert(reads==0 and closes==0)')
                self.assertEqual(lua.globals()[b'opens'], opened)

    def test_size_errors_refuse_before_bounded_allocation_and_read(self):
        for flag, value in ((b'size_failure', True), (b'wrong_size', SIZE - 1), (b'wrong_size', SIZE + 1), (b'wrong_size', 2**32 + SIZE)):
            with self.subTest(flag=flag, value=value):
                lua = reader_fixture(); lua.globals()[flag] = value
                lua.execute(b'local data,reason=READER();assert(data==nil and reason);assert(closes==1 and reads==0)')

    def test_failed_short_exception_and_close_failures_preserve_guard_boundary(self):
        for flag in (b'read_failure', b'short_read', b'read_exception', b'close_failure'):
            with self.subTest(flag=flag):
                lua = reader_fixture(); lua.globals()[flag] = True
                lua.execute(b'local data,reason=READER();assert(data==nil and reason);assert(closes==1 and reads==1)')

    @unittest.skipUnless(os.name == 'nt', 'Windows wide-file API fixture')
    def test_real_win32_read_from_unicode_and_spaces_path(self):
        lua = reader_fixture()
        with tempfile.TemporaryDirectory(prefix='CoopPlus Unicode Ω ') as folder:
            fixture = pathlib.Path(folder) / 'Noita Ω 𐐀 fixture.exe'
            fixture.write_bytes(b'fixture\0' * (SIZE // 8))
            lua.globals()[b'WIDE'] = str(fixture).encode('utf-16-le')
            lua.execute(b'''
            FFI.cdef[[
              void* __stdcall CreateFileW(const unsigned short*,unsigned long,unsigned long,void*,unsigned long,unsigned long,void*);
              int __stdcall GetFileSizeEx(void*,long long*);
              int __stdcall ReadFile(void*,void*,unsigned long,unsigned long*,void*);
              int __stdcall CloseHandle(void*);
            ]]
            local win=FFI.load('kernel32')
            K.CreateFileW=win.CreateFileW;K.GetFileSizeEx=win.GetFileSizeEx
            K.ReadFile=win.ReadFile;K.CloseHandle=win.CloseHandle
            local data,reason=READER();assert(data==EXPECTED,reason)
            ''')


if __name__ == '__main__':
    unittest.main()
