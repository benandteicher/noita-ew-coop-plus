-- Verified-build inventory capture only. Native cards always render normally.
-- Derived from noita-tooltip-example/cardhook.lua, cad71b191ba9dce1fdce701d75a4756466adac33.
-- Full upstream MIT notice is retained below. No executable file is modified.
local registry = "__ew_native_inventory_capture_v1"
if rawget(_G, registry) then return rawget(_G, registry) end
local M = { installed = false, last_error = nil }
local EXPECTED_SHA = "808d2a0ab51ea0b46e9ad2aeb3327a4b0ce3feae04f32ba26326bf585b5779bd"
local BODY_SHA = "b085cab9b7462217547a03b4b7e5f70899bb906f36054ad69464d36052e538b1"
local RVA, LENGTH = 0x765fb0, 48761
local ORIGINAL = string.char(0x55, 0x8b, 0xec, 0x6a, 0xff)
local MAGIC = "EWcapture-only-v1"
local calls = {0x771d6e, 0x7733ed, 0x779504, 0x7817a6, 0x781f32, 0x790304}
local ffi, kernel, crypt, sdl, image, page, code, stub, patch, getmouse
local disk, parsed, metadata
local speed={installed=false}
local speed_owns,speed_adopt
local SPEED_HOOK,SPEED_BLOCK,SPEED_LENGTH=0x76af45,0x76aa84,0x4c1
local SPEED_PAD,PAD_CODE=0x76796b,2560
local PAD_ORIGINAL=string.char(0xf3,0x0f,0x11,0x85,0x44,0xbf,0xff,0xff)
local SPEED_LAYOUT=0x76650b
local LAYOUT_ORIGINAL=string.char(0xf3,0x0f,0x11,0x85,0x74,0xbf,0xff,0xff)
local SPEED_ORIGINAL=string.char(0x8b,0x85,0x5c,0xbf,0xff,0xff)
local function fail(reason) M.last_error = reason; return false end
local function u32(s, at)
    local a,b,c,d=s:byte(at,at+3)
    if not d then error("truncated PE") end
    return a+b*256+c*65536+d*16777216
end
local function u16(s, at) local a,b=s:byte(at,at+1);if not b then error("truncated PE")end;return a+b*256 end
local function bytes32(n)
    n=n%4294967296
    return string.char(n%256,math.floor(n/256)%256,math.floor(n/65536)%256,math.floor(n/16777216)%256)
