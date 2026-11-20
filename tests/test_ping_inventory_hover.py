"""Fresh native inventory identity requires an explicit, stable, bounded request.

The adapter tests use capture windows, never guessed inventory coordinates.
Native entry proof and physical input are separate coordinator evidence.
"""
import hashlib
import pathlib
import struct
import unittest
from lupa.luajit21 import LuaRuntime

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT/'quant.ew/files/system/player_ping/native_inventory_capture.lua'
INSTALLED_EXE = pathlib.Path(r'D:\SteamLibrary\steamapps\common\Noita\noita.exe')


class CaptureMemory:
    """Win32 memory/allocator substitute: no process memory or native code executes."""
    def __init__(self, data):
        self.regions=[];self.next_heap=0x30000000;self.allocations=0;self.frees=0
        self.fail_text=False;self.fail_code=False;self.fail_alloc=False
        self.add(0x400000,data[:0x400].ljust(0x1000,b'\0'),0x20,0x400000)
        pe=struct.unpack_from('<I',data,0x3c)[0]
        for i in range(struct.unpack_from('<H',data,pe+6)[0]):
            section=pe+24+struct.unpack_from('<H',data,pe+20)[0]+i*40
            rva,size,raw=struct.unpack_from('<III',data,section+12)
            self.add(0x400000+rva,data[raw:raw+size],0x20,0x400000)

    def add(self,address,data,protection,allocation):
        self.regions.append([address,bytearray(data),protection,allocation])

    def region(self,address,size=1):
        for region in self.regions:
            if region[0]<=address and address+size<=region[0]+len(region[1]):return region
        raise ValueError('mock memory outside allocation')

    def read(self,address,size):
        result=[]
        while size:
            r=self.region(address);offset=address-r[0];count=min(size,len(r[1])-offset)
            result.append(bytes(r[1][offset:offset+count]));address+=count;size-=count
        return b''.join(result)

    def write(self,address,data):
        while data:
            r=self.region(address);offset=address-r[0];count=min(len(data),len(r[1])-offset)
            r[1][offset:offset+count]=data[:count];address+=count;data=data[count:]

    def heap(self,size):
        address=self.next_heap;self.next_heap+=(size+15)&~15
        self.add(address,b'\0'*size,4,address);return address

    def alloc(self):
        if self.fail_alloc:return 0
        self.allocations+=1
        address=0x10000000+self.allocations*0x10000
        self.add(address,b'\0'*4096,4,address)
        self.add(address+4096,b'\0'*4096,4,address)
        return address

    def free(self,address):
        before=len(self.regions);self.regions=[r for r in self.regions if r[3]!=address]
        if len(self.regions)==before:return 0
        self.frees+=1;return 1

    def query(self,address):
        try:r=self.region(address)
        except ValueError:return (0,0,0,0,0,0,0)
        return r[0],r[3],r[2],len(r[1]),0x1000,r[2],0x20000

    def protect(self,address,protection):
        if self.fail_text and address==0xb65fb0:return 0
        if self.fail_code and 0x10000000<=address<0x20000000:return 0
        r=self.region(address);old=r[2];r[2]=protection;return old


