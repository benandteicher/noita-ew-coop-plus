-- Incremental persistence for the v2 income protocol. Pages retain every claim
-- identity; only the bounded payment-history protocol may remove entries.
-- Native save checkpoints capture these Globals alongside the ordinary root.
local bitser=dofile_once('mods/quant.ew/files/lib/bitser.lua')
local base64=dofile_once('mods/quant.ew/files/resource/base64.lua')
local M={HISTORY=65536}
local maps=setmetatable({},{__mode='k'})
local PAGE_SIZE,HASH_PAGES=128,256
local SEQUENCE_SLOTS=M.HISTORY/PAGE_SIZE+1 -- both partial boundary pages fit
local function encode(v)return base64.encode(bitser.dumps(v))end
local function decode(v)return bitser.loads(base64.decode(v))end
local function page_for(kind,key)
    if kind=='sequence'then
        assert(type(key)=='number'and key>=1 and key<=2147483647 and key==math.floor(key),'Invalid income sequence key')
        return math.floor((key-1)/PAGE_SIZE)+1
    end
    assert(type(key)=='string','Invalid income identity key')
    local h=0
    for i=1,#key do h=(h*33+key:byte(i))%HASH_PAGES end
    return h+1
end
local function raw_write(k,v)GlobalsSetValue(k,encode(v))end
local function slot(kind,p)return kind=='sequence'and((p-1)%SEQUENCE_SLOTS+1)or p end
local function page_key(prefix,p,kind)return prefix..':'..slot(kind,p)end
local function descriptor(prefix,kind)
    return {__ew_income_pages=2,prefix=prefix,kind=kind,present={},generation=0}
end
local function register(map,prefix,kind)
    assert(type(map)=='table','Missing income history map')
    local c=maps[map]
    if c then
        assert(c.desc.prefix==prefix and c.desc.kind==kind,'Income history map reused under another key')
        return c
    end
    c={desc=descriptor(prefix,kind),pages={},dirty={}}
    maps[map]=c
    -- First attachment/migration is a one-time full walk. Ordinary saves only
    -- encode pages explicitly marked by the transaction that changed them.
    for key,value in pairs(map)do
        local p=page_for(kind,key)
        c.pages[p]=c.pages[p]or{};c.pages[p][key]=value
        c.dirty[p]=true;c.desc.present[p]=0
    end
    return c
end
function M.touch(map,key)
    local c=maps[map]
    -- A newly created map is enrolled in full by the first write().
    if not c then return end
    local p=page_for(c.desc.kind,key)
    c.pages[p]=c.pages[p]or{}
    c.pages[p][key]=map[key]
    c.dirty[p]=true
    c.desc.present[p]=next(c.pages[p])and(c.desc.present[p]or 0)or nil
end
local function write_map(map,prefix,kind)
    local c=register(map,prefix,kind)
    if kind=='sequence'then
        local used={}
        for p in pairs(c.desc.present)do
            local physical=slot(kind,p)
            assert(not used[physical],'Income recovery pages exceed retained window');used[physical]=true
        end
    end
    if next(c.dirty)then
        c.desc.generation=c.desc.generation+1
        assert(c.desc.generation<9007199254740991,'Income page revision limit reached')
    end
    for p in pairs(c.dirty)do
        -- Empty pages are written too, so they cannot retain a stale suffix if
        -- later reused. The descriptor is committed after all changed pages.
        local page=c.pages[p]
        local present=next(page)~=nil
        local reused=false
        if not present and kind=='sequence'then
            for q in pairs(c.desc.present)do
                if q~=p and slot(kind,q)==slot(kind,p)then reused=true;break end
            end
        end
        if present then c.desc.present[p]=c.desc.generation end
        if not reused then
            raw_write(page_key(prefix,p,kind),{page=p,revision=c.desc.generation,entries=page})
        end
        if not present then c.pages[p]=nil end
        c.dirty[p]=nil
    end
    return c.desc
end
local function read_map(v,prefix,kind)
    if type(v)~='table'or not v.__ew_income_pages then return v end
    assert(v.__ew_income_pages==2 and v.prefix==prefix and v.kind==kind and type(v.present)=='table'
        and type(v.generation)=='number'and v.generation>=0 and v.generation==math.floor(v.generation),'Invalid income page descriptor')
    local map,c={},{desc=v,pages={},dirty={}}
    local used={}
    for p,revision in pairs(v.present)do
        assert(type(revision)=='number'and revision>=1 and revision<=v.generation and revision==math.floor(revision)
            and type(p)=='number'and p>=1 and p==math.floor(p)and(kind~='identity'or p<=HASH_PAGES),'Invalid income page index')
        local physical=slot(kind,p)
        assert(not used[physical],'Income recovery pages exceed retained window');used[physical]=true
        local bytes=GlobalsGetValue(page_key(prefix,p,kind),'')
        assert(bytes~='','Missing income history page; restore matching saves')
        local envelope=decode(bytes)
        assert(type(envelope)=='table'and envelope.page==p and envelope.revision==revision,'Income page revision mismatch; restore matching saves')
        local page=envelope.entries
        assert(type(page)=='table'and next(page),'Invalid empty income history page')
        for key,value in pairs(page)do
            assert(page_for(kind,key)==p and map[key]==nil,'Invalid income history page membership')
            map[key]=value
        end
        c.pages[p]=page
    end
    maps[map]=c
    return map
end
local function shallow(v)
    local out={};for k,value in pairs(v)do out[k]=value end;return out
end
function M.write(key,v)
    if v.version~=2 then raw_write(key,v);return end
    local out=shallow(v)
    if v.accounts then
        out.claims=write_map(v.claims,key..':claims','identity')
        out.accounts={}
        for peer,a in pairs(v.accounts)do
            local account=shallow(a);out.accounts[peer]=account
            account.pending=write_map(a.pending,key..':account:'..peer..':pending','sequence')
            account.history=write_map(a.history,key..':account:'..peer..':history','sequence')
        end
    else
        out.retired=write_map(v.retired,key..':retired','identity')
    end
    raw_write(key,out)
end
function M.read(key)
    local bytes=GlobalsGetValue(key,'');if bytes==''then return end
    local v=decode(bytes)
    if v.version~=2 then return v end
    if v.accounts then
        v.claims=read_map(v.claims,key..':claims','identity')
        for peer,a in pairs(v.accounts)do
            a.pending=read_map(a.pending,key..':account:'..peer..':pending','sequence')
            a.history=read_map(a.history,key..':account:'..peer..':history','sequence')
        end
    else
        v.retired=read_map(v.retired,key..':retired','identity')
    end
    return v
end
return M
