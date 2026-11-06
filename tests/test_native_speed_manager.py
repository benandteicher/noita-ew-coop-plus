"""Verified native-row consumer lifecycle; simulated Win32 memory, no native code executes."""
import pathlib, unittest
import test_ping_inventory_hover as fixture
ROOT=pathlib.Path(__file__).resolve().parents[1]

class RowMemory(fixture.CaptureMemory):
    def alloc(self):
        if self.fail_alloc:return 0
        self.allocations+=1;address=0x10000000+self.allocations*0x20000
        self.add(address,b"\0"*65536,4,address);self.add(address+65536,b"\0"*4096,4,address)
        return address

def native():
    old=fixture.CaptureMemory;fixture.CaptureMemory=RowMemory
    try:lua,memory=fixture.native_fixture(fixture.INSTALLED_EXE.read_bytes())
    finally:fixture.CaptureMemory=old
    lua.globals()[b"READ_MODULE"]=lambda p:(ROOT/p.decode().removeprefix('mods/')).read_bytes()
    lua.execute(b"function dofile_once(path)return assert(loadstring(READ_MODULE(path)))()end")
    return lua,memory

@unittest.skipUnless(fixture.INSTALLED_EXE.exists(),'Installed verified PE required for memory lifecycle substitute')
class SpeedManagerTests(unittest.TestCase):
    def test_independent_consumers_release_only_their_own_patches(self):
        lua,m=native();lua.execute(b'assert(backend.install_speed());assert(backend.install());assert(backend.close());assert(backend.speed_status().owned);assert(backend.close_speed())')
        self.assertEqual(m.allocations,m.frees)
        lua,m=native();lua.execute(b'assert(backend.install());assert(backend.install_speed());assert(backend.close_speed());assert(backend.status().installed);assert(backend.consume()==nil);assert(backend.close())')
        self.assertEqual(m.allocations,m.frees)

    def test_formatted_map_is_bounded_and_rejects_invalid_entries(self):
        lua,m=native();lua.execute(b"assert(backend.install_speed());assert(backend.update_speeds({{id=170,text='0.3333x'},{id=171,text='1.2e-8x'},{id=0,text='bad'},{id=172,text='a'..string.char(0)..'b'}}));assert(backend.speed_status().count==2)")
        row=next(r for r in m.regions if bytes(r[1]).startswith(b'EWnative-speed-row-v3'))
        import struct
        self.assertEqual(struct.unpack('<I',m.read(row[0]+256,4))[0],170)
        self.assertTrue(m.read(row[0]+260,96).startswith(b'0.3333x\0'))
        lua.execute(b"local many={};for i=1,600 do many[i]={id=i,text='1x'}end;assert(backend.update_speeds(many));assert(backend.speed_status().count==512);assert(backend.close_speed())")

    def test_cold_module_adopts_owned_speed_and_capture_then_releases_exactly(self):
        lua,m=native();lua.execute(b"assert(backend.install());assert(backend.install_speed());_G.__ew_native_inventory_capture_v1=nil;backend=assert(loadstring(SOURCE))();assert(backend.install_speed());assert(backend.speed_status().owned);assert(backend.status().installed);assert(backend.close_speed());assert(backend.close())")
        self.assertEqual(m.allocations,2);self.assertEqual(m.frees,2)

    def test_conflicting_row_is_preserved_and_native_entry_is_untouched(self):
        lua,m=native();old=m.read(0xb65fb0,5);foreign=b'\xe9\0\0\0\0\x90';m.write(0xb6af45,foreign)
        lua.execute(b'assert(not backend.install_speed());assert(not backend.speed_status().installed)')
        self.assertEqual(m.read(0xb6af45,6),foreign);self.assertEqual(m.read(0xb65fb0,5),old);self.assertEqual(m.allocations,0)

    def test_allocation_and_code_protection_fail_without_hooking(self):
        for flag in ('fail_alloc','fail_code'):
            lua,m=native();setattr(m,flag,True);old=m.read(0xb6af45,6)
            lua.execute(b'assert(not backend.install_speed());assert(backend.close_speed())')
            self.assertEqual(m.read(0xb6af45,6),old);self.assertEqual(m.allocations,m.frees)

    def test_foreign_replacement_cannot_be_uninstalled_or_freed(self):
        lua,m=native();lua.execute(b'assert(backend.install_speed())');foreign=b'\xe9\1\2\3\4\x90';m.write(0xb6af45,foreign)
        lua.execute(b'assert(not backend.close_speed())');self.assertEqual(m.read(0xb6af45,6),foreign);self.assertEqual(m.frees,0)

    def test_cold_cleanup_releases_owned_row_despite_unrelated_body_conflict(self):
        lua,m=native();lua.execute(b'assert(backend.install_speed())')
        foreign=m.read(0xb66010,1);m.write(0xb66010,bytes([foreign[0]^1]))
        lua.execute(b"_G.__ew_native_inventory_capture_v1=nil;backend=assert(loadstring(SOURCE))();assert(not backend.install_speed());assert(not backend.install_speed());assert(backend.close_speed());assert(not backend.speed_status().installed)")
        self.assertEqual(m.frees,1);self.assertEqual(m.read(0xb66010,1),bytes([foreign[0]^1]))

    def test_cold_capture_close_does_not_acquire_a_new_hook(self):
        lua,m=native();lua.execute(b'assert(backend.close())');self.assertEqual(m.allocations,0)
        lua.execute(b"assert(backend.install());assert(backend.install_speed());_G.__ew_native_inventory_capture_v1=nil;backend=assert(loadstring(SOURCE))();assert(backend.close());assert(not backend.status().installed);assert(backend.speed_status().owned);assert(backend.close_speed())")
        self.assertEqual(m.allocations,2);self.assertEqual(m.frees,2)

    def test_cold_partial_owned_pair_is_recoverable_without_freeing_reachable_code(self):
        for original_site,raw in ((0xb6af45,bytes.fromhex('8b855cbfffff')),(0xb6650b,bytes.fromhex('f30f118574bfffff')),(0xb6796b,bytes.fromhex('f30f118544bfffff'))):
            lua,m=native();lua.execute(b'assert(backend.install_speed())');m.write(original_site,raw)
            lua.execute(b"_G.__ew_native_inventory_capture_v1=nil;backend=assert(loadstring(SOURCE))();assert(not backend.install_speed());assert(backend.close_speed());assert(not backend.speed_status().installed)")
            self.assertEqual(m.frees,1)

    def test_each_lone_owned_site_is_cold_recovered_without_freeing_live_code(self):
        sites=((0xb6af45,bytes.fromhex('8b855cbfffff')),(0xb6650b,bytes.fromhex('f30f118574bfffff')),(0xb6796b,bytes.fromhex('f30f118544bfffff')))
        for keep,_ in sites:
            lua,m=native();lua.execute(b'assert(backend.install_speed())')
            for address,raw in sites:
                if address!=keep:m.write(address,raw)
            lua.execute(b"_G.__ew_native_inventory_capture_v1=nil;backend=assert(loadstring(SOURCE))();assert(not backend.install_speed());assert(backend.close_speed())")
            self.assertEqual(m.frees,1)
            for address,raw in sites:self.assertEqual(m.read(address,len(raw)),raw)

    def test_foreign_height_seam_is_not_overwritten_or_freed(self):
        lua,m=native();lua.execute(b'assert(backend.install_speed())');foreign=b'\xe9\x01\x02\x03\x04\x90\x90\x90';m.write(0xb6796b,foreign)
        lua.execute(b'assert(not backend.close_speed())');self.assertEqual(m.read(0xb6796b,8),foreign);self.assertEqual(m.frees,0)