def native_fixture(data):
    """Run the real lifecycle/verification Lua with injectable OS and FFI memory."""
    lua=LuaRuntime(encoding=None,unpack_returned_tuples=True)
    memory=CaptureMemory(data)
    values={b'READMEM':memory.read,b'WRITEMEM':memory.write,b'HEAP':memory.heap,
            b'ALLOC':memory.alloc,b'FREE':memory.free,b'QUERY':memory.query,b'PROTECT':memory.protect,
            b'HASH':lambda value:hashlib.sha256(value).digest(),b'EXE':data,b'SOURCE':BACKEND.read_bytes()}
    for key,value in values.items():lua.globals()[key]=value
    lua.execute(b'''
    local pointer
    local function addr(p)return type(p)=='table'and p.address or p end
    pointer=function(address,stride,signed)
      return setmetatable({address=address,stride=stride or 1,signed=signed}, {
        __index=function(p,k)
          if type(k)~='number'then return nil end
          local raw=READMEM(p.address+k*p.stride,p.stride);local n=0
          for i=#raw,1,-1 do n=n*256+raw:byte(i)end
          if p.signed and n>=2147483648 then n=n-4294967296 end
          return n
        end,
        __newindex=function(p,k,value)
          value=addr(value)or 0;value=value%4294967296;local result={}
          for i=1,p.stride do result[i]=string.char(value%256);value=math.floor(value/256)end
          WRITEMEM(p.address+k*p.stride,table.concat(result))
        end})
    end
    f={os='Windows',abi=function(kind)return kind=='32bit'end,cdef=function()end}
    function f.cast(kind,value)
      if kind=='uintptr_t'then return addr(value)end
      local width=(kind:find('char',1,true)and 1 or kind:find('short',1,true)and 2 or kind:find('long long',1,true)and 8 or 4)
      return pointer(addr(value),width,kind=='int32_t*')
    end
    function f.new(kind,initial)
      local width=(kind:find('char',1,true)and 1 or kind:find('short',1,true)and 2 or kind:find('long long',1,true)and 8 or 4)
      local count=tonumber(kind:match('%[(%d+)%]'));local p=pointer(HEAP(count*width),width)
      if initial then p[0]=initial end;return p
    end
    function f.string(p,size)return READMEM(addr(p),size)end
    function f.copy(p,value,size)WRITEMEM(addr(p),value:sub(1,size or #value))end
    function f.fill(p,size)WRITEMEM(addr(p),string.rep(string.char(0),size))end
    local kernel={}
    function kernel.GetModuleHandleA()return 0x400000 end
    function kernel.GetModuleFileNameW(_,out)for i=0,7 do out[i]=('mock.exe'):byte(i+1)end;out[8]=0;return 8 end
    function kernel.CreateFileW()return 41 end
    function kernel.GetFileSizeEx(_,out)out[0]=#EXE;return 1 end
    function kernel.ReadFile(_,out,size,count)f.copy(out,EXE,size);count[0]=#EXE;return 1 end
    function kernel.CloseHandle()return 1 end
    function kernel.GetCurrentProcess()return 1 end
    function kernel.FlushInstructionCache()return 1 end
    function kernel.VirtualQuery(p,out)
      local values={QUERY(addr(p))};if values[5]==0 then return 0 end
      for i=1,7 do out[i-1]=values[i]end;return 28
    end
    function kernel.VirtualAlloc()local p=ALLOC();return p~=0 and pointer(p)or nil end
    function kernel.VirtualFree(p)return FREE(addr(p))end
    function kernel.VirtualProtect(p,size,protection,old)
      local result=PROTECT(addr(p),protection)
      if result==0 then return 0 end;old[0]=result;return 1
    end
    local crypt={}
    function crypt.CryptAcquireContextA(p)p[0]=1;return 1 end
    function crypt.CryptCreateHash(_,_,_,_,p)p[0]=2;return 1 end
    function crypt.CryptHashData(_,data)hash_data=data;return 1 end
    function crypt.CryptGetHashParam(_,_,out,size)f.copy(out,HASH(hash_data));size[0]=32;return 1 end
    function crypt.CryptDestroyHash()return 1 end
    function crypt.CryptReleaseContext()return 1 end
    local sdl={SDL_GetMouseState=0x20001000}
    function f.load(name)if name=='kernel32'then return kernel elseif name=='advapi32'then return crypt else return sdl end end
    local previous=require
    function require(name)if name=='ffi'then return f else return previous(name)end end
    backend=assert(loadstring(SOURCE))()
    ''')
    return lua,memory