end
local function ptr(n) return ffi.cast("void*",n) end
local function number(p) return tonumber(ffi.cast("uintptr_t",p)) end
local function equal(address,value) return ffi.string(ptr(address),#value)==value end

local function setup()
    local ok,lib=pcall(require,"ffi")
    if not ok or lib.os~="Windows" or not lib.abi("32bit") then return fail("requires Windows PE32 LuaJIT") end
    ffi=lib
    ffi.cdef[[
      void* __stdcall GetModuleHandleA(const char*);
      unsigned long __stdcall GetModuleFileNameA(void*,char*,unsigned long);
      void* __stdcall GetCurrentProcess(void);
      int __stdcall FlushInstructionCache(void*,const void*,unsigned long);
      void* __stdcall VirtualAlloc(void*,unsigned long,unsigned long,unsigned long);
      int __stdcall VirtualFree(void*,unsigned long,unsigned long);
      int __stdcall VirtualProtect(void*,unsigned long,unsigned long,unsigned long*);
      unsigned long __stdcall VirtualQuery(const void*,void*,unsigned long);
      int __stdcall CryptAcquireContextA(void**,const char*,const char*,unsigned long,unsigned long);
      int __stdcall CryptCreateHash(void*,unsigned long,void*,unsigned long,void**);
      int __stdcall CryptHashData(void*,const unsigned char*,unsigned long,unsigned long);
      int __stdcall CryptGetHashParam(void*,unsigned long,unsigned char*,unsigned long*,unsigned long);
      int __stdcall CryptDestroyHash(void*);
      int __stdcall CryptReleaseContext(void*,unsigned long);
      unsigned int SDL_GetMouseState(int*,int*);
    ]]
    kernel=ffi.load("kernel32");crypt=ffi.load("advapi32");sdl=ffi.load("SDL2")
    getmouse=number(sdl.SDL_GetMouseState)
    return true
end
local function sha256(data)
    local provider,hash=ffi.new("void*[1]"),ffi.new("void*[1]")
    if crypt.CryptAcquireContextA(provider,nil,nil,24,0xf0000000)==0 then error("SHA256 provider unavailable")end
    local ok,result=pcall(function()
        if crypt.CryptCreateHash(provider[0],0x800c,nil,0,hash)==0 then error("SHA256 hash unavailable")end
        if crypt.CryptHashData(hash[0],data,#data,0)==0 then error("SHA256 input failed")end
        local out,size=ffi.new("unsigned char[32]"),ffi.new("unsigned long[1]",32)
        if crypt.CryptGetHashParam(hash[0],2,out,size,0)==0 or size[0]~=32 then error("SHA256 output failed")end
        local hex={};for i=0,31 do hex[#hex+1]=string.format("%02x",out[i])end
        return table.concat(hex)
    end)
    if hash[0]~=nil then crypt.CryptDestroyHash(hash[0])end
    crypt.CryptReleaseContext(provider[0],0)
    if not ok then error(result)end
    return result
end
local function parse(data)
    if data:sub(1,2)~="MZ" then error("not MZ")end
    local pe=u32(data,0x3c+1)+1
    if data:sub(pe,pe+3)~="PE\0\0" or u16(data,pe+4)~=0x14c then error("not PE32 i386")end
    local optional=pe+24
    if u16(data,optional)~=0x10b or u32(data,pe+8)~=0x6794ee3c or u16(data,pe+6)~=8
        or u32(data,optional+28)~=0x400000 or u32(data,optional+56)~=0xee9000 then error("PE build mismatch")end
    local sections={}
    for i=0,7 do
        local at=optional+u16(data,pe+20)+i*40
        sections[#sections+1]={rva=u32(data,at+12),size=u32(data,at+16),raw=u32(data,at+20)}
    end
    local function at(rva,size)
        for _,s in ipairs(sections)do
            if rva>=s.rva and rva+size<=s.rva+s.size then return s.raw+rva-s.rva+1 end
        end
        error("RVA outside file sections")
    end
    local reloc_rva,reloc_size=u32(data,optional+96+5*8),u32(data,optional+96+5*8+4)
    local reloc={};local pos=at(reloc_rva,reloc_size);local stop=pos+reloc_size
    while pos<stop do
        local rva,size=u32(data,pos),u32(data,pos+4)
        if size<8 or size%2~=0 or pos+size>stop then error("bad relocations")end
        for entry=pos+8,pos+size-1,2 do
            local value=u16(data,entry);local kind=math.floor(value/4096)
            if kind==3 then
                local address=rva+value%4096
                if address>=RVA and address<0x790359 then reloc[#reloc+1]=address end
            elseif kind~=0 then error("unsupported relocation")end
        end
        pos=pos+size
    end
    return {at=at,reloc=reloc}
end
local function committed(address,size,allocation)
    -- MEMORY_BASIC_INFORMATION for PE32. Guard/noaccess/noncommitted reads are rejected.
    local info=ffi.new("unsigned long[7]")
    if kernel.VirtualQuery(ptr(address),info,28)~=28 then return false end
    local protection=tonumber(info[5])
    local readable={ [2]=true,[4]=true,[8]=true,[0x20]=true,[0x40]=true,[0x80]=true }
    return info[4]==0x1000 and protection%512<256 and readable[protection%256]
        and tonumber(info[0])<=address and tonumber(info[0])+tonumber(info[3])>=address+size
        and (not allocation or tonumber(info[1])==allocation)
end
local function expected(rva,size)
    local result=disk:sub(parsed.at(rva,size),parsed.at(rva,size)+size-1)
    local delta=image-0x400000
    if delta~=0 then
        for _,r in ipairs(parsed.reloc)do
            if r>=rva and r+4<=rva+size then
                local at=r-rva+1;result=result:sub(1,at-1)..bytes32(u32(result,at)+delta)..result:sub(at+4)
            end
        end
    end
    return result
end
local function verify_build()
    image=number(kernel.GetModuleHandleA(nil))
    if image==0 or not committed(image,512)then return fail("module unavailable")end
    local path=ffi.new("char[2048]");local n=kernel.GetModuleFileNameA(nil,path,2048)
    if n==0 or n>=2048 then return fail("module path unavailable")end
    local name=ffi.string(path,n)
    local file=io.open(name,"rb");if not file then return fail("cannot read actual executable")end
    disk=file:read("*a");file:close()
    if #disk~=15460864 or sha256(disk)~=EXPECTED_SHA then return fail("unsupported executable SHA256")end
    parsed=parse(disk)
    local pe=u32(disk,0x3c+1)
    if not committed(image+pe,248)or not equal(image+pe,disk:sub(pe+1,pe+248))then return fail("loaded PE header mismatch")end
    local body=expected(RVA,LENGTH)
    if sha256(disk:sub(parsed.at(RVA,LENGTH),parsed.at(RVA,LENGTH)+LENGTH-1))~=BODY_SHA then return fail("function hash mismatch")end
    if not committed(image+RVA,LENGTH)then return fail("function memory unavailable")end
    local actual=ffi.string(ptr(image+RVA),LENGTH)
    -- Permit only the five-byte ownership-verified entry detour, checked separately below.
    if actual:sub(1,1)==string.char(0xe9)then actual=ORIGINAL..actual:sub(6)end
    if not equal(image+SPEED_HOOK,SPEED_ORIGINAL)then
        if not(speed_owns and speed_owns())and not(speed_adopt and speed_adopt())then return fail("foreign speed row detour")end
        local offset=SPEED_HOOK-RVA
        actual=actual:sub(1,offset)..SPEED_ORIGINAL..actual:sub(offset+#SPEED_ORIGINAL+1)
    end
    if not equal(image+SPEED_LAYOUT,LAYOUT_ORIGINAL)then
        if not(speed_owns and speed_owns())and not(speed_adopt and speed_adopt())then return fail("foreign speed layout detour")end
        local offset=SPEED_LAYOUT-RVA
        actual=actual:sub(1,offset)..LAYOUT_ORIGINAL..actual:sub(offset+#LAYOUT_ORIGINAL+1)
    end
    if not equal(image+SPEED_PAD,PAD_ORIGINAL)then
        if not(speed_owns and speed_owns())and not(speed_adopt and speed_adopt())then return fail("foreign speed padding detour")end
        local offset=SPEED_PAD-RVA
        actual=actual:sub(1,offset)..PAD_ORIGINAL..actual:sub(offset+#PAD_ORIGINAL+1)
    end
    if actual~=body then return fail("native function body differs")end
    for _,rva in ipairs(calls)do
        if not committed(image+rva,85)or not equal(image+rva,expected(rva,85))then return fail("native callsite differs")end
    end
    if speed.installed then speed.validated=true end
    metadata={sha256=EXPECTED_SHA,body_sha256=BODY_SHA,base=image,function_address=image+RVA,
        function_size=LENGTH,pe32=true,aslr=image~=0x400000,body_memory_verified=true,callsite_count=#calls}
    return true
end

-- Header page + mutable capture buffer; code occupies the second page (RX).
-- Buffer starts at page+128: seq,id,count,ambiguous,mx,my,buttons,caller,
-- current_id,current_caller,temp_mx,temp_my,temp_buttons,last_caller.
local function build_code(address,buffer,fn,mouse)
    local parts,labels,fixups={}, {}, {};local length=0
    local function emit(s)parts[#parts+1]=s;length=length+#s end
    local function op(...)emit(string.char(...))end
    local function abs(opcode,offset)emit(opcode..bytes32(buffer+offset))end
    local function jump(opcode,label)emit(opcode);fixups[#fixups+1]={at=length,label=label};emit("\0\0\0\0")end
    local function mark(label)labels[label]=length end
    op(0x9c,0x60,0x8b,0xec) -- pushfd; pushad; ebp = saved register frame
    abs("\xff\x05",0) -- seq odd while writing
    op(0x8b,0x45,0x28,0x85,0xc0);jump("\x0f\x84","invalid") -- first Entity* at original esp+4
    op(0x3d);emit(bytes32(0x10000));jump("\x0f\x82","invalid")
    op(0x3d);emit(bytes32(0xffff0000));jump("\x0f\x83","invalid")
    op(0x8b,0x00);jump("\xe9","have_id") -- dereference only at live native entry
    mark("invalid");op(0x31,0xc0)
    mark("have_id");abs("\xa3",32);op(0x8b,0x45,0x24);abs("\xa3",36)
    -- Preserve x87/MMX/SSE/MXCSR around public SDL cdecl input query.
    op(0x81,0xec);emit(bytes32(528));op(0x83,0xe4,0xf0,0x0f,0xae,0x04,0x24)
    op(0x68);emit(bytes32(buffer+44));op(0x68);emit(bytes32(buffer+40))
    op(0xb8);emit(bytes32(mouse));op(0xff,0xd0,0x83,0xc4,0x08);abs("\xa3",48)
    op(0x0f,0xae,0x0c,0x24,0x8b,0xe5)
    abs("\xff\x05",8);abs("\x83\x3d",8);op(0x01);jump("\x0f\x85","compare")
    for _,pair in ipairs({{32,4},{40,16},{44,20},{48,24},{36,28}})do abs("\xa1",pair[1]);abs("\xa3",pair[2])end
    jump("\xe9","done")
    mark("compare")
    for _,pair in ipairs({{32,4},{40,16},{44,20},{48,24}})do
        abs("\xa1",pair[1]);abs("\x3b\x05",pair[2]);jump("\x0f\x85","ambiguous")
    end
    jump("\xe9","done")
    mark("ambiguous");abs("\xc7\x05",12);emit(bytes32(1))
    mark("done");abs("\xa1",36);abs("\xa3",52);abs("\xff\x05",0)
    op(0x61,0x9d);emit(ORIGINAL);op(0xe9);emit(bytes32(fn+5-(address+length+4)))
    local result=table.concat(parts)
    for _,f in ipairs(fixups)do
        local at=f.at+1;result=result:sub(1,at-1)..bytes32(labels[f.label]-(f.at+4))..result:sub(at+4)
    end
    return result
end
M.build_code=build_code -- pure byte builder for diagnostic disassembly/tests
local function header(p,c) return MAGIC..bytes32(image+RVA)..bytes32(p)..bytes32(c)..bytes32(getmouse)..ORIGINAL end
local function detour(c)return string.char(0xe9)..bytes32(c-(image+RVA+5))end
local function owns()
    return page and image and stub and patch and committed(image+RVA,5)
        and committed(page,256,page)and committed(code,#stub,page)
        and equal(page,header(page,code))and equal(code,stub)and equal(image+RVA,patch)
end
local function change_text(before,after)
    local fn=image+RVA
    if not equal(fn,before)then return false,"entry ownership changed"end
    local old=ffi.new("unsigned long[1]")
    if kernel.VirtualProtect(ptr(fn),5,0x40,old)==0 then return false,"VirtualProtect failed"end
    if not equal(fn,before)then
        local ignored=ffi.new("unsigned long[1]");kernel.VirtualProtect(ptr(fn),5,old[0],ignored)
        return false,"entry changed during protection"
    end
    ffi.copy(ptr(fn),after,5)
    local flushed=kernel.FlushInstructionCache(kernel.GetCurrentProcess(),ptr(fn),5)~=0
    local ignored=ffi.new("unsigned long[1]")
    local restored=kernel.VirtualProtect(ptr(fn),5,old[0],ignored)~=0
    if flushed and restored then return true end
    -- Retain executable allocation unless entry restoration is confirmed by caller.
    local rollback=ffi.new("unsigned long[1]")
    if kernel.VirtualProtect(ptr(fn),5,0x40,rollback)~=0 then
        ffi.copy(ptr(fn),before,5);kernel.FlushInstructionCache(kernel.GetCurrentProcess(),ptr(fn),5)
        kernel.VirtualProtect(ptr(fn),5,old[0],ignored)
    end
    return false,"instruction cache/protection failure"
end
function M.install()
    if M.installed then if owns()then return true end;return fail("installed capture ownership changed")end
    local ok,result=pcall(function()
        if not setup()or not verify_build()then return false end
        local fn=image+RVA
        if not equal(fn,ORIGINAL)then
            if ffi.cast("unsigned char*",fn)[0]~=0xe9 then return fail("conflicting entry detour")end
            local rel=tonumber(ffi.cast("int32_t*",fn+1)[0]);local candidate=(fn+5+rel)%4294967296
            local allocation=candidate-4096
            if allocation<0x10000 or not committed(allocation,256,allocation)or not committed(candidate,1024,allocation)
                or not equal(allocation,header(allocation,candidate))then return fail("foreign entry detour")end
            local candidate_stub=build_code(candidate,allocation+128,fn,getmouse)
            if not equal(candidate,candidate_stub)then return fail("owned cave signature differs")end
            page,code,stub,patch=allocation,candidate,candidate_stub,detour(candidate)
            M.installed=true;M.last_error=nil;return true -- adopt exact owned singleton after Lua reload
        end
        local allocated=kernel.VirtualAlloc(nil,8192,0x3000,0x04)
        if allocated==nil then return fail("capture allocation failed")end
        page=number(allocated);code=page+4096;stub=build_code(code,page+128,fn,getmouse);patch=detour(code)
        ffi.fill(allocated,8192);ffi.copy(allocated,header(page,code));ffi.copy(ptr(code),stub,#stub)
        local old=ffi.new("unsigned long[1]")
        if #stub>1024 or kernel.VirtualProtect(ptr(code),4096,0x20,old)==0
            or kernel.FlushInstructionCache(kernel.GetCurrentProcess(),ptr(code),#stub)==0 then
            kernel.VirtualFree(allocated,0,0x8000);page=nil;return fail("capture code protection/cache failed")
        end
        local changed,reason=change_text(ORIGINAL,patch)
        if not changed then
            if equal(fn,ORIGINAL)then kernel.VirtualFree(allocated,0,0x8000);page=nil end
            return fail(reason)
        end
        M.installed=true;M.last_error=nil;return true
    end)
    disk,parsed=nil,nil -- keep bounded capture state, not the complete executable image
    if not ok then return fail(tostring(result))end
    return result
end
function M.consume()
    if not M.installed or not owns()then return nil end
    local b=ffi.cast("uint32_t*",page+128);local seq=tonumber(b[0])
    if seq%2~=0 or b[2]==0 then return nil end
    local result={seq=seq,id=tonumber(b[1]),count=tonumber(b[2]),ambiguous=b[3]~=0,
        mx=tonumber(ffi.cast("int32_t*",page+128)[4]),my=tonumber(ffi.cast("int32_t*",page+128)[5]),
        buttons=tonumber(b[6]),caller=tonumber(b[7]),last_caller=tonumber(b[13])}
    if b[0]~=seq then return nil end
    b[1],b[2],b[3]=0,0,0
    return result
end
function M.close()
    if not M.installed then
        local ok,inactive=pcall(function()
            if not kernel and not setup()then return true end
            image=image or number(kernel.GetModuleHandleA(nil))
            return committed(image+RVA,5)and equal(image+RVA,ORIGINAL)
        end)
        if ok and inactive then return true end
        -- Recover only the exact owned singleton before teardown after cold Lua
        -- loading. install() verifies the complete build and owned cave first.
        if not M.install()then return false end
    end
    if not owns()then
        return fail("cannot uninstall: ownership changed")
    end
    local ok,reason=change_text(patch,ORIGINAL)
    if not ok then return fail(reason)end
    if not equal(image+RVA,ORIGINAL)then return fail("entry restore unconfirmed")end
    if kernel.VirtualFree(ptr(page),0,0x8000)==0 then return fail("capture allocation release failed")end
    page,code,stub,patch=nil,nil,nil,nil;M.installed=false;return true
end
M.uninstall=M.close
function M.mouse()
    if not sdl then local ok,result=pcall(setup);if not ok or not result then return nil end end
    local x,y=ffi.new("int[1]"),ffi.new("int[1]")
    local buttons=sdl.SDL_GetMouseState(x,y)
    return tonumber(x[0]),tonumber(y[0]),tonumber(buttons)
end
function M.status()
    local result={installed=M.installed,error=M.last_error,page=page,code=code,metadata=metadata}
    if M.installed and page then result.seq=tonumber(ffi.cast("uint32_t*",page+128)[0])end
    return result
end

-- Ordinary wand speed uses the native row layout, never a GUI overlay or card
-- replacement. This independent consumer shares the verified function manager.
local SPEED_MAGIC="EWnative-speed-row-v3"
local COUNT,ENTRIES,STRIDE,MAX=128,256,100,512
local CONSTANTS=53248
local function speed_addresses(page)
    return {icon_prefix=page+CONSTANTS,icon_name=page+CONSTANTS+128,
        label=page+CONSTANTS+160,help=page+CONSTANTS+224,empty=page+CONSTANTS+384}
end
local function speed_header(page,code)
    return SPEED_MAGIC..bytes32(image+SPEED_HOOK)..bytes32(page)..bytes32(code)..SPEED_ORIGINAL
end
local function speed_detour(code,rva,length)
    rva,length=rva or SPEED_HOOK,length or 6
    return string.char(0xe9)..bytes32(code-(image+rva+5))..string.rep(string.char(0x90),length-5)
end
local speed_strings={icon_prefix="mods/quant.ew/files/system/player_ping/assets/",icon_name="projectile_speed",
    label="Projectile speed",help="Intrinsic projectile-speed multiplier stored on this wand.",empty=""}
local function speed_builder()
    return dofile_once("mods/quant.ew/files/system/player_ping/native_speed_row.lua")
end
local function speed_code(address,page,block)
    local parts,labels,fixups={}, {}, {};local length=0
    local function emit(value)parts[#parts+1]=value;length=length+#value end
    local function op(...)emit(string.char(...))end
    local function jump(opcode,label)emit(opcode);fixups[#fixups+1]={at=length,label=label};emit("\0\0\0\0")end
    local function mark(label)labels[label]=length end
    op(0x6a,0x00,0x9c,0x60) -- reserve result; preserve flags and GPRs
    op(0x8b,0x55,0x08,0x85,0xd2);jump("\x0f\x84","lookup_done")
    op(0x8b,0x1a) -- item Entity.id; exact native frame already owns live item
    op(0x8b,0x0d);emit(bytes32(page+COUNT));op(0x81,0xf9);emit(bytes32(MAX))
    jump("\x0f\x87","lookup_done");op(0x85,0xc9);jump("\x0f\x84","lookup_done")
    op(0xbe);emit(bytes32(page+ENTRIES))
    mark("lookup");op(0x39,0x1e);jump("\x0f\x84","found")
    op(0x83,0xc6,STRIDE,0x49);jump("\x0f\x85","lookup")
    jump("\xe9","lookup_done")
    mark("found");op(0x8d,0x46,0x04,0x89,0x44,0x24,0x24)
    mark("lookup_done");op(0x61,0x83,0x7c,0x24,0x04,0x00);jump("\x0f\x84","skip")
    -- The cloned row updates only the native layout/frame locals. Preserve the
    -- original renderer's live GPR, x87/MMX/SSE/MXCSR state across added calls.
    op(0x60,0x8b,0x5c,0x24,0x24,0x8b,0xc4) -- pushad; EBX = reserved text above saved flags
    op(0x81,0xec);emit(bytes32(528));op(0x83,0xe4,0xf0)
    op(0x89,0x84,0x24);emit(bytes32(512));op(0x0f,0xae,0x04,0x24)
    local clone=speed_builder().build(address+length,image,block,speed_addresses(page))
    emit(clone)
    op(0x0f,0xae,0x0c,0x24,0x8b,0xa4,0x24);emit(bytes32(512));op(0x61)
    mark("skip");op(0x9d,0x8d,0x64,0x24,0x04);emit(SPEED_ORIGINAL) -- final POPFD; LEA preserves it
    op(0xe9);emit(bytes32(image+SPEED_HOOK+#SPEED_ORIGINAL-(address+length+4)))
    local result=table.concat(parts)
    for _,f in ipairs(fixups)do
        local at=f.at+1;result=result:sub(1,at-1)..bytes32(labels[f.label]-(f.at+4))..result:sub(at+4)
    end
    return result
end
M.build_speed_code=speed_code -- pure builder; no patch or native execution
local function layout_code(address,page)
    local parts,labels,fixups={}, {}, {};local length=0
    local function emit(s)parts[#parts+1]=s;length=length+#s end
    local function op(...)emit(string.char(...))end
    local function jump(opcode,label)emit(opcode);fixups[#fixups+1]={at=length,label=label};emit("\0\0\0\0")end
    local function mark(label)labels[label]=length end
    op(0x6a,0x00,0x9c,0x60,0x8b,0x55,0x08,0x85,0xd2);jump("\x0f\x84","done")
    op(0x8b,0x1a,0x8b,0x0d);emit(bytes32(page+COUNT));op(0x81,0xf9);emit(bytes32(MAX))
    jump("\x0f\x87","done");op(0x85,0xc9);jump("\x0f\x84","done")
    op(0xbe);emit(bytes32(page+ENTRIES))
    mark("lookup");op(0x39,0x1e);jump("\x0f\x84","found")
    op(0x83,0xc6,STRIDE,0x49);jump("\x0f\x85","lookup");jump("\xe9","done")
    mark("found");op(0xc7,0x44,0x24,0x24,1,0,0,0)
    mark("done");op(0x61,0x83,0x7c,0x24,0x04,0);jump("\x0f\x84","skip")
    -- Native column is relative to the card. Raise it only for inspected wands
    -- to fit the measured added label; all original value rows stay aligned.
    op(0x83,0xec,0x10,0x0f,0x11,0x0c,0x24) -- full XMM1, unaligned-safe MOVUPS
    op(0xf3,0x0f,0x2a,0x0d);emit(bytes32(page+132));op(0xf3,0x0f,0x5f,0xc1)
    op(0x0f,0x10,0x0c,0x24,0x83,0xc4,0x10)
    mark("skip");op(0x9d,0x8d,0x64,0x24,0x04);emit(LAYOUT_ORIGINAL) -- final POPFD; flag-neutral stack release
    op(0xe9);emit(bytes32(image+SPEED_LAYOUT+#LAYOUT_ORIGINAL-(address+length+4)))
    local result=table.concat(parts)
    for _,f in ipairs(fixups)do local at=f.at+1
        result=result:sub(1,at-1)..bytes32(labels[f.label]-(f.at+4))..result:sub(at+4)
    end
    return result
end
M.build_speed_layout=layout_code
local function padding_code(address,page)
    local parts,labels,fixups={}, {}, {};local length=0
    local function emit(s)parts[#parts+1]=s;length=length+#s end
    local function op(...)emit(string.char(...))end
    local function jump(opcode,label)emit(opcode);fixups[#fixups+1]={at=length,label=label};emit("\0\0\0\0")end
    local function mark(label)labels[label]=length end
    op(0x9c,0x60) -- keep incoming flags until all extra work is finished
    op(0x80,0xbd);emit(bytes32(-0x4071));op(0);jump("\x0f\x84","skip") -- detailed card only
    op(0x8b,0x55,0x08,0x85,0xd2);jump("\x0f\x84","skip")
    op(0x8b,0x1a,0x8b,0x0d);emit(bytes32(page+COUNT));op(0x81,0xf9);emit(bytes32(MAX))
    jump("\x0f\x87","skip");op(0x85,0xc9);jump("\x0f\x84","skip")
    op(0xbe);emit(bytes32(page+ENTRIES))
    mark("lookup");op(0x39,0x1e);jump("\x0f\x84","found")
    op(0x83,0xc6,STRIDE,0x49);jump("\x0f\x85","lookup");jump("\xe9","skip")
    mark("found")
    -- The native height prepass predates our extra stat row. Add precisely the
    -- native row advance before placement/clamping and frame construction.
    -- FXSAVE's saved XMM0.low is the sole intentional SIMD output; every other
    -- lane/register/FP state is restored, then the original height store executes.
    op(0x8b,0xc4,0x81,0xec);emit(bytes32(528));op(0x83,0xe4,0xf0)
    op(0x89,0x84,0x24);emit(bytes32(512));op(0x0f,0xae,0x04,0x24)
    op(0xf3,0x0f,0x58,0x05);emit(bytes32(image+0xd5331c))
    op(0xf3,0x0f,0x11,0x84,0x24);emit(bytes32(160)) -- update only saved XMM0.low
    op(0x0f,0xae,0x0c,0x24,0x8b,0xa4,0x24);emit(bytes32(512))
    mark("skip");op(0x61,0x9d);emit(PAD_ORIGINAL)
    op(0xe9);emit(bytes32(image+SPEED_PAD+#PAD_ORIGINAL-(address+length+4)))
    local result=table.concat(parts)
    for _,f in ipairs(fixups)do local at=f.at+1
        result=result:sub(1,at-1)..bytes32(labels[f.label]-(f.at+4))..result:sub(at+4)
    end
    return result
end
M.build_speed_padding=padding_code
local function speed_memory_owned()
    return speed.installed and speed.page and speed.code and speed.stub and speed.patch
        and committed(speed.page,65536,speed.page)and committed(speed.code,#speed.stub,speed.page)
        and equal(speed.page,speed_header(speed.page,speed.code))and equal(speed.code,speed.stub)
        and equal(speed.code+2048,speed.layout_stub)and equal(speed.code+PAD_CODE,speed.pad_stub)
end
speed_owns=function()
    return speed_memory_owned()and equal(image+SPEED_HOOK,speed.patch)
        and equal(image+SPEED_LAYOUT,speed.layout_patch)and equal(image+SPEED_PAD,speed.pad_patch)
end
speed_adopt=function()
    -- Exact cold-owned allocation can be recovered; foreign hooks are never chained.
    local fn=image+SPEED_HOOK
    if not committed(fn,6)then return false end
    local layout_entry=false
    if ffi.cast("unsigned char*",fn)[0]~=0xe9 then
        if not equal(fn,SPEED_ORIGINAL)or not committed(image+SPEED_LAYOUT,8)
            then return false end
        if ffi.cast("unsigned char*",image+SPEED_LAYOUT)[0]==0xe9 then fn=image+SPEED_LAYOUT;layout_entry=2048
        elseif equal(image+SPEED_LAYOUT,LAYOUT_ORIGINAL)and ffi.cast("unsigned char*",image+SPEED_PAD)[0]==0xe9 then fn=image+SPEED_PAD;layout_entry=PAD_CODE
        else return false end
    end
    local rel=tonumber(ffi.cast("int32_t*",fn+1)[0]);local code=(fn+5+rel)%4294967296
    if layout_entry then code=code-layout_entry end
    local page=code-65536
    if page<0x10000 or not committed(page,65536,page)or not committed(code,4096,page)
        or not equal(page,speed_header(page,code))then return false end
    local constants=speed_addresses(page)
    for key,value in pairs(speed_strings)do if not equal(constants[key],value.."\0")then return false end end
    local block=expected(SPEED_BLOCK,SPEED_LENGTH)
    local candidate=speed_code(code,page,block)
    if #candidate>2048 or not equal(code,candidate)
        or not(equal(image+SPEED_HOOK,SPEED_ORIGINAL)or equal(image+SPEED_HOOK,speed_detour(code)))then return false end
    local layout=layout_code(code+2048,page)
    local layout_patch=speed_detour(code+2048,SPEED_LAYOUT,8)
    if not equal(code+2048,layout)or not(equal(image+SPEED_LAYOUT,LAYOUT_ORIGINAL)or equal(image+SPEED_LAYOUT,layout_patch))then return false end
    local pad=padding_code(code+PAD_CODE,page);local pad_patch=speed_detour(code+PAD_CODE,SPEED_PAD,8)
    if not equal(code+PAD_CODE,pad)or not(equal(image+SPEED_PAD,PAD_ORIGINAL)or equal(image+SPEED_PAD,pad_patch))then return false end
    speed={installed=true,page=page,code=code,stub=candidate,patch=speed_detour(code),layout_stub=layout,layout_patch=layout_patch,pad_stub=pad,pad_patch=pad_patch}
    M.speed_installed=true;return true
end
local function change_speed(before,after,rva)
    local fn=image+(rva or SPEED_HOOK)
    if not equal(fn,before)then return false,"speed row ownership changed"end
    local old=ffi.new("unsigned long[1]")
    if kernel.VirtualProtect(ptr(fn),#before,0x40,old)==0 then return false,"speed row protection failed"end
    local ok=equal(fn,before)
    if ok then ffi.copy(ptr(fn),after,#after);ok=kernel.FlushInstructionCache(kernel.GetCurrentProcess(),ptr(fn),#after)~=0 end
    local ignored=ffi.new("unsigned long[1]")
    local restored=kernel.VirtualProtect(ptr(fn),#before,old[0],ignored)~=0
    if ok and restored then return true end
    local rollback=ffi.new("unsigned long[1]")
    if kernel.VirtualProtect(ptr(fn),#before,0x40,rollback)~=0 then
        if equal(fn,after)then ffi.copy(ptr(fn),before,#before);kernel.FlushInstructionCache(kernel.GetCurrentProcess(),ptr(fn),#before)end
        kernel.VirtualProtect(ptr(fn),#before,old[0],ignored)
    end
    return false,"speed row cache/protection failure"
end
function M.install_speed()
    if speed.installed and speed.validated then return speed_owns()or fail("speed row ownership changed")end
    local ok,result=pcall(function()
        if not setup()or not verify_build()then return false end
        if not equal(image+RVA,ORIGINAL)and not(M.installed and owns())then
            local fn=image+RVA
            if ffi.cast("unsigned char*",fn)[0]~=0xe9 then return fail("conflicting native entry")end
            local rel=tonumber(ffi.cast("int32_t*",fn+1)[0]);local candidate=(fn+5+rel)%4294967296
            local allocation=candidate-4096
            if allocation<0x10000 or not committed(allocation,256,allocation)or not committed(candidate,1024,allocation)
                or not equal(allocation,header(allocation,candidate))then return fail("foreign native entry")end
            local candidate_stub=build_code(candidate,allocation+128,fn,getmouse)
            if not equal(candidate,candidate_stub)then return fail("capture ownership differs")end
            page,code,stub,patch=allocation,candidate,candidate_stub,detour(candidate)
            M.installed=true
        end
        if speed.installed then return speed_owns()or fail("incomplete owned speed row")end -- cold owned pair
        local block=expected(SPEED_BLOCK,SPEED_LENGTH)
        local original=disk:sub(parsed.at(SPEED_BLOCK,SPEED_LENGTH),parsed.at(SPEED_BLOCK,SPEED_LENGTH)+SPEED_LENGTH-1)
        if sha256(original)~=speed_builder().BLOCK_SHA256 then return fail("native speed row source differs")end
        local allocated=kernel.VirtualAlloc(nil,69632,0x3000,0x04)
        if allocated==nil then return fail("speed allocation failed")end
        local page=number(allocated);local code=page+65536
        speed={installed=false,page=page,code=code}
        local stub=speed_code(code,page,block);local patch=speed_detour(code)
        local layout=layout_code(code+2048,page);local layout_patch=speed_detour(code+2048,SPEED_LAYOUT,8)
        local pad=padding_code(code+PAD_CODE,page);local pad_patch=speed_detour(code+PAD_CODE,SPEED_PAD,8)
        ffi.fill(allocated,69632);ffi.copy(allocated,speed_header(page,code))
        local constants=speed_addresses(page)
        for key,value in pairs(speed_strings)do ffi.copy(ptr(constants[key]),value.."\0",#value+1)end
        ffi.copy(ptr(code),stub,#stub);ffi.copy(ptr(code+2048),layout,#layout);ffi.copy(ptr(code+PAD_CODE),pad,#pad)
        ffi.cast("uint32_t*",page+132)[0]=80
        local old=ffi.new("unsigned long[1]")
        if #stub>2048 or #layout>512 or #pad>1536 or kernel.VirtualProtect(ptr(code),4096,0x20,old)==0
            or kernel.FlushInstructionCache(kernel.GetCurrentProcess(),ptr(code),4096)==0 then
            kernel.VirtualFree(allocated,0,0x8000);return fail("speed code protection/cache failed")end
        speed={installed=true,validated=true,page=page,code=code,stub=stub,patch=patch,layout_stub=layout,layout_patch=layout_patch,pad_stub=pad,pad_patch=pad_patch}
        local changed,reason=change_speed(SPEED_ORIGINAL,patch)
        if changed then
            changed,reason=change_speed(LAYOUT_ORIGINAL,layout_patch,SPEED_LAYOUT)
            if not changed then change_speed(patch,SPEED_ORIGINAL)end
        end
        if changed then
            changed,reason=change_speed(PAD_ORIGINAL,pad_patch,SPEED_PAD)
            if not changed then change_speed(layout_patch,LAYOUT_ORIGINAL,SPEED_LAYOUT);change_speed(patch,SPEED_ORIGINAL)end
        end
        if not changed then
            if equal(image+SPEED_HOOK,SPEED_ORIGINAL)and equal(image+SPEED_LAYOUT,LAYOUT_ORIGINAL)and equal(image+SPEED_PAD,PAD_ORIGINAL)then
                kernel.VirtualFree(allocated,0,0x8000);speed={installed=false}
            end
            M.speed_installed=speed.installed;return fail(reason)
        end
        M.speed_installed=true;M.last_error=nil;return true
    end)
    disk,parsed=nil,nil
    if not ok then
        if speed.page and not speed.installed then kernel.VirtualFree(ptr(speed.page),0,0x8000);speed={installed=false}end
        return fail(tostring(result))
    end
    return result
end
function M.update_speeds(values,label_width)
    if not speed_owns()or type(values)~="table"then return false end
    local count=0;ffi.cast("uint32_t*",speed.page+COUNT)[0]=0
    if type(label_width)=="number"and label_width==label_width and label_width>=1 and label_width<=512 then
        ffi.cast("uint32_t*",speed.page+132)[0]=math.ceil(12+label_width+4)
    end
    for _,value in ipairs(values)do
        local id,text=value.id,value.text
        if type(id)=="number"and id>=1 and id<=2147483647 and id%1==0 and type(text)=="string"
            and #text>0 and #text<=95 and not text:find("%z")then
            local entry=speed.page+ENTRIES+count*STRIDE
            ffi.cast("uint32_t*",entry)[0]=id;ffi.fill(ptr(entry+4),96);ffi.copy(ptr(entry+4),text,#text)
            count=count+1;if count==MAX then break end
        end
    end
    ffi.cast("uint32_t*",speed.page+COUNT)[0]=count
    return true
end
function M.close_speed()
    if not speed.installed then
        local loaded,ready=pcall(function()
            if not kernel and not setup()then return false end
            image=image or number(kernel.GetModuleHandleA(nil))
            return committed(image+SPEED_HOOK,6)and equal(image+SPEED_HOOK,SPEED_ORIGINAL)
                and equal(image+SPEED_LAYOUT,LAYOUT_ORIGINAL)and equal(image+SPEED_PAD,PAD_ORIGINAL)
        end)
        if loaded and ready then return true end
        -- During a cold Lua reload only an exact owned patch may be adopted.
        local ok,result=pcall(function()return setup()and verify_build()end)
        disk,parsed=nil,nil
        -- Validation can adopt our exact allocation before a later foreign-body
        -- check fails. Still release only that owned row; never claim it closed
        -- while leaving its detour active.
        if not speed.installed then return true end
    end
    if not speed_memory_owned()then return fail("cannot uninstall foreign speed row")end
    if not(equal(image+SPEED_HOOK,speed.patch)or equal(image+SPEED_HOOK,SPEED_ORIGINAL))
        or not(equal(image+SPEED_LAYOUT,speed.layout_patch)or equal(image+SPEED_LAYOUT,LAYOUT_ORIGINAL))
        or not(equal(image+SPEED_PAD,speed.pad_patch)or equal(image+SPEED_PAD,PAD_ORIGINAL))then
        return fail("cannot uninstall foreign speed row")
    end
    if equal(image+SPEED_HOOK,speed.patch)then
        local ok,reason=change_speed(speed.patch,SPEED_ORIGINAL)
        if not ok then return fail(reason)end
    end
    if equal(image+SPEED_LAYOUT,speed.layout_patch)then
        local ok,reason=change_speed(speed.layout_patch,LAYOUT_ORIGINAL,SPEED_LAYOUT)
        if not ok then return fail(reason)end
    end
    if equal(image+SPEED_PAD,speed.pad_patch)then
        local ok,reason=change_speed(speed.pad_patch,PAD_ORIGINAL,SPEED_PAD)
        if not ok then return fail(reason)end
    end
    if not equal(image+SPEED_HOOK,SPEED_ORIGINAL)or not equal(image+SPEED_LAYOUT,LAYOUT_ORIGINAL)
        or not equal(image+SPEED_PAD,PAD_ORIGINAL)then return fail("speed row restoration unconfirmed")end
    if kernel.VirtualFree(ptr(speed.page),0,0x8000)==0 then return fail("speed allocation release failed")end
    speed={installed=false};M.speed_installed=false;return true
end
function M.speed_status()
    local result={installed=speed.installed,owned=speed_owns()or false,error=M.last_error}
    if result.owned then result.count=tonumber(ffi.cast("uint32_t*",speed.page+COUNT)[0])end
    return result
end

rawset(_G,registry,M)
return M

-- MIT License
-- Copyright (c) 2026 Graham Forest
-- Permission is hereby granted, free of charge, to any person obtaining a copy
-- of this software and associated documentation files (the "Software"), to deal
-- in the Software without restriction, including without limitation the rights
-- to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
-- copies of the Software, and to permit persons to whom the Software is
-- furnished to do so, subject to the following conditions:
-- The above copyright notice and this permission notice shall be included in all
-- copies or substantial portions of the Software.
-- THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
-- IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
-- FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
-- AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
-- LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
-- OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
-- SOFTWARE.