def fixture(default_validator=False):
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().READ=lambda path:(ROOT/path.removeprefix('mods/')).read_text(encoding='utf-8')
    lua.execute('''
    function dofile_once(path)return assert(loadstring(READ(path)))()end
    module=dofile_once('mods/quant.ew/files/system/player_ping/inventory_hover.lua')
    b={installed=false,seq=0,queue={},installs=0,closes=0}
    function b.install()b.installs=b.installs+1;b.installed=true;return true end
    function b.consume()return table.remove(b.queue,1)end
    function b.status()return{installed=b.installed,seq=b.seq}end
    function b.close()b.closes=b.closes+1;b.installed=false;return true end
    function b.mouse()return 98,59,0 end
    accepted_id=170
    function validate(id,context)return id==accepted_id end
    h=module.new(b,validate)
    c={run='run',body=100,inventory=500,frame=0,inventory_open=true,focused=true,blocked=false,mx=98,my=59,buttons=0}
    function settle()
      for frame=0,2 do c.frame=frame;h.poll(c)end
    end
    function capture(id,seq,options)
      options=options or{}
      local value={id=id or 170,seq=seq,count=options.count or 1,ambiguous=options.ambiguous==true,
        mx=options.mx or 98,my=options.my or 59,buttons=options.buttons or 0}
      b.seq=seq;b.queue[#b.queue+1]=value
    end
    function next_capture(frame,seq,id,options)c.frame=frame;capture(id,seq,options);return h.poll(c)end
    ''')
    if default_validator:
        lua.execute('''
        local once=dofile_once
        function dofile_once(path)
          if path:find('wand_snapshot.lua',1,true)then return{capture=function(id)return valid_snapshot and{}or nil end}end
          return once(path)
        end
        valid_snapshot=true;dead={};tags={[170]={wand=true},[167]={wand=true}};roots={[100]=100,[170]=100,[167]=100}
        inventory=500;items={167,170}
        function EntityGetIsAlive(id)return not dead[id]end
        function EntityHasTag(id,tag)return tags[id]and tags[id][tag]or false end
        function EntityGetRootEntity(id)return roots[id]or id end
        function EntityGetFirstComponentIncludingDisabled()return inventory end
        function GameGetAllInventoryItems()return items end
        function mutation()error('hover validation must be read-only')end
        ComponentSetValue2,EntityAddChild,EntitySetTransform=mutation,mutation,mutation
        h=module.new(b)
        ''')
    return lua


class InventoryHoverTests(unittest.TestCase):
    def test_requires_two_prior_pointer_frames_and_two_new_post_input_windows(self):
        fixture().execute('''
        assert(not h.request(c)and b.installs==0)
        h.poll(c);c.frame=1;h.poll(c);assert(not h.request(c))
        c.frame=2;h.poll(c);assert(h.request(c)and b.installs==1)
        capture(170,2);assert(not h.poll(c)) -- origin-frame capture cannot count
        c.frame=3;assert(not h.poll(c));assert(h.status().captures==1)
        local id,reason,cap=next_capture(4,4)
        assert(id==170 and reason=='ready' and cap.id==170 and not h.status().pending)
        c.frame=5;assert(not h.poll(c)) -- no automatic repeat
        ''')

    def test_previous_capture_and_duplicate_generation_do_not_establish_freshness(self):
        fixture().execute('''
        settle();capture(167,100);assert(h.request(c));assert(#b.queue==0)
        assert(not next_capture(3,100));assert(h.status().captures==0)
        assert(not next_capture(4,102));assert(h.status().captures==1)
        assert(not next_capture(5,102));assert(h.status().captures==1)
        local id=next_capture(6,104);assert(id==170)
        ''')

    def test_two_render_calls_in_one_window_are_only_one_fresh_sample(self):
        fixture().execute('''
        settle();assert(h.request(c))
        assert(not next_capture(3,4,170,{count=2}));assert(h.status().captures==1)
        assert(not h.poll(c));assert(h.status().captures==1)
        assert(next_capture(4,6)==170)
        ''')

    def test_pointer_move_invalidates_even_if_native_tooltip_still_names_old_item(self):
        fixture().execute('''
        settle();assert(h.request(c));assert(not next_capture(3,2))
        c.frame=4;c.mx=100;capture(170,4,{mx=100});assert(not h.poll(c))
        assert(not h.status().pending and h.status().reason=='context changed')
        assert(not h.request(c)) -- no immediate fallback to the old/held item
        c.frame=5;h.poll(c);assert(not h.request(c))
        c.frame=6;h.poll(c);assert(h.request(c))
        ''')

    def test_capture_pointer_disagreement_ambiguous_or_distinct_item_cancels(self):
        for options in ('{mx=99}','{my=60}','{ambiguous=true,count=2}','{count=0}','{buttons=1}','{buttons=4}'):
            with self.subTest(options=options):
                fixture().execute('settle();assert(h.request(c));assert(not next_capture(3,2,170,'+options+'));assert(not h.status().pending)')
        fixture().execute('''
        settle();assert(h.request(c));assert(not next_capture(3,2));accepted_id=167
        assert(not next_capture(4,4,167));assert(h.status().reason=='hover changed')
        ''')

    def test_middle_release_is_allowed_but_left_right_and_side_buttons_block(self):
        fixture().execute('''
        settle();c.buttons=2;assert(h.request(c))
        assert(not next_capture(3,2,170,{buttons=2}))
        c.buttons=0;assert(next_capture(4,4)==170)
        ''')
        for buttons in (1,4,5,8,16):
            with self.subTest(buttons=buttons):
                fixture().execute('settle();c.buttons='+str(buttons)+';assert(not h.request(c)and b.installs==0)')

    def test_body_run_inventory_menu_focus_and_rollback_changes_cancel(self):
        changes=('c.body=101','c.run="other"','c.inventory=501','c.inventory_open=false','c.focused=false','c.blocked=true','c.frame=1')
        for change in changes:
            with self.subTest(change=change):
                fixture().execute('settle();assert(h.request(c));c.frame=3;'+change+';capture(170,2);assert(not h.poll(c));assert(not h.status().pending)')

    def test_no_capture_times_out_at_six_frame_budget_and_cold_close_resets_history(self):
        fixture().execute('''
        settle();assert(h.request(c))
        for frame=3,8 do c.frame=frame;assert(not h.poll(c))end
        c.frame=9;assert(not h.poll(c)and h.status().reason=='timeout')
        assert(h.close()and b.closes==1 and not b.installed)
        assert(h.close()and b.closes==2)
        assert(not h.request(c)) -- reopening cannot reuse pre-close pointer history
        c.frame=10;h.poll(c);c.frame=11;h.poll(c);c.frame=12;h.poll(c)
        assert(h.request(c))
        ''')

    def test_missing_backend_conflict_install_failure_and_close_failure_fail_closed(self):
        fixture().execute('''
        b.install=function()return false end;settle();assert(not h.request(c)and not h.status().pending)
        b.install=function()error('foreign detour')end;assert(not h.request(c))
        b.install=function()b.installed=true;return true end;assert(h.request(c))
        b.close=function()return false end;assert(not h.close()and not h.status().pending)
        ''')

    def test_generation_reset_and_invalid_native_id_do_not_share(self):
        fixture().execute('''
        settle();capture(170,100);assert(h.request(c))
        assert(not next_capture(3,2));assert(h.status().reason=='capture generation reset')
        ''')
        for entity in (0,-1,2147483648):
            with self.subTest(entity=entity):
                fixture().execute('settle();assert(h.request(c));capture('+str(entity)+',2);c.frame=3;assert(not h.poll(c));assert(not h.status().pending)')

    def test_validation_reacquires_body_inventory_owner_and_actual_membership(self):
        fixture(True).execute('''
        settle();assert(h.request(c));assert(not next_capture(3,2));assert(next_capture(4,4)==170)
        ''')
        for change in ('dead[170]=true','tags[170].wand=nil','tags[100]={heart_statue=true}',
                       'tags[100]={ew_notplayer=true}','roots[170]=200','inventory=501',
                       'items={167}','valid_snapshot=false'):
            with self.subTest(change=change):
                fixture(True).execute('settle();assert(h.request(c));'+change+';assert(not next_capture(3,2));assert(h.status().reason=="invalid wand ownership")')

    def test_capture_validation_exception_and_backend_loss_cancel(self):
        fixture().execute('''
        h=module.new(b,function()error('entity replaced')end)
        settle();assert(h.request(c));assert(not next_capture(3,2));assert(not h.status().pending)
        ''')
        fixture().execute('''
        settle();assert(h.request(c));b.installed=false;c.frame=3
        assert(not h.poll(c)and not h.status().pending and h.status().reason=='capture unavailable')
        ''')


class NativeCaptureContractTests(unittest.TestCase):
    def installed_data(self):
        if not INSTALLED_EXE.exists():self.skipTest('installed Noita unavailable')
        return INSTALLED_EXE.read_bytes()

    def test_real_verifier_install_capture_reload_adoption_and_owned_cold_teardown(self):
        lua,memory=native_fixture(self.installed_data())
        lua.execute(b'assert(backend.install(),backend.last_error)')
        self.assertEqual(memory.allocations,1)
        page=int(lua.eval(b'backend.status().page'))
        memory.write(page+128,struct.pack('<14I',2,170,1,0,98,59,0,0xb73432,170,0xb73432,98,59,0,0xb73432))
        lua.execute(b'''
        local cap=backend.consume();assert(cap.id==170 and cap.seq==2 and not cap.ambiguous and cap.mx==98)
        assert(not backend.consume());assert(backend.install())
        rawset(_G,'__ew_native_inventory_capture_v1',nil)
        backend=assert(loadstring(SOURCE))();assert(backend.install(),backend.last_error)
        assert(backend.close(),backend.last_error);assert(backend.close())
        ''')
        self.assertEqual(memory.allocations,1)
        self.assertEqual(memory.frees,1)
        self.assertEqual(memory.read(0xb65fb0,5),bytes.fromhex('558bec6aff'))

    def test_conflicting_detours_and_changed_long_body_never_get_overwritten(self):
        for offset,value in ((0,b'\xe9\0\0\0\0'),(100,b'\x90')):
            with self.subTest(offset=offset):
                lua,memory=native_fixture(self.installed_data())
                memory.write(0xb65fb0+offset,value)
                lua.execute(b'assert(not backend.install()and not backend.status().installed)')
                expected=b'foreign entry detour' if offset==0 else b'native function body differs'
                self.assertEqual(lua.eval(b'backend.last_error'),expected)
                self.assertEqual(memory.allocations,0)
                self.assertEqual(memory.read(0xb65fb0+offset,len(value)),value)

    def test_protection_failure_releases_unreferenced_code_and_foreign_replacement_is_retained(self):
        lua,memory=native_fixture(self.installed_data());memory.fail_text=True
        lua.execute(b'assert(not backend.install())')
        self.assertEqual(memory.allocations,1);self.assertEqual(memory.frees,1)
        self.assertEqual(memory.read(0xb65fb0,5),bytes.fromhex('558bec6aff'))
        memory.fail_text=False
        lua.execute(b'assert(backend.install(),backend.last_error)')
        memory.write(0xb65fb0,b'\xe9\0\0\0\0')
        lua.execute(b'assert(not backend.install());assert(not backend.close())')
        self.assertEqual(memory.frees,1) # another hook may still chain into our allocation
        self.assertEqual(memory.read(0xb65fb0,5),b'\xe9\0\0\0\0')

    def test_byte_builder_preserves_register_frame_and_always_resumes_native_prologue(self):
        lua=LuaRuntime(encoding=None,unpack_returned_tuples=True)
        backend=lua.execute(BACKEND.read_bytes())
        code=backend[b'build_code'](0x10001000,0x10000080,0xb65fb0,0x20001000)
        self.assertEqual(code[:4],bytes.fromhex('9c608bec'))  # pushfd/pushad/saved ESP
        self.assertIn(bytes.fromhex('8b4528'),code)  # original [esp+4], after 36 saved bytes
        self.assertIn(bytes.fromhex('0fae0424'),code)  # FXSAVE for public SDL call
        self.assertIn(bytes.fromhex('0fae0c24'),code)  # FXRSTOR
        self.assertEqual(code[-12:-5],bytes.fromhex('619d558bec6aff'))
        self.assertEqual(code[-5],0xe9)
        target=(0x10001000+len(code)+struct.unpack('<i',code[-4:])[0])&0xffffffff
        self.assertEqual(target,0xb65fb5)
        self.assertNotIn(b'\xc3',code)  # no early-return/blanking instruction

    def test_module_reload_is_singleton_without_an_install_or_allocation(self):
        lua=LuaRuntime(unpack_returned_tuples=True)
        lua.globals().SOURCE=BACKEND.read_text(encoding='utf-8')
        lua.execute('''
        local a=assert(loadstring(SOURCE))();local b=assert(loadstring(SOURCE))()
        assert(a==b and not a.status().installed and a.close())
        ''')

    def test_installed_executable_and_complete_function_match_guarded_build(self):
        path=pathlib.Path(r'D:\SteamLibrary\steamapps\common\Noita\noita.exe')
        if not path.exists():self.skipTest('installed Noita unavailable')
        data=path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),'808d2a0ab51ea0b46e9ad2aeb3327a4b0ce3feae04f32ba26326bf585b5779bd')
        pe=struct.unpack_from('<I',data,0x3c)[0]
        self.assertEqual(struct.unpack_from('<H',data,pe+4)[0],0x14c)
        self.assertEqual(struct.unpack_from('<I',data,pe+8)[0],0x6794ee3c)
        self.assertEqual(struct.unpack_from('<H',data,pe+24)[0],0x10b)
        body=data[0x7653b0:0x7653b0+48761]
        self.assertEqual(hashlib.sha256(body).hexdigest(),'b085cab9b7462217547a03b4b7e5f70899bb906f36054ad69464d36052e538b1')
        self.assertEqual(body[:5],bytes.fromhex('558bec6aff'))
        self.assertEqual(body[-1],0xc3)


if __name__=='__main__':
    unittest.main()
